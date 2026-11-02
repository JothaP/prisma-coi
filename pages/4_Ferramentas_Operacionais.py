import streamlit as st

from auth import verificar_autenticacao


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Ferramentas Operacionais - COI",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("4")


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
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("### 🛠️ Ferramentas Operacionais")

    st.caption(
        f"Usuário: **{st.session_state.get('usuario_logado', '')}**"
    )

    st.caption(
        f"Perfil: **{st.session_state.get('perfil', '').upper()}**"
    )

    st.divider()

    if st.button(
        "🏠 Voltar ao Menu Principal",
        use_container_width=True,
        key="voltar_menu_principal",
    ):
        st.switch_page("app.py")


# ============================================================
# CABEÇALHO
# ============================================================

st.title("🛠️ Ferramentas Operacionais")

st.caption(
    "Selecione a ferramenta operacional desejada."
)

st.divider()


# ============================================================
# FERRAMENTAS
# ============================================================

col1, col2, col3 = st.columns(3)


# ============================================================
# 4.1 — GERADOR DE LOTES
# ============================================================

with col1:

    st.markdown("### 📦 Gerador de Lotes")

    st.write(
        "Geração de lotes para processos de cancelamento "
        "de Ordens de Serviço."
    )

    st.caption("Status: **Ativo**")

    st.write("")

    if st.button(
        "Acessar Gerador de Lotes",
        type="primary",
        use_container_width=True,
        key="btn_gerador_lotes",
    ):
        st.session_state["ferramenta_atual"] = None
        st.switch_page(
            "pages/4_1_Gerador_Lotes_Cancelamento.py"
        )


# ============================================================
# 4.2 — GERADOR DE PAINEL
# ============================================================

with col2:

    st.markdown("### 📊 Gerador de Painel")

    st.write(
        "Geração de painéis e análises operacionais "
        "a partir das bases disponibilizadas."
    )

    st.caption("Status: **Ativo**")

    st.write("")

    if st.button(
        "Acessar Gerador de Painel",
        type="primary",
        use_container_width=True,
        key="btn_gerador_painel",
    ):

        st.switch_page(
            "pages/4_2_Gerador_de_Painel.py"
        )


# ============================================================
# 4.3 — CARDS OPERACIONAIS
# ============================================================

with col3:

    st.markdown("### 🃏 Cards Operacionais")

    st.write(
        "Geração de cards e indicadores executivos "
        "para acompanhamento operacional."
    )

    st.caption("Status: **Ativo**")

    st.write("")

    if st.button(
        "Acessar Cards Operacionais",
        type="primary",
        use_container_width=True,
        key="btn_cards_operacionais",
    ):

        st.switch_page(
            "pages/4_3_Cards_Operacionais.py"
        )
