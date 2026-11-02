import re
import unicodedata

import pandas as pd
import streamlit as st

from ..exportacao import dataframe_para_excel
from ..estado import obter_base
from ..zonas import obter_zona
from .componentes import selecionar_modo_api_the


NOME_ARQUIVO_API = "Serviços API.xlsx"
NOME_ARQUIVO_THE = "Serviços THE.xlsx"

COL_MATRICULA = "MATRICULA"
COL_PROTOCOLO = "COD. PROTOCOLO ORIGEM"
COL_CIDADE = "CIDADE"

COL_SAIDA_MATRICULA = "Matricula"
COL_SAIDA_ZONA = "Zona Ligacao"
COL_SAIDA_NUMERO = "Numero Do Pedido"
COL_SAIDA_ANO = "Ano Do Pedido"
COL_SAIDA_TIPO = "Tipo Encerramento"
COL_SAIDA_OBSERVACOES = "Observações"


# ============================================================
# VISUAL
# ============================================================

def aplicar_modo_visual():
    modo_escuro = st.session_state.get(
        "servicos_modo_escuro",
        False,
    )

    if modo_escuro:
        st.markdown(
            """
            <style>
                /* ==================================================
                   BASE
                   ================================================== */

                .stApp {
                    background-color: #0e1117;
                    color: #f0f2f6;
                }

                .main .block-container {
                    padding-top: 2rem;
                    padding-bottom: 3rem;
                }

                /* ==================================================
                   SIDEBAR
                   ================================================== */

                [data-testid="stSidebar"] {
                    background-color: #161b22;
                    border-right: 1px solid #30363d;
                }

                [data-testid="stSidebar"] * {
                    color: #f0f2f6;
                }

                [data-testid="stSidebar"] .stButton > button {
                    background-color: #21262d;
                    color: #f0f2f6;
                    border: 1px solid #30363d;
                }

                [data-testid="stSidebar"] .stButton > button:hover {
                    background-color: #30363d;
                    border-color: #58a6ff;
                    color: #ffffff;
                }

                /* ==================================================
                   TEXTOS
                   ================================================== */

                h1, h2, h3, h4, h5, h6 {
                    color: #f0f2f6 !important;
                }

                p, label, span, div {
                    color: inherit;
                }

                [data-testid="stCaptionContainer"] {
                    color: #8b949e !important;
                }

                /* ==================================================
                   DIVISORES
                   ================================================== */

                hr {
                    border-color: #30363d !important;
                }

                /* ==================================================
                   RADIO / TOGGLE
                   ================================================== */

                [data-testid="stRadio"] label {
                    color: #f0f2f6 !important;
                }

                /* ==================================================
                   CARDS
                   ================================================== */

                .coi-card {
                    background-color: #161b22;
                    border: 1px solid #30363d;
                    border-radius: 10px;
                    padding: 1rem 1.1rem;
                    margin-bottom: 0.8rem;
                }

                .coi-card-title {
                    font-size: 0.95rem;
                    font-weight: 700;
                    color: #f0f2f6;
                    margin-bottom: 0.35rem;
                }

                .coi-card-text {
                    font-size: 0.86rem;
                    color: #8b949e;
                }

                /* ==================================================
                   MÉTRICAS
                   ================================================== */

                [data-testid="stMetric"] {
                    background-color: #161b22;
                    border: 1px solid #30363d;
                    border-radius: 10px;
                    padding: 0.85rem 1rem;
                }

                [data-testid="stMetricLabel"] {
                    color: #8b949e !important;
                }

                [data-testid="stMetricValue"] {
                    color: #f0f2f6 !important;
                }

                /* ==================================================
                   BOTÕES
                   ================================================== */

                .stButton > button {
                    border-radius: 8px;
                    min-height: 42px;
                    font-weight: 600;
                }

                .stDownloadButton > button {
                    border-radius: 8px;
                    min-height: 42px;
                    font-weight: 600;
                }

                /* ==================================================
                   TABELA
                   ================================================== */

                [data-testid="stDataFrame"] {
                    border: 1px solid #30363d;
                    border-radius: 8px;
                    overflow: hidden;
                }

                /* ==================================================
                   ALERTAS
                   ================================================== */

                [data-testid="stAlert"] {
                    border-radius: 8px;
                }

                /* ==================================================
                   EXPANDER
                   ================================================== */

                [data-testid="stExpander"] {
                    border: 1px solid #30363d;
                    border-radius: 8px;
                    overflow: hidden;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )

    else:
        st.markdown(
            """
            <style>
                /* ==================================================
                   BASE
                   ================================================== */

                .stApp {
                    background-color: #f5f7fa;
                    color: #1f2937;
                }

                .main .block-container {
                    padding-top: 2rem;
                    padding-bottom: 3rem;
                }

                /* ==================================================
                   SIDEBAR
                   ================================================== */

                [data-testid="stSidebar"] {
                    background-color: #ffffff;
                    border-right: 1px solid #d9dee7;
                }

                [data-testid="stSidebar"] * {
                    color: #1f2937;
                }

                [data-testid="stSidebar"] .stButton > button {
                    background-color: #ffffff;
                    color: #1f2937;
                    border: 1px solid #d9dee7;
                }

                [data-testid="stSidebar"] .stButton > button:hover {
                    background-color: #f0f4f8;
                    border-color: #2563eb;
                    color: #1d4ed8;
                }

                /* ==================================================
                   TEXTOS
                   ================================================== */

                h1, h2, h3, h4, h5, h6 {
                    color: #111827 !important;
                }

                p, label {
                    color: #374151;
                }

                [data-testid="stCaptionContainer"] {
                    color: #6b7280 !important;
                }

                /* ==================================================
                   DIVISORES
                   ================================================== */

                hr {
                    border-color: #d9dee7 !important;
                }

                /* ==================================================
                   RADIO / TOGGLE
                   ================================================== */

                [data-testid="stRadio"] label {
                    color: #374151 !important;
                }

                /* ==================================================
                   CARDS
                   ================================================== */

                .coi-card {
                    background-color: #ffffff;
                    border: 1px solid #d9dee7;
                    border-radius: 10px;
                    padding: 1rem 1.1rem;
                    margin-bottom: 0.8rem;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
                }

                .coi-card-title {
                    font-size: 0.95rem;
                    font-weight: 700;
                    color: #111827;
                    margin-bottom: 0.35rem;
                }

                .coi-card-text {
                    font-size: 0.86rem;
                    color: #6b7280;
                }

                /* ==================================================
                   MÉTRICAS
                   ================================================== */

                [data-testid="stMetric"] {
                    background-color: #ffffff;
                    border: 1px solid #d9dee7;
                    border-radius: 10px;
                    padding: 0.85rem 1rem;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
                }

                [data-testid="stMetricLabel"] {
                    color: #6b7280 !important;
                }

                [data-testid="stMetricValue"] {
                    color: #111827 !important;
                }

                /* ==================================================
                   BOTÕES
                   ================================================== */

                .stButton > button {
                    border-radius: 8px;
                    min-height: 42px;
                    font-weight: 600;
                }

                .stDownloadButton > button {
                    border-radius: 8px;
                    min-height: 42px;
                    font-weight: 600;
                }

                /* ==================================================
                   TABELA
                   ================================================== */

                [data-testid="stDataFrame"] {
                    border: 1px solid #d9dee7;
                    border-radius: 8px;
                    overflow: hidden;
                    background-color: #ffffff;
                }

                /* ==================================================
                   ALERTAS
                   ================================================== */

                [data-testid="stAlert"] {
                    border-radius: 8px;
                }

                /* ==================================================
                   EXPANDER
                   ================================================== */

                [data-testid="stExpander"] {
                    border: 1px solid #d9dee7;
                    border-radius: 8px;
                    overflow: hidden;
                    background-color: #ffffff;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )


def render_sidebar():
    with st.sidebar:
        st.markdown("## 🛠️ Serviços")
        st.caption("Navegação da ferramenta")

        st.divider()

        modo_escuro = st.toggle(
            "🌙 Modo escuro",
            value=st.session_state.get(
                "servicos_modo_escuro",
                False,
            ),
            key="servicos_modo_escuro",
        )

        if modo_escuro:
            st.caption("Modo escuro ativado")
        else:
            st.caption("Modo claro ativado")

        st.divider()

        if st.button(
            "⬅️ Voltar às Ferramentas",
            use_container_width=True,
            key="servicos_voltar_ferramentas",
        ):
            st.session_state.ferramenta_atual = None
            st.switch_page(
                "pages/4_Ferramentas_Operacionais.py"
            )

        if st.button(
            "🏠 Voltar ao Gerador de Lotes",
            use_container_width=True,
            key="servicos_voltar_gerador",
        ):
            st.session_state.ferramenta_atual = None
            st.switch_page(
                "pages/4_1_Gerador_Lotes_Cancelamento.py"
            )

    aplicar_modo_visual()


# ============================================================
# NORMALIZAÇÕES
# ============================================================

def normalizar_texto(valor):
    if pd.isna(valor):
        return ""

    texto = str(valor).strip().upper()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )

    return texto


def normalizar_matricula(valor):
    if pd.isna(valor):
        return ""

    texto = str(valor).strip()

    if not texto:
        return ""

    if re.fullmatch(r"\d+\.0", texto):
        texto = texto[:-2]

    return re.sub(r"\D", "", texto)


def matricula_valida(matricula, modo):
    if not matricula:
        return False

    quantidade_digitos = (
        9
        if modo == "API"
        else 8
    )

    return (
        matricula.isdigit()
        and len(matricula) == quantidade_digitos
    )


# ============================================================
# LOCALIZAÇÃO DE COLUNAS
# ============================================================

def localizar_coluna(df, nome_desejado):
    if nome_desejado in df.columns:
        return nome_desejado

    alvo = normalizar_texto(nome_desejado)

    for coluna in df.columns:
        if normalizar_texto(coluna) == alvo:
            return coluna

    return None


def localizar_coluna_descricao(df):
    possibilidades = [
        "CÓDIGO/DESCRIÇÃO",
        "CODIGO/DESCRICAO",
        "CÓDIGO / DESCRIÇÃO",
        "CODIGO / DESCRICAO",
        "CÓDIGO/ DESCRIÇÃO",
        "CODIGO/ DESCRICAO",
        "CÓDIGO /DESCRIÇÃO",
        "CODIGO /DESCRICAO",
        "DESCRIÇÃO",
        "DESCRICAO",
        "DESCRIÇÃO DO SERVIÇO",
        "DESCRICAO DO SERVICO",
        "SERVIÇO",
        "SERVICO",
        "TIPO DE SERVIÇO",
        "TIPO DE SERVICO",
        "NOME DO SERVIÇO",
        "NOME DO SERVICO",
    ]

    colunas_normalizadas = {
        normalizar_texto(coluna): coluna
        for coluna in df.columns
    }

    for nome in possibilidades:
        chave = normalizar_texto(nome)

        if chave in colunas_normalizadas:
            return colunas_normalizadas[chave]

    return None


# ============================================================
# PROTOCOLO
# ============================================================

def parse_protocolo(valor):
    """
    Aceita protocolos nos formatos:

        1318439/2026
        1318439/2026-1
        1318439/2026-2
        1318439/2026-10

    O número e o ano são extraídos para o lote,
    enquanto o texto original completo é preservado
    para a observação.
    """

    if pd.isna(valor):
        return None

    texto = str(valor).strip()

    if not texto:
        return None

    correspondencia = re.fullmatch(
        r"(\d+)\s*/\s*(\d{4})(?:\s*-\s*\d+)?",
        texto,
    )

    if not correspondencia:
        return None

    try:
        numero = int(
            correspondencia.group(1)
        )

        ano = int(
            correspondencia.group(2)
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    return {
        "numero": numero,
        "ano": ano,
        "texto": texto,
    }


def chave_protocolo(info):
    if not info:
        return (
            float("inf"),
            float("inf"),
        )

    return (
        info["ano"],
        info["numero"],
    )


# ============================================================
# CONSOLIDAÇÃO DOS SERVIÇOS
# ============================================================

def consolidar_servicos(df_servicos, modo):
    avisos = []

    if (
        df_servicos is None
        or df_servicos.empty
    ):
        return (
            {},
            {
                "linhas_analisadas": 0,
                "matriculas_validas": 0,
                "matriculas_ignoradas": 0,
                "servicos_considerados": 0,
            },
            avisos,
        )

    coluna_matricula = localizar_coluna(
        df_servicos,
        COL_MATRICULA,
    )

    coluna_protocolo = localizar_coluna(
        df_servicos,
        COL_PROTOCOLO,
    )

    coluna_descricao = localizar_coluna_descricao(
        df_servicos
    )

    if not coluna_matricula:
        avisos.append(
            "A base de Serviços não possui "
            "a coluna de matrícula."
        )

        return (
            {},
            {
                "linhas_analisadas": len(df_servicos),
                "matriculas_validas": 0,
                "matriculas_ignoradas": len(df_servicos),
                "servicos_considerados": 0,
            },
            avisos,
        )

    if not coluna_protocolo:
        avisos.append(
            "A base de Serviços não possui "
            "a coluna Cód. Protocolo Origem."
        )

        return (
            {},
            {
                "linhas_analisadas": len(df_servicos),
                "matriculas_validas": 0,
                "matriculas_ignoradas": len(df_servicos),
                "servicos_considerados": 0,
            },
            avisos,
        )

    if not coluna_descricao:
        avisos.append(
            "A base de Serviços não possui "
            "uma coluna de descrição do serviço reconhecida."
        )

        return (
            {},
            {
                "linhas_analisadas": len(df_servicos),
                "matriculas_validas": 0,
                "matriculas_ignoradas": len(df_servicos),
                "servicos_considerados": 0,
            },
            avisos,
        )

    servicos_por_matricula = {}

    matriculas_validas = 0
    matriculas_ignoradas = 0
    servicos_considerados = 0

    for _, linha in df_servicos.iterrows():

        matricula = normalizar_matricula(
            linha[coluna_matricula]
        )

        if not matricula_valida(
            matricula,
            modo,
        ):
            matriculas_ignoradas += 1
            continue

        protocolo = parse_protocolo(
            linha[coluna_protocolo]
        )

        descricao = linha[coluna_descricao]

        if protocolo is None:
            matriculas_ignoradas += 1
            continue

        if pd.isna(descricao):
            matriculas_ignoradas += 1
            continue

        descricao = str(descricao).strip()

        if not descricao:
            matriculas_ignoradas += 1
            continue

        matriculas_validas += 1

        registro = {
            "matricula": matricula,
            "protocolo": protocolo,
            "descricao": descricao,
        }

        servico_existente = (
            servicos_por_matricula.get(
                matricula
            )
        )

        if servico_existente is None:
            servicos_por_matricula[
                matricula
            ] = registro

            servicos_considerados += 1

            continue

        if chave_protocolo(
            protocolo
        ) < chave_protocolo(
            servico_existente["protocolo"]
        ):
            servicos_por_matricula[
                matricula
            ] = registro

    return (
        servicos_por_matricula,
        {
            "linhas_analisadas": len(df_servicos),
            "matriculas_validas": matriculas_validas,
            "matriculas_ignoradas": matriculas_ignoradas,
            "servicos_considerados": servicos_considerados,
        },
        avisos,
    )


# ============================================================
# GERAÇÃO DO LOTE
# ============================================================

def gerar_lote_servicos(
    df_backlog,
    servicos_por_matricula,
    modo,
):
    avisos = []

    if (
        df_backlog is None
        or df_backlog.empty
    ):
        return (
            pd.DataFrame(),
            {
                "os_analisadas": 0,
                "matriculas_validas": 0,
                "os_para_cancelar": 0,
                "sem_servico": 0,
            },
            avisos,
        )

    coluna_matricula = localizar_coluna(
        df_backlog,
        COL_MATRICULA,
    )

    coluna_protocolo = localizar_coluna(
        df_backlog,
        COL_PROTOCOLO,
    )

    coluna_cidade = localizar_coluna(
        df_backlog,
        COL_CIDADE,
    )

    if not coluna_matricula:
        avisos.append(
            "A base do backlog não possui "
            "a coluna de matrícula."
        )

        return (
            pd.DataFrame(),
            {
                "os_analisadas": len(df_backlog),
                "matriculas_validas": 0,
                "os_para_cancelar": 0,
                "sem_servico": 0,
            },
            avisos,
        )

    if not coluna_protocolo:
        avisos.append(
            "A base do backlog não possui "
            "a coluna Cód. Protocolo Origem."
        )

        return (
            pd.DataFrame(),
            {
                "os_analisadas": len(df_backlog),
                "matriculas_validas": 0,
                "os_para_cancelar": 0,
                "sem_servico": 0,
            },
            avisos,
        )

    if modo == "API" and not coluna_cidade:
        avisos.append(
            "A base API não possui a coluna CIDADE, "
            "necessária para determinar a Zona Ligacao."
        )

        return (
            pd.DataFrame(),
            {
                "os_analisadas": len(df_backlog),
                "matriculas_validas": 0,
                "os_para_cancelar": 0,
                "sem_servico": 0,
            },
            avisos,
        )

    resultados = []

    os_analisadas = len(df_backlog)
    matriculas_validas = 0
    os_para_cancelar = 0
    sem_servico = 0

    for _, linha in df_backlog.iterrows():

        matricula = normalizar_matricula(
            linha[coluna_matricula]
        )

        if not matricula_valida(
            matricula,
            modo,
        ):
            continue

        matriculas_validas += 1

        servico = servicos_por_matricula.get(
            matricula
        )

        if servico is None:
            sem_servico += 1
            continue

        protocolo_os = parse_protocolo(
            linha[coluna_protocolo]
        )

        if protocolo_os is None:
            avisos.append(
                "Uma O.S. com matrícula "
                f"{matricula} possui protocolo inválido "
                "e foi ignorada."
            )
            continue

        if modo == "THE":
            zona = 1

        else:
            cidade = linha[coluna_cidade]

            if pd.isna(cidade):
                avisos.append(
                    f"A O.S. {protocolo_os['texto']} "
                    f"possui matrícula {matricula}, "
                    "mas não possui cidade."
                )
                continue

            cidade = str(cidade).strip()

            try:
                zona = obter_zona(cidade)
            except Exception:
                zona = None

            if zona is None:
                avisos.append(
                    f"A cidade '{cidade}' não possui "
                    "zona cadastrada para a O.S. "
                    f"{protocolo_os['texto']}."
                )
                continue

        observacao = (
            "Cliente já possui serviço em aberto: "
            f"{servico['descricao']} - "
            f"O.S. {servico['protocolo']['texto']}"
        )

        resultados.append(
            {
                COL_SAIDA_MATRICULA: matricula,
                COL_SAIDA_ZONA: zona,
                COL_SAIDA_NUMERO: protocolo_os["numero"],
                COL_SAIDA_ANO: protocolo_os["ano"],
                COL_SAIDA_TIPO: 6,
                COL_SAIDA_OBSERVACOES: observacao,
            }
        )

        os_para_cancelar += 1

    resultado = pd.DataFrame(
        resultados,
        columns=[
            COL_SAIDA_MATRICULA,
            COL_SAIDA_ZONA,
            COL_SAIDA_NUMERO,
            COL_SAIDA_ANO,
            COL_SAIDA_TIPO,
            COL_SAIDA_OBSERVACOES,
        ],
    )

    indicadores = {
        "os_analisadas": os_analisadas,
        "matriculas_validas": matriculas_validas,
        "os_para_cancelar": os_para_cancelar,
        "sem_servico": sem_servico,
    }

    return (
        resultado,
        indicadores,
        avisos,
    )


# ============================================================
# ESTADO
# ============================================================

def inicializar_estado_servicos():
    estados = {
        "servicos_resultado_api": None,
        "servicos_resultado_the": None,
        "servicos_indicadores_api": None,
        "servicos_indicadores_the": None,
        "servicos_avisos_api": [],
        "servicos_avisos_the": [],
        "servicos_info_api": None,
        "servicos_info_the": None,
    }

    for chave, valor in estados.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


def limpar_resultado_servicos(modo=None):
    if modo is None:
        modos = [
            "api",
            "the",
        ]
    else:
        modos = [
            modo.lower()
        ]

    for chave_modo in modos:
        st.session_state[
            f"servicos_resultado_{chave_modo}"
        ] = None

        st.session_state[
            f"servicos_indicadores_{chave_modo}"
        ] = None

        st.session_state[
            f"servicos_avisos_{chave_modo}"
        ] = []

        st.session_state[
            f"servicos_info_{chave_modo}"
        ] = None


# ============================================================
# INTERFACE PRINCIPAL
# ============================================================

def render_servicos():
    inicializar_estado_servicos()
    render_sidebar()

    st.title("🛠️ Análise de Serviços")

    st.markdown(
        """
        Identifica as O.S. de **Falta de Água** cuja matrícula
        também possui um serviço em aberto na base correspondente.
        """
    )

    modo, df_backlog = selecionar_modo_api_the(
        key="servicos_modo_api_the",
    )

    if modo is None or df_backlog is None:
        st.stop()

    if modo not in {
        "API",
        "THE",
    }:
        modo = "API"

    chave_servicos = (
        "servicos_api"
        if modo == "API"
        else "servicos_the"
    )

    df_servicos = obter_base(
        chave_servicos
    )

    # ========================================================
    # BASES UTILIZADAS
    # ========================================================

    st.subheader("📊 Bases utilizadas")

    col1, col2 = st.columns(2)

    with col1:
        if df_backlog is not None:
            st.markdown(
                f"""
                <div class="coi-card">
                    <div class="coi-card-title">
                        💧 Backlog de Falta de Água — {modo}
                    </div>
                    <div class="coi-card-text">
                        Base carregada •
                        {len(df_backlog):,} registros
                    </div>
                </div>
                """.replace(",", "."),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div class="coi-card">
                    <div class="coi-card-title">
                        💧 Backlog de Falta de Água — {modo}
                    </div>
                    <div class="coi-card-text">
                        Base não carregada.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    with col2:
        if df_servicos is not None:
            st.markdown(
                f"""
                <div class="coi-card">
                    <div class="coi-card-title">
                        🛠️ Serviços — {modo}
                    </div>
                    <div class="coi-card-text">
                        Base carregada •
                        {len(df_servicos):,} registros
                    </div>
                </div>
                """.replace(",", "."),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div class="coi-card">
                    <div class="coi-card-title">
                        🛠️ Serviços — {modo}
                    </div>
                    <div class="coi-card-text">
                        Base não carregada.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    if (
        df_backlog is None
        or df_backlog.empty
    ):
        st.warning(
            "Carregue a base de Falta de Água "
            "no Gerador de Lotes antes de executar "
            "a análise."
        )
        st.stop()

    if (
        df_servicos is None
        or df_servicos.empty
    ):
        st.warning(
            "Carregue a base de Serviços correspondente "
            f"ao modo {modo} no Gerador de Lotes "
            "antes de executar a análise."
        )
        st.stop()

    st.divider()

    # ========================================================
    # EXECUÇÃO
    # ========================================================

    if st.button(
        "▶️ Executar análise",
        type="primary",
        use_container_width=True,
        key="servicos_executar",
    ):
        (
            servicos_por_matricula,
            info_servicos,
            avisos_servicos,
        ) = consolidar_servicos(
            df_servicos,
            modo,
        )

        (
            resultado,
            indicadores,
            avisos_backlog,
        ) = gerar_lote_servicos(
            df_backlog,
            servicos_por_matricula,
            modo,
        )

        avisos = (
            avisos_servicos
            + avisos_backlog
        )

        chave_modo = modo.lower()

        st.session_state[
            f"servicos_resultado_{chave_modo}"
        ] = resultado

        st.session_state[
            f"servicos_indicadores_{chave_modo}"
        ] = indicadores

        st.session_state[
            f"servicos_avisos_{chave_modo}"
        ] = avisos

        st.session_state[
            f"servicos_info_{chave_modo}"
        ] = info_servicos

        st.rerun()

    # ========================================================
    # RESULTADO
    # ========================================================

    chave_modo = modo.lower()

    resultado = st.session_state.get(
        f"servicos_resultado_{chave_modo}"
    )

    indicadores = st.session_state.get(
        f"servicos_indicadores_{chave_modo}"
    )

    avisos = st.session_state.get(
        f"servicos_avisos_{chave_modo}",
        [],
    )

    info_servicos = st.session_state.get(
        f"servicos_info_{chave_modo}"
    )

    if indicadores is None:
        st.info(
            "Selecione o modo e clique em "
            "**▶️ Executar análise** para iniciar."
        )
        st.stop()

    st.divider()

    st.subheader("📈 Resultado da análise")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "O.S. no backlog",
            f"{indicadores['os_analisadas']:,}"
            .replace(",", "."),
        )

    with col2:
        st.metric(
            "Matrículas válidas",
            f"{indicadores['matriculas_validas']:,}"
            .replace(",", "."),
        )

    with col3:
        st.metric(
            "O.S. para cancelar",
            f"{indicadores['os_para_cancelar']:,}"
            .replace(",", "."),
        )

    with col4:
        st.metric(
            "Sem Serviço correspondente",
            f"{indicadores['sem_servico']:,}"
            .replace(",", "."),
        )

    if info_servicos:
        ignorados = info_servicos.get(
            "matriculas_ignoradas",
            0,
        )

        considerados = info_servicos.get(
            "servicos_considerados",
            0,
        )

        st.caption(
            "Serviços considerados no cruzamento: "
            f"{considerados:,}".replace(",", ".")
            + " • Registros de serviços ignorados "
            "por informações incompletas ou inválidas: "
            f"{ignorados:,}".replace(",", ".")
        )

    if avisos:
        with st.expander(
            f"⚠️ Avisos ({len(avisos)})",
            expanded=False,
        ):
            for aviso in avisos:
                st.warning(aviso)

    # ========================================================
    # PRÉVIA
    # ========================================================

    st.subheader(
        "📋 Prévia do lote de cancelamento"
    )

    if (
        resultado is None
        or resultado.empty
    ):
        st.info(
            "Nenhuma O.S. foi identificada "
            "para cancelamento."
        )
        st.stop()

    st.dataframe(
        resultado.head(100),
        use_container_width=True,
        hide_index=True,
    )

    if len(resultado) > 100:
        st.caption(
            "Exibindo as primeiras 100 O.S. "
            f"de {len(resultado):,}."
            .replace(",", ".")
        )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    arquivo = dataframe_para_excel(
        resultado,
        nome_aba="Serviços",
    )

    if arquivo:
        nome_arquivo = (
            NOME_ARQUIVO_API
            if modo == "API"
            else NOME_ARQUIVO_THE
        )

        st.download_button(
            label="📥 Baixar lote de cancelamento",
            data=arquivo,
            file_name=nome_arquivo,
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
            key=f"servicos_download_{chave_modo}",
        )

    st.caption(
        "As bases permanecem carregadas durante a sessão. "
        "Você pode alternar entre API e THE e executar "
        "novas análises sem reenviar os arquivos."
    )
