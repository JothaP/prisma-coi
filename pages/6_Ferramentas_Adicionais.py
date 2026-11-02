import streamlit as st

from auth import verificar_autenticacao, tem_acesso_modulo

from pages.ferramentas_adicionais.excel_tools import (
    render_juntar_excel,
    render_visualizar_excel,
    render_comparar_bases,
    render_remover_duplicidades,
    render_separar_excel,
    render_exportar_excel,
)

from pages.ferramentas_adicionais.word_tools import (
    render_formatador_relatorios,
)

from pages.ferramentas_adicionais.calculadoras import (
    render_vazao,
    render_pressao,
    render_consumo,
    render_volume,
    render_tempo,
    render_sla,
    render_conversor_unidades,
)


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Ferramentas Adicionais - COI",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("6")


# ============================================================
# SIDEBAR
# ============================================================

if "modo_escuro_ferramentas" not in st.session_state:
    st.session_state["modo_escuro_ferramentas"] = False

with st.sidebar:
    st.markdown("## ⚙️ Opções")

    modo_escuro = st.toggle(
        "🌙 Modo escuro",
        value=st.session_state["modo_escuro_ferramentas"],
        key="toggle_modo_escuro_ferramentas",
    )

    if modo_escuro != st.session_state["modo_escuro_ferramentas"]:
        st.session_state["modo_escuro_ferramentas"] = modo_escuro
        st.rerun()

    st.divider()

    if st.button(
        "🏢 Voltar ao Hub Central",
        key="voltar_hub_sidebar_ferramentas",
        use_container_width=True,
    ):
        st.switch_page("app.py")


# ============================================================
# AUTENTICAÇÃO
# ============================================================

verificar_autenticacao()

if not st.session_state.get("autenticado"):
    st.warning("Sessão não iniciada ou expirada.")

    if st.button(
        "🔐 Ir para o login",
        key="ir_login_ferramentas",
        use_container_width=True,
    ):
        st.switch_page("app.py")

    st.stop()

# Defesa também para acesso direto via URL, seguindo as permissões do Hub.
if not tem_acesso_modulo("6"):
    st.error("Seu perfil não está autorizado a acessar Ferramentas Adicionais.")
    st.stop()


# ============================================================
# MODO ESCURO
# ============================================================

if st.session_state.get("modo_escuro_ferramentas"):
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background-color: #0e1117;
        }

        [data-testid="stHeader"] {
            background-color: #0e1117;
        }

        [data-testid="stSidebar"] {
            background-color: #161b22;
        }

        [data-testid="stSidebar"] * {
            color: #f0f2f6;
        }

        .secao-ferramentas p {
            color: #b8c0cc !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# ESTILO GERAL
# ============================================================

st.markdown(
    """
    <style>
    .titulo-ferramentas {
        background: linear-gradient(135deg, #17365D, #1F4E78);
        color: white;
        padding: 22px 26px;
        border-radius: 14px;
        margin-bottom: 24px;
    }

    .titulo-ferramentas h1 {
        margin: 0;
        font-size: 28px;
        font-weight: 700;
    }

    .titulo-ferramentas p {
        margin: 7px 0 0 0;
        font-size: 14px;
        opacity: 0.92;
    }

    .secao-ferramentas {
        margin-top: 10px;
        margin-bottom: 12px;
    }

    .secao-ferramentas h2 {
        font-size: 21px;
        margin-bottom: 4px;
    }

    .secao-ferramentas p {
        margin-top: 0;
        margin-bottom: 14px;
    }

    div.stButton > button {
        min-height: 64px;
        border-radius: 10px;
        font-weight: 600;
        font-size: 15px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# CABEÇALHO
# ============================================================

st.markdown(
    """
    <div class="titulo-ferramentas">
        <h1>🔧 Ferramentas Adicionais</h1>
        <p>Ferramentas rápidas para Excel, Word e cálculos operacionais.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# POPUP — JUNTAR EXCEL
# ============================================================

@st.dialog("🔗 Juntar Excel", width="large")
def abrir_juntar_excel():
    render_juntar_excel()


# ============================================================
# POPUPS — EXCEL TOOLS
# ============================================================

@st.dialog("👁️ Visualizar Excel", width="large")
def abrir_visualizar_excel():
    render_visualizar_excel()


@st.dialog("🔍 Comparar Bases", width="large")
def abrir_comparar_bases():
    render_comparar_bases()


@st.dialog("🧹 Remover Duplicidades", width="large")
def abrir_remover_duplicidades():
    render_remover_duplicidades()


@st.dialog("✂️ Separar Excel", width="large")
def abrir_separar_excel():
    render_separar_excel()


@st.dialog("📤 Exportar / Converter Excel", width="large")
def abrir_exportar_excel():
    render_exportar_excel()


# ============================================================
# POPUP — WORD TOOLS
# ============================================================

@st.dialog("📄 Formatador de Relatórios", width="large")
def abrir_formatador_relatorios():
    render_formatador_relatorios()


# ============================================================
# POPUPS — CALCULADORAS
# ============================================================

@st.dialog("💧 Vazão", width="large")
def abrir_vazao():
    render_vazao()


@st.dialog("📈 Pressão", width="large")
def abrir_pressao():
    render_pressao()


@st.dialog("🚰 Consumo", width="large")
def abrir_consumo():
    render_consumo()


@st.dialog("📦 Volume", width="large")
def abrir_volume():
    render_volume()


@st.dialog("⏱️ Tempo", width="large")
def abrir_tempo():
    render_tempo()


@st.dialog("📅 SLA", width="large")
def abrir_sla():
    render_sla()


@st.dialog("🔄 Conversor de Unidades", width="large")
def abrir_unidades():
    render_conversor_unidades()


# ============================================================
# EXCEL TOOLS
# ============================================================

st.markdown(
    """
    <div class="secao-ferramentas">
        <h2>📊 Excel Tools</h2>
        <p>
            Ferramentas rápidas para manipulação e tratamento de arquivos Excel.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


excel_tools = [
    ("🔗 Juntar Excel", abrir_juntar_excel),
    ("👁️ Visualizar Excel", abrir_visualizar_excel),
    ("🔍 Comparar Bases", abrir_comparar_bases),
    ("🧹 Remover Duplicidades", abrir_remover_duplicidades),
    ("✂️ Separar Excel", abrir_separar_excel),
    ("📤 Exportar/Converter Excel", abrir_exportar_excel),
]


colunas_excel = st.columns(4)

for indice, (nome, funcao) in enumerate(excel_tools):
    with colunas_excel[indice % 4]:
        if st.button(
            nome,
            key=f"ferramenta_excel_{indice}",
            use_container_width=True,
        ):
            funcao()


st.divider()


# ============================================================
# 6.2 — WORD TOOLS
# ============================================================

st.markdown(
    """
    <div class="secao-ferramentas">
        <h2>📄 Word Tools</h2>
        <p>
            Ferramentas para formatação e padronização de documentos Word.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

colunas_word = st.columns(4)
with colunas_word[0]:
    if st.button(
        "📄 Formatador de Relatórios",
        key="wordtools_botao_formatador_relatorios",
        use_container_width=True,
    ):
        abrir_formatador_relatorios()


st.divider()


# ============================================================
# 6.3 — CALCULADORAS
# ============================================================

st.markdown(
    """
    <div class="secao-ferramentas">
        <h2>🧮 Calculadoras</h2>
        <p>
            Calculadoras rápidas para operações e conversões.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


calculadoras = [
    ("💧 Vazão", abrir_vazao),
    ("📈 Pressão", abrir_pressao),
    ("🚰 Consumo", abrir_consumo),
    ("📦 Volume", abrir_volume),
    ("⏱️ Tempo", abrir_tempo),
    ("📅 SLA", abrir_sla),
    ("🔄 Conversor de Unidades", abrir_unidades),
]


colunas_calculadoras = st.columns(4)

for indice, (nome, funcao) in enumerate(calculadoras):
    with colunas_calculadoras[indice % 4]:
        if st.button(
            nome,
            key=f"calculadora_{indice}",
            use_container_width=True,
        ):
            funcao()


st.divider()


# ============================================================
# VOLTAR AO HUB CENTRAL
# ============================================================

if st.button(
    "🏢 Voltar ao Hub Central",
    key="voltar_hub_ferramentas",
    use_container_width=True,
):
    st.switch_page("app.py")
