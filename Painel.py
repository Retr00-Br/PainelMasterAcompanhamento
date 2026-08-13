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
    Lê o CSV, faz a correspondência com Clientes e Status e insere os Pedidos no Supabase.
    Exibe diagnósticos detalhados caso encontre inconsistências.
    """
    try:
        # 1. Leitura do arquivo CSV com encoding resiliente
        try:
            df = pd.read_csv(file_upload, encoding='latin1', sep=None, engine='python')
        except Exception:
            file_upload.seek(0)
            df = pd.read_csv(file_upload, encoding='utf-8', sep=None, engine='python')

        # Normalização dos nomes das colunas
        df.columns = [str(col).strip() for col in df.columns]

        st.info(f"⏳ Lendo {len(df)} registros do arquivo CSV...")

        # --- A. SINCRONIZAÇÃO DE CLIENTES ---
        # Normalização de strings para evitar falhas por espaços em branco
        df['Cliente_Clean'] = df['Cliente'].astype(str).str.strip()
        clientes_unicos = [c for c in df['Cliente_Clean'].unique() if c and c.lower() != 'nan']

        res_clientes = supabase.table('clientes').select('id, nome').execute()
        df_clientes_bd = pd.DataFrame(res_clientes.data)

        if not df_clientes_bd.empty:
            df_clientes_bd['nome_clean'] = df_clientes_bd['nome'].astype(str).str.strip()
            nomes_existentes = set(df_clientes_bd['nome_clean'].values)
            novos_clientes = [c for c in clientes_unicos if c not in nomes_existentes]
        else:
            novos_clientes = clientes_unicos

        if novos_clientes:
            st.write(f"➕ Cadastrando {len(novos_clientes)} novos clientes no banco...")
            payload_clientes = [{'nome': c} for c in novos_clientes]
            supabase.table('clientes').insert(payload_clientes).execute()
            
            # Recarrega a lista atualizada de clientes
            res_clientes = supabase.table('clientes').select('id, nome').execute()
            df_clientes_bd = pd.DataFrame(res_clientes.data)
            df_clientes_bd['nome_clean'] = df_clientes_bd['nome'].astype(str).str.strip()

        mapa_clientes = dict(zip(df_clientes_bd['nome_clean'], df_clientes_bd['id']))

        # --- B. MAPEAMENTO DE STATUS ---
        res_status = supabase.table('status_separacao').select('id, codigo, descricao').execute()
        df_status_bd = pd.DataFrame(res_status.data)

        mapa_status = {}
        if not df_status_bd.empty:
            for _, row in df_status_bd.iterrows():
                s_id = int(row['id'])
                cod = str(row['codigo']).strip() if pd.notna(row['codigo']) else ""
                desc = str(row['descricao']).strip() if pd.notna(row['descricao']) else ""

                if cod:
                    mapa_status[cod] = s_id
                if desc:
                    mapa_status[desc] = s_id
                if cod and desc:
                    mapa_status[f"{cod}-{desc}"] = s_id

        # --- C. CONSTRUÇÃO DO PAYLOAD E VALIDAÇÃO ---
        payload_pedidos = []
        falhas_cliente = set()
        falhas_status = set()

        for _, row in df.iterrows():
            pedido_val = row.get('Pedido')
            cliente_str = str(row.get('Cliente_Clean', '')).strip()
            status_str = str(row.get('Status', '')).strip()

            if pd.isna(pedido_val):
                continue

            # Mapeamento do Cliente
            c_id = mapa_clientes.get(cliente_str)

            # Mapeamento de Status
            s_id = mapa_status.get(status_str)
            if s_id is None and '-' in status_str:
                cod_extraido = status_str.split('-')[0].strip()
                s_id = mapa_status.get(cod_extraido)

            # Registra inconsistências se houver
            if c_id is None:
                falhas_cliente.add(cliente_str)
            if s_id is None:
                falhas_status.add(status_str)

            # Só adiciona se AMBOS existirem (notando que s_id pode ser 0)
            if c_id is not None and s_id is not None:
                # Tratamento da data
                data_parsed = pd.to_datetime(row.get('AbertoEm'), dayfirst=True, errors='coerce')
                data_iso = data_parsed.strftime('%Y-%m-%dT%H:%M:%S') if pd.notna(data_parsed) else None

                # Tratamento de volumes
                vol_val = pd.to_numeric(row.get('Volumes'), errors='coerce')
                vol_int = int(vol_val) if pd.notna(vol_val) else 0

                payload_pedidos.append({
                    'numero_pedido': int(pedido_val),
                    'cliente_id': int(c_id),
                    'status_id': int(s_id),
                    'aberto_em': data_iso,
                    'volumes': vol_int
                })

        # --- D. EXIBIÇÃO DE ERROS DE DE-PARA (SE HOUVER) ---
        if falhas_cliente:
            st.error(f"❌ {len(falhas_cliente)} cliente(s) do CSV não foram encontrados/cadastrados: {list(falhas_cliente)[:5]}")
        if falhas_status:
            st.error(f"❌ {len(falhas_status)} status do CSV não foram mapeados: {list(falhas_status)}")

        # --- E. INJEÇÃO DOS DADOS EM LOTES ---
        if payload_pedidos:
            tamanho_lote = 100
            for i in range(0, len(payload_pedidos), tamanho_lote):
                lote = payload_pedidos[i:i + tamanho_lote]
                supabase.table('pedidos').insert(lote).execute()

            st.success(f"✅ Sucesso! {len(payload_pedidos)} pedidos injetados no Supabase.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.warning("⚠️ Nenhum pedido passou na validação de de-para para injeção.")

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
