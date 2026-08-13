import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
import os


# 1. CONFIGURAÇÃO DA PÁGINA E BRANDING

NOME_APLICACAO = "Painel Master Higimed"
URL_LOGO_MASTER_HIGIMED = "https://via.placeholder.com/200x60.png?text=Master+Higimed"  # Substitua pelo link direto/caminho local da logo

st.set_page_config(
    page_title=NOME_APLICACAO,
    page_icon="📦",
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

# Recomenda-se colocar as chaves no arquivo .streamlit/secrets.toml
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", "https://seu-projeto.supabase.co"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", "sua-chave-anon-key"))

@st.cache_resource
def iniciar_conexao_supabase() -> Client:
    """Inicializa o cliente do Supabase."""
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = iniciar_conexao_supabase()



# 3. UPLOAD CSV

def processar_e_injetar_csv(file_upload):
    """
    Lê o CSV enviado, insere novos clientes no BD, mapeia os IDs
    e injeta os pedidos na tabela 'pedidos' do Supabase.
    """
    try:
        # Tenta ler o CSV com a codificação correta
        try:
            df = pd.read_csv(file_upload, encoding='latin1', sep=None, engine='python')
        except Exception:
            file_upload.seek(0)
            df = pd.read_csv(file_upload, encoding='utf-8', sep=None, engine='python')

        # Normalização dos nomes das colunas esperadas
        df.columns = [col.strip() for col in df.columns]

        st.info(f"⏳ Processando {len(df)} registros do arquivo...")

        # --- A. Garantir/Inserir Clientes Únicos ---
        clientes_unicos = df['Cliente'].dropna().unique().tolist()
        
        # Busca clientes já cadastrados
        res_clientes = supabase.table('clientes').select('id, nome').execute()
        df_clientes_bd = pd.DataFrame(res_clientes.data)

        if not df_clientes_bd.empty:
            novos_clientes = [c for c in clientes_unicos if c not in df_clientes_bd['nome'].values]
        else:
            novos_clientes = clientes_unicos

        if novos_clientes:
            payload_clientes = [{'nome': c} for c in novos_clientes]
            supabase.table('clientes').insert(payload_clientes).execute()
            # Recarrega a tabela de clientes para pegar os novos IDs
            res_clientes = supabase.table('clientes').select('id, nome').execute()
            df_clientes_bd = pd.DataFrame(res_clientes.data)

        mapa_clientes = dict(zip(df_clientes_bd['nome'], df_clientes_bd['id']))

        # --- B. Mapear Status IDs ---
        res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
        df_status_bd = pd.DataFrame(res_status.data)
        
        # Cria um mapeamento concatenado (ex: "10-Ag.Inicio Operação" -> ID)
        mapa_status = {}
        for _, row in df_status_bd.iterrows():
            chave_completa = f"{row['codigo']}-{row['descricao']}"
            mapa_status[chave_completa] = row['id']

        # --- C. Preparar Payload da Tabela Pedidos ---
        df['aberto_em'] = pd.to_datetime(df['AbertoEm'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%dT%H:%M:%S')
        df['volumes'] = pd.to_numeric(df['Volumes'], errors='coerce').fillna(0).astype(int)
        df['cliente_id'] = df['Cliente'].map(mapa_clientes)
        df['status_id'] = df['Status'].map(mapa_status)

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

        # --- D. Injeção no Banco de Dados ---
        if payload_pedidos:
            # Insere em lotes de 100 para evitar timeout de API
            tamanho_lote = 100
            for i in range(0, len(payload_pedidos), tamanho_lote):
                lote = payload_pedidos[i:i + tamanho_lote]
                supabase.table('pedidos').insert(lote).execute()

            st.success(f"✅ Sucesso! {len(payload_pedidos)} pedidos injetados no Supabase.")
            st.cache_data.clear()  # Limpa o cache para recarregar o painel
        else:
            st.warning("⚠️ Nenhum pedido válido encontrado para injeção.")

    except Exception as e:
        st.error(f"❌ Erro durante o processamento do CSV: {str(e)}")



# 4. CARREGAMENTO DOS DADOS PARA O DASHBOARD (BI)

@st.cache_data(ttl=60)
def carregar_dados_painel() -> pd.DataFrame:
    """Busca os dados relacioandos via Supabase API."""
    try:
        # Consulta com JOIN entre Pedidos, Clientes e Status
        query = """
            id,
            numero_pedido,
            aberto_em,
            volumes,
            clientes (nome),
            status_separacao (codigo, descricao)
        """
        response = supabase.table('pedidos').select(query).execute()
        
        if not response.data:
            return pd.DataFrame()

        # Normaliza a estrutura JSON do JOIN para colunas simples
        registros = []
        for item in response.data:
            registros.append({
                'Pedido': item['numero_pedido'],
                'AbertoEm': item['aberto_em'],
                'Cliente': item['clientes']['nome'] if item.get('clientes') else 'N/I',
                'Volumes': item['volumes'],
                'Status_Codigo': item['status_separacao']['codigo'] if item.get('status_separacao') else '00',
                'Status': f"{item['status_separacao']['codigo']}-{item['status_separacao']['descricao']}" if item.get('status_separacao') else 'Indefinido'
            })

        return pd.DataFrame(registros)
    except Exception as e:
        st.error(f"Erro ao conectar ao Supabase: {str(e)}")
        return pd.DataFrame()



# 5. COMPONENTES VISUAIS E PAINEL MASTER

def renderizar_blocos_status(df: pd.DataFrame, status_list: list):
    """Renderiza os botões clicáveis estilo MTC para filtrar o painel."""
    st.subheader("Painel de Acompanhamento de Separações")
    
    if 'status_selecionado' not in st.session_state:
        st.session_state.status_selecionado = "Todos"

    colunas = st.columns(len(status_list) + 1)

    # Botão "Todos"
    with colunas[0]:
        qtd_total = len(df)
        tipo = "primary" if st.session_state.status_selecionado == "Todos" else "secondary"
        if st.button(f"**Todos**\n### {qtd_total}", key="btn_status_todos", use_container_width=True, type=tipo):
            st.session_state.status_selecionado = "Todos"
            st.rerun()

    # Botões dinâmicos por Status do BD
    for idx, status in enumerate(status_list, start=1):
        qtd = len(df[df['Status'] == status]) if not df.empty else 0
        with colunas[idx]:
            tipo = "primary" if st.session_state.status_selecionado == status else "secondary"
            if st.button(f"**{status}**\n### {qtd}", key=f"btn_status_{idx}", use_container_width=True, type=tipo):
                st.session_state.status_selecionado = status
                st.rerun()


def renderizar_graficos(df: pd.DataFrame, status_ativo: str):
    """Exibe o gráfico de volumetria/quantidade por cliente para o status selecionado."""
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



# 6. PONTO DE ENTRADA 

def main():
    # --- BARRA LATERAL (SIDEBAR) ---
    st.sidebar.image(URL_LOGO_MASTER_HIGIMED, use_column_width=True)
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

    # --- PÁGINA PRINCIPAL ---
    st.title(f"🏢 {NOME_APLICACAO}")
    st.markdown("Acompanhamento em tempo real dos pedidos de separação e fluxo logístico.")
    st.markdown("---")

    # Carrega os dados atualizados do banco
    df_pedidos = carregar_dados_painel()

    # Mapeia lista de status únicos do banco
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
