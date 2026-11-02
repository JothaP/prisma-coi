import io
import os
import re
import textwrap
import unicodedata
import zipfile
from datetime import timedelta

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import streamlit as st

from PIL import Image


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Cards Operacionais - COI",
    page_icon="🃏",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("4")


# ============================================================
# AUTENTICAÇÃO
# ============================================================

from auth import verificar_autenticacao


if not verificar_autenticacao():
    st.warning("Sessão não iniciada ou expirada.")

    if st.button(
        "Ir para o Login",
        use_container_width=True,
    ):
        st.switch_page("app.py")

    st.stop()

# ============================================================
# ESTILO
# ============================================================

st.markdown(
    """
    <style>

        [data-testid="stSidebarNav"] {
            display: none !important;
        }

        .main-title {
            font-size: 30px;
            font-weight: 700;
            color: #0F172A;
            margin-bottom: 2px;
        }

        .main-subtitle {
            font-size: 15px;
            color: #64748B;
            margin-bottom: 20px;
        }

        .section-title {
            font-size: 18px;
            font-weight: 700;
            color: #0F172A;
            margin-top: 8px;
            margin-bottom: 4px;
        }

        .section-description {
            color: #64748B;
            font-size: 13px;
            margin-bottom: 14px;
        }

        .config-box {
            background-color: #F8FAFC;
            border: 1px solid #CBD5E1;
            border-radius: 10px;
            padding: 18px;
            margin-bottom: 18px;
        }

        .preview-box {
            background-color: #F4F7FB;
            border: 1px solid #CBD5E1;
            border-radius: 10px;
            padding: 18px;
        }

        .info-box {
            background-color: #F0F4FF;
            border-left: 4px solid #0027BC;
            border-radius: 6px;
            padding: 12px 15px;
            margin: 10px 0 18px 0;
            color: #334155;
        }

        .stDownloadButton > button {
            width: 100%;
        }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("### 🃏 Cards Operacionais")

    st.caption(
        f"Usuário: **{st.session_state.get('usuario_logado', '')}**"
    )

    st.caption(
        f"Perfil: **{st.session_state.get('perfil', '').upper()}**"
    )

    st.divider()

    if st.button(
        "🛠️ Voltar para Ferramentas Operacionais",
        use_container_width=True,
        key="voltar_ferramentas_operacionais",
    ):
        st.switch_page(
            "pages/4_Ferramentas_Operacionais.py"
        )

    if st.button(
        "🏠 Voltar ao Menu Principal",
        use_container_width=True,
        key="voltar_menu_principal",
    ):
        st.switch_page("app.py")

    st.divider()

    # ========================================================
    # APARÊNCIA
    # ========================================================

    st.markdown("### Aparência")

    modo_noturno = st.toggle(
        "🌙 Modo noturno",
        value=st.session_state.get("modo_noturno_cards", False),
        key="modo_noturno_cards",
    )

    st.divider()

st.caption("Disponível para usuários autenticados.")

# ============================================================
# PALETA
# ============================================================

C_PRIMARY = "#0027BC"
C_PRIMARY_LIGHT = "#F0F4FF"
C_INFO = "#F59E0B"
C_ALERT = "#D32F2F"
C_SUCCESS = "#059669"
C_TEXT = "#0F172A"
C_MUTED = "#64748B"
C_OBS_DARK = "#334155"
C_BORDER = "#CBD5E1"
C_BG = "#F4F7FB"
C_CARD = "#FFFFFF"


# ============================================================
# IDENTIDADE VISUAL / LOGOS DAS UNIDADES
# ============================================================

LOGOS_UNIDADES = {
    "Águas de Teresina": "logo_aguas_de_teresina.png",
    "Águas do Piauí": "logo_aguas_do_piaui.png",
    "Águas de Timon": "logo_aguas_de_timon.png",
}


def carregar_logo_unidade(nome_unidade):
    """
    Localiza e carrega automaticamente a logo da unidade
    a partir dos arquivos presentes no repositório.

    São aceitas as seguintes estruturas:

        pages/
            4_3_Cards_Operacionais.py
            logo_aguas_de_teresina.png

    ou:

        pages/
            4_3_Cards_Operacionais.py
            logos/
                logo_aguas_de_teresina.png

    ou:

        assets/
            logo_aguas_de_teresina.png

    ou:

        assets/
            logos/
                logo_aguas_de_teresina.png
    """

    nome_arquivo = LOGOS_UNIDADES.get(
        nome_unidade
    )

    if not nome_arquivo:
        return None

    diretorio_modulo = os.path.dirname(
        os.path.abspath(__file__)
    )

    diretorio_projeto = os.path.dirname(
        diretorio_modulo
    )

    caminhos = [

        # Mesmo diretório do módulo
        os.path.join(
            diretorio_modulo,
            nome_arquivo,
        ),

        # pages/logos/
        os.path.join(
            diretorio_modulo,
            "logos",
            nome_arquivo,
        ),

        # pages/assets/
        os.path.join(
            diretorio_modulo,
            "assets",
            nome_arquivo,
        ),

        # pages/assets/logos/
        os.path.join(
            diretorio_modulo,
            "assets",
            "logos",
            nome_arquivo,
        ),

        # raiz do projeto
        os.path.join(
            diretorio_projeto,
            nome_arquivo,
        ),

        # raiz do projeto / logos
        os.path.join(
            diretorio_projeto,
            "logos",
            nome_arquivo,
        ),

        # raiz do projeto / assets
        os.path.join(
            diretorio_projeto,
            "assets",
            nome_arquivo,
        ),

        # raiz do projeto / assets / logos
        os.path.join(
            diretorio_projeto,
            "assets",
            "logos",
            nome_arquivo,
        ),
    ]

    for caminho in caminhos:

        if os.path.exists(caminho):

            try:

                return Image.open(
                    caminho
                ).convert("RGBA")

            except Exception:

                continue

    return None


# ============================================================
# UTILITÁRIOS
# ============================================================

def remover_acentos(texto):

    if pd.isna(texto):
        return ""

    texto = str(texto)

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    return "".join(
        c for c in texto
        if not unicodedata.combining(c)
    )


def criar_chave_texto(valor):

    if pd.isna(valor):
        return ""

    texto = remover_acentos(valor).upper()

    texto = re.sub(
        r"[^A-Z0-9 ]",
        " ",
        texto,
    )

    return re.sub(
        r"\s+",
        " ",
        texto,
    ).strip()


def padronizar_nome_coluna(col):

    if pd.isna(col):
        return ""

    col_limpa = remover_acentos(str(col)).lower()

    return re.sub(
        r"[^a-z0-9]",
        "",
        col_limpa,
    )


def formatar_nome_cidade(nome):

    if pd.isna(nome):
        return "Não informado"

    palavras = str(nome).strip().split()

    minusculas = {
        "de",
        "do",
        "da",
        "dos",
        "das",
        "e",
    }

    resultado = []

    for idx, palavra in enumerate(palavras):

        if (
            idx > 0
            and palavra.lower() in minusculas
        ):
            resultado.append(
                palavra.lower()
            )
        else:
            resultado.append(
                palavra.capitalize()
            )

    return " ".join(resultado)


def corrigir_nome_exibicao(nome):

    correcoes = {

        "Parnaiba":
            "Parnaíba",

        "Luis Correia":
            "Luís Correia",

        "Cajueiro da Praia":
            "Cajueiro da Praia",

        "Piracuruca":
            "Piracuruca",

        "Sao Raimundo Nonato":
            "São Raimundo Nonato",

        "Sao Joao do Piaui":
            "São João do Piauí",

        "Santo Inacio do Piaui":
            "Santo Inácio do Piauí",

        "Conceicao do Caninde":
            "Conceição do Canindé",

        "Anisio de Abreu":
            "Anísio de Abreu",

        "Dom Inocencio":
            "Dom Inocêncio",

        "Simplicio Mendes":
            "Simplício Mendes",

        "Bonfim do Piaui":
            "Bonfim do Piauí",

        "Sao Lourenco":
            "São Lourenço",

        "Sao Francisco de Assis":
            "São Francisco de Assis",
    }

    return correcoes.get(
        nome,
        nome,
    )


def obter_coluna_unica(
    df_target,
    nome_col,
):

    if nome_col not in df_target.columns:
        return pd.Series(
            [None] * len(df_target),
            index=df_target.index,
        )

    val = df_target[nome_col]

    if isinstance(
        val,
        pd.DataFrame,
    ):
        return val.iloc[:, 0]

    return val


def obter_campo_agrupamento(
    escopo,
):

    escopo = str(
        escopo
    ).upper()

    if escopo == "TERESINA":

        return (
            "bairro_fmt",
            "bairro_key",
            "BAIRROS",
            "BAIRRO",
        )

    return (
        "cidade_fmt",
        "cidade_key",
        "MUNICÍPIOS",
        "MUNICÍPIO",
    )


# ============================================================
# CLASSIFICAÇÃO
# ============================================================

def classificar_tipo_registro(
    servico,
    codigo=None,
):

    if pd.notna(codigo):

        cod_str = str(
            codigo
        ).strip()

        if "146003" in cod_str:
            return "RECLAMACAO"

        elif "146005" in cod_str:
            return "INFORMACAO"

    texto = criar_chave_texto(
        servico
    )

    if (
        "146003" in texto
        or
        "RECLAMACAO DE FALTA DE AGUA"
        in texto
    ):
        return "RECLAMACAO"

    if (
        "146005" in texto
        or
        "INFORMACAO DE FALTA DE AGUA"
        in texto
    ):
        return "INFORMACAO"

    return "OUTRO"


# ============================================================
# DETECÇÃO DE CABEÇALHO
# ============================================================

def ajustar_cabecalho_dataframe(df):

    if df.empty:
        return df

    termos_chave = [

        "dataemissao",
        "dtemissao",
        "data",
        "emissao",
        "ndaos",
        "numos",
        "numeroos",
        "os",
        "servicosolicitado",
        "servicoexecutado",
        "servico",
        "solicitacao",
        "cidade",
        "municipio",
        "bairro",
        "localidade",
        "inicio",
        "termino",
        "codigoservico",
        "codigo",
    ]

    cols_atuais = [
        padronizar_nome_coluna(c)
        for c in df.columns
    ]

    match_header = sum(
        1
        for c in cols_atuais
        if any(
            t == c or t in c
            for t in termos_chave
        )
    )

    if match_header >= 2:
        return df

    for idx in range(
        min(25, len(df))
    ):

        linha_vals = [
            padronizar_nome_coluna(val)
            for val in df.iloc[idx].values
            if pd.notna(val)
        ]

        match_count = sum(
            1
            for v in linha_vals
            if any(
                t == v or t in v
                for t in termos_chave
            )
        )

        if match_count >= 2:

            novos_cabecalhos = [
                str(c).strip()
                if pd.notna(c)
                else f"Col_{i}"
                for i, c in enumerate(
                    df.iloc[idx]
                )
            ]

            df.columns = novos_cabecalhos

            df = df.iloc[
                idx + 1:
            ].reset_index(
                drop=True
            )

            return df

    return df


# ============================================================
# PREPARAÇÃO DA BASE
# ============================================================

def preparar_dataframe_os(df):

    df = df.loc[
        :,
        ~df.columns.duplicated()
    ].copy()

    df = ajustar_cabecalho_dataframe(
        df
    )

    cols_unidas = " ".join(
        [
            str(c)
            for c in df.columns
        ]
    ).lower()

    if (
        "contagem de"
        in cols_unidas
        or
        "rotulos de coluna"
        in cols_unidas
    ):

        raise ValueError(
            "O arquivo anexado é uma "
            "Tabela Dinâmica. Envie a "
            "base bruta."
        )

    mapa_colunas = {

        "Data Emissão": [
            "dtemissao",
            "dataemissao",
            "data",
            "emissao",
            "dtabertura",
            "dataabertura",
        ],

        "Nº da O.S": [
            "ndaos",
            "numos",
            "numeroos",
            "os",
            "nos",
            "numero",
            "ordemdeservico",
        ],

        "Serviço Solicitado": [
            "servicoexecutado",
            "servicosolicitado",
            "servico",
            "descricaoservico",
            "solicitacao",
            "tiposervico",
            "descricao",
        ],

        "Código Serviço": [
            "codigoservico",
            "codservico",
            "codigo",
            "cdservico",
        ],

        "Cidade": [
            "cidade",
            "municipio",
            "localidade",
            "cidadenome",
        ],

        "Bairro": [
            "bairro",
            "bairronome",
            "subdistrito",
        ],
    }

    colunas_renomeadas = {}

    colunas_usadas = set()

    for col_orig in df.columns:

        col_clean = padronizar_nome_coluna(
            col_orig
        )

        for (
            col_oficial,
            sinonimos,
        ) in mapa_colunas.items():

            if col_oficial in colunas_usadas:
                continue

            if (
                col_clean in sinonimos
                or
                col_clean
                ==
                padronizar_nome_coluna(
                    col_oficial
                )
            ):

                colunas_renomeadas[
                    col_orig
                ] = col_oficial

                colunas_usadas.add(
                    col_oficial
                )

                break

    df = df.rename(
        columns=colunas_renomeadas
    )

    obrigatorias = [

        "Data Emissão",
        "Nº da O.S",
        "Serviço Solicitado",
        "Cidade",
        "Bairro",
    ]

    faltantes = [
        c
        for c in obrigatorias
        if c not in df.columns
    ]

    if faltantes:

        raise ValueError(
            "Colunas obrigatórias "
            "ausentes na planilha de O.S.: "
            f"{faltantes}"
        )

    df = df.dropna(
        subset=["Nº da O.S"]
    ).copy()

    col_dt_emissao = obter_coluna_unica(
        df,
        "Data Emissão",
    )

    df["data_emissao"] = pd.to_datetime(
        col_dt_emissao,
        dayfirst=True,
        errors="coerce",
    )

    col_cod = (
        df["Código Serviço"]
        if "Código Serviço"
        in df.columns
        else None
    )

    col_serv_sol = obter_coluna_unica(
        df,
        "Serviço Solicitado",
    )

    tipos = []

    for idx, serv in enumerate(
        col_serv_sol
    ):

        codigo = None

        if col_cod is not None:
            codigo = col_cod.iloc[idx]

        tipos.append(
            classificar_tipo_registro(
                serv,
                codigo,
            )
        )

    df["tipo_registro"] = tipos

    col_cid = obter_coluna_unica(
        df,
        "Cidade",
    )

    col_bairro = obter_coluna_unica(
        df,
        "Bairro",
    )

    df["cidade_fmt"] = (
        col_cid
        .apply(formatar_nome_cidade)
        .apply(corrigir_nome_exibicao)
    )

    df["cidade_key"] = (
        col_cid.apply(
            criar_chave_texto
        )
    )

    df["bairro_fmt"] = (
        col_bairro
        .fillna("Não informado")
        .astype(str)
        .str.strip()
        .str.title()
    )

    df["bairro_key"] = (
        col_bairro.apply(
            criar_chave_texto
        )
    )

    return df


# ============================================================
# PERÍODO
# ============================================================

def detectar_periodo_e_data_ref(
    df_os,
    modo_selecionado="AUTO",
    data_manual="",
):

    if (
        df_os.empty
        or
        "data_emissao"
        not in df_os.columns
    ):
        return (
            "DIARIO",
            "30/08/2026",
        )

    datas_validas = (
        df_os["data_emissao"]
        .dropna()
    )

    if datas_validas.empty:

        return (
            "DIARIO",
            "30/08/2026",
        )

    maior_data = (
        datas_validas.max()
    )

    menor_data = (
        datas_validas.min()
    )

    if (
        data_manual
        and
        str(data_manual).strip()
    ):

        dt_valida = pd.to_datetime(
            data_manual,
            dayfirst=True,
            errors="coerce",
        )

        if pd.notna(dt_valida):

            data_ref_str = (
                dt_valida.strftime(
                    "%d/%m/%Y"
                )
            )

        else:

            data_ref_str = (
                maior_data.strftime(
                    "%d/%m/%Y"
                )
            )

    else:

        data_ref_str = (
            maior_data.strftime(
                "%d/%m/%Y"
            )
        )

    if modo_selecionado != "AUTO":

        return (
            modo_selecionado,
            data_ref_str,
        )

    diferenca_dias = (
        maior_data.date()
        -
        menor_data.date()
    ).days + 1

    if diferenca_dias <= 1:

        return (
            "DIARIO",
            data_ref_str,
        )

    elif diferenca_dias <= 7:

        return (
            "SEMANAL",
            data_ref_str,
        )

    return (
        "MENSAL",
        data_ref_str,
    )


def filtrar_periodo(
    df,
    modo,
    data_referencia,
):

    if df.empty:
        return df.copy()

    data_ref = pd.to_datetime(
        data_referencia,
        dayfirst=True,
        errors="coerce",
    )

    if pd.isna(data_ref):

        raise ValueError(
            "Data de referência inválida."
        )

    data_ref = data_ref.normalize()

    modo = str(
        modo
    ).upper()

    if modo == "DIARIO":

        inicio = data_ref

        fim = (
            data_ref
            +
            timedelta(days=1)
        )

    elif modo == "SEMANAL":

        inicio = (
            data_ref
            -
            timedelta(days=6)
        )

        fim = (
            data_ref
            +
            timedelta(days=1)
        )

    elif modo == "MENSAL":

        inicio = data_ref.replace(
            day=1
        )

        if data_ref.month == 12:

            fim = data_ref.replace(
                year=data_ref.year + 1,
                month=1,
                day=1,
            )

        else:

            fim = data_ref.replace(
                month=data_ref.month + 1,
                day=1,
            )

    else:

        raise ValueError(
            f"Modo de período inválido: {modo}"
        )

    return df[
        (df["data_emissao"] >= inicio)
        &
        (df["data_emissao"] < fim)
    ].copy()


def formatar_periodo_cabecalho(
    data_ref_str,
    modo,
):

    data_ref = pd.to_datetime(
        data_ref_str,
        dayfirst=True,
        errors="coerce",
    )

    if pd.isna(data_ref):
        return data_ref_str

    modo = str(
        modo
    ).upper()

    if modo == "DIARIO":

        return data_ref.strftime(
            "%d/%m/%Y"
        )

    elif modo == "SEMANAL":

        inicio = (
            data_ref
            -
            timedelta(days=6)
        )

        return (
            f"{inicio.strftime('%d/%m/%Y')}"
            f" a "
            f"{data_ref.strftime('%d/%m/%Y')}"
        )

    elif modo == "MENSAL":

        meses = {

            1: "Janeiro",
            2: "Fevereiro",
            3: "Março",
            4: "Abril",
            5: "Maio",
            6: "Junho",
            7: "Julho",
            8: "Agosto",
            9: "Setembro",
            10: "Outubro",
            11: "Novembro",
            12: "Dezembro",
        }

        return (
            f"{meses[data_ref.month]}"
            f"/{data_ref.year}"
        )

    return data_ref_str


# ============================================================
# INDICADORES
# ============================================================

def calcular_kpis_principais(
    df_periodo,
    escopo="PIAUI",
):

    base = df_periodo[
        df_periodo["tipo_registro"].isin(
            [
                "RECLAMACAO",
                "INFORMACAO",
            ]
        )
    ]

    _, col_key, _, _ = (
        obter_campo_agrupamento(
            escopo
        )
    )

    return {

        "total_registros":
            len(base),

        "total_reclamacoes":
            len(
                base[
                    base["tipo_registro"]
                    ==
                    "RECLAMACAO"
                ]
            ),

        "total_informacoes":
            len(
                base[
                    base["tipo_registro"]
                    ==
                    "INFORMACAO"
                ]
            ),

        "municipios_afetados":
            base[col_key].nunique(),
    }


def calcular_top5_municipios(
    df_periodo,
    escopo="PIAUI",
):

    base = df_periodo[
        df_periodo["tipo_registro"].isin(
            [
                "RECLAMACAO",
                "INFORMACAO",
            ]
        )
    ]

    col_fmt, _, _, _ = (
        obter_campo_agrupamento(
            escopo
        )
    )

    if base.empty:

        return pd.DataFrame(
            columns=[
                col_fmt,
                "RECLAMACAO",
                "INFORMACAO",
                "TOTAL",
                "PERCENTUAL",
            ]
        )

    total_periodo = len(base)

    agrupado = (
        base
        .groupby(
            [
                col_fmt,
                "tipo_registro",
            ]
        )
        .size()
        .unstack(
            fill_value=0
        )
    )

    if (
        "RECLAMACAO"
        not in agrupado.columns
    ):

        agrupado[
            "RECLAMACAO"
        ] = 0

    if (
        "INFORMACAO"
        not in agrupado.columns
    ):

        agrupado[
            "INFORMACAO"
        ] = 0

    agrupado["TOTAL"] = (
        agrupado["RECLAMACAO"]
        +
        agrupado["INFORMACAO"]
    )

    agrupado["PERCENTUAL"] = (
        agrupado["TOTAL"]
        /
        total_periodo
        *
        100
    ).round(1)

    agrupado = (
        agrupado
        .sort_values(
            by="TOTAL",
            ascending=False,
        )
        .reset_index()
    )

    if escopo == "PIAUI":

        agrupado[col_fmt] = (
            agrupado[col_fmt]
            .apply(
                corrigir_nome_exibicao
            )
        )

    return agrupado.head(5)


def calcular_municipio_lider(
    df_periodo,
    escopo="PIAUI",
):

    base = df_periodo[
        df_periodo["tipo_registro"].isin(
            [
                "RECLAMACAO",
                "INFORMACAO",
            ]
        )
    ]

    col_fmt, _, _, tipo_rotulo = (
        obter_campo_agrupamento(
            escopo
        )
    )

    if base.empty:

        return {
            "municipio":
                "Sem dados",

            "total_registros":
                0,

            "lider_tipo":
                tipo_rotulo,
        }

    ranking = (
        base[col_fmt]
        .value_counts()
    )

    nome_lider = ranking.index[0]

    if escopo == "PIAUI":

        nome_lider = (
            corrigir_nome_exibicao(
                nome_lider
            )
        )

    return {

        "municipio":
            nome_lider,

        "total_registros":
            int(ranking.iloc[0]),

        "lider_tipo":
            tipo_rotulo,
    }


def calcular_kpi_areas_criticas(
    df_periodo,
    escopo="PIAUI",
):

    base_rec = df_periodo[
        df_periodo["tipo_registro"]
        ==
        "RECLAMACAO"
    ]

    col_fmt, _, _, _ = (
        obter_campo_agrupamento(
            escopo
        )
    )

    rotulo = (
        "Bairros"
        if escopo == "TERESINA"
        else "Municípios"
    )

    if base_rec.empty:

        return {

            "qtd_criticas":
                0,

            "rotulo_entidade":
                rotulo,
        }

    rec_por_agrupamento = (
        base_rec
        .groupby(col_fmt)
        .size()
    )

    qtd_criticas = int(
        (
            rec_por_agrupamento
            >
            5
        ).sum()
    )

    return {

        "qtd_criticas":
            qtd_criticas,

        "rotulo_entidade":
            rotulo,
    }


def calcular_municipios_atencao(
    df_periodo,
    limite=5,
    escopo="PIAUI",
):

    col_fmt, _, _, _ = (
        obter_campo_agrupamento(
            escopo
        )
    )

    base = df_periodo[
        df_periodo["tipo_registro"].isin(
            [
                "RECLAMACAO",
                "INFORMACAO",
            ]
        )
    ]

    if base.empty:
        return []

    agrupado = (
        base
        .groupby(
            [
                col_fmt,
                "tipo_registro",
            ]
        )
        .size()
        .unstack(
            fill_value=0
        )
    )

    if (
        "RECLAMACAO"
        not in agrupado.columns
    ):

        agrupado[
            "RECLAMACAO"
        ] = 0

    if (
        "INFORMACAO"
        not in agrupado.columns
    ):

        agrupado[
            "INFORMACAO"
        ] = 0

    agrupado["TOTAL"] = (
        agrupado["RECLAMACAO"]
        +
        agrupado["INFORMACAO"]
    )

    agrupado = (
        agrupado
        .sort_values(
            by="TOTAL",
            ascending=False,
        )
        .reset_index()
    )

    resultado = []

    for _, row in (
        agrupado.head(limite)
        .iterrows()
    ):

        nome_item = row[col_fmt]

        if escopo == "PIAUI":

            nome_item = (
                corrigir_nome_exibicao(
                    nome_item
                )
            )

        resultado.append(
            {

                "municipio":
                    nome_item,

                "registros":
                    int(row["TOTAL"]),

                "rec_oficial":
                    int(row["RECLAMACAO"]),

                "inf_oficial":
                    int(row["INFORMACAO"]),
            }
        )

    return resultado


def construir_dashboard_data(
    df_periodo,
    modo,
    data_ref,
    escopo,
    eventos_manuais=None,
):

    if (
        str(escopo).upper()
        ==
        "TERESINA"
    ):

        df_periodo = df_periodo[
            df_periodo["cidade_key"]
            ==
            "TERESINA"
        ].copy()

    kpis = (
        calcular_kpis_principais(
            df_periodo,
            escopo=escopo,
        )
    )

    top5 = (
        calcular_top5_municipios(
            df_periodo,
            escopo=escopo,
        )
    )

    return {

        "kpis":
            kpis,

        "top5":
            top5,

        "lider":
            calcular_municipio_lider(
                df_periodo,
                escopo=escopo,
            ),

        "areas_criticas":
            calcular_kpi_areas_criticas(
                df_periodo,
                escopo=escopo,
            ),

        "concentracao_top5":
            round(
                (
                    top5["TOTAL"].sum()
                    /
                    kpis[
                        "total_registros"
                    ]
                    *
                    100
                ),
                1,
            )
            if kpis[
                "total_registros"
            ] > 0
            else 0.0,

        "municipios_atencao":
            calcular_municipios_atencao(
                df_periodo,
                escopo=escopo,
            ),

        "escopo":
            escopo,

        "eventos_manuais":
            eventos_manuais or [],
    }


# ============================================================
# LOGO
# ============================================================

def converter_logo_para_branco(
    pil_img,
):

    if pil_img is None:
        return None

    try:

        img = pil_img.convert(
            "RGBA"
        )

        data = np.array(img)

        data[..., :3] = [
            255,
            255,
            255,
        ]

        return Image.fromarray(
            data
        )

    except Exception:

        return pil_img


# ============================================================
# RENDERIZAÇÃO DO CARD
# ============================================================

def renderizar_card_executivo(
    dashboard_data,
    data_ref,
    modo,
    regional,
    logo_img=None,
    unidade_nome="Águas do Piauí",
):

    escopo = dashboard_data.get(
        "escopo",
        "PIAUI",
    )

    (
        col_fmt,
        _,
        plural_rotulo,
        singular_rotulo,
    ) = obter_campo_agrupamento(
        escopo
    )

    fig = plt.figure(
        figsize=(16, 9),
        facecolor=C_BG,
    )

    gs = fig.add_gridspec(
        5,
        7,
        height_ratios=[
            0.8,
            1.2,
            2.5,
            2.5,
            0.3,
        ],
        wspace=0.18,
        hspace=0.3,
        left=0.03,
        right=0.97,
        top=0.95,
        bottom=0.02,
    )

    # --------------------------------------------------------
    # CABEÇALHO
    # --------------------------------------------------------

    header = mpatches.FancyBboxPatch(
        (
            0.03,
            0.88,
        ),
        0.94,
        0.075,
        boxstyle=(
            "round,pad=0.0,"
            "rounding_size=0.02"
        ),
        facecolor=C_PRIMARY,
        edgecolor="none",
        zorder=1,
        transform=fig.transFigure,
    )

    fig.patches.append(
        header
    )

    ax_header = fig.add_axes(
        [
            0.04,
            0.88,
            0.92,
            0.075,
        ],
        facecolor="none",
        zorder=2,
    )

    ax_header.axis("off")

    periodo_texto = (
        formatar_periodo_cabecalho(
            data_ref,
            modo,
        )
    )

    ax_header.text(
        0.01,
        0.75,
        regional.upper(),
        color="#8DB4FF",
        fontsize=9.0,
        fontweight="bold",
        va="center",
    )

    ax_header.text(
        0.01,
        0.45,
        "RELATÓRIO EXECUTIVO DE FALTA DE ÁGUA",
        color="#FFFFFF",
        fontsize=18,
        fontweight="bold",
        va="center",
    )

    ax_header.text(
        0.01,
        0.15,
        (
            f"Período Analisado: "
            f"{periodo_texto} ({modo})"
        ),
        color="#E2E8F0",
        fontsize=9.5,
        va="center",
    )

    # --------------------------------------------------------
    # LOGO DA UNIDADE
    # --------------------------------------------------------

    texto_fallback_unidade = (
        unidade_nome.upper()
        if unidade_nome
        else "ÁGUAS DO PIAUÍ"
    )

    if logo_img is not None:

        try:

            logo_branca = (
                converter_logo_para_branco(
                    logo_img
                )
            )

            ax_logo = fig.add_axes(
                [
                    0.80,
                    0.885,
                    0.15,
                    0.06,
                ],
                zorder=3,
            )

            ax_logo.imshow(
                logo_branca
            )

            ax_logo.axis("off")

        except Exception:

            ax_header.text(
                0.99,
                0.50,
                texto_fallback_unidade,
                color="#FFFFFF",
                fontsize=16,
                fontweight="bold",
                ha="right",
                va="center",
            )

    else:

        ax_header.text(
            0.99,
            0.50,
            texto_fallback_unidade,
            color="#FFFFFF",
            fontsize=16,
            fontweight="bold",
            ha="right",
            va="center",
        )

    # --------------------------------------------------------
    # ESTILO DOS CARDS
    # --------------------------------------------------------

    def estilizar_card(
        ax,
        border_color=C_BORDER,
        bg_color=C_CARD,
    ):

        ax.set_facecolor(
            bg_color
        )

        ax.set_xticks([])
        ax.set_yticks([])

        for spine in ax.spines.values():

            spine.set_color(
                border_color
            )

            spine.set_linewidth(
                1.1
            )

    # --------------------------------------------------------
    # KPI
    # --------------------------------------------------------

    kpis = dashboard_data[
        "kpis"
    ]

    lider = dashboard_data[
        "lider"
    ]

    areas_criticas = (
        dashboard_data[
            "areas_criticas"
        ]
    )

    def desenhar_kpi(
        ax,
        titulo,
        valor,
        cor_val,
        subtexto=None,
        cor_sub=C_MUTED,
    ):

        estilizar_card(ax)

        patch_accent = (
            mpatches.FancyBboxPatch(
                (
                    0.0,
                    0.90,
                ),
                1.0,
                0.10,
                boxstyle=(
                    "round,pad=0.0"
                ),
                facecolor=cor_val,
                edgecolor="none",
                transform=ax.transAxes,
            )
        )

        ax.add_patch(
            patch_accent
        )

        ax.text(
            0.08,
            0.72,
            titulo.upper(),
            fontsize=7.5,
            fontweight="bold",
            color=C_MUTED,
            transform=ax.transAxes,
        )

        ax.text(
            0.08,
            0.35,
            str(valor),
            fontsize=26,
            fontweight="bold",
            color=C_TEXT,
            transform=ax.transAxes,
        )

        if subtexto:

            ax.text(
                0.08,
                0.12,
                subtexto,
                fontsize=7.5,
                fontweight="semibold",
                color=cor_sub,
                transform=ax.transAxes,
            )

    # --------------------------------------------------------
    # 7 KPIs
    # --------------------------------------------------------

    ax_k1 = fig.add_subplot(
        gs[1, 0]
    )

    desenhar_kpi(
        ax_k1,
        "Total O.S.",
        kpis[
            "total_registros"
        ],
        C_PRIMARY,
        "Demanda total do período",
    )

    ax_k2 = fig.add_subplot(
        gs[1, 1]
    )

    pct_rec = (
        kpis[
            "total_reclamacoes"
        ]
        /
        kpis[
            "total_registros"
        ]
        *
        100
        if kpis[
            "total_registros"
        ] > 0
        else 0
    )

    desenhar_kpi(
        ax_k2,
        "Reclamações",
        kpis[
            "total_reclamacoes"
        ],
        C_PRIMARY,
        f"{pct_rec:.0f}% do total",
    )

    ax_k3 = fig.add_subplot(
        gs[1, 2]
    )

    pct_inf = (
        kpis[
            "total_informacoes"
        ]
        /
        kpis[
            "total_registros"
        ]
        *
        100
        if kpis[
            "total_registros"
        ] > 0
        else 0
    )

    desenhar_kpi(
        ax_k3,
        "Informações",
        kpis[
            "total_informacoes"
        ],
        C_INFO,
        f"{pct_inf:.0f}% do total",
    )

    ax_k4 = fig.add_subplot(
        gs[1, 3]
    )

    desenhar_kpi(
        ax_k4,
        plural_rotulo,
        kpis[
            "municipios_afetados"
        ],
        "#334155",
        "Com registros no período",
    )

    ax_k5 = fig.add_subplot(
        gs[1, 4]
    )

    desenhar_kpi(
        ax_k5,
        f"{singular_rotulo} LÍDER",
        lider[
            "total_registros"
        ],
        C_PRIMARY,
        lider[
            "municipio"
        ].upper(),
        cor_sub=C_PRIMARY,
    )

    ax_k6 = fig.add_subplot(
        gs[1, 5]
    )

    qtd_criticas = (
        areas_criticas[
            "qtd_criticas"
        ]
    )

    cor_al = (
        C_ALERT
        if qtd_criticas > 0
        else C_TEXT
    )

    desenhar_kpi(
        ax_k6,
        "ÁREAS CRÍTICAS",
        qtd_criticas,
        cor_al,
        (
            "> 5 rec."
            if qtd_criticas > 0
            else
            "Nenhum alerta"
        ),
        cor_sub=cor_al,
    )

    ax_k7 = fig.add_subplot(
        gs[1, 6]
    )

    desenhar_kpi(
        ax_k7,
        "CONCENTRAÇÃO TOP 5",
        (
            f"{dashboard_data.get('concentracao_top5', 0.0):.0f}%"
        ),
        C_PRIMARY,
        "Volume acumulado",
        cor_sub=C_MUTED,
    )

    # --------------------------------------------------------
    # TOP 5
    # --------------------------------------------------------

    ax_top5 = fig.add_subplot(
        gs[2:4, 0:4]
    )

    estilizar_card(
        ax_top5
    )

    ax_top5.grid(
        False
    )

    top5_df = dashboard_data[
        "top5"
    ]

    if not top5_df.empty:

        cidades = (
            top5_df[
                col_fmt
            ].tolist()
        )

        reclamacoes_of = (
            top5_df[
                "RECLAMACAO"
            ].tolist()
        )

        informacoes_of = (
            top5_df[
                "INFORMACAO"
            ].tolist()
        )

        totais_of = (
            top5_df[
                "TOTAL"
            ].tolist()
        )

        x = np.arange(
            len(cidades)
        )

        width = 0.35

        rects1 = ax_top5.bar(
            x - width / 2,
            reclamacoes_of,
            width,
            label="Reclamação",
            color=C_PRIMARY,
        )

        rects2 = ax_top5.bar(
            x + width / 2,
            informacoes_of,
            width,
            label="Informação",
            color=C_INFO,
        )

        ax_top5.set_title(
            (
                f"TOP 5 "
                f"{plural_rotulo} "
                f"COM MAIOR DEMANDA"
            ),
            fontsize=11.0,
            fontweight="bold",
            color=C_TEXT,
            pad=15,
            loc="left",
        )

        ax_top5.set_xticks(
            x
        )

        labels_eixo = [
            f"{cid}\nTotal: {tot}"
            for cid, tot
            in zip(
                cidades,
                totais_of,
            )
        ]

        ax_top5.set_xticklabels(
            labels_eixo,
            fontsize=10.0,
            fontweight="bold",
            color=C_TEXT,
        )

        ax_top5.legend(
            loc="upper right",
            frameon=False,
            fontsize=9.5,
        )

        max_val = max(
            max(
                reclamacoes_of,
                default=1,
            ),
            max(
                informacoes_of,
                default=1,
            ),
        )

        ax_top5.set_ylim(
            0,
            max_val * 1.25,
        )

        for rects, col_color in zip(
            [
                rects1,
                rects2,
            ],
            [
                C_PRIMARY,
                C_INFO,
            ],
        ):

            for rect in rects:

                h = (
                    rect.get_height()
                )

                if h > 0:

                    y_pos = (
                        h
                        +
                        (
                            max_val
                            *
                            0.02
                        )
                        if h
                        <
                        max_val
                        *
                        0.15
                        else
                        h * 0.5
                    )

                    color_txt = (
                        col_color
                        if h
                        <
                        max_val
                        *
                        0.15
                        else
                        "white"
                    )

                    va_align = (
                        "bottom"
                        if h
                        <
                        max_val
                        *
                        0.15
                        else
                        "center"
                    )

                    ax_top5.text(
                        (
                            rect.get_x()
                            +
                            rect.get_width()
                            /
                            2.
                        ),
                        y_pos,
                        f"{int(h)}",
                        ha="center",
                        va=va_align,
                        fontsize=10.5,
                        fontweight="bold",
                        color=color_txt,
                    )

    else:

        ax_top5.text(
            0.5,
            0.5,
            "Nenhum registro encontrado no período.",
            ha="center",
            va="center",
            fontsize=11,
            color=C_MUTED,
            transform=ax_top5.transAxes,
        )

    # --------------------------------------------------------
    # ÁREAS EM ATENÇÃO
    # --------------------------------------------------------

    ax_atencao = fig.add_subplot(
        gs[2, 4:7]
    )

    estilizar_card(
        ax_atencao
    )

    ax_atencao.text(
        0.04,
        0.86,
        f"{plural_rotulo} EM ATENÇÃO",
        fontsize=10.5,
        fontweight="bold",
        color=C_ALERT,
        transform=ax_atencao.transAxes,
    )

    atencao_list = (
        dashboard_data[
            "municipios_atencao"
        ]
    )

    if not atencao_list:

        ax_atencao.text(
            0.04,
            0.50,
            (
                f"Nenhum "
                f"{singular_rotulo.lower()} "
                f"registrado."
            ),
            fontsize=9.5,
            color=C_MUTED,
            va="center",
            transform=ax_atencao.transAxes,
        )

    else:

        y_p = 0.58

        for i, item in enumerate(
            atencao_list[:3]
        ):

            if i > 0:

                ax_atencao.plot(
                    [
                        0.04,
                        0.96,
                    ],
                    [
                        y_p + 0.12,
                        y_p + 0.12,
                    ],
                    color="#E2E8F0",
                    linewidth=0.8,
                    transform=ax_atencao.transAxes,
                )

            ax_atencao.text(
                0.04,
                y_p,
                f"{item['registros']}",
                fontsize=18,
                fontweight="bold",
                color=C_ALERT,
                va="center",
                transform=ax_atencao.transAxes,
            )

            ax_atencao.text(
                0.15,
                y_p + 0.06,
                f"{item['municipio']}",
                fontsize=11.0,
                fontweight="bold",
                color=C_TEXT,
                va="center",
                transform=ax_atencao.transAxes,
            )

            ax_atencao.text(
                0.15,
                y_p - 0.06,
                (
                    f"R: {item.get('rec_oficial', 0)}"
                    f"   |   "
                    f"I: {item.get('inf_oficial', 0)}"
                ),
                fontsize=9.5,
                fontweight="bold",
                color=C_MUTED,
                va="center",
                transform=ax_atencao.transAxes,
            )

            y_p -= 0.22

    # --------------------------------------------------------
    # INFORMAÇÕES OPERACIONAIS
    # --------------------------------------------------------

    ax_eventos = fig.add_subplot(
        gs[3, 4:7]
    )

    estilizar_card(
        ax_eventos
    )

    ax_eventos.text(
        0.04,
        0.88,
        "INFORMAÇÕES OPERACIONAIS",
        fontsize=10.5,
        fontweight="bold",
        color=C_PRIMARY,
        transform=ax_eventos.transAxes,
    )

    ev_manuais = dashboard_data.get(
        "eventos_manuais",
        [],
    )

    if not ev_manuais:

        ax_eventos.text(
            0.04,
            0.50,
            (
                "Nenhum evento operacional "
                "de destaque registrado."
            ),
            fontsize=9.5,
            color=C_MUTED,
            va="center",
            transform=ax_eventos.transAxes,
        )

    else:

        num_ev = len(
            ev_manuais
        )

        if num_ev == 3:

            y_tops = [
                0.72,
                0.44,
                0.16,
            ]

        elif num_ev == 2:

            y_tops = [
                0.65,
                0.30,
            ]

        else:

            y_tops = [
                0.55
            ]

        for i, ev in enumerate(
            ev_manuais
        ):

            y_top = y_tops[i]

            mun_str = (
                ev.get(
                    "mun",
                    "",
                )
                .strip()
                .title()
            )

            tag_str = (
                ev.get(
                    "tag",
                    "",
                )
                .strip()
                .upper()
            )

            desc_str = (
                ev.get(
                    "desc",
                    "",
                )
                .strip()
                .title()
            )

            linha_parts = []

            if mun_str:
                linha_parts.append(
                    mun_str
                )

            if tag_str:
                linha_parts.append(
                    tag_str
                )

            if desc_str:
                linha_parts.append(
                    desc_str
                )

            titulo_completo = (
                " – ".join(
                    linha_parts
                )
            )

            if not titulo_completo:

                titulo_completo = (
                    f"Evento {i + 1}"
                )

            obs_wrap = "\n".join(
                textwrap.wrap(
                    str(
                        ev.get(
                            "obs",
                            "",
                        )
                    ).strip(),
                    width=78,
                )
            )[:150]

            ax_eventos.text(
                0.04,
                y_top,
                titulo_completo,
                fontsize=10.0,
                fontweight="bold",
                color=C_TEXT,
                transform=ax_eventos.transAxes,
            )

            if obs_wrap:

                ax_eventos.text(
                    0.04,
                    y_top - 0.08,
                    obs_wrap,
                    fontsize=9.5,
                    color=C_OBS_DARK,
                    va="top",
                    transform=ax_eventos.transAxes,
                )

    # --------------------------------------------------------
    # RODAPÉ
    # --------------------------------------------------------

    ax_footer = fig.add_subplot(
        gs[4, :]
    )

    ax_footer.axis(
        "off"
    )

    ax_footer.text(
        0.5,
        0.50,
        "Fonte: COI – Centro de Operações Integradas",
        ha="center",
        va="center",
        fontsize=9.0,
        fontweight="semibold",
        color=C_MUTED,
    )

    # --------------------------------------------------------
    # EXPORTAÇÃO
    # --------------------------------------------------------

    buffer = io.BytesIO()

    plt.savefig(
        buffer,
        format="png",
        dpi=300,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
    )

    buffer.seek(0)

    plt.close(fig)

    return buffer.getvalue()


# ============================================================
# ZIP
# ============================================================

def gerar_zip_dashboard(
    png_bytes,
):

    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as zipf:

        zipf.writestr(
            "card_executivo.png",
            png_bytes,
        )

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# EVENTOS OPERACIONAIS
# ============================================================

def extrair_eventos_manuais():

    eventos = []

    for i in range(1, 4):

        mun = st.session_state.get(
            f"evento_{i}_mun",
            "",
        )

        tag = st.session_state.get(
            f"evento_{i}_tag",
            "",
        )

        desc = st.session_state.get(
            f"evento_{i}_desc",
            "",
        )

        obs = st.session_state.get(
            f"evento_{i}_obs",
            "",
        )

        if any(
            [
                mun.strip(),
                tag.strip(),
                desc.strip(),
                obs.strip(),
            ]
        ):

            eventos.append(
                {
                    "mun": mun,
                    "tag": tag,
                    "desc": desc,
                    "obs": obs,
                }
            )

    return eventos[:3]


# ============================================================
# CABEÇALHO PRINCIPAL
# ============================================================

st.markdown(
    '<div class="main-title">Cards Operacionais</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="main-subtitle">'
    "Geração de relatório executivo de falta de água"
    "</div>",
    unsafe_allow_html=True,
)


# ============================================================
# 1 — BASE DE DADOS
# ============================================================

st.markdown(
    '<div class="section-title">1. Base de Dados</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="section-description">'
    "Envie a base de Ordens de Serviço que será utilizada "
    "para gerar o card executivo."
    "</div>",
    unsafe_allow_html=True,
)

arquivo_excel = st.file_uploader(
    "Planilha de O.S.",
    type=[
        "xlsx",
        "xls",
    ],
    help=(
        "Envie a base bruta de Ordens de Serviço. "
        "Tabelas Dinâmicas não são aceitas."
    ),
)


# ============================================================
# 2 — CONFIGURAÇÃO
# ============================================================

st.markdown(
    '<div class="section-title">2. Configuração do Card</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="section-description">'
    "Defina o escopo, regional e período que serão apresentados "
    "no relatório executivo."
    "</div>",
    unsafe_allow_html=True,
)

config_container = st.container(
    border=True
)

with config_container:

    col_config_1, col_config_2 = (
        st.columns(2)
    )

    with col_config_1:

        escopo = st.selectbox(
            "Escopo",
            options=[
                (
                    "Piauí (Municípios)",
                    "PIAUI",
                ),
                (
                    "Teresina (Bairros)",
                    "TERESINA",
                ),
            ],
            format_func=lambda x: x[0],
            index=0,
        )

        regional = st.text_input(
            "Regional",
            value=(
                "REGIONAL SEMIÁRIDO - SUL"
            ),
        )

    with col_config_2:

        modo_selecionado = st.selectbox(
            "Período",
            options=[
                "AUTO",
                "DIARIO",
                "SEMANAL",
                "MENSAL",
            ],
            format_func=lambda x: {
                "AUTO":
                    "Automático",
                "DIARIO":
                    "Diário",
                "SEMANAL":
                    "Semanal",
                "MENSAL":
                    "Mensal",
            }[x],
            index=0,
        )

        data_manual = st.text_input(
            "Data de referência",
            value="",
            placeholder="Ex.: 30/09/2026",
            help=(
                "Deixe em branco para utilizar "
                "automaticamente a maior data encontrada "
                "na base."
            ),
        )


# ============================================================
# 3 — IDENTIDADE VISUAL
# ============================================================

st.markdown(
    '<div class="section-title">3. Identidade Visual</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="section-description">'
    "Selecione a unidade para aplicar automaticamente "
    "a identidade visual correspondente ao card."
    "</div>",
    unsafe_allow_html=True,
)

unidade_selecionada = st.radio(
    "Selecionar Unidade",
    options=list(
        LOGOS_UNIDADES.keys()
    ),
    horizontal=True,
    key="unidade_selecionada",
)

logo_selecionada = carregar_logo_unidade(
    unidade_selecionada
)

if logo_selecionada is None:

    st.error(
        f"Logo da unidade '{unidade_selecionada}' "
        "não foi encontrada no repositório."
    )

    st.caption(
        "Verifique se o arquivo "
        f"'{LOGOS_UNIDADES[unidade_selecionada]}' "
        "foi enviado ao GitHub."
    )

else:

    st.caption(
        f"Unidade selecionada: **{unidade_selecionada}**"
    )


# ============================================================
# 4 — INFORMAÇÕES OPERACIONAIS
# ============================================================

st.markdown(
    '<div class="section-title">4. Informações Operacionais</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="section-description">'
    "Adicione até três informações operacionais para aparecerem "
    "no card executivo."
    "</div>",
    unsafe_allow_html=True,
)


for i in range(1, 4):

    st.markdown(
        f"**Evento {i}**"
    )

    col_mun, col_tag, col_evento = (
        st.columns(
            [1.0, 0.8, 1.4]
        )
    )

    with col_mun:

        st.text_input(
            "Município",
            key=f"evento_{i}_mun",
            placeholder="Ex.: Picos",
        )

    with col_tag:

        st.text_input(
            "TAG",
            key=f"evento_{i}_tag",
            placeholder="Ex.: ETA-01",
        )

    with col_evento:

        st.text_input(
            "Evento",
            key=f"evento_{i}_desc",
            placeholder="Ex.: Falta de energia",
        )

    st.text_input(
        "Observação",
        key=f"evento_{i}_obs",
        placeholder="Breve detalhe operacional...",
    )

    if i < 3:
        st.divider()


# ============================================================
# GERAÇÃO
# ============================================================

st.markdown("")

col_gerar_1, col_gerar_2, col_gerar_3 = (
    st.columns(
        [1, 1, 1]
    )
)

with col_gerar_2:

    gerar_card = st.button(
        "Gerar Card Executivo",
        type="primary",
        use_container_width=True,
    )


# ============================================================
# PROCESSAMENTO
# ============================================================

if gerar_card:

    if arquivo_excel is None:

        st.error(
            "Envie uma planilha de O.S. antes de gerar o card."
        )

        st.stop()

    if logo_selecionada is None:

        st.error(
            "A logo da unidade selecionada não foi encontrada. "
            "Verifique os arquivos no GitHub antes de gerar o card."
        )

        st.stop()

    try:

        with st.spinner(
            "Processando a base e gerando o card executivo..."
        ):

            raw_df = pd.read_excel(
                arquivo_excel
            )

            df_os = preparar_dataframe_os(
                raw_df
            )

            modo, data_ref = (
                detectar_periodo_e_data_ref(
                    df_os,
                    modo_selecionado,
                    data_manual,
                )
            )

            df_periodo = filtrar_periodo(
                df_os,
                modo=modo,
                data_referencia=data_ref,
            )

            eventos = (
                extrair_eventos_manuais()
            )

            dashboard_data = (
                construir_dashboard_data(
                    df_periodo,
                    modo,
                    data_ref,
                    escopo[1],
                    eventos,
                )
            )

            png_bytes = (
                renderizar_card_executivo(
                    dashboard_data,
                    data_ref,
                    modo,
                    regional.strip()
                    or "REGIONAL",
                    logo_selecionada,
                    unidade_selecionada,
                )
            )

            zip_bytes = (
                gerar_zip_dashboard(
                    png_bytes
                )
            )

            st.session_state[
                "card_png_bytes"
            ] = png_bytes

            st.session_state[
                "card_zip_bytes"
            ] = zip_bytes

            st.session_state[
                "card_dashboard_data"
            ] = dashboard_data

            st.session_state[
                "card_modo"
            ] = modo

            st.session_state[
                "card_data_ref"
            ] = data_ref

            st.session_state[
                "card_unidade"
            ] = unidade_selecionada

            st.session_state[
                "card_gerado"
            ] = True

    except ValueError as err:

        st.error(
            str(err)
        )

        st.session_state[
            "card_gerado"
        ] = False

    except Exception as err:

        st.error(
            "Não foi possível gerar o card."
        )

        st.exception(
            err
        )

        st.session_state[
            "card_gerado"
        ] = False


# ============================================================
# RESULTADO
# ============================================================

if st.session_state.get(
    "card_gerado",
    False,
):

    dashboard_data = (
        st.session_state[
            "card_dashboard_data"
        ]
    )

    modo = (
        st.session_state[
            "card_modo"
        ]
    )

    data_ref = (
        st.session_state[
            "card_data_ref"
        ]
    )

    unidade_gerada = (
        st.session_state.get(
            "card_unidade",
            unidade_selecionada,
        )
    )

    png_bytes = (
        st.session_state[
            "card_png_bytes"
        ]
    )

    zip_bytes = (
        st.session_state[
            "card_zip_bytes"
        ]
    )

    st.divider()

    # --------------------------------------------------------
    # RESUMO DO PROCESSAMENTO
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">'
        "Card gerado"
        "</div>",
        unsafe_allow_html=True,
    )

    kpis_resultado = (
        dashboard_data[
            "kpis"
        ]
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        st.metric(
            "O.S. no período",
            kpis_resultado[
                "total_registros"
            ],
        )

    with c2:

        st.metric(
            "Reclamações",
            kpis_resultado[
                "total_reclamacoes"
            ],
        )

    with c3:

        st.metric(
            "Informações",
            kpis_resultado[
                "total_informacoes"
            ],
        )

    with c4:

        st.metric(
            (
                "Municípios"
                if escopo[1]
                ==
                "PIAUI"
                else
                "Bairros"
            ),
            kpis_resultado[
                "municipios_afetados"
            ],
        )

    st.caption(
        (
            f"Unidade: **{unidade_gerada}**  |  "
            f"Período utilizado: "
            f"{formatar_periodo_cabecalho(data_ref, modo)} "
            f"({modo})"
        )
    )

    # --------------------------------------------------------
    # PRÉ-VISUALIZAÇÃO
    # --------------------------------------------------------

    st.markdown(
        '<div class="section-title">'
        "Pré-visualização"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="section-description">'
        "Visualização do card executivo em formato paisagem 16:9."
        "</div>",
        unsafe_allow_html=True,
    )

    st.image(
        png_bytes,
        use_container_width=True,
    )

    # --------------------------------------------------------
    # DOWNLOADS
    # --------------------------------------------------------

    st.markdown("")

    col_download_1, col_download_2 = (
        st.columns(2)
    )

    with col_download_1:

        st.download_button(
            label="Baixar Card em PNG",
            data=png_bytes,
            file_name=(
                "card_executivo.png"
            ),
            mime="image/png",
            use_container_width=True,
        )

    with col_download_2:

        st.download_button(
            label="Baixar Pacote ZIP",
            data=zip_bytes,
            file_name=(
                "card_executivo.zip"
            ),
            mime="application/zip",
            use_container_width=True,
        )
