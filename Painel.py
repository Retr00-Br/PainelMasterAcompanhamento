import pandas as pd
import streamlit as st
import plotly.express as px
from supabase import create_client, Client
import os

# 1. CONFIGURAÇÃO DA PÁGINA E BRANDING

NOME_APLICACAO = "Painel Master Higimed"
CAMINHO_LOGO = "logo.webp"

st.set_page_config(
    page_title=NOME_APLICACAO,
    page_icon=CAMINHO_LOGO if os.path.exists(CAMINHO_LOGO) else "📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilização CSS customizada
st.markdown("""
    <style>
    .stAppHeader { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 5px solid #0056b3; }
    div[data-testid="stMetricValue"] > div { font-size: 26px; color: #0056b3; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# 2. CONEXÃO COM O SUPABASE (BD)

@st.cache_resource
def iniciar_conexao_supabase() -> Client:
    """Inicializa e mantém em cache a conexão com o Supabase."""
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

# 3. INJEÇÃO DE DADOS (UPLOAD CSV/EXCEL SEM DUPLICIDADE)

def processar_e_injetar_arquivo(file_upload):
    """
    Lê CSV ou Excel, cadastra clientes/status e injeta/atualiza os pedidos no Supabase 
    evitando duplicidade via verificação prévia de 'numero_pedido'.
    """
    try:
        nome_arquivo = file_upload.name.lower()
        if nome_arquivo.endswith('.xlsx') or nome_arquivo.endswith('.xls'):
            df = pd.read_excel(file_upload)
        else:
            try:
                file_upload.seek(0)
                df = pd.read_csv(file_upload, encoding='utf-8-sig', sep=None, engine='python')
            except Exception:
                file_upload.seek(0)
                df = pd.read_csv(file_upload, encoding='latin1', sep=None, engine='python')

        df.columns = [str(col).replace('\ufeff', '').strip() for col in df.columns]
        st.info(f"⏳ Lendo {len(df)} registros do arquivo...")

        cols_lower = {col.lower(): col for col in df.columns}

        col_cliente = cols_lower.get('cliente_nome') or cols_lower.get('cliente')
        col_pedido = (cols_lower.get('pv_limpo') or cols_lower.get('pv') or 
                      cols_lower.get('numero_pedido') or cols_lower.get('pedido') or 
                      cols_lower.get('(wms) ped. orig.') or cols_lower.get('nf'))
        col_data = (cols_lower.get('emissão pv_data') or cols_lower.get('emissão pv') or 
                    cols_lower.get('abertoem') or cols_lower.get('emissao nf'))
        col_vol = cols_lower.get('volume') or cols_lower.get('volumes')

        if not col_cliente or not col_pedido:
            st.error(f"❌ Colunas essenciais não encontradas. Colunas disponíveis: {list(df.columns)}")
            return

        # Sincronia de Status
        res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
        df_status_bd = pd.DataFrame(res_status.data) if res_status and res_status.data else pd.DataFrame()

        if df_status_bd.empty:
            status_padrao = [
                {'id': 1, 'codigo': '00', 'descricao': 'Sem Saldo'},
                {'id': 2, 'codigo': '10', 'descricao': 'Ag. Inicio Operacao'},
                {'id': 3, 'codigo': '30', 'descricao': 'Em Separacao'},
                {'id': 4, 'codigo': '60', 'descricao': 'Ag. Faturamento Venda'},
                {'id': 5, 'codigo': '65', 'descricao': 'Ag. Transportadora'}
            ]
            supabase.table('status_separacao').insert(status_padrao).execute()
            res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
            df_status_bd = pd.DataFrame(res_status.data)

        mapa_status_cod = {}
        for _, row in df_status_bd.iterrows():
            s_id = int(row['id'])
            cod = str(row['codigo']).strip().lower() if pd.notna(row['codigo']) else ""
            desc = str(row['descricao']).strip().lower() if pd.notna(row['descricao']) else ""
            if cod: mapa_status_cod[cod] = s_id
            if desc: mapa_status_cod[desc] = s_id

        status_padrao_id = int(df_status_bd.iloc[0]['id'])

        # Sincronia de Clientes
        df['Cliente_Clean'] = df[col_cliente].astype(str).str.strip().str.slice(0, 255)
        clientes_unicos = [c for c in df['Cliente_Clean'].unique() if c and c.lower() not in ['nan', 'none', '']]

        res_clientes = supabase.table('clientes').select('id, nome').execute()
        df_clientes_bd = pd.DataFrame(res_clientes.data) if res_clientes and res_clientes.data else pd.DataFrame(columns=['id', 'nome'])

        if not df_clientes_bd.empty:
            df_clientes_bd['nome_clean'] = df_clientes_bd['nome'].astype(str).str.strip()
            nomes_existentes = set(df_clientes_bd['nome_clean'].str.lower().values)
            novos_clientes = [c for c in clientes_unicos if c.lower() not in nomes_existentes]
        else:
            novos_clientes = clientes_unicos

        if novos_clientes:
            st.write(f"➕ Cadastrando {len(novos_clientes)} novos clientes...")
            payload_clientes = [{'nome': c} for c in novos_clientes]
            supabase.table('clientes').insert(payload_clientes).execute()

            res_clientes = supabase.table('clientes').select('id, nome').execute()
            df_clientes_bd = pd.DataFrame(res_clientes.data)
            df_clientes_bd['nome_clean'] = df_clientes_bd['nome'].astype(str).str.strip()

        mapa_clientes = {str(row['nome_clean']).lower(): int(row['id']) for _, row in df_clientes_bd.iterrows()}

        # Busca pedidos existentes no Supabase para evitar duplicidade
        res_pedidos_existentes = supabase.table('pedidos').select('numero_pedido, id').execute()
        mapa_pedidos_existentes = {}
        if res_pedidos_existentes and res_pedidos_existentes.data:
            mapa_pedidos_existentes = {p['numero_pedido']: p['id'] for p in res_pedidos_existentes.data if p.get('numero_pedido')}

        novos_pedidos = []
        pedidos_para_atualizar = []

        for _, row in df.iterrows():
            pedido_val = row.get(col_pedido)
            cliente_str = str(row.get('Cliente_Clean', '')).strip()

            if pd.isna(pedido_val) or str(pedido_val).strip() in ['', 'nan']:
                continue

            pedido_digits = ''.join(filter(str.isdigit, str(pedido_val)))
            if not pedido_digits:
                continue
            pedido_num = int(pedido_digits)

            c_id = mapa_clientes.get(cliente_str.lower())

            if pd.notna(row.get('NF')) and str(row.get('NF')).strip() not in ['', 'nan']:
                cod_inferido = '65' if pd.notna(row.get('Transport.')) else '60'
            elif pd.notna(row.get('FinalizacaoConferencia')) and str(row.get('FinalizacaoConferencia')).strip() not in ['', 'nan']:
                cod_inferido = '60'
            elif pd.notna(row.get('Dt.Inicio Separacao')) and str(row.get('Dt.Inicio Separacao')).strip() not in ['', 'nan']:
                cod_inferido = '30'
            else:
                cod_inferido = '10'

            s_id = mapa_status_cod.get(cod_inferido.lower(), status_padrao_id)

            if c_id is not None:
                raw_data = row.get(col_data) if col_data else None
                data_parsed = pd.to_datetime(raw_data, dayfirst=True, errors='coerce')
                data_iso = data_parsed.strftime('%Y-%m-%dT%H:%M:%S') if pd.notna(data_parsed) else None

                raw_vol = row.get(col_vol) if col_vol else 0
                vol_val = pd.to_numeric(raw_vol, errors='coerce')
                vol_int = int(vol_val) if pd.notna(vol_val) else 0

                item_pedido = {
                    'numero_pedido': pedido_num,
                    'cliente_id': int(c_id),
                    'status_id': int(s_id),
                    'aberto_em': data_iso,
                    'volumes': vol_int
                }

                if pedido_num in mapa_pedidos_existentes:
                    item_pedido['id'] = mapa_pedidos_existentes[pedido_num]
                    pedidos_para_atualizar.append(item_pedido)
                else:
                    novos_pedidos.append(item_pedido)

        # Inserção e Atualização em lotes de 100
        tamanho_lote = 100
        if novos_pedidos:
            st.write(f"📦 Inserindo {len(novos_pedidos)} novos pedidos...")
            for i in range(0, len(novos_pedidos), tamanho_lote):
                lote = novos_pedidos[i:i + tamanho_lote]
                supabase.table('pedidos').insert(lote).execute()

        if pedidos_para_atualizar:
            st.write(f"🔄 Atualizando {len(pedidos_para_atualizar)} pedidos existentes...")
            for p in pedidos_para_atualizar:
                supabase.table('pedidos').update({
                    'cliente_id': p['cliente_id'],
                    'status_id': p['status_id'],
                    'aberto_em': p['aberto_em'],
                    'volumes': p['volumes']
                }).eq('id', p['id']).execute()

        st.success(f"✅ Sucesso! Dados sincronizados sem duplicidades.")
        st.cache_data.clear()
        st.rerun()

    except Exception as e:
        st.error(f"❌ Erro no processamento do arquivo: {str(e)}")

# 4. CARREGAMENTO DOS DADOS PARA O DASHBOARD

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
        st.error(f"Erro ao conectar ao Supabase: {str(e)}")
        return pd.DataFrame()

# 5. COMPONENTES VISUAIS - BI DE OPERAÇÕES DE SEPARAÇÃO

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
            st.info("Nenhum pedido encontrado para o filtro selecionado.")

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
            st.info("Sem registro de volumes para os pedidos deste status.")

# 6. COMPONENTES VISUAIS - CONTROLE LOGÍSTICO (NOVA ABA)

def renderizar_controle_logistico(file_upload):
    st.subheader("🚛 Gestão e Controle Logístico de Entregas")

    if file_upload is not None:
        try:
            file_upload.seek(0)
            if file_upload.name.endswith('.xlsx') or file_upload.name.endswith('.xls'):
                df_log = pd.read_excel(file_upload)
            else:
                df_log = pd.read_csv(file_upload)

            # Métricas rápidas da Logística
            m1, m2, m3, m4 = st.columns(4)
            val_total = df_log['ValorNF.1'].sum() if 'ValorNF.1' in df_log.columns else 0
            transp_count = df_log['Transport.'].nunique() if 'Transport.' in df_log.columns else 0
            entregues = df_log['Dt. Entrega Real'].notna().sum() if 'Dt. Entrega Real' in df_log.columns else 0
            dev = df_log['NF. Devolucao'].notna().sum() if 'NF. Devolucao' in df_log.columns else 0

            m1.metric("Faturamento Processado", f"R$ {val_total:,.2f}")
            m2.metric("Transportadoras Ativas", transp_count)
            m3.metric("Entregas Concluídas", entregues)
            m4.metric("Devoluções Registradas", dev)

            st.markdown("---")

            col_a, col_b = st.columns(2)

            with col_a:
                st.markdown("**Volume Faturado por Transportadora**")
                if 'Transport.' in df_log.columns and 'ValorNF.1' in df_log.columns:
                    df_transp = df_log.groupby('Transport.')['ValorNF.1'].sum().reset_index()
                    fig_tr = px.pie(df_transp, values='ValorNF.1', names='Transport.', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
                    st.plotly_chart(fig_tr, use_container_width=True)

            with col_b:
                st.markdown("**Status de Entregas Realizadas**")
                if 'Dt. Entrega Real' in df_log.columns:
                    df_log['Status_Entrega'] = df_log['Dt. Entrega Real'].apply(lambda x: 'Entregue' if pd.notna(x) else 'Em Trânsito / Pendente')
                    fig_ent = px.histogram(df_log, x='Status_Entrega', color='Status_Entrega', color_discrete_sequence=['#2ecc71', '#e74c3c'])
                    st.plotly_chart(fig_ent, use_container_width=True)

            st.markdown("**Detalhamento de Romaneios e Expedição Logística**")
            cols_exibicao = [c for c in ['NF', 'Cliente', 'Transport.', 'Dt. Romaneio', 'Dt. Entrega Real', 'ValorNF.1', 'Comentario'] if c in df_log.columns]
            st.dataframe(df_log[cols_exibicao], use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Erro ao carregar os dados logísticos do arquivo: {str(e)}")
    else:
        st.info("💡 Faça o upload da planilha de Base de Dados (`.xlsx` ou `.csv`) na barra lateral para carregar a análise logística completa.")

# 7. PONTO DE ENTRADA (MAIN)

def main():
    if os.path.exists(CAMINHO_LOGO):
        st.sidebar.image(CAMINHO_LOGO, use_container_width=True)
    else:
        st.sidebar.title(NOME_APLICACAO)

    st.sidebar.markdown("---")

    st.sidebar.header("📥 Injeção de Dados (CSV / Excel)")
    arquivo_upload = st.sidebar.file_uploader("Selecione o arquivo:", type=["csv", "xlsx", "xls"])

    if arquivo_upload is not None:
        if st.sidebar.button("🚀 Sincronizar com Supabase", use_container_width=True):
            processar_e_injetar_arquivo(arquivo_upload)

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

    # Estrutura de Abas para Separar o BI das Operações e a Gestão Logística
    aba_bi, aba_logistica = st.tabs(["📊 BI Operações & Separação", "🚚 Controle Logístico & Entregas"])

    with aba_bi:
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

    with aba_logistica:
        renderizar_controle_logistico(arquivo_upload)


if __name__ == "__main__":
    main()
