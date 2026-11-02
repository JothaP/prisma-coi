import re
import unicodedata
from datetime import datetime

import pandas as pd
import streamlit as st

from ..exportacao import dataframe_para_excel
from ..zonas import obter_zona
from .componentes import selecionar_modo_api_the


# ============================================================
# CONFIGURAÇÕES
# ============================================================

NOME_ARQUIVO_API = "Duplicidade API.xlsx"
NOME_ARQUIVO_THE = "Duplicidade THE.xlsx"

COL_MATRICULA_PADRAO = "Matrícula"
COL_PROTOCOLO_PADRAO = "Cód. Protocolo Origem"
COL_DATA_PADRAO = "INÍCIO DO SLA"
COL_CIDADE_PADRAO = "Cidade"


# ============================================================
# MODO ESCURO
# ============================================================

def aplicar_modo_visual():
    if "duplicidade_modo_escuro" not in st.session_state:
        st.session_state["duplicidade_modo_escuro"] = False

    if st.session_state["duplicidade_modo_escuro"]:
        st.markdown(
            """
            <style>

            /* =====================================================
               FUNDO PRINCIPAL
               ===================================================== */

            .stApp {
                background-color: #111827 !important;
            }

            [data-testid="stAppViewContainer"] {
                background-color: #111827 !important;
            }

            [data-testid="stMain"] {
                background-color: #111827 !important;
            }

            [data-testid="stHeader"] {
                background-color: #111827 !important;
            }


            /* =====================================================
               SIDEBAR
               ===================================================== */

            [data-testid="stSidebar"] {
                background-color: #1f2937 !important;
            }

            [data-testid="stSidebar"] > div {
                background-color: #1f2937 !important;
            }

            [data-testid="stSidebar"] * {
                color: #f9fafb !important;
            }


            /* =====================================================
               TEXTOS
               ===================================================== */

            .stMarkdown,
            .stText,
            label,
            p,
            h1,
            h2,
            h3,
            h4,
            h5,
            h6 {
                color: #f9fafb !important;
            }


            /* =====================================================
               MÉTRICAS
               ===================================================== */

            [data-testid="stMetricValue"],
            [data-testid="stMetricLabel"],
            [data-testid="stMetricDelta"] {
                color: #f9fafb !important;
            }


            /* =====================================================
               EXPANDERS
               ===================================================== */

            [data-testid="stExpander"] {
                background-color: #1f2937 !important;
                border-color: #374151 !important;
            }

            [data-testid="stExpander"] summary {
                background-color: #1f2937 !important;
                color: #f9fafb !important;
            }


            /* =====================================================
               DATAFRAME
               ===================================================== */

            [data-testid="stDataFrame"] {
                background-color: #1f2937 !important;
            }


            /* =====================================================
               BOTÕES DA SIDEBAR
               ===================================================== */

            [data-testid="stSidebar"] [data-testid="stButton"] {
                width: 100% !important;
            }

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button {
                background: #1f2937 !important;
                background-color: #1f2937 !important;
                color: #ffffff !important;
                border: 1px solid #4b5563 !important;
                box-shadow: none !important;
                opacity: 1 !important;
            }

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button:hover {
                background: #374151 !important;
                background-color: #374151 !important;
                color: #ffffff !important;
                border-color: #6b7280 !important;
            }

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button:focus,
            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button:active {
                background: #374151 !important;
                background-color: #374151 !important;
                color: #ffffff !important;
                border-color: #6b7280 !important;
                box-shadow: none !important;
            }

            /* Container interno do botão */

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button > div {
                background: transparent !important;
                color: #ffffff !important;
            }

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button > div > div {
                background: transparent !important;
                color: #ffffff !important;
            }

            /* Texto do botão */

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button p {
                color: #ffffff !important;
                background: transparent !important;
                opacity: 1 !important;
            }

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button span {
                color: #ffffff !important;
                background: transparent !important;
                opacity: 1 !important;
            }

            [data-testid="stSidebar"]
            [data-testid="stButton"]
            button div {
                color: #ffffff !important;
            }


            /* =====================================================
               DOWNLOAD
               ===================================================== */

            [data-testid="stDownloadButton"] button {
                background: #1f2937 !important;
                background-color: #1f2937 !important;
                color: #ffffff !important;
                border: 1px solid #4b5563 !important;
            }

            [data-testid="stDownloadButton"] button:hover {
                background: #374151 !important;
                background-color: #374151 !important;
                color: #ffffff !important;
                border-color: #6b7280 !important;
            }

            [data-testid="stDownloadButton"] button p,
            [data-testid="stDownloadButton"] button span {
                color: #ffffff !important;
                background: transparent !important;
            }


            /* =====================================================
               INPUTS
               ===================================================== */

            [data-testid="stSelectbox"] label,
            [data-testid="stMultiSelect"] label,
            [data-testid="stTextInput"] label,
            [data-testid="stNumberInput"] label,
            [data-testid="stDateInput"] label,
            [data-testid="stTimeInput"] label {
                color: #f9fafb !important;
            }


            /* =====================================================
               DIVISORES
               ===================================================== */

            hr {
                border-color: #374151 !important;
            }

            </style>
            """,
            unsafe_allow_html=True,
        )

def render_sidebar():
    with st.sidebar:
        st.markdown("## 🛠️ Duplicidade")
        st.caption("Navegação da ferramenta")

        st.divider()

        modo_escuro = st.toggle(
            "🌙 Modo escuro",
            value=st.session_state.get(
                "duplicidade_modo_escuro",
                False,
            ),
            key="duplicidade_modo_escuro",
        )

        if modo_escuro:
            st.caption("Modo escuro ativado")
        else:
            st.caption("Modo claro ativado")

        st.divider()

        if st.button(
            "⬅️ Voltar às Ferramentas",
            use_container_width=True,
            key="duplicidade_voltar_ferramentas",
        ):
            st.session_state.ferramenta_atual = None
            st.switch_page(
                "pages/4_Ferramentas_Operacionais.py"
            )

        if st.button(
            "🏠 Voltar ao Gerador de Lotes",
            use_container_width=True,
            key="duplicidade_voltar_gerador",
        ):
            st.session_state.ferramenta_atual = None
            st.switch_page(
                "pages/4_1_Gerador_Lotes_Cancelamento.py"
            )

    aplicar_modo_visual()

# ============================================================
# NORMALIZAÇÃO DE TEXTO
# ============================================================

def normalizar_texto(texto):
    if pd.isna(texto):
        return ""

    texto = str(texto)

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )

    texto = texto.upper().strip()

    texto = re.sub(
        r"\s+",
        " ",
        texto,
    )

    return texto


# ============================================================
# LOCALIZAÇÃO DAS COLUNAS
#
# IMPORTANTE:
# "DATA" NÃO É PROCURADA.
# A única coluna temporal aceita é INÍCIO DO SLA.
# ============================================================

def localizar_coluna(df, tipo):
    colunas = list(df.columns)

    normalizadas = {
        coluna: normalizar_texto(coluna)
        for coluna in colunas
    }

    if tipo == "matricula":
        for coluna, nome in normalizadas.items():
            if nome == "MATRICULA":
                return coluna

        for coluna, nome in normalizadas.items():
            if "MATRICULA" in nome:
                return coluna

    elif tipo == "protocolo":
        for coluna, nome in normalizadas.items():
            if nome == "COD. PROTOCOLO ORIGEM":
                return coluna

        for coluna, nome in normalizadas.items():
            if (
                "PROTOCOLO" in nome
                and "ORIGEM" in nome
            ):
                return coluna

    elif tipo == "cidade":
        for coluna, nome in normalizadas.items():
            if nome == "CIDADE":
                return coluna

    elif tipo == "data":
        # REGRA INQUEBRÁVEL:
        # somente INÍCIO DO SLA pode ser utilizado.
        for coluna, nome in normalizadas.items():
            if nome == "INICIO DO SLA":
                return coluna

        for coluna, nome in normalizadas.items():
            if (
                "INICIO DO SLA" in nome
                or "INICIO SLA" in nome
            ):
                return coluna

        return None

    return None


# ============================================================
# MATRÍCULA
# ============================================================

def normalizar_matricula(valor):
    """
    Retorna somente matrículas que representam exatamente
    a sequência numérica existente na base.

    API = 9 dígitos
    THE = 8 dígitos
    """

    if pd.isna(valor):
        return ""

    if isinstance(valor, bool):
        return ""

    if isinstance(valor, int):
        return str(valor)

    if isinstance(valor, float):
        if not valor.is_integer():
            return ""

        return str(int(valor))

    texto = str(valor).strip()

    if not texto:
        return ""

    if not texto.isdigit():
        return ""

    return texto


def matricula_valida(valor, modo):
    matricula = normalizar_matricula(valor)

    tamanho = 9 if modo == "API" else 8

    return (
        matricula != ""
        and len(matricula) == tamanho
    )


# ============================================================
# CONVERSÃO ROBUSTA DO INÍCIO DO SLA
#
# NUNCA utiliza a coluna "Data".
# ============================================================

def converter_datas_robusto(serie):
    resultado = pd.Series(
        pd.NaT,
        index=serie.index,
        dtype="datetime64[ns]",
    )

    # --------------------------------------------------------
    # DATETIME / TIMESTAMP
    # --------------------------------------------------------

    mascara_datetime = serie.map(
        lambda valor: isinstance(
            valor,
            (datetime, pd.Timestamp),
        )
    )

    if mascara_datetime.any():
        resultado.loc[mascara_datetime] = pd.to_datetime(
            serie.loc[mascara_datetime],
            errors="coerce",
        )

    # --------------------------------------------------------
    # NÚMEROS DO EXCEL
    # --------------------------------------------------------

    restantes = resultado.isna()

    valores_numericos = pd.to_numeric(
        serie.loc[restantes],
        errors="coerce",
    )

    mascara_excel = (
        valores_numericos.notna()
        & valores_numericos.between(
            1,
            100000,
        )
    )

    if mascara_excel.any():
        indices = valores_numericos.index[
            mascara_excel
        ]

        resultado.loc[indices] = pd.to_datetime(
            valores_numericos.loc[indices],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )

    # --------------------------------------------------------
    # DATAS BRASILEIRAS
    # --------------------------------------------------------

    restantes = resultado.isna()

    textos = (
        serie.loc[restantes]
        .astype(str)
        .str.strip()
    )

    mascara_br = textos.str.match(
        r"^\d{1,2}/\d{1,2}/\d{4}"
        r"(?:\s+\d{1,2}:\d{2}"
        r"(?::\d{2})?)?$",
        na=False,
    )

    if mascara_br.any():
        indices = textos.index[
            mascara_br
        ]

        resultado.loc[indices] = pd.to_datetime(
            textos.loc[indices],
            format="mixed",
            dayfirst=True,
            errors="coerce",
        )

    # --------------------------------------------------------
    # ISO
    # --------------------------------------------------------

    restantes = resultado.isna()

    textos = (
        serie.loc[restantes]
        .astype(str)
        .str.strip()
    )

    mascara_iso = textos.str.match(
        r"^\d{4}-\d{1,2}-\d{1,2}",
        na=False,
    )

    if mascara_iso.any():
        indices = textos.index[
            mascara_iso
        ]

        resultado.loc[indices] = pd.to_datetime(
            textos.loc[indices],
            format="mixed",
            errors="coerce",
        )

    # --------------------------------------------------------
    # ÚLTIMA TENTATIVA
    # --------------------------------------------------------

    restantes = resultado.isna()

    if restantes.any():
        textos = (
            serie.loc[restantes]
            .astype(str)
            .str.strip()
        )

        resultado.loc[restantes] = pd.to_datetime(
            textos,
            format="mixed",
            dayfirst=True,
            errors="coerce",
        )

    return resultado


# ============================================================
# PROTOCOLO
# ============================================================

def parse_protocolo(valor):
    """
    Espera protocolo no formato:
        numero/ano

    Retorna:
        numero_str
        ano_str
        numero_int
    """

    if pd.isna(valor):
        return "", "", None

    texto = str(valor).strip()

    match = re.search(
        r"(\d+)\s*/\s*(\d{4})",
        texto,
    )

    if not match:
        return "", "", None

    numero_str = match.group(1)
    ano_str = match.group(2)

    try:
        numero_int = int(numero_str)
    except (TypeError, ValueError):
        numero_int = None

    return (
        numero_str,
        ano_str,
        numero_int,
    )


# ============================================================
# IDENTIFICAÇÃO DAS DUPLICIDADES
# ============================================================

def identificar_duplicidades(df, modo):
    if df is None or df.empty:
        raise ValueError(
            "Não existem dados no backlog ativo."
        )

    # --------------------------------------------------------
    # COLUNAS OBRIGATÓRIAS
    # --------------------------------------------------------

    col_matricula = localizar_coluna(
        df,
        "matricula",
    )

    col_protocolo = localizar_coluna(
        df,
        "protocolo",
    )

    col_data = localizar_coluna(
        df,
        "data",
    )

    if col_matricula is None:
        raise ValueError(
            "Não foi encontrada a coluna obrigatória "
            "'Matrícula'."
        )

    if col_protocolo is None:
        raise ValueError(
            "Não foi encontrada a coluna obrigatória "
            "'Cód. Protocolo Origem'."
        )

    if col_data is None:
        raise ValueError(
            "Não foi encontrada a coluna obrigatória "
            "'INÍCIO DO SLA'. "
            "A análise de duplicidade utiliza "
            "exclusivamente 'INÍCIO DO SLA'."
        )

    col_cidade = None

    if modo == "API":
        col_cidade = localizar_coluna(
            df,
            "cidade",
        )

        if col_cidade is None:
            raise ValueError(
                "A base API não possui a coluna obrigatória "
                "'Cidade', necessária para determinar a zona."
            )

    # --------------------------------------------------------
    # PREPARAÇÃO
    # --------------------------------------------------------

    trabalho = df.copy()

    trabalho["_ORDEM_ORIGINAL"] = range(
        len(trabalho)
    )

    trabalho["_MATRICULA_NORMALIZADA"] = (
        trabalho[col_matricula]
        .map(normalizar_matricula)
    )

    trabalho["_MATRICULA_VALIDA"] = (
        trabalho[col_matricula]
        .map(
            lambda valor: matricula_valida(
                valor,
                modo,
            )
        )
    )

    # --------------------------------------------------------
    # MATRÍCULAS INVÁLIDAS / IGNORADAS
    # --------------------------------------------------------

    registros_ignorados = trabalho.loc[
        ~trabalho["_MATRICULA_VALIDA"]
    ].copy()

    trabalho_validos = trabalho.loc[
        trabalho["_MATRICULA_VALIDA"]
    ].copy()

    if trabalho_validos.empty:
        return {
            "df_duplicidades": pd.DataFrame(
                columns=df.columns
            ),
            "df_mantidos": pd.DataFrame(
                columns=df.columns
            ),
            "df_lote": pd.DataFrame(),
            "df_ignorados": registros_ignorados.drop(
                columns=[
                    "_ORDEM_ORIGINAL",
                    "_MATRICULA_NORMALIZADA",
                    "_MATRICULA_VALIDA",
                ],
                errors="ignore",
            ),
            "qtd_matriculas_duplicadas": 0,
            "qtd_duplicidades": 0,
            "qtd_mantidos": 0,
            "qtd_datas_invalidas": 0,
            "avisos_data": [],
        }

    # --------------------------------------------------------
    # DATA — EXCLUSIVAMENTE INÍCIO DO SLA
    # --------------------------------------------------------

    trabalho_validos["_DATA_SLA"] = (
        converter_datas_robusto(
            trabalho_validos[col_data]
        )
    )

    qtd_datas_invalidas = int(
        trabalho_validos["_DATA_SLA"]
        .isna()
        .sum()
    )

    # --------------------------------------------------------
    # PROTOCOLO NUMÉRICO
    # --------------------------------------------------------

    protocolo_info = (
        trabalho_validos[col_protocolo]
        .map(parse_protocolo)
    )

    trabalho_validos["_PROTOCOLO_NUMERO"] = (
        protocolo_info.map(
            lambda item: item[2]
        )
    )

    # --------------------------------------------------------
    # CONTAGEM POR MATRÍCULA
    # --------------------------------------------------------

    contagem = (
        trabalho_validos
        .groupby(
            "_MATRICULA_NORMALIZADA"
        )
        .size()
    )

    matriculas_duplicadas = contagem[
        contagem > 1
    ].index

    qtd_matriculas_duplicadas = len(
        matriculas_duplicadas
    )

    if qtd_matriculas_duplicadas == 0:
        return {
            "df_duplicidades": pd.DataFrame(
                columns=df.columns
            ),
            "df_mantidos": pd.DataFrame(
                columns=df.columns
            ),
            "df_lote": pd.DataFrame(),
            "df_ignorados": registros_ignorados.drop(
                columns=[
                    "_ORDEM_ORIGINAL",
                    "_MATRICULA_NORMALIZADA",
                    "_MATRICULA_VALIDA",
                ],
                errors="ignore",
            ),
            "qtd_matriculas_duplicadas": 0,
            "qtd_duplicidades": 0,
            "qtd_mantidos": 0,
            "qtd_datas_invalidas": qtd_datas_invalidas,
            "avisos_data": [],
        }

    # --------------------------------------------------------
    # SOMENTE GRUPOS DUPLICADOS
    # --------------------------------------------------------

    candidatos = trabalho_validos[
        trabalho_validos[
            "_MATRICULA_NORMALIZADA"
        ].isin(matriculas_duplicadas)
    ].copy()

    candidatos["_DATA_INVALIDA"] = (
        candidatos["_DATA_SLA"].isna()
    )

    candidatos["_PROTOCOLO_INVALIDO"] = (
        candidatos["_PROTOCOLO_NUMERO"].isna()
    )

    # --------------------------------------------------------
    # CRITÉRIO DE ESCOLHA DA ORIGINAL
    # --------------------------------------------------------

    candidatos = candidatos.sort_values(
        by=[
            "_MATRICULA_NORMALIZADA",
            "_DATA_INVALIDA",
            "_DATA_SLA",
            "_PROTOCOLO_INVALIDO",
            "_PROTOCOLO_NUMERO",
            "_ORDEM_ORIGINAL",
        ],
        ascending=[
            True,
            True,
            True,
            True,
            True,
            True,
        ],
        na_position="last",
        kind="stable",
    )

    # --------------------------------------------------------
    # ORIGINAL DE CADA MATRÍCULA DUPLICADA
    # --------------------------------------------------------

    indices_mantidos = (
        candidatos
        .groupby(
            "_MATRICULA_NORMALIZADA",
            sort=False,
        )
        .head(1)
        .index
    )

    # --------------------------------------------------------
    # TODAS AS DEMAIS O.S. = DUPLICIDADES
    # --------------------------------------------------------

    mascara_mantido = candidatos.index.isin(
        indices_mantidos
    )

    duplicidades = candidatos.loc[
        ~mascara_mantido
    ].copy()

    mantidos = candidatos.loc[
        mascara_mantido
    ].copy()

    # --------------------------------------------------------
    # AVISOS DE DATA
    # --------------------------------------------------------

    avisos_data = []

    for matricula in matriculas_duplicadas:
        grupo = candidatos.loc[
            candidatos[
                "_MATRICULA_NORMALIZADA"
            ] == matricula
        ].copy()

        if grupo["_DATA_SLA"].isna().all():
            original = grupo.iloc[0]

            avisos_data.append(
                {
                    "Matrícula": original[
                        col_matricula
                    ],
                    "Motivo": (
                        "Todas as O.S. da matrícula "
                        "possuem INÍCIO DO SLA inválido. "
                        "Foi mantida a O.S. com o menor "
                        "número de protocolo."
                    ),
                }
            )

        elif grupo["_DATA_SLA"].isna().any():
            for _, linha in grupo.loc[
                grupo["_DATA_SLA"].isna()
            ].iterrows():
                avisos_data.append(
                    {
                        "Matrícula": linha[
                            col_matricula
                        ],
                        "Motivo": (
                            "O.S. com INÍCIO DO SLA inválido. "
                            "Não foi escolhida como original, "
                            "pois datas válidas possuem prioridade."
                        ),
                    }
                )

    # --------------------------------------------------------
    # DATAFRAMES FINAIS
    # --------------------------------------------------------

    colunas_auxiliares = [
        "_ORDEM_ORIGINAL",
        "_MATRICULA_NORMALIZADA",
        "_MATRICULA_VALIDA",
        "_DATA_SLA",
        "_DATA_INVALIDA",
        "_PROTOCOLO_NUMERO",
        "_PROTOCOLO_INVALIDO",
    ]

    df_mantidos = (
        mantidos
        .sort_values(
            "_ORDEM_ORIGINAL"
        )
        .drop(
            columns=colunas_auxiliares,
            errors="ignore",
        )
        .reset_index(drop=True)
    )

    df_duplicidades = (
        duplicidades
        .sort_values(
            "_ORDEM_ORIGINAL"
        )
        .drop(
            columns=colunas_auxiliares,
            errors="ignore",
        )
        .reset_index(drop=True)
    )

    df_ignorados = (
        registros_ignorados
        .drop(
            columns=[
                "_ORDEM_ORIGINAL",
                "_MATRICULA_NORMALIZADA",
                "_MATRICULA_VALIDA",
            ],
            errors="ignore",
        )
        .reset_index(drop=True)
    )

    return {
        "df_duplicidades": df_duplicidades,
        "df_mantidos": df_mantidos,
        "df_ignorados": df_ignorados,
        "qtd_matriculas_duplicadas": (
            qtd_matriculas_duplicadas
        ),
        "qtd_duplicidades": len(
            df_duplicidades
        ),
        "qtd_mantidos": len(
            df_mantidos
        ),
        "qtd_datas_invalidas": (
            qtd_datas_invalidas
        ),
        "avisos_data": avisos_data,
    }


# ============================================================
# GERAÇÃO DO LOTE DE CANCELAMENTO
# ============================================================

def gerar_lote_cancelamento(
    df_duplicidades,
    df_mantidos,
    modo,
):
    if (
        df_duplicidades is None
        or df_duplicidades.empty
    ):
        return pd.DataFrame()

    col_matricula = localizar_coluna(
        df_duplicidades,
        "matricula",
    )

    col_protocolo = localizar_coluna(
        df_duplicidades,
        "protocolo",
    )

    col_cidade = None

    if modo == "API":
        col_cidade = localizar_coluna(
            df_duplicidades,
            "cidade",
        )

    if col_matricula is None:
        raise ValueError(
            "Não foi encontrada a coluna 'Matrícula' "
            "para gerar o lote."
        )

    if col_protocolo is None:
        raise ValueError(
            "Não foi encontrada a coluna "
            "'Cód. Protocolo Origem' para gerar o lote."
        )

    if modo == "API" and col_cidade is None:
        raise ValueError(
            "Não foi encontrada a coluna 'Cidade' "
            "na base API."
        )

    # --------------------------------------------------------
    # MAPA DA MATRÍCULA ORIGINAL → PROTOCOLO ORIGINAL
    # --------------------------------------------------------

    col_matricula_mantido = localizar_coluna(
        df_mantidos,
        "matricula",
    )

    col_protocolo_mantido = localizar_coluna(
        df_mantidos,
        "protocolo",
    )

    if (
        col_matricula_mantido is None
        or col_protocolo_mantido is None
    ):
        raise ValueError(
            "Não foi possível identificar Matrícula e "
            "Cód. Protocolo Origem nas O.S. mantidas."
        )

    mapa_original = {}

    for _, linha in df_mantidos.iterrows():
        matricula = normalizar_matricula(
            linha[col_matricula_mantido]
        )

        protocolo_original = linha[
            col_protocolo_mantido
        ]

        if matricula:
            mapa_original[matricula] = (
                protocolo_original
            )

    # --------------------------------------------------------
    # GERAÇÃO
    # --------------------------------------------------------

    registros = []

    for _, linha in df_duplicidades.iterrows():
        matricula = normalizar_matricula(
            linha[col_matricula]
        )

        protocolo = linha[
            col_protocolo
        ]

        numero_str, ano_str, numero_int = (
            parse_protocolo(protocolo)
        )

        protocolo_original = mapa_original.get(
            matricula
        )

        if protocolo_original is None:
            protocolo_original = ""

        numero_original = (
            str(protocolo_original)
            if not pd.isna(protocolo_original)
            else ""
        )

        observacao = (
            f"Duplicidade com O.S N. "
            f"{numero_original}"
        )

        # ----------------------------------------------------
        # ZONA
        # ----------------------------------------------------

        if modo == "THE":
            zona = 1
        else:
            cidade = linha[col_cidade]
            zona = obter_zona(cidade)

            if zona is None:
                zona = ""

        registros.append(
            {
                "Matricula": matricula,
                "Zona Ligacao": zona,
                "Numero Do Pedido": (
                    numero_int
                    if numero_int is not None
                    else ""
                ),
                "Ano Do Pedido": (
                    int(ano_str)
                    if ano_str.isdigit()
                    else ""
                ),
                "Tipo Encerramento": 6,
                "Observações": observacao,
            }
        )

    return pd.DataFrame(
        registros,
        columns=[
            "Matricula",
            "Zona Ligacao",
            "Numero Do Pedido",
            "Ano Do Pedido",
            "Tipo Encerramento",
            "Observações",
        ],
    )


# ============================================================
# RENDERIZAÇÃO
# ============================================================

def render_duplicidade():

    # --------------------------------------------------------
    # SIDEBAR
    # --------------------------------------------------------

    render_sidebar()

    # --------------------------------------------------------
    # TÍTULO
    # --------------------------------------------------------

    st.title("♻️ Análise de Duplicidade")

    st.caption(
        "Identificação de O.S. duplicadas por Matrícula, "
        "mantendo somente a O.S. original."
    )

    st.divider()

    # --------------------------------------------------------
    # SELEÇÃO DA BASE
    # --------------------------------------------------------

    modo, df = selecionar_modo_api_the(
        key="duplicidade_modo_api_the",
        titulo="Base de operação",
        limpar_resultado_callback=None,
    )

    if modo is None or df is None:
        st.stop()

    # --------------------------------------------------------
    # IDENTIFICAÇÃO DO ESTADO POR MODO
    # --------------------------------------------------------

    chave_resultado = (
        "duplicidade_resultado_api"
        if modo == "API"
        else "duplicidade_resultado_the"
    )

    chave_lote = (
        "duplicidade_lote_api"
        if modo == "API"
        else "duplicidade_lote_the"
    )

    chave_mantidos = (
        "duplicidade_mantidos_api"
        if modo == "API"
        else "duplicidade_mantidos_the"
    )

    chave_ignorados = (
        "duplicidade_ignorados_api"
        if modo == "API"
        else "duplicidade_ignorados_the"
    )

    chave_analisada = (
        "duplicidade_analisada_api"
        if modo == "API"
        else "duplicidade_analisada_the"
    )

    # --------------------------------------------------------
    # INFORMAÇÕES DA BASE
    # --------------------------------------------------------

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Base",
            modo,
        )

    with col2:
        st.metric(
            "Registros",
            f"{len(df):,}".replace(",", "."),
        )

    with col3:
        st.metric(
            "Colunas",
            f"{len(df.columns):,}".replace(",", "."),
        )

    st.divider()

    # --------------------------------------------------------
    # REGRAS
    # --------------------------------------------------------

    with st.expander(
        "ℹ️ Regras da análise",
        expanded=False,
    ):
        st.markdown(
            """
            **A análise utiliza o backlog ativo da base selecionada.**

            - API → `df_api`
            - THE → `df_the`
            - Matrícula API → exatamente 9 dígitos
            - Matrícula THE → exatamente 8 dígitos
            - Matrículas inválidas são ignoradas e contabilizadas
            - `INÍCIO DO SLA` é a única referência de data/hora
            - A coluna `Data` não é utilizada
            - Data válida possui prioridade sobre data inválida
            - Entre datas válidas, permanece a mais antiga
            - Empate de data → menor número do protocolo
            - Se todas as datas forem inválidas → menor número do protocolo
            - Todas as demais O.S. da matrícula serão canceladas
            - O.S. com matrícula válida e única não entra na análise
            """
        )

    st.divider()

    # --------------------------------------------------------
    # BOTÃO DE ANÁLISE
    # --------------------------------------------------------

    if st.button(
        "🔍 Analisar Duplicidades",
        type="primary",
        use_container_width=True,
        key=f"btn_analisar_duplicidades_{modo.lower()}",
    ):
        try:
            with st.spinner(
                "Analisando duplicidades..."
            ):
                resultado = identificar_duplicidades(
                    df,
                    modo,
                )

                df_duplicidades = resultado[
                    "df_duplicidades"
                ]

                df_mantidos = resultado[
                    "df_mantidos"
                ]

                df_ignorados = resultado[
                    "df_ignorados"
                ]

                df_lote = gerar_lote_cancelamento(
                    df_duplicidades,
                    df_mantidos,
                    modo,
                )

                st.session_state[
                    chave_resultado
                ] = resultado

                st.session_state[
                    chave_lote
                ] = df_lote

                st.session_state[
                    chave_mantidos
                ] = df_mantidos

                st.session_state[
                    chave_ignorados
                ] = df_ignorados

                st.session_state[
                    chave_analisada
                ] = True

            st.success(
                "Análise de duplicidades concluída."
            )

        except Exception as erro:
            st.error(
                f"Não foi possível realizar a análise: {erro}"
            )
            st.stop()

    # --------------------------------------------------------
    # RESULTADO EXISTENTE PARA ESTE MODO
    # --------------------------------------------------------

    if not st.session_state.get(
        chave_analisada,
        False,
    ):
        return

    resultado = st.session_state.get(
        chave_resultado
    )

    df_lote = st.session_state.get(
        chave_lote
    )

    df_ignorados = st.session_state.get(
        chave_ignorados
    )

    if resultado is None:
        return

    # --------------------------------------------------------
    # MÉTRICAS
    # --------------------------------------------------------

    qtd_total = len(df)

    qtd_ignorados = (
        len(df_ignorados)
        if df_ignorados is not None
        else 0
    )

    qtd_validos = (
        qtd_total
        - qtd_ignorados
    )

    qtd_matriculas = resultado[
        "qtd_matriculas_duplicadas"
    ]

    qtd_cancelar = resultado[
        "qtd_duplicidades"
    ]

    qtd_mantidos = resultado[
        "qtd_mantidos"
    ]

    qtd_datas_invalidas = resultado[
        "qtd_datas_invalidas"
    ]

    st.markdown(
        "### 📊 Resultado da análise"
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Registros analisados",
            f"{qtd_total:,}".replace(",", "."),
        )

    with col2:
        st.metric(
            "Matrículas válidas",
            f"{qtd_validos:,}".replace(",", "."),
        )

    with col3:
        st.metric(
            "Matrículas ignoradas",
            f"{qtd_ignorados:,}".replace(",", "."),
        )

    col4, col5, col6 = st.columns(3)

    with col4:
        st.metric(
            "Matrículas com duplicidade",
            f"{qtd_matriculas:,}".replace(",", "."),
        )

    with col5:
        st.metric(
            "O.S. a cancelar",
            f"{qtd_cancelar:,}".replace(",", "."),
        )

    with col6:
        st.metric(
            "O.S. mantidas",
            f"{qtd_mantidos:,}".replace(",", "."),
        )

    st.divider()

    # --------------------------------------------------------
    # INFORMAÇÕES DE DATAS
    # --------------------------------------------------------

    col_data1, col_data2 = st.columns(2)

    with col_data1:
        st.metric(
            "INÍCIO DO SLA inválido",
            f"{qtd_datas_invalidas:,}".replace(",", "."),
        )

    with col_data2:
        qtd_avisos = len(
            resultado["avisos_data"]
        )

        st.metric(
            "Ocorrências com aviso",
            f"{qtd_avisos:,}".replace(",", "."),
        )

    # --------------------------------------------------------
    # AVISOS
    # --------------------------------------------------------

    avisos_data = resultado[
        "avisos_data"
    ]

    if avisos_data:
        st.warning(
            f"Foram identificadas {len(avisos_data):,} "
            "ocorrências que exigem atenção quanto ao "
            "INÍCIO DO SLA.".replace(",", ".")
        )

        with st.expander(
            "⚠️ Ver avisos de data",
            expanded=False,
        ):
            st.dataframe(
                pd.DataFrame(
                    avisos_data
                ),
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # MATRÍCULAS IGNORADAS
    # --------------------------------------------------------

    if (
        df_ignorados is not None
        and not df_ignorados.empty
    ):
        with st.expander(
            "⚠️ Matrículas ignoradas — para tratamento posterior",
            expanded=False,
        ):
            st.caption(
                "Estas O.S. foram contabilizadas nos registros "
                "analisados, mas não participaram da identificação "
                "de duplicidades porque a Matrícula não possui "
                "o formato válido para o modo selecionado."
            )

            st.dataframe(
                df_ignorados,
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # PREVIEW EXCLUSIVO DO LOTE DE CANCELAMENTO
    # --------------------------------------------------------

    st.markdown(
        "### 📦 Preview do lote de cancelamento"
    )

    if (
        df_lote is None
        or df_lote.empty
    ):
        st.info(
            "Nenhuma O.S. foi identificada como duplicidade. "
            "Não há lote de cancelamento para gerar."
        )
    else:
        st.caption(
            "Exibindo as primeiras 10 O.S. que serão "
            "incluídas no lote de cancelamento."
        )

        df_preview = (
            df_lote
            .head(10)
            .copy()
        )

        st.dataframe(
            df_preview,
            use_container_width=True,
            hide_index=True,
        )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    if (
        df_lote is not None
        and not df_lote.empty
    ):
        st.divider()

        nome_arquivo = (
            NOME_ARQUIVO_API
            if modo == "API"
            else NOME_ARQUIVO_THE
        )

        arquivo_excel = dataframe_para_excel(
            df_lote,
            nome_aba="Lote Cancelamento",
        )

        if arquivo_excel is not None:
            st.download_button(
                label="📥 Baixar Lote de Cancelamento",
                data=arquivo_excel,
                file_name=nome_arquivo,
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
                type="primary",
                use_container_width=True,
                key=f"download_duplicidade_{modo.lower()}",
            )

    # --------------------------------------------------------
    # OBSERVAÇÃO SOBRE A TROCA API/THE
    # --------------------------------------------------------

    st.caption(
        "A base original permanece preservada durante a sessão. "
        "Você pode alternar entre API e THE e gerar outro lote "
        "sem realizar novo upload."
    )
