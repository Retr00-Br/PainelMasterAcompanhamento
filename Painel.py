import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
from supabase import create_client, Client
import os
from datetime import datetime

# 1. CONFIGURAÇÃO DA PÁGINA
NOME_APLICACAO = "Painel Master Higimed"
CAMINHO_LOGO = "logo.webp"

st.set_page_config(
    page_title=NOME_APLICACAO,
    page_icon=CAMINHO_LOGO if os.path.exists(CAMINHO_LOGO) else "📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .stAppHeader { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 5px solid #0056b3; }
    div[data-testid="stMetricValue"] > div { font-size: 26px; color: #0056b3; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# 2. CONEXÃO SUPABASE
@st.cache_resource
def iniciar_conexao_supabase() -> Client:
    try:
        if "supabase" in st.secrets:
            url = st.secrets["supabase"]["SUPABASE_URL"]
            key = st.secrets["supabase"]["SUPABASE_KEY"]
        else:
            url = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
            key = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))

        return create_client(url.rstrip("/"), key)
    except Exception as e:
        st.error(f"Erro ao conectar com o Supabase: {str(e)}")
        st.stop()

supabase = iniciar_conexao_supabase()

# 3. ETL INTEGRADO COM CRUZAMENTO CORRETO
def executar_etl_bi(file_separacao, file_logistica=None) -> pd.DataFrame:
    try:
        # Carregamento Base Principal
        file_separacao.seek(0)
        if file_separacao.name.endswith(('.xlsx', '.xls')):
            df_sep = pd.read_excel(file_separacao)
        else:
            df_sep = pd.read_csv(file_separacao, sep=';', encoding='latin1', on_bad_lines='skip')

        df_sep.columns = df_sep.columns.str.replace('ï»¿', '').str.strip()

        # Carregamento Logística
        if file_logistica is not None:
            file_logistica.seek(0)
            if file_logistica.name.endswith(('.xlsx', '.xls')):
                df_log = pd.read_excel(file_logistica)
            else:
                df_log = pd.read_csv(file_logistica, sep=';', encoding='latin1', on_bad_lines='skip')
            df_log.columns = df_log.columns.str.strip()
        else:
            df_log = pd.DataFrame(columns=['Pedido', 'AbertoEm', 'Cliente', 'Status'])

        # Chave de Ligação Padronizada
        col_wms = next((c for c in ['(WMS) Ped. Orig.', 'PV', 'pv_limpo'] if c in df_sep.columns), 'NF')
        df_sep['PEDIDO_KEY'] = df_sep[col_wms].fillna(df_sep.get('NF', '')).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

        if not df_log.empty and 'Pedido' in df_log.columns:
            df_log['PEDIDO_KEY'] = df_log['Pedido'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            df_bi = pd.merge(df_sep, df_log, on='PEDIDO_KEY', how='outer', suffixes=('_sep', '_log'))
        else:
            df_bi = df_sep.copy()
            df_bi['Status'] = np.nan

        # Coalescência de Cliente
        if 'Cliente_sep' in df_bi.columns and 'Cliente_log' in df_bi.columns:
            df_bi['Cliente_Final'] = df_bi['Cliente_sep'].combine_first(df_bi['Cliente_log'])
        elif 'Cliente' in df_bi.columns:
            df_bi['Cliente_Final'] = df_bi['Cliente']
        else:
            df_bi['Cliente_Final'] = 'Cliente Não Identificado'

        # Atribuição Transparente do Status Unificado
        df_bi['Status_Unificado'] = df_bi['Status'].fillna('10-Ag.Inicio Operação').astype(str).str.strip()
        return df_bi

    except Exception as e:
        st.error(f"❌ Erro no processamento dos dados: {str(e)}")
        return pd.DataFrame()

# 4. INJEÇÃO DE DADOS SEM PERDA DE ATRIBUTOS
def sincronizar_com_supabase(df_bi: pd.DataFrame):
    try:
        if df_bi.empty:
            st.warning("Nenhum dado para sincronizar.")
            return

        # 1. Garantir Cadastro de Clientes
        clientes_unicos = [str(c).strip() for c in df_bi['Cliente_Final'].dropna().unique() if str(c).strip() != '']
        res_clientes = supabase.table('clientes').select('id, nome').execute()
        df_cli_bd = pd.DataFrame(res_clientes.data) if res_clientes and res_clientes.data else pd.DataFrame(columns=['id', 'nome'])

        if not df_cli_bd.empty:
            existentes = set(df_cli_bd['nome'].astype(str).str.strip().str.lower().values)
            novos = [{'nome': c} for c in clientes_unicos if c.lower() not in existentes]
        else:
            novos = [{'nome': c} for c in clientes_unicos]

        if novos:
            supabase.table('clientes').insert(novos).execute()
            res_clientes = supabase.table('clientes').select('id, nome').execute()
            df_cli_bd = pd.DataFrame(res_clientes.data)

        mapa_cli = {str(r['nome']).strip().lower(): int(r['id']) for _, r in df_cli_bd.iterrows()}

        # 2. Mapeamento de Status por Código ou Descrição Completa
        res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
        mapa_status = {}
        if res_status and res_status.data:
            for s in res_status.data:
                mapa_status[str(s['codigo']).strip()] = int(s['id'])
                mapa_status[f"{s['codigo']}-{s['descricao']}".strip()] = int(s['id'])

        # 3. Mapeamento dos Pedidos
        res_pedidos = supabase.table('pedidos').select('id, numero_pedido').execute()
        pedidos_bd = {str(p['numero_pedido']).strip(): p['id'] for p in res_pedidos.data} if res_pedidos and res_pedidos.data else {}

        payload = []
        agora = datetime.now().isoformat()

        for _, row in df_bi.iterrows():
            ped_str = str(row['PEDIDO_KEY']).strip()
            if not ped_str or ped_str == 'nan':
                continue

            cli_nome = str(row.get('Cliente_Final', '')).strip().lower()
            c_id = mapa_cli.get(cli_nome)
            if not c_id:
                continue

            status_str = str(row.get('Status_Unificado', '')).strip()
            cod_s = status_str.split('-')[0] if '-' in status_str else status_str
            s_id = mapa_status.get(status_str) or mapa_status.get(cod_s) or 1

            item = {
                'numero_pedido': ped_str,
                'cliente_id': c_id,
                'status_id': s_id,
                'aberto_em': agora,
                'volumes': 1
            }

            if ped_str in pedidos_bd:
                item['id'] = pedidos_bd[ped_str]

            payload.append(item)

        # Batch Upsert
        for i in range(0, len(payload), 100):
            supabase.table('pedidos').upsert(payload[i:i + 100], on_conflict='numero_pedido').execute()

        st.success("✅ Cruzamento e sincronização realizados com sucesso!")
        st.cache_data.clear()
        st.rerun()

    except Exception as e:
        st.error(f"Erro na sincronização: {str(e)}")

# 5. CARREGAMENTO PARA O DASHBOARD
@st.cache_data(ttl=60)
def carregar_dados_painel() -> pd.DataFrame:
    try:
        res = supabase.table('pedidos').select(
            'id, numero_pedido, aberto_em, volumes, clientes(nome), status_separacao(codigo, descricao)'
        ).execute()

        if not res.data:
            return pd.DataFrame()

        registros = []
        for item in res.data:
            c_obj = item.get('clientes') or {}
            s_obj = item.get('status_separacao') or {}

            nome_cli = c_obj.get('nome', 'N/I') if isinstance(c_obj, dict) else 'N/I'
            cod_st = s_obj.get('codigo', '00') if isinstance(s_obj, dict) else '00'
            desc_st = s_obj.get('descricao', 'Indefinido') if isinstance(s_obj, dict) else 'Indefinido'

            registros.append({
                'Pedido': item.get('numero_pedido'),
                'AbertoEm': item.get('aberto_em'),
                'Cliente': nome_cli,
                'Volumes': item.get('volumes', 0),
                'Status_Codigo': cod_st,
                'Status': f"{cod_st}-{desc_st}" if desc_st != 'Indefinido' else cod_st
            })

        return pd.DataFrame(registros)
    except Exception as e:
        st.error(f"Erro ao carregar dados do banco: {str(e)}")
        return pd.DataFrame()

# 6. RENDERIZAÇÃO DOS PAINÉIS
def renderizar_blocos_status(df: pd.DataFrame, status_list: list):
    st.subheader("Painel de Acompanhamento de Separações")
    if 'status_selecionado' not in st.session_state:
        st.session_state.status_selecionado = "Todos"

    colunas = st.columns(len(status_list) + 1)

    with colunas[0]:
        qtd_total = len(df)
        tipo = "primary" if st.session_state.status_selecionado == "Todos" else "secondary"
        if st.button(f"**Todos**\n### {qtd_total}", key="btn_status_todos", use_container_width=True, type=tipo):
            st.session_state.status_selecionado = "Todos"
            st.rerun()

    for idx, status in enumerate(status_list, start=1):
        cod_s = status.split('-')[0]
        qtd = len(df[df['Status_Codigo'] == cod_s]) if not df.empty else 0
        with colunas[idx]:
            tipo = "primary" if st.session_state.status_selecionado == status else "secondary"
            if st.button(f"**{status}**\n### {qtd}", key=f"btn_status_{idx}", use_container_width=True, type=tipo):
                st.session_state.status_selecionado = status
                st.rerun()

def renderizar_graficos(df: pd.DataFrame, status_ativo: str):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(f"Top 10 Clientes - [{status_ativo}]")
        if not df.empty:
            top_cli = df['Cliente'].value_counts().reset_index().head(10)
            top_cli.columns = ['Cliente', 'Qtd_Pedidos']

            fig = px.bar(top_cli, x='Qtd_Pedidos', y='Cliente', orientation='h', color='Qtd_Pedidos', color_continuous_scale='Blues_r', text='Qtd_Pedidos')
            fig.update_traces(textposition='outside')
            fig.update_layout(yaxis={'categoryorder': 'total ascending'}, xaxis_title=None, yaxis_title=None, height=380, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Nenhum pedido encontrado para o status selecionado.")

    with col2:
        st.subheader(f"Distribuição de Volumes - [{status_ativo}]")
        if not df.empty and df['Volumes'].sum() > 0:
            top_vol = df.groupby('Cliente')['Volumes'].sum().reset_index().sort_values(by='Volumes', ascending=False).head(10)
            fig_vol = px.bar(top_vol, x='Volumes', y='Cliente', orientation='h', color='Volumes', color_continuous_scale='Teal', text='Volumes')
            fig_vol.update_traces(textposition='outside')
            fig_vol.update_layout(yaxis={'categoryorder': 'total ascending'}, xaxis_title=None, yaxis_title=None, height=380, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("Sem registros de volumes.")

# 7. MAIN
def main():
    if os.path.exists(CAMINHO_LOGO):
        st.sidebar.image(CAMINHO_LOGO, use_container_width=True)
    
    st.sidebar.markdown("---")
    st.sidebar.header("📥 Injeção & ETL de Dados")
    f_log = st.sidebar.file_uploader("1. Arquivo Logística (`excel_...csv`):", type=["csv", "xlsx"], key="u_log")
    f_base = st.sidebar.file_uploader("2. Base Principal (`BaseDados.csv`):", type=["csv", "xlsx"], key="u_base")

    if f_base is not None:
        if st.sidebar.button("🚀 Processar & Sincronizar", use_container_width=True):
            df_proc = executar_etl_bi(f_base, f_log)
            sincronizar_com_supabase(df_proc)

    st.sidebar.markdown("---")

    col_logo, col_titulo = st.columns([1, 4])
    with col_logo:
        if os.path.exists(CAMINHO_LOGO):
            st.image(CAMINHO_LOGO, width=150)
    with col_titulo:
        st.title(NOME_APLICACAO)
        st.caption("Acompanhamento unificado dos pedidos em tempo real.")

    st.markdown("---")

    df_pedidos = carregar_dados_painel()

    lista_status = [
        "00-Sem Saldo",
        "10-Ag.Inicio Operação",
        "30-Em Separação",
        "60-Ag.Faturamento Venda",
        "65-Ag.Transportadora"
    ]

    renderizar_blocos_status(df_pedidos, lista_status)
    st.markdown("---")

    status_ativo = st.session_state.get('status_selecionado', 'Todos')
    if status_ativo != "Todos" and not df_pedidos.empty:
        cod_s = status_ativo.split('-')[0]
        df_filtrado = df_pedidos[df_pedidos['Status_Codigo'] == cod_s]
    else:
        df_filtrado = df_pedidos.copy()

    renderizar_graficos(df_filtrado, status_ativo)

if __name__ == "__main__":
    main()
