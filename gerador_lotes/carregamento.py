import hashlib
import io

import pandas as pd
import streamlit as st

from .estado import definir_base


EXTENSOES_EXCEL_VALIDAS = (
    ".xlsx",
    ".xlsm",
)


def assinatura_arquivo(arquivo):
    if arquivo is None:
        return None

    conteudo = arquivo.getvalue()

    return (
        arquivo.name,
        len(conteudo),
        hashlib.md5(
            conteudo
        ).hexdigest(),
    )


def assinatura_arquivos(arquivos):
    if not arquivos:
        return None

    assinaturas = []

    for arquivo in arquivos:
        conteudo = arquivo.getvalue()

        assinaturas.append(
            (
                arquivo.name,
                len(conteudo),
                hashlib.md5(
                    conteudo
                ).hexdigest(),
            )
        )

    return tuple(
        sorted(assinaturas)
    )


def ler_excel(arquivo):
    if arquivo is None:
        return None

    nome = arquivo.name.lower()

    if not nome.endswith(
        EXTENSOES_EXCEL_VALIDAS
    ):
        raise ValueError(
            f"O arquivo '{arquivo.name}' não é um Excel válido. "
            "Utilize .xlsx ou .xlsm."
        )

    conteudo = arquivo.getvalue()

    df = pd.read_excel(
        io.BytesIO(conteudo),
        engine="openpyxl",
    )

    df.columns = [
        str(col).strip()
        for col in df.columns
    ]

    return df


def remover_linhas_vazias(df):
    if df is None or df.empty:
        return df

    df = df.copy()

    mascara_vazia = df.apply(
        lambda linha: all(
            pd.isna(valor)
            or str(valor).strip() == ""
            for valor in linha
        ),
        axis=1,
    )

    return df.loc[
        ~mascara_vazia
    ].reset_index(drop=True)


def consolidar_arquivos(arquivos):
    if not arquivos:
        return None

    dfs = []

    for arquivo in arquivos:
        df = ler_excel(arquivo)

        if df is not None and not df.empty:
            dfs.append(df)

    if not dfs:
        return None

    df_final = pd.concat(
        dfs,
        ignore_index=True,
        sort=False,
    )

    df_final = remover_linhas_vazias(
        df_final
    )

    if (
        df_final is None
        or df_final.empty
    ):
        return None

    df_final = (
        df_final
        .drop_duplicates(
            keep="first"
        )
        .reset_index(drop=True)
    )

    return df_final


def _obter_assinatura_anterior(
    nome_base
):
    assinaturas = st.session_state.get(
        "assinaturas_upload",
        {},
    )

    return assinaturas.get(
        nome_base
    )


def _registrar_assinatura(
    nome_base,
    assinatura,
):
    if (
        "assinaturas_upload"
        not in st.session_state
    ):
        st.session_state[
            "assinaturas_upload"
        ] = {}

    st.session_state[
        "assinaturas_upload"
    ][nome_base] = assinatura


def processar_upload_multiplo(
    nome_base,
    arquivos,
):
    if not arquivos:
        return False

    assinatura = assinatura_arquivos(
        arquivos
    )

    assinatura_anterior = (
        _obter_assinatura_anterior(
            nome_base
        )
    )

    if (
        assinatura
        == assinatura_anterior
    ):
        return False

    dados_arquivos = []

    for arquivo in arquivos:
        df_arquivo = ler_excel(
            arquivo
        )

        if (
            df_arquivo is not None
            and not df_arquivo.empty
        ):
            dados_arquivos.append(
                {
                    "nome": arquivo.name,
                    "df": (
                        remover_linhas_vazias(
                            df_arquivo
                        )
                    ),
                }
            )

    df = consolidar_arquivos(
        arquivos
    )

    if df is None or df.empty:
        st.warning(
            f"Nenhum dado válido encontrado "
            f"para a base '{nome_base}'."
        )

        return False

    definir_base(
        nome_base,
        df,
        arquivos=[
            arquivo.name
            for arquivo in arquivos
        ],
        dados_arquivos=dados_arquivos,
    )

    _registrar_assinatura(
        nome_base,
        assinatura,
    )

    return True


def processar_upload_unico(
    nome_base,
    arquivo,
):
    if arquivo is None:
        return False

    assinatura = assinatura_arquivo(
        arquivo
    )

    assinatura_anterior = (
        _obter_assinatura_anterior(
            nome_base
        )
    )

    if (
        assinatura
        == assinatura_anterior
    ):
        return False

    df = ler_excel(
        arquivo
    )

    if df is None or df.empty:
        st.warning(
            f"O arquivo '{arquivo.name}' "
            "não contém dados."
        )

        return False

    df = remover_linhas_vazias(
        df
    )

    if df is None or df.empty:
        st.warning(
            f"O arquivo '{arquivo.name}' "
            "não contém dados válidos."
        )

        return False

    definir_base(
        nome_base,
        df,
        arquivos=[
            arquivo.name
        ],
        dados_arquivos=[
            {
                "nome": arquivo.name,
                "df": df,
            }
        ],
    )

    _registrar_assinatura(
        nome_base,
        assinatura,
    )

    return True
