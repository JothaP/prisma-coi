import io
import re

import pandas as pd
import streamlit as st


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def _normalizar_coluna(nome):
    return re.sub(r"\s+", " ", str(nome).strip()).casefold()


def _ler_excel(arquivo):
    nome = arquivo.name.lower()

    if nome.endswith(".xlsx"):
        return pd.read_excel(arquivo, engine="openpyxl")

    if nome.endswith(".xls"):
        try:
            return pd.read_excel(arquivo, engine="xlrd")
        except ImportError as exc:
            raise ValueError(
                "Arquivos .xls exigem a dependência 'xlrd'. "
                "Adicione 'xlrd' ao requirements.txt e faça o novo deploy."
            ) from exc

    raise ValueError(f"Formato não suportado: {arquivo.name}")


def _estrutura_dataframe(df):
    return [_normalizar_coluna(coluna) for coluna in df.columns]


def _montar_excel(df):
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(
            writer,
            index=False,
            sheet_name="Dados",
        )

    buffer.seek(0)
    return buffer.getvalue()


def _valor_separacao(valor):
    if pd.isna(valor):
        return ""

    texto = str(valor).strip()
    texto = re.sub(r"\s+", " ", texto)

    return texto.casefold()


def _nome_arquivo_seguro(valor):
    valor = str(valor).strip()

    if not valor:
        valor = "Vazios"

    valor = re.sub(r'[\\/:*?"<>|]+', "_", valor)
    valor = re.sub(r"\s+", " ", valor)

    return valor[:120]


# ============================================================
# 6.1.1 — JUNTAR EXCEL
# ============================================================

def render_juntar_excel():
    st.markdown("### Juntar Excel")

    st.caption(
        "Junte dois ou mais arquivos Excel em uma única planilha."
    )

    arquivos = st.file_uploader(
        "Selecione os arquivos Excel",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key="juntar_excel_arquivos",
    )

    if not arquivos:
        st.info("Selecione dois ou mais arquivos para começar.")
        return

    if len(arquivos) < 2:
        st.warning("Selecione pelo menos dois arquivos.")
        return

    dados = []

    try:
        for arquivo in arquivos:
            arquivo.seek(0)
            df = _ler_excel(arquivo)

            dados.append(
                {
                    "nome": arquivo.name,
                    "df": df,
                    "estrutura": _estrutura_dataframe(df),
                }
            )

    except Exception as exc:
        st.error(f"Não foi possível ler os arquivos: {exc}")
        return

    estruturas = [item["estrutura"] for item in dados]

    mesma_estrutura = all(
        estrutura == estruturas[0]
        for estrutura in estruturas
    )

    st.markdown("#### Resumo dos arquivos")

    total_registros = sum(
        len(item["df"])
        for item in dados
    )

    total_colunas = len(
        set(
            coluna
            for item in dados
            for coluna in item["df"].columns
        )
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Arquivos", len(dados))

    with col2:
        st.metric("Registros", f"{total_registros:,}")

    with col3:
        st.metric("Colunas", f"{total_colunas:,}")

    if mesma_estrutura:
        st.success(
            "Todos os arquivos possuem a mesma estrutura."
        )

        df_final = pd.concat(
            [item["df"] for item in dados],
            ignore_index=True,
        )

    else:
        st.warning(
            "Os arquivos possuem estruturas diferentes."
        )

        st.markdown("#### Diferenças encontradas")

        todas_colunas = []

        for item in dados:
            todas_colunas.extend(
                list(item["df"].columns)
            )

        todas_colunas_normalizadas = {}

        for coluna in todas_colunas:
            todas_colunas_normalizadas[
                _normalizar_coluna(coluna)
            ] = coluna

        st.write(
            list(todas_colunas_normalizadas.values())
        )

        opcao = st.radio(
            "Como deseja proceder?",
            [
                "A — Juntar mesmo assim, preenchendo colunas ausentes",
                "B — Bloquear a operação",
            ],
            key="juntar_excel_opcao_estrutura",
        )

        if opcao.startswith("B"):
            st.info(
                "A operação foi bloqueada porque as estruturas são diferentes."
            )
            return

        df_final = pd.concat(
            [item["df"] for item in dados],
            ignore_index=True,
            sort=False,
        )

    try:
        dados_saida = _montar_excel(df_final)

    except Exception as exc:
        st.error(
            f"Não foi possível gerar o arquivo consolidado: {exc}"
        )
        return

    st.success(
        f"Arquivo consolidado pronto: "
        f"**{len(df_final):,} registros**."
    )

    st.download_button(
        "📥 Baixar Excel Consolidado",
        data=dados_saida,
        file_name="Excel_Consolidado.xlsx",
        mime=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        use_container_width=True,
        key="download_juntar_excel",
    )


# ============================================================
# 6.1.2 — VISUALIZAR EXCEL
# ============================================================

def render_visualizar_excel():
    st.markdown("### Visualizar Excel")

    st.caption(
        "Abra e consulte um arquivo Excel sem alterar o original."
    )

    arquivo = st.file_uploader(
        "Selecione o arquivo Excel",
        type=["xlsx", "xls"],
        accept_multiple_files=False,
        key="visualizar_excel_arquivo",
    )

    if arquivo is None:
        st.info("Selecione um arquivo para começar.")
        return

    try:
        arquivo.seek(0)
        df = _ler_excel(arquivo)

    except Exception as exc:
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    if df.empty:
        st.warning("O arquivo não possui registros.")
        return

    st.success(
        f"Arquivo carregado: **{arquivo.name}**"
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Registros",
            f"{len(df):,}",
        )

    with col2:
        st.metric(
            "Colunas",
            f"{len(df.columns):,}",
        )

    with col3:
        st.metric(
            "Células",
            f"{len(df) * len(df.columns):,}",
        )

    st.markdown("#### 🔎 Pesquisa")

    termo = st.text_input(
        "Pesquisar em todas as colunas",
        key="visualizar_excel_pesquisa",
    )

    df_filtrado = df.copy()

    if termo.strip():
        termo_normalizado = termo.strip().casefold()

        mascara = pd.Series(
            False,
            index=df_filtrado.index,
        )

        for coluna in df_filtrado.columns:
            mascara = mascara | (
                df_filtrado[coluna]
                .astype(str)
                .str.strip()
                .str.casefold()
                .str.contains(
                    termo_normalizado,
                    regex=False,
                    na=False,
                )
            )

        df_filtrado = df_filtrado[mascara]

    st.markdown("#### 🔽 Filtro por coluna")

    coluna_filtro = st.selectbox(
        "Coluna",
        ["— Nenhum filtro —"] + list(df.columns),
        key="visualizar_excel_coluna_filtro",
    )

    if coluna_filtro != "— Nenhum filtro —":
        valores = (
            df_filtrado[coluna_filtro]
            .dropna()
            .astype(str)
            .drop_duplicates()
            .sort_values()
            .tolist()
        )

        valor_filtro = st.selectbox(
            "Valor",
            ["— Todos —"] + valores,
            key="visualizar_excel_valor_filtro",
        )

        if valor_filtro != "— Todos —":
            df_filtrado = df_filtrado[
                df_filtrado[coluna_filtro]
                .astype(str)
                == valor_filtro
            ]

    st.markdown("#### ↕️ Ordenação")

    col_ord1, col_ord2 = st.columns(2)

    with col_ord1:
        coluna_ordem = st.selectbox(
            "Ordenar por",
            ["— Sem ordenação —"] + list(df.columns),
            key="visualizar_excel_coluna_ordem",
        )

    with col_ord2:
        ordem = st.radio(
            "Ordem",
            ["Crescente", "Decrescente"],
            horizontal=True,
            key="visualizar_excel_ordem",
        )

    if coluna_ordem != "— Sem ordenação —":
        df_filtrado = df_filtrado.sort_values(
            by=coluna_ordem,
            ascending=(ordem == "Crescente"),
            na_position="last",
        )

    max_linhas = st.number_input(
        "Máximo de linhas exibidas",
        min_value=10,
        max_value=10000,
        value=1000,
        step=100,
        key="visualizar_excel_max_linhas",
    )

    st.markdown("#### Resultado")

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "Registros encontrados",
            f"{len(df_filtrado):,}",
        )

    with col2:
        st.metric(
            "Registros exibidos",
            f"{min(len(df_filtrado), max_linhas):,}",
        )

    st.dataframe(
        df_filtrado.head(max_linhas),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# 6.1.3 — COMPARAR BASES
# ============================================================

def render_comparar_bases():
    st.markdown("### Comparar Bases")

    st.caption(
        "Compare duas bases e identifique registros exclusivos "
        "ou presentes nas duas."
    )

    col1, col2 = st.columns(2)

    with col1:
        arquivo_1 = st.file_uploader(
            "Base 1",
            type=["xlsx", "xls"],
            key="comparar_bases_arquivo_1",
        )

    with col2:
        arquivo_2 = st.file_uploader(
            "Base 2",
            type=["xlsx", "xls"],
            key="comparar_bases_arquivo_2",
        )

    if arquivo_1 is None or arquivo_2 is None:
        st.info("Selecione as duas bases para começar.")
        return

    try:
        arquivo_1.seek(0)
        df1 = _ler_excel(arquivo_1)

        arquivo_2.seek(0)
        df2 = _ler_excel(arquivo_2)

    except Exception as exc:
        st.error(f"Não foi possível ler as bases: {exc}")
        return

    st.markdown("#### Resumo")

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "Registros — Base 1",
            f"{len(df1):,}",
        )

    with col2:
        st.metric(
            "Registros — Base 2",
            f"{len(df2):,}",
        )

    mapa_colunas_1 = {
        _normalizar_coluna(coluna): coluna
        for coluna in df1.columns
    }

    mapa_colunas_2 = {
        _normalizar_coluna(coluna): coluna
        for coluna in df2.columns
    }

    comuns_normalizados = sorted(
        set(mapa_colunas_1)
        & set(mapa_colunas_2)
    )

    if not comuns_normalizados:
        st.error(
            "As bases não possuem colunas em comum."
        )
        return

    colunas_comuns = [
        mapa_colunas_1[coluna]
        for coluna in comuns_normalizados
    ]

    colunas_selecionadas = st.multiselect(
        "Selecione as colunas que identificam o registro",
        options=colunas_comuns,
        key="comparar_bases_colunas_chave",
    )

    if not colunas_selecionadas:
        st.info(
            "Selecione pelo menos uma coluna para realizar a comparação."
        )
        return

    def normalizar_valor(valor):
        if pd.isna(valor):
            return ""

        texto = str(valor).strip()
        texto = re.sub(r"\s+", " ", texto)

        return texto.casefold()

    def criar_chave(df, colunas):
        partes = []

        for coluna in colunas:
            partes.append(
                df[coluna]
                .map(normalizar_valor)
            )

        if len(partes) == 1:
            return partes[0]

        resultado = partes[0]

        for parte in partes[1:]:
            resultado = resultado + "||" + parte

        return resultado

    chave1 = criar_chave(
        df1,
        colunas_selecionadas,
    )

    chave2 = criar_chave(
        df2,
        colunas_selecionadas,
    )

    conjunto1 = set(chave1)
    conjunto2 = set(chave2)

    somente_1 = conjunto1 - conjunto2
    somente_2 = conjunto2 - conjunto1
    ambas = conjunto1 & conjunto2

    df_somente_1 = df1[
        chave1.isin(somente_1)
    ].copy()

    df_somente_2 = df2[
        chave2.isin(somente_2)
    ].copy()

    df_ambas_1 = df1[
        chave1.isin(ambas)
    ].copy()

    st.markdown("#### Resultado da comparação")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric(
            "Somente Base 1",
            f"{len(df_somente_1):,}",
        )

    with c2:
        st.metric(
            "Somente Base 2",
            f"{len(df_somente_2):,}",
        )

    with c3:
        st.metric(
            "Em ambas",
            f"{len(df_ambas_1):,}",
        )

    buffer = io.BytesIO()

    with pd.ExcelWriter(
        buffer,
        engine="openpyxl",
    ) as writer:
        df_somente_1.to_excel(
            writer,
            index=False,
            sheet_name="Somente_Base_1",
        )

        df_somente_2.to_excel(
            writer,
            index=False,
            sheet_name="Somente_Base_2",
        )

        df_ambas_1.to_excel(
            writer,
            index=False,
            sheet_name="Em_Ambas",
        )

    buffer.seek(0)

    st.download_button(
        "📥 Baixar Comparação",
        data=buffer.getvalue(),
        file_name="Comparacao_Bases.xlsx",
        mime=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        use_container_width=True,
        key="download_comparar_bases",
    )


# ============================================================
# 6.1.4 — REMOVER DUPLICIDADES
# ============================================================

def render_remover_duplicidades():
    st.markdown("### Remover Duplicidades")

    st.caption(
        "Remova registros duplicados com base em uma ou mais colunas."
    )

    arquivo = st.file_uploader(
        "Selecione o arquivo Excel",
        type=["xlsx", "xls"],
        key="remover_duplicidades_arquivo",
    )

    if arquivo is None:
        st.info("Selecione um arquivo para começar.")
        return

    try:
        arquivo.seek(0)
        df = _ler_excel(arquivo)

    except Exception as exc:
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    if df.empty:
        st.warning("O arquivo não possui registros.")
        return

    colunas = st.multiselect(
        "Selecione as colunas usadas para identificar duplicidades",
        options=list(df.columns),
        key="remover_duplicidades_colunas",
    )

    if not colunas:
        st.info(
            "Selecione pelo menos uma coluna."
        )
        return

    manter = st.radio(
        "Em caso de duplicidade, manter:",
        [
            "Primeira ocorrência",
            "Última ocorrência",
        ],
        horizontal=True,
        key="remover_duplicidades_manter",
    )

    df_normalizado = df.copy()

    for coluna in colunas:
        df_normalizado[coluna] = (
            df_normalizado[coluna]
            .map(_valor_separacao)
        )

    duplicados = df_normalizado.duplicated(
        subset=colunas,
        keep=False,
    )

    grupos_duplicados = (
        df_normalizado.loc[
            duplicados,
            colunas,
        ]
        .drop_duplicates()
        .shape[0]
    )

    registros_duplicados = int(
        duplicados.sum()
    )

    st.markdown("#### Resumo")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric(
            "Registros originais",
            f"{len(df):,}",
        )

    with c2:
        st.metric(
            "Registros em grupos duplicados",
            f"{registros_duplicados:,}",
        )

    with c3:
        st.metric(
            "Grupos duplicados",
            f"{grupos_duplicados:,}",
        )

    if manter == "Primeira ocorrência":
        keep = "first"
    else:
        keep = "last"

    df_resultado = df.loc[
        ~df_normalizado.duplicated(
            subset=colunas,
            keep=keep,
        )
    ].copy()

    st.success(
        f"Resultado: **{len(df_resultado):,} registros**."
    )

    dados_saida = _montar_excel(df_resultado)

    st.download_button(
        "📥 Baixar Excel sem Duplicidades",
        data=dados_saida,
        file_name="Excel_Sem_Duplicidades.xlsx",
        mime=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        use_container_width=True,
        key="download_remover_duplicidades",
    )


# ============================================================
# 6.1.5 — SEPARAR EXCEL
# ============================================================

def render_separar_excel():
    st.markdown("### Separar Excel")

    st.caption(
        "Separe uma planilha em vários arquivos com base nos "
        "valores de uma coluna."
    )

    arquivo = st.file_uploader(
        "Selecione o arquivo Excel",
        type=["xlsx", "xls"],
        accept_multiple_files=False,
        key="separar_excel_arquivo",
    )

    if arquivo is None:
        st.info("Selecione um arquivo para começar.")
        return

    try:
        arquivo.seek(0)
        df = _ler_excel(arquivo)

    except Exception as exc:
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    if df.empty:
        st.warning("O arquivo não possui registros.")
        return

    coluna = st.selectbox(
        "Selecione a coluna usada para separar",
        options=list(df.columns),
        key="separar_excel_coluna",
    )

    df_aux = df.copy()

    df_aux["_valor_separacao"] = (
        df_aux[coluna]
        .map(_valor_separacao)
    )

    valores_unicos = (
        df_aux["_valor_separacao"]
        .drop_duplicates()
        .tolist()
    )

    valores_exibicao = []

    mapa_valores = {}

    for valor_normalizado in valores_unicos:
        if valor_normalizado == "":
            exibicao = "Vazios"
        else:
            valores_originais = df.loc[
                df_aux["_valor_separacao"]
                == valor_normalizado,
                coluna,
            ]

            if valores_originais.empty:
                exibicao = valor_normalizado
            else:
                exibicao = str(
                    valores_originais.iloc[0]
                ).strip()

        valores_exibicao.append(exibicao)
        mapa_valores[exibicao] = valor_normalizado

    valores_exibicao = sorted(
        valores_exibicao,
        key=lambda valor: str(valor).casefold(),
    )

    selecionados = st.multiselect(
        "Selecione os valores que deseja gerar",
        options=valores_exibicao,
        key="separar_excel_valores",
    )

    if not selecionados:
        st.info(
            "Selecione pelo menos um valor."
        )
        return

    st.markdown("#### Resumo da separação")

    resumo = []

    for valor in selecionados:
        valor_normalizado = mapa_valores[valor]

        quantidade = int(
            (
                df_aux["_valor_separacao"]
                == valor_normalizado
            ).sum()
        )

        resumo.append(
            {
                "Valor": valor,
                "Registros": quantidade,
            }
        )

    st.dataframe(
        pd.DataFrame(resumo),
        use_container_width=True,
        hide_index=True,
    )

    if st.button(
        "✂️ Gerar arquivos separados",
        type="primary",
        use_container_width=True,
        key="executar_separar_excel",
    ):
        resultados = []

        for valor in selecionados:
            valor_normalizado = mapa_valores[valor]

            df_saida = df.loc[
                df_aux["_valor_separacao"]
                == valor_normalizado
            ].copy()

            nome_valor = _nome_arquivo_seguro(
                valor
            )

            dados = _montar_excel(df_saida)

            resultados.append(
                {
                    "nome": f"Separado_{nome_valor}.xlsx",
                    "dados": dados,
                    "quantidade": len(df_saida),
                }
            )

        st.session_state[
            "separar_excel_resultados"
        ] = resultados

    resultados = st.session_state.get(
        "separar_excel_resultados"
    )

    if not resultados:
        return

    st.success(
        f"{len(resultados)} arquivo(s) gerado(s)."
    )

    for indice, resultado in enumerate(
        resultados
    ):
        st.download_button(
            f"📥 Baixar {resultado['nome']} "
            f"({resultado['quantidade']:,} registros)",
            data=resultado["dados"],
            file_name=resultado["nome"],
            mime=(
                "application/vnd.openxmlformats-"
                "officedocument.spreadsheetml.sheet"
            ),
            use_container_width=True,
            key=f"download_separar_excel_{indice}",
        )


# ============================================================
# 6.1.6 — EXPORTAR / CONVERTER EXCEL
# ============================================================

def render_exportar_excel():
    st.markdown("### Exportar / Converter Excel")

    st.caption(
        "Converta arquivos entre Excel e CSV sem alterar "
        "o arquivo original."
    )

    arquivo = st.file_uploader(
        "Selecione o arquivo",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=False,
        key="exportar_excel_arquivo",
    )

    if arquivo is None:
        st.info("Selecione um arquivo para começar.")
        return

    extensao = (
        arquivo.name
        .lower()
        .rsplit(".", 1)[-1]
    )

    # --------------------------------------------------------
    # LEITURA DO ARQUIVO
    # --------------------------------------------------------

    try:
        arquivo.seek(0)

        if extensao == "csv":

            df = pd.read_csv(arquivo)

            abas_disponiveis = []

        elif extensao == "xlsx":

            excel = pd.ExcelFile(
                arquivo,
                engine="openpyxl",
            )

            abas_disponiveis = excel.sheet_names

            if len(abas_disponiveis) > 1:

                aba = st.selectbox(
                    "Selecione a planilha",
                    abas_disponiveis,
                    key="exportar_excel_aba",
                )

            else:

                aba = abas_disponiveis[0]

            arquivo.seek(0)

            df = pd.read_excel(
                arquivo,
                sheet_name=aba,
                engine="openpyxl",
            )

        elif extensao == "xls":

            try:

                excel = pd.ExcelFile(
                    arquivo,
                    engine="xlrd",
                )

            except ImportError as exc:

                st.error(
                    "Arquivos .xls exigem a dependência "
                    "'xlrd'. Adicione 'xlrd' ao "
                    "requirements.txt e faça um novo deploy."
                )

                return

            abas_disponiveis = excel.sheet_names

            if len(abas_disponiveis) > 1:

                aba = st.selectbox(
                    "Selecione a planilha",
                    abas_disponiveis,
                    key="exportar_excel_aba",
                )

            else:

                aba = abas_disponiveis[0]

            arquivo.seek(0)

            df = pd.read_excel(
                arquivo,
                sheet_name=aba,
                engine="xlrd",
            )

        else:

            st.error("Formato não suportado.")
            return

    except Exception as exc:

        st.error(
            f"Não foi possível ler o arquivo: {exc}"
        )

        return

    # --------------------------------------------------------
    # INFORMAÇÕES DO ARQUIVO
    # --------------------------------------------------------

    if df.empty:

        st.warning(
            "O arquivo não possui registros."
        )

        return

    st.success(
        f"Arquivo carregado: **{arquivo.name}** — "
        f"{len(df):,} registros e "
        f"{len(df.columns):,} colunas."
    )

    # --------------------------------------------------------
    # FORMATOS DE SAÍDA
    # --------------------------------------------------------

    st.markdown("#### 📤 Formato de saída")

    if extensao == "csv":

        # CSV pode ser convertido para Excel
        formatos_saida = [
            "Excel (.xlsx)",
        ]

    elif extensao in ["xlsx", "xls"]:

        # Excel pode ser convertido para CSV
        # ou normalizado para XLSX
        formatos_saida = [
            "CSV (.csv)",
            "Excel (.xlsx)",
        ]

    else:

        formatos_saida = []

    formato_saida = st.radio(
        "Escolha o formato",
        options=formatos_saida,
        horizontal=True,
        key="exportar_excel_formato_saida",
    )

    # --------------------------------------------------------
    # CONVERSÃO
    # --------------------------------------------------------

    if st.button(
        "📤 Converter arquivo",
        type="primary",
        use_container_width=True,
        key="executar_exportar_excel",
    ):

        try:

            # ==================================================
            # EXCEL → CSV
            # ==================================================

            if formato_saida == "CSV (.csv)":

                buffer = io.StringIO()

                df.to_csv(
                    buffer,
                    index=False,
                    encoding="utf-8-sig",
                )

                dados = buffer.getvalue().encode(
                    "utf-8-sig"
                )

                nome_saida = (
                    arquivo.name
                    .rsplit(".", 1)[0]
                    + ".csv"
                )

                mime = "text/csv"

            # ==================================================
            # CSV → EXCEL
            # ==================================================

            elif formato_saida == "Excel (.xlsx)":

                dados = _montar_excel(df)

                nome_saida = (
                    arquivo.name
                    .rsplit(".", 1)[0]
                    + ".xlsx"
                )

                mime = (
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                )

            else:

                st.error(
                    "Formato de saída inválido."
                )

                return

            # --------------------------------------------------
            # ARMAZENAR RESULTADO
            # --------------------------------------------------

            st.session_state[
                "exportar_excel_resultado"
            ] = {
                "dados": dados,
                "nome": nome_saida,
                "mime": mime,
            }

        except Exception as exc:

            st.error(
                f"Não foi possível converter o arquivo: {exc}"
            )

            return

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    resultado = st.session_state.get(
        "exportar_excel_resultado"
    )

    if resultado is None:
        return

    st.success(
        f"✅ Conversão concluída: "
        f"**{resultado['nome']}**"
    )

    st.download_button(
        "📥 Baixar arquivo convertido",
        data=resultado["dados"],
        file_name=resultado["nome"],
        mime=resultado["mime"],
        use_container_width=True,
        key="download_exportar_excel",
    )
