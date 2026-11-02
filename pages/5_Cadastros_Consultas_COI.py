import json
import re

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Cadastros e Consultas COI",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("5")


# ============================================================
# AUTENTICAÇÃO
# ============================================================

from auth import verificar_autenticacao, pode_executar_acao


verificar_autenticacao()

if not st.session_state.get("autenticado"):
    st.warning("Sessão não iniciada ou expirada.")

    if st.button(
        "↩️ Ir para o Login",
        use_container_width=True,
        key="ir_login_cadastros",
    ):
        st.switch_page("app.py")

    st.stop()


SPREADSHEET_ID = "15iN3YEGyxk3l1ZKaHJJp-BvTfVHqpd7gL1GX3RbAKUU"


# ============================================================
# MODO ESCURO
# ============================================================

if "modo_escuro_cadastros" not in st.session_state:
    st.session_state.modo_escuro_cadastros = False

modo_escuro_cadastros = st.toggle(
    "🌙 Modo escuro",
    value=st.session_state.modo_escuro_cadastros,
    key="toggle_modo_escuro_cadastros",
)

st.session_state.modo_escuro_cadastros = modo_escuro_cadastros


if modo_escuro_cadastros:
    st.markdown(
        """
        <style>
            .stApp {
                background-color: #0e1117;
                color: #fafafa;
            }

            .stApp p,
            .stApp label,
            .stApp h1,
            .stApp h2,
            .stApp h3,
            .stApp h4,
            .stApp h5,
            .stApp h6 {
                color: #f0f0f0 !important;
            }

            [data-testid="stSidebar"] {
                background-color: #161b22;
            }

            .stButton > button,
            .stDownloadButton > button {
                background-color: #000000 !important;
                color: #ffffff !important;
                border: 1px solid #444c56 !important;
            }

            .stButton > button *,
            .stDownloadButton > button * {
                color: #ffffff !important;
            }

            .stButton > button:hover,
            .stDownloadButton > button:hover {
                background-color: #000000 !important;
                color: #ff0000 !important;
                border-color: #ff0000 !important;
            }

            .stButton > button:hover *,
            .stDownloadButton > button:hover * {
                color: #ff0000 !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# CONEXÃO COM GOOGLE SHEETS
# ============================================================

@st.cache_resource
def conectar_google_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials_dict = json.loads(st.secrets["gcp_json"])

    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=scopes,
    )

    gc = gspread.authorize(credentials)
    sh = gc.open_by_key(SPREADSHEET_ID)

    abas_configuradas = {
        "bairros_teresina": [
            "BAIRRO",
            "ZONA",
        ],
        "municipios_regionais": [
            "MUNICIPIO",
            "BASE",
            "REGIONAL",
            "ZONA",
        ],
        "pessoas_funcoes": [
            "NOME",
            "FUNCAO",
            "MUNICIPIO",
            "BASE",
            "REGIONAL",
            "TELEFONE",
            "EMAIL",
            "STATUS",
            "OBSERVACAO",
        ],
    }

    worksheets = {}

    for nome_aba, cabecalho in abas_configuradas.items():
        try:
            ws = sh.worksheet(nome_aba)
        except Exception:
            ws = sh.add_worksheet(
                title=nome_aba,
                rows="2000",
                cols=max(10, len(cabecalho) + 2),
            )

        try:
            valores = ws.get_all_values()

            if not valores:
                if pode_executar_acao("5", "editar"):
                    ws.append_row(cabecalho)
            else:
                cabecalho_atual = [
                    str(valor).strip()
                    for valor in valores[0]
                ]

                if cabecalho_atual != cabecalho:
                    if pode_executar_acao("5", "editar"):
                        ws.update(
                            "A1",
                            [cabecalho],
                        )
        except Exception:
            pass

        worksheets[nome_aba] = ws

    return worksheets


try:
    worksheets = conectar_google_sheets()
except Exception as e:
    st.error(
        f"❌ Erro ao conectar com o Google Sheets: {e}"
    )
    st.stop()


ws_bairros = worksheets["bairros_teresina"]
ws_municipios = worksheets["municipios_regionais"]
ws_pessoas = worksheets["pessoas_funcoes"]


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def normalizar_texto(valor):
    if valor is None:
        return ""

    texto = str(valor).strip().upper()

    substituicoes = {
        "Á": "A",
        "À": "A",
        "Ã": "A",
        "Â": "A",
        "Ä": "A",
        "É": "E",
        "È": "E",
        "Ê": "E",
        "Ë": "E",
        "Í": "I",
        "Ì": "I",
        "Î": "I",
        "Ï": "I",
        "Ó": "O",
        "Ò": "O",
        "Õ": "O",
        "Ô": "O",
        "Ö": "O",
        "Ú": "U",
        "Ù": "U",
        "Û": "U",
        "Ü": "U",
        "Ç": "C",
    }

    for origem, destino in substituicoes.items():
        texto = texto.replace(origem, destino)

    texto = re.sub(r"\s+", " ", texto)

    return texto


def limpar_dataframe(df):
    if df.empty:
        return df

    df = df.fillna("")

    for coluna in df.columns:
        df[coluna] = df[coluna].astype(str).str.strip()

    return df


def carregar_aba(ws, colunas):
    valores = ws.get_all_values()

    if not valores:
        return pd.DataFrame(columns=colunas)

    cabecalho = valores[0]

    if len(cabecalho) < len(colunas):
        cabecalho = colunas

    linhas = []

    for linha in valores[1:]:
        linha = list(linha)

        if len(linha) < len(colunas):
            linha += [""] * (len(colunas) - len(linha))

        linhas.append(linha[:len(colunas)])

    df = pd.DataFrame(
        linhas,
        columns=colunas,
    )

    return limpar_dataframe(df)


def salvar_dataframe_na_aba(ws, df, colunas):
    if not pode_executar_acao("5", "editar"):
        raise PermissionError("Usuário sem permissão para modificar cadastros.")
    ws.clear()

    valores = [colunas]

    if not df.empty:
        df_temp = df.copy()

        for coluna in colunas:
            if coluna not in df_temp.columns:
                df_temp[coluna] = ""

        df_temp = df_temp[colunas].fillna("")

        for coluna in colunas:
            df_temp[coluna] = (
                df_temp[coluna]
                .astype(str)
                .replace("nan", "")
            )

        valores.extend(
            df_temp.values.tolist()
        )

    ws.update(
        "A1",
        valores,
    )


def buscar_municipio(df_municipios, municipio):
    municipio_normalizado = normalizar_texto(municipio)

    if not municipio_normalizado:
        return None

    for _, linha in df_municipios.iterrows():
        if (
            normalizar_texto(linha["MUNICIPIO"])
            == municipio_normalizado
        ):
            return linha

    return None


def buscar_base(df_municipios, base):
    base_normalizada = normalizar_texto(base)

    if not base_normalizada:
        return None

    resultados = df_municipios[
        df_municipios["BASE"].apply(normalizar_texto)
        == base_normalizada
    ]

    if resultados.empty:
        return None

    return resultados.iloc[0]


def obter_regionais_da_base(df_municipios, base):
    base_normalizada = normalizar_texto(base)

    resultados = df_municipios[
        df_municipios["BASE"].apply(normalizar_texto)
        == base_normalizada
    ]

    if resultados.empty:
        return []

    regionais = []

    for regional in resultados["REGIONAL"].tolist():
        if str(regional).strip():
            if normalizar_texto(regional) not in [
                normalizar_texto(item)
                for item in regionais
            ]:
                regionais.append(str(regional).strip())

    return regionais


def listar_municipios_da_base(df_municipios, base):
    base_normalizada = normalizar_texto(base)

    resultados = df_municipios[
        df_municipios["BASE"].apply(normalizar_texto)
        == base_normalizada
    ]

    if resultados.empty:
        return []

    municipios = []

    for municipio in resultados["MUNICIPIO"].tolist():
        municipio = str(municipio).strip()

        if municipio and normalizar_texto(municipio) not in [
            normalizar_texto(item)
            for item in municipios
        ]:
            municipios.append(municipio)

    return sorted(municipios)


def obter_bases_da_pessoa(df_pessoas, nome):
    nome_normalizado = normalizar_texto(nome)

    registros = df_pessoas[
        df_pessoas["NOME"].apply(normalizar_texto)
        == nome_normalizado
    ]

    bases = []

    for base in registros["BASE"].tolist():
        base = str(base).strip()

        if base and normalizar_texto(base) not in [
            normalizar_texto(item)
            for item in bases
        ]:
            bases.append(base)

    return bases


# ============================================================
# CARREGAMENTO DOS DADOS
# ============================================================

df_bairros = carregar_aba(
    ws_bairros,
    ["BAIRRO", "ZONA"],
)

df_municipios = carregar_aba(
    ws_municipios,
    ["MUNICIPIO", "BASE", "REGIONAL", "ZONA"],
)

df_pessoas = carregar_aba(
    ws_pessoas,
    [
        "NOME",
        "FUNCAO",
        "MUNICIPIO",
        "BASE",
        "REGIONAL",
        "TELEFONE",
        "EMAIL",
        "STATUS",
        "OBSERVACAO",
    ],
)


# ============================================================
# CABEÇALHO
# ============================================================

st.title("📋 Cadastros e Consultas COI")

st.write(
    "Cadastros operacionais e referências utilizadas pela Plataforma COI."
)

st.divider()


# ============================================================
# NAVEGAÇÃO
# ============================================================

opcao = st.radio(
    "Selecione a área:",
    [
        "🏘️ Zona de Teresina",
        "🗺️ Municípios, Bases e Regionais",
        "👥 Pessoas e Funções",
    ],
    horizontal=True,
    key="menu_cadastros_coi",
)


# ============================================================
# ZONA DE TERESINA
# ============================================================

if opcao == "🏘️ Zona de Teresina":

    st.subheader("🏘️ Zona de Teresina")

    aba_consulta, aba_cadastro = st.tabs(
        [
            "🔎 Consultar",
            "✏️ Administrar",
        ]
    )

    with aba_consulta:

        if df_bairros.empty:
            st.info(
                "Nenhum bairro foi cadastrado ainda."
            )

        else:

            busca = st.text_input(
                "Pesquisar bairro",
                key="busca_bairro_teresina",
            )

            if busca.strip():

                termo = normalizar_texto(busca)

                df_exibicao = df_bairros[
                    df_bairros["BAIRRO"]
                    .apply(normalizar_texto)
                    .str.contains(
                        termo,
                        regex=False,
                    )
                ]

                if df_exibicao.empty:

                    st.info(
                        "Nenhum bairro encontrado para a consulta."
                    )

                else:

                    st.dataframe(
                        df_exibicao[
                            ["BAIRRO", "ZONA"]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

    with aba_cadastro:

        acao_bairro = st.radio(
            "Operação",
            [
                "Adicionar",
                "Editar",
                "Excluir",
            ],
            horizontal=True,
            key="acao_bairro",
        )

        if acao_bairro == "Adicionar":

            with st.form("form_adicionar_bairro"):

                bairro = st.text_input(
                    "Bairro"
                )

                zona = st.text_input(
                    "Zona"
                )

                salvar = st.form_submit_button(
                    "💾 Adicionar bairro",
                    use_container_width=True,
                )

                if salvar:

                    bairro_normalizado = normalizar_texto(
                        bairro
                    )

                    if not bairro_normalizado:
                        st.error(
                            "Informe o nome do bairro."
                        )

                    elif not str(zona).strip():
                        st.error(
                            "Informe a zona."
                        )

                    elif (
                        bairro_normalizado
                        in df_bairros["BAIRRO"]
                        .apply(normalizar_texto)
                        .tolist()
                    ):
                        st.error(
                            "Esse bairro já está cadastrado."
                        )

                    else:

                        df_novo = pd.concat(
                            [
                                df_bairros,
                                pd.DataFrame(
                                    [
                                        {
                                            "BAIRRO": bairro.strip(),
                                            "ZONA": str(zona).strip(),
                                        }
                                    ]
                                ),
                            ],
                            ignore_index=True,
                        )

                        salvar_dataframe_na_aba(
                            ws_bairros,
                            df_novo,
                            ["BAIRRO", "ZONA"],
                        )

                        st.success(
                            "Bairro cadastrado com sucesso."
                        )

                        st.rerun()

        elif acao_bairro == "Editar":

            if df_bairros.empty:
                st.info(
                    "Não há bairros cadastrados."
                )

            else:

                bairro_selecionado = st.selectbox(
                    "Bairro",
                    sorted(
                        df_bairros["BAIRRO"]
                        .dropna()
                        .astype(str)
                        .tolist()
                    ),
                    key="bairro_editar",
                )

                indice = df_bairros[
                    df_bairros["BAIRRO"]
                    == bairro_selecionado
                ].index[0]

                zona_atual = str(
                    df_bairros.loc[
                        indice,
                        "ZONA",
                    ]
                )

                with st.form("form_editar_bairro"):

                    novo_bairro = st.text_input(
                        "Bairro",
                        value=bairro_selecionado,
                    )

                    nova_zona = st.text_input(
                        "Zona",
                        value=zona_atual,
                    )

                    salvar = st.form_submit_button(
                        "💾 Salvar alterações",
                        use_container_width=True,
                    )

                    if salvar:

                        if not novo_bairro.strip():
                            st.error(
                                "Informe o nome do bairro."
                            )

                        elif not nova_zona.strip():
                            st.error(
                                "Informe a zona."
                            )

                        else:

                            duplicado = (
                                normalizar_texto(
                                    novo_bairro
                                )
                                != normalizar_texto(
                                    bairro_selecionado
                                )
                                and
                                normalizar_texto(
                                    novo_bairro
                                )
                                in df_bairros["BAIRRO"]
                                .apply(normalizar_texto)
                                .tolist()
                            )

                            if duplicado:
                                st.error(
                                    "Já existe outro cadastro com esse bairro."
                                )

                            else:

                                df_bairros.loc[
                                    indice,
                                    "BAIRRO",
                                ] = novo_bairro.strip()

                                df_bairros.loc[
                                    indice,
                                    "ZONA",
                                ] = nova_zona.strip()

                                salvar_dataframe_na_aba(
                                    ws_bairros,
                                    df_bairros,
                                    ["BAIRRO", "ZONA"],
                                )

                                st.success(
                                    "Bairro atualizado com sucesso."
                                )

                                st.rerun()

        else:

            if df_bairros.empty:
                st.info(
                    "Não há bairros cadastrados."
                )

            else:

                bairro_excluir = st.selectbox(
                    "Bairro",
                    sorted(
                        df_bairros["BAIRRO"]
                        .dropna()
                        .astype(str)
                        .tolist()
                    ),
                    key="bairro_excluir",
                )

                st.warning(
                    "A exclusão removerá o cadastro do bairro."
                )

                if st.button(
                    "🗑️ Excluir bairro",
                    type="primary",
                    use_container_width=True,
                    key="confirmar_exclusao_bairro",
                ):

                    df_bairros = df_bairros[
                        df_bairros["BAIRRO"]
                        != bairro_excluir
                    ].reset_index(drop=True)

                    salvar_dataframe_na_aba(
                        ws_bairros,
                        df_bairros,
                        ["BAIRRO", "ZONA"],
                    )

                    st.success(
                        "Bairro excluído com sucesso."
                    )

                    st.rerun()


# ============================================================
# MUNICÍPIOS / BASES / REGIONAIS
# ============================================================

elif opcao == "🗺️ Municípios, Bases e Regionais":

    st.subheader(
        "🗺️ Municípios, Bases, Regionais e Zonas"
    )

    aba_consulta, aba_cadastro = st.tabs(
        [
            "🔎 Consultar",
            "✏️ Administrar",
        ]
    )

    with aba_consulta:

        if df_municipios.empty:

            st.info(
                "Nenhum município foi cadastrado ainda."
            )

        else:

            busca_municipio = st.text_input(
                "Pesquisar município, base ou zona",
                key="busca_municipio",
                placeholder="Digite município, base ou zona",
            )

            if busca_municipio.strip():

                termo = normalizar_texto(
                    busca_municipio
                )

                # ====================================================
                # PESQUISA POR MUNICÍPIO
                # ====================================================

                encontrados_municipio = df_municipios[
                    df_municipios["MUNICIPIO"]
                    .apply(normalizar_texto)
                    .str.contains(
                        termo,
                        regex=False,
                    )
                ]

                if not encontrados_municipio.empty:

                    st.markdown(
                        "### 🏘️ Resultado por município"
                    )

                    st.dataframe(
                        encontrados_municipio[
                            [
                                "MUNICIPIO",
                                "BASE",
                                "REGIONAL",
                                "ZONA",
                            ]
                        ]
                        .drop_duplicates()
                        .sort_values(
                            [
                                "MUNICIPIO",
                                "BASE",
                                "REGIONAL",
                                "ZONA",
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

                else:

                    # ====================================================
                    # PESQUISA POR BASE
                    # ====================================================

                    encontrados_base = df_municipios[
                        df_municipios["BASE"]
                        .apply(normalizar_texto)
                        .str.contains(
                            termo,
                            regex=False,
                        )
                    ]

                    if not encontrados_base.empty:

                        st.markdown(
                            "### 🏢 Resultado por base"
                        )

                        st.dataframe(
                            encontrados_base[
                                [
                                    "MUNICIPIO",
                                    "BASE",
                                    "REGIONAL",
                                    "ZONA",
                                ]
                            ]
                            .drop_duplicates()
                            .sort_values(
                                [
                                    "BASE",
                                    "REGIONAL",
                                    "MUNICIPIO",
                                    "ZONA",
                                ]
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )

                    else:

                        # ====================================================
                        # PESQUISA POR ZONA
                        # ====================================================

                        encontrados_zona = df_municipios[
                            df_municipios["ZONA"]
                            .apply(normalizar_texto)
                            .str.contains(
                                termo,
                                regex=False,
                            )
                        ]

                        if not encontrados_zona.empty:

                            st.markdown(
                                "### 📍 Resultado por zona"
                            )

                            st.dataframe(
                                encontrados_zona[
                                    [
                                        "MUNICIPIO",
                                        "BASE",
                                        "REGIONAL",
                                        "ZONA",
                                    ]
                                ]
                                .drop_duplicates()
                                .sort_values(
                                    [
                                        "ZONA",
                                        "BASE",
                                        "REGIONAL",
                                        "MUNICIPIO",
                                    ]
                                ),
                                use_container_width=True,
                                hide_index=True,
                            )

                        else:

                            st.info(
                                "Nenhum município, base ou zona "
                                "encontrado para a consulta."
                            )

    with aba_cadastro:

        acao_municipio = st.radio(
            "Operação",
            [
                "Adicionar",
                "Editar",
                "Excluir",
            ],
            horizontal=True,
            key="acao_municipio",
        )

        if acao_municipio == "Adicionar":

            with st.form("form_adicionar_municipio"):

                municipio = st.text_input(
                    "Município"
                )

                base = st.text_input(
                    "Base"
                )

                regional = st.text_input(
                    "Regional"
                )

                zona = st.text_input(
                    "Zona"
                )

                salvar = st.form_submit_button(
                    "💾 Adicionar município",
                    use_container_width=True,
                )

                if salvar:

                    if not municipio.strip():
                        st.error(
                            "Informe o município."
                        )

                    elif not base.strip():
                        st.error(
                            "Informe a base."
                        )

                    elif not regional.strip():
                        st.error(
                            "Informe a regional."
                        )

                    elif (
                        normalizar_texto(municipio)
                        in df_municipios["MUNICIPIO"]
                        .apply(normalizar_texto)
                        .tolist()
                    ):
                        st.error(
                            "Esse município já está cadastrado."
                        )

                    else:

                        df_novo = pd.concat(
                            [
                                df_municipios,
                                pd.DataFrame(
                                    [
                                        {
                                            "MUNICIPIO": municipio.strip(),
                                            "BASE": base.strip(),
                                            "REGIONAL": regional.strip(),
                                            "ZONA": zona.strip(),
                                        }
                                    ]
                                ),
                            ],
                            ignore_index=True,
                        )

                        salvar_dataframe_na_aba(
                            ws_municipios,
                            df_novo,
                            [
                                "MUNICIPIO",
                                "BASE",
                                "REGIONAL",
                                "ZONA",
                            ],
                        )

                        st.success(
                            "Município cadastrado com sucesso."
                        )

                        st.rerun()

        elif acao_municipio == "Editar":

            if df_municipios.empty:

                st.info(
                    "Não há municípios cadastrados."
                )

            else:

                municipio_selecionado = st.selectbox(
                    "Município",
                    sorted(
                        df_municipios["MUNICIPIO"]
                        .dropna()
                        .astype(str)
                        .tolist()
                    ),
                    key="municipio_editar",
                )

                indice = df_municipios[
                    df_municipios["MUNICIPIO"]
                    == municipio_selecionado
                ].index[0]

                with st.form("form_editar_municipio"):

                    novo_municipio = st.text_input(
                        "Município",
                        value=str(
                            df_municipios.loc[
                                indice,
                                "MUNICIPIO",
                            ]
                        ),
                    )

                    nova_base = st.text_input(
                        "Base",
                        value=str(
                            df_municipios.loc[
                                indice,
                                "BASE",
                            ]
                        ),
                    )

                    nova_regional = st.text_input(
                        "Regional",
                        value=str(
                            df_municipios.loc[
                                indice,
                                "REGIONAL",
                            ]
                        ),
                    )

                    nova_zona = st.text_input(
                        "Zona",
                        value=str(
                            df_municipios.loc[
                                indice,
                                "ZONA",
                            ]
                        ),
                    )

                    salvar = st.form_submit_button(
                        "💾 Salvar alterações",
                        use_container_width=True,
                    )

                    if salvar:

                        if not novo_municipio.strip():
                            st.error(
                                "Informe o município."
                            )

                        elif not nova_base.strip():
                            st.error(
                                "Informe a base."
                            )

                        elif not nova_regional.strip():
                            st.error(
                                "Informe a regional."
                            )

                        else:

                            duplicado = (
                                normalizar_texto(
                                    novo_municipio
                                )
                                != normalizar_texto(
                                    municipio_selecionado
                                )
                                and
                                normalizar_texto(
                                    novo_municipio
                                )
                                in df_municipios["MUNICIPIO"]
                                .apply(normalizar_texto)
                                .tolist()
                            )

                            if duplicado:
                                st.error(
                                    "Já existe outro cadastro com esse município."
                                )

                            else:

                                df_municipios.loc[
                                    indice,
                                    "MUNICIPIO",
                                ] = novo_municipio.strip()

                                df_municipios.loc[
                                    indice,
                                    "BASE",
                                ] = nova_base.strip()

                                df_municipios.loc[
                                    indice,
                                    "REGIONAL",
                                ] = nova_regional.strip()

                                df_municipios.loc[
                                    indice,
                                    "ZONA",
                                ] = nova_zona.strip()

                                salvar_dataframe_na_aba(
                                    ws_municipios,
                                    df_municipios,
                                    [
                                        "MUNICIPIO",
                                        "BASE",
                                        "REGIONAL",
                                        "ZONA",
                                    ],
                                )

                                st.success(
                                    "Município atualizado com sucesso."
                                )

                                st.rerun()

        else:

            if df_municipios.empty:

                st.info(
                    "Não há municípios cadastrados."
                )

            else:

                municipio_excluir = st.selectbox(
                    "Município",
                    sorted(
                        df_municipios["MUNICIPIO"]
                        .dropna()
                        .astype(str)
                        .tolist()
                    ),
                    key="municipio_excluir",
                )

                st.warning(
                    "A exclusão removerá o cadastro do município."
                )

                if st.button(
                    "🗑️ Excluir município",
                    type="primary",
                    use_container_width=True,
                    key="confirmar_exclusao_municipio",
                ):

                    df_municipios = df_municipios[
                        df_municipios["MUNICIPIO"]
                        != municipio_excluir
                    ].reset_index(drop=True)

                    salvar_dataframe_na_aba(
                        ws_municipios,
                        df_municipios,
                        [
                            "MUNICIPIO",
                            "BASE",
                            "REGIONAL",
                            "ZONA",
                        ],
                    )

                    st.success(
                        "Município excluído com sucesso."
                    )

                    st.rerun()


# ============================================================
# PESSOAS E FUNÇÕES
# ============================================================

else:

    st.subheader("👥 Pessoas e Funções")

    aba_consulta, aba_cadastro = st.tabs(
        [
            "🔎 Consultar",
            "✏️ Administrar",
        ]
    )

    # ========================================================
    # CONSULTA DE PESSOAS
    # ========================================================

    with aba_consulta:

        if df_pessoas.empty:

            st.info(
                "Nenhuma pessoa foi cadastrada ainda."
            )

        else:

            busca_pessoa = st.text_input(
                "Pesquisar pessoa",
                key="busca_pessoa",
            )

            if busca_pessoa.strip():

                termo = normalizar_texto(
                    busca_pessoa
                )

                nomes_encontrados = sorted(
                    [
                        nome
                        for nome in df_pessoas["NOME"]
                        .dropna()
                        .astype(str)
                        .unique()
                        if nome.strip()
                        and termo in normalizar_texto(nome)
                    ]
                )

                if not nomes_encontrados:

                    st.info(
                        "Nenhuma pessoa encontrada para a consulta."
                    )

                else:

                    pessoa_selecionada = st.selectbox(
                        "Selecione a pessoa encontrada",
                        nomes_encontrados,
                        key="pessoa_consulta",
                    )

                    registros = df_pessoas[
                        df_pessoas["NOME"].apply(normalizar_texto)
                        == normalizar_texto(
                            pessoa_selecionada
                        )
                    ].copy()

                    primeira_linha = registros.iloc[0]

                    st.markdown(
                        f"### 👤 {pessoa_selecionada}"
                    )

                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.markdown(
                            f"**Função:** "
                            f"{primeira_linha['FUNCAO'] or '-'}"
                        )

                    with col2:
                        st.markdown(
                            f"**Telefone:** "
                            f"{primeira_linha['TELEFONE'] or '-'}"
                        )

                    with col3:
                        st.markdown(
                            f"**E-mail:** "
                            f"{primeira_linha['EMAIL'] or '-'}"
                        )

                    st.divider()

                    regionais = sorted(
                        [
                            str(regional).strip()
                            for regional in registros["REGIONAL"]
                            if str(regional).strip()
                        ]
                    )

                    regionais_unicas = []

                    for regional in regionais:
                        if normalizar_texto(
                            regional
                        ) not in [
                            normalizar_texto(item)
                            for item in regionais_unicas
                        ]:
                            regionais_unicas.append(
                                regional
                            )

                    if regionais_unicas:

                        st.markdown(
                            "**Regional:** "
                            + ", ".join(regionais_unicas)
                        )

                    st.markdown(
                        "#### 🗂️ Responsabilidades"
                    )

                    linhas_responsabilidades = []

                    for _, registro in registros.iterrows():

                        municipio = str(
                            registro["MUNICIPIO"]
                        ).strip()

                        base = str(
                            registro["BASE"]
                        ).strip()

                        regional = str(
                            registro["REGIONAL"]
                        ).strip()

                        if (
                            municipio
                            or base
                            or regional
                        ):
                            linhas_responsabilidades.append(
                                {
                                    "REGIONAL": regional,
                                    "BASE": base,
                                    "MUNICIPIO": municipio,
                                }
                            )

                    if linhas_responsabilidades:

                        df_responsabilidades = (
                            pd.DataFrame(
                                linhas_responsabilidades
                            )
                            .drop_duplicates()
                            .sort_values(
                                [
                                    "REGIONAL",
                                    "BASE",
                                    "MUNICIPIO",
                                ]
                            )
                        )

                        st.dataframe(
                            df_responsabilidades,
                            use_container_width=True,
                            hide_index=True,
                        )

                    else:

                        st.info(
                            "Nenhum vínculo operacional informado."
                        )

                    observacoes = [
                        str(valor).strip()
                        for valor in registros["OBSERVACAO"]
                        if str(valor).strip()
                    ]

                    if observacoes:

                        st.markdown(
                            "#### 📝 Observações"
                        )

                        observacoes_unicas = []

                        for observacao in observacoes:
                            if observacao not in observacoes_unicas:
                                observacoes_unicas.append(
                                    observacao
                                )

                        for observacao in observacoes_unicas:
                            st.write(
                                f"• {observacao}"
                            )

    # ========================================================
    # ADMINISTRAÇÃO DE PESSOAS
    # ========================================================

    with aba_cadastro:

        acao_pessoa = st.radio(
            "Operação",
            [
                "Adicionar vínculo",
                "Editar vínculo",
                "Excluir vínculo",
            ],
            horizontal=True,
            key="acao_pessoa",
        )

        # ====================================================
        # ADICIONAR
        # ====================================================

        if acao_pessoa == "Adicionar vínculo":

            with st.form("form_adicionar_pessoa"):

                nome = st.text_input(
                    "Nome"
                )

                funcao = st.text_input(
                    "Função"
                )

                st.markdown(
                    "**Responsabilidade operacional**"
                )

                municipio_opcao = st.selectbox(
                    "Município",
                    [
                        "Não informar município"
                    ]
                    + sorted(
                        [
                            municipio
                            for municipio in df_municipios[
                                "MUNICIPIO"
                            ]
                            .dropna()
                            .astype(str)
                            if municipio.strip()
                        ]
                    ),
                    key="municipio_pessoa_novo",
                )

                base_opcao = st.selectbox(
                    "Base",
                    [
                        "Não informar base"
                    ]
                    + sorted(
                        [
                            base
                            for base in df_municipios[
                                "BASE"
                            ]
                            .dropna()
                            .astype(str)
                            if base.strip()
                        ]
                        + [
                            base
                            for base in []
                        ]
                    ),
                    key="base_pessoa_novo",
                )

                regional_manual = st.text_input(
                    "Regional",
                    help=(
                        "Use este campo quando a pessoa for "
                        "de coordenação, gerência ou direção, "
                        "sem vínculo específico com município/base."
                    ),
                )

                telefone = st.text_input(
                    "Telefone"
                )

                email = st.text_input(
                    "E-mail"
                )

                status = st.selectbox(
                    "Status",
                    [
                        "Ativo",
                        "Inativo",
                    ],
                    key="status_pessoa_novo",
                )

                observacao = st.text_area(
                    "Observação"
                )

                salvar = st.form_submit_button(
                    "💾 Cadastrar pessoa",
                    use_container_width=True,
                )

                if salvar:

                    if not nome.strip():
                        st.error(
                            "Informe o nome."
                        )

                    elif not funcao.strip():
                        st.error(
                            "Informe a função."
                        )

                    else:

                        municipio = ""

                        if (
                            municipio_opcao
                            != "Não informar município"
                        ):
                            municipio = (
                                municipio_opcao
                            )

                        base = ""

                        if (
                            base_opcao
                            != "Não informar base"
                        ):
                            base = base_opcao

                        regional = (
                            regional_manual.strip()
                        )

                        # ------------------------------------
                        # MUNICÍPIO → BASE → REGIONAL
                        # ------------------------------------

                        if municipio:

                            registro_municipio = (
                                buscar_municipio(
                                    df_municipios,
                                    municipio,
                                )
                            )

                            if (
                                registro_municipio
                                is None
                            ):
                                st.error(
                                    "O município selecionado "
                                    "não possui cadastro válido."
                                )
                                st.stop()

                            base = str(
                                registro_municipio[
                                    "BASE"
                                ]
                            ).strip()

                            regional = str(
                                registro_municipio[
                                    "REGIONAL"
                                ]
                            ).strip()

                        # ------------------------------------
                        # BASE → REGIONAL
                        # ------------------------------------

                        elif base:

                            registro_base = (
                                buscar_base(
                                    df_municipios,
                                    base,
                                )
                            )

                            if (
                                registro_base
                                is None
                            ):
                                st.error(
                                    "A base informada não foi encontrada "
                                    "na aba municipios_regionais."
                                )
                                st.stop()

                            regionais = (
                                obter_regionais_da_base(
                                    df_municipios,
                                    base,
                                )
                            )

                            if len(regionais) == 1:
                                regional = (
                                    regionais[0]
                                )

                            elif len(regionais) > 1:
                                st.error(
                                    "Essa base está vinculada a mais "
                                    "de uma regional. Corrija a aba "
                                    "municipios_regionais antes de "
                                    "continuar."
                                )
                                st.stop()

                        # ------------------------------------
                        # VALIDAÇÃO DA REGIONAL
                        # ------------------------------------

                        if not regional:

                            st.error(
                                "Informe uma Regional ou selecione "
                                "um Município/Base que permita "
                                "identificá-la automaticamente."
                            )

                        else:

                            novo_registro = {
                                "NOME": nome.strip(),
                                "FUNCAO": funcao.strip(),
                                "MUNICIPIO": municipio,
                                "BASE": base,
                                "REGIONAL": regional,
                                "TELEFONE": telefone.strip(),
                                "EMAIL": email.strip(),
                                "STATUS": status,
                                "OBSERVACAO": observacao.strip(),
                            }

                            df_novo = pd.concat(
                                [
                                    df_pessoas,
                                    pd.DataFrame(
                                        [novo_registro]
                                    ),
                                ],
                                ignore_index=True,
                            )

                            salvar_dataframe_na_aba(
                                ws_pessoas,
                                df_novo,
                                [
                                    "NOME",
                                    "FUNCAO",
                                    "MUNICIPIO",
                                    "BASE",
                                    "REGIONAL",
                                    "TELEFONE",
                                    "EMAIL",
                                    "STATUS",
                                    "OBSERVACAO",
                                ],
                            )

                            st.success(
                                "Vínculo cadastrado com sucesso."
                            )

                            st.rerun()

        # ====================================================
        # EDITAR
        # ====================================================

        elif acao_pessoa == "Editar vínculo":

            if df_pessoas.empty:

                st.info(
                    "Nenhuma pessoa cadastrada."
                )

            else:

                df_pessoas["_INDICE"] = range(
                    len(df_pessoas)
                )

                opcoes = []

                for _, registro in df_pessoas.iterrows():

                    descricao = (
                        f"{registro['NOME']} | "
                        f"{registro['FUNCAO']} | "
                        f"{registro['MUNICIPIO'] or 'Sem município'} | "
                        f"{registro['BASE'] or 'Sem base'}"
                    )

                    opcoes.append(
                        (
                            int(registro["_INDICE"]),
                            descricao,
                        )
                    )

                descricao_selecionada = st.selectbox(
                    "Selecione o vínculo",
                    opcoes,
                    format_func=lambda item: item[1],
                    key="vinculo_editar",
                )

                indice = descricao_selecionada[0]

                registro = df_pessoas[
                    df_pessoas["_INDICE"]
                    == indice
                ].iloc[0]

                with st.form("form_editar_pessoa"):

                    novo_nome = st.text_input(
                        "Nome",
                        value=str(
                            registro["NOME"]
                        ),
                    )

                    nova_funcao = st.text_input(
                        "Função",
                        value=str(
                            registro["FUNCAO"]
                        ),
                    )

                    municipios_disponiveis = [
                        "Não informar município"
                    ] + sorted(
                        [
                            municipio
                            for municipio in df_municipios[
                                "MUNICIPIO"
                            ]
                            .dropna()
                            .astype(str)
                            if municipio.strip()
                        ]
                    )

                    municipio_atual = (
                        str(
                            registro["MUNICIPIO"]
                        ).strip()
                    )

                    municipio_default = (
                        municipio_atual
                        if municipio_atual
                        in municipios_disponiveis
                        else "Não informar município"
                    )

                    municipio_editado = st.selectbox(
                        "Município",
                        municipios_disponiveis,
                        index=municipios_disponiveis.index(
                            municipio_default
                        ),
                        key="municipio_pessoa_editar",
                    )

                    bases_disponiveis = [
                        "Não informar base"
                    ] + sorted(
                        [
                            base
                            for base in df_municipios[
                                "BASE"
                            ]
                            .dropna()
                            .astype(str)
                            if base.strip()
                        ]
                    )

                    base_atual = (
                        str(
                            registro["BASE"]
                        ).strip()
                    )

                    base_default = (
                        base_atual
                        if base_atual
                        in bases_disponiveis
                        else "Não informar base"
                    )

                    base_editada = st.selectbox(
                        "Base",
                        bases_disponiveis,
                        index=bases_disponiveis.index(
                            base_default
                        ),
                        key="base_pessoa_editar",
                    )

                    regional_editada = st.text_input(
                        "Regional",
                        value=str(
                            registro["REGIONAL"]
                        ),
                    )

                    telefone_editado = st.text_input(
                        "Telefone",
                        value=str(
                            registro["TELEFONE"]
                        ),
                    )

                    email_editado = st.text_input(
                        "E-mail",
                        value=str(
                            registro["EMAIL"]
                        ),
                    )

                    status_editado = st.selectbox(
                        "Status",
                        [
                            "Ativo",
                            "Inativo",
                        ],
                        index=(
                            0
                            if str(
                                registro["STATUS"]
                            ).strip()
                            != "Inativo"
                            else 1
                        ),
                        key="status_pessoa_editar",
                    )

                    observacao_editada = st.text_area(
                        "Observação",
                        value=str(
                            registro["OBSERVACAO"]
                        ),
                    )

                    salvar = st.form_submit_button(
                        "💾 Salvar alterações",
                        use_container_width=True,
                    )

                    if salvar:

                        if not novo_nome.strip():
                            st.error(
                                "Informe o nome."
                            )
                            st.stop()

                        if not nova_funcao.strip():
                            st.error(
                                "Informe a função."
                            )
                            st.stop()

                        municipio = ""

                        if (
                            municipio_editado
                            != "Não informar município"
                        ):
                            municipio = (
                                municipio_editado
                            )

                        base = ""

                        if (
                            base_editada
                            != "Não informar base"
                        ):
                            base = base_editada

                        regional = (
                            regional_editada.strip()
                        )

                        # --------------------------------
                        # MUNICÍPIO → BASE → REGIONAL
                        # --------------------------------

                        if municipio:

                            registro_municipio = (
                                buscar_municipio(
                                    df_municipios,
                                    municipio,
                                )
                            )

                            if (
                                registro_municipio
                                is None
                            ):
                                st.error(
                                    "Município não encontrado."
                                )
                                st.stop()

                            base = str(
                                registro_municipio[
                                    "BASE"
                                ]
                            ).strip()

                            regional = str(
                                registro_municipio[
                                    "REGIONAL"
                                ]
                            ).strip()

                        # --------------------------------
                        # BASE → REGIONAL
                        # --------------------------------

                        elif base:

                            regionais = (
                                obter_regionais_da_base(
                                    df_municipios,
                                    base,
                                )
                            )

                            if len(regionais) == 1:

                                regional = (
                                    regionais[0]
                                )

                            elif len(regionais) == 0:

                                st.error(
                                    "A base selecionada não possui "
                                    "regional cadastrada."
                                )
                                st.stop()

                            else:

                                st.error(
                                    "A base selecionada está associada "
                                    "a mais de uma regional."
                                )
                                st.stop()

                        if not regional:

                            st.error(
                                "Informe a regional."
                            )
                            st.stop()

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "NOME",
                        ] = novo_nome.strip()

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "FUNCAO",
                        ] = nova_funcao.strip()

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "MUNICIPIO",
                        ] = municipio

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "BASE",
                        ] = base

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "REGIONAL",
                        ] = regional

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "TELEFONE",
                        ] = telefone_editado.strip()

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "EMAIL",
                        ] = email_editado.strip()

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "STATUS",
                        ] = status_editado

                        df_pessoas.loc[
                            df_pessoas["_INDICE"]
                            == indice,
                            "OBSERVACAO",
                        ] = observacao_editada.strip()

                        df_pessoas = df_pessoas.drop(
                            columns=["_INDICE"]
                        )

                        salvar_dataframe_na_aba(
                            ws_pessoas,
                            df_pessoas,
                            [
                                "NOME",
                                "FUNCAO",
                                "MUNICIPIO",
                                "BASE",
                                "REGIONAL",
                                "TELEFONE",
                                "EMAIL",
                                "STATUS",
                                "OBSERVACAO",
                            ],
                        )

                        st.success(
                            "Vínculo atualizado com sucesso."
                        )

                        st.rerun()

        # ====================================================
        # EXCLUIR
        # ====================================================

        else:

            if df_pessoas.empty:

                st.info(
                    "Nenhuma pessoa cadastrada."
                )

            else:

                df_pessoas["_INDICE"] = range(
                    len(df_pessoas)
                )

                opcoes = []

                for _, registro in df_pessoas.iterrows():

                    descricao = (
                        f"{registro['NOME']} | "
                        f"{registro['FUNCAO']} | "
                        f"{registro['MUNICIPIO'] or 'Sem município'} | "
                        f"{registro['BASE'] or 'Sem base'}"
                    )

                    opcoes.append(
                        (
                            int(registro["_INDICE"]),
                            descricao,
                        )
                    )

                vinculo_excluir = st.selectbox(
                    "Selecione o vínculo",
                    opcoes,
                    format_func=lambda item: item[1],
                    key="vinculo_excluir",
                )

                indice = vinculo_excluir[0]

                st.warning(
                    "O vínculo selecionado será removido do cadastro."
                )

                if st.button(
                    "🗑️ Excluir vínculo",
                    type="primary",
                    use_container_width=True,
                    key="confirmar_exclusao_pessoa",
                ):

                    df_pessoas = df_pessoas[
                        df_pessoas["_INDICE"]
                        != indice
                    ].drop(
                        columns=["_INDICE"]
                    ).reset_index(
                        drop=True
                    )

                    salvar_dataframe_na_aba(
                        ws_pessoas,
                        df_pessoas,
                        [
                            "NOME",
                            "FUNCAO",
                            "MUNICIPIO",
                            "BASE",
                            "REGIONAL",
                            "TELEFONE",
                            "EMAIL",
                            "STATUS",
                            "OBSERVACAO",
                        ],
                    )

                    st.success(
                        "Vínculo excluído com sucesso."
                    )

                    st.rerun()


# ============================================================
# RODAPÉ
# ============================================================

st.divider()

if st.button(
    "↩️ Voltar ao Hub Central",
    use_container_width=True,
    key="voltar_hub_cadastros",
):
    st.switch_page("app.py")
