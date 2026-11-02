import streamlit as st

from auth import verificar_autenticacao

from gerador_lotes import (
    inicializar_estado,
    base_carregada,
    obter_base,
    limpar_bases,
    processar_upload_multiplo,
)

from gerador_lotes.ferramentas import (
    render_filtragem,
    render_duplicidade,
    render_servicos,
    render_eventos,
    render_lotes,
    render_lista_rapida,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

st.set_page_config(
    page_title="Gerador de Lotes - COI",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("4")


# ============================================================
# ESTADO DO TEMA
# ============================================================

if "modo_escuro_gerador" not in st.session_state:
    st.session_state.modo_escuro_gerador = False

modo_escuro = st.session_state.modo_escuro_gerador


# ============================================================
# ESTILO
# ============================================================

if modo_escuro:

    st.markdown(
        """
        <style>

        .stApp {
            background-color: #0e1117;
            color: #f1f5f9;
        }

        [data-testid="stAppViewContainer"] {
            background-color: #0e1117;
        }

        [data-testid="stHeader"] {
            background-color: #0e1117;
        }

        [data-testid="stSidebar"] {
            background-color: #161b22;
            border-right: 1px solid #30363d;
        }

        [data-testid="stSidebar"] * {
            color: #f1f5f9;
        }

        h1, h2, h3, h4, h5, h6,
        p, label,
        [data-testid="stMarkdownContainer"] {
            color: #f1f5f9;
        }

        .stCaption,
        [data-testid="stCaptionContainer"] {
            color: #aab4c3 !important;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
        }

        [data-testid="stFileUploader"] {
            background-color: #1b222c;
            border-radius: 10px;
        }

        [data-testid="stFileUploaderDropzone"] {
            background-color: #1b222c;
            border: 1px dashed #4b5563;
        }

        [data-testid="stFileUploaderDropzone"] * {
            color: #dbe4ee !important;
        }

        .stButton > button {
            border-radius: 8px;
            border: 1px solid #3b4654;
        }

        .stButton > button:hover {
            border-color: #64748b;
        }

        input,
        textarea,
        select {
            background-color: #1b222c !important;
            color: #f1f5f9 !important;
            border-color: #3b4654 !important;
        }

        hr {
            border-color: #30363d;
        }

        .card-status {
            font-size: 0.82rem;
            margin-top: -0.15rem;
            margin-bottom: 0.45rem;
        }

        .card-registros {
            font-size: 0.78rem;
            color: #9ca8b7 !important;
            margin-bottom: 0.35rem;
        }

        div[data-testid="stVerticalBlock"] > div {
            gap: 0.45rem;
        }

        [data-testid="stSidebar"] .stButton > button {
            background-color: #212833 !important;
            color: #f1f5f9 !important;
            border: 1px solid #3b4654 !important;
            border-radius: 8px !important;
        }

        [data-testid="stSidebar"] .stButton > button:hover {
            background-color: #2b3441 !important;
            color: #ffffff !important;
            border-color: #64748b !important;
        }

        [data-testid="stSidebar"] .stButton > button p {
            color: #f1f5f9 !important;
        }

        [data-testid="stSidebar"] .stButton > button:hover p {
            color: #ffffff !important;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )

else:

    st.markdown(
        """
        <style>

        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }

        h1 {
            margin-bottom: 0.2rem;
        }

        h2, h3 {
            margin-top: 0.5rem;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px;
        }

        .card-status {
            font-size: 0.82rem;
            margin-top: -0.15rem;
            margin-bottom: 0.45rem;
        }

        .card-registros {
            font-size: 0.78rem;
            color: #6b7280;
            margin-bottom: 0.35rem;
        }

        div[data-testid="stVerticalBlock"] > div {
            gap: 0.45rem;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# AUTENTICAÇÃO
# ============================================================

if not verificar_autenticacao():

    st.warning("Sessão não iniciada ou expirada.")

    if st.button(
        "Ir para o Login",
        use_container_width=True,
    ):
        st.switch_page("app.py")

    st.stop()


# ============================================================
# ESTADO
# ============================================================

inicializar_estado()


# ============================================================
# CONFIGURAÇÃO DAS BASES
# ============================================================

BASES_CONFIG = [
    {
        "nome": "API",
        "chave": "api",
        "icone": "🔵",
        "descricao": "Base principal de API.",
    },
    {
        "nome": "THE",
        "chave": "the",
        "icone": "🟢",
        "descricao": "Base principal de THE.",
    },
    {
        "nome": "Serviços API",
        "chave": "servicos_api",
        "icone": "🔵",
        "descricao": "Serviços vinculados à API.",
    },
    {
        "nome": "Serviços THE",
        "chave": "servicos_the",
        "icone": "🟢",
        "descricao": "Serviços vinculados ao THE.",
    },
    {
        "nome": "Eventos",
        "chave": "eventos",
        "icone": "📋",
        "descricao": "Base de eventos operacionais.",
    },
    {
        "nome": "Lotes",
        "chave": "lotes",
        "icone": "📦",
        "descricao": "Base de lotes operacionais.",
    },
]


# ============================================================
# CONFIGURAÇÃO DAS FERRAMENTAS
# ============================================================

FERRAMENTAS = [
    {
        "nome": "Filtragem",
        "icone": "🔎",
        "descricao": "Seleção de argumentos.",
    },
    {
        "nome": "Duplicidade",
        "icone": "♻️",
        "descricao": "Análise de registros duplicados.",
    },
    {
        "nome": "Serviços",
        "icone": "🛠️",
        "descricao": "Análise e tratamento de serviços.",
    },
    {
        "nome": "Eventos",
        "icone": "📋",
        "descricao": "Consulta e análise de eventos.",
    },
    {
        "nome": "Lotes",
        "icone": "📦",
        "descricao": "Consulta e análise de lotes.",
    },
    {
        "nome": "Lista Rápida",
        "icone": "⚡",
        "descricao": "Operações rápidas sobre as bases.",
    },
]


# ============================================================
# ROTEAMENTO DAS FERRAMENTAS
# ============================================================

ferramenta_atual = st.session_state.get("ferramenta_atual")

if ferramenta_atual == "filtragem":
    render_filtragem()
    st.stop()

if ferramenta_atual == "duplicidade":
    render_duplicidade()
    st.stop()

if ferramenta_atual == "servicos":
    render_servicos()
    st.stop()

if ferramenta_atual == "eventos":
    render_eventos()
    st.stop()

if ferramenta_atual == "lotes":
    render_lotes()
    st.stop()

if ferramenta_atual == "lista_rapida":
    render_lista_rapida()
    st.stop()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("### 📦 Gerador de Lotes")

    st.caption(
        f"Usuário: **{st.session_state.get('usuario_logado', '')}**"
    )

    st.caption(
        f"Perfil: **{st.session_state.get('perfil', '').upper()}**"
    )

    st.divider()

    # --------------------------------------------------------
    # TEMA
    # --------------------------------------------------------

    novo_modo_escuro = st.toggle(
        "🌙 Modo escuro",
        value=modo_escuro,
        key="toggle_modo_escuro_gerador",
    )

    if novo_modo_escuro != modo_escuro:
        st.session_state.modo_escuro_gerador = novo_modo_escuro
        st.rerun()

    st.divider()

    # --------------------------------------------------------
    # NAVEGAÇÃO
    # --------------------------------------------------------

    if st.button(
        "🛠️  Ferramentas Operacionais",
        key="btn_voltar_ferramentas",
        use_container_width=True,
    ):
        st.session_state.ferramenta_atual = None
        st.switch_page("pages/4_Ferramentas_Operacionais.py")

    if st.button(
        "🏠  Menu Principal",
        key="btn_voltar_principal",
        use_container_width=True,
    ):
        st.session_state.ferramenta_atual = None
        st.switch_page("app.py")

    st.divider()

    # --------------------------------------------------------
    # LIMPAR BASES
    # --------------------------------------------------------

    if st.button(
        "🗑️  Limpar todas as bases",
        key="btn_limpar_todas_bases",
        use_container_width=True,
    ):
        limpar_bases()
        st.rerun()


# ============================================================
# CABEÇALHO
# ============================================================

st.title("📦 Gerador de Lotes")

st.caption(
    "Gerencie as bases operacionais e acesse as ferramentas do módulo."
)


# ============================================================
# RESUMO
# ============================================================

total_bases_carregadas = sum(
    1
    for base in BASES_CONFIG
    if base_carregada(base["chave"])
)

st.markdown(
    f"**Bases carregadas:** "
    f"{total_bases_carregadas} de {len(BASES_CONFIG)}"
)


# ============================================================
# BASES DE DADOS
# ============================================================

st.markdown("### 📁 Bases de Dados")

colunas_bases = st.columns(3)

for indice, base in enumerate(BASES_CONFIG):

    coluna = colunas_bases[indice % 3]

    chave = base["chave"]
    carregada = base_carregada(chave)
    df = obter_base(chave)

    with coluna:

        with st.container(border=True):

            st.markdown(
                f"#### {base['icone']} {base['nome']}"
            )

            st.caption(base["descricao"])

            if carregada:

                quantidade = len(df)

                st.markdown(
                    '<div class="card-status">'
                    '🟢 <strong>Carregada</strong>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                st.markdown(
                    f'<div class="card-registros">'
                    f'{quantidade:,}'.replace(",", ".")
                    + " registros"
                    "</div>",
                    unsafe_allow_html=True,
                )

            else:

                st.markdown(
                    '<div class="card-status">'
                    '⚪ <strong>Não carregada</strong>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                st.markdown(
                    '<div class="card-registros">&nbsp;</div>',
                    unsafe_allow_html=True,
                )

            versao = st.session_state.get(
                f"versao_upload_{chave}",
                0,
            )

            arquivos = st.file_uploader(
                "Upload",
                type=["xlsx", "xlsm"],
                accept_multiple_files=True,
                key=f"upload_{chave}_{versao}",
                label_visibility="collapsed",
                help=(
                    f"Carregar arquivo(s) da base {base['nome']}. "
                    "Formatos aceitos: XLSX e XLSM."
                ),
            )

            if arquivos:

                processar_upload_multiplo(
                    chave,
                    arquivos,
                )

            arquivos_carregados = st.session_state.get(
                f"arquivos_{chave}",
                [],
            )

            if arquivos_carregados:

                st.caption(
                    f"📎 {len(arquivos_carregados)} arquivo(s)"
                )

                if st.button(
                    "Limpar",
                    key=f"limpar_base_{chave}",
                    use_container_width=True,
                ):

                    st.session_state[f"df_{chave}"] = None
                    st.session_state[f"arquivos_{chave}"] = []

                    assinaturas = st.session_state.get(
                        "assinaturas_upload",
                        {},
                    )

                    assinaturas.pop(chave, None)

                    st.session_state["assinaturas_upload"] = assinaturas

                    if f"versao_upload_{chave}" not in st.session_state:
                        st.session_state[f"versao_upload_{chave}"] = 0

                    st.session_state[f"versao_upload_{chave}"] += 1

                    st.rerun()


# ============================================================
# FERRAMENTAS
# ============================================================

st.markdown("### 🛠️ Ferramentas Operacionais")

st.caption(
    "Selecione uma ferramenta para iniciar a operação."
)

colunas_ferramentas = st.columns(3)

MAPA_FERRAMENTAS = {
    "Filtragem": "filtragem",
    "Duplicidade": "duplicidade",
    "Serviços": "servicos",
    "Eventos": "eventos",
    "Lotes": "lotes",
    "Lista Rápida": "lista_rapida",
}


# ============================================================
# CONTROLE DE ACESSO
# ============================================================

perfil_atual = st.session_state.get("perfil", "").lower()

FERRAMENTAS_LIBERADAS_USUARIO = {
    "Filtragem",
    "Duplicidade",
    "Serviços",
    "Eventos",
    "Lista Rápida",
    "Lotes",
}


for indice, ferramenta in enumerate(FERRAMENTAS):

    coluna = colunas_ferramentas[indice % 3]

    with coluna:

        with st.container(border=True):

            st.markdown(
                f"#### {ferramenta['icone']} {ferramenta['nome']}"
            )

            st.caption(ferramenta["descricao"])

            chave_ferramenta = (
                ferramenta["nome"]
                .lower()
                .replace(" ", "_")
            )

            # ------------------------------------------------
            # ADMIN
            # ------------------------------------------------

            if perfil_atual == "admin":

                if st.button(
                    "Acessar",
                    key=f"btn_ferramenta_{chave_ferramenta}",
                    type="primary",
                    use_container_width=True,
                ):

                    st.session_state.ferramenta_atual = (
                        MAPA_FERRAMENTAS[ferramenta["nome"]]
                    )

                    st.rerun()

            # ------------------------------------------------
            # USUÁRIO
            # ------------------------------------------------

            elif (
                perfil_atual == "usuario"
                and ferramenta["nome"] in FERRAMENTAS_LIBERADAS_USUARIO
            ):

                if st.button(
                    "Acessar",
                    key=f"btn_ferramenta_{chave_ferramenta}",
                    type="primary",
                    use_container_width=True,
                ):

                    st.session_state.ferramenta_atual = (
                        MAPA_FERRAMENTAS[ferramenta["nome"]]
                    )

                    st.rerun()

            # ------------------------------------------------
            # BLOQUEADO
            # ------------------------------------------------

            else:

                st.button(
                    "🔒 Restrito",
                    key=f"btn_ferramenta_bloqueada_{chave_ferramenta}",
                    disabled=True,
                    use_container_width=True,
                )

                st.markdown(
                    "<p style='font-size:12px; color:gray;'>"
                    "🔒 Restrito a administradores"
                    "</p>",
                    unsafe_allow_html=True,
                )
