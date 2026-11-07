
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
from streamlit_autorefresh import st_autorefresh
import io
import hashlib
import uuid
from typing import Optional
import time
import re
import math
import unicodedata
from pathlib import Path
from html import escape
from branca.element import Element, MacroElement
from jinja2 import Template

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Mapeamento de Pressão - COI",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded"
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("2")

# Estilização CSS para ocultar navegação padrão
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
# TRAVA DE SEGURANÇA E CONTROLE DE SESSÃO
# ============================================================
from auth import verificar_autenticacao, pode_executar_acao, exigir_acao

if not verificar_autenticacao():
    st.warning("Sessão não iniciada ou expirada.")
    if st.button("Ir para o Login"):
        st.switch_page("app.py")
    st.stop()

# ============================================================
# CONSTANTES E ESTRUTURA DE COLUNAS
# ============================================================
LAT_BASE = -5.0892
LON_BASE = -42.8019

# A cota do terreno é amostrada nos GeoTIFFs de dados/topografia,
# com o mesmo mecanismo do Módulo 1. Não sobrepõe camada de relevo.
# Fonte apenas de leitura: não modifica medições do Google Sheets.
MP_RAIZ = Path(__file__).resolve().parent
if MP_RAIZ.name.lower() == "pages":
    MP_RAIZ = MP_RAIZ.parent
MP_DEM_DIRETORIO = MP_RAIZ / "dados" / "topografia"

# Reutiliza o conversor inteligente do Módulo 1. É o mesmo arquivo auxiliar
# utilizado pela importação automatizada, e não uma segunda implementação.
import sys
if str(MP_RAIZ) not in sys.path:
    sys.path.insert(0, str(MP_RAIZ))
from sai_pressao_importacao import converter_bruta, ler_arquivo


# Mesma classificação e mesmas cores dos marcadores do Módulo 1.
COR_SEM_PRESSAO = "#FF5C60"
COR_BAIXA_PRESSAO = "#F8DC00"
COR_EM_ATENCAO = "#FF8FE1"
COR_ALTA_PRESSAO = "#A11FFF"


def mp_classificar_pressao(pressao):
    valor = float(pressao)
    if valor == 0:
        return "Sem Pressão", COR_SEM_PRESSAO
    if valor <= 5:
        return "Baixa Pressão", COR_BAIXA_PRESSAO
    if valor <= 15:
        return "Em Atenção", COR_EM_ATENCAO
    return "Alta Pressão", COR_ALTA_PRESSAO


def mp_formatar_numero(numero, casas=2):
    return f"{float(numero):.{casas}f}".rstrip("0").rstrip(".").replace(".", ",")


@st.cache_data(show_spinner=False, max_entries=128)
def mp_dem_limites_geograficos(caminho: str, versao: int, tamanho: int) -> tuple:
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


def mp_catalogo_topografia() -> tuple:
    """Descobre GeoTIFFs a cada execução; metadados válidos ficam em cache.

    Arquivos de menor cobertura são consultados primeiro em sobreposições;
    um raster maior pode complementar pixels sem dados (NoData).
    Retorna (catálogo, alertas) sem impedir que o mapa seja exibido.
    """
    if not MP_DEM_DIRETORIO.is_dir():
        return (), ()

    arquivos = sorted(
        (arquivo for arquivo in MP_DEM_DIRETORIO.rglob("*")
         if arquivo.is_file() and arquivo.suffix.lower() in (".tif", ".tiff")),
        key=lambda arquivo: str(arquivo).casefold(),
    )
    catalogo = []
    alertas = []
    for arquivo in arquivos:
        try:
            stat = arquivo.stat()
            oeste, sul, leste, norte = mp_dem_limites_geograficos(
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
def mp_cotas_terreno(caminho: str, versao: int, coordenadas: tuple) -> tuple:
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


def mp_cotas_automaticas(coordenadas: tuple) -> tuple:
    """Consulta em lote o GeoTIFF que cobre cada (latitude, longitude).

    O catálogo é refeito a cada rerun (novos arquivos são detectados sem
    alterações no código). Só abre rasters com pontos dentro dos seus limites.
    Em áreas sobrepostas, um pixel NoData permite tentar o próximo arquivo.
    """
    catalogo, alertas_catalogo = mp_catalogo_topografia()
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
            amostras = mp_cotas_terreno(caminho, versao, amostras_coords)
        except (OSError, ValueError, TypeError, RuntimeError, ImportError) as erro:
            alertas.append(f"{Path(caminho).name}: {erro}")
            continue
        for indice, valor in zip(pendentes, amostras):
            if valor is not None and math.isfinite(float(valor)):
                cotas[indice] = float(valor)

        if all(cota is not None for cota in cotas):
            break
    return tuple(cotas), tuple(alertas)



SPREADSHEET_ID = "1idCgabn5g5X48hmdE-gqWfrUKUf2XxWGB5M_yJFk0yM"
COLUNAS_PADRAO = ["Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observacao"]
CABECALHO_SHEETS = ["Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observação"]


# ============================================================
# CONFERÊNCIA DO CABEÇALHO E LEITURA DE LAYOUTS LEGADOS
# ============================================================
def _chave_cabecalho(campo):
    texto = unicodedata.normalize("NFKD", str(campo or "").strip().upper())
    return "".join(ch for ch in texto if ch.isalnum() and not unicodedata.combining(ch))


def _data_valida_planilha(valor):
    """Data da célula em formatos usuais do Sheets, sem inventar datas."""
    if isinstance(valor, (datetime, date, pd.Timestamp)):
        try:
            return pd.Timestamp(valor).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return ""
    texto = str(valor or "").strip().strip("'")
    if not texto or texto.lower() in {"nan", "none", "nat", "-"}:
        return ""
    for formato in (
        "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(texto, formato).strftime("%d/%m/%Y")
        except ValueError:
            pass
    # Algumas células do Sheets contêm a data como número de série.
    if re.fullmatch(r"[0-9]{5}(?:[.,][0-9]+)?", texto):
        try:
            serial = float(texto.replace(",", "."))
            if 20000 <= serial <= 90000:
                return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%d/%m/%Y")
        except (ValueError, OverflowError):
            pass
    return ""


def _numero_valido_planilha(valor, minimo, maximo):
    """Aceita decimal com vírgula/ponto e não aceita NaN/Inf."""
    if valor is None or str(valor).strip().lower() in {"", "nan", "none", "nat"}:
        return None
    texto = str(valor).strip().replace(" ", "")
    if "," in texto and "." in texto:
        texto = (texto.replace(".", "").replace(",", ".")
                 if texto.rfind(",") > texto.rfind(".") else texto.replace(",", ""))
    else:
        texto = texto.replace(",", ".")
    try:
        valor_num = float(texto)
        if math.isfinite(valor_num) and minimo <= valor_num <= maximo:
            return valor_num
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _eh_medicao_completa(campos):
    """Valida os 7 campos de uma medição antes de definir o deslocamento."""
    campos = list(campos) + [""] * max(0, 7 - len(campos))
    data, municipio, ponto, lat, lon, mca, _obs = campos[:7]
    return (
        bool(_data_valida_planilha(data))
        and bool(str(municipio).strip())
        and bool(str(ponto).strip())
        and _numero_valido_planilha(lat, -90, 90) not in (None, 0)
        and _numero_valido_planilha(lon, -180, 180) not in (None, 0)
        and _numero_valido_planilha(mca, 0, float("inf")) is not None
    )


def _formato_cabecalho(cabecalho):
    chaves = [_chave_cabecalho(v) for v in cabecalho]
    esperado = [_chave_cabecalho(v) for v in CABECALHO_SHEETS]
    if chaves[:7] == esperado and not any(chaves[7:]):
        return "sem_id"
    if chaves[:8] == ["ID"] + esperado and not any(chaves[8:]):
        return "com_id"
    return "desconhecido"


def _classificar_linha_medicao(linha, modo_cabecalho):
    """Localiza os dados sem alterar a planilha; aceita linhas A:G e B:H."""
    valores = list(linha) + [""] * max(0, 8 - len(linha))
    sem_id = _eh_medicao_completa(valores[:7])
    com_id = _eh_medicao_completa(valores[1:8])
    # Não usar a primeira hipótese se existir um valor extra não explicado em H.
    if sem_id and not str(valores[7]).strip() and not com_id:
        return "sem_id", valores[:7]
    if com_id and modo_cabecalho == "com_id" and not sem_id:
        return "com_id", valores[1:8]
    if com_id and not sem_id:
        return "com_id", valores[1:8]
    return "invalida", None


def conferir_estrutura_planilha(ws):
    """Não migra nem descarta linhas. Identifica os dois layouts já usados no banco.

    Cabeçalho A:H com ID pode conviver com linhas antigas A:G, gravadas antes
    da correção. Exigir que a linha 2 determine o formato de toda a aba
    bloqueava a aplicação indevidamente.
    """
    cabecalho = ws.row_values(1)
    if not any(str(v).strip() for v in cabecalho):
        exigir_acao("2", "editar")
        ws.update(range_name="A1:G1", values=[CABECALHO_SHEETS], value_input_option="RAW")
        return "sem_id"
    modo = _formato_cabecalho(cabecalho)
    if modo == "desconhecido":
        raise ValueError(
            "Cabeçalho não reconhecido na aba mapeamento_pressao. "
            "Nenhum dado foi modificado. Esperado: Data, Município, Pontos, "
            "Latitude, Longitude, MCA, Observação (com ou sem ID inicial)."
        )
    return modo

# ============================================================
# CONEXÃO COM GOOGLE SHEETS
# ============================================================
@st.cache_resource
def conectar_google_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    config = st.secrets["gcp_json"]
    credentials_dict = json.loads(config) if isinstance(config, str) else dict(config)
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    gc = gspread.authorize(credentials)
    sh = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet("mapeamento_pressao")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="mapeamento_pressao", rows=1000, cols=20)

    # Não insere outra linha de cabeçalho: isso desalinhará novamente o banco.
    conferir_estrutura_planilha(ws)
    return ws


try:
    worksheet = conectar_google_sheets()
except Exception as e:
    st.error(f"❌ Erro ao conferir o banco do Módulo 2: {e}")
    st.stop()

# ============================================================
# FUNÇÕES DE CONVERSÃO E TRATAMENTO DE DADOS
# ============================================================
def normalizar_coluna(nome: str) -> str:
    nome = str(nome).strip().lower()

    mapeamento = {
        "data": "Data",
        "municipio": "Município",
        "município": "Município",
        "pontos": "Pontos",
        "ponto": "Pontos",
        "latitude": "Latitude",
        "lat": "Latitude",
        "longitude": "Longitude",
        "lon": "Longitude",
        "long": "Longitude",
        "mca": "MCA",
        "pressao": "MCA",
        "pressão": "MCA",
        "observacao": "Observacao",
        "observação": "Observacao",
        "obs": "Observacao"
    }

    return mapeamento.get(nome, nome.title())


def parse_float(valor, default=None):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return default

    if isinstance(valor, (int, float)):
        return float(valor)

    try:
        texto = str(valor).strip()

        if not texto or texto.lower() in ("nan", "none", "nat", ""):
            return default

        texto = texto.replace(",", ".").replace(" ", "")

        return float(texto)

    except (ValueError, TypeError):
        return default


def normalizar_coordenada(valor, tipo: str = "lat") -> Optional[float]:
    num = parse_float(valor, default=None)

    if num is None:
        return None

    if tipo == "lat" and not (-90.0 <= num <= 90.0):
        return None

    if tipo == "lon" and not (-180.0 <= num <= 180.0):
        return None

    if num == 0.0:
        return None

    return round(float(num), 6)


def normalizar_data(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""

    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")

    texto = str(valor).strip()

    if not texto or texto.lower() in ("nan", "none", "nat", ""):
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
            return datetime.strptime(texto, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue

    return texto


def carregar_dados() -> pd.DataFrame:
    """Lê o banco por posição, inclusive linhas legadas deslocadas com ID."""
    try:
        valores = worksheet.get_all_values()
    except Exception as e:
        st.error(f"Erro ao ler planilha: {e}")
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    # A planilha pode ficar apenas com o cabeçalho após o arquivamento;
    # nesse caso, as medições devem continuar disponíveis no Supabase.
    if not valores:
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    modo = _formato_cabecalho(valores[0])
    if modo == "desconhecido":
        st.error("Cabeçalho do banco não reconhecido; leitura suspensa para proteger os dados.")
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    registros, linhas_nao_interpretadas = [], []
    for idx_linha, linha in enumerate(valores[1:], start=0):
        if not any(str(v).strip() for v in linha):
            continue
        formato, campos = _classificar_linha_medicao(linha, modo)
        if campos is None:
            linhas_nao_interpretadas.append(idx_linha + 2)
            continue
        registro = dict(zip(COLUNAS_PADRAO, campos))
        registro["Data"] = _data_valida_planilha(registro["Data"])
        registro["_linha_real"] = idx_linha  # Linha física do Sheets: +2.
        registro["_layout_linha"] = formato
        registro["_historico_id"] = str(linha[0]).strip() if formato == "com_id" else ""
        registro["_arquivo_historico"] = False
        registros.append(registro)

    # A consulta ao Storage é opcional e exclusivamente de leitura. Linhas
    # arquivadas recebem _linha_real=-1 e nunca podem ser editadas no Sheets.
    from historico_pressao_supabase import adicionar_modulo2, ErroHistorico
    try:
        registros = adicionar_modulo2(registros)
    except ErroHistorico as exc:
        st.error(f"Histórico indisponível: {exc}")
        st.stop()

    if linhas_nao_interpretadas:
        amostra = ", ".join(map(str, linhas_nao_interpretadas[:12]))
        restante = " ..." if len(linhas_nao_interpretadas) > 12 else ""
        st.warning(
            f"Módulo 2: {len(linhas_nao_interpretadas)} linha(s) da aba "
            f"mapeamento_pressao não seguem um formato de medição válido "
            f"(linhas {amostra}{restante}). Elas NÃO foram excluídas nem "
            "modificadas; continuam disponíveis no Google Sheets para conferência."
        )

    if not registros:
        return pd.DataFrame(columns=COLUNAS_PADRAO + ["_linha_real"])

    df = pd.DataFrame(registros)
    df["Município"] = df["Município"].astype(str).str.strip()
    df["Pontos"] = df["Pontos"].astype(str).str.strip()
    df["Latitude"] = df["Latitude"].map(lambda v: normalizar_coordenada(v, "lat"))
    df["Longitude"] = df["Longitude"].map(lambda v: normalizar_coordenada(v, "lon"))
    df["MCA"] = df["MCA"].map(lambda v: parse_float(v, 0.0))
    df["Observacao"] = df["Observacao"].map(
        lambda v: "" if str(v).strip().lower() in {"nan", "none", "nat"} else str(v).strip()
    )
    return df[COLUNAS_PADRAO + ["_linha_real", "_arquivo_historico"]].reset_index(drop=True)


def limpar_cache():
    st.cache_data.clear()


# ============================================================
# IMPORTAÇÃO INTELIGENTE — MESMO CONVERSOR DO MÓDULO 1
# ============================================================
MP_MARCADOR_ATIVIDADE = re.compile(
    r"\bSAI_ATIVIDADE\s*=\s*[A-Z0-9_-]+", flags=re.IGNORECASE
)


def mp_observacao_visivel(valor) -> str:
    """Não apresenta o ID técnico nos pop-ups, na tabela ou na prévia."""
    texto = str(valor or "").strip()
    if texto.lower() in {"nan", "none", "nat"}:
        return ""
    texto = MP_MARCADOR_ATIVIDADE.sub("", texto)
    texto = re.sub(r"(?:\s*[|;]\s*){2,}", " | ", texto)
    return texto.strip(" \t\r\n|;,/-")


def mp_observacao_preservando_id(nova, anterior) -> str:
    """Permite editar a observação sem perder a chave original da atividade."""
    visivel = mp_observacao_visivel(nova)
    marcadores = list(dict.fromkeys(MP_MARCADOR_ATIVIDADE.findall(str(anterior or ""))))
    return " | ".join([parte for parte in (visivel, *marcadores) if parte])


def mp_chave_medicao(data, municipio, ponto, lat, lon, mca, observacao):
    """Conserva deduplicação antiga; distingue atividades da base bruta.

    Na base nativa, a chave continua sendo data+município+ponto+posição.
    Na base bruta, SAI_ATIVIDADE identifica registros diferentes no mesmo
    local/data; sem marcador, MCA evita descartar medições distintas.
    """
    base = (str(data).strip(), str(municipio).strip().casefold(),
            str(ponto).strip().casefold(), f"{float(lat):.6f}", f"{float(lon):.6f}")
    achado = MP_MARCADOR_ATIVIDADE.search(str(observacao or ""))
    if achado:
        return ("atividade", base[0], base[1], achado.group().upper().replace(" ", ""))
    if " | MAT. " in str(ponto).upper():
        return ("medicao-bruta", *base, f"{float(mca):.4f}")
    return ("ponto-manual", *base)


def mp_linhas_brutas_para_modulo2(linhas) -> list[list]:
    """Mapeia as nove colunas do M1 para as sete do M2, sem perder a aferição.

    Origem M1: ID, Data, Municipio, Bairro, Latitude, Longitude,
    Pressao_MCA, Observacao, Matricula.
    Destino M2: Data, Município, Pontos, Latitude, Longitude, MCA, Observacao.
    """
    saida = []
    for indice, linha in enumerate(linhas, start=1):
        if len(linha) < 9:
            raise ValueError(f"Linha convertida {indice}: estrutura do Módulo 1 incompleta.")
        _id, data, municipio, bairro, lat, lon, pressao, observacao, matricula = linha[:9]
        data_n = _data_valida_planilha(data)
        lat_n = normalizar_coordenada(lat, "lat")
        lon_n = normalizar_coordenada(lon, "lon")
        mca_n = _numero_valido_planilha(pressao, 0, float("inf"))
        municipio_n = str(municipio or "").strip()
        bairro_n = str(bairro or "").strip()
        matricula_n = str(matricula or "").strip()
        if not data_n or not municipio_n or not bairro_n or lat_n is None or lon_n is None or mca_n is None:
            raise ValueError(f"Medição convertida {indice} não passou na validação de data, bairro, coordenadas ou MCA.")
        # Como a planilha do M2 não possui Bairro e Matrícula como colunas,
        # ambos aparecem juntos no nome do ponto, legível nos filtros e mapa.
        ponto = f"{bairro_n} | MAT. {matricula_n}" if matricula_n else bairro_n
        saida.append([data_n, municipio_n, ponto, lat_n, lon_n,
                      float(mca_n), str(observacao or "")])
    return saida


def mp_converter_planilha_modulo2(df: pd.DataFrame, data_padrao: date | None = None) -> list[list]:
    """Preserva a importação do modelo próprio do M2 para arquivos padronizados."""
    campos = {_chave_cabecalho(col): col for col in df.columns}
    obrigatorios = ("Data", "Município", "Pontos", "Latitude", "Longitude", "MCA")
    faltantes = [c for c in obrigatorios if _chave_cabecalho(c) not in campos]
    if faltantes:
        raise ValueError("Colunas do modelo Módulo 2 ausentes: " + ", ".join(faltantes))
    def valor(linha, campo, padrao=""):
        chave = campos.get(_chave_cabecalho(campo))
        v = linha.get(chave, padrao) if chave else padrao
        return "" if pd.isna(v) else v
    saida = []
    for numero, (_, linha) in enumerate(df.iterrows(), start=2):
        if not any(str(v).strip() for v in linha.values if not pd.isna(v)):
            continue
        data = _data_valida_planilha(valor(linha, "Data"))
        # Compatibilidade restrita com o modelo antigo: data vazia pode usar
        # a data do filtro; data preenchida mas inválida é sempre rejeitada.
        if not data and not str(valor(linha, "Data")).strip() and data_padrao is not None:
            data = data_padrao.strftime("%d/%m/%Y")
        municipio = str(valor(linha, "Município")).strip()
        ponto = str(valor(linha, "Pontos")).strip()
        lat = normalizar_coordenada(valor(linha, "Latitude"), "lat")
        lon = normalizar_coordenada(valor(linha, "Longitude"), "lon")
        mca = _numero_valido_planilha(valor(linha, "MCA"), 0, float("inf"))
        obs = valor(linha, "Observacao")
        if not data or not municipio or not ponto or lat is None or lon is None or mca is None:
            raise ValueError(f"Linha {numero} do modelo Módulo 2 contém data, município, ponto, coordenadas ou MCA inválidos.")
        saida.append([data, municipio, ponto, lat, lon, float(mca), str(obs)])
    return saida


def mp_processar_importacao(nome_arquivo: str, conteudo: bytes, data_padrao=None):
    """Retorna (linhas M2, resumo M1 opcional, tipo de arquivo)."""
    df = ler_arquivo(nome_arquivo, conteudo)
    cabecalhos = {_chave_cabecalho(col) for col in df.columns}
    modelo_modulo2 = { _chave_cabecalho(c) for c in
        ("Data", "Município", "Pontos", "Latitude", "Longitude", "MCA") }
    if modelo_modulo2.issubset(cabecalhos):
        return mp_converter_planilha_modulo2(df, data_padrao), None, "Modelo do Módulo 2"
    # Igual ao M1: mesmo parser, validações, extração do parecer e resultado.
    resultado = converter_bruta(df, nome_arquivo)
    return mp_linhas_brutas_para_modulo2(resultado.linhas), resultado, "Base bruta / Modelo do Módulo 1"


def adicionar_ponto(
    data_str: str,
    municipio: str,
    pontos: str,
    lat: float,
    lon: float,
    mca: float,
    obs: str
):
    # Cadastro manual usa a mesma prevenção de duplicidades do upload em lote.
    if not pode_executar_acao("2", "criar"):
        st.error("Sem permissão para esta operação.")
        return None
    return adicionar_lote_seguro([[
        str(data_str), str(municipio), str(pontos),
        float(lat), float(lon), float(mca), str(obs or "")
    ]])


def adicionar_lote_seguro(linhas_dados: list):
    if not pode_executar_acao("2", "importar"):
        st.error("Sem permissão para esta operação.")
        return 0
    if not linhas_dados:
        return 0

    df_atual = carregar_dados()
    chaves_existentes = set()
    if not df_atual.empty:
        for _, r in df_atual.iterrows():
            if pd.isna(r["Latitude"]) or pd.isna(r["Longitude"]):
                continue
            chave = mp_chave_medicao(
                r["Data"], r["Município"], r["Pontos"],
                r["Latitude"], r["Longitude"], r["MCA"], r["Observacao"]
            )
            chaves_existentes.add(chave)

    linhas_novas = []
    for linha in linhas_dados:
        d_val, mun_val, p_val, lat_val, lon_val, mca_val, obs_val = linha
        lat_n = normalizar_coordenada(lat_val, "lat")
        lon_n = normalizar_coordenada(lon_val, "lon")
        mca_n = _numero_valido_planilha(mca_val, 0, float("inf"))
        data_n = _data_valida_planilha(d_val)
        if not data_n or not str(mun_val).strip() or not str(p_val).strip() or lat_n is None or lon_n is None or mca_n is None:
            raise ValueError("Lote contém data, ponto, coordenada ou MCA inválido; nenhum registro foi gravado.")
        chave_nova = mp_chave_medicao(
            data_n, mun_val, p_val, lat_n, lon_n, mca_n, obs_val
        )
        if chave_nova not in chaves_existentes:
            linhas_novas.append([data_n, str(mun_val).strip(), str(p_val).strip(),
                                 f"{lat_n:.6f}", f"{lon_n:.6f}", str(mca_n), str(obs_val or "")])
            chaves_existentes.add(chave_nova)

    if linhas_novas:
        # Respeita o cabeçalho real sem reescrever linhas antigas A:G.
        modo = conferir_estrutura_planilha(worksheet)
        if modo == "com_id":
            dados = [[f"MP-{uuid.uuid4().hex[:16].upper()}"] + campos for campos in linhas_novas]
            tabela = "A1:H"
        else:
            dados = linhas_novas
            tabela = "A1:G"
        worksheet.append_rows(dados, value_input_option="RAW", table_range=tabela)
        time.sleep(0.3)
        limpar_cache()
        return len(linhas_novas)
    return 0


def atualizar_ponto(
    linha_real: int,
    data_str: str,
    municipio: str,
    pontos: str,
    lat: float,
    lon: float,
    mca: float,
    obs: str
) -> bool:
    if not pode_executar_acao("2", "editar"):
        st.error("Sem permissão para esta operação.")
        return False
    try:
        target_row = int(linha_real) + 2
        atual = worksheet.row_values(target_row)
        modo = conferir_estrutura_planilha(worksheet)
        formato, _ = _classificar_linha_medicao(atual, modo)
        if formato == "invalida":
            raise ValueError(
                f"Linha {target_row} não corresponde a uma medição reconhecida. "
                "A edição foi bloqueada para proteger os dados."
            )
        data_n = _data_valida_planilha(data_str)
        lat_n = normalizar_coordenada(lat, "lat")
        lon_n = normalizar_coordenada(lon, "lon")
        mca_n = _numero_valido_planilha(mca, 0, float("inf"))
        if not data_n or lat_n is None or lon_n is None or mca_n is None:
            raise ValueError("Data, coordenadas ou MCA inválido.")
        campos = [data_n, str(municipio), str(pontos), f"{lat_n:.6f}",
                  f"{lon_n:.6f}", str(mca_n), str(obs or "")]
        # Preserva a coluna ID de registros B:H e o formato A:G nos legados.
        colunas = ("B", "H") if formato == "com_id" else ("A", "G")
        worksheet.update(range_name=f"{colunas[0]}{target_row}:{colunas[1]}{target_row}",
                         values=[campos], value_input_option="RAW")
        time.sleep(0.3)
        limpar_cache()
        return True
    except Exception as e:
        st.error(f"Erro ao atualizar: {e}")
        return False


def excluir_pontos_lote(linhas_reais: list) -> bool:
    if any(int(numero) < 0 for numero in linhas_reais):
        st.error("Registros arquivados não podem ser excluídos pelo Google Sheets.")
        return False
    if not pode_executar_acao("2", "excluir"):
        st.error("Sem permissão para esta operação.")
        return False
    try:
        linhas_ordenadas = sorted(
            [r + 2 for r in linhas_reais],
            reverse=True
        )

        for r in linhas_ordenadas:
            worksheet.delete_rows(r)

        time.sleep(0.3)
        limpar_cache()

        return True

    except Exception as e:
        st.error(f"Erro ao excluir: {e}")
        return False


def data_para_str(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def gerar_kml(df):
    kml = simplekml.Kml()

    for _, row in df.iterrows():
        lat = row.get("Latitude")
        lon = row.get("Longitude")

        if pd.notnull(lat) and pd.notnull(lon):
            try:
                kml.newpoint(
                    name=str(row.get("Pontos", "Ponto")),
                    description=(
                        f"Data: {row.get('Data', '')}\n"
                        f"Município: {row.get('Município', '')}\n"
                        f"Ponto: {row.get('Pontos', '')}\n"
                        f"MCA: {row.get('MCA', '')}\n"
                        f"Observação: {mp_observacao_visivel(row.get('Observacao', ''))}"
                    ),
                    coords=[(float(lon), float(lat))]
                )

            except (ValueError, TypeError):
                continue

    return kml.kml()


# ============================================================
# SESSION STATE
# ============================================================
hoje = date.today()

# Depois de uma importação, ajusta o período ANTES de criar date_inputs.
# Evita conflito de estado de widget dentro do mesmo ciclo do Streamlit.
mp_periodo_importado = st.session_state.pop("mp_periodo_importado", None)
if mp_periodo_importado:
    mp_inicio, mp_fim = mp_periodo_importado
    st.session_state["calendario_data_inicial"] = mp_inicio
    st.session_state["calendario_data_final"] = mp_fim
    st.session_state.data_inicial_selecionada = mp_inicio
    st.session_state.data_final_selecionada = mp_fim
    st.session_state["filtro_municipio"] = "Todos"
    st.session_state["filtro_ponto"] = "Todos"
    st.session_state["filtro_mca"] = "Todas"

if "data_inicial_selecionada" not in st.session_state:
    st.session_state.data_inicial_selecionada = hoje

if "data_final_selecionada" not in st.session_state:
    st.session_state.data_final_selecionada = hoje

if "clicked_lat" not in st.session_state:
    st.session_state.clicked_lat = None

if "clicked_lon" not in st.session_state:
    st.session_state.clicked_lon = None

if "modo_adicionar_mapa" not in st.session_state:
    st.session_state.modo_adicionar_mapa = False

if "dados_upload_pendentes" not in st.session_state:
    st.session_state.dados_upload_pendentes = None
if "mp_resumo_conversao" not in st.session_state:
    st.session_state.mp_resumo_conversao = None
if "mp_tipo_upload" not in st.session_state:
    st.session_state.mp_tipo_upload = ""
if "mp_assinatura_upload" not in st.session_state:
    st.session_state.mp_assinatura_upload = None

if "nome_arquivo_pendente" not in st.session_state:
    st.session_state.nome_arquivo_pendente = None

if "file_uploader_key" not in st.session_state:
    st.session_state.file_uploader_key = 0


# ============================================================
# DIALOGS (POP-UPS DE CADASTRO, EDIÇÃO E PRÉ-VISUALIZAÇÃO DE UPLOAD)
# ============================================================
@st.dialog("➕ Cadastrar Ponto de Pressão")
def modal_novo_ponto():

    lat_default = (
        st.session_state.clicked_lat
        if st.session_state.clicked_lat is not None
        else 0.0
    )

    lon_default = (
        st.session_state.clicked_lon
        if st.session_state.clicked_lon is not None
        else 0.0
    )

    with st.form("form_novo_ponto_modal", clear_on_submit=True):

        data_cadastro = st.date_input(
            "Data do Registro",
            value=st.session_state.data_final_selecionada,
            format="DD/MM/YYYY"
        )

        municipio = st.text_input(
            "Município *",
            placeholder="Ex: Teresina"
        )

        pontos = st.text_input(
            "Pontos / Local *",
            placeholder="Ex: Ponto A-01"
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

        mca = st.number_input(
            "MCA *",
            format="%.2f",
            value=0.00,
            min_value=0.0,
            step=0.1
        )

        obs = st.text_input(
            "Observação",
            placeholder="Ex: Válvula regulada"
        )

        enviado = st.form_submit_button(
            "Cadastrar Ponto",
            type="primary",
            use_container_width=True
        )

        if enviado:

            lat_n = normalizar_coordenada(lat, "lat")
            lon_n = normalizar_coordenada(lon, "lon")

            if not municipio.strip():
                st.error("O campo 'Município' é obrigatório.")

            elif not pontos.strip():
                st.error("O campo 'Pontos' é obrigatório.")

            elif lat_n is None or lon_n is None:
                st.error(
                    "Coordenadas inválidas. Verifique os valores de Latitude e Longitude."
                )

            else:
                qtd_inserida = adicionar_ponto(
                    data_para_str(data_cadastro),
                    municipio.strip(),
                    pontos.strip(),
                    lat_n,
                    lon_n,
                    mca,
                    obs.strip()
                )

                if qtd_inserida:
                    st.success("Ponto cadastrado com sucesso!")
                else:
                    st.info("Este ponto já foi registrado nesta data. Nenhuma duplicação foi feita.")

                st.session_state.clicked_lat = None
                st.session_state.clicked_lon = None

                st.rerun()


@st.dialog("✏️ Editar Ponto de Pressão")
def modal_editar_ponto(linha_real: int):

    if int(linha_real) < 0:
        st.warning("Registros arquivados são somente leitura.")
        return

    df_all = carregar_dados()

    reg_edit = df_all[df_all["_linha_real"] == linha_real]

    if not reg_edit.empty:

        reg_edit = reg_edit.iloc[0]

        try:
            data_parsed = datetime.strptime(
                str(reg_edit["Data"]),
                "%d/%m/%Y"
            ).date()

        except ValueError:
            data_parsed = hoje

        with st.form("form_edicao_modal"):

            data_e = st.date_input(
                "Data do Registro",
                value=data_parsed,
                format="DD/MM/YYYY"
            )

            municipio_e = st.text_input(
                "Município *",
                value=str(reg_edit["Município"])
            )

            pontos_e = st.text_input(
                "Pontos *",
                value=str(reg_edit["Pontos"])
            )

            c1, c2 = st.columns(2)

            with c1:
                lat_e = st.text_input(
                    "Latitude *",
                    value=str(reg_edit["Latitude"] or LAT_BASE)
                )

            with c2:
                lon_e = st.text_input(
                    "Longitude *",
                    value=str(reg_edit["Longitude"] or LON_BASE)
                )

            mca_e = st.number_input(
                "MCA *",
                format="%.2f",
                value=float(reg_edit["MCA"] or 0.0),
                min_value=0.0,
                step=0.1
            )

            obs_e = st.text_input(
                "Observação",
                value=mp_observacao_visivel(reg_edit["Observacao"])
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

                lat_n = normalizar_coordenada(lat_e, "lat")
                lon_n = normalizar_coordenada(lon_e, "lon")

                if not municipio_e.strip():
                    st.error("O campo 'Município' é obrigatório.")

                elif lat_n is None or lon_n is None:
                    st.error("Coordenadas inválidas.")

                else:

                    if atualizar_ponto(
                        linha_real,
                        data_para_str(data_e),
                        municipio_e.strip(),
                        pontos_e,
                        lat_n,
                        lon_n,
                        mca_e,
                        mp_observacao_preservando_id(obs_e, reg_edit["Observacao"])
                    ):
                        st.success("Atualizado com sucesso!")
                        st.rerun()

            if cancelar_edicao:
                st.rerun()

    else:
        st.warning("Registro não encontrado.")


@st.dialog("📋 Pré-visualização da Planilha")
def modal_previa_upload():

    st.write(
        f"Arquivo carregado: **{st.session_state.nome_arquivo_pendente}**"
    )

    df_preview = pd.DataFrame(
        st.session_state.dados_upload_pendentes,
        columns=[
            "Data",
            "Município",
            "Pontos",
            "Latitude",
            "Longitude",
            "MCA",
            "Observacao"
        ]
    )

    df_preview_visivel = df_preview.copy()
    df_preview_visivel["Observacao"] = df_preview_visivel["Observacao"].map(mp_observacao_visivel)
    st.caption(f"Formato reconhecido: {st.session_state.get('mp_tipo_upload', 'Planilha')}")
    st.dataframe(df_preview_visivel.head(300), use_container_width=True, hide_index=True)
    if len(df_preview_visivel) > 300:
        st.caption("Prévia limitada a 300 linhas para preservar o desempenho; todas serão consideradas no envio.")

    st.info(
        f"Total de registros válidos prontos para envio: **{len(df_preview)}**"
    )
    resumo = st.session_state.get("mp_resumo_conversao")
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

            lote_confirmado = st.session_state.dados_upload_pendentes
            with st.spinner(
                "Enviando registros com segurança para o Google Sheets..."
            ):
                qtd_inserida = adicionar_lote_seguro(lote_confirmado)

            if qtd_inserida > 0:
                datas_lote = [datetime.strptime(linha[0], "%d/%m/%Y").date()
                              for linha in lote_confirmado]
                if datas_lote:
                    st.session_state["mp_periodo_importado"] = (min(datas_lote), max(datas_lote))

            st.session_state.dados_upload_pendentes = None
            st.session_state.nome_arquivo_pendente = None
            st.session_state.mp_resumo_conversao = None
            st.session_state.mp_tipo_upload = ""
            st.session_state.mp_assinatura_upload = None
            st.session_state.file_uploader_key += 1

            if qtd_inserida > 0:
                st.success(
                    f"✅ {qtd_inserida} novos registros importados com sucesso!"
                )
            else:
                st.info(
                    "ℹ️ Todos os registros da planilha já existiam no sistema. "
                    "Nenhuma duplicação foi feita."
                )

            time.sleep(1)
            st.rerun()

    with col_btn2:

        if st.button(
            "Cancelar",
            use_container_width=True
        ):
            st.session_state.dados_upload_pendentes = None
            st.session_state.nome_arquivo_pendente = None
            st.session_state.mp_resumo_conversao = None
            st.session_state.mp_tipo_upload = ""
            st.session_state.mp_assinatura_upload = None
            st.session_state.file_uploader_key += 1
            st.rerun()


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:

    st.markdown("### 🗺️ COI - Mapeamento")
    st.caption("⚙️ Painel Operacional")

    st.markdown("#### 📅 Selecionar Período")

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

    periodo_valido = data_inicial <= data_final

    if not periodo_valido:

        st.error(
            "A data inicial não pode ser maior que a data final."
        )

    elif data_inicial == data_final:

        st.info(
            f"Exibindo dados de **{data_para_str(data_inicial)}**"
        )

    else:

        st.info(
            f"Exibindo dados de **{data_para_str(data_inicial)}** "
            f"a **{data_para_str(data_final)}**"
        )

    st.divider()

    st.markdown("#### 🔍 Filtros")

    df_all = carregar_dados()

    if not df_all.empty and periodo_valido:

        df_all["DataObjFiltro"] = pd.to_datetime(
            df_all["Data"],
            format="%d/%m/%Y",
            errors="coerce"
        )

        # Correção: comparação entre Timestamp e Timestamp
        data_inicial_dt = pd.Timestamp(data_inicial)
        data_final_dt = pd.Timestamp(data_final)

        df_data = df_all[
            (df_all["DataObjFiltro"] >= data_inicial_dt)
            & (df_all["DataObjFiltro"] <= data_final_dt)
        ].copy()

    else:

        df_data = (
            df_all.iloc[0:0].copy()
            if not df_all.empty
            else df_all
        )

    municipios_opts = (
        ["Todos"]
        + sorted(
            df_data["Município"].dropna().unique().tolist()
        )
        if not df_data.empty
        else ["Todos"]
    )

    municipio_sel = st.selectbox(
        "Município",
        municipios_opts,
        key="filtro_municipio"
    )

    df_data_mun = (
        df_data[df_data["Município"] == municipio_sel]
        if municipio_sel != "Todos"
        else df_data
    )

    pontos_opts = (
        ["Todos"]
        + sorted(
            df_data_mun["Pontos"].dropna().unique().tolist()
        )
        if not df_data_mun.empty
        else ["Todos"]
    )

    ponto_sel = st.selectbox(
        "Ponto",
        pontos_opts,
        key="filtro_ponto"
    )

    faixa_sel = st.selectbox(
        "Faixa de MCA",
        [
            "Todas",
            "Críticos (0 MCA)",
            "Atenção (≤ 5 MCA)",
            "Normais (> 5 MCA)"
        ],
        key="filtro_mca"
    )

    st.divider()

    st.markdown("#### ➕ Ações e Dados")

    if st.button(
        "Adicionar Novo Ponto",
        type="primary",
        use_container_width=True
    ):
        modal_novo_ponto()

    arquivo_upload = st.file_uploader(
        "📂 Enviar base bruta ou planilha modelo (XLSX/CSV)",
        type=["xlsx", "csv"],
        key=f"upload_mapeamento_{st.session_state.file_uploader_key}"
    )

    df_modelo = pd.DataFrame(
        [{
            "Data": datetime.now().strftime("%d/%m/%Y"),
            "Município": "Teresina",
            "Pontos": "Ponto Exemplo 01",
            "Latitude": -5.0892,
            "Longitude": -42.8019,
            "MCA": 12.5,
            "Observacao": "Exemplo de preenchimento"
        }],
        columns=COLUNAS_PADRAO
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
        file_name="modelo_importacao_pressao.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    # Conversão inteligente: mesmo motor do Módulo 1, com prévia obrigatória.
    # O processamento ocorre apenas quando o conteúdo muda (SHA-256), não a
    # cada rerun/autorefresh; não confunde arquivos diferentes com mesmo nome.
    if arquivo_upload is not None:
        conteudo = arquivo_upload.getvalue()
        assinatura = hashlib.sha256(conteudo).hexdigest()
        if st.session_state.mp_assinatura_upload != assinatura:
            st.session_state.dados_upload_pendentes = None
            st.session_state.nome_arquivo_pendente = None
            st.session_state.mp_resumo_conversao = None
            st.session_state.mp_tipo_upload = ""
            st.session_state.mp_assinatura_upload = assinatura
            try:
                lote, resumo, tipo = mp_processar_importacao(
                    arquivo_upload.name, conteudo, data_final
                )
                st.session_state.mp_resumo_conversao = resumo
                st.session_state.mp_tipo_upload = tipo
                if lote:
                    st.session_state.dados_upload_pendentes = lote
                    st.session_state.nome_arquivo_pendente = arquivo_upload.name
                else:
                    st.warning(
                        "Nenhuma medição numérica válida para importar. "
                        "Parecer sem pressão medida não é incluído no mapa."
                    )
            except Exception as erro:
                st.error(f"❌ Erro ao processar a planilha: {erro}")

    if st.session_state.dados_upload_pendentes is not None:
        modal_previa_upload()

    st.divider()

    st.markdown("#### 📥 Exportar Dados")

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        if not df_all.empty:

            df_all[
                COLUNAS_PADRAO
            ].to_excel(
                writer,
                index=False,
                sheet_name="Mapeamento_Pressao"
            )

        else:

            pd.DataFrame(
                columns=COLUNAS_PADRAO
            ).to_excel(
                writer,
                index=False,
                sheet_name="Mapeamento_Pressao"
            )

    excel_data = output.getvalue()

    st.download_button(
        label="📊 Baixar em Excel (XLSX)",
        data=excel_data,
        file_name=(
            f"mapeamento_pressao_"
            f"{datetime.now().strftime('%Y%m%d')}.xlsx"
        ),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

    kml_string = (
        gerar_kml(df_all)
        if not df_all.empty
        else simplekml.Kml().kml()
    )

    st.download_button(
        label="🗺️ Baixar Mapa (KML/KMZ)",
        data=kml_string,
        file_name=(
            f"mapeamento_pressao_"
            f"{datetime.now().strftime('%Y%m%d')}.kml"
        ),
        mime="application/vnd.google-earth.kml+xml",
        use_container_width=True
    )

    st.divider()

    st.markdown("#### ⏱️ Atualização")

    intervalo = st.select_slider(
        "Intervalo (segundos)",
        options=[0, 15, 30, 60, 120],
        value=60
    )

    if intervalo > 0:
        st_autorefresh(
            interval=intervalo * 1000,
            key="autorefresh"
        )

    st.markdown(
        "<br>" * 2,
        unsafe_allow_html=True
    )

    st.divider()

    if st.button(
        "🏠 Voltar ao Menu Principal",
        use_container_width=True
    ):
        st.switch_page("app.py")

    st.divider()

    if "modo_escuro_mp" not in st.session_state:
        st.session_state.modo_escuro_mp = False

    modo_escuro_mp = st.toggle(
        "🌙 Modo escuro",
        value=st.session_state.modo_escuro_mp,
        key="toggle_modo_escuro_mp",
    )

    st.session_state.modo_escuro_mp = modo_escuro_mp

    if modo_escuro_mp:

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

                /* Botões normais e botões de download */
                .stButton > button,
                .stDownloadButton > button {
                    background-color: #000000 !important;
                    color: #ffffff !important;
                    border: 1px solid #444c56 !important;
                }

                /* Texto interno dos botões */
                .stButton > button *,
                .stDownloadButton > button * {
                    color: #ffffff !important;
                }

                /* Efeito ao passar o mouse */
                .stButton > button:hover,
                .stDownloadButton > button:hover {
                    background-color: #000000 !important;
                    color: #ff0000 !important;
                    border-color: #ff0000 !important;
                }

                /* Texto interno no hover */
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
# ÁREA PRINCIPAL
# ============================================================
st.title("🗺️ Painel de Mapeamento de Pressão - COI")

if periodo_valido:

    if data_inicial == data_final:

        st.caption(
            f"Visualizando dados da data: "
            f"**{data_para_str(data_inicial)}**"
        )

    else:

        st.caption(
            f"Visualizando dados do período: "
            f"**{data_para_str(data_inicial)} "
            f"a {data_para_str(data_final)}**"
        )

else:

    st.caption(
        "Período inválido. Ajuste as datas para visualizar os dados."
    )


df = carregar_dados()

if not df.empty and periodo_valido:

    df["DataObjFiltro"] = pd.to_datetime(
        df["Data"],
        format="%d/%m/%Y",
        errors="coerce"
    )

    # Correção: comparação entre Timestamp e Timestamp
    data_inicial_dt = pd.Timestamp(data_inicial)
    data_final_dt = pd.Timestamp(data_final)

    df_filtrado = df[
        (df["DataObjFiltro"] >= data_inicial_dt)
        & (df["DataObjFiltro"] <= data_final_dt)
    ].copy()

else:

    df_filtrado = (
        df.iloc[0:0].copy()
        if not df.empty
        else df.copy()
    )


if municipio_sel != "Todos":
    df_filtrado = df_filtrado[
        df_filtrado["Município"] == municipio_sel
    ]


if ponto_sel != "Todos":
    df_filtrado = df_filtrado[
        df_filtrado["Pontos"] == ponto_sel
    ]


if faixa_sel == "Críticos (0 MCA)":

    df_filtrado = df_filtrado[
        df_filtrado["MCA"] == 0
    ]

elif faixa_sel == "Atenção (≤ 5 MCA)":

    df_filtrado = df_filtrado[
        (df_filtrado["MCA"] > 0)
        & (df_filtrado["MCA"] <= 5)
    ]

elif faixa_sel == "Normais (> 5 MCA)":

    df_filtrado = df_filtrado[
        df_filtrado["MCA"] > 5
    ]


if not df_filtrado.empty:

    total = len(df_filtrado)

    criticos = len(
        df_filtrado[
            df_filtrado["MCA"] == 0
        ]
    )

    atencao = len(
        df_filtrado[
            (df_filtrado["MCA"] > 0)
            & (df_filtrado["MCA"] <= 5)
        ]
    )

    normais = len(
        df_filtrado[
            df_filtrado["MCA"] > 5
        ]
    )

    k1, k2, k3, k4 = st.columns(4)

    k1.metric(
        "Total de Ocorrências",
        total
    )

    k2.metric(
        "Críticos (0 MCA)",
        criticos
    )

    k3.metric(
        "Em Atenção (≤ 5 MCA)",
        atencao
    )

    k4.metric(
        "Normais (> 5 MCA)",
        normais
    )

else:

    if periodo_valido:

        if data_inicial == data_final:

            st.info(
                f"Nenhum ponto registrado para a data "
                f"{data_para_str(data_inicial)} "
                f"com os filtros selecionados."
            )

        else:

            st.info(
                f"Nenhum ponto registrado no período de "
                f"{data_para_str(data_inicial)} "
                f"a {data_para_str(data_final)} "
                f"com os filtros selecionados."
            )

    else:

        st.info(
            "Ajuste o período selecionado para visualizar os dados."
        )


st.divider()


# ============================================================
# MAPA COM SELETOR DE TIPO DE MAPA
# ============================================================
st.subheader("🗺️ Mapa de Mapeamento de Pressão")

c_map1, c_map2, c_map3 = st.columns([2.5, 1.4, 2.1])

with c_map1:

    tipo_mapa = st.selectbox(
        "🗺️ Tipo de Mapa",
        options=[
            "Mapa Padrão (OpenStreetMap)",
            "Satélite (Esri World Imagery)",
            "Terreno (OpenTopoMap)"
        ],
        key="seletor_tipo_mapa"
    )


with c_map2:

    mostrar_rotulos = st.checkbox(
        "Exibir rótulos",
        value=True,
        help="Mostra a pressão e, quando disponível, a cota do terreno junto ao marcador.",
    )


with c_map3:

    st.session_state.modo_adicionar_mapa = st.checkbox(
        "📍 Modo adicionar ponto",
        value=st.session_state.modo_adicionar_mapa
    )



if (
    not df_filtrado.empty
    and df_filtrado["Latitude"].notna().any()
):

    centro_lat = float(
        df_filtrado["Latitude"].mean()
    )

    centro_lon = float(
        df_filtrado["Longitude"].mean()
    )

    zoom = 13

else:

    centro_lat = LAT_BASE
    centro_lon = LON_BASE
    zoom = 12


m = folium.Map(
    location=[centro_lat, centro_lon],
    zoom_start=zoom,
    tiles=None
)

# ============================================================
# TELA CHEIA — MAPA PRINCIPAL DO MÓDULO 2
# ============================================================
# Usa o mesmo controle do Módulo 1. A expansão é feita dentro do
# mapa Leaflet, preservando filtros, medições, rótulos, pop-ups e
# a seleção de coordenadas para cadastro de pontos.
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
            "USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, "
            "IGP, UPR-EGP, and the GIS User Community"
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


# ============================================================
# MARCADORES — MESMO PADRÃO VISUAL DO MÓDULO 1
# ============================================================
def gerar_pin_svg(
    cor: str,
    tamanho: str = "bairro"
) -> str:

    if tamanho == "individual":

        largura = 34
        altura = 39


    else:

        largura = 40
        altura = 45


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



def mp_preparar_marcadores_sem_duplicatas(df_mapa: pd.DataFrame) -> pd.DataFrame:
    """Exibe um só pin/rótulo por localização + pressão visualmente iguais.

    O mapa de um período pode conter várias linhas para a mesma medição,
    inclusive em datas diferentes. Esses registros não devem produzir dois
    cartões idênticos sobre a mesma gota. Esta consolidação é EXCLUSIVA do
    mapa: nenhum registro do Google Sheets, indicadores ou tabelas é alterado.

    Coordenadas a 6 casas (~0,11 m) e MCA a 2 casas (precisão do rótulo).
    Leituras de valores diferentes permanecem como marcadores distintos.
    """
    if df_mapa.empty:
        return df_mapa.copy()

    base = df_mapa.dropna(subset=["Latitude", "Longitude", "MCA"]).copy()
    if base.empty:
        return base
    base["_mp_mca_num"] = pd.to_numeric(base["MCA"], errors="coerce")
    base = base[base["_mp_mca_num"].map(math.isfinite) & (base["_mp_mca_num"] >= 0)].copy()
    if base.empty:
        return base

    base["_mp_chave_marcador"] = list(zip(
        base["Latitude"].astype(float).round(6),
        base["Longitude"].astype(float).round(6),
        base["_mp_mca_num"].round(2),
    ))
    contagens = base.groupby("_mp_chave_marcador", sort=False).size()
    representantes = base.drop_duplicates(subset=["_mp_chave_marcador"], keep="first").copy()
    representantes["_mp_registros_coincidentes"] = (
        representantes["_mp_chave_marcador"].map(contagens).astype(int)
    )
    return representantes


if not df_filtrado.empty:
    validos = mp_preparar_marcadores_sem_duplicatas(df_filtrado)
    # Consulta pontual à cota do terreno, sem carregar relevo sombreado.
    # O mesmo catálogo do Módulo 1 reconhece automaticamente os GeoTIFFs
    # nas subpastas de dados/topografia, respeitando seus limites e CRS.
    cotas_mp = (None,) * len(validos)
    if not validos.empty:
        try:
            coords_mp = tuple(
                (float(lat), float(lon))
                for lat, lon in zip(validos["Latitude"], validos["Longitude"])
            )
            cotas_mp, alertas_mp = mp_cotas_automaticas(coords_mp)
            if alertas_mp:
                st.warning(
                    "Alguns arquivos topográficos não puderam ser utilizados: "
                    + "; ".join(alertas_mp[:3])
                    + (f" (e mais {len(alertas_mp)-3})" if len(alertas_mp) > 3 else "")
                )
        except (ImportError, OSError, ValueError, TypeError, RuntimeError) as mp_erro_cota:
            st.warning(f"Não foi possível consultar cotas do terreno: {mp_erro_cota}")

    for (_, row), cota in zip(validos.iterrows(), cotas_mp):
        mca = parse_float(row["MCA"], None)
        if mca is None or not math.isfinite(mca) or mca < 0:
            continue
        classificacao, cor = mp_classificar_pressao(mca)
        mca_fmt = mp_formatar_numero(mca)
        ponto = escape(str(row.get("Pontos", "")))
        municipio = escape(str(row.get("Município", "")))
        data_medicao = escape(str(row.get("Data", "")))
        observacao = escape(mp_observacao_visivel(row.get("Observacao", "")))
        cota_disponivel = cota is not None and math.isfinite(float(cota))
        cota_fmt = mp_formatar_numero(cota, 1) if cota_disponivel else None
        texto_cota = f"{cota_fmt} m" if cota_fmt else "Não disponível"
        observacao_html = (
            f'<div style="margin-top:10px;color:#475569;font-size:11px">'
            f'<b>Observação:</b> {observacao}</div>' if observacao else ""
        )
        qtd_coincidentes = int(row.get("_mp_registros_coincidentes", 1))
        info_coincidentes = (
            f'<div style="margin-top:9px;padding:7px 9px;background:#eff6ff;'
            f'border-radius:6px;color:#1e40af;font-size:11px">'
            f'{qtd_coincidentes} registros com a mesma localização e pressão '
            f'estão representados por um único marcador. Os dados originais '
            f'continuam disponíveis na tabela.</div>'
            if qtd_coincidentes > 1 else ""
        )
        popup = f"""
        <div style="font-family:Arial,sans-serif;min-width:220px;max-width:300px;color:#1f2937">
          <div style="font-size:14px;font-weight:800;margin-bottom:5px">{ponto}</div>
          <div style="font-size:11px;color:#64748b;margin-bottom:8px">
            {municipio} · {data_medicao}</div>
          <div style="padding:9px;background:#f8fafc;border-radius:8px;
                      border-left:4px solid {cor};margin-bottom:8px">
            <div style="font-size:10px;color:#64748b">PRESSÃO MEDIDA</div>
            <div style="font-size:19px;font-weight:800">{mca_fmt} MCA</div>
          </div>
          <div style="font-size:11px;line-height:1.6;color:#475569">
            <b>Classificação:</b> {classificacao}<br>
            <b>Cota da superfície (DSM):</b> {texto_cota}
          </div>{observacao_html}{info_coincidentes}
        </div>"""
        # Padrão do Módulo 1: medição + cota real do terreno, se disponível.
        # O restante dos dados continua no popup. Nunca inventar altitude.
        rotulo = ""
        if mostrar_rotulos:
            cota_rotulo = (
                '<span class="bp-label-elevation" '
                'title="Cota do terreno extraída do modelo digital de elevação">'
                f'{float(cota):.0f} m</span>'
                if cota_disponivel else ""
            )
            rotulo = f"""
            <div class="bp-label-card bp-label-card-individual" data-bp-default="top">
                <div class="bp-label-data">
                    <span class="bp-label-pressure">{mca_fmt} mca</span>
                    {cota_rotulo}
                </div>
            </div>"""
        prioridade = 4 if mca == 0 else 3 if mca <= 5 else 2 if mca <= 15 else 1
        marker_html = f"""
        <div class="bp-marker-wrapper bp-individual-marker-wrapper"
             data-bp-priority="{prioridade}"
             style="--bp-color:{cor};position:relative;width:42px;height:70px;
                    overflow:visible;font-family:Arial,sans-serif;pointer-events:auto">
            {gerar_pin_svg(cor, "individual")}{rotulo}
        </div>"""
        folium.map.Marker(
            location=[float(row["Latitude"]), float(row["Longitude"])],
            icon=folium.DivIcon(
                html=marker_html,
                icon_size=(42, 70),
                icon_anchor=(21, 70),
                class_name="pressao-individual-marker",
            ),
            popup=folium.Popup(popup, max_width=310),
            tooltip=f"{mca_fmt} mca",
        ).add_to(m)


# ============================================================
# ESTILO, REPOSICIONAMENTO DE RÓTULOS E LEGENDA (MÓDULO 1)
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

        /* Pressão + cota quando disponível, como no Módulo 1. */
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
        mapa.on("zoomend moveend resize viewreset enterFullscreen exitFullscreen",agendar);
        window.addEventListener("resize",agendar);
    });
})();
{% endmacro %}
""")


if mostrar_rotulos:
    m.add_child(ReorganizarRotulosIndividuais())


legenda_mp = f"""
<div style="position:fixed;bottom:24px;left:24px;z-index:9999;
            background:rgba(255,255,255,.95);color:#1f2937;
            border:1px solid #cbd5e1;border-radius:8px;
            box-shadow:0 2px 8px rgba(0,0,0,.16);
            padding:9px 11px;font:11px Arial,sans-serif;line-height:1.6">
  <div style="font-weight:800;margin-bottom:4px">Pressão medida</div>
  <div><span style="color:{COR_SEM_PRESSAO}">●</span> Sem Pressão (0 MCA)</div>
  <div><span style="color:{COR_BAIXA_PRESSAO}">●</span> Baixa Pressão (&gt; 0 a 5)</div>
  <div><span style="color:{COR_EM_ATENCAO}">●</span> Em Atenção (&gt; 5 a 15)</div>
  <div><span style="color:{COR_ALTA_PRESSAO}">●</span> Alta Pressão (&gt; 15)</div>
  <div style="font-size:10px;color:#64748b;margin-top:4px">
    Cota do terreno nos rótulos quando disponível</div>
</div>
"""
m.get_root().html.add_child(Element(legenda_mp))

map_data = st_folium(
    m,
    width="100%",
    height=520,
    returned_objects=["last_clicked"],
    key="mapa_principal_map"
)


if (
    st.session_state.modo_adicionar_mapa
    and map_data
    and map_data.get("last_clicked")
):

    clicked = map_data["last_clicked"]

    if clicked:

        st.session_state.clicked_lat = round(
            clicked["lat"],
            6
        )

        st.session_state.clicked_lon = round(
            clicked["lng"],
            6
        )

        modal_novo_ponto()


st.divider()


# ============================================================
# TABELA E GERENCIAMENTO
# (EDIÇÃO E EXCLUSÃO MÚLTIPLA COM CHAVE REAL)
# ============================================================
st.subheader("📋 Registro de Pontos Mapeados")

if not df_filtrado.empty:

    # Histórico visível e pesquisável, mas separado das operações de edição.
    arquivados_filtrados = df_filtrado[
        df_filtrado["_arquivo_historico"] == True
    ].copy()
    if not arquivados_filtrados.empty:
        st.caption("Histórico do Supabase — registros somente leitura")
        st.dataframe(
            arquivados_filtrados[COLUNAS_PADRAO],
            use_container_width=True, hide_index=True, height=230
        )
    editaveis_filtrados = df_filtrado[
        df_filtrado["_arquivo_historico"] == False
    ].copy()

    # A tabela interativa contém somente linhas físicas do Google Sheets.
    df_show = editaveis_filtrados[
        COLUNAS_PADRAO + ["_linha_real"]
    ].copy()

    df_show["Observacao"] = df_show["Observacao"].map(mp_observacao_visivel)
    df_show.insert(
        0,
        "Selecionar",
        False
    )

    edited_df = st.data_editor(
        df_show,
        use_container_width=True,
        height=350,
        disabled=COLUNAS_PADRAO + ["_linha_real"],
        key="editor_tabela_map_unificado",
        hide_index=True,
    )

    # ===== EXCLUSÃO =====
    selecionados = edited_df[
        edited_df["Selecionar"] == True
    ]

    c_edit, c_del = st.columns([2, 2])

    with c_edit:

        # ===== EDIÇÃO mais segura =====
        # Cria opções únicas e legíveis
        opcoes_map = {}
        opcoes_lista = [
            "-- Selecione o Ponto --"
        ]

        for _, row in editaveis_filtrados.iterrows():

            label = (
                f"{row['Pontos']}  |  "
                f"{row['Município']}  |  "
                f"MCA: {row['MCA']}"
            )

            # garante unicidade mesmo com nomes repetidos
            while label in opcoes_map:
                label += " "

            opcoes_lista.append(label)

            opcoes_map[label] = int(
                row["_linha_real"]
            )

        ponto_para_editar = st.selectbox(
            "Selecionar Ponto para Editar",
            options=opcoes_lista,
            key="select_edicao_ponto_preciso"
        )

        if (
            ponto_para_editar
            != "-- Selecione o Ponto --"
        ):

            if st.button(
                "✏️ Abrir Edição do Ponto Selecionado",
                type="primary",
                use_container_width=True
            ):

                linha_real_alvo = opcoes_map[
                    ponto_para_editar
                ]

                modal_editar_ponto(
                    linha_real_alvo
                )

    with c_del:

        st.markdown(
            "<br>",
            unsafe_allow_html=True
        )

        if not selecionados.empty:

            if st.button(
                f"🗑️ Excluir Selecionados "
                f"({len(selecionados)})",
                type="primary",
                use_container_width=True
            ):

                # Agora pega diretamente a coluna
                # _linha_real
                linhas_reais_para_excluir = (
                    selecionados["_linha_real"]
                    .astype(int)
                    .tolist()
                )

                if excluir_pontos_lote(
                    linhas_reais_para_excluir
                ):

                    st.success(
                        f"🗑️ "
                        f"{len(linhas_reais_para_excluir)} "
                        f"registro(s) excluído(s) "
                        f"com sucesso!"
                    )

                    st.rerun()

        else:

            st.button(
                "🗑️ Excluir Selecionados",
                disabled=True,
                use_container_width=True
            )

else:

    if periodo_valido:

        if data_inicial == data_final:

            st.info(
                f"Nenhum ponto registrado para a data "
                f"{data_para_str(data_inicial)} "
                f"com os filtros selecionados."
            )

        else:

            st.info(
                f"Nenhum ponto registrado no período de "
                f"{data_para_str(data_inicial)} "
                f"a {data_para_str(data_final)} "
                f"com os filtros selecionados."
            )

    else:

        st.info(
            "Ajuste o período selecionado para visualizar os dados."
        )
