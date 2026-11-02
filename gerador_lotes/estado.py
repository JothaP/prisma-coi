import streamlit as st


BASES = [
    "api",
    "the",
    "eventos",
    "servicos_api",
    "servicos_the",
    "lotes",
]


def inicializar_estado():
    for base in BASES:
        chave_df = f"df_{base}"
        chave_arquivos = f"arquivos_{base}"
        chave_detalhes = f"dados_arquivos_{base}"

        if chave_df not in st.session_state:
            st.session_state[chave_df] = None

        if chave_arquivos not in st.session_state:
            st.session_state[chave_arquivos] = []

        if chave_detalhes not in st.session_state:
            st.session_state[chave_detalhes] = []

    if "assinaturas_upload" not in st.session_state:
        st.session_state["assinaturas_upload"] = {}

    if "ferramenta_atual" not in st.session_state:
        st.session_state["ferramenta_atual"] = None


def obter_base(nome_base):
    inicializar_estado()

    nome_base = str(nome_base).strip().lower()

    if nome_base not in BASES:
        raise ValueError(
            f"Base inválida: {nome_base}"
        )

    return st.session_state.get(
        f"df_{nome_base}"
    )


def base_carregada(nome_base):
    df = obter_base(nome_base)

    return (
        df is not None
        and not df.empty
    )


def definir_base(
    nome_base,
    df,
    arquivos=None,
    dados_arquivos=None,
):
    inicializar_estado()

    nome_base = str(nome_base).strip().lower()

    if nome_base not in BASES:
        raise ValueError(
            f"Base inválida: {nome_base}"
        )

    st.session_state[
        f"df_{nome_base}"
    ] = df

    if arquivos is not None:
        st.session_state[
            f"arquivos_{nome_base}"
        ] = arquivos

    if dados_arquivos is not None:
        st.session_state[
            f"dados_arquivos_{nome_base}"
        ] = dados_arquivos


def obter_arquivos_base(nome_base):
    inicializar_estado()

    nome_base = str(nome_base).strip().lower()

    if nome_base not in BASES:
        raise ValueError(
            f"Base inválida: {nome_base}"
        )

    return st.session_state.get(
        f"dados_arquivos_{nome_base}",
        [],
    )


def limpar_resultado():
    chaves_resultado = [
        "df_resultado",
        "df_log",
        "nome_arquivo_resultado",
        "resultado",
        "log_resultado",
    ]

    for chave in chaves_resultado:
        if chave in st.session_state:
            st.session_state[chave] = None

    chaves_lotes = [
        "lote_gerado",
        "lote_resultado",
        "lote_log",
        "lote_nome_arquivo",
    ]

    for chave in chaves_lotes:
        if chave in st.session_state:
            st.session_state[chave] = None


def limpar_bases():
    inicializar_estado()

    for base in BASES:
        st.session_state[
            f"df_{base}"
        ] = None

        st.session_state[
            f"arquivos_{base}"
        ] = []

        st.session_state[
            f"dados_arquivos_{base}"
        ] = []

    st.session_state[
        "assinaturas_upload"
    ] = {}


def voltar_ao_hub():
    inicializar_estado()

    st.session_state[
        "ferramenta_atual"
    ] = None
