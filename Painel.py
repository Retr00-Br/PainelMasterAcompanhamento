import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
import os

# 1. CONFIGURAÇÃO DA PÁGINA E BRANDING

NOME_APLICACAO = "Painel Master Higimed"
CAMINHO_LOGO = "logo.webp"  # Certifique-se de que o arquivo 'logo.webp' está na raiz do seu projeto no Git

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
    """Inicializa e mantém em cache a conexão com o Supabase de forma segura."""
    try:
        # Busca no st.secrets ou em variáveis de ambiente
        if "supabase" in st.secrets:
            url = st.secrets["supabase"]["SUPABASE_URL"]
            key = st.secrets["supabase"]["SUPABASE_KEY"]
        else:
            url = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
            key = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))

        # Trata barras residuais no final da URL para evitar erro PGRST125
        url_limpa = url.rstrip("/")
        return create_client(url_limpa, key)
    except Exception as e:
        st.error(f"Erro ao carregar credenciais do Supabase: {str(e)}")
        st.stop()

supabase = iniciar_conexao_supabase()

# 3. INJEÇÃO DE DADOS (UPLOAD CSV)

def processar_e_injetar_csv(file_upload):
    """
    Lê o CSV enviado, insere novos clientes no BD, mapeia os IDs
    e injeta os pedidos na tabela 'pedidos' do Supabase.
    """
    try:
        try:
            df = pd.read_csv(file_upload, encoding='latin1', sep=None, engine='python')
        except Exception:
            file_upload.seek(0)
            df = pd.read_csv(file_upload, encoding='utf-8', sep=None, engine='python')

        df.columns = [col.strip() for col in df.columns]

        st.info(f"⏳ Processando {len(df)} registros do arquivo...")

        # --- A. Garantir/Inserir Clientes Únicos ---
        clientes_unicos = df['Cliente'].dropna().unique().tolist()
        
        res_clientes = supabase.table('clientes').select('id, nome').execute()
        df_clientes_bd = pd.DataFrame(res_clientes.data)

        if not df_clientes_bd.empty:
            novos_clientes = [c for c in clientes_unicos if c not in df_clientes_bd['nome'].values]
        else:
            novos_clientes = clientes_unicos

        if novos_clientes:
            payload_clientes = [{'nome': c} for c in novos_clientes]
            supabase.table('clientes').insert(payload_clientes).execute()
            res_clientes = supabase.table('clientes').select('id, nome').execute()
            df_clientes_bd = pd.DataFrame(res_clientes.data)

        mapa_clientes = dict(zip(df_clientes_bd['nome'], df_clientes_bd['id']))

        # --- B. Mapear Status IDs (Resiliente e Flexível) ---
        res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
        df_status_bd = pd.DataFrame(res_status.data)
        
        mapa_status = {}
        if not df_status_bd.empty:
            for _, row in df_status_bd.iterrows():
                cod = str(row['codigo']).strip()
                desc = str(row['descricao']).strip()
                status_id = row['id']

                # Aceita diferentes padrões de escrita que podem vir do CSV
                mapa_status[f"{cod}-{desc}"] = status_id
                mapa_status[cod] = status_id
                mapa_status[desc] = status_id

        # --- C. Preparar Payload ---
        df['aberto_em'] = pd.to_datetime(df['AbertoEm'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%dT%H:%M:%S')
        df['volumes'] = pd.to_numeric(df['Volumes'], errors='coerce').fillna(0).astype(int)
        df['cliente_id'] = df['Cliente'].map(mapa_clientes)
        
        # Limpa espaços extras no campo de Status do CSV antes do de-para
        df['status_limpo'] = df['Status'].astype(str).str.strip()
        df['status_id'] = df['status_limpo'].map(mapa_status)

        payload_pedidos = []
        for _, row in df.iterrows():
            if pd.notna(row['cliente_id']) and pd.notna(row['status_id']):
                payload_pedidos.append({
                    'numero_pedido': int(row['Pedido']),
                    'cliente_id': int(row['cliente_id']),
                    'status_id': int(row['status_id']),
                    'aberto_em': row['aberto_em'],
                    'volumes': int(row['volumes'])
                })

        # --- D. Injeção no Banco de Dados em Lotes ---
        if payload_pedidos:
            tamanho_lote = 100
            for i in range(0, len(payload_pedidos), tamanho_lote):
                lote = payload_pedidos[i:i + tamanho_lote]
                supabase.table('pedidos').insert(lote).execute()

            st.success(f"✅ Sucesso! {len(payload_pedidos)} pedidos injetados no Supabase.")
            st.cache_data.clear()
        else:
            st.warning("⚠️ Nenhum pedido válido encontrado para injeção.")

    except Exception as e:
        st.error(f"❌ Erro durante o processamento do CSV: {str(e)}")

# 4. CARREGAMENTO DOS DADOS PARA O DASHBOARD

@st.cache_data(ttl=60)
def carregar_dados_painel() -> pd.DataFrame:
    """Busca os dados relacionados via Supabase API utilizando JOIN normalizado."""
    try:
        # Query simplificada para evitar falhas de interpretação na rota API
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
        
# 5. COMPONENTES VISUAIS

def renderizar_blocos_status(df: pd.DataFrame, status_list: list):
    """Renderiza os botões clicáveis para filtrar o painel."""
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
    """Exibe os gráficos de pedidos e volumetria por cliente."""
    col1, col2 = st.columns(2)

    with col1:
        st.subheader(f"Top 10 Clientes com Mais Pedidos - [{status_ativo}]")
        if not df.empty:
            top_clientes = df['Cliente'].value_counts().reset_index().head(10)
            top_clientes.columns = ['Cliente', 'Qtd_Pedidos']

            fig = px.bar(
                top_clientes,
                x='Qtd_Pedidos',
                y='Cliente',
                orientation='h',
                color='Qtd_Pedidos',
                color_continuous_scale='Blues_r',
                text='Qtd_Pedidos'
            )
            fig.update_traces(textposition='outside')
            fig.update_layout(
                yaxis={'categoryorder': 'total ascending'},
                xaxis_title=None, yaxis_title=None,
                coloraxis_showscale=False, height=380,
                margin=dict(l=0, r=0, t=20, b=0)
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Nenhum pedido encontrado para o filtro selecionado.")

    with col2:
        st.subheader(f"Distribuição de Volumes - [{status_ativo}]")
        if not df.empty and df['Volumes'].sum() > 0:
            top_volumes = df.groupby('Cliente')['Volumes'].sum().reset_index().sort_values(by='Volumes', ascending=False).head(10)
            fig_vol = px.bar(
                top_volumes,
                x='Volumes',
                y='Cliente',
                orientation='h',
                color='Volumes',
                color_continuous_scale='Teal',
                text='Volumes'
            )
            fig_vol.update_traces(textposition='outside')
            fig_vol.update_layout(
                yaxis={'categoryorder': 'total ascending'},
                xaxis_title=None, yaxis_title=None,
                coloraxis_showscale=False, height=380,
                margin=dict(l=0, r=0, t=20, b=0)
            )
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("Sem registro de volumes para os pedidos deste status.")

# 6. PONTO DE ENTRADA (MAIN)

def main():
    # --- BARRA LATERAL (SIDEBAR) ---
    if os.path.exists(CAMINHO_LOGO):
        st.sidebar.image(CAMINHO_LOGO, use_container_width=True)
    else:
        st.sidebar.title(NOME_APLICACAO)

    st.sidebar.markdown("---")

    # Módulo de Injeção de Dados (Upload)
    st.sidebar.header("📥 Injeção de Dados (CSV)")
    arquivo_csv = st.sidebar.file_uploader("Selecione o arquivo CSV:", type=["csv"])

    if arquivo_csv is not None:
        if st.sidebar.button("🚀 Injetar no Supabase", use_container_width=True):
            processar_e_injetar_csv(arquivo_csv)

    st.sidebar.markdown("---")
    st.sidebar.caption("Master Higimed © 2026 - Gestão de Operações")

    # --- PÁGINA PRINCIPAL COM LOGO ---
    col_logo, col_titulo = st.columns([1, 4])
    with col_logo:
        if os.path.exists(CAMINHO_LOGO):
            st.image(CAMINHO_LOGO, width=150)
    with col_titulo:
        st.title(NOME_APLICACAO)
        st.caption("Acompanhamento em tempo real dos pedidos de separação e fluxo logístico.")

    st.markdown("---")

    # Carrega os dados atualizados do banco
    df_pedidos = carregar_dados_painel()

    # Mapeia lista de status para a esteira
    lista_status = [
        "00-Sem Saldo",
        "10-Ag.Inicio Operação",
        "30-Em Separação",
        "60-Ag.Faturamento Venda",
        "65-Ag.Transportadora"
    ]

    # Renderiza os blocos superiores interativos
    renderizar_blocos_status(df_pedidos, lista_status)
    st.markdown("---")

    # Aplica o filtro selecionado pelo usuário nos botões
    status_ativo = st.session_state.get('status_selecionado', 'Todos')
    if status_ativo != "Todos" and not df_pedidos.empty:
        df_filtrado = df_pedidos[df_pedidos['Status'] == status_ativo]
    else:
        df_filtrado = df_pedidos.copy()

    # Exibe gráficos dinâmicos
    renderizar_graficos(df_filtrado, status_ativo)
    st.markdown("---")

    # Tabela analítica detalhada
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
