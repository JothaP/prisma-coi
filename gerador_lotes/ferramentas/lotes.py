import re
import unicodedata

import pandas as pd
import streamlit as st

from ..estado import (
    obter_arquivos_base,
    base_carregada,
    limpar_resultado,
)
from ..exportacao import dataframe_para_excel
from .componentes import selecionar_modo_api_the


def normalizar_texto(texto):
    if texto is None or pd.isna(texto):
        return ""

    texto = str(texto)

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        c
        for c in texto
        if not unicodedata.combining(c)
    )

    texto = texto.upper().strip()

    texto = re.sub(
        r"\s+",
        " ",
        texto,
    )

    return texto


def parse_protocolo(proto):
    if proto is None or pd.isna(proto):
        return None, None, None

    texto = str(proto).strip()

    if not texto:
        return None, None, None

    texto = re.sub(
        r"[^\d/]",
        "",
        texto,
    )

    if "/" in texto:
        partes = texto.split("/")

        numero_str = partes[0]

        ano_str = (
            partes[1]
            if len(partes) > 1
            else None
        )
    else:
        numero_str = texto
        ano_str = None

    try:
        numero_int = (
            int(numero_str)
            if numero_str
            else None
        )
    except ValueError:
        numero_int = None

    return (
        numero_str,
        ano_str,
        numero_int,
    )


def localizar_coluna(df, tipo):
    if df is None or df.empty:
        return None

    normalizadas = {
        col: normalizar_texto(col)
        for col in df.columns
    }

    if tipo == "protocolo":
        for col, nome in normalizadas.items():
            if (
                "COD PROTOCOLO ORIGEM"
                in nome
                or
                "COD. PROTOCOLO ORIGEM"
                in nome
            ):
                return col

            if (
                "PROTOCOLO" in nome
                and "ORIGEM" in nome
            ):
                return col

        for col, nome in normalizadas.items():
            if "PROTOCOLO" in nome:
                return col

    elif tipo == "matricula":
        for col, nome in normalizadas.items():
            if nome == "MATRICULA":
                return col

        for col, nome in normalizadas.items():
            if "MATRICULA" in nome:
                return col

    elif tipo == "bairro":
        for col, nome in normalizadas.items():
            if nome == "BAIRRO":
                return col

        for col, nome in normalizadas.items():
            if "BAIRRO" in nome:
                return col

    return None


def localizar_coluna_lote(
    df,
    candidatos,
    obrigatoria=True,
):
    normalizadas = {
        coluna: normalizar_texto(coluna)
        for coluna in df.columns
    }

    for candidato in candidatos:
        candidato_norm = normalizar_texto(
            candidato
        )

        for coluna, nome in normalizadas.items():
            if nome == candidato_norm:
                return coluna

    for candidato in candidatos:
        candidato_norm = normalizar_texto(
            candidato
        )

        for coluna, nome in normalizadas.items():
            if candidato_norm in nome:
                return coluna

    if obrigatoria:
        raise ValueError(
            "Não foi encontrada a coluna de lote: "
            + " / ".join(candidatos)
        )

    return None


def consolidar_lotes_com_fonte(
    lista_lotes
):
    if not lista_lotes:
        raise ValueError(
            "Nenhum arquivo de lote foi carregado."
        )

    bases = []

    for item in lista_lotes:
        df = item["df"]

        if df is None or df.empty:
            continue

        temp = df.copy()

        temp["_FONTE_LOTE"] = item[
            "nome"
        ]

        bases.append(temp)

    if not bases:
        raise ValueError(
            "Os arquivos de lote carregados "
            "não possuem dados."
        )

    df = pd.concat(
        bases,
        ignore_index=True,
        sort=False,
    )

    df = df.dropna(
        how="all"
    ).reset_index(
        drop=True
    )

    if df.empty:
        raise ValueError(
            "Após consolidação, os lotes "
            "ficaram sem registros."
        )

    return df


def cruzar_acompanhamento_lote(
    df_backlog,
    df_lotes_com_fonte,
    motivos_por_arquivo,
):
    if (
        df_backlog is None
        or df_backlog.empty
    ):
        raise ValueError(
            "Backlog de O.S. de reclamação "
            "vazio ou não carregado."
        )

    if (
        df_lotes_com_fonte is None
        or df_lotes_com_fonte.empty
    ):
        raise ValueError(
            "Nenhum lote consolidado."
        )

    col_mat_lote = localizar_coluna_lote(
        df_lotes_com_fonte,
        [
            "Matricula",
            "Matrícula",
        ],
    )

    col_num_lote = localizar_coluna_lote(
        df_lotes_com_fonte,
        [
            "Numero Do Pedido",
            "Número Do Pedido",
            "Numero do Pedido",
        ],
    )

    col_ano_lote = localizar_coluna_lote(
        df_lotes_com_fonte,
        [
            "Ano Do Pedido",
            "Ano do Pedido",
        ],
    )

    col_mat_back = localizar_coluna(
        df_backlog,
        "matricula",
    )

    col_prot_back = localizar_coluna(
        df_backlog,
        "protocolo",
    )

    col_bairro_back = localizar_coluna(
        df_backlog,
        "bairro",
    )

    if col_mat_back is None:
        raise ValueError(
            "Não foi encontrada a coluna "
            "'Matrícula' no backlog."
        )

    if col_prot_back is None:
        raise ValueError(
            "Não foi encontrada a coluna "
            "'Cód. Protocolo Origem' no backlog."
        )

    if col_bairro_back is None:
        raise ValueError(
            "Não foi encontrada a coluna "
            "'Bairro' no backlog."
        )

    # ========================================================
    # ÍNDICE DO BACKLOG
    # ========================================================

    indice_backlog = {}

    for _, linha in df_backlog.iterrows():

        mat_norm = normalizar_texto(
            linha[col_mat_back]
        )

        if not mat_norm:
            continue

        protocolo = linha[
            col_prot_back
        ]

        _, ano_str, numero_int = (
            parse_protocolo(
                protocolo
            )
        )

        if numero_int is None:
            continue

        try:
            ano_int = (
                int(ano_str)
                if ano_str
                else None
            )
        except Exception:
            ano_int = None

        if ano_int is None:
            continue

        chave = (
            mat_norm,
            numero_int,
            ano_int,
        )

        if chave in indice_backlog:
            continue

        bairro = ""

        if pd.notna(
            linha[col_bairro_back]
        ):
            bairro = str(
                linha[col_bairro_back]
            ).strip()

        indice_backlog[chave] = {
            "bairro": bairro,
            "protocolo": (
                str(protocolo).strip()
                if pd.notna(protocolo)
                else ""
            ),
            "matricula_original": (
                linha[col_mat_back]
            ),
        }

    # ========================================================
    # CRUZAMENTO DOS LOTES
    # ========================================================

    resultados = []
    avisos = []

    ja_incluidas = set()

    for _, linha in (
        df_lotes_com_fonte.iterrows()
    ):

        mat_lote = linha[
            col_mat_lote
        ]

        mat_norm = normalizar_texto(
            mat_lote
        )

        if not mat_norm:
            continue

        try:
            numero = (
                int(linha[col_num_lote])
                if pd.notna(
                    linha[col_num_lote]
                )
                else None
            )
        except Exception:
            numero = None

        try:
            ano = (
                int(linha[col_ano_lote])
                if pd.notna(
                    linha[col_ano_lote]
                )
                else None
            )
        except Exception:
            ano = None

        fonte = ""

        if (
            "_FONTE_LOTE"
            in df_lotes_com_fonte.columns
        ):
            valor_fonte = linha.get(
                "_FONTE_LOTE"
            )

            if pd.notna(valor_fonte):
                fonte = str(
                    valor_fonte
                ).strip()

        if (
            numero is None
            or ano is None
        ):
            avisos.append(
                {
                    "Tipo": (
                        "Pedido inválido no lote"
                    ),
                    "Matrícula": mat_lote,
                    "Numero Do Pedido": (
                        linha[col_num_lote]
                    ),
                    "Ano Do Pedido": (
                        linha[col_ano_lote]
                    ),
                    "Arquivo Lote": fonte,
                    "Motivo": (
                        "Número ou Ano do Pedido "
                        "inválido no lote"
                    ),
                }
            )

            continue

        chave = (
            mat_norm,
            numero,
            ano,
        )

        # Primeira ocorrência prevalece.
        if chave in ja_incluidas:
            continue

        info = indice_backlog.get(
            chave
        )

        # ====================================================
        # FALLBACK EXISTENTE NO COLAB
        # ====================================================

        if info is None:
            candidatos = [
                valor
                for chave_backlog, valor
                in indice_backlog.items()
                if chave_backlog[0]
                == mat_norm
            ]

            if len(candidatos) == 1:
                info = candidatos[0]

            else:
                avisos.append(
                    {
                        "Tipo": (
                            "O.S. não encontrada "
                            "no backlog"
                        ),
                        "Matrícula": mat_lote,
                        "Numero Do Pedido": numero,
                        "Ano Do Pedido": ano,
                        "Arquivo Lote": fonte,
                        "Motivo": (
                            "Matrícula + Número/Ano "
                            "não encontrados no "
                            "backlog de reclamação"
                        ),
                    }
                )

                continue

        motivo = ""

        if (
            fonte
            and motivos_por_arquivo
        ):
            motivo = str(
                motivos_por_arquivo.get(
                    fonte,
                    "",
                )
                or ""
            ).strip()

        numero_os = (
            f"{numero} / {ano}"
        )

        resultados.append(
            {
                "Matrícula": (
                    info[
                        "matricula_original"
                    ]
                ),
                "Número da O.S.": numero_os,
                "Bairro": info[
                    "bairro"
                ],
                "Motivo do cancelamento": motivo,
                "Arquivo Lote": fonte,
            }
        )

        ja_incluidas.add(
            chave
        )

    df_saida = pd.DataFrame(
        resultados,
        columns=[
            "Matrícula",
            "Número da O.S.",
            "Bairro",
            "Motivo do cancelamento",
            "Arquivo Lote",
        ],
    )

    return (
        df_saida,
        avisos,
        len(df_lotes_com_fonte),
        len(resultados),
    )


def limpar_estado_lotes():
    chaves = [
        "lotes_motivos",
        "lotes_resultado",
        "lotes_avisos",
        "lotes_analisado",
        "lotes_total_registros",
        "lotes_total_encontradas",
    ]

    for chave in chaves:
        if chave in st.session_state:
            del st.session_state[chave]

    limpar_resultado()


def aplicar_estilo_lotes():
    st.markdown(
        """
        <style>

        .lotes-header {
            margin-bottom: 24px;
        }

        .lotes-header-title {
            font-size: 1.65rem;
            font-weight: 700;
            line-height: 1.2;
            margin-bottom: 5px;
        }

        .lotes-header-description {
            color: rgba(128, 128, 128, 0.95);
            font-size: 0.92rem;
        }

        .lotes-card {
            border: 1px solid rgba(
                128,
                128,
                128,
                0.22
            );
            border-radius: 12px;
            padding: 20px 22px;
            margin-bottom: 18px;
            background: rgba(
                128,
                128,
                128,
                0.025
            );
        }

        .lotes-card-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin-bottom: 4px;
        }

        .lotes-card-description {
            color: rgba(
                128,
                128,
                128,
                0.90
            );
            font-size: 0.85rem;
            margin-bottom: 14px;
        }

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

        </style>
        """,
        unsafe_allow_html=True,
    )


def render_lotes():
    aplicar_estilo_lotes()

    st.markdown(
        """
        <div class="lotes-header">
            <div class="lotes-header-title">
                📦 Análise de Lotes
            </div>
            <div class="lotes-header-description">
                Cruza os lotes gerados com o backlog de
                reclamação para identificar o bairro e
                acompanhar o motivo do cancelamento.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ========================================================
    # VERIFICAÇÃO DOS ARQUIVOS DE LOTE
    # ========================================================

    arquivos_lotes = obter_arquivos_base(
        "lotes"
    )

    if not arquivos_lotes:
        st.warning(
            "Nenhum arquivo de Lote foi carregado no Hub Central."
        )

        st.info(
            "Volte ao Hub, carregue um ou mais arquivos na "
            "base Lotes e acesse novamente esta ferramenta."
        )

        st.divider()

        if st.button(
            "⬅️ Voltar ao Hub",
            use_container_width=True,
            key="btn_voltar_hub_lotes_sem_arquivos",
        ):
            st.session_state[
                "ferramenta_atual"
            ] = None

            st.rerun()

        st.stop()

    # ========================================================
    # MODO API / THE
    # ========================================================

    modo, df_backlog = selecionar_modo_api_the(
        key="lotes_modo_api_the",
        titulo="Base de operação",
        limpar_resultado_callback=limpar_estado_lotes,
    )

    if modo is None or df_backlog is None:
        st.stop()

    st.divider()

    # ========================================================
    # ARQUIVOS CARREGADOS
    # ========================================================

    st.markdown(
        """
        <div class="lotes-card">
            <div class="lotes-card-title">
                📂 Lotes carregados
            </div>
            <div class="lotes-card-description">
                Estes são os arquivos carregados na base Lotes
                pelo Hub Central. Informe abaixo o motivo de
                cancelamento correspondente a cada arquivo.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    motivos = {}

    for indice, item in enumerate(
        arquivos_lotes
    ):
        nome_arquivo = item.get(
            "nome",
            f"Lote {indice + 1}",
        )

        chave = (
            f"lotes_motivo_"
            f"{indice}_"
            f"{nome_arquivo}"
        )

        motivo = st.text_input(
            f"Motivo do cancelamento — {nome_arquivo}",
            key=chave,
            placeholder=(
                "Digite o motivo do cancelamento"
            ),
        )

        motivos[
            nome_arquivo
        ] = motivo.strip()

    st.divider()

    # ========================================================
    # RESUMO
    # ========================================================

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Arquivos de lote",
            len(arquivos_lotes),
        )

    with col2:
        st.metric(
            "Registros no backlog",
            f"{len(df_backlog):,}".replace(
                ",",
                ".",
            ),
        )

    with col3:
        preenchidos = sum(
            bool(motivo)
            for motivo in motivos.values()
        )

        st.metric(
            "Motivos preenchidos",
            f"{preenchidos}/{len(arquivos_lotes)}",
        )

    st.divider()

    # ========================================================
    # GERAR ACOMPANHAMENTO
    # ========================================================

    if st.button(
        "⚙️ Gerar acompanhamento",
        type="primary",
        use_container_width=True,
        key="btn_gerar_acompanhamento_lotes",
    ):
        arquivos_sem_motivo = [
            nome
            for nome, motivo
            in motivos.items()
            if not motivo
        ]

        if arquivos_sem_motivo:
            st.error(
                "Informe o motivo do cancelamento "
                "para todos os arquivos de lote."
            )

        else:
            try:
                df_lotes_com_fonte = (
                    consolidar_lotes_com_fonte(
                        arquivos_lotes
                    )
                )

                (
                    df_saida,
                    avisos,
                    qtd_registros,
                    qtd_encontradas,
                ) = cruzar_acompanhamento_lote(
                    df_backlog,
                    df_lotes_com_fonte,
                    motivos,
                )

                st.session_state[
                    "lotes_motivos"
                ] = motivos

                st.session_state[
                    "lotes_resultado"
                ] = df_saida

                st.session_state[
                    "lotes_avisos"
                ] = avisos

                st.session_state[
                    "lotes_total_registros"
                ] = qtd_registros

                st.session_state[
                    "lotes_total_encontradas"
                ] = qtd_encontradas

                st.session_state[
                    "lotes_analisado"
                ] = True

                st.rerun()

            except Exception as exc:
                st.error(
                    f"Não foi possível gerar o acompanhamento: {exc}"
                )

    # ========================================================
    # RESULTADO
    # ========================================================

    if not st.session_state.get(
        "lotes_analisado",
        False,
    ):
        st.divider()

        if st.button(
            "⬅️ Voltar ao Hub",
            use_container_width=True,
            key="btn_voltar_hub_lotes",
        ):
            st.session_state[
                "ferramenta_atual"
            ] = None

            st.rerun()

        return

    df_saida = st.session_state.get(
        "lotes_resultado"
    )

    avisos = st.session_state.get(
        "lotes_avisos",
        [],
    )

    qtd_registros = st.session_state.get(
        "lotes_total_registros",
        0,
    )

    qtd_encontradas = st.session_state.get(
        "lotes_total_encontradas",
        0,
    )

    st.divider()

    st.markdown(
        "### 📋 Resultado do acompanhamento"
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Registros dos lotes",
            f"{qtd_registros:,}".replace(
                ",",
                ".",
            ),
        )

    with col2:
        st.metric(
            "Encontrados no backlog",
            f"{qtd_encontradas:,}".replace(
                ",",
                ".",
            ),
        )

    with col3:
        st.metric(
            "Avisos",
            f"{len(avisos):,}".replace(
                ",",
                ".",
            ),
        )

    if (
        df_saida is None
        or df_saida.empty
    ):
        st.warning(
            "Nenhuma O.S. dos lotes foi encontrada "
            "no backlog selecionado."
        )

    else:
        st.dataframe(
            df_saida,
            use_container_width=True,
            hide_index=True,
        )

        arquivo_excel = (
            dataframe_para_excel(
                df_saida,
                nome_aba="Acompanhamento",
            )
        )

        if arquivo_excel is not None:
            st.download_button(
                "📥 Baixar acompanhamento",
                data=arquivo_excel,
                file_name=(
                    "Acompanhamento de Lotes.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
                key=(
                    "btn_download_acompanhamento_lotes"
                ),
            )

    # ========================================================
    # AVISOS
    # ========================================================

    if avisos:
        st.divider()

        st.markdown(
            "### ⚠️ Avisos"
        )

        df_avisos = pd.DataFrame(
            avisos
        )

        st.dataframe(
            df_avisos,
            use_container_width=True,
            hide_index=True,
        )

        arquivo_avisos = (
            dataframe_para_excel(
                df_avisos,
                nome_aba="Avisos",
            )
        )

        if arquivo_avisos is not None:
            st.download_button(
                "📥 Baixar log de avisos",
                data=arquivo_avisos,
                file_name=(
                    "Log de Avisos - Lotes.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
                key="btn_download_avisos_lotes",
            )

    # ========================================================
    # AÇÕES
    # ========================================================

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        if st.button(
            "🗑️ Limpar análise",
            use_container_width=True,
            key="btn_limpar_lotes",
        ):
            limpar_estado_lotes()
            st.rerun()

    with col2:
        if st.button(
            "⬅️ Voltar ao Hub",
            use_container_width=True,
            key="btn_voltar_hub_lotes_resultado",
        ):
            st.session_state[
                "ferramenta_atual"
            ] = None

            limpar_resultado()

            st.rerun()
