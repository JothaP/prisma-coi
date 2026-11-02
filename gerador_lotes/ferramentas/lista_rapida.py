import re
import unicodedata

import pandas as pd
import streamlit as st

from ..estado import obter_base, base_carregada, limpar_resultado
from ..exportacao import dataframe_para_excel
from ..zonas import obter_zona
from .componentes import selecionar_modo_api_the


TIPO_ENCERRAMENTO = 6

COLUNAS_LOTE = [
    "Matricula",
    "Zona Ligacao",
    "Numero Do Pedido",
    "Ano Do Pedido",
    "Tipo Encerramento",
    "Observações",
]


def normalizar_texto(valor):
    if valor is None:
        return ""

    if pd.isna(valor):
        return ""

    texto = str(valor).strip().upper()
    texto = unicodedata.normalize(
        "NFKD",
        texto,
    ).encode(
        "ASCII",
        "ignore",
    ).decode(
        "ASCII",
    )
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def encontrar_coluna(df, nomes):
    if df is None or df.empty:
        return None

    mapa = {
        normalizar_texto(coluna): coluna
        for coluna in df.columns
    }

    for nome in nomes:
        chave = normalizar_texto(nome)

        if chave in mapa:
            return mapa[chave]

    return None


def parse_protocolo(valor):
    """
    Retorna (numero_str, ano_str).
    Aceita: 1275203/2026 | 1275203 | texto contendo o padrão.
    """
    if valor is None or (
        isinstance(valor, float)
        and pd.isna(valor)
    ):
        return None, None

    texto = str(valor).strip()

    if not texto:
        return None, None

    texto_limpo = re.sub(
        r"\s+",
        "",
        texto,
    )

    match = re.fullmatch(
        r"(\d+)/(\d{4})",
        texto_limpo,
    )

    if match:
        return (
            str(int(match.group(1))),
            match.group(2),
        )

    match = re.fullmatch(
        r"(\d+)",
        texto_limpo,
    )

    if match:
        return (
            str(int(match.group(1))),
            None,
        )

    match = re.search(
        r"(\d+)/(\d{4})",
        texto_limpo,
    )

    if match:
        return (
            str(int(match.group(1))),
            match.group(2),
        )

    match = re.search(
        r"(\d+)",
        texto_limpo,
    )

    if match:
        return (
            str(int(match.group(1))),
            None,
        )

    return None, None


def extrair_protocolos(texto):
    if not texto:
        return []

    partes = re.split(
        r"[\n,;]+",
        str(texto),
    )

    protocolos = []

    for parte in partes:
        parte = parte.strip()

        if not parte:
            continue

        numero, ano = parse_protocolo(parte)

        protocolos.append(
            {
                "entrada": parte,
                "numero": numero,
                "ano": ano,
                "valido": numero is not None,
            }
        )

    return protocolos


def preparar_backlog(df):
    """
    Prepara o backlog de reclamação para a Lista Rápida.

    Número e ano saem de 'Cód. Protocolo Origem' (ex.: 1275203/2026).
    Não depende de coluna ANO separada.
    """
    if df is None or df.empty:
        return None, "A base selecionada está vazia."

    col_protocolo = encontrar_coluna(
        df,
        [
            "COD. PROTOCOLO ORIGEM",
            "COD PROTOCOLO ORIGEM",
            "CODIGO PROTOCOLO ORIGEM",
            "CÓD. PROTOCOLO ORIGEM",
            "PROTOCOLO",
        ],
    )

    col_matricula = encontrar_coluna(
        df,
        ["MATRICULA", "MATRÍCULA"],
    )

    col_cidade = encontrar_coluna(
        df,
        ["CIDADE", "MUNICIPIO", "MUNICÍPIO"],
    )

    if col_protocolo is None:
        return None, (
            "A base selecionada não possui a coluna "
            "'Cód. Protocolo Origem'."
        )

    if col_matricula is None:
        return None, (
            "A base selecionada não possui a coluna 'Matrícula'."
        )

    if col_cidade is None:
        return None, (
            "A base selecionada não possui a coluna 'Cidade'."
        )

    base = df.copy()

    parsed = base[col_protocolo].apply(parse_protocolo)

    base["_numero_lista_rapida"] = parsed.apply(
        lambda x: x[0] if isinstance(x, tuple) else None
    )

    base["_ano_lista_rapida"] = parsed.apply(
        lambda x: x[1] if isinstance(x, tuple) else None
    )

    base["_matricula_lista_rapida"] = base[col_matricula]
    base["_cidade_lista_rapida"] = base[col_cidade]

    base = base[
        base["_numero_lista_rapida"].notna()
    ].copy()

    if base.empty:
        return None, (
            "Nenhum protocolo válido foi encontrado na coluna "
            "'Cód. Protocolo Origem'."
        )

    return base, None


def cruzar_lista_com_backlog(
    protocolos,
    df_backlog,
    modo,
    observacoes,
):
    base, erro = preparar_backlog(df_backlog)

    if erro:
        return (
            pd.DataFrame(columns=COLUNAS_LOTE),
            [],
            [],
            erro,
        )

    indice_num_ano = {}
    indice_numero = {}

    for _, linha in base.iterrows():
        numero = linha["_numero_lista_rapida"]
        ano = linha["_ano_lista_rapida"]

        if numero is None:
            continue

        if numero not in indice_numero:
            indice_numero[numero] = linha

        if ano is not None:
            chave = (numero, ano)

            if chave not in indice_num_ano:
                indice_num_ano[chave] = linha

    resultado = []
    nao_encontrados = []
    entradas_invalidas = []
    ja_incluidas = set()

    for item in protocolos:
        entrada = item["entrada"]
        numero = item["numero"]
        ano = item["ano"]

        if not item["valido"] or numero is None:
            entradas_invalidas.append(entrada)
            continue

        chave_unica = (
            (numero, ano)
            if ano is not None
            else (numero, None)
        )

        if chave_unica in ja_incluidas:
            continue

        linha = None

        if ano is not None:
            linha = indice_num_ano.get(
                (numero, ano)
            )

        if linha is None:
            linha = indice_numero.get(numero)

        if linha is None:
            nao_encontrados.append(entrada)
            continue

        ja_incluidas.add(chave_unica)

        cidade = linha["_cidade_lista_rapida"]
        matricula = linha["_matricula_lista_rapida"]

        ano_final = linha["_ano_lista_rapida"]

        if ano_final is None:
            ano_final = ano

        if modo == "THE":
            zona = 1
        else:
            zona = obter_zona(cidade)

            if zona is None:
                nao_encontrados.append(
                    f"{entrada} — cidade sem zona: {cidade}"
                )
                continue

        resultado.append(
            {
                "Matricula": matricula,
                "Zona Ligacao": zona,
                "Numero Do Pedido": numero,
                "Ano Do Pedido": ano_final,
                "Tipo Encerramento": TIPO_ENCERRAMENTO,
                "Observações": observacoes or "",
            }
        )

    df_saida = pd.DataFrame(
        resultado,
        columns=COLUNAS_LOTE,
    )

    for col in [
        "Zona Ligacao",
        "Numero Do Pedido",
        "Ano Do Pedido",
        "Tipo Encerramento",
    ]:
        if col in df_saida.columns:
            df_saida[col] = pd.to_numeric(
                df_saida[col],
                errors="coerce",
            ).astype("Int64")

    return (
        df_saida,
        nao_encontrados,
        entradas_invalidas,
        None,
    )


def limpar_estado_lista_rapida():
    chaves = [
        "lista_rapida_resultado",
        "lista_rapida_log",
        "lista_rapida_nome_arquivo",
        "lista_rapida_geracao_info",
    ]

    for chave in chaves:
        if chave in st.session_state:
            st.session_state[chave] = None


def aplicar_estilo_lista_rapida():
    st.markdown(
        """
        <style>

        .lista-rapida-header {
            margin-bottom: 24px;
        }

        .lista-rapida-header-title {
            font-size: 1.65rem;
            font-weight: 700;
            line-height: 1.2;
            margin-bottom: 5px;
        }

        .lista-rapida-header-description {
            color: rgba(128, 128, 128, 0.95);
            font-size: 0.92rem;
        }

        .lista-rapida-card {
            border: 1px solid rgba(128, 128, 128, 0.22);
            border-radius: 12px;
            padding: 20px 22px;
            margin-bottom: 18px;
            background: rgba(128, 128, 128, 0.025);
        }

        .lista-rapida-card-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin-bottom: 4px;
        }

        .lista-rapida-card-description {
            color: rgba(128, 128, 128, 0.9);
            font-size: 0.85rem;
            margin-bottom: 14px;
        }

        .lista-rapida-section {
            margin-top: 8px;
            margin-bottom: 12px;
        }

        .lista-rapida-section-title {
            font-size: 1.08rem;
            font-weight: 700;
            margin-bottom: 12px;
        }

        .lista-rapida-metricas {
            display: grid;
            grid-template-columns: repeat(
                4,
                minmax(0, 1fr)
            );
            gap: 12px;
            margin: 16px 0 20px 0;
        }

        .lista-rapida-metrica {
            border: 1px solid rgba(128, 128, 128, 0.22);
            border-radius: 10px;
            padding: 13px 16px;
            background: rgba(128, 128, 128, 0.025);
        }

        .lista-rapida-metrica-label {
            color: rgba(128, 128, 128, 0.9);
            font-size: 0.78rem;
            margin-bottom: 3px;
        }

        .lista-rapida-metrica-valor {
            font-size: 1.3rem;
            font-weight: 700;
            line-height: 1.2;
        }

        .lista-rapida-preview-title {
            font-size: 1.08rem;
            font-weight: 700;
            margin-bottom: 10px;
        }

        /* =========================================================
           BOTÕES — PADRÃO TEMA ESCURO
           ========================================================= */

        [data-testid="stButton"] > button,
        [data-testid="stDownloadButton"] > button {
            border-radius: 8px !important;
            min-height: 42px !important;
            font-weight: 600 !important;
        }

        [data-testid="stButton"] > button:not([kind="primary"]),
        [data-testid="stDownloadButton"] > button {
            background-color: transparent !important;
            color: inherit !important;
            border: 1px solid rgba(
                128,
                128,
                128,
                0.45
            ) !important;
        }

        [data-testid="stButton"] > button:not([kind="primary"]):hover,
        [data-testid="stDownloadButton"] > button:hover {
            background-color: rgba(
                128,
                128,
                128,
                0.10
            ) !important;
            color: inherit !important;
            border-color: rgba(
                128,
                128,
                128,
                0.65
            ) !important;
        }

        [data-testid="stButton"] > button[kind="primary"] {
            color: white !important;
            border: 1px solid transparent !important;
        }

        [data-testid="stButton"] > button[kind="primary"]:hover {
            filter: brightness(1.08);
        }

        [data-testid="stButton"] > button p,
        [data-testid="stButton"] > button span,
        [data-testid="stDownloadButton"] > button p,
        [data-testid="stDownloadButton"] > button span {
            color: inherit !important;
        }

        @media (max-width: 900px) {
            .lista-rapida-metricas {
                grid-template-columns: repeat(
                    2,
                    minmax(0, 1fr)
                );
            }
        }

        @media (max-width: 600px) {
            .lista-rapida-metricas {
                grid-template-columns: 1fr;
            }

            .lista-rapida-card {
                padding: 16px;
            }
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


def render_lista_rapida():
    aplicar_estilo_lista_rapida()

    st.markdown(
        """
        <div class="lista-rapida-header">
            <div class="lista-rapida-header-title">
                📋 Lista Rápida
            </div>
            <div class="lista-rapida-header-description">
                Geração de lote de cancelamento a partir de uma lista
                de O.S. ou protocolos.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not base_carregada("api") and not base_carregada("the"):
        st.warning(
            "Nenhuma base de operação disponível. "
            "Carregue a base API e/ou THE no Hub."
        )

        if st.button(
            "⬅️ Voltar ao Gerador de Lotes",
            key="lista_rapida_voltar_sem_base",
            use_container_width=False,
        ):
            st.session_state["ferramenta_atual"] = None
            st.rerun()

        return

    if "lista_rapida_resultado" not in st.session_state:
        st.session_state["lista_rapida_resultado"] = None

    if "lista_rapida_log" not in st.session_state:
        st.session_state["lista_rapida_log"] = None

    if "lista_rapida_nome_arquivo" not in st.session_state:
        st.session_state["lista_rapida_nome_arquivo"] = None

    if "lista_rapida_protocolos" not in st.session_state:
        st.session_state["lista_rapida_protocolos"] = ""

    if "lista_rapida_observacoes" not in st.session_state:
        st.session_state["lista_rapida_observacoes"] = ""

    st.markdown(
        '<div class="lista-rapida-card">',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card-title">'
        "⚙️ Base de operação"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card-description">'
        "Selecione a base que será utilizada para localizar "
        "as O.S. informadas."
        "</div>",
        unsafe_allow_html=True,
    )

    modo, df_backlog = selecionar_modo_api_the(
        key="lista_rapida_modo",
        titulo="Base de operação",
        limpar_resultado_callback=limpar_estado_lista_rapida,
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    if modo is None or df_backlog is None:
        return

    st.markdown(
        '<div class="lista-rapida-card">',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card-title">'
        "📋 Lista de protocolos"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card-description">'
        "Informe uma O.S./protocolo por linha ou separe "
        "os registros por vírgula."
        "</div>",
        unsafe_allow_html=True,
    )

    st.text_area(
        "Protocolos / O.S.",
        height=230,
        placeholder=(
            "Exemplo:\n"
            "123456/2026\n"
            "123457/2026\n"
            "123458/2026"
        ),
        key="lista_rapida_protocolos",
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card">',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card-title">'
        "📝 Observações"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="lista-rapida-card-description">'
        "Texto definido pelo usuário e aplicado ao lote gerado."
        "</div>",
        unsafe_allow_html=True,
    )

    st.text_area(
        "Observações do lote",
        height=120,
        placeholder=(
            "Digite a observação que deverá constar no lote."
        ),
        key="lista_rapida_observacoes",
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="lista-rapida-section">
            <div class="lista-rapida-section-title">
                📦 Geração do lote
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "⚙️ Gerar lote",
        type="primary",
        use_container_width=True,
        key="lista_rapida_gerar_lote",
    ):
        protocolos_texto = st.session_state.get(
            "lista_rapida_protocolos",
            "",
        )

        observacoes = st.session_state.get(
            "lista_rapida_observacoes",
            "",
        )

        if not protocolos_texto.strip():
            st.warning(
                "Informe pelo menos uma O.S./protocolo."
            )
            st.stop()

        protocolos = extrair_protocolos(
            protocolos_texto
        )

        if not protocolos:
            st.warning(
                "Nenhum protocolo válido foi identificado."
            )
            st.stop()

        (
            df_resultado,
            nao_encontrados,
            entradas_invalidas,
            erro,
        ) = cruzar_lista_com_backlog(
            protocolos=protocolos,
            df_backlog=df_backlog,
            modo=modo,
            observacoes=observacoes,
        )

        if erro:
            st.error(erro)
            st.stop()

        st.session_state["lista_rapida_resultado"] = (
            df_resultado
        )

        st.session_state["lista_rapida_log"] = {
            "nao_encontrados": nao_encontrados,
            "entradas_invalidas": entradas_invalidas,
            "total_informado": len(protocolos),
            "total_gerado": len(df_resultado),
        }

        st.session_state["lista_rapida_nome_arquivo"] = (
            f"Lista Rapida {modo}.xlsx"
        )

        st.rerun()

    df_resultado = st.session_state.get(
        "lista_rapida_resultado"
    )

    log = st.session_state.get(
        "lista_rapida_log"
    )

    if df_resultado is None:
        if st.button(
            "⬅️ Voltar",
            key="lista_rapida_voltar",
            use_container_width=False,
        ):
            st.session_state["ferramenta_atual"] = None
            st.rerun()

        return

    st.markdown("---")

    st.markdown(
        '<div class="lista-rapida-preview-title">'
        "📊 Prévia do lote"
        "</div>",
        unsafe_allow_html=True,
    )

    total_informado = (
        log.get(
            "total_informado",
            0,
        )
        if log
        else 0
    )

    total_gerado = len(df_resultado)

    nao_encontrados = (
        log.get(
            "nao_encontrados",
            [],
        )
        if log
        else []
    )

    entradas_invalidas = (
        log.get(
            "entradas_invalidas",
            [],
        )
        if log
        else []
    )

    st.markdown(
        '<div class="lista-rapida-metricas">'
        '<div class="lista-rapida-metrica">'
        '<div class="lista-rapida-metrica-label">'
        "Informados"
        "</div>"
        '<div class="lista-rapida-metrica-valor">'
        f"{total_informado}"
        "</div>"
        "</div>"
        '<div class="lista-rapida-metrica">'
        '<div class="lista-rapida-metrica-label">'
        "Gerados"
        "</div>"
        '<div class="lista-rapida-metrica-valor">'
        f"{total_gerado}"
        "</div>"
        "</div>"
        '<div class="lista-rapida-metrica">'
        '<div class="lista-rapida-metrica-label">'
        "Não encontrados"
        "</div>"
        '<div class="lista-rapida-metrica-valor">'
        f"{len(nao_encontrados)}"
        "</div>"
        "</div>"
        '<div class="lista-rapida-metrica">'
        '<div class="lista-rapida-metrica-label">'
        "Inválidos"
        "</div>"
        '<div class="lista-rapida-metrica-valor">'
        f"{len(entradas_invalidas)}"
        "</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if entradas_invalidas:
        st.warning(
            "Entradas não reconhecidas como protocolo: "
            + ", ".join(
                str(item)
                for item in entradas_invalidas
            )
        )

    if nao_encontrados:
        st.warning(
            "O.S. não encontrada: "
            + ", ".join(
                str(item)
                for item in nao_encontrados
            )
        )

    if df_resultado.empty:
        st.error(
            "Nenhum registro da lista foi encontrado "
            "no backlog."
        )

    else:
        st.dataframe(
            df_resultado,
            use_container_width=True,
            hide_index=True,
        )

        arquivo = dataframe_para_excel(
            df_resultado,
            nome_aba="Lista Rápida",
        )

        if arquivo is not None:
            dados_excel = (
                arquivo
                if isinstance(
                    arquivo,
                    (bytes, bytearray),
                )
                else arquivo.getvalue()
            )

            st.download_button(
                label="⬇️ Baixar lote",
                data=dados_excel,
                file_name=st.session_state.get(
                    "lista_rapida_nome_arquivo",
                    "Lista Rapida.xlsx",
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
                key="lista_rapida_download",
            )

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        if st.button(
            "🧹 Limpar resultado",
            use_container_width=True,
            key="lista_rapida_limpar_resultado",
        ):
            limpar_resultado()
            limpar_estado_lista_rapida()
            st.rerun()

    with col2:
        if st.button(
            "⬅️ Voltar",
            use_container_width=True,
            key="lista_rapida_voltar_resultado",
        ):
            limpar_resultado()
            limpar_estado_lista_rapida()
            st.session_state["ferramenta_atual"] = None
            st.rerun()
