import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import json
import pandas as pd
import folium
from folium.plugins import Fullscreen
from streamlit_folium import st_folium
import os
import simplekml
from datetime import datetime, date, timedelta
import io
import uuid
from typing import Optional
import time
import plotly.express as px
import plotly.graph_objects as go
from branca.element import Element, MacroElement
from jinja2 import Template
from html import escape
import unicodedata
import re
import math
from pathlib import Path
import sys

# Mesmo conversor da sincronização automática por GitHub Actions.
# Aceita execução em pages/ e no Ambiente de Teste.
BP_RAIZ_SAI = Path(__file__).resolve().parent
if BP_RAIZ_SAI.name.lower() == "pages":
    BP_RAIZ_SAI = BP_RAIZ_SAI.parent
if str(BP_RAIZ_SAI) not in sys.path:
    sys.path.insert(0, str(BP_RAIZ_SAI))
from sai_pressao_importacao import (
    IndiceDuplicidades, converter_bruta, ler_arquivo,
)
import xml.etree.ElementTree as ET


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Monitoramento de Baixa Pressão - COI",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded"
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("1")

st.markdown(
    """
    <style>
        [data-testid="stSidebarNav"] {
            display: none !important;
        }

        [data-testid="stSidebar"] div.block-container {
            padding-top: 1.5rem;
            padding-bottom: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# TRAVA DE SEGURANÇA E CONTROLE DE SESSÃO DO HUB
# ============================================================
from auth import verificar_autenticacao, pode_executar_acao

if not verificar_autenticacao():
    st.warning("Sessão não iniciada ou expirada.")

    if st.button("Ir para o Login"):
        st.switch_page("app.py")

    st.stop()


# ============================================================
# CONTROLE DE PERFIL
# ============================================================
perfil_atual = str(
    st.session_state.get("perfil", "")
).strip().lower()

eh_operador = perfil_atual == "operador"

# Operador é somente consulta/análise.
# Admin e usuário mantêm as permissões de alteração existentes.
pode_alterar_dados = any(pode_executar_acao("1", a) for a in ("criar", "importar", "editar", "excluir"))


# ============================================================
# CONSTANTES
# ============================================================
LAT_BASE = -5.0892
LON_BASE = -42.8019

SPREADSHEET_ID = (
    "15iN3YEGyxk3l1ZKaHJJp-BvTfVHqpd7gL1GX3RbAKUU"
)

COLUNAS_PADRAO = [
    "ID",
    "Data",
    "Municipio",
    "Bairro",
    "Latitude",
    "Longitude",
    "Pressao_MCA",
    "Observacao",
    "Matricula"
]

# Campo adicional de leitura: quando existir na planilha, o protocolo é mostrado
# no relatório Sanear. NÃO muda A:I nem o sincronizador de nove colunas.
BP_COLUNA_PROTOCOLO = "Cod_Protocolo"


# Marcador técnico da atividade: indispensável à deduplicação, mas não é
# uma observação operacional. Nunca altere o valor bruto no Google Sheets.
_BP_MARCADOR_ATIVIDADE = re.compile(
    r"\bSAI_ATIVIDADE\s*=\s*[A-Z0-9_-]+", re.IGNORECASE
)


def bp_observacao_visivel(valor) -> str:
    """Oculta apenas o marcador técnico nas telas e exportações para leitura."""
    if valor is None:
        return ""
    texto = str(valor).strip()
    if texto.lower() in ("nan", "none", "nat", ""):
        return ""
    if not _BP_MARCADOR_ATIVIDADE.search(texto):
        return texto
    texto = _BP_MARCADOR_ATIVIDADE.sub("", texto)
    texto = re.sub(r"(?:\s*[|;]\s*){2,}", " | ", texto)
    return texto.strip(" \t\r\n|;,/-")


def bp_observacao_preservando_id(texto_editado, observacao_original) -> str:
    """Preserva o ID técnico ao salvar uma observação editada no formulário."""
    observacao = bp_observacao_visivel(texto_editado)
    marcadores = list(dict.fromkeys(
        _BP_MARCADOR_ATIVIDADE.findall(str(observacao_original or ""))
    ))
    if not marcadores:
        return observacao
    retorno = " | ".join([valor for valor in [observacao, *marcadores] if valor])
    return retorno


# ============================================================
# SANEAR — ÁREAS DE INTERVENÇÃO DE ESGOTAMENTO SANITÁRIO
# ============================================================
def bp_encontrar_kml_sanear():
    """Localiza o KML no repositório (módulo na raiz ou em pages/)."""
    relativo = Path("geodados") / "areas_influencia" / "sanear.kml"
    base = Path(__file__).resolve().parent
    candidatos = (base / relativo, base.parent / relativo, Path.cwd() / relativo)
    for caminho in candidatos:
        if caminho.is_file():
            return caminho
    return candidatos[0]


@st.cache_data(show_spinner=False)
def bp_carregar_poligonos_sanear(caminho: str, versao: int):
    """Lê polígonos KML locais em GeoJSON, sem dependências geoespaciais extras.

    O KML guarda os vértices em longitude/latitude. GeoJSON utiliza a mesma
    ordem, enquanto o Leaflet se encarrega da projeção no mapa.
    `versao` recebe o mtime_ns para invalidar o cache ao atualizar o arquivo.
    """
    _ = versao
    raiz = ET.parse(caminho).getroot()
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    placas = raiz.findall(".//k:Placemark", ns)
    if not placas:
        placas = raiz.findall(".//Placemark")

    def ler_anel(elemento):
        if elemento is None:
            return None
        txt = elemento.findtext(".//k:coordinates", default="", namespaces=ns)
        if not txt:
            txt = elemento.findtext(".//coordinates", default="")
        pontos = []
        for item in txt.split():
            valores = item.split(",")
            if len(valores) < 2:
                continue
            try:
                longitude = float(valores[0])
                latitude = float(valores[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(longitude) and math.isfinite(latitude):
                pontos.append([longitude, latitude])
        if len(pontos) < 3:
            return None
        if pontos[0] != pontos[-1]:
            pontos.append(pontos[0])
        return pontos if len(pontos) >= 4 else None

    feicoes = []
    for placa in placas:
        dados = {}
        for item in placa.findall(".//k:SimpleData", ns):
            dados[item.get("name", "")] = (item.text or "").strip()
        if not dados:
            for item in placa.findall(".//SimpleData"):
                dados[item.get("name", "")] = (item.text or "").strip()
        nome_placa = placa.findtext("k:name", default="", namespaces=ns) or ""
        atributos = {
            "Área": dados.get("NOME") or nome_placa or "Não identificada",
            "Bacia": dados.get("BACIA") or "Não informada",
            "Município": dados.get("MUNICIPIO") or "Não informado",
            "Empresa": dados.get("EMPRESA") or "Não informada",
        }
        poligonos = placa.findall(".//k:Polygon", ns)
        if not poligonos:
            poligonos = placa.findall(".//Polygon")
        for poligono in poligonos:
            exterior = poligono.find("k:outerBoundaryIs", ns)
            if exterior is None:
                exterior = poligono.find("outerBoundaryIs")
            anel_externo = ler_anel(exterior)
            if not anel_externo:
                continue
            aneis = [anel_externo]
            interiores = poligono.findall("k:innerBoundaryIs", ns)
            if not interiores:
                interiores = poligono.findall("innerBoundaryIs")
            for interior in interiores:
                anel = ler_anel(interior)
                if anel:
                    aneis.append(anel)
            feicoes.append({
                "type": "Feature",
                "properties": atributos.copy(),
                "geometry": {"type": "Polygon", "coordinates": aneis},
            })

    return {"type": "FeatureCollection", "features": feicoes}


# ============================================================
# CLASSIFICAÇÃO E PALETA DE CORES DA PRESSÃO
# ============================================================
COR_SEM_PRESSAO = "#FF5C60"       # Vermelho
COR_BAIXA_PRESSAO = "#F8DC00"     # Amarelo
COR_EM_ATENCAO = "#FF8FE1"       # Rosa
COR_NORMAL = "#2563EB"           # Azul
COR_ALTA_PRESSAO = "#22C55E"     # Verde

# Cinco intervalos mutuamente exclusivos: 5 MCA pertence a Baixa Pressão;
# 10 MCA pertence a Normal; 15 MCA ainda pertence a Normal.
BP_FAIXAS_PRESSAO = (
    "Sem Pressão (0 MCA)",
    "Baixa Pressão (> 0 e ≤ 5 MCA)",
    "Em Atenção (> 5 e < 10 MCA)",
    "Normal (≥ 10 e ≤ 15 MCA)",
    "Alta Pressão (> 15 MCA)",
)


def classificar_pressao(pressao):
    try:
        valor = float(pressao)
    except (TypeError, ValueError):
        valor = 0.0

    if valor == 0:
        return "Sem Pressão", COR_SEM_PRESSAO
    if valor <= 5:
        return "Baixa Pressão", COR_BAIXA_PRESSAO
    if valor < 10:
        return "Em Atenção", COR_EM_ATENCAO
    if valor <= 15:
        return "Normal", COR_NORMAL
    return "Alta Pressão", COR_ALTA_PRESSAO


def bp_filtrar_por_faixas(base: pd.DataFrame, selecionadas) -> pd.DataFrame:
    """União das faixas selecionadas; sem seleção, exibe todas as medições.

    Não altera os dados originais nem a pressão informada pelo usuário.
    """
    if base.empty or not selecionadas:
        return base
    p = pd.to_numeric(base["Pressao_MCA"], errors="coerce")
    mascara = pd.Series(False, index=base.index)
    faixas = set(selecionadas)
    if BP_FAIXAS_PRESSAO[0] in faixas:
        mascara |= p.eq(0)
    if BP_FAIXAS_PRESSAO[1] in faixas:
        mascara |= p.gt(0) & p.le(5)
    if BP_FAIXAS_PRESSAO[2] in faixas:
        mascara |= p.gt(5) & p.lt(10)
    if BP_FAIXAS_PRESSAO[3] in faixas:
        mascara |= p.ge(10) & p.le(15)
    if BP_FAIXAS_PRESSAO[4] in faixas:
        mascara |= p.gt(15)
    return base.loc[mascara].copy()


# ============================================================
# CONEXÃO COM GOOGLE SHEETS — ADMIN/USUÁRIO
# ============================================================
@st.cache_resource
def conectar_google_sheets():

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    credentials_dict = json.loads(
        st.secrets["gcp_json"]
    )

    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=scopes
    )

    gc = gspread.authorize(credentials)

    sh = gc.open_by_key(
        SPREADSHEET_ID
    )

    try:

        ws = sh.worksheet(
            "baixa_pressao"
        )

    except Exception:

        try:

            ws = sh.add_worksheet(
                title="baixa_pressao",
                rows="1000",
                cols="20"
            )

        except Exception:

            ws = sh.sheet1

    try:

        dados_iniciais = ws.get_all_values()

        if not dados_iniciais or len(dados_iniciais) == 0:

            ws.append_row([
                "ID",
                "Data",
                "Municipio",
                "Bairro",
                "Latitude",
                "Longitude",
                "Pressao_MCA",
                "Observacao",
                "Matricula"
            ])

        else:

            cabecalho_atual = [
                str(c).strip()
                for c in dados_iniciais[0]
            ]

            # Garante a coluna Observacao
            if (
                len(cabecalho_atual) >= 7
                and "Observacao" not in cabecalho_atual
                and "Observação" not in cabecalho_atual
            ):

                ws.update(
                    "H1",
                    [["Observacao"]]
                )

            # Garante a coluna Matricula na coluna I.
            if (
                "Matricula" not in cabecalho_atual
                and "Matrícula" not in cabecalho_atual
            ):

                ws.update(
                    "I1",
                    [["Matricula"]]
                )

    except Exception:
        pass

    return ws


# ============================================================
# CONEXÃO GOOGLE SHEETS — OPERADOR
# SOMENTE LEITURA
# ============================================================
@st.cache_resource
def conectar_google_sheets_leitura():

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]

    credentials_dict = json.loads(
        st.secrets["gcp_json"]
    )

    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=scopes
    )

    gc = gspread.authorize(credentials)

    sh = gc.open_by_key(
        SPREADSHEET_ID
    )

    ws = sh.worksheet(
        "baixa_pressao"
    )

    return ws


# ============================================================
# SELEÇÃO DA CONEXÃO DE ACORDO COM O PERFIL
# ============================================================
try:

    if eh_operador:

        worksheet = conectar_google_sheets_leitura()

    else:

        worksheet = conectar_google_sheets()

except Exception as e:

    st.error(
        f"❌ Erro ao conectar com o Google Sheets: {e}"
    )

    st.stop()


# ============================================================
# FUNÇÕES DE DADOS E EXPORTAÇÃO
# ============================================================
def gerar_id() -> str:

    return str(
        uuid.uuid4()
    )[:8].upper()


def normalizar_coluna(nome: str) -> str:

    nome = str(
        nome
    ).strip().lower()

    mapeamento = {

        "id": "ID",

        "data": "Data",

        "municipio": "Municipio",
        "município": "Municipio",

        "bairro": "Bairro",

        "latitude": "Latitude",
        "lat": "Latitude",

        "longitude": "Longitude",
        "lon": "Longitude",
        "long": "Longitude",

        "pressao_mca": "Pressao_MCA",
        "pressão_mca": "Pressao_MCA",
        "pressao": "Pressao_MCA",
        "pressão": "Pressao_MCA",
        "mca": "Pressao_MCA",

        "observacao": "Observacao",
        "observação": "Observacao",
        "obs": "Observacao",

        "matricula": "Matricula",
        "matrícula": "Matricula",
        "matrícula ": "Matricula",
        "mat": "Matricula",

        "cod_protocolo": BP_COLUNA_PROTOCOLO,
        "codigo_protocolo": BP_COLUNA_PROTOCOLO,
        "código protocolo": BP_COLUNA_PROTOCOLO,
        "código do protocolo": BP_COLUNA_PROTOCOLO,
        "cod. protocolo": BP_COLUNA_PROTOCOLO,
        "cód. protocolo": BP_COLUNA_PROTOCOLO,
        "cód. protocolo origem": BP_COLUNA_PROTOCOLO,
        "cod. protocolo origem": BP_COLUNA_PROTOCOLO,
        "cód. protocolo de origem": BP_COLUNA_PROTOCOLO,
    }

    if nome in mapeamento:
        return mapeamento[nome]
    # Nomes aceitos de campos opcionais de origem, sem alterar as nove colunas.
    sem_pontuacao = re.sub(r"[._-]+", " ", nome)
    if ("protocolo" in sem_pontuacao and
            (sem_pontuacao.startswith("cod ") or
             sem_pontuacao.startswith("cód ") or
             sem_pontuacao.startswith("codigo ") or
             sem_pontuacao.startswith("código "))):
        return BP_COLUNA_PROTOCOLO
    return nome.title()


def normalizar_texto_local(
    valor,
    padrao=""
) -> str:

    if valor is None:
        return padrao

    texto = str(
        valor
    ).strip()

    if not texto or texto.lower() in (
        "nan",
        "none",
        "nat"
    ):

        return padrao

    texto = unicodedata.normalize(
        "NFKD",
        texto
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )

    return texto.upper().strip()


def parse_float(
    valor,
    default=None
):

    if valor is None or (
        isinstance(valor, float)
        and pd.isna(valor)
    ):

        return default

    if isinstance(
        valor,
        (int, float)
    ):

        return float(valor)

    try:

        texto = str(
            valor
        ).strip()

        if not texto or texto.lower() in (
            "nan",
            "none",
            "nat",
            ""
        ):

            return default

        texto = (
            texto
            .replace(",", ".")
            .replace(" ", "")
        )

        return float(texto)

    except (
        ValueError,
        TypeError
    ):

        return default


def normalizar_coordenada(
    valor,
    tipo: str = "lat"
) -> Optional[float]:

    num = parse_float(
        valor,
        default=None
    )

    if num is None:
        return None

    if tipo == "lat" and not (
        -90.0 <= num <= 90.0
    ):

        return None

    if tipo == "lon" and not (
        -180.0 <= num <= 180.0
    ):

        return None

    if num == 0.0:
        return None

    return round(
        float(num),
        6
    )


def normalizar_data(valor) -> str:

    if valor is None or (
        isinstance(valor, float)
        and pd.isna(valor)
    ):

        return ""

    if isinstance(
        valor,
        (datetime, date)
    ):

        return valor.strftime(
            "%d/%m/%Y"
        )

    texto = str(
        valor
    ).strip()

    if not texto or texto.lower() in (
        "nan",
        "none",
        "nat",
        ""
    ):

        return ""

    formatos = [
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y/%m/%d"
    ]

    for fmt in formatos:

        try:

            return datetime.strptime(
                texto,
                fmt
            ).strftime(
                "%d/%m/%Y"
            )

        except ValueError:

            continue

    return texto


def carregar_dados() -> pd.DataFrame:

    try:

        valores = worksheet.get_all_values()

    except Exception as e:

        st.error(
            f"Erro ao ler planilha: {e}"
        )

        return pd.DataFrame(
            columns=COLUNAS_PADRAO
        )

    # O histórico é opcional e lido SOMENTE no servidor. Falha do Storage
    # jamais deve ser interpretada como ausência de registros.
    from historico_pressao_supabase import combinar_modulo1, ErroHistorico
    try:
        valores, origens_arquivo = combinar_modulo1(valores)
    except ErroHistorico as exc:
        st.error(f"Histórico indisponível: {exc}")
        st.stop()

    if not valores or len(valores) < 2:

        return pd.DataFrame(
            columns=COLUNAS_PADRAO
        )

    cabecalhos_raw = valores[0]

    cabecalhos = [
        normalizar_coluna(c)
        for c in cabecalhos_raw
    ]

    registros = []

    for idx_origem, linha in enumerate(valores[1:]):

        if not any(
            str(c).strip()
            for c in linha
        ):

            continue

        reg = {}

        for i, col in enumerate(
            cabecalhos
        ):

            reg[col] = (
                linha[i]
                if i < len(linha)
                else ""
            )

        reg["_arquivo_historico"] = bool(origens_arquivo[idx_origem])
        registros.append(reg)

    if not registros:

        return pd.DataFrame(
            columns=COLUNAS_PADRAO
        )

    df = pd.DataFrame(
        registros
    )

    for col in COLUNAS_PADRAO + [BP_COLUNA_PROTOCOLO]:

        if col not in df.columns:
            df[col] = ""

    def limpar_id(v):

        s = str(v).strip()

        if not s or s.lower() in (
            "nan",
            "none",
            ""
        ):

            return gerar_id()

        return s

    df["ID"] = df["ID"].apply(
        limpar_id
    )

    df["Data"] = df["Data"].apply(
        normalizar_data
    )

    df["Municipio"] = df["Municipio"].apply(
        lambda x:
        normalizar_texto_local(
            x,
            padrao="TERESINA"
        )
    )

    df["Bairro"] = df["Bairro"].apply(
        lambda x:
        normalizar_texto_local(
            x,
            padrao=""
        )
    )

    df["Latitude"] = df[
        "Latitude"
    ].apply(
        lambda x:
        normalizar_coordenada(
            x,
            "lat"
        )
    )

    df["Longitude"] = df[
        "Longitude"
    ].apply(
        lambda x:
        normalizar_coordenada(
            x,
            "lon"
        )
    )

    df["Pressao_MCA"] = df[
        "Pressao_MCA"
    ].apply(
        lambda x:
        parse_float(
            x,
            0.0
        ) or 0.0
    )

    df["Observacao"] = (
        df["Observacao"]
        .astype(str)
        .str.strip()
        .replace({
            "nan": "",
            "None": ""
        })
    )

    df["Matricula"] = (
        df["Matricula"]
        .astype(str)
        .str.strip()
        .replace({
            "nan": "",
            "None": ""
        })
    )

    df = df[
        df["Bairro"]
        .astype(str)
        .str.strip()
        != ""
    ]

    # Campo opcional J (ou nome equivalente) é apenas lido e não é escrito.
    df[BP_COLUNA_PROTOCOLO] = (
        df[BP_COLUNA_PROTOCOLO].fillna("").astype(str).str.strip()
        .replace({"nan": "", "None": ""})
    )

    return df[
        COLUNAS_PADRAO + [BP_COLUNA_PROTOCOLO, "_arquivo_historico"]
    ].reset_index(
        drop=True
    )


def limpar_cache():

    st.cache_data.clear()


# ============================================================
# FUNÇÕES DE ALTERAÇÃO — TODAS PROTEGIDAS
# ============================================================
def adicionar_ponto(
    municipio: str,
    matricula: str,
    bairro: str,
    lat: float,
    lon: float,
    pressao: float,
    data_str: str,
    observacao: str
):

    if not pode_executar_acao("1", "criar"):
        st.error("Sem permissão para esta operação.")
        return None
    if not pode_alterar_dados:
        st.error(
            "Seu perfil não possui permissão para cadastrar pontos."
        )
        return None

    novo_id = gerar_id()

    municipio_normalizado = normalizar_texto_local(
        municipio,
        padrao="TERESINA"
    )

    bairro_normalizado = normalizar_texto_local(
        bairro,
        padrao=""
    )

    matricula_normalizada = str(
        matricula or ""
    ).strip()

    worksheet.append_row([
        str(novo_id),
        str(data_str),
        municipio_normalizado,
        bairro_normalizado,
        float(lat),
        float(lon),
        float(pressao),
        str(observacao),
        matricula_normalizada
    ])

    time.sleep(0.3)

    limpar_cache()

    return novo_id


def adicionar_lote_seguro(linhas_dados: list) -> int:
    if not pode_executar_acao("1", "importar"):
        st.error("Sem permissão para esta operação.")
        return 0
    """Grava pontos novos seguindo exatamente a regra da automação."""
    if not pode_alterar_dados:
        st.error("Seu perfil não possui permissão para importar dados.")
        return 0
    if not linhas_dados:
        return 0

    # O identificador da atividade diferencia medições feitas no mesmo local.
    # Sem ID da atividade, compara data, local, pressão e matrícula.
    registros_sheet = worksheet.get_all_values()
    indice = IndiceDuplicidades(registros_sheet[1:])
    linhas_novas, repetidas, _ = indice.selecionar(linhas_dados)
    if not linhas_novas:
        return 0

    registros_formatados = []
    for linha in linhas_novas:
        (id_v, data, municipio, bairro, lat, lon, pressao, obs, matricula) = linha
        registros_formatados.append([
            str(id_v), str(data),
            normalizar_texto_local(municipio, padrao="TERESINA"),
            normalizar_texto_local(bairro),
            float(lat), float(lon), float(pressao),
            str(obs), str(matricula).strip(),
        ])
    # Sem reformatar datas automaticamente no Google Sheets.
    worksheet.append_rows(registros_formatados, value_input_option="RAW")
    time.sleep(0.3)
    limpar_cache()
    return len(registros_formatados)


def atualizar_ponto(
    id_registro: str,
    municipio: str,
    matricula: str,
    bairro: str,
    lat: float,
    lon: float,
    pressao: float,
    data_str: str,
    observacao: str
) -> bool:

    if not pode_executar_acao("1", "editar"):
        st.error("Sem permissão para esta operação.")
        return False
    if not pode_alterar_dados:
        st.error(
            "Seu perfil não possui permissão para editar pontos."
        )
        return False

    try:

        celula = worksheet.find(
            str(id_registro)
        )

        if celula is None:
            return False

        linha = celula.row

        worksheet.update(
            f"A{linha}:I{linha}",
            [[
                str(id_registro),
                str(data_str),
                normalizar_texto_local(
                    municipio,
                    padrao="TERESINA"
                ),
                normalizar_texto_local(
                    bairro,
                    padrao=""
                ),
                float(lat),
                float(lon),
                float(pressao),
                str(observacao),
                str(matricula).strip()
            ]]
        )

        time.sleep(0.3)

        limpar_cache()

        return True

    except Exception as e:

        st.error(
            f"Erro ao atualizar: {e}"
        )

        return False


def excluir_ponto(
    id_registro: str
) -> bool:

    if not pode_executar_acao("1", "excluir"):
        st.error("Sem permissão para esta operação.")
        return False
    if not pode_alterar_dados:
        st.error(
            "Seu perfil não possui permissão para excluir pontos."
        )
        return False

    try:

        celula = worksheet.find(
            str(id_registro)
        )

        if celula is None:
            return False

        worksheet.delete_rows(
            celula.row
        )

        time.sleep(0.3)

        limpar_cache()

        return True

    except Exception:

        return False


def data_para_str(
    d: date
) -> str:

    return d.strftime(
        "%d/%m/%Y"
    )


def gerar_kml(df):

    kml = simplekml.Kml()

    for _, row in df.iterrows():

        lat = row.get(
            "Latitude"
        )

        lon = row.get(
            "Longitude"
        )

        if pd.notnull(lat) and pd.notnull(lon):

            try:

                obs_visivel = bp_observacao_visivel(
                    row.get("Observacao", "")
                )
                obs_text = (
                    f"\nObservação: {obs_visivel}" if obs_visivel else ""
                )

                matricula_text = (
                    f"\nMatrícula: "
                    f"{row.get('Matricula', '')}"
                    if str(
                        row.get(
                            "Matricula",
                            ""
                        )
                    ).strip()
                    else ""
                )

                kml.newpoint(
                    name=str(
                        row.get(
                            "ID",
                            "Ponto"
                        )
                    ),
                    description=(
                        f"Município: "
                        f"{row.get('Municipio', '')}\n"
                        f"Bairro: "
                        f"{row.get('Bairro', '')}\n"
                        f"Matrícula: "
                        f"{row.get('Matricula', '')}\n"
                        f"Pressão: "
                        f"{row.get('Pressao_MCA', '')} MCA"
                        f"{obs_text}"
                    ),
                    coords=[
                        (
                            float(lon),
                            float(lat)
                        )
                    ]
                )

            except (
                ValueError,
                TypeError
            ):

                continue

    return kml.kml()



# ============================================================
# RELATÓRIO PDF DE BAIXA PRESSÃO — USA SOMENTE df_filtrado
# Mantém independente a estrutura existente do Módulo 1.
# ============================================================
from reportlab.lib import colors as bp_rel_colors
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4 as BP_REL_A4, landscape as bp_rel_landscape
from reportlab.lib.units import cm as bp_rel_cm
from reportlab.lib.styles import ParagraphStyle as BP_REL_ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate as BP_REL_Doc, Paragraph as BP_REL_Paragraph,
    Spacer as BP_REL_Spacer, Table as BP_REL_Table, TableStyle as BP_REL_TableStyle,
    Image as BP_REL_Image, PageBreak as BP_REL_PageBreak, KeepTogether as BP_REL_KeepTogether)
from reportlab.graphics.shapes import Drawing, Circle, String, Rect, Line, PolyLine
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xml.sax.saxutils import escape as bp_rel_xml_escape
from pathlib import Path as BP_REL_Path
import hashlib as bp_rel_hashlib
import shutil as bp_rel_shutil
import tempfile as bp_rel_tempfile
import math

# Cores e faixas iguais nos dashboards do Streamlit e no PDF.
BP_REL_CORES_FAIXAS_PRESSAO = {
    "0 mca": "#e64e62",
    ">5 a 10 mca": "#e6bc34",
    ">0 a 5 mca": "#f3a53a",
    ">10 a 15 mca": "#cf71a9",
    ">15 mca": "#8554b8",
}
BP_REL_FUNDO_GRAFICO_PRESSAO = "#0e1117"


def bp_rel_contagem_faixas(valores):
    """Cinco classes mutuamente exclusivas; 0 mca é medição válida."""
    p = pd.to_numeric(valores, errors="coerce").dropna()
    return {
        "0 mca": int(p.eq(0).sum()),
        ">5 a 10 mca": int(((p > 5) & (p <= 10)).sum()),
        ">0 a 5 mca": int(((p > 0) & (p <= 5)).sum()),
        ">10 a 15 mca": int(((p > 10) & (p <= 15)).sum()),
        ">15 mca": int(p.gt(15).sum()),
    }


def bp_rel_chart_faixas(pressao, fonte="Helvetica"):
    """Gráfico de rosca vetorial, com o visual escuro da referência."""
    from reportlab.graphics.shapes import Wedge

    largura, altura = 355, 267
    d = Drawing(largura, altura)
    bg = colors.HexColor(BP_REL_FUNDO_GRAFICO_PRESSAO)
    branco = colors.HexColor("#f7f8fb")
    d.add(Rect(0, 0, largura, altura, fillColor=bg, strokeColor=None))
    d.add(String(13, 241, "Distribuição por faixa de pressão",
                 fontName=fonte, fontSize=11.7, fillColor=branco))

    grupos = bp_rel_contagem_faixas(pressao["_pressao"])
    total = sum(grupos.values())
    centro_x, centro_y, raio_externo, raio_interno = 177, 146, 78, 43
    # O ângulo inicial posiciona a primeira faixa do lado direito, como na imagem.
    angulo = -180 * grupos["0 mca"] / max(total, 1)
    for nome, qtd in grupos.items():
        if qtd <= 0 or not total:
            continue
        abertura = 360 * qtd / total
        inicio = angulo
        fim = angulo + min(abertura, 359.999)
        cor = colors.HexColor(BP_REL_CORES_FAIXAS_PRESSAO[nome])
        d.add(Wedge(centro_x, centro_y, raio_externo, inicio, fim,
                    fillColor=cor, strokeColor=None))
        if abertura >= 28:
            ang_medio = math.radians(inicio + abertura / 2)
            rr = (raio_externo + raio_interno) / 2
            texto = f"{100 * qtd / total:.0f}%"
            x = centro_x + rr * math.cos(ang_medio)
            y = centro_y + rr * math.sin(ang_medio) - 4
            largura_texto = pdfmetrics.stringWidth(texto, fonte, 10)
            cor_texto = (bg if nome == ">5 a 10 mca" else branco)
            d.add(String(x - largura_texto / 2, y, texto,
                         fontName=fonte, fontSize=10, fillColor=cor_texto))
        angulo += abertura
    d.add(Circle(centro_x, centro_y, raio_interno, fillColor=bg, strokeColor=None))

    # Duas linhas de legenda evitam fontes minúsculas no PDF A4 paisagem.
    legenda = list(BP_REL_CORES_FAIXAS_PRESSAO.items())
    for i, (nome, hex_cor) in enumerate(legenda):
        linha, coluna = divmod(i, 3)
        x, y = 13 + coluna * 116, 43 - linha * 20
        d.add(Rect(x, y - 1, 9, 9, fillColor=colors.HexColor(hex_cor), strokeColor=None))
        d.add(String(x + 15, y, nome, fontName=fonte,
                     fontSize=7.65, fillColor=branco))
    return d


def bp_rel_chart_bairros(bairros, fonte="Helvetica"):
    """Média por bairro: barras horizontais azuis, maior média no topo."""
    largura, altura = 355, 267
    d = Drawing(largura, altura)
    bg = colors.HexColor(BP_REL_FUNDO_GRAFICO_PRESSAO)
    branco = colors.HexColor("#f7f8fb")
    d.add(Rect(0, 0, largura, altura, fillColor=bg, strokeColor=None))
    d.add(String(12, 241, "Média das aferições por bairro",
                 fontName=fonte, fontSize=11.7, fillColor=branco))

    if bairros is None or bairros.empty:
        d.add(String(18, 132, "Sem aferições no período", fontName=fonte,
                     fontSize=10, fillColor=branco))
        return d

    # Mostra os dez bairros de menor média (prioridade operacional), com
    # barras visualmente ordenadas da maior para a menor, como na referência.
    amostra = (bairros.sort_values(["Média (mca)", "Medições"],
                                   ascending=[True, False]).head(10)
               .sort_values(["Média (mca)", "Medições"],
                            ascending=[False, False]).copy())
    maior = max(float(amostra["Média (mca)"].max()), 0)
    # Limite superior simples, sem distorcer a escala de pressão.
    escala = max(2, int(math.ceil(maior / 2.0) * 2))
    x0, xmax = 148, 343
    largura_grafico = xmax - x0
    y_topo, y_base = 214, 36
    qt = len(amostra)
    passo = min(34, (y_topo - y_base) / max(qt, 1))
    altura_barra = min(19, passo * 0.65)

    for tick in range(0, escala + 1, 2):
        x = x0 + largura_grafico * tick / escala
        d.add(Line(x, 30, x, 225, strokeColor=colors.HexColor("#243040"), strokeWidth=.6))
        texto = str(tick)
        d.add(String(x - pdfmetrics.stringWidth(texto, fonte, 7.4) / 2,
                     16, texto, fontName=fonte, fontSize=7.4,
                     fillColor=colors.HexColor("#e1e7f0")))
    d.add(String(238, 3, "Média (mca)", fontName=fonte, fontSize=8,
                 fillColor=branco))

    for i, (_, r) in enumerate(amostra.iterrows()):
        y = y_topo - i * passo
        nome = f"{r['Bairro']} / {r['Município']}".upper()
        original = nome
        tamanho = 7.0
        while len(nome) > 4 and pdfmetrics.stringWidth(nome, fonte, tamanho) > 137:
            nome = nome[:-1]
        if nome != original:
            nome = nome.rstrip() + "…"
        d.add(String(7, y - 3, nome, fontName=fonte,
                     fontSize=tamanho, fillColor=branco))
        media = max(0.0, float(r["Média (mca)"]))
        if media > 0:
            d.add(Rect(x0, y - altura_barra / 2, largura_grafico * media / escala,
                       altura_barra, fillColor=colors.HexColor("#327cb7"), strokeColor=None))
    return d



def bp_rel_preparar(df):
    """A entrada precisa ser o DataFrame JÁ FILTRADO pelo Módulo 1."""
    base=df.copy()
    base["_pressao"]=pd.to_numeric(base["Pressao_MCA"],errors="coerce")
    base=base[base["_pressao"].notna() & (base["_pressao"]>=0)].copy()
    base["_municipio"]=base["Municipio"].fillna("").astype(str).str.strip()
    base["_bairro"]=base["Bairro"].fillna("").astype(str).str.strip()
    base["_data"]=pd.to_datetime(base["Data"],format="%d/%m/%Y",errors="coerce")
    return base


def bp_rel_resumo_bairros(base):
    """Média amostral por bairro e município, sem declarar valor representativo da rede."""
    cols=["Município","Bairro","Medições","Matrículas","0 mca","Até 5 mca",
          "5–10 mca","10–15 mca",">15 mca","Mín. (mca)","Média (mca)",
          "Mediana (mca)","Máx. (mca)","% até 5 mca"]
    if base.empty:return pd.DataFrame(columns=cols)
    linhas=[]
    for (mun,bairro),grupo in base.groupby(["_municipio","_bairro"],dropna=False):
        p=grupo["_pressao"].dropna()
        if p.empty:continue
        mat=grupo["Matricula"].fillna("").astype(str).str.strip()
        linhas.append({"Município":mun or "Não informado","Bairro":bairro or "Não informado",
           "Medições":len(p),"Matrículas":mat[~mat.isin(["","nan","None"])].nunique(),
           "0 mca":int(p.eq(0).sum()),"Até 5 mca":int(p.le(5).sum()),
           "5–10 mca":int(((p>5)&(p<=10)).sum()),
           "10–15 mca":int(((p>10)&(p<=15)).sum()),">15 mca":int(p.gt(15).sum()),
           "Mín. (mca)":round(float(p.min()),2),"Média (mca)":round(float(p.mean()),2),
           "Mediana (mca)":round(float(p.median()),2),"Máx. (mca)":round(float(p.max()),2),
           "% até 5 mca":round(float(p.le(5).mean()*100),1)})
    return pd.DataFrame(linhas,columns=cols).sort_values(
        ["% até 5 mca","Até 5 mca","Medições","Bairro"],
        ascending=[False,False,False,True]).reset_index(drop=True)


def bp_rel_mapa(base):
    """Mapa de pontos, incluindo também as medições entre 10 e 15 MCA."""
    validos=base.dropna(subset=["Latitude","Longitude","_pressao"]).copy()
    if validos.empty:return None
    mapa=folium.Map(location=[float(validos["Latitude"].mean()),float(validos["Longitude"].mean())],
        zoom_start=12,tiles="OpenStreetMap",control_scale=True,zoom_control=False)
    posicoes=[]
    for _,r in validos.iterrows():
        lat=float(r["Latitude"]);lon=float(r["Longitude"])
        if not (-90<=lat<=90 and -180<=lon<=180):continue
        press=float(r["_pressao"])
        cor="#e64e62" if press==0 else "#f3a53a" if press<=5 else "#e6bc34" if press<=10 else "#cf71a9" if press<=15 else "#8554b8"
        pop=(f"<b>{escape(str(r['Bairro']))} / {escape(str(r['Municipio']))}</b><br>"
             f"Pressão: <b>{press:g} mca</b><br>"
             f"Data: {escape(str(r['Data']))}<br>"
             f"Matrícula: {escape(str(r.get('Matricula','')))}")
        folium.CircleMarker([lat,lon],radius=7,color="#ffffff",weight=1.3,
            fill=True,fill_color=cor,fill_opacity=.97,
            tooltip=f"{r['Bairro']}: {press:g} mca",popup=folium.Popup(pop,max_width=360)).add_to(mapa)
        posicoes.append((lat,lon))
    if not posicoes:return None
    if len(posicoes)>1:mapa.fit_bounds(posicoes,padding=(45,45),max_zoom=14)
    from branca.element import Element
    mapa.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
        'background:white;padding:8px 11px;border:1px solid #ccd5e0;border-radius:5px;'
        'font:12px Arial;color:#24364b"><b>Medições de pressão</b><br>'
        '<span style="color:#e64e62">●</span> 0 mca &nbsp; '
        '<span style="color:#f3a53a">●</span> &gt;0–5 &nbsp; '
        '<span style="color:#e6bc34">●</span> &gt;5–10 &nbsp; '
        '<span style="color:#cf71a9">●</span> &gt;10–15 &nbsp; '
        '<span style="color:#8554b8">●</span> &gt;15 mca</div>'))
    return mapa


def bp_rel_imagem_mapa(mapa, largura=1280, altura=650):
    """Fotografa tiles reais via Chromium/Playwright, sem gerar mapas fictícios."""
    from playwright.sync_api import sync_playwright
    html=mapa.get_root().render().replace('</head>',
        f'<style>html,body{{margin:0;width:{largura}px;height:{altura}px;overflow:hidden}}'
        f'.folium-map{{width:{largura}px !important;height:{altura}px !important}}</style></head>',1)
    with bp_rel_tempfile.TemporaryDirectory(prefix="coi_pressao_pdf_") as diretorio:
        caminho=BP_REL_Path(diretorio)/"mapa.html"
        caminho.write_text(html,encoding="utf-8")
        with sync_playwright() as playwright:
            executavel=(os.environ.get("CHROMIUM_PATH") or bp_rel_shutil.which("chromium")
               or bp_rel_shutil.which("chromium-browser") or bp_rel_shutil.which("google-chrome"))
            opcoes={"headless":True,"args":["--no-sandbox","--disable-dev-shm-usage"]}
            if executavel:opcoes["executable_path"]=executavel
            navegador=playwright.chromium.launch(**opcoes)
            try:
                pagina=navegador.new_page(viewport={"width":largura,"height":altura},device_scale_factor=1.2)
                pagina.goto(caminho.as_uri(),wait_until="load",timeout=45000)
                pagina.wait_for_function("typeof L !== 'undefined' && document.querySelector('.leaflet-map-pane') !== null",timeout=30000)
                pagina.wait_for_timeout(2100)
                if pagina.locator('.leaflet-tile-loaded').count()==0:
                    raise RuntimeError("O mapa-base não carregou no Chromium. Verifique a rede.")
                return pagina.locator('.folium-map').first.screenshot(type="png")
            finally:navegador.close()


def bp_rel_mapa_fallback(base, largura=1280, altura=650):
    """Plano geográfico esquemático com coordenadas reais caso Chromium/tiles falhem.

    Usa somente coordenadas dos pontos selecionados; jamais simula ruas/mapa-base.
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont
    pontos=base.dropna(subset=["Latitude","Longitude","_pressao"]).copy()
    imagem=PILImage.new("RGB",(largura,altura),"#f1f5fa")
    desenho=ImageDraw.Draw(imagem)
    try:
        font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",16)
        forte=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",19)
    except OSError:
        font=ImageFont.load_default(); forte=font
    desenho.text((30,19),"LOCALIZAÇÃO DAS MEDIÇÕES — MAPA-BASE INDISPONÍVEL",font=forte,fill="#193c65")
    desenho.text((30,50),"Representação por coordenadas geográficas; sem ruas ou limites cartográficos",font=font,fill="#60748b")
    x0,y0,x1,y1=55,105,largura-55,altura-100
    desenho.rectangle((x0,y0,x1,y1),outline="#a1b3c5",width=2,fill="#ffffff")
    if not pontos.empty:
        lats=pontos["Latitude"].astype(float).tolist()
        lons=pontos["Longitude"].astype(float).tolist()
        lat_c=(min(lats)+max(lats))/2
        # Pondera o eixo horizontal para aproximar escalas lineares em metros.
        corrigir=max(.2,math.cos(math.radians(lat_c)))
        xs=[v*corrigir for v in lons]
        minx,maxx=min(xs),max(xs);miny,maxy=min(lats),max(lats)
        meio_x=(minx+maxx)/2; meio_y=(miny+maxy)/2
        spanx=max(maxx-minx,.02);spany=max(maxy-miny,.02)
        span=max(spanx/(x1-x0),spany/(y1-y0))*1.18
        for idx,(_,r) in enumerate(pontos.iterrows()):
            px=(x0+x1)/2+((float(r["Longitude"])*corrigir-meio_x)/span)
            py=(y0+y1)/2-((float(r["Latitude"])-meio_y)/span)
            press=float(r["_pressao"])
            cor="#e64e62" if press==0 else "#f3a53a" if press<=5 else "#e6bc34" if press<=10 else "#cf71a9" if press<=15 else "#8554b8"
            raio=9
            desenho.ellipse((px-raio,py-raio,px+raio,py+raio),fill=cor,outline="#ffffff",width=2)
    legenda=[("#e64e62","0 mca"),("#f3a53a",">0–5 mca"),
             ("#e6bc34",">5–10 mca"),("#cf71a9",">10–15 mca"),("#8554b8",">15 mca")]
    for j,(cor,rotulo) in enumerate(legenda):
        ex=60+j*230
        desenho.ellipse((ex,altura-63,ex+14,altura-49),fill=cor)
        desenho.text((ex+22,altura-65),rotulo,font=font,fill="#193c65")
    buffer_img=io.BytesIO(); imagem.save(buffer_img,format="PNG")
    return buffer_img.getvalue()



# ============================================================
# RELATÓRIO — ASSOCIAÇÃO ESPACIAL DAS MEDIÇÕES AOS POLÍGONOS SANEAR
# ============================================================
def bp_rel_ponto_no_anel(lon, lat, anel):
    """Ray casting lon/lat; bordas do anel são consideradas incluídas."""
    dentro = False
    for i in range(len(anel) - 1):
        ax, ay = anel[i][0], anel[i][1]
        bx, by = anel[i+1][0], anel[i+1][1]
        dx, dy = bx - ax, by - ay
        cruz = (lon - ax) * dy - (lat - ay) * dx
        if (abs(cruz) <= 1e-11 and
                min(ax,bx)-1e-11 <= lon <= max(ax,bx)+1e-11 and
                min(ay,by)-1e-11 <= lat <= max(ay,by)+1e-11):
            return True
        if (ay > lat) != (by > lat):
            intercepto = ax + (lat-ay) * dx / dy
            if lon < intercepto:
                dentro = not dentro
    return dentro


def bp_rel_ponto_na_geometria(lon, lat, geometria):
    """Polígonos com furos e multipolígonos; sem buffers ou inferências por bairro."""
    tipo = geometria.get("type", "")
    conjuntos = geometria.get("coordinates", [])
    if tipo == "Polygon":
        conjuntos = [conjuntos]
    elif tipo != "MultiPolygon":
        return False
    for aneis in conjuntos:
        if not aneis or not bp_rel_ponto_no_anel(lon, lat, aneis[0]):
            continue
        if not any(bp_rel_ponto_no_anel(lon, lat, furo) for furo in aneis[1:]):
            return True
    return False


def bp_rel_selecionar_sanear(base, geojson):
    """Retorna pontos no Sanear e índices das feições correspondentes.

    Um registro será listado uma única vez mesmo ao interceptar polígonos
    sobrepostos; todas as áreas correspondentes são desenhadas no mapa.
    """
    feicoes = geojson.get("features", [])
    limites = []
    for feat in feicoes:
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        if geom.get("type") == "Polygon":
            exteriores = [coords[0]] if coords else []
        elif geom.get("type") == "MultiPolygon":
            exteriores = [pol[0] for pol in coords if pol]
        else:
            exteriores = []
        pontos = [p for anel in exteriores for p in anel]
        if not pontos:
            limites.append(None)
            continue
        limites.append((min(p[0] for p in pontos), min(p[1] for p in pontos),
                        max(p[0] for p in pontos), max(p[1] for p in pontos)))
    selecionados = []
    feicoes_usadas = set()
    for idx, registro in base.iterrows():
        try:
            lat = float(registro["Latitude"])
            lon = float(registro["Longitude"])
        except (ValueError, TypeError, KeyError):
            continue
        if not math.isfinite(lat) or not math.isfinite(lon):
            continue
        acertos = []
        for pos, (feat, lim) in enumerate(zip(feicoes, limites)):
            if lim is None or not (lim[0] <= lon <= lim[2] and lim[1] <= lat <= lim[3]):
                continue
            if bp_rel_ponto_na_geometria(lon, lat, feat.get("geometry", {})):
                acertos.append(pos)
        if acertos:
            selecionados.append(idx)
            feicoes_usadas.update(acertos)
    # Índices preservados até o recorte; nunca multiplica o número de medições.
    return base.loc[selecionados].copy(), sorted(feicoes_usadas)


def bp_rel_mapa_sanear(pontos, geojson, indices):
    """Mapa do anexo Sanear: feições reais + posições das medições associadas."""
    feicoes = [geojson["features"][i] for i in indices]
    coords = []
    for feat in feicoes:
        geom = feat["geometry"]
        rings = ([geom["coordinates"]] if geom["type"] == "Polygon"
                 else geom["coordinates"] if geom["type"] == "MultiPolygon" else [])
        for pol in rings:
            for lon, lat, *resto in pol[0]:
                coords.append((lat, lon))
    for _, r in pontos.iterrows():
        coords.append((float(r["Latitude"]), float(r["Longitude"])))
    if not coords:
        return None
    mapa = folium.Map(location=[sum(p[0] for p in coords)/len(coords),
                                 sum(p[1] for p in coords)/len(coords)],
                       tiles="OpenStreetMap", zoom_start=13,
                       control_scale=True, zoom_control=False)
    folium.GeoJson({"type": "FeatureCollection", "features": feicoes},
        name="Polígonos Sanear com medições",
        style_function=lambda _: {"color": "#087e73", "weight": 3,
                                  "fillColor": "#2DD4BF", "fillOpacity": 0.22},
        tooltip=folium.GeoJsonTooltip(fields=["Área", "Bacia"],
                    aliases=["Área Sanear:", "Bacia:"])).add_to(mapa)
    for _, r in pontos.iterrows():
        press = float(r["_pressao"])
        cor = ("#e64e62" if press == 0 else "#f3a53a" if press <= 5
               else "#e6bc34" if press <= 10 else "#cf71a9"
               if press <= 15 else "#8554b8")
        protocolo = str(r.get(BP_COLUNA_PROTOCOLO, "") or "").strip() or "Não informado"
        msg = (f"<b>{escape(str(r.get('Bairro','')))}</b><br>"
               f"Matrícula: {escape(str(r.get('Matricula','')))}<br>"
               f"Cód. protocolo: {escape(protocolo)}<br>"
               f"Medição: {press:g} mca")
        folium.CircleMarker([float(r["Latitude"]), float(r["Longitude"])],
             radius=7, color="white", weight=1.6, fill=True,
             fill_color=cor, fill_opacity=1,
             popup=folium.Popup(msg, max_width=320),
             tooltip=f"{press:g} mca").add_to(mapa)
    if len(coords) > 1:
        mapa.fit_bounds([[min(p[0] for p in coords), min(p[1] for p in coords)],
                         [max(p[0] for p in coords), max(p[1] for p in coords)]],
                         padding=(38, 38), max_zoom=15)
    mapa.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
        'background:#fff;padding:8px 11px;border:1px solid #ccd5e0;'
        'border-radius:5px;font:12px Arial;color:#24364b">'
        '<b>Sanear — medições nas áreas</b><br>'
        '<span style="color:#087e73">▰</span> Polígono Sanear &nbsp; '
        '<span style="color:#e64e62">●</span> 0 mca &nbsp; '
        '<span style="color:#f3a53a">●</span> &gt;0–5 &nbsp; '
        '<span style="color:#e6bc34">●</span> &gt;5–10 &nbsp; '
        '<span style="color:#cf71a9">●</span> &gt;10–15 &nbsp; '
        '<span style="color:#8554b8">●</span> &gt;15 mca</div>'))
    return mapa


def bp_rel_fallback_sanear(pontos, geojson, indices, largura=1280, altura=650):
    """Alternativa sem mapa-base, com poligonais e pontos em coordenadas reais."""
    from PIL import Image as PILImage, ImageDraw, ImageFont
    imagem = PILImage.new("RGB", (largura, altura), "#eef3f8")
    pincel = ImageDraw.Draw(imagem)
    try:
        fonte = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        negrito = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 19)
    except OSError:
        fonte = ImageFont.load_default()
        negrito = fonte
    pincel.text((35, 18), "SANEAR — LOCALIZAÇÃO DAS MEDIÇÕES", font=negrito, fill="#193c65")
    pincel.text((35, 49), "Mapa geográfico esquemático, sem ruas; posições e limites reais", font=fonte, fill="#60748b")
    shapes = []
    poligonos = [geojson["features"][i]["geometry"] for i in indices]
    for geom in poligonos:
        coords = geom.get("coordinates", [])
        shapes.extend([coords] if geom.get("type") == "Polygon" else coords if geom.get("type") == "MultiPolygon" else [])
    vertices = [(lon, lat) for pol in shapes for anel in pol for lon, lat, *rest in anel]
    vertices.extend((float(r["Longitude"]),float(r["Latitude"])) for _, r in pontos.iterrows())
    if vertices:
        lat_c = sum(lat for _, lat in vertices)/len(vertices)
        escala_x = max(0.2, math.cos(math.radians(lat_c)))
        valores_x = [x*escala_x for x,y in vertices]
        valores_y = [y for x,y in vertices]
        minx,maxx,miny,maxy = min(valores_x),max(valores_x),min(valores_y),max(valores_y)
        mx,my = (minx+maxx)/2, (miny+maxy)/2
        x0,y0,x1,y1 = 60, 95, largura-60, altura-92
        extensao = max((maxx-minx)/(x1-x0), (maxy-miny)/(y1-y0), 1e-8)*1.15
        def pixel(lon,lat):
            return (round((x0+x1)/2+(lon*escala_x-mx)/extensao),
                    round((y0+y1)/2-(lat-my)/extensao))
        pincel.rectangle((x0,y0,x1,y1),outline="#c2d1df",fill="white",width=2)
        for pol in shapes:
            if not pol:
                continue
            pincel.polygon([pixel(*pt[:2]) for pt in pol[0]],
                          fill="#bceae5", outline="#087e73", width=3)
            for furo in pol[1:]:
                pincel.polygon([pixel(*pt[:2]) for pt in furo],
                              fill="#ffffff", outline="#087e73", width=2)
        for _, r in pontos.iterrows():
            x,y=pixel(float(r["Longitude"]),float(r["Latitude"]))
            press=float(r["_pressao"])
            cor=("#e64e62" if press==0 else "#f3a53a" if press<=5
                 else "#e6bc34" if press<=10 else "#cf71a9" if press<=15 else "#8554b8")
            pincel.ellipse((x-8,y-8,x+8,y+8),fill=cor,outline="white",width=3)
    pincel.text((42, altura-58), "Polígonos Sanear: verde-água  |  Marcadores: medições (cores por faixa de pressão)",
                 font=fonte,fill="#193c65")
    buf=io.BytesIO()
    imagem.save(buf,format="PNG")
    return buf.getvalue()


def bp_rel_pdf(df_filtrado,inicio,fim,municipio,bairro,matricula,faixas):
    """PDF gerado EXCLUSIVAMENTE a partir dos registros visíveis após TODOS os filtros."""
    base=bp_rel_preparar(df_filtrado)
    if base.empty:raise ValueError("Nenhuma medição válida corresponde aos filtros atuais.")
    bairros=bp_rel_resumo_bairros(base)
    medidas=base["_pressao"]
    buffer=io.BytesIO()
    doc=BP_REL_Doc(buffer,pagesize=bp_rel_landscape(BP_REL_A4),
        leftMargin=1.2*bp_rel_cm,rightMargin=1.2*bp_rel_cm,
        topMargin=1.2*bp_rel_cm,bottomMargin=1.2*bp_rel_cm)
    regular="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.isfile(regular) and "COIBPRegular" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("COIBPRegular",regular))
        pdfmetrics.registerFont(TTFont("COIBPBold",bold))
        pdfmetrics.registerFontFamily("COIBPRegular",normal="COIBPRegular",bold="COIBPBold")
    fonte="COIBPRegular" if "COIBPRegular" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    titulo=BP_REL_ParagraphStyle("bp_pdf_titulo",fontName=fonte,fontSize=13,leading=17,
                                 textColor=bp_rel_colors.HexColor("#193c65"),spaceAfter=7)
    sub=BP_REL_ParagraphStyle("bp_pdf_sub",parent=titulo,fontSize=10.2,leading=14,spaceBefore=7)
    corpo=BP_REL_ParagraphStyle("bp_pdf_corpo",fontName=fonte,fontSize=8.5,leading=11.8)
    pequeno=BP_REL_ParagraphStyle("bp_pdf_pequeno",parent=corpo,fontSize=7.5,leading=10)
    cabecalho=BP_REL_ParagraphStyle("bp_pdf_cabecalho_tabela",parent=pequeno,fontSize=7.5,
                  leading=10,textColor=bp_rel_colors.HexColor("#193c65"))
    def paragrafo(s,estilo=corpo):
        return BP_REL_Paragraph(bp_rel_xml_escape(str(s if s is not None else "")).replace("\n","<br/>"),estilo)
    def tabela(titulos,linhas,larguras):
        dados=[[paragrafo(t,cabecalho) for t in titulos]] + [
            [paragrafo(x,pequeno) for x in linha] for linha in linhas]
        tab=BP_REL_Table(dados,colWidths=larguras,repeatRows=1,hAlign="LEFT")
        tab.setStyle(BP_REL_TableStyle([
            ("BACKGROUND",(0,0),(-1,0),bp_rel_colors.HexColor("#e1eaf4")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[bp_rel_colors.white,bp_rel_colors.HexColor("#f4f7fb")]),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))
        return tab
    historia=[paragrafo("MONITORAMENTO DE BAIXA PRESSÃO — COI",titulo),
             paragrafo(f"Período: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}  |  Relatório gerado em {datetime.now():%d/%m/%Y %H:%M}",corpo)]
    faixas_texto=", ".join(faixas) if faixas else "Todas as faixas"
    filtros=f"Município: {municipio}  |  Bairro: {bairro}  |  Matrícula: {matricula}  |  Faixas: {faixas_texto}"
    historia.extend([paragrafo("Filtros aplicados: "+filtros,pequeno),BP_REL_Spacer(1,10)])

    def cartao(rotulo,valor,nota):
        t=BP_REL_Table([[paragrafo(rotulo,pequeno)],
           [paragrafo(valor,BP_REL_ParagraphStyle("bp_kpi_"+re.sub(r"\W+","_",rotulo),parent=corpo,
                fontSize=16,leading=19,textColor=bp_rel_colors.HexColor("#193c65")))],
           [paragrafo(nota,pequeno)]],colWidths=[6.25*bp_rel_cm])
        t.setStyle(BP_REL_TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),bp_rel_colors.HexColor("#f1f5fa")),
            ("BOX",(0,0),(-1,-1),.6,bp_rel_colors.HexColor("#dce5ef")),
            ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
        return t
    crit=int(medidas.le(5).sum())
    cards=BP_REL_Table([[
        cartao("MEDIÇÕES",str(len(base)),"aferições filtradas"),
        cartao("BAIRROS",str(len(bairros)),"monitorados no filtro"),
        cartao("MÉDIA",f"{medidas.mean():.2f} mca","média amostral"),
        cartao("ATÉ 5 MCA",f"{crit} ({100*crit/len(base):.0f}%)","inclui leituras de 0 mca"),
        ]],colWidths=[6.55*bp_rel_cm]*4)
    cards.setStyle(BP_REL_TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),2),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    historia.extend([cards,BP_REL_Spacer(1,9)])
    mapa=bp_rel_mapa(base)
    if mapa is not None:
        historia.append(paragrafo("MAPA DAS AFERIÇÕES",sub))
        try:
            imagem=bp_rel_imagem_mapa(mapa)
        except Exception:
            # Falha de browser/mapa-base não deve bloquear o relatório inteiro.
            imagem=bp_rel_mapa_fallback(base)
            historia.append(paragrafo("Mapa-base indisponível: pontos exibidos em plano geográfico esquemático, sem ruas.",pequeno))
        historia.append(BP_REL_Image(io.BytesIO(imagem),width=26.1*bp_rel_cm,height=11.25*bp_rel_cm))
    else:
        historia.append(paragrafo("Não há pontos com coordenadas válidas e visíveis no mapa para os filtros selecionados.",corpo))
    historia.append(BP_REL_Spacer(1,5))
    historia.append(paragrafo("Mapa: vermelho (0 mca), laranja (>0–5 mca), amarelo (>5–10 mca), rosa (>10–15 mca) e roxo (>15 mca).",pequeno))
    historia.append(BP_REL_PageBreak())
    historia.extend([paragrafo("DASHBOARD DE PRESSÃO — REGISTROS FILTRADOS",titulo),
                    paragrafo("Distribuição das medições e média das aferições por bairro (somente registros selecionados).",pequeno),
                    BP_REL_Spacer(1,10)])
    graficos=BP_REL_Table([[
        bp_rel_chart_faixas(base,fonte),bp_rel_chart_bairros(bairros,fonte)
    ]],colWidths=[13.1*bp_rel_cm,13.1*bp_rel_cm],hAlign="LEFT")
    graficos.setStyle(BP_REL_TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),1),("RIGHTPADDING",(0,0),(-1,-1),1),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    historia.append(graficos)
    historia.append(BP_REL_PageBreak())
    historia.append(paragrafo("DIAGNÓSTICO POR BAIRRO",titulo))
    if not bairros.empty:
        críticos=bairros[bairros["Até 5 mca"]>0]
        if not críticos.empty:
            lideres=críticos.sort_values(["Até 5 mca","Medições"],ascending=False).head(4)
            texto="Pontos de atenção: "+"; ".join(
                f"{r['Bairro']} / {r['Município']} ({int(r['Até 5 mca'])} de {int(r['Medições'])} medições até 5 mca)"
                for _,r in lideres.iterrows())+"."
            historia.append(paragrafo(texto,corpo))
            historia.append(BP_REL_Spacer(1,7))
        if (bairros["Medições"]==1).any():
            historia.append(paragrafo("Bairros com uma única aferição: a média representa somente aquele ponto de medição.",pequeno))
            historia.append(BP_REL_Spacer(1,5))
    linhas=[]
    for _,r in bairros.iterrows():
        linhas.append([f"{r['Bairro']} / {r['Município']}",int(r["Medições"]),int(r["Matrículas"]),
              f"{r['Mín. (mca)']:g}",f"{r['Média (mca)']:g}",
              f"{r['Máx. (mca)']:g}",f"{r['Até 5 mca']}/{r['Medições']}",
              f"{r['% até 5 mca']:g}%"])
    historia.append(tabela(["Bairro / Município","n","Matrículas","Mín.","Média",
                           "Máx.","≤5 mca","% ≤5"],linhas,
        [9.6*bp_rel_cm,1.4*bp_rel_cm,2.2*bp_rel_cm,2*bp_rel_cm,
         2*bp_rel_cm,2*bp_rel_cm,3*bp_rel_cm,2.8*bp_rel_cm]))
    historia.extend([BP_REL_Spacer(1,9),paragrafo(
        "As médias refletem somente os registros filtrados, não a pressão de toda a rede ou de todas as residências do bairro.",pequeno)])
    # O anexo SANEAR é independente do checkbox de visualização do mapa.
    # Usa exatamente os pontos que passaram pelos filtros do PDF.
    historia.append(BP_REL_PageBreak())
    historia.extend([paragrafo("MEDIÇÕES EM ÁREAS DE INTERVENÇÃO SANEAR", titulo),
        paragrafo("Cruzamento geográfico entre as coordenadas das medições e "
                   "os polígonos oficiais carregados de sanear.kml.", pequeno),
        BP_REL_Spacer(1, 6)])
    try:
        arquivo_sanear = bp_encontrar_kml_sanear()
        if not arquivo_sanear.is_file():
            historia.append(paragrafo(
                "Camada Sanear indisponível no repositório: "
                "geodados/areas_influencia/sanear.kml. "
                "Nenhum cruzamento foi executado.", corpo))
        else:
            geometrias = bp_carregar_poligonos_sanear(
                str(arquivo_sanear), arquivo_sanear.stat().st_mtime_ns)
            if not geometrias.get("features"):
                historia.append(paragrafo(
                    "A camada Sanear não contém polígonos válidos. "
                    "Nenhum cruzamento foi executado.", corpo))
            else:
                dentro, indices_sanear = bp_rel_selecionar_sanear(base, geometrias)
                historia.append(paragrafo(
                    f"Medições dentro ou sobre o limite dos polígonos: {len(dentro)} "
                    f"de {len(base)} registros filtrados. "
                    f"Áreas intersectadas: {len(indices_sanear)}.", corpo))
                if dentro.empty:
                    historia.append(BP_REL_Spacer(1, 9))
                    historia.append(paragrafo(
                        "Não foram encontradas medições nas áreas Sanear "
                        "para o período e os filtros selecionados.", pequeno))
                else:
                    historia.append(BP_REL_Spacer(1, 6))
                    mapa_sanear = bp_rel_mapa_sanear(
                        dentro, geometrias, indices_sanear)
                    try:
                        imagem_sanear = bp_rel_imagem_mapa(mapa_sanear)
                    except Exception:
                        imagem_sanear = bp_rel_fallback_sanear(
                            dentro, geometrias, indices_sanear)
                        historia.append(paragrafo(
                            "Mapa-base indisponível: a imagem mantém as "
                            "geometrias Sanear e as coordenadas reais, sem ruas.", pequeno))
                    historia.append(BP_REL_Image(io.BytesIO(imagem_sanear),
                            width=26.1*bp_rel_cm, height=10.7*bp_rel_cm))
                    historia.append(BP_REL_Spacer(1, 5))
                    historia.append(paragrafo(
                        "Relação das medições geograficamente contidas nas áreas Sanear.", pequeno))
                    linhas_sanear=[]
                    for _, r in dentro.sort_values(
                            ["_bairro", "_data", "Matricula"],
                            na_position="last").iterrows():
                        protocolo = str(r.get(BP_COLUNA_PROTOCOLO, "") or "").strip()
                        if protocolo.lower() in ("nan", "none", "nat"):
                            protocolo = ""
                        linhas_sanear.append([
                            str(r.get("Matricula", "") or "") or "—",
                            protocolo or "Não informado",
                            str(r.get("Bairro", "") or "") or "—",
                            f"{float(r['_pressao']):g} mca"])
                    historia.append(tabela(
                        ["MATRÍCULA", "CÓD. PROTOCOLO", "BAIRRO", "MEDIÇÃO"],
                        linhas_sanear,
                        [5.5*bp_rel_cm, 6.1*bp_rel_cm, 10.2*bp_rel_cm, 4.4*bp_rel_cm]))
                    if any(linha[1] == "Não informado" for linha in linhas_sanear):
                        historia.append(BP_REL_Spacer(1, 5))
                        historia.append(paragrafo(
                            "Nota: o código de protocolo não está presente em todos os "
                            "registros recebidos pelo Módulo 1. O identificador "
                            "SAI_ATIVIDADE não é utilizado como substituto do protocolo.",
                            pequeno))
    except (ET.ParseError, OSError, ValueError, TypeError, KeyError, IndexError) as erro_sanear:
        historia.append(paragrafo(
            "Não foi possível realizar o cruzamento com a camada Sanear: "
            + str(erro_sanear), pequeno))
    doc.build(historia)
    return buffer.getvalue()



# ============================================================
# SESSION STATE
# ============================================================
hoje = date.today()

if "data_inicial_selecionada" not in st.session_state:
    st.session_state.data_inicial_selecionada = hoje

if "data_final_selecionada" not in st.session_state:
    st.session_state.data_final_selecionada = hoje

if "resumo_upload_pendentes_bp" not in st.session_state:
    st.session_state.resumo_upload_pendentes_bp = None

if "dados_upload_pendentes_bp" not in st.session_state:
    st.session_state.dados_upload_pendentes_bp = None

if "nome_arquivo_pendente_bp" not in st.session_state:
    st.session_state.nome_arquivo_pendente_bp = None

if "file_uploader_key_bp" not in st.session_state:
    st.session_state.file_uploader_key_bp = 0


# ============================================================
# GARANTIA DE ESTADO SEGURO PARA OPERADOR
# ============================================================
if eh_operador:

    st.session_state.dados_upload_pendentes_bp = None
    st.session_state.resumo_upload_pendentes_bp = None

    st.session_state.nome_arquivo_pendente_bp = None


# ============================================================
# DIALOGS
# ============================================================
@st.dialog("➕ Cadastrar Novo Ponto")
def modal_novo_ponto():

    if not pode_alterar_dados:

        st.error(
            "Seu perfil não possui permissão para cadastrar pontos."
        )

        return

    lat_default = LAT_BASE
    lon_default = LON_BASE

    with st.form(
        "form_novo_ponto_modal",
        clear_on_submit=True
    ):

        data_cadastro = st.date_input(
            "Data do Registro",
            value=st.session_state.data_final_selecionada,
            format="DD/MM/YYYY"
        )

        municipio = st.text_input(
            "Município *",
            value="Teresina"
        )

        matricula = st.text_input(
            "Matrícula",
            placeholder="Ex: 123456789"
        )

        bairro = st.text_input(
            "Bairro *",
            placeholder="Ex: Centro"
        )

        c1, c2 = st.columns(2)

        with c1:

            lat = st.text_input(
                "Latitude * (aceita vírgula ou ponto)",
                value=str(lat_default)
            )

        with c2:

            lon = st.text_input(
                "Longitude * (aceita vírgula ou ponto)",
                value=str(lon_default)
            )

        pressao = st.number_input(
            "Pressão (MCA) *",
            format="%.2f",
            value=0.00,
            min_value=0.0,
            step=0.1
        )

        observacao = st.text_input(
            "Observação",
            placeholder="Ex: Válvula fechada"
        )

        enviado = st.form_submit_button(
            "Cadastrar Ponto",
            type="primary",
            use_container_width=True
        )

        if enviado:

            lat_n = normalizar_coordenada(
                lat,
                "lat"
            )

            lon_n = normalizar_coordenada(
                lon,
                "lon"
            )

            if not municipio.strip() or not bairro.strip():

                st.error(
                    "Município e Bairro são obrigatórios."
                )

            elif lat_n is None or lon_n is None:

                st.error(
                    "Coordenadas inválidas. "
                    "Verifique os valores de Latitude e Longitude."
                )

            else:

                novo_id = adicionar_ponto(
                    normalizar_texto_local(
                        municipio,
                        padrao="TERESINA"
                    ),
                    matricula.strip(),
                    normalizar_texto_local(
                        bairro,
                        padrao=""
                    ),
                    lat_n,
                    lon_n,
                    pressao,
                    data_para_str(
                        data_cadastro
                    ),
                    observacao.strip()
                )

                if novo_id:

                    st.success(
                        f"Ponto cadastrado com sucesso! ID: {novo_id}"
                    )

                    st.rerun()


@st.dialog("✏️ Editar Ponto")
def modal_editar_ponto(
    id_registro: str
):

    if not pode_alterar_dados:

        st.error(
            "Seu perfil não possui permissão para editar pontos."
        )

        return

    df_all = carregar_dados()

    df_edit_busca = df_all[
        df_all["ID"] == id_registro
    ]

    if not df_edit_busca.empty:

        reg_edit = df_edit_busca.iloc[0]
        if bool(reg_edit.get("_arquivo_historico", False)):
            st.warning("Registro arquivado no Supabase: consulta somente leitura.")
            return

        try:

            data_parsed = datetime.strptime(
                str(reg_edit["Data"]),
                "%d/%m/%Y"
            ).date()

        except ValueError:

            data_parsed = hoje

        with st.form(
            "form_edicao_modal"
        ):

            data_e = st.date_input(
                "Data do Registro",
                value=data_parsed,
                format="DD/MM/YYYY"
            )

            municipio_e = st.text_input(
                "Município *",
                value=str(
                    reg_edit["Municipio"]
                )
            )

            matricula_e = st.text_input(
                "Matrícula",
                value=str(
                    reg_edit["Matricula"]
                )
            )

            bairro_e = st.text_input(
                "Bairro *",
                value=str(
                    reg_edit["Bairro"]
                )
            )

            c1, c2 = st.columns(2)

            with c1:

                lat_e = st.text_input(
                    "Latitude *",
                    value=str(
                        reg_edit["Latitude"]
                        or LAT_BASE
                    )
                )

            with c2:

                lon_e = st.text_input(
                    "Longitude *",
                    value=str(
                        reg_edit["Longitude"]
                        or LON_BASE
                    )
                )

            pressao_e = st.number_input(
                "Pressão (MCA) *",
                format="%.2f",
                value=float(
                    reg_edit["Pressao_MCA"]
                    or 0.0
                ),
                min_value=0.0,
                step=0.1
            )

            observacao_e = st.text_input(
                "Observação",
                value=bp_observacao_visivel(
                    reg_edit["Observacao"]
                )
            )

            col_salvar, col_canc = st.columns(2)

            with col_salvar:

                salvar_edicao = st.form_submit_button(
                    "💾 Salvar",
                    type="primary",
                    use_container_width=True
                )

            with col_canc:

                cancelar_edicao = st.form_submit_button(
                    "❌ Cancelar",
                    use_container_width=True
                )

            if salvar_edicao:

                lat_n = normalizar_coordenada(
                    lat_e,
                    "lat"
                )

                lon_n = normalizar_coordenada(
                    lon_e,
                    "lon"
                )

                if (
                    not municipio_e.strip()
                    or not bairro_e.strip()
                ):

                    st.error(
                        "Município e Bairro são obrigatórios."
                    )

                elif lat_n is None or lon_n is None:

                    st.error(
                        "Coordenadas inválidas."
                    )

                else:

                    if atualizar_ponto(
                        id_registro,
                        normalizar_texto_local(
                            municipio_e,
                            padrao="TERESINA"
                        ),
                        matricula_e.strip(),
                        normalizar_texto_local(
                            bairro_e,
                            padrao=""
                        ),
                        lat_n,
                        lon_n,
                        pressao_e,
                        data_para_str(
                            data_e
                        ),
                        bp_observacao_preservando_id(
                            observacao_e.strip(), reg_edit["Observacao"]
                        )
                    ):

                        st.success(
                            "Atualizado com sucesso!"
                        )

                        st.rerun()

            if cancelar_edicao:
                st.rerun()

    else:

        st.warning(
            "Registro não encontrado."
        )


@st.dialog("📋 Pré-visualização da Planilha")
def modal_previa_upload():

    if not pode_alterar_dados:

        st.error(
            "Seu perfil não possui permissão para importar dados."
        )

        return

    st.write(
        f"Arquivo carregado: "
        f"**{st.session_state.nome_arquivo_pendente_bp}**"
    )

    df_preview = pd.DataFrame(
        st.session_state.dados_upload_pendentes_bp,
        columns=[
            "ID",
            "Data",
            "Municipio",
            "Bairro",
            "Latitude",
            "Longitude",
            "Pressao_MCA",
            "Observacao",
            "Matricula"
        ]
    )

    df_preview_visivel = df_preview.copy()
    df_preview_visivel["Observacao"] = (
        df_preview_visivel["Observacao"].map(bp_observacao_visivel)
    )
    st.dataframe(
        df_preview_visivel[
            [
                "Data",
                "Municipio",
                "Matricula",
                "Bairro",
                "Latitude",
                "Longitude",
                "Pressao_MCA",
                "Observacao"
            ]
        ],
        use_container_width=True
    )

    st.info(
        f"Total de registros válidos prontos para envio: "
        f"**{len(df_preview)}**"
    )

    resumo = st.session_state.get("resumo_upload_pendentes_bp")
    if resumo is not None:
        st.caption(
            f"Atividades recebidas: {resumo.origem} | "
            f"Com medição numérica: {len(resumo.linhas)} | "
            f"Sem medição: {resumo.sem_pressao_numerica} | "
            f"Sem coordenadas: {resumo.sem_coordenadas} | "
            f"Sem data: {resumo.sem_data} | "
            f"Sem bairro: {resumo.excluidas_ou_fora_da_faixa}"
        )

    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:

        if st.button(
            "Confirmar e Enviar",
            type="primary",
            use_container_width=True
        ):

            with st.spinner(
                "Enviando registros com segurança para o Google Sheets..."
            ):

                qtd_inserida = adicionar_lote_seguro(
                    st.session_state.dados_upload_pendentes_bp
                )

            st.session_state.dados_upload_pendentes_bp = None
            st.session_state.resumo_upload_pendentes_bp = None
            st.session_state.nome_arquivo_pendente_bp = None
            st.session_state.file_uploader_key_bp += 1

            if qtd_inserida > 0:

                st.success(
                    f"✅ {qtd_inserida} novos registros importados com sucesso!"
                )

            else:

                st.info(
                    "ℹ️ Todos os registros da planilha já existiam "
                    "no sistema. Nenhuma duplicação foi feita."
                )

            time.sleep(1)

            st.rerun()

    with col_btn2:

        if st.button(
            "Cancelar",
            use_container_width=True
        ):

            st.session_state.dados_upload_pendentes_bp = None
            st.session_state.resumo_upload_pendentes_bp = None
            st.session_state.nome_arquivo_pendente_bp = None
            st.session_state.file_uploader_key_bp += 1

            st.rerun()


# O botão é exibido em sua posição na sidebar depois de df_filtrado ficar pronto.
relatorio_pdf_placeholder = None

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:

    st.markdown(
        "### 💧 COI - Monitoramento"
    )

    st.caption(
        "Baixa Pressão • Tempo Real"
    )

    if eh_operador:

        st.info(
            "👁️ **Modo consulta**\n\n"
            "Visualização e análises liberadas. "
            "Alterações, importações e exportações estão desabilitadas."
        )

    st.markdown(
        "#### 📅 Selecionar Período"
    )

    c_data_ini, c_data_fim = st.columns(2)

    with c_data_ini:

        data_inicial = st.date_input(
            "Data inicial",
            value=st.session_state.data_inicial_selecionada,
            format="DD/MM/YYYY",
            key="calendario_data_inicial"
        )

    with c_data_fim:

        data_final = st.date_input(
            "Data final",
            value=st.session_state.data_final_selecionada,
            format="DD/MM/YYYY",
            key="calendario_data_final"
        )

    st.session_state.data_inicial_selecionada = data_inicial
    st.session_state.data_final_selecionada = data_final

    if data_inicial > data_final:

        st.error(
            "A data inicial não pode ser maior que a data final."
        )

        periodo_valido = False

    else:

        periodo_valido = True

        if data_inicial == data_final:

            st.success(
                f"Exibindo dados de "
                f"**{data_para_str(data_inicial)}**"
            )

        else:

            st.info(
                f"Exibindo dados de "
                f"**{data_para_str(data_inicial)}** "
                f"a **{data_para_str(data_final)}**"
            )

    st.divider()

    st.markdown(
        "#### 🔍 Filtros"
    )

    df_all = carregar_dados()

    if not df_all.empty and periodo_valido:

        df_all["DataObjFiltro"] = pd.to_datetime(
            df_all["Data"],
            format="%d/%m/%Y",
            errors="coerce"
        )

        df_data = df_all[
            (
                df_all["DataObjFiltro"].dt.date
                >= data_inicial
            )
            &
            (
                df_all["DataObjFiltro"].dt.date
                <= data_final
            )
        ].copy()

    else:

        df_data = (
            df_all.copy()
            if not df_all.empty
            else df_all
        )

    municipios_opts = (
        ["Todos"]
        +
        sorted(
            df_data["Municipio"]
            .dropna()
            .unique()
            .tolist()
        )
        if not df_data.empty
        else ["Todos"]
    )

    mun_sel = st.selectbox(
        "Município",
        municipios_opts,
        key="filtro_municipio"
    )

    bairros_base = (
        df_data[
            df_data["Municipio"] == mun_sel
        ]
        if (
            mun_sel != "Todos"
            and not df_data.empty
        )
        else df_data
    )

    bairros_opts = (
        ["Todos"]
        +
        sorted(
            bairros_base["Bairro"]
            .dropna()
            .unique()
            .tolist()
        )
        if not bairros_base.empty
        else ["Todos"]
    )

    bairro_sel = st.selectbox(
        "Bairro",
        bairros_opts,
        key="filtro_bairro"
    )

    # ========================================================
    # FILTRO DE MATRÍCULA
    # ========================================================
    matriculas_base = (
        df_data[
            (
                df_data["Municipio"] == mun_sel
            )
            &
            (
                df_data["Bairro"] == bairro_sel
            )
        ]
        if (
            mun_sel != "Todos"
            and bairro_sel != "Todos"
            and not df_data.empty
        )
        else (
            df_data[
                df_data["Municipio"] == mun_sel
            ]
            if (
                mun_sel != "Todos"
                and not df_data.empty
            )
            else df_data
        )
    )

    matriculas_opts = (
        ["Todas"]
        +
        sorted(
            [
                str(x).strip()
                for x in matriculas_base["Matricula"]
                .dropna()
                .unique()
                if str(x).strip()
                and str(x).strip().lower()
                not in ("nan", "none")
            ]
        )
        if not matriculas_base.empty
        else ["Todas"]
    )

    matricula_sel = st.selectbox(
        "Matrícula",
        matriculas_opts,
        key="filtro_matricula"
    )

    # Filtro geral: afeta indicadores, relatórios, tabela e mapa.
    # Sem seleção = todas as medições.
    faixas_selecionadas = st.multiselect(
        "Faixas de Pressão",
        options=BP_FAIXAS_PRESSAO,
        default=[],
        placeholder="Todas as faixas",
        help=(
            "Filtro geral do módulo: indicadores, PDF, tabela e mapa. "
            "Sem seleção, considera todas as medições."
        ),
        key="filtro_faixas_pressao_v2",
    )

    # ========================================================
    # AÇÕES E DADOS — SOMENTE ADMIN/USUÁRIO
    # ========================================================
    if pode_alterar_dados:

        st.divider()

        st.markdown(
            "#### ➕ Ações e Dados"
        )

        if st.button(
            "Adicionar Novo Ponto",
            type="primary",
            use_container_width=True
        ):

            modal_novo_ponto()

        arquivo_upload = st.file_uploader(
            "📂 Enviar Planilha (XLSX/CSV)",
            type=[
                "xlsx",
                "csv"
            ],
            key=(
                f"upload_baixa_pressao_"
                f"{st.session_state.file_uploader_key_bp}"
            )
        )

        # ====================================================
        # MODELO XLSX
        # ====================================================
        df_modelo = pd.DataFrame(
            [{
                "Data": datetime.now().strftime(
                    "%d/%m/%Y"
                ),
                "Municipio": "TERESINA",
                "Bairro": "CENTRO",
                "Latitude": -5.0892,
                "Longitude": -42.8019,
                "Pressao_MCA": 2.5,
                "Observacao": "EXEMPLO DE OBSERVAÇÃO",
                "Matricula": "123456789"
            }],
            columns=COLUNAS_PADRAO[1:]
        )

        output_modelo = io.BytesIO()

        with pd.ExcelWriter(
            output_modelo,
            engine="openpyxl"
        ) as writer:

            df_modelo.to_excel(
                writer,
                index=False,
                sheet_name="Modelo"
            )

        st.download_button(
            label="📥 Baixar Planilha Modelo",
            data=output_modelo.getvalue(),
            file_name=(
                "modelo_importacao_"
                "baixa_pressao.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True
        )

        # ====================================================
        # UPLOAD — BRUTO DO SISTEMA OU MODELO SAI
        # ====================================================
        if arquivo_upload is not None:
            # A prévia é recalculada quando muda o arquivo selecionado.
            if st.session_state.nome_arquivo_pendente_bp != arquivo_upload.name:
                try:
                    conteudo = arquivo_upload.getvalue()
                    df_up = ler_arquivo(arquivo_upload.name, conteudo)
                    resultado = converter_bruta(df_up, arquivo_upload.name)
                    st.session_state.resumo_upload_pendentes_bp = resultado
                    if resultado.linhas:
                        st.session_state.dados_upload_pendentes_bp = resultado.linhas
                        st.session_state.nome_arquivo_pendente_bp = arquivo_upload.name
                    else:
                        st.session_state.dados_upload_pendentes_bp = None
                        st.warning(
                            "Nenhuma medição numérica válida encontrada. "
                            "Parecer sem leitura numérica não é importado."
                        )
                except Exception as e:
                    st.session_state.dados_upload_pendentes_bp = None
                    st.session_state.resumo_upload_pendentes_bp = None
                    st.error(f"❌ Erro ao processar a planilha: {e}")

        if st.session_state.dados_upload_pendentes_bp is not None:
            modal_previa_upload()

        st.divider()

        st.markdown(
            "#### 📥 Exportar Dados"
        )

        if not df_all.empty:

            output = io.BytesIO()

            with pd.ExcelWriter(
                output,
                engine="openpyxl"
            ) as writer:

                df_exportacao = df_all.copy()
                df_exportacao["Observacao"] = (
                    df_exportacao["Observacao"].map(bp_observacao_visivel)
                )
                df_exportacao.to_excel(
                    writer,
                    index=False,
                    sheet_name="Baixa_Pressao"
                )

            excel_data = output.getvalue()

            st.download_button(
                label="📊 Baixar em Excel (XLSX)",
                data=excel_data,
                file_name=(
                    f"baixa_pressao_"
                    f"{datetime.now().strftime('%Y%m%d')}.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True
            )

            kml_string = gerar_kml(
                df_all
            )

            st.download_button(
                label="🗺️ Baixar Mapa (KML/KMZ)",
                data=kml_string,
                file_name=(
                    f"baixa_pressao_"
                    f"{datetime.now().strftime('%Y%m%d')}.kml"
                ),
                mime="application/vnd.google-earth.kml+xml",
                use_container_width=True
            )

            # O conteúdo é preenchido depois dos filtros. A posição do botão
            # continua nesta seção da sidebar, logo após as exportações.
            relatorio_pdf_placeholder = st.empty()

    # ========================================================
    # ATUALIZAÇÃO — DISPONÍVEL PARA TODOS
    # ========================================================
    st.divider()

    st.markdown(
        "#### ⏱️ Atualização"
    )

    intervalo = st.select_slider(
        "Intervalo (segundos)",
        options=[
            0,
            15,
            30,
            60,
            120
        ],
        value=120
    )

    # O agendamento nativo é registrado após a sidebar.
    # 0 = atualização automática desativada.

    st.markdown(
        "<br>" * 2,
        unsafe_allow_html=True
    )

    st.divider()

    if st.button(
        "🏠 Voltar ao Menu Principal",
        use_container_width=True
    ):

        st.switch_page(
            "app.py"
        )

    st.divider()

    if "modo_escuro_bp" not in st.session_state:
        st.session_state.modo_escuro_bp = False

    modo_escuro_bp = st.toggle(
        "🌙 Modo escuro",
        value=st.session_state.modo_escuro_bp,
        key="toggle_modo_escuro_bp",
    )

    st.session_state.modo_escuro_bp = (
        modo_escuro_bp
    )

    if modo_escuro_bp:

        st.markdown(
            """
            <style>
                .stApp {
                    background-color: #0e1117;
                    color: #fafafa;
                }

                [data-testid="stSidebar"] {
                    background-color: #161b22;
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

                [data-testid="stMetric"],
                [data-testid="stExpander"],
                div[data-testid="stDataFrame"] {
                    background-color: #161b22;
                }

                input,
                textarea,
                [data-baseweb="select"] > div {
                    background-color: #21262d !important;
                    color: #f0f0f0 !important;
                }

                [data-baseweb="select"] * {
                    color: #f0f0f0 !important;
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

                [data-testid="stDataFrame"] {
                    color: #f0f0f0;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# ATUALIZAÇÃO AUTOMÁTICA NATIVA — SEM COMPONENTES EXTERNOS
# ============================================================
# O seletor permanece na sidebar com as opções 0/15/30/60/120 s
# e 120 s como padrão. O fragment funciona como um agendador:
# na hora programada, atualiza a PÁGINA INTEIRA, evitando que
# indicadores, filtros, tabela e mapa apresentem dados defasados
# em relação uns aos outros.
if intervalo > 0:

    @st.fragment(run_every=intervalo)
    def bp_atualizacao_programada():
        agora = time.monotonic()
        ultimo_intervalo = st.session_state.get("_bp_refresh_intervalo")
        ultimo_ciclo = st.session_state.get("_bp_refresh_ultimo_ciclo")

        # Primeira execução / alteração de intervalo: programa a próxima
        # atualização, sem recarregar imediatamente nem entrar em loop.
        if ultimo_intervalo != intervalo or ultimo_ciclo is None:
            st.session_state["_bp_refresh_intervalo"] = intervalo
            st.session_state["_bp_refresh_ultimo_ciclo"] = agora
            return

        # Pequena tolerância para imprecisões de agendamento do navegador.
        # Marca o horário ANTES do rerun para prevenir ciclos imediatos.
        if agora - ultimo_ciclo >= max(0.0, intervalo - 0.5):
            st.session_state["_bp_refresh_ultimo_ciclo"] = agora
            st.rerun(scope="app")

    bp_atualizacao_programada()
else:
    # Ao selecionar 0, interrompe o agendamento da sessão.
    st.session_state.pop("_bp_refresh_intervalo", None)
    st.session_state.pop("_bp_refresh_ultimo_ciclo", None)


# ============================================================
# ÁREA PRINCIPAL
# ============================================================
st.title(
    "💧 Painel de Monitoramento de Baixa Pressão - COI"
)

if eh_operador:

    st.info(
        "👁️ **Modo consulta/análise — perfil OPERADOR**  \n"
        "Você pode utilizar o mapa, filtros, tabela e análises. "
        "Funções de inclusão, edição, exclusão, importação, "
        "alteração e exportação estão desabilitadas."
    )

if periodo_valido:

    if data_inicial == data_final:

        st.caption(
            f"Visualizando: "
            f"**{data_para_str(data_inicial)}**"
        )

    else:

        st.caption(
            f"Visualizando de "
            f"**{data_para_str(data_inicial)}** "
            f"a **{data_para_str(data_final)}**"
        )

df = carregar_dados()


# ============================================================
# FILTRO PRINCIPAL POR INTERVALO
# ============================================================
if not df.empty and periodo_valido:

    df["DataObjFiltro"] = pd.to_datetime(
        df["Data"],
        format="%d/%m/%Y",
        errors="coerce"
    )

    df_filtrado = df[
        (
            df["DataObjFiltro"].dt.date
            >= data_inicial
        )
        &
        (
            df["DataObjFiltro"].dt.date
            <= data_final
        )
    ].copy()

else:

    df_filtrado = (
        df.copy()
        if not df.empty
        else df.copy()
    )


if mun_sel != "Todos":

    df_filtrado = df_filtrado[
        df_filtrado["Municipio"] == mun_sel
    ]


if bairro_sel != "Todos":

    df_filtrado = df_filtrado[
        df_filtrado["Bairro"] == bairro_sel
    ]


# ============================================================
# FILTRO PRINCIPAL POR MATRÍCULA
# ============================================================
if matricula_sel != "Todas":

    df_filtrado = df_filtrado[
        df_filtrado["Matricula"]
        .astype(str)
        .str.strip()
        == matricula_sel
    ]


# Filtro geral de pressão (união das faixas escolhidas).
# Sem seleção, mantém todas as medições e todos os demais filtros existentes.
df_filtrado = bp_filtrar_por_faixas(df_filtrado, faixas_selecionadas)

# ============================================================
# GERAR PDF A PARTIR DOS MESMOS FILTROS EXIBIDOS NA TELA
# ============================================================
if relatorio_pdf_placeholder is not None:
    with relatorio_pdf_placeholder.container():
        if not periodo_valido or df_filtrado.empty:
            st.button("📄 Gerar relatório (PDF)", disabled=True,
                      use_container_width=True, key="bp_rel_botao_desabilitado")
            st.caption("Sem registros para o período e os filtros selecionados.")
        else:
            # Descarta arquivo previamente gerado se algum filtro ou registro mudou.
            assinatura_dados = bp_rel_hashlib.sha256(
                pd.util.hash_pandas_object(
                    df_filtrado[COLUNAS_PADRAO + [BP_COLUNA_PROTOCOLO]].reset_index(drop=True),
                    index=False
                ).values.tobytes()
            ).hexdigest()
            assinatura_filtro = (
                data_inicial.isoformat(), data_final.isoformat(), mun_sel,
                bairro_sel, matricula_sel, tuple(faixas_selecionadas), assinatura_dados
            )
            if st.session_state.get("bp_rel_assinatura") != assinatura_filtro:
                st.session_state.pop("bp_rel_pdf_gerado", None)
            if st.button("📄 Gerar relatório (PDF)", type="primary",
                         use_container_width=True, key="bp_gerar_relatorio_pdf"):
                try:
                    with st.spinner("Gerando relatório das medições filtradas..."):
                        st.session_state["bp_rel_pdf_gerado"] = bp_rel_pdf(
                            df_filtrado, data_inicial, data_final,
                            mun_sel, bairro_sel, matricula_sel,
                            tuple(faixas_selecionadas)
                        )
                        st.session_state["bp_rel_assinatura"] = assinatura_filtro
                except Exception as erro_pdf:
                    st.session_state.pop("bp_rel_pdf_gerado", None)
                    st.error(f"Não foi possível gerar o PDF: {erro_pdf}")
            if st.session_state.get("bp_rel_pdf_gerado") is not None and \
                    st.session_state.get("bp_rel_assinatura") == assinatura_filtro:
                st.download_button(
                    "⬇️ Baixar relatório gerado",
                    data=st.session_state["bp_rel_pdf_gerado"],
                    file_name=f"relatorio_baixa_pressao_{datetime.now():%Y%m%d}.pdf",
                    mime="application/pdf", use_container_width=True,
                    key="bp_rel_baixar_pdf"
                )
                st.caption(f"PDF com {len(df_filtrado)} medição(ões) filtrada(s).")


# ============================================================
# MÉTRICAS
# ============================================================
if not df_filtrado.empty:

    total = len(
        df_filtrado
    )

    sem_pressao = len(
        df_filtrado[
            df_filtrado["Pressao_MCA"] == 0
        ]
    )

    baixa_pressao = len(
        df_filtrado[
            (
                df_filtrado["Pressao_MCA"] > 0
            )
            &
            (
                df_filtrado["Pressao_MCA"] <= 5
            )
        ]
    )

    em_atencao = len(
        df_filtrado[
            (df_filtrado["Pressao_MCA"] > 5)
            & (df_filtrado["Pressao_MCA"] < 10)
        ]
    )

    normal = len(
        df_filtrado[
            (df_filtrado["Pressao_MCA"] >= 10)
            & (df_filtrado["Pressao_MCA"] <= 15)
        ]
    )

    alta_pressao = len(
        df_filtrado[
            df_filtrado["Pressao_MCA"] > 15
        ]
    )

    k1, k2, k3, k4, k5, k6 = st.columns(6)

    k1.metric("Total de Ocorrências", total)
    k2.metric("Sem Pressão (0 MCA)", sem_pressao)
    k3.metric("Baixa Pressão (> 0 e ≤ 5 MCA)", baixa_pressao)
    k4.metric("Em Atenção (> 5 e < 10 MCA)", em_atencao)
    k5.metric("Normal (≥ 10 e ≤ 15 MCA)", normal)
    k6.metric("Alta Pressão (> 15 MCA)", alta_pressao)

else:

    if periodo_valido:

        st.info(
            "Nenhum ponto registrado no período "
            "e filtros selecionados."
        )


st.divider()


# ============================================================
# COTAS DO TERRENO NOS RÓTULOS DAS MEDIÇÕES INDIVIDUAIS
# ============================================================
# Descoberta automática de TODOS os GeoTIFFs em dados/topografia, inclusive
# subpastas. A cobertura é identificada pelas coordenadas e pelo CRS real
# de cada raster — nunca pelo nome do arquivo ou do município.
# Sem cobertura/valor válido, omite a cota; não inventa valores.
# A cota DSM/MDE é da superfície, não da rede de distribuição.
BP_DEM_DIRETORIO = BP_RAIZ_SAI / "dados" / "topografia"


@st.cache_data(show_spinner=False, max_entries=128)
def bp_dem_limites_geograficos(caminho: str, versao: int, tamanho: int) -> tuple:
    """Obtém limites WGS84 uma vez por versão de um GeoTIFF georreferenciado.

    Alterações no arquivo invalidam o cache por mtime_ns e tamanho.
    """
    _ = (versao, tamanho)
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(caminho) as origem:
        if origem.crs is None or origem.count < 1:
            raise ValueError("GeoTIFF sem sistema de coordenadas ou banda válida")
        limites = transform_bounds(
            origem.crs, "EPSG:4326", *origem.bounds, densify_pts=21
        )
        if (not all(math.isfinite(float(v)) for v in limites)
                or limites[0] >= limites[2] or limites[1] >= limites[3]):
            raise ValueError("Limites geográficos inválidos no GeoTIFF")
        return tuple(float(v) for v in limites)  # oeste, sul, leste, norte


def bp_catalogo_topografia() -> tuple:
    """Descobre GeoTIFFs a cada execução; metadados válidos ficam em cache.

    Arquivos de menor cobertura são consultados primeiro em sobreposições;
    um raster maior pode complementar pixels sem dados (NoData).
    Retorna (catálogo, alertas) sem impedir que o mapa seja exibido.
    """
    if not BP_DEM_DIRETORIO.is_dir():
        return (), ()

    arquivos = sorted(
        (arquivo for arquivo in BP_DEM_DIRETORIO.rglob("*")
         if arquivo.is_file() and arquivo.suffix.lower() in (".tif", ".tiff")),
        key=lambda arquivo: str(arquivo).casefold(),
    )
    catalogo = []
    alertas = []
    for arquivo in arquivos:
        try:
            stat = arquivo.stat()
            oeste, sul, leste, norte = bp_dem_limites_geograficos(
                str(arquivo), stat.st_mtime_ns, stat.st_size
            )
            catalogo.append((
                str(arquivo), stat.st_mtime_ns, stat.st_size,
                oeste, sul, leste, norte,
            ))
        except (OSError, ValueError, TypeError, RuntimeError, ImportError) as erro:
            alertas.append(f"{arquivo.name}: {erro}")

    # Prefere recortes de maior detalhe espacial, preservando ordenação
    # determinística se dois arquivos cobrirem áreas do mesmo tamanho.
    catalogo.sort(key=lambda x: (
        (x[5] - x[3]) * (x[6] - x[4]), x[0].casefold()
    ))
    return tuple(catalogo), tuple(alertas)


@st.cache_data(show_spinner=False, max_entries=64)
def bp_cotas_terreno(caminho: str, versao: int, coordenadas: tuple) -> tuple:
    """Amostra cotas do GeoTIFF em lote, retornando None quando não houver valor.

    `coordenadas` é uma tupla de pares (latitude, longitude) em WGS84, na
    mesma ordem dos registros exibidos no mapa. O arquivo é aberto uma vez.
    `versao` é o mtime_ns, para invalidar o cache quando o raster for trocado.
    """
    _ = versao
    if not coordenadas:
        return ()

    import numpy as np
    import rasterio
    from rasterio.warp import transform

    with rasterio.open(caminho) as origem:
        if origem.crs is None or origem.count < 1:
            raise ValueError("GeoTIFF de elevação sem georreferenciamento válido.")

        latitudes = [float(c[0]) for c in coordenadas]
        longitudes = [float(c[1]) for c in coordenadas]
        xs, ys = transform("EPSG:4326", origem.crs, longitudes, latitudes)
        limites = origem.bounds
        validos = [
            (math.isfinite(x) and math.isfinite(y)
             and limites.left <= x < limites.right
             and limites.bottom < y <= limites.top)
            for x, y in zip(xs, ys)
        ]
        resultado = [None] * len(coordenadas)
        indices = [i for i, valido in enumerate(validos) if valido]
        for indice, amostra in zip(
            indices, origem.sample(((xs[i], ys[i]) for i in indices), indexes=1, masked=True)
        ):
            valor = amostra[0]
            if np.ma.is_masked(valor):
                continue
            cota = float(valor)
            if not math.isfinite(cota):
                continue
            if origem.nodata is not None and np.isclose(cota, origem.nodata):
                continue
            resultado[indice] = cota
        return tuple(resultado)


def bp_cotas_automaticas(coordenadas: tuple) -> tuple:
    """Consulta em lote o GeoTIFF que cobre cada (latitude, longitude).

    O catálogo é refeito a cada rerun (novos arquivos são detectados sem
    alterações no código). Só abre rasters com pontos dentro dos seus limites.
    Em áreas sobrepostas, um pixel NoData permite tentar o próximo arquivo.
    """
    catalogo, alertas_catalogo = bp_catalogo_topografia()
    if not coordenadas or not catalogo:
        return (None,) * len(coordenadas), alertas_catalogo

    cotas = [None] * len(coordenadas)
    alertas = list(alertas_catalogo)
    for caminho, versao, _tamanho, oeste, sul, leste, norte in catalogo:
        pendentes = [
            i for i, (lat, lon) in enumerate(coordenadas)
            if cotas[i] is None
            and math.isfinite(lat) and math.isfinite(lon)
            and oeste <= lon <= leste and sul <= lat <= norte
        ]
        if not pendentes:
            continue
        amostras_coords = tuple(coordenadas[i] for i in pendentes)
        try:
            amostras = bp_cotas_terreno(caminho, versao, amostras_coords)
        except (OSError, ValueError, TypeError, RuntimeError, ImportError) as erro:
            alertas.append(f"{Path(caminho).name}: {erro}")
            continue
        for indice, valor in zip(pendentes, amostras):
            if valor is not None and math.isfinite(float(valor)):
                cotas[indice] = float(valor)

        if all(cota is not None for cota in cotas):
            break
    return tuple(cotas), tuple(alertas)


# ============================================================
# MAPA
# ============================================================
st.subheader(
    "🗺️ Mapa de Baixa Pressão"
)

c_map1, c_map2, c_map_faixas, c_map3, c_map4 = st.columns(
    [2, 2.2, 2.3, 1.55, 1.15]
)


with c_map1:

    tipo_mapa = st.selectbox(
        "🗺️ Tipo de Mapa",
        options=[
            "Mapa Padrão (OpenStreetMap)",
            "Satélite (Esri World Imagery)",
            "Terreno (OpenTopoMap)"
        ],
        key="seletor_tipo_mapa_bp"
    )


with c_map2:

    modo_visualizacao = st.selectbox(
        "📊 Visualização",
        options=[
            "Por bairro",
            "Medições individuais"
        ],
        index=1,  # Abertura padrão: exibir as medições individuais no mapa.
        key="modo_visualizacao_mapa_bp",
        help=(
            "Por bairro mostra uma visão gerencial consolidada. "
            "Medições individuais mostra cada ponto registrado."
        )
    )


with c_map_faixas:
    faixas_mapa = st.multiselect(
        "🎛️ Faixas de pressão",
        options=BP_FAIXAS_PRESSAO,
        default=[],
        placeholder="Todas as faixas",
        help=(
            "Selecione uma ou mais faixas para exibir apenas esses pontos "
            "no mapa. Sem seleção, exibe todas as faixas. "
            "Os indicadores, a tabela e o PDF não são alterados por este filtro."
        ),
        key="bp_faixas_mapa_v1",
    )


with c_map3:

    st.checkbox(
        "Exibir rótulos dos bairros",
        value=True,
        key="mostrar_rotulos_bp",
        help=(
            "Mostra informações junto aos pins. "
            "No modo por bairro mostra pressão média e quantidade. "
            "No modo individual mostra a medição daquele ponto."
        )
    )

    mostrar_rotulos = st.session_state.mostrar_rotulos_bp


with c_map4:
    st.checkbox(
        "🛠️ Sanear",
        value=False,
        key="bp_exibir_sanear",
        help=(
            "Exibe os polígonos de áreas com intervenções do projeto SANEAR "
            "para análise espacial das medições de baixa pressão."
        ),
    )


# Este segundo filtro é EXCLUSIVO do mapa. O filtro geral da sidebar
# permanece aplicado ao restante da interface.
df_mapa = bp_filtrar_por_faixas(df_filtrado, faixas_mapa)

if (
    not df_mapa.empty
    and df_mapa["Latitude"].notna().any()
):

    centro_lat = float(
        df_mapa["Latitude"].mean()
    )

    centro_lon = float(
        df_mapa["Longitude"].mean()
    )

    zoom = 13

else:

    centro_lat = LAT_BASE
    centro_lon = LON_BASE
    zoom = 12


m = folium.Map(
    location=[
        centro_lat,
        centro_lon
    ],
    zoom_start=zoom,
    tiles=None,
    control_scale=True
)

# ============================================================
# CONTROLE DE TELA CHEIA — MAPA PRINCIPAL
# ============================================================
# O Fullscreen é um controle nativo do Leaflet/Folium; não altera
# os filtros, as camadas, a base de dados ou a chave do st_folium.
# O tamanho do mapa é recalculado ao expandir e ao restaurar.
Fullscreen(
    position="topright",
    title="Expandir mapa para tela cheia",
    title_cancel="Sair da tela cheia",
    force_separate_button=True,
).add_to(m)


if "Satélite" in tipo_mapa:

    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/"
            "ArcGIS/rest/services/World_Imagery/"
            "MapServer/tile/{z}/{y}/{x}"
        ),
        attr=(
            "Esri &mdash; Source: Esri, i-cubed, USDA, "
            "USGS, AEX, GeoEye, Getmapping, Aerogrid, "
            "IGN, IGP, UPR-EGP, and the GIS User Community"
        ),
        name="Satélite (Esri World Imagery)"
    ).add_to(m)

elif "Terreno" in tipo_mapa:

    folium.TileLayer(
        "OpenTopoMap",
        name="Terreno (OpenTopoMap)"
    ).add_to(m)

else:

    folium.TileLayer(
        "OpenStreetMap",
        name="Mapa Padrão (OpenStreetMap)"
    ).add_to(m)


# SANEAR: camada somente de leitura, desenhada sob os marcadores de pressão.
# Não modifica a base de medições nem a amostragem de cotas do terreno.
if st.session_state.bp_exibir_sanear:
    try:
        bp_kml_sanear = bp_encontrar_kml_sanear()
        if not bp_kml_sanear.is_file():
            st.warning(
                "Camada Sanear indisponível: arquivo "
                "`geodados/areas_influencia/sanear.kml` não encontrado."
            )
        else:
            bp_geojson_sanear = bp_carregar_poligonos_sanear(
                str(bp_kml_sanear), bp_kml_sanear.stat().st_mtime_ns
            )
            if not bp_geojson_sanear["features"]:
                st.warning("A camada Sanear não contém polígonos válidos.")
            else:
                folium.GeoJson(
                    bp_geojson_sanear,
                    name="Sanear — áreas de obras de esgoto",
                    style_function=lambda feature: {
                        "color": "#0F766E",
                        "weight": 2.2,
                        "opacity": 0.95,
                        "fillColor": "#2DD4BF",
                        "fillOpacity": 0.16,
                    },
                    highlight_function=lambda feature: {
                        "color": "#115E59",
                        "weight": 3.2,
                        "fillOpacity": 0.28,
                    },
                    tooltip=folium.GeoJsonTooltip(
                        fields=["Área", "Bacia", "Município", "Empresa"],
                        aliases=["Área SANEAR:", "Bacia:", "Município:", "Empresa:"],
                        labels=True,
                        sticky=True,
                        style=(
                            "background-color: #ffffff; color: #12303A; "
                            "font: 12px Arial, sans-serif; padding: 7px;"
                        ),
                    ),
                ).add_to(m)
    except (ET.ParseError, OSError, ValueError, TypeError, KeyError) as bp_exc:
        st.warning(f"Não foi possível carregar os polígonos Sanear: {bp_exc}")


# ============================================================
# FUNÇÕES VISUAIS DOS PINS
# ============================================================
def gerar_pin_svg(
    cor: str,
    tamanho: str = "bairro"
) -> str:

    if tamanho == "individual":

        largura = 34
        altura = 39

        escala = 0.88

    else:

        largura = 40
        altura = 45

        escala = 1.0

    return f"""
    <svg
        class="bp-pin-svg"
        width="{largura}"
        height="{altura}"
        viewBox="0 0 40 45"
        xmlns="http://www.w3.org/2000/svg"
        style="
            position:absolute;
            left:50%;
            bottom:0;
            transform:translateX(-50%);
            overflow:visible;
            pointer-events:auto;
            z-index:1100;
        "
    >

        <path
            d="
                M20 43
                C18.5 40.8 5 26.2 5 16
                C5 7.72 11.72 1 20 1
                C28.28 1 35 7.72 35 16
                C35 26.2 21.5 40.8 20 43
                Z
            "
            fill="{cor}"
            stroke="#ffffff"
            stroke-width="2"
            stroke-linejoin="round"
        />

        <circle
            cx="20"
            cy="16"
            r="5"
            fill="#ffffff"
        />

    </svg>
    """


# ============================================================
# VISUALIZAÇÃO DOS MARCADORES
# ============================================================
if not df_mapa.empty:

    validos = df_mapa.dropna(
        subset=[
            "Latitude",
            "Longitude"
        ]
    ).copy()

    if not validos.empty:

        validos["Municipio"] = (
            validos["Municipio"]
            .fillna("MUNICIPIO NAO INFORMADO")
            .astype(str)
            .str.strip()
        )

        validos["Bairro"] = (
            validos["Bairro"]
            .fillna("BAIRRO NAO INFORMADO")
            .astype(str)
            .str.strip()
        )

        validos["Matricula"] = (
            validos["Matricula"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        validos["Pressao_MCA"] = pd.to_numeric(
            validos["Pressao_MCA"],
            errors="coerce"
        ).fillna(0.0)

        # ====================================================
        # MEDIÇÕES INDIVIDUAIS
        # ====================================================
        if modo_visualizacao == "Medições individuais":

            # A cota é obtida pelo ponto medido, não por interpolação entre
            # medições ou pela média de um bairro. Sem DEM/cobertura, deixa
            # apenas a pressão no rótulo, sem inventar altitude.
            bp_cotas_individuais = (None,) * len(validos)
            if mostrar_rotulos:
                try:
                    bp_coords = tuple(
                        (float(lat), float(lon))
                        for lat, lon in zip(validos["Latitude"], validos["Longitude"])
                    )
                    bp_cotas_individuais, bp_alertas_dem = bp_cotas_automaticas(bp_coords)
                    if bp_alertas_dem:
                        st.warning(
                            "Alguns arquivos topográficos não puderam ser utilizados: "
                            + "; ".join(bp_alertas_dem[:3])
                            + (f" (e mais {len(bp_alertas_dem)-3})" if len(bp_alertas_dem)>3 else "")
                        )
                except (OSError, ValueError, TypeError, RuntimeError, ImportError) as bp_erro_cota:
                    st.warning(f"Cota do terreno indisponível nos rótulos: {bp_erro_cota}")

            for (_, registro), bp_cota in zip(validos.iterrows(), bp_cotas_individuais):

                pressao = float(
                    registro["Pressao_MCA"]
                )

                classificacao, cor = classificar_pressao(
                    pressao
                )

                pressao_formatada = (
                    f"{pressao:.2f}"
                    .replace(".", ",")
                )

                bairro = str(
                    registro["Bairro"]
                )

                municipio = str(
                    registro["Municipio"]
                )

                matricula = str(
                    registro.get(
                        "Matricula",
                        ""
                    )
                ).strip()

                data_registro = str(
                    registro["Data"]
                )

                observacao = bp_observacao_visivel(
                    registro.get("Observacao", "")
                )

                observacao_html = ""

                if (
                    observacao
                    and observacao.lower() != "nan"
                ):

                    observacao_html = f"""
                    <div style="
                        margin-top:9px;
                        font-size:11px;
                        color:#475569;
                    ">
                        <b>Observação:</b> {escape(observacao)}
                    </div>
                    """

                popup_individual = f"""
                <div style="
                    width:250px;
                    font-family:Arial,sans-serif;
                    color:#111827;
                ">

                    <div style="
                        font-size:16px;
                        font-weight:800;
                        margin-bottom:2px;
                    ">
                        {escape(bairro)}
                    </div>

                    <div style="
                        font-size:11px;
                        color:#6b7280;
                        margin-bottom:8px;
                    ">
                        {escape(municipio)}
                    </div>

                    <div style="
                        font-size:11px;
                        color:#475569;
                        margin-bottom:10px;
                    ">
                        <b>Matrícula:</b>
                        {escape(matricula) if matricula else "Não informada"}
                    </div>

                    <div style="
                        background:#f8fafc;
                        border-radius:8px;
                        padding:9px;
                        border-left:4px solid {cor};
                        margin-bottom:10px;
                    ">

                        <div style="
                            font-size:10px;
                            color:#64748b;
                        ">
                            PRESSÃO MEDIDA
                        </div>

                        <div style="
                            font-size:20px;
                            font-weight:800;
                        ">
                            {pressao_formatada} MCA
                        </div>

                    </div>

                    <div style="
                        font-size:11px;
                        color:#475569;
                        line-height:1.55;
                    ">
                        <b>Classificação:</b> {classificacao}<br>
                        <b>Data:</b> {escape(data_registro)}<br>
                        <b>ID:</b> {escape(str(registro['ID']))}
                    </div>

                    {observacao_html}

                </div>
                """

                if mostrar_rotulos:
                    # Sem valor validado no raster, não exibe cota inventada.
                    bp_cota_html = ""
                    if bp_cota is not None and math.isfinite(float(bp_cota)):
                        bp_cota_fmt = f"{float(bp_cota):.0f}".replace(".", ",")
                        bp_cota_html = (
                            '<span class="bp-label-elevation" '
                            'title="Cota do terreno extraída do modelo digital de elevação">'
                            f'{bp_cota_fmt} m</span>'
                        )
                    bp_pressao_rotulo = f"{pressao:.2f}".rstrip("0").rstrip(".").replace(".", ",")

                    marker_html = f"""
                    <div
                        class="bp-marker-wrapper bp-individual-marker-wrapper"
                        data-bp-priority="{5 if pressao == 0 else 4 if pressao <= 5 else 3 if pressao < 10 else 2 if pressao <= 15 else 1}"
                        style="
                            position:relative;
                            width:42px;
                            height:70px;
                            overflow:visible;
                            font-family:Arial,sans-serif;
                            pointer-events:auto;
                        "
                    >

                        {gerar_pin_svg(
                            cor,
                            "individual"
                        )}

                        <div
                            class="bp-label-card bp-label-card-individual"
                            data-bp-default="top"
                        >
                            <div class="bp-label-data">
                                <span class="bp-label-pressure">
                                    {bp_pressao_rotulo} MCA
                                </span>
                                {bp_cota_html}
                            </div>
                        </div>

                    </div>
                    """

                else:

                    marker_html = f"""
                    <div
                        class="bp-marker-wrapper bp-individual-marker-wrapper"
                        data-bp-priority="{5 if pressao == 0 else 4 if pressao <= 5 else 3 if pressao < 10 else 2 if pressao <= 15 else 1}"
                        style="
                            position:relative;
                            width:42px;
                            height:70px;
                            overflow:visible;
                            font-family:Arial,sans-serif;
                            pointer-events:auto;
                        "
                    >

                        {gerar_pin_svg(
                            cor,
                            "individual"
                        )}

                    </div>
                    """

                folium.map.Marker(
                    location=[
                        float(registro["Latitude"]),
                        float(registro["Longitude"])
                    ],
                    icon=folium.DivIcon(
                        html=marker_html,
                        icon_size=(
                            42,
                            70
                        ),
                        icon_anchor=(
                            21,
                            70
                        ),
                        class_name=(
                            "pressao-individual-marker"
                        )
                    ),
                    popup=folium.Popup(
                        popup_individual,
                        max_width=290
                    ),
                    tooltip=(
                        f"{bairro} | "
                        f"{pressao_formatada} MCA | "
                        f"{classificacao}"
                    )
                ).add_to(m)

        # ====================================================
        # POR BAIRRO
        # ====================================================
        else:

            grupos_bairro = validos.groupby(
                [
                    "Municipio",
                    "Bairro"
                ],
                dropna=False,
                sort=True
            )

            for (
                municipio,
                bairro
            ), grupo in grupos_bairro:

                quantidade = int(
                    len(grupo)
                )

                media = float(
                    grupo["Pressao_MCA"].mean()
                )

                lat_bairro = float(
                    grupo["Latitude"].mean()
                )

                lon_bairro = float(
                    grupo["Longitude"].mean()
                )

                classificacao, cor = classificar_pressao(
                    media
                )

                media_formatada = (
                    f"{media:.2f}"
                    .replace(".", ",")
                )

                texto_medicoes = (
                    "medição"
                    if quantidade == 1
                    else "medições"
                )

                pressao_min = float(
                    grupo["Pressao_MCA"].min()
                )

                pressao_max = float(
                    grupo["Pressao_MCA"].max()
                )

                min_formatado = (
                    f"{pressao_min:.2f}"
                    .replace(".", ",")
                )

                max_formatado = (
                    f"{pressao_max:.2f}"
                    .replace(".", ",")
                )

                bairro_html = escape(
                    str(bairro)
                )

                municipio_html = escape(
                    str(municipio)
                )

                linhas_medicoes = []

                for _, registro in grupo.sort_values(
                    by=[
                        "Data",
                        "ID"
                    ],
                    ascending=[
                        False,
                        True
                    ]
                ).iterrows():

                    pressao_reg = float(
                        registro["Pressao_MCA"]
                    )

                    pressao_reg_formatada = (
                        f"{pressao_reg:.2f}"
                        .replace(".", ",")
                    )

                    matricula_reg = str(
                        registro.get(
                            "Matricula",
                            ""
                        )
                    ).strip()

                    matricula_html = ""

                    if matricula_reg:

                        matricula_html = f"""
                        <div style="
                            color:#6b7280;
                            margin-top:2px;
                        ">
                            Matrícula:
                            {escape(matricula_reg)}
                        </div>
                        """

                    linhas_medicoes.append(
                        f"""
                        <div style="
                            padding:7px 0;
                            border-bottom:1px solid #e5e7eb;
                            font-size:11px;
                        ">

                            <div style="
                                font-weight:700;
                                color:#111827;
                            ">
                                {escape(str(registro['Data']))} ·
                                {pressao_reg_formatada} MCA
                            </div>

                            <div style="
                                color:#6b7280;
                                margin-top:2px;
                            ">
                                ID: {escape(str(registro['ID']))}
                            </div>

                            {matricula_html}

                        </div>
                        """
                    )

                detalhes_medicoes = "".join(
                    linhas_medicoes[:12]
                )

                if quantidade > 12:

                    detalhes_medicoes += (
                        f"""
                        <div style="
                            margin-top:7px;
                            color:#6b7280;
                            font-size:10px;
                            text-align:center;
                        ">
                            + {quantidade - 12}
                            medições adicionais
                        </div>
                        """
                    )

                popup = f"""
                <div style="
                    width:280px;
                    font-family:Arial,sans-serif;
                    color:#111827;
                ">

                    <div style="
                        font-size:17px;
                        font-weight:800;
                        margin-bottom:2px;
                    ">
                        {bairro_html}
                    </div>

                    <div style="
                        font-size:11px;
                        color:#6b7280;
                        margin-bottom:12px;
                    ">
                        {municipio_html}
                    </div>

                    <div style="
                        display:flex;
                        gap:6px;
                        margin-bottom:10px;
                    ">

                        <div style="
                            flex:1;
                            background:#f8fafc;
                            border-radius:8px;
                            padding:8px;
                            border-left:4px solid {cor};
                        ">

                            <div style="
                                font-size:10px;
                                color:#64748b;
                            ">
                                MÉDIA
                            </div>

                            <div style="
                                font-size:18px;
                                font-weight:800;
                            ">
                                {media_formatada} MCA
                            </div>

                        </div>

                        <div style="
                            width:92px;
                            background:#f8fafc;
                            border-radius:8px;
                            padding:8px;
                        ">

                            <div style="
                                font-size:10px;
                                color:#64748b;
                            ">
                                MEDIÇÕES
                            </div>

                            <div style="
                                font-size:18px;
                                font-weight:800;
                            ">
                                {quantidade}
                            </div>

                        </div>

                    </div>

                    <div style="
                        font-size:11px;
                        color:#475569;
                        margin-bottom:8px;
                    ">
                        Faixa: <b>{classificacao}</b><br>
                        Mínima: <b>{min_formatado} MCA</b> ·
                        Máxima: <b>{max_formatado} MCA</b>
                    </div>

                    <div style="
                        max-height:220px;
                        overflow-y:auto;
                        border-top:1px solid #e5e7eb;
                    ">
                        {detalhes_medicoes}
                    </div>

                </div>
                """

                if mostrar_rotulos:

                    marker_html = f"""
                    <div
                        class="bp-marker-wrapper bp-bairro-marker-wrapper"
                        data-bp-priority="{quantidade}"
                        style="
                            position:relative;
                            width:44px;
                            height:75px;
                            overflow:visible;
                            font-family:Arial,sans-serif;
                            pointer-events:auto;
                        "
                    >

                        {gerar_pin_svg(
                            cor,
                            "bairro"
                        )}

                        <div
                            class="bp-label-card bp-label-card-bairro"
                            data-bp-default="top"
                        >

                            <div class="bp-label-title">
                                {bairro_html}
                            </div>

                            <div class="bp-label-data">

                                <span class="bp-label-pressure">
                                    {media_formatada} MCA
                                </span>

                                <span class="bp-label-separator"></span>

                                <span>
                                    {quantidade}
                                    {texto_medicoes}
                                </span>

                            </div>

                        </div>

                    </div>
                    """

                else:

                    marker_html = f"""
                    <div
                        class="bp-marker-wrapper bp-bairro-marker-wrapper"
                        data-bp-priority="{quantidade}"
                        style="
                            position:relative;
                            width:44px;
                            height:75px;
                            overflow:visible;
                            font-family:Arial,sans-serif;
                            pointer-events:auto;
                        "
                    >

                        {gerar_pin_svg(
                            cor,
                            "bairro"
                        )}

                    </div>
                    """

                folium.map.Marker(
                    location=[
                        lat_bairro,
                        lon_bairro
                    ],
                    icon=folium.DivIcon(
                        html=marker_html,
                        icon_size=(
                            44,
                            75
                        ),
                        icon_anchor=(
                            22,
                            75
                        ),
                        class_name=(
                            "pressao-bairro-marker"
                        )
                    ),
                    popup=folium.Popup(
                        popup,
                        max_width=320
                    ),
                    tooltip=(
                        f"{bairro} | "
                        f"{media_formatada} MCA | "
                        f"{quantidade} {texto_medicoes}"
                    )
                ).add_to(m)


# ============================================================
# ESTILO DOS PINS E RÓTULOS
# ============================================================
map_style_html = """
<style>

    .leaflet-marker-icon.pressao-bairro-marker,
    .leaflet-marker-icon.pressao-individual-marker {

        background: transparent !important;
        border: 0 !important;
        overflow: visible !important;
        padding: 0 !important;
        margin: 0 !important;
    }


    .pressao-bairro-marker .bp-marker-wrapper,
    .pressao-individual-marker .bp-marker-wrapper {

        position: relative !important;
        overflow: visible !important;
        pointer-events: auto !important;
        font-family: Arial, sans-serif;
    }


    .bp-pin-svg {

        display: block !important;
        visibility: visible !important;
        opacity: 1 !important;
        overflow: visible !important;
        pointer-events: auto !important;
    }


    .pressao-bairro-marker .bp-pin-svg,
    .pressao-individual-marker .bp-pin-svg {

        z-index: 1100 !important;
    }


    .bp-label-card {

        position: absolute;

        width: 158px;

        box-sizing: border-box;

        padding: 5px 8px;

        background: rgba(255, 255, 255, 0.97);

        border: 1px solid #d7dee8;

        border-left: 3px solid var(--bp-color, #64748b);

        border-radius: 6px;

        box-shadow:
            0 2px 8px rgba(15, 23, 42, 0.20);

        color: #111827;

        font-family: Arial, sans-serif;

        display: block;

        opacity: 1;

        visibility: visible;

        z-index: 1000;

        pointer-events: none;

        line-height: 1.1;

        white-space: normal;

        transition:
            left 0.12s ease,
            top 0.12s ease,
            transform 0.12s ease;

        overflow: hidden;
    }


    .bp-marker-wrapper {

        --bp-color: #64748b;
    }


    .bp-label-title {

        font-size: 11px;

        font-weight: 800;

        line-height: 1.15;

        white-space: nowrap;

        overflow: hidden;

        text-overflow: ellipsis;

        color: #111827;
    }


    .bp-label-data {

        display: flex;

        align-items: center;

        gap: 6px;

        margin-top: 4px;

        font-size: 9px;

        line-height: 1.1;

        color: #475569;

        white-space: nowrap;
    }


    .bp-label-pressure {

        font-weight: 800;

        color: #111827;
    }


    .bp-label-separator {

        display: inline-block;

        width: 1px;

        height: 10px;

        background: #cbd5e1;

        flex: 0 0 auto;
    }


    .bp-label-card-individual {

        /* Mesmo cartão de pressão, agora com cota opcional à direita. */
        width: max-content;
        min-width: 66px;
        max-width: 165px;

        padding: 4px 6px 4px 8px;
        border-radius: 999px;

        /* Rótulos sempre presentes: o JavaScript só ajusta a posição. */
        visibility: visible;
        transition: none;

        white-space: nowrap;
    }


    .bp-label-card-individual .bp-label-data {

        font-size: 10px;

        margin-top: 0;

        gap: 6px;

        justify-content: center;
    }

    .bp-label-elevation {
        display: inline-block;
        flex: 0 0 auto;
        padding: 2px 5px;
        border: 1px solid #dbe3ee;
        border-radius: 999px;
        background: #f1f5f9;
        font-size: 9px;
        font-weight: 700;
        color: #475569;
        white-space: nowrap;
    }


    .bp-label-card-bairro {

        width: 158px;
    }


    .pressao-bairro-marker .leaflet-tooltip,
    .pressao-individual-marker .leaflet-tooltip {

        display: none !important;
    }

</style>
"""

m.get_root().html.add_child(
    Element(map_style_html)
)


# ============================================================
# RÓTULOS INDIVIDUAIS — ANCORADOS, SEM OCULTAÇÃO POR COLISÃO
# ============================================================
# Mantém todas as medições visíveis. Posiciona cada rótulo tocando a borda
# do pin sempre que possível. Em áreas densas, busca posições alternativas
# progressivamente mais distantes. Não utiliza display:none/visibility:hidden
# para resolver colisões; quando a geometria não permite separação completa,
# escolhe a posição de menor interferência e mantém o rótulo visível.
# A rotina roda após a criação dos pins e novamente após zoom/pan/resize.
class ReorganizarRotulosIndividuais(MacroElement):
    def __init__(self):
        super().__init__()
        self._name = "ReorganizarRotulosIndividuais"
        self._template = Template(r"""
{% macro script(this, kwargs) %}
(function() {
    var mapa = {{this._parent.get_name()}};
    var temporizador = null;

    // Retângulos em coordenadas da janela (getBoundingClientRect).
    function retangulo(x, y, largura, altura) {
        return {left:x, top:y, right:x+largura, bottom:y+altura};
    }

    function intersecao(a, b) {
        var w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        var h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        return (w > 0 && h > 0) ? w*h : 0;
    }

    function pontosCandidatos(pin, largura, altura) {
        var cx = (pin.left + pin.right) / 2;
        var cy = (pin.top + pin.bottom) / 2;
        var w = largura, h = altura;
        var candidatos = [];
        var vistos = {};

        function colocar(x, y, afastamento) {
            var chave = Math.round(x)+":"+Math.round(y);
            if (vistos[chave]) return;
            vistos[chave] = true;
            candidatos.push({rect:retangulo(x,y,w,h), distancia:afastamento});
        }

        // Primeiras posições: o cartão literalmente TOCA o pin (0 px).
        // As laterais ficam centralizadas na cabeça da gota.
        var cyCabeça = pin.top + Math.min(15, (pin.bottom-pin.top)*0.35);
        var bases = [
            {x:cx-w/2, y:pin.top-h},
            {x:pin.right, y:cyCabeça-h/2},
            {x:pin.left-w, y:cyCabeça-h/2},
            {x:cx-w/2, y:pin.bottom},
            {x:pin.right, y:pin.top-h},
            {x:pin.left-w, y:pin.top-h},
            {x:pin.right, y:pin.bottom},
            {x:pin.left-w, y:pin.bottom}
        ];
        bases.forEach(function(p) {colocar(p.x,p.y,0);});

        // Mais opções próximas antes de afastar vertical ou horizontalmente.
        [-26,-13,13,26].forEach(function(dx) {
            colocar(cx-w/2+dx,pin.top-h,Math.abs(dx));
            colocar(cx-w/2+dx,pin.bottom,Math.abs(dx));
        });
        [-16,16].forEach(function(dy) {
            colocar(pin.right,cyCabeça-h/2+dy,Math.abs(dy));
            colocar(pin.left-w,cyCabeça-h/2+dy,Math.abs(dy));
        });

        // Apenas se necessário: anéis progressivos para regiões congestionadas.
        [6,12,20,30,44,60,80,105,135].forEach(function(d) {
            colocar(cx-w/2,pin.top-h-d,d);
            colocar(pin.right+d,cyCabeça-h/2,d);
            colocar(pin.left-w-d,cyCabeça-h/2,d);
            colocar(cx-w/2,pin.bottom+d,d);
            colocar(pin.right+d,pin.top-h-d,d*1.42);
            colocar(pin.left-w-d,pin.top-h-d,d*1.42);
            colocar(pin.right+d,pin.bottom+d,d*1.42);
            colocar(pin.left-w-d,pin.bottom+d,d*1.42);
        });
        return candidatos;
    }

    function avaliar(item, candidato, itens, pinos, limites) {
        var r = candidato.rect;
        var pontuacao = candidato.distancia * 0.65;

        // Evita cartões recortados pelo mapa. Penaliza em vez de ocultar.
        var sobra = Math.max(0,limites.left+2-r.left) +
                    Math.max(0,r.right-limites.right+2) +
                    Math.max(0,limites.top+2-r.top) +
                    Math.max(0,r.bottom-limites.bottom+2);
        pontuacao += sobra * 350;

        // Não deixar o próprio cartão encobrir a gota correspondente.
        pontuacao += intersecao(r,item.pin) * 20;

        // As gotas sempre permanecem no mapa; a rotina evita encobri-las.
        pinos.forEach(function(p) {
            if (p.id !== item.id) {
                pontuacao += intersecao(r,p.rect) * 16;
            }
        });

        // Conflito com outros cartões: custo muito maior que afastamento.
        itens.forEach(function(outro) {
            if (outro.id !== item.id && outro.posicao) {
                var sobreposicao = intersecao(r,outro.posicao);
                if (sobreposicao > 0) pontuacao += 2500 + sobreposicao * 14;
            }
        });
        return pontuacao;
    }

    function melhorPosicao(item, itens, pinos, limites) {
        var melhor = null, custoMinimo = Infinity;
        item.candidatos.forEach(function(candidato) {
            var custo = avaliar(item,candidato,itens,pinos,limites);
            if (custo < custoMinimo) {
                melhor = candidato;
                custoMinimo = custo;
            }
        });
        return melhor;
    }

    function reorganizar() {
        var container = mapa.getContainer();
        if (!container) return;
        var limites = container.getBoundingClientRect();
        var wrappers = container.querySelectorAll(
            ".pressao-individual-marker .bp-individual-marker-wrapper"
        );
        if (!wrappers.length) return;

        var itens = [], pinos = [];
        wrappers.forEach(function(wrapper, i) {
            var rotulo = wrapper.querySelector(".bp-label-card-individual");
            var svg = wrapper.querySelector(".bp-pin-svg");
            if (!svg || !rotulo) return;
            var pin = svg.getBoundingClientRect();
            if (!pin.width || !pin.height) return;

            // Marcadores fora da tela não entram no cálculo; a etiqueta não é
            // removida, apenas fica naturalmente fora da área visível.
            if (pin.right < limites.left || pin.left > limites.right ||
                pin.bottom < limites.top || pin.top > limites.bottom) return;

            rotulo.style.display = "block";
            rotulo.style.visibility = "hidden"; // apenas durante a medição deste ciclo
            rotulo.style.transform = "none";
            var largura = rotulo.offsetWidth;
            var altura = rotulo.offsetHeight;
            if (!largura || !altura) {
                rotulo.style.visibility = "visible";
                return;
            }

            var caminho = svg.querySelector("path");
            var cor = caminho && caminho.getAttribute("fill");
            if (cor) wrapper.style.setProperty("--bp-color",cor);

            var item = {
                id:i, wrapper:wrapper, rotulo:rotulo, pin:pin,
                prioridade:Number(wrapper.getAttribute("data-bp-priority") || 0),
                posicao:null,
                candidatos:pontosCandidatos(pin,largura,altura)
            };
            itens.push(item);
            pinos.push({id:i,rect:pin});
        });

        // Prioridade no desempate para pressão zero, SEM eliminar outros rótulos.
        itens.sort(function(a,b) {
            return (b.prioridade - a.prioridade) || (a.id - b.id);
        });

        // Primeira alocação: procura espaços vazios junto aos respectivos pins.
        itens.forEach(function(item) {
            var melhor = melhorPosicao(item,itens,pinos,limites);
            item.posicao = melhor ? melhor.rect : null;
        });

        // Reajuste local: permite mover uma etiqueta que bloqueia outras.
        // Não muda os pins e nunca descarta rótulos.
        for (var rodada=0; rodada<3; rodada++) {
            var mudou = false;
            itens.forEach(function(item) {
                var anterior = item.posicao;
                item.posicao = null;
                var melhor = melhorPosicao(item,itens,pinos,limites);
                item.posicao = melhor ? melhor.rect : anterior;
                if (item.posicao && anterior &&
                    (item.posicao.left !== anterior.left || item.posicao.top !== anterior.top)) {
                    mudou = true;
                }
            });
            if (!mudou) break;
        }

        itens.forEach(function(item) {
            var r = item.posicao;
            if (r) {
                var origem = item.wrapper.getBoundingClientRect();
                item.rotulo.style.left = (r.left-origem.left)+"px";
                item.rotulo.style.top = (r.top-origem.top)+"px";
            }
            // Mesmo em densidade extrema, a etiqueta permanece visível.
            item.rotulo.style.visibility = "visible";
        });
    }

    function agendar() {
        if (temporizador !== null) clearTimeout(temporizador);
        temporizador = setTimeout(function() {
            temporizador = null;
            requestAnimationFrame(reorganizar);
        },90);
    }

    mapa.whenReady(function() {
        agendar();
        mapa.on("zoomend moveend resize viewreset",agendar);
        // Reposiciona os rótulos também após transições de tela cheia.
        mapa.on("enterFullscreen exitFullscreen",agendar);
        window.addEventListener("resize",agendar);
    });
})();
{% endmacro %}
""")


if modo_visualizacao == "Medições individuais" and mostrar_rotulos:
    m.add_child(ReorganizarRotulosIndividuais())

# ============================================================
# LEGENDA DAS CORES
# ============================================================
legend_html = f"""
<div style="
    position: fixed;
    bottom: 24px;
    left: 24px;
    z-index: 9999;
    background-color: rgba(255, 255, 255, 0.94);
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    padding: 7px 9px;
    box-shadow: 0 2px 7px rgba(0, 0, 0, 0.16);
    font-family: Arial, sans-serif;
    font-size: 10px;
    line-height: 1.35;
    width: 194px;
    color: #1f2937;
">

    <div style="
        font-weight: 700;
        margin-bottom: 4px;
        color: #111827;
    ">
        Pressão
    </div>

    <div style="
        white-space: nowrap;
    ">
        <span style="
            display:inline-block;
            width:9px;
            height:9px;
            border-radius:50%;
            background:{COR_SEM_PRESSAO};
            margin-right:5px;
            vertical-align:middle;
        "></span>

        Sem Pressão (0 MCA)
    </div>

    <div style="
        white-space: nowrap;
    ">
        <span style="
            display:inline-block;
            width:9px;
            height:9px;
            border-radius:50%;
            background:{COR_BAIXA_PRESSAO};
            margin-right:5px;
            vertical-align:middle;
        "></span>

        Baixa Pressão (&gt; 0 e ≤ 5 MCA)
    </div>

    <div style="
        white-space: nowrap;
    ">
        <span style="
            display:inline-block;
            width:9px;
            height:9px;
            border-radius:50%;
            background:{COR_EM_ATENCAO};
            margin-right:5px;
            vertical-align:middle;
        "></span>

        Em Atenção (&gt; 5 e &lt; 10 MCA)
    </div>

    <div style="
        white-space: nowrap;
    ">
        <span style="
            display:inline-block;
            width:9px;
            height:9px;
            border-radius:50%;
            background:{COR_NORMAL};
            margin-right:5px;
            vertical-align:middle;
        "></span>

        Normal (≥ 10 e ≤ 15 MCA)
    </div>

    <div style="
        white-space: nowrap;
    ">
        <span style="
            display:inline-block;
            width:9px;
            height:9px;
            border-radius:50%;
            background:{COR_ALTA_PRESSAO};
            margin-right:5px;
            vertical-align:middle;
        "></span>

        Alta Pressão (&gt; 15 MCA)
    </div>

</div>
"""

m.get_root().html.add_child(
    Element(legend_html)
)


# ============================================================
# RENDERIZAÇÃO DO MAPA
# ============================================================
st_folium(
    m,
    width="100%",
    height=520,
    returned_objects=[],
    key="mapa_principal_pressao",
)


st.divider()


# ============================================================
# TABELA
# ============================================================
st.subheader(
    "📋 Registro de Pontos"
)

if not df_filtrado.empty:

    df_show = df_filtrado[
        [
            "ID",
            "Data",
            "Municipio",
            "Matricula",
            "Bairro",
            "Latitude",
            "Longitude",
            "Pressao_MCA",
            "Observacao"
        ]
    ].reset_index(
        drop=True
    ).copy()
    df_show["Observacao"] = df_show["Observacao"].map(
        bp_observacao_visivel
    )

    # ========================================================
    # OPERADOR — SOMENTE VISUALIZAÇÃO
    # ========================================================
    if eh_operador:

        st.dataframe(
            df_show,
            use_container_width=True,
            height=300,
            hide_index=True
        )

    # ========================================================
    # ADMIN/USUÁRIO — TABELA INTERATIVA
    # ========================================================
    else:

        evento = st.dataframe(
            df_show,
            use_container_width=True,
            height=300,
            on_select="rerun",
            selection_mode="single-row",
            key="tabela_registros"
        )

        linhas_selecionadas = (
            evento.selection.rows
            if evento and evento.selection
            else []
        )

        if linhas_selecionadas:

            idx = linhas_selecionadas[0]

            registro = df_show.iloc[
                idx
            ]

            id_sel = str(
                registro["ID"]
            )
            somente_leitura = bool(
                df_filtrado.iloc[idx].get("_arquivo_historico", False)
            )
            if somente_leitura:
                st.caption("Este registro está no histórico do Supabase e é somente leitura.")

            col_a, col_b, _ = st.columns(
                [1, 1, 4]
            )

            with col_a:

                if st.button(
                    "✏️ Editar",
                    use_container_width=True,
                    disabled=somente_leitura
                ):

                    modal_editar_ponto(
                        id_sel
                    )

            with col_b:

                if st.button(
                    "🗑️ Excluir",
                    use_container_width=True,
                    disabled=somente_leitura
                ):

                    if excluir_ponto(
                        id_sel
                    ):

                        st.success(
                            "Excluído com sucesso."
                        )

                        st.rerun()


# ============================================================
# ANALÍTICO
# ============================================================
st.divider()

st.subheader(
    "📈 Análise de Tendência e Variação por Bairro"
)

st.markdown(
    "Selecione um período e um bairro para acompanhar "
    "o histórico e a variação da pressão ao longo do tempo."
)

if not df_all.empty:

    col_g1, col_g2, col_g3 = st.columns(
        [2, 2, 2]
    )

    with col_g1:

        data_inicio_padrao = (
            hoje - timedelta(
                days=30
            )
        )

        data_ini_analise = st.date_input(
            "Data Inicial",
            value=data_inicio_padrao,
            format="DD/MM/YYYY",
            key="analise_ini"
        )

    with col_g2:

        data_fim_analise = st.date_input(
            "Data Final",
            value=hoje,
            format="DD/MM/YYYY",
            key="analise_fim"
        )

    with col_g3:

        bairros_disponiveis = sorted(
            df_all["Bairro"]
            .dropna()
            .unique()
            .tolist()
        )

        bairro_analise = st.selectbox(
            "Selecione o Bairro",
            options=[
                "Selecione..."
            ]
            +
            bairros_disponiveis,
            key="analise_bairro"
        )

    if bairro_analise != "Selecione...":

        if data_ini_analise > data_fim_analise:

            st.error(
                "A data inicial não pode ser maior "
                "que a data final."
            )

        else:

            df_tendencia = df_all[
                df_all["Bairro"]
                == bairro_analise
            ].copy()

            if not df_tendencia.empty:

                df_tendencia["DataObj"] = pd.to_datetime(
                    df_tendencia["Data"],
                    format="%d/%m/%Y",
                    errors="coerce"
                )

                df_tendencia = (
                    df_tendencia
                    .dropna(
                        subset=["DataObj"]
                    )
                )

                mask = (
                    (
                        df_tendencia[
                            "DataObj"
                        ].dt.date
                        >= data_ini_analise
                    )
                    &
                    (
                        df_tendencia[
                            "DataObj"
                        ].dt.date
                        <= data_fim_analise
                    )
                )

                df_tendencia = (
                    df_tendencia
                    .loc[mask]
                    .sort_values(
                        "DataObj"
                    )
                )

                if not df_tendencia.empty:

                    fig = px.line(
                        df_tendencia,
                        x="Data",
                        y="Pressao_MCA",
                        markers=True,
                        title=(
                            f"Evolução da Pressão (MCA) "
                            f"— {bairro_analise}"
                        ),
                        labels={
                            "Data": "Data do Registro",
                            "Pressao_MCA": "Pressão (MCA)"
                        },
                    )

                    fig.add_hline(
                        y=5,
                        line_dash="dash",
                        line_color=COR_BAIXA_PRESSAO,
                        annotation_text=(
                            "Limite: "
                            "Baixa Pressão (5 MCA)"
                        ),
                        annotation_position="top left"
                    )

                    fig.add_hline(
                        y=10,
                        line_dash="dash",
                        line_color=COR_NORMAL,
                        annotation_text="Normal a partir de 10 MCA",
                        annotation_position="top left"
                    )

                    fig.add_hline(
                        y=15,
                        line_dash="dash",
                        line_color=COR_ALTA_PRESSAO,
                        annotation_text=(
                            "Limite superior da faixa normal (15 MCA)"
                        ),
                        annotation_position="top left"
                    )

                    fig.add_hline(
                        y=0,
                        line_dash="solid",
                        line_color=COR_SEM_PRESSAO,
                        annotation_text=(
                            "Sem Pressão (0 MCA)"
                        ),
                        annotation_position="bottom left"
                    )

                    fig.update_traces(
                        line_color="#0284c7",
                        line_width=3,
                        marker_size=8
                    )

                    fig.update_layout(
                        xaxis_title="Data",
                        yaxis_title="Pressão (MCA)",
                        hovermode="x unified"
                    )

                    st.plotly_chart(
                        fig,
                        use_container_width=True
                    )

                else:

                    st.info(
                        f"Nenhum registro encontrado "
                        f"para o bairro "
                        f"**{bairro_analise}** "
                        f"no período selecionado."
                    )

            else:

                st.warning(
                    "Não há dados históricos suficientes "
                    "para este bairro."
                )

    else:

        st.info(
            "👆 Selecione um **Bairro** acima para "
            "carregar a análise de tendência temporal."
        )

else:

    st.info(
        "Aguardando dados para gerar "
        "o gráfico de tendência."
    )
