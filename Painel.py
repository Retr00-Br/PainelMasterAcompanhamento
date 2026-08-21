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

# 3. ETL INTEGRADO
def executar_etl_bi(file_separacao, file_logistica=None) -> pd.DataFrame:
    try:
        file_separacao.seek(0)
        if file_separacao.name.endswith(('.xlsx', '.xls')):
            df_sep = pd.read_excel(file_separacao)
        else:
            df_sep = pd.read_csv(file_separacao, sep=';', encoding='latin1', on_bad_lines='skip')

        df_sep.columns = df_sep.columns.str.replace('ï»¿', '').str.strip()

        if file_logistica is not None:
            file_logistica.seek(0)
            if file_logistica.name.endswith(('.xlsx', '.xls')):
                df_log = pd.read_excel(file_logistica)
            else:
                df_log = pd.read_csv(file_logistica, sep=';', encoding='latin1', on_bad_lines='skip')
            df_log.columns = df_log.columns.str.strip()
        else:
            df_log = pd.DataFrame(columns=['Pedido', 'AbertoEm', 'Cliente', 'Status'])

        col_wms = next((c for c in ['(WMS) Ped. Orig.', 'PV', 'pv_limpo'] if c in df_sep.columns), 'NF')
        df_sep['PEDIDO_KEY'] = df_sep[col_wms].fillna(df_sep.get('NF', '')).astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

        if not df_log.empty and 'Pedido' in df_log.columns:
            df_log['PEDIDO_KEY'] = df_log['Pedido'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            df_bi = pd.merge(df_sep, df_log, on='PEDIDO_KEY', how='outer', suffixes=('_sep', '_log'))
        else:
            df_bi = df_sep.copy()
            df_bi['Status'] = np.nan

        if 'Cliente_sep' in df_bi.columns and 'Cliente_log' in df_bi.columns:
            df_bi['Cliente_Final'] = df_bi['Cliente_sep'].combine_first(df_bi['Cliente_log'])
        elif 'Cliente' in df_bi.columns:
            df_bi['Cliente_Final'] = df_bi['Cliente']
        else:
            df_bi['Cliente_Final'] = 'Cliente Não Identificado'

        df_bi['Status_Unificado'] = df_bi['Status'].fillna('10-Ag.Inicio Operação').astype(str).str.strip()
        return df_bi

    except Exception as e:
        st.error(f"❌ Erro no ETL: {str(e)}")
        return pd.DataFrame()

# 4. INJEÇÃO SUPABASE
def sincronizar_com_supabase(df_bi: pd.DataFrame):
    try:
        if df_bi.empty:
            st.warning("Nenhum dado para sincronizar.")
            return

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

        res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
        mapa_status = {}
        if res_status and res_status.data:
            for s in res_status.data:
                mapa_status[str(s['codigo']).strip()] = int(s['id'])
                mapa_status[f"{s['codigo']}-{s['descricao']}".strip()] = int(s['id'])

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

        for i in range(0, len(payload), 100):
            supabase.table('pedidos').upsert(payload[i:i + 100], on_conflict='numero_pedido').execute()

        st.success("✅ Sincronização concluída com sucesso!")
        st.cache_data.clear()
        st.rerun()

    except Exception as e:
        st.error(f"Erro na sincronização: {str(e)}")

# 5. CARREGAMENTO SUPABASE
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
        st.error(f"Erro ao carregar dados: {str(e)}")
        return pd.DataFrame()

# 6. RENDERIZAÇÃO ABA 1 (BI OPERAÇÕES)
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
        st.subheader(f"Top 10 Clientes com Mais Pedidos - [{status_ativo}]")
        if not df.empty:
            top_cli = df['Cliente'].value_counts().reset_index().head(10)
            top_cli.columns = ['Cliente', 'Qtd_Pedidos']

            fig = px.bar(top_cli, x='Qtd_Pedidos', y='Cliente', orientation='h', color='Qtd_Pedidos', color_continuous_scale='Blues_r', text='Qtd_Pedidos')
            fig.update_traces(textposition='outside')
            fig.update_layout(yaxis={'categoryorder': 'total ascending'}, xaxis_title=None, yaxis_title=None, height=380, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Nenhum pedido encontrado.")

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

# 7. RENDERIZAÇÃO ABA 2 (CONTROLE LOGÍSTICO & ENTREGAS)
def renderizar_controle_logistico(f_base):
    st.subheader("🚚 Gestão e Controle Logístico de Entregas (Somente NFs Emitidas)")

    if f_base is None:
        st.info("💡 Faça o upload da **Base Principal (`BaseDados.csv`)** na barra lateral para visualizar o fluxo de NFs e Romaneios.")
        return

    try:
        f_base.seek(0)
        if f_base.name.endswith(('.xlsx', '.xls')):
            df_log = pd.read_excel(f_base)
        else:
            df_log = pd.read_csv(f_base, encoding='latin1', sep=';', on_bad_lines='skip')

        df_log.columns = [str(c).replace('ï»¿', '').strip() for c in df_log.columns]

        col_nf_nome = 'NF' if 'NF' in df_log.columns else 'Nota Fiscal'
        col_ped_nome = '(WMS) Ped. Orig.' if '(WMS) Ped. Orig.' in df_log.columns else 'Pedido'

        # Filtro estrito: Apenas registros válidos com NF e Pedido
        if col_nf_nome in df_log.columns:
            df_log = df_log[df_log[col_nf_nome].notna() & (df_log[col_nf_nome].astype(str).str.strip() != '') & (df_log[col_nf_nome].astype(str) != 'nan')]
        if col_ped_nome in df_log.columns:
            df_log = df_log[df_log[col_ped_nome].notna() & (df_log[col_ped_nome].astype(str).str.strip() != '') & (df_log[col_ped_nome].astype(str) != 'nan')]

        if df_log.empty:
            st.warning("⚠️ Nenhuma Nota Fiscal/Pedido válido encontrado nesta base.")
            return

        col_v = 'ValorNF.1' if 'ValorNF.1' in df_log.columns else ('ValorNF' if 'ValorNF' in df_log.columns else None)
        if col_v:
            df_log['ValorNF_Clean'] = df_log[col_v].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
            df_log['ValorNF_Clean'] = pd.to_numeric(df_log['ValorNF_Clean'], errors='coerce').fillna(0)
        else:
            df_log['ValorNF_Clean'] = 0.0

        if 'Dt. Romaneio' in df_log.columns:
            df_log['Dt_Romaneio_Parsed'] = pd.to_datetime(df_log['Dt. Romaneio'], dayfirst=True, errors='coerce')
        else:
            df_log['Dt_Romaneio_Parsed'] = pd.NaT

        # Motor de busca e Filtro por datas
        st.markdown("#### 🔍 Motor de Busca e Filtro por Data de Romaneio")
        col_s1, col_s2 = st.columns([2, 2])

        with col_s1:
            termo_busca = st.text_input("Pesquisar por NF, Pedido, Cliente ou Transportadora:", placeholder="Digite para buscar...")

        with col_s2:
            dt_min = df_log['Dt_Romaneio_Parsed'].min()
            dt_max = df_log['Dt_Romaneio_Parsed'].max()
            intervalo_datas = st.date_input("Intervalo de Datas do Romaneio:", value=(dt_min.date(), dt_max.date())) if pd.notna(dt_min) and pd.notna(dt_max) else None

        df_filtrado_log = df_log.copy()

        if termo_busca:
            tb = termo_busca.lower()
            cols_busca = [c for c in [col_nf_nome, col_ped_nome, 'Cliente', 'Transport.'] if c in df_filtrado_log.columns]
            mascara = pd.Series(False, index=df_filtrado_log.index)
            for c in cols_busca:
                mascara = mascara | df_filtrado_log[c].astype(str).str.lower().str.contains(tb, na=False)
            df_filtrado_log = df_filtrado_log[mascara]

        if intervalo_datas and len(intervalo_datas) == 2:
            d_inicio, d_fim = intervalo_datas
            df_filtrado_log = df_filtrado_log[
                (df_filtrado_log['Dt_Romaneio_Parsed'].dt.date >= d_inicio) & 
                (df_filtrado_log['Dt_Romaneio_Parsed'].dt.date <= d_fim)
            ]

        st.markdown("---")

        # Métricas da Aba 2
        m1, m2, m3, m4 = st.columns(4)
        val_total = df_filtrado_log['ValorNF_Clean'].sum()
        transp_count = df_filtrado_log['Transport.'].nunique() if 'Transport.' in df_filtrado_log.columns else 0
        entregues = df_filtrado_log['Dt. Entrega Real'].notna().sum() if 'Dt. Entrega Real' in df_filtrado_log.columns else 0
        dev = df_filtrado_log['NF. Devolucao'].notna().sum() if 'NF. Devolucao' in df_filtrado_log.columns else 0

        m1.metric("Faturamento Processado", f"R$ {val_total:,.2f}")
        m2.metric("Transportadoras Ativas", transp_count)
        m3.metric("NFs Entregues (Sucesso)", entregues)
        m4.metric("Devoluções / Ocorrências", dev)

        st.markdown("---")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Volume Faturado por Transportadora**")
            if 'Transport.' in df_filtrado_log.columns and not df_filtrado_log.empty:
                df_transp = df_filtrado_log.groupby('Transport.')['ValorNF_Clean'].sum().reset_index()
                fig_tr = px.pie(df_transp, values='ValorNF_Clean', names='Transport.', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig_tr, use_container_width=True)

        with col_b:
            st.markdown("**Reflexos das Notas (Sucesso vs. Pendente/Devolução)**")
            if 'Dt. Entrega Real' in df_filtrado_log.columns and not df_filtrado_log.empty:
                def categorizar_reflexo(r):
                    if pd.notna(r.get('NF. Devolucao')) and str(r.get('NF. Devolucao')).strip() not in ['', 'nan']:
                        return 'Com Devolução'
                    elif pd.notna(r.get('Dt. Entrega Real')) and str(r.get('Dt. Entrega Real')).strip() != '':
                        return 'Entregue com Sucesso'
                    else:
                        return 'Em Trânsito / Pendente'

                df_filtrado_log['Status_Reflexo'] = df_filtrado_log.apply(categorizar_reflexo, axis=1)
                fig_ent = px.histogram(df_filtrado_log, x='Status_Reflexo', color='Status_Reflexo', color_discrete_map={'Entregue com Sucesso': '#2ecc71', 'Em Trânsito / Pendente': '#f39c12', 'Com Devolução': '#e74c3c'})
                fig_ent.update_layout(xaxis_title=None, yaxis_title="Qtd NFs")
                st.plotly_chart(fig_ent, use_container_width=True)

        st.markdown(f"**Detalhamento de Romaneios e Expedição Logística ({len(df_filtrado_log)} NFs registradas)**")
        cols_exibicao = [c for c in [col_nf_nome, col_ped_nome, 'Cliente', 'Transport.', 'Dt. Romaneio', 'Dt. Entrega Real', col_v, 'NF. Devolucao', 'Comentario'] if c in df_filtrado_log.columns]
        st.dataframe(df_filtrado_log[cols_exibicao], use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Erro ao carregar controle logístico: {str(e)}")

# 8. MAIN
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

    # MANTÉM AS DUAS ABAS ATIVAS
    aba_bi, aba_logistica = st.tabs(["📊 BI Operações & Separação", "🚚 Controle Logístico & Entregas"])

    with aba_bi:
        df_pedidos = carregar_dados_painel()
        lista_status = ["00-Sem Saldo", "10-Ag.Inicio Operação", "30-Em Separação", "60-Ag.Faturamento Venda", "65-Ag.Transportadora"]
        renderizar_blocos_status(df_pedidos, lista_status)
        st.markdown("---")

        status_ativo = st.session_state.get('status_selecionado', 'Todos')
        if status_ativo != "Todos" and not df_pedidos.empty:
            cod_s = status_ativo.split('-')[0]
            df_filtrado = df_pedidos[df_pedidos['Status_Codigo'] == cod_s]
        else:
            df_filtrado = df_pedidos.copy()

        renderizar_graficos(df_filtrado, status_ativo)
        st.markdown("---")

        st.subheader(f"📋 Relação Detalhada dos Pedidos - [{status_ativo}]")
        if not df_filtrado.empty:
            st.dataframe(df_filtrado[['Pedido', 'Cliente', 'AbertoEm', 'Volumes', 'Status']], use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum pedido encontrado.")

    with aba_logistica:
        renderizar_controle_logistico(f_base)

if __name__ == "__main__":
    main()
