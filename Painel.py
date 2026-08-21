import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
from supabase import create_client, Client
import os
from datetime import datetime

# 1. CONFIGURAÇÃO DA PÁGINA E BRANDING
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

# 2. CONEXÃO COM O SUPABASE
@st.cache_resource
def iniciar_conexao_supabase() -> Client:
    try:
        if "supabase" in st.secrets:
            url = st.secrets["supabase"]["SUPABASE_URL"]
            key = st.secrets["supabase"]["SUPABASE_KEY"]
        else:
            url = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
            key = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))

        url_limpa = url.rstrip("/")
        return create_client(url_limpa, key)
    except Exception as e:
        st.error(f"Erro ao carregar credenciais do Supabase: {str(e)}")
        st.stop()

supabase = iniciar_conexao_supabase()

# 3. PIPELINE DE ETL INTEGRADO
def executar_etl_bi(file_separacao, file_logistica=None) -> pd.DataFrame:
    """Processa, limpa, cruza as bases e gera o DataFrame unificado do BI."""
    try:
        # Carregamento Separação
        if isinstance(file_separacao, str):
            df_sep = pd.read_csv(file_separacao, sep=';', encoding='latin1', on_bad_lines='skip')
        else:
            file_separacao.seek(0)
            df_sep = pd.read_csv(file_separacao, sep=';', encoding='latin1', on_bad_lines='skip')

        df_sep.columns = df_sep.columns.str.replace('ï»¿', '').str.strip()

        # Carregamento Logística (se fornecido)
        if file_logistica is not None:
            if isinstance(file_logistica, str):
                df_log = pd.read_csv(file_logistica, sep=';', encoding='latin1', on_bad_lines='skip')
            else:
                file_logistica.seek(0)
                df_log = pd.read_csv(file_logistica, sep=';', encoding='latin1', on_bad_lines='skip')
            df_log.columns = df_log.columns.str.strip()
        else:
            df_log = pd.DataFrame(columns=['Pedido', 'AbertoEm', 'Cliente', 'Status'])

        # Padronização e Limpeza de Chaves de Ligação
        col_wms = next((c for c in ['(WMS) Ped. Orig.', 'PV', 'pv_limpo'] if c in df_sep.columns), 'NF')
        
        df_sep['PEDIDO_KEY'] = (
            df_sep[col_wms]
            .fillna(df_sep.get('NF', ''))
            .astype(str)
            .str.replace(r'\.0$', '', regex=True)
            .str.strip()
        )

        if not df_log.empty and 'Pedido' in df_log.columns:
            df_log['PEDIDO_KEY'] = (
                df_log['Pedido']
                .astype(str)
                .str.replace(r'\.0$', '', regex=True)
                .str.strip()
            )
            # Merge Outer (Cruza sem perda de registros)
            df_bi = pd.merge(df_sep, df_log, on='PEDIDO_KEY', how='outer', suffixes=('_sep', '_log'))
        else:
            df_bi = df_sep.copy()
            df_bi['Status'] = np.nan

        # Coalescência de Colunas
        if 'Cliente_sep' in df_bi.columns and 'Cliente_log' in df_bi.columns:
            df_bi['Cliente_Final'] = df_bi['Cliente_sep'].combine_first(df_bi['Cliente_log'])
        elif 'Cliente' in df_bi.columns:
            df_bi['Cliente_Final'] = df_bi['Cliente']
        else:
            df_bi['Cliente_Final'] = 'N/I'

        # Regra de Status Unificado
        condicoes = [
            df_bi['Dt. Entrega Real'].notna() & (df_bi['Dt. Entrega Real'].astype(str).str.strip() != '') if 'Dt. Entrega Real' in df_bi.columns else pd.Series(False, index=df_bi.index),
            df_bi['Status'].notna() & (df_bi['Status'].astype(str).str.strip() != '') & (df_bi['Status'].astype(str) != 'nan')
        ]
        escolhas = [
            'Entregue',
            df_bi['Status']
        ]

        df_bi['Status_Unificado'] = np.select(condicoes, escolhas, default='10-Ag.Inicio Operação')
        return df_bi

    except Exception as e:
        st.error(f"❌ Erro na execução do ETL: {str(e)}")
        return pd.DataFrame()

# 4. INJEÇÃO DOS DADOS NO SUPABASE
def sincronizar_com_supabase(df_bi: pd.DataFrame):
    """Lê o DataFrame consolidado do ETL e executa a atualização no Supabase."""
    try:
        if df_bi.empty:
            st.warning("Nenhum dado para sincronizar.")
            return

        st.info(f"⏳ Sincronizando {len(df_bi)} registros consolidados...")

        # 1. Clientes
        clientes_unicos = [c for c in df_bi['Cliente_Final'].dropna().unique() if str(c).strip() != '']
        res_clientes = supabase.table('clientes').select('id, nome').execute()
        df_cli_bd = pd.DataFrame(res_clientes.data) if res_clientes and res_clientes.data else pd.DataFrame(columns=['id', 'nome'])

        if not df_cli_bd.empty:
            df_cli_bd['nome_clean'] = df_cli_bd['nome'].astype(str).str.strip().str.lower()
            existentes = set(df_cli_bd['nome_clean'].values)
            novos = [{'nome': c} for c in clientes_unicos if str(c).strip().lower() not in existentes]
        else:
            novos = [{'nome': c} for c in clientes_unicos]

        if novos:
            supabase.table('clientes').insert(novos).execute()
            res_clientes = supabase.table('clientes').select('id, nome').execute()
            df_cli_bd = pd.DataFrame(res_clientes.data)

        mapa_cli = {str(r['nome']).strip().lower(): int(r['id']) for _, r in df_cli_bd.iterrows()}

        # 2. Status
        res_status = supabase.table('status_separacao').select('id, codigo').execute()
        mapa_status = {str(r['codigo']).strip(): int(r['id']) for r in res_status.data} if res_status and res_status.data else {}

        # 3. Pedidos
        res_pedidos = supabase.table('pedidos').select('id, numero_pedido').execute()
        pedidos_bd = {p['numero_pedido']: p['id'] for p in res_pedidos.data} if res_pedidos and res_pedidos.data else {}

        payload = []
        agora = datetime.now().isoformat()

        for _, row in df_bi.iterrows():
            ped_raw = row['PEDIDO_KEY']
            digits = ''.join(filter(str.isdigit, str(ped_raw)))
            if not digits:
                continue

            ped_num = int(digits)
            cli_nome = str(row.get('Cliente_Final', '')).strip().lower()
            c_id = mapa_cli.get(cli_nome)

            if not c_id:
                continue

            item = {
                'numero_pedido': ped_num,
                'cliente_id': c_id,
                'status_id': mapa_status.get('10', 1),
                'aberto_em': agora,
                'volumes': 1
            }

            if ped_num in pedidos_bd:
                item['id'] = pedidos_bd[ped_num]

            payload.append(item)

        # Upsert Lote
        for i in range(0, len(payload), 100):
            supabase.table('pedidos').upsert(payload[i:i + 100], on_conflict='numero_pedido').execute()

        st.success("✅ Dados sincronizados com o Supabase com sucesso!")
        st.cache_data.clear()
        st.rerun()

    except Exception as e:
        st.error(f"Erro ao injetar no banco: {str(e)}")

# 5. CARREGAMENTO DOS DADOS PARA O DASHBOARD
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
            cliente_obj = item.get('clientes') or {}
            status_obj = item.get('status_separacao') or {}

            nome_cliente = cliente_obj.get('nome', 'N/I') if isinstance(cliente_obj, dict) else 'N/I'
            cod_status = status_obj.get('codigo', '00') if isinstance(status_obj, dict) else '00'
            desc_status = status_obj.get('descricao', 'Indefinido') if isinstance(status_obj, dict) else 'Indefinido'

            registros.append({
                'Pedido': item.get('numero_pedido'),
                'AbertoEm': item.get('aberto_em'),
                'Cliente': nome_cliente,
                'Volumes': item.get('volumes', 0),
                'Status_Codigo': cod_status,
                'Status': f"{cod_status}-{desc_status}"
            })

        return pd.DataFrame(registros)
    except Exception as e:
        st.error(f"Erro ao carregar do banco: {str(e)}")
        return pd.DataFrame()

# 6. COMPONENTES VISUAIS
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
        qtd = len(df[df['Status'] == status]) if not df.empty else 0
        with colunas[idx]:
            tipo = "primary" if st.session_state.status_selecionado == status else "secondary"
            if st.button(f"**{status}**\n### {qtd}", key=f"btn_status_{idx}", use_container_width=True, type=tipo):
                st.session_state.status_selecionado = status
                st.rerun()

def renderizar_graficos(df: pd.DataFrame, status_ativo: str):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(f"Top 10 Clientes com Mais Pedidos - [{status_ativo}]")
        if not df.empty:
            top_clientes = df['Cliente'].value_counts().reset_index().head(10)
            top_clientes.columns = ['Cliente', 'Qtd_Pedidos']

            fig = px.bar(
                top_clientes, x='Qtd_Pedidos', y='Cliente', orientation='h',
                color='Qtd_Pedidos', color_continuous_scale='Blues_r', text='Qtd_Pedidos'
            )
            fig.update_traces(textposition='outside')
            fig.update_layout(yaxis={'categoryorder': 'total ascending'}, xaxis_title=None, yaxis_title=None, coloraxis_showscale=False, height=380, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Nenhum pedido encontrado.")

    with col2:
        st.subheader(f"Distribuição de Volumes - [{status_ativo}]")
        if not df.empty and df['Volumes'].sum() > 0:
            top_volumes = df.groupby('Cliente')['Volumes'].sum().reset_index().sort_values(by='Volumes', ascending=False).head(10)
            fig_vol = px.bar(
                top_volumes, x='Volumes', y='Cliente', orientation='h',
                color='Volumes', color_continuous_scale='Teal', text='Volumes'
            )
            fig_vol.update_traces(textposition='outside')
            fig_vol.update_layout(yaxis={'categoryorder': 'total ascending'}, xaxis_title=None, yaxis_title=None, coloraxis_showscale=False, height=380, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("Sem registro de volumes.")

# 7. PONTO DE ENTRADA (MAIN)
def main():
    if os.path.exists(CAMINHO_LOGO):
        st.sidebar.image(CAMINHO_LOGO, use_container_width=True)
    else:
        st.sidebar.title(NOME_APLICACAO)

    st.sidebar.markdown("---")

    st.sidebar.header("📥 Injeção & ETL de Dados")
    f_sep = st.sidebar.file_uploader("1. Base de Separação (BaseDados.csv):", type=["csv", "xlsx"])
    f_log = st.sidebar.file_uploader("2. Base Logística (excel_...csv):", type=["csv", "xlsx"])

    if f_sep is not None:
        if st.sidebar.button("🚀 Processar ETL & Sincronizar", use_container_width=True):
            df_processado = executar_etl_bi(f_sep, f_log)
            sincronizar_com_supabase(df_processado)

    st.sidebar.markdown("---")
    st.sidebar.caption("Master Higimed © 2026 - Gestão de Operações")

    col_logo, col_titulo = st.columns([1, 4])
    with col_logo:
        if os.path.exists(CAMINHO_LOGO):
            st.image(CAMINHO_LOGO, width=150)
    with col_titulo:
        st.title(NOME_APLICACAO)
        st.caption("Acompanhamento em tempo real dos pedidos de separação e fluxo logístico.")

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
        df_filtrado = df_pedidos[df_pedidos['Status'] == status_ativo]
    else:
        df_filtrado = df_pedidos.copy()

    renderizar_graficos(df_filtrado, status_ativo)
    st.markdown("---")

    st.subheader(f"📋 Relação Detalhada dos Pedidos - [{status_ativo}]")
    if not df_filtrado.empty:
        st.dataframe(
            df_filtrado[['Pedido', 'Cliente', 'AbertoEm', 'Volumes', 'Status']],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Nenhum pedido cadastrado no momento.")

if __name__ == "__main__":
    main()
