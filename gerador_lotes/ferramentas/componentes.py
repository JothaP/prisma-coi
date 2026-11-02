import streamlit as st

from ..estado import base_carregada, obter_base


def selecionar_modo_api_the(
    key="modo_api_the",
    titulo="Base de operação",
    limpar_resultado_callback=None,
):
    """
    Seletor compartilhado entre os módulos que trabalham com API/THE.

    As bases são obtidas exclusivamente do estado compartilhado
    do pacote gerador_lotes.

    Regras:
    - Não realiza upload.
    - Não recarrega arquivos.
    - Não altera df_api ou df_the.
    - Permite alternar entre API e THE durante a mesma sessão.
    - A troca de modo não apaga as bases carregadas.
    - Opcionalmente limpa apenas o resultado específico do módulo.

    Retorna:
        (modo, df)
    """

    modos_disponiveis = []

    if base_carregada("api"):
        modos_disponiveis.append("API")

    if base_carregada("the"):
        modos_disponiveis.append("THE")

    if not modos_disponiveis:
        st.warning(
            "Nenhuma base API ou THE está carregada no Hub "
            "do Gerador de Lotes."
        )
        return None, None

    chave_valor = f"{key}_valor"

    modo_atual = st.session_state.get(
        chave_valor,
        modos_disponiveis[0],
    )

    if modo_atual not in modos_disponiveis:
        modo_atual = modos_disponiveis[0]
        st.session_state[chave_valor] = modo_atual

    modo = st.radio(
        titulo,
        modos_disponiveis,
        index=modos_disponiveis.index(modo_atual),
        horizontal=True,
        key=key,
    )

    modo_anterior = st.session_state.get(chave_valor)

    if modo_anterior is None:
        st.session_state[chave_valor] = modo

    elif modo_anterior != modo:
        st.session_state[chave_valor] = modo

        if limpar_resultado_callback:
            limpar_resultado_callback()

    nome_base = "api" if modo == "API" else "the"

    df = obter_base(nome_base)

    return modo, df
