"""Módulo 8 — Consulta Operacional (Teresina).

Consulta de matrículas e seleção territorial com rede de água sob demanda.
- Acesso aos perfis autenticados admin, usuario e operador.
- Base de matrículas XLS/XLSX ou SQLite indexado, somente leitura.
- Índice SQLite local criado SOMENTE na primeira pesquisa caso necessário.
- Nenhum histórico de consultas, nem escrita no Excel ou em dados de produção.
- Seleção de área reutiliza o cadastro de matrículas, sem conexão com o Farol.
- Regiões de influência extraídas exclusivamente dos polígonos KMZ/KML.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape as html_escape
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import unicodedata
import uuid
import zipfile
import json
import xml.etree.ElementTree as ET
from branca.element import Element

import folium
from folium.plugins import Draw, Fullscreen
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

# Compatibilidade com importação de auth.py no Streamlit Cloud.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
from auth import verificar_autenticacao

st.set_page_config(
    page_title="Consulta Operacional | COI",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("8")
if not verificar_autenticacao():
    st.error("Sessão não autenticada. Acesse o Hub Central para entrar.")
    st.stop()
# Permissão da versão de produção: qualquer perfil habilitado no Hub Central.
PERFIS_CONSULTA = {"admin", "usuario", "operador"}
if str(st.session_state.get("perfil", "")).strip().lower() not in PERFIS_CONSULTA:
    st.error("Seu perfil não está autorizado a acessar a Consulta Operacional.")
    st.stop()

PASTA_DADOS = RAIZ / "dados"
ARQUIVO_POCOS = PASTA_DADOS / "pocos.csv"
ARQUIVO_LOGGERS = PASTA_DADOS / "loggers.csv"
PASTA_BAIRROS = RAIZ / "geodados" / "municipios"
PASTA_INFLUENCIAS = RAIZ / "geodados" / "areas_influencia"
ARQUIVO_REDE_AGUA = PASTA_DADOS / "Rede_Agua_THE.kmz"
# Não envia rede de toda a cidade ao navegador. Limites protetivos por consulta.
LIMITE_REDE_TRECHOS = 4000
LIMITE_REDE_VERTICES = 90000
LIMITE_EQUIPAMENTOS_MAPA = 350
PASTA_TOPOGRAFIA = PASTA_DADOS / "topografia"

COLUNAS_MATRICULAS = (
    "CATEGORIA", "NUM_LIGACAO", "BAIRRO",
    "COD_LATITUDE", "COD_LONGITUDE", "END_LIGACAO",
)
COLUNAS_POCOS = (
    "ID_POCO", "IDENTIFICACAO_ATIVO", "NOME_POCO", "MUNICIPIO", "LATITUDE", "LONGITUDE",
)
COLUNAS_LOGGERS = (
    "ID_LOGGER", "IDENTIFICACAO_ATIVO", "ENDERECO", "MUNICIPIO", "LATITUDE", "LONGITUDE",
)

# Processo de indexação é único no processo Streamlit e não é feito no carregamento da página.
_BLOQUEIO_INDICE = threading.Lock()


def _normalizar_texto(valor: object) -> str:
    texto = unicodedata.normalize("NFKD", str(valor if valor is not None else "").strip().upper())
    return "".join(c for c in texto if not unicodedata.combining(c))


def _normalizar_cabecalho(valor: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", _normalizar_texto(valor))


def _matricula(valor: object) -> str:
    """Não remove zeros à esquerda que estejam armazenados como texto."""
    valor = str(valor if valor is not None else "").strip()
    if re.fullmatch(r"\d+\.0+", valor):
        return valor.split(".", 1)[0]
    return valor


def _coordenada(valor: object, tipo: str) -> float | None:
    if valor is None or pd.isna(valor):
        return None
    bruto = str(valor).strip().replace(" ", "")
    if not bruto:
        return None
    if "," in bruto and "." in bruto:
        bruto = (bruto.replace(".", "").replace(",", ".")
                 if bruto.rfind(",") > bruto.rfind(".") else bruto.replace(",", ""))
    else:
        bruto = bruto.replace(",", ".")
    try:
        numero = float(bruto)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(numero) or numero == 0:
        return None
    if tipo == "lat" and not -90 <= numero <= 90:
        return None
    if tipo == "lon" and not -180 <= numero <= 180:
        return None
    return numero


def _assinatura(caminho: Path) -> tuple[str, int, int]:
    info = caminho.stat()
    return str(caminho), info.st_size, info.st_mtime_ns


def _arquivo_matriculas() -> Path | None:
    if not PASTA_DADOS.is_dir():
        return None
    arquivos = {p.name.lower(): p for p in PASTA_DADOS.iterdir() if p.is_file()}
    return arquivos.get("matriculas.xls") or arquivos.get("matriculas.xlsx")


def _arquivo_sqlite_publicado() -> Path | None:
    """Arquivo indexado opcional previamente produzido pelo conversor."""
    if not PASTA_DADOS.is_dir():
        return None
    arquivos = {p.name.lower(): p for p in PASTA_DADOS.iterdir() if p.is_file()}
    return arquivos.get("matriculas.sqlite") or arquivos.get("matriculas.db")


@st.cache_data(show_spinner=False, max_entries=4)
def _resumo_hash(assinatura: tuple[str, int, int]) -> str:
    """Hash de leitura sequencial, sem interpretar planilha nem carregar tudo na RAM."""
    h = hashlib.sha256()
    with open(assinatura[0], "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(2 * 1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _conexao_leitura(db: Path):
    """Abre o SQLite SEM permitir gravação pelo mecanismo de consulta."""
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)


def _metadados_indice(db: Path) -> dict[str, str] | None:
    try:
        with _conexao_leitura(db) as con:
            meta = dict(con.execute("SELECT chave, valor FROM metadados"))
            # Evita considerar um arquivo incompleto como base válida.
            con.execute("SELECT NUM_LIGACAO FROM matriculas LIMIT 1").fetchone()
            return meta if meta.get("versao") == "3" else None
    except (sqlite3.Error, OSError, ValueError):
        return None


def _linhas_excel(caminho: Path):
    """Percorre TODAS as abas com cabeçalho esperado sem montar um DataFrame gigante.

    Detecta o formato real pelo cabeçalho do arquivo, inclusive arquivo XLSX
    indevidamente nomeado .xls. Um .xls antigo suporta no máximo 65.536 linhas por aba.
    """
    with caminho.open("rb") as f:
        assinatura = f.read(8)
    if assinatura.startswith(b"PK"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError("Adicione openpyxl ao requirements.txt.") from e
        # Arquivo binário, não caminho: aceita conteúdo XLSX mesmo se nomeado .xls.
        entrada = caminho.open("rb")
        try:
            workbook = load_workbook(entrada, read_only=True, data_only=True)
            for aba in workbook.worksheets:
                linhas = aba.iter_rows(values_only=True)
                cabecalho = next(linhas, None)
                if cabecalho is None:
                    continue
                campos = {_normalizar_cabecalho(v): i for i, v in enumerate(cabecalho)}
                if any(_normalizar_cabecalho(c) not in campos for c in COLUNAS_MATRICULAS):
                    continue
                indices = [campos[_normalizar_cabecalho(c)] for c in COLUNAS_MATRICULAS]
                yield aba.title, (tuple((linha[i] if i < len(linha) and linha[i] is not None else "")
                                      for i in indices) for linha in linhas)
        finally:
            if "workbook" in locals():
                workbook.close()
            entrada.close()
    elif assinatura.startswith(b"\xd0\xcf\x11\xe0"):
        try:
            import xlrd
        except ImportError as e:
            raise RuntimeError("Adicione xlrd>=2.0.1 ao requirements.txt para ler .xls.") from e
        workbook = xlrd.open_workbook(str(caminho), on_demand=True)
        try:
            for aba in workbook.sheets():
                if aba.nrows < 1:
                    continue
                campos = {_normalizar_cabecalho(v): i for i, v in enumerate(aba.row_values(0))}
                if any(_normalizar_cabecalho(c) not in campos for c in COLUNAS_MATRICULAS):
                    continue
                indices = [campos[_normalizar_cabecalho(c)] for c in COLUNAS_MATRICULAS]
                def obter(worksheet=aba, cols=indices):
                    for r in range(1, worksheet.nrows):
                        yield tuple(worksheet.cell_value(r, i) if i < worksheet.ncols else "" for i in cols)
                yield aba.name, obter()
        finally:
            workbook.release_resources()
    else:
        raise ValueError("Formato Excel desconhecido. Envie uma planilha .xls ou .xlsx válida.")


def _texto_celula(valor: object) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _construir_indice_excel(excel: Path, destino: Path, digest: str) -> int:
    """Índice local descartável. Nunca grava sobre o Excel nem sobre base existente."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + "." + uuid.uuid4().hex + ".tmp")
    total = 0
    abas_validas = 0
    try:
        os.close(os.open(temporario, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        con = sqlite3.connect(temporario)
        try:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("CREATE TABLE matriculas ("
                        "CATEGORIA TEXT, NUM_LIGACAO TEXT NOT NULL, BAIRRO TEXT, "
                        "COD_LATITUDE TEXT, COD_LONGITUDE TEXT, END_LIGACAO TEXT)")
            con.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
            lote = []
            for nome_aba, linhas in _linhas_excel(excel):
                abas_validas += 1
                for linha in linhas:
                    valores = [_texto_celula(c) for c in linha]
                    valores[1] = _matricula(valores[1])
                    if not valores[1]:
                        continue
                    lote.append(valores)
                    if len(lote) >= 5000:
                        con.executemany("INSERT INTO matriculas VALUES (?,?,?,?,?,?)", lote)
                        total += len(lote)
                        lote.clear()
                if lote:
                    con.executemany("INSERT INTO matriculas VALUES (?,?,?,?,?,?)", lote)
                    total += len(lote)
                    lote.clear()
            if not abas_validas:
                raise ValueError("Nenhuma aba tem as seis colunas esperadas do cadastro de matrículas.")
            con.execute("CREATE INDEX idx_num_ligacao ON matriculas(NUM_LIGACAO)")
            con.executemany("INSERT INTO metadados(chave,valor) VALUES (?,?)", [
                ("versao", "3"), ("fonte", excel.name), ("sha256", digest),
                ("quantidade", str(total)), ("abas_validas", str(abas_validas)),
            ])
            con.commit()
        finally:
            con.close()
        os.replace(temporario, destino)
        os.chmod(destino, 0o600)
        return total
    finally:
        if temporario.exists():
            temporario.unlink()


def _indice_matriculas() -> tuple[Path, dict[str, str]]:
    """Executada somente após o clique em Pesquisar.

    Usa base SQLite publicada e verificada; senão índice local em /tmp,
    gerado uma única vez por versão da planilha durante a vida do ambiente.
    """
    fonte = _arquivo_matriculas()
    publicado = _arquivo_sqlite_publicado()
    if fonte is None and publicado is None:
        raise FileNotFoundError("Adicione dados/matriculas.xls, .xlsx ou matriculas.sqlite.")

    if fonte is None:
        meta = _metadados_indice(publicado)
        if meta is None:
            raise ValueError(f"O índice {publicado.name} é inválido. Gere-o novamente.")
        return publicado, meta

    assinatura = _assinatura(fonte)
    digest = _resumo_hash(assinatura)
    if publicado is not None:
        meta = _metadados_indice(publicado)
        if meta and meta.get("sha256") == digest:
            return publicado, meta

    codigo = hashlib.sha256((str(fonte.resolve()) + "|" + str(assinatura[1]) + "|" + digest).encode()).hexdigest()[:20]
    destino = Path(tempfile.gettempdir()) / "coi_consulta_operacional" / f"matriculas_{codigo}.sqlite"
    meta = _metadados_indice(destino)
    if meta and meta.get("sha256") == digest:
        return destino, meta
    with _BLOQUEIO_INDICE:
        meta = _metadados_indice(destino)
        if not meta or meta.get("sha256") != digest:
            _construir_indice_excel(fonte, destino, digest)
        meta = _metadados_indice(destino)
    if meta is None:
        raise RuntimeError("Não foi possível gerar o índice de matrículas.")
    return destino, meta


def _buscar_matriculas(codigos: list[str]) -> tuple[pd.DataFrame, dict[str, str]]:
    caminho, meta = _indice_matriculas()
    if not codigos:
        return pd.DataFrame(columns=COLUNAS_MATRICULAS), meta
    with _conexao_leitura(caminho) as con:
        sql = ("SELECT CATEGORIA, NUM_LIGACAO, BAIRRO, COD_LATITUDE, COD_LONGITUDE, END_LIGACAO "
               f"FROM matriculas WHERE NUM_LIGACAO IN ({','.join('?' for _ in codigos)})")
        base = pd.read_sql_query(sql, con, params=codigos, dtype=str).fillna("")
    # Converter apenas coordenadas dos registros solicitados.
    base["_lat"] = base["COD_LATITUDE"].map(lambda v: _coordenada(v, "lat"))
    base["_lon"] = base["COD_LONGITUDE"].map(lambda v: _coordenada(v, "lon"))
    ordem = {codigo: i for i, codigo in enumerate(codigos)}
    if not base.empty:
        base["_ord"] = base["NUM_LIGACAO"].map(ordem)
        base = base.sort_values("_ord", kind="stable").drop(columns="_ord")
    return base, meta


def _validar_colunas(df: pd.DataFrame, esperadas: tuple[str, ...], origem: str) -> pd.DataFrame:
    mapa = {_normalizar_cabecalho(c): c for c in df.columns}
    faltantes = [c for c in esperadas if _normalizar_cabecalho(c) not in mapa]
    if faltantes:
        raise ValueError(f"{origem}: colunas não encontradas: {', '.join(faltantes)}.")
    selecionadas = [mapa[_normalizar_cabecalho(c)] for c in esperadas]
    resultado = df.loc[:, selecionadas].copy()
    resultado.columns = list(esperadas)
    return resultado.fillna("")


@st.cache_data(show_spinner=False, max_entries=6)
def _ler_ativos_csv(assinatura: tuple[str, int, int], tipo: str) -> pd.DataFrame:
    caminho = assinatura[0]
    colunas = COLUNAS_POCOS if tipo == "Poço / Ativo" else COLUNAS_LOGGERS
    try:
        df = pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{Path(caminho).name}: salve o CSV como UTF-8.") from exc
    dados = _validar_colunas(df, colunas, Path(caminho).name)
    dados["_lat"] = dados["LATITUDE"].map(lambda v: _coordenada(v, "lat"))
    dados["_lon"] = dados["LONGITUDE"].map(lambda v: _coordenada(v, "lon"))
    dados = dados.loc[dados["_lat"].notna() & dados["_lon"].notna()].copy()
    dados["_tipo"] = tipo
    dados["_nome"] = dados["NOME_POCO"] if tipo == "Poço / Ativo" else dados["ENDERECO"]
    return dados


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(a)))


def _grade_espacial(df: pd.DataFrame, celula: float = 0.01):
    grade: dict[tuple[int, int], list[tuple[int, float, float]]] = defaultdict(list)
    registros = df.to_dict("records")
    for indice, registro in enumerate(registros):
        lat, lon = float(registro["_lat"]), float(registro["_lon"])
        grade[math.floor(lat / celula), math.floor(lon / celula)].append((indice, lat, lon))
    return grade, registros


def _buscar_proximos(grade, registros, lat: float, lon: float, raio_m: float, celula: float = 0.01):
    delta_lat = raio_m / 110000.0 + 1e-8
    cosseno = max(0.000001, math.cos(math.radians(min(89.999, abs(lat) + delta_lat))))
    delta_lon = raio_m / (110000.0 * cosseno) + 1e-8
    for ix in range(math.floor((lat - delta_lat) / celula), math.floor((lat + delta_lat) / celula) + 1):
        for iy in range(math.floor((lon - delta_lon) / celula), math.floor((lon + delta_lon) / celula) + 1):
            for indice, alat, alon in grade.get((ix, iy), ()):
                distancia = _distance_m(lat, lon, alat, alon)
                if distancia <= raio_m:
                    yield indice, registros[indice], distancia


def _consultar_proximidade(clientes: pd.DataFrame, bases: dict[str, pd.DataFrame], raio: int):
    resultados = []
    ids_mapa: set[tuple[str, str, str]] = set()
    ativos_mapa = []
    for tipo, base in bases.items():
        if base.empty:
            continue
        grade, registros = _grade_espacial(base)
        for _, cliente in clientes.iterrows():
            lat, lon = float(cliente["_lat"]), float(cliente["_lon"])
            for _, ativo, distancia in _buscar_proximos(grade, registros, lat, lon, raio):
                identificacao = str(ativo.get("IDENTIFICACAO_ATIVO", "")).strip()
                nome = str(ativo.get("_nome", "")).strip()
                resultados.append({
                    "Matrícula": cliente["NUM_LIGACAO"],
                    "Tipo": tipo,
                    "Identificação": identificacao,
                    "Nome / endereço": nome,
                    "Distância (m)": round(distancia, 1),
                })
                chave = (tipo, str(ativo.get("ID_POCO") or ativo.get("ID_LOGGER") or identificacao),
                         f"{ativo['_lat']:.6f},{ativo['_lon']:.6f}")
                if chave not in ids_mapa:
                    ids_mapa.add(chave)
                    ativos_mapa.append(ativo)
    resultado_df = pd.DataFrame(resultados,
        columns=["Matrícula", "Tipo", "Identificação", "Nome / endereço", "Distância (m)"])
    if not resultado_df.empty:
        resultado_df = resultado_df.sort_values(["Matrícula", "Distância (m)", "Tipo"], kind="stable")
    return resultado_df, ativos_mapa


def _interpretar_pesquisa(texto: str) -> tuple[list[str], list[str]]:
    valores = re.split(r"[,;\s]+", texto.strip())
    vistos = set()
    matriculas = []
    invalidos = []
    for valor in valores:
        if not valor:
            continue
        numero = _matricula(valor)
        if not numero.isdecimal():
            invalidos.append(valor)
        elif numero not in vistos:
            vistos.add(numero)
            matriculas.append(numero)
    return matriculas, invalidos


def _popup_cliente(linha: pd.Series) -> str:
    """Exatamente os três campos pedidos; escape contra HTML nas planilhas e no KML."""
    campos = (
        ("Matrícula", linha.get("NUM_LIGACAO", "")),
        ("Endereço", linha.get("END_LIGACAO", "")),
        ("Área de Influência", linha.get("_influencia", "Área de influência não identificada")),
    )
    return "<div style='min-width:245px'>" + "".join(
        f"<div><b>{html_escape(str(k))}:</b> {html_escape(str(v))}</div>" for k, v in campos
    ) + "</div>"


def _popup_ativo(ativo: dict) -> str:
    return "<div style='min-width:190px'>" + "".join(
        f"<div><b>{html_escape(k)}:</b> {html_escape(str(v))}</div>"
        for k, v in (("Tipo", ativo.get("_tipo", "")),
                     ("Identificação", ativo.get("IDENTIFICACAO_ATIVO", "")),
                     ("Nome / endereço", ativo.get("_nome", "")))
    ) + "</div>"


def _caminho_bairros_teresina() -> Path | None:
    if not PASTA_BAIRROS.is_dir():
        return None
    candidatos = [p for p in PASTA_BAIRROS.iterdir()
                  if p.is_file() and p.suffix.lower() in {".gpkg", ".kml", ".kmz"}
                  and "TERESINA" in _normalizar_texto(p.stem)
                  and not any(t in _normalizar_texto(p.stem) for t in ("INFLUENCIA", "ABRANGENCIA"))]
    prioridade = {".gpkg": 0, ".kmz": 1, ".kml": 2}
    if candidatos:
        return sorted(candidatos, key=lambda p: (prioridade[p.suffix.lower()], p.name))[0]
    legado = RAIZ / "TERESINA.kmz"
    return legado if legado.is_file() else None


@st.cache_data(show_spinner=False, max_entries=2)
def _bairros_geograficos(assinatura: tuple[str, int, int]) -> list[dict]:
    """Código anterior preservado para as feições de bairro, independente da influência."""
    from pyproj import CRS, Transformer
    from shapely import wkb
    from shapely.geometry import Polygon
    from shapely.ops import transform

    caminho = Path(assinatura[0])
    bairros = []
    if caminho.suffix.lower() == ".gpkg":
        with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as banco:
            tabelas = banco.execute("SELECT table_name, srs_id FROM gpkg_contents WHERE data_type='features'").fetchall()
            for tabela, srs_id in tabelas:
                campo_geo = banco.execute(
                    "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?", (tabela,)
                ).fetchone()
                if not campo_geo:
                    continue
                nomes = [c[1] for c in banco.execute('PRAGMA table_info("' + tabela.replace('"', '""') + '")')]
                campos = {_normalizar_cabecalho(c): c for c in nomes}
                campo_nome = next((campos[c] for c in ("NMBAIRRO", "NOMEBAIRRO", "BAIRRO", "NOME") if c in campos), None)
                campo_municipio = next((campos[c] for c in ("NMMUN", "MUNICIPIO", "NOMEMUNICIPIO", "CIDADE") if c in campos), None)
                if not campo_nome:
                    continue
                nome_geom, srs_geom = campo_geo
                linha_crs = banco.execute(
                    "SELECT organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys WHERE srs_id=?",
                    (srs_geom or srs_id,),
                ).fetchone()
                if not linha_crs:
                    continue
                org, cod, wkt_crs = linha_crs
                try:
                    origem = CRS.from_user_input(f"{org}:{cod}") if org and cod else CRS.from_wkt(wkt_crs)
                except Exception:
                    origem = CRS.from_wkt(wkt_crs)
                conversor = None if origem.equals(CRS.from_epsg(4326)) else Transformer.from_crs(origem, 4326, always_xy=True)
                citar = lambda s: '"' + s.replace('"', '""') + '"'
                cols = [nome_geom, campo_nome] + ([campo_municipio] if campo_municipio else [])
                consulta = f"SELECT {', '.join(citar(c) for c in cols)} FROM {citar(tabela)}"
                for registro in banco.execute(consulta):
                    blob, nome, *mun = registro
                    if not blob or not nome or (mun and mun[0] and _normalizar_texto(mun[0]) != "TERESINA"):
                        continue
                    blob = bytes(blob)
                    if blob[:2] != b"GP" or len(blob) < 8:
                        continue
                    envelope = (blob[3] >> 1) & 7
                    tamanhos = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
                    if envelope not in tamanhos:
                        continue
                    geo = wkb.loads(blob[8 + tamanhos[envelope]:])
                    if conversor:
                        geo = transform(conversor.transform, geo)
                    for area in ([geo] if geo.geom_type == "Polygon" else list(geo.geoms) if geo.geom_type == "MultiPolygon" else []):
                        if not area.is_empty:
                            simples = area.simplify(0.00007, preserve_topology=True)
                            visual = simples if simples.geom_type == "Polygon" else area
                            bairros.append({"nome": str(nome), "coords": [(y, x) for x, y in visual.exterior.coords],
                                            "limites": area.bounds, "geometria": area})
    else:
        if caminho.suffix.lower() == ".kmz":
            with zipfile.ZipFile(caminho) as zipado:
                kmls = [n for n in zipado.namelist() if n.lower().endswith(".kml")]
                if not kmls:
                    return []
                xml = zipado.read(kmls[0])
        else:
            xml = caminho.read_bytes()
        root = ET.fromstring(xml)
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        for lugar in root.findall(".//k:Placemark", ns):
            el_nome = lugar.find("k:name", ns)
            nome = el_nome.text.strip() if el_nome is not None and el_nome.text else ""
            if not nome:
                continue
            for poligono in lugar.findall(".//k:Polygon", ns):
                ring = poligono.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", ns)
                if ring is None or not ring.text:
                    continue
                pontos = []
                for termo in ring.text.split():
                    try:
                        longitude, latitude = (float(x) for x in termo.split(",")[:2])
                        pontos.append((longitude, latitude))
                    except (ValueError, TypeError):
                        continue
                if len(pontos) >= 3:
                    area = Polygon(pontos)
                    if not area.is_empty:
                        simples = area.simplify(0.00007, preserve_topology=True)
                        visual = simples if simples.geom_type == "Polygon" else area
                        bairros.append({"nome": nome, "coords": [(y, x) for x, y in visual.exterior.coords],
                                        "limites": area.bounds, "geometria": area})
    return bairros


def _caminho_influencias() -> Path | None:
    """Prefere diretório dedicado; evita confundir polígonos de bairros e abastecimento."""
    candidatos = []
    if PASTA_INFLUENCIAS.is_dir():
        candidatos.extend(p for p in PASTA_INFLUENCIAS.iterdir()
                          if p.is_file() and p.suffix.lower() in {".kmz", ".kml"})
    if not candidatos and PASTA_BAIRROS.is_dir():
        candidatos.extend(p for p in PASTA_BAIRROS.iterdir()
                          if p.is_file() and p.suffix.lower() in {".kmz", ".kml"}
                          and ("INFLUENCIA" in _normalizar_texto(p.stem)
                               or "ABRANGENCIA" in _normalizar_texto(p.stem)))
    return sorted(candidatos, key=lambda p: p.name)[0] if candidatos else None


def _coordenadas_kml(texto: str | None) -> list[tuple[float, float]]:
    pontos = []
    for item in (texto or "").split():
        partes = item.split(",")
        if len(partes) < 2:
            continue
        try:
            lon, lat = float(partes[0]), float(partes[1])
        except ValueError:
            continue
        if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
            pontos.append((lon, lat))
    return pontos


@st.cache_data(show_spinner=False, max_entries=3)
def _ler_influencias(assinatura: tuple[str, int, int]) -> list[dict]:
    """Carrega todos os polígonos, inclusive MultiGeometry e buracos internos.

    Não insere no mapa os pontos do KML, apenas polígonos das áreas.
    Geometria original é preservada para cruzamento exato; simplificação só visual.
    """
    from shapely.geometry import Polygon
    caminho = Path(assinatura[0])
    if caminho.suffix.lower() == ".kmz":
        with zipfile.ZipFile(caminho) as compactado:
            arquivos = [n for n in compactado.namelist() if n.lower().endswith(".kml")]
            if not arquivos:
                raise ValueError("O KMZ não contém arquivos KML.")
            fontes = [compactado.read(n) for n in arquivos]
    else:
        fontes = [caminho.read_bytes()]
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    saida = []
    for xml in fontes:
        raiz = ET.fromstring(xml)
        for placemark in raiz.findall(".//k:Placemark", ns):
            elemento_nome = placemark.find("k:name", ns)
            nome = (elemento_nome.text or "").strip() if elemento_nome is not None else ""
            if not nome:
                continue
            for elemento in placemark.findall(".//k:Polygon", ns):
                externa = elemento.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", ns)
                vertices = _coordenadas_kml(externa.text if externa is not None else None)
                if len(vertices) < 3:
                    continue
                furos = []
                for anel in elemento.findall("k:innerBoundaryIs/k:LinearRing/k:coordinates", ns):
                    coords = _coordenadas_kml(anel.text)
                    if len(coords) >= 3:
                        furos.append(coords)
                poligono = Polygon(vertices, holes=furos)
                if not poligono.is_valid:
                    poligono = poligono.buffer(0)
                if poligono.is_empty:
                    continue
                partes = ([poligono] if poligono.geom_type == "Polygon"
                          else list(poligono.geoms) if poligono.geom_type == "MultiPolygon" else [])
                for parte in partes:
                    visual = parte.simplify(0.00007, preserve_topology=True)
                    if visual.is_empty or visual.geom_type != "Polygon":
                        visual = parte
                    saida.append({
                        "nome": nome,
                        "geometria": parte,
                        "limites": parte.bounds,
                        "contorno": [[lat, lon] for lon, lat in visual.exterior.coords],
                        "buracos": [[[lat, lon] for lon, lat in anel.coords] for anel in visual.interiors],
                    })
    return saida


def _identificar_influencias(lat: float, lon: float, areas: list[dict]) -> list[str]:
    from shapely.geometry import Point
    ponto = Point(lon, lat)
    correspondentes = []
    vistos = set()
    for area in areas:
        minlon, minlat, maxlon, maxlat = area["limites"]
        if not (minlon <= lon <= maxlon and minlat <= lat <= maxlat):
            continue
        if area["geometria"].covers(ponto):
            nome = area["nome"]
            if nome not in vistos:
                vistos.add(nome)
                correspondentes.append(nome)
    return correspondentes


# A interface integrada utiliza um único mapa Folium. A busca de matrículas,
# o desenho de perímetros, os ativos e o recorte da rede são combinados abaixo.

def _geometria_area_bairro(bairros: list[dict], nome: str):
    """Une feições do mesmo bairro sem substituir seus contornos originais."""
    from shapely.ops import unary_union

    chave = _normalizar_texto(nome)
    geometrias = [b["geometria"] for b in bairros if _normalizar_texto(b["nome"]) == chave]
    return unary_union(geometrias) if geometrias else None


def _validar_area_selecionada(geometria):
    """Aceita apenas polígonos pequenos e válidos (coordenadas WGS84)."""
    if geometria is None or geometria.is_empty:
        return None, "Selecione ou desenhe um polígono válido."
    if not geometria.is_valid:
        geometria = geometria.buffer(0)
    if geometria.is_empty or geometria.geom_type not in {"Polygon", "MultiPolygon"}:
        return None, "A área selecionada precisa ser um polígono."
    xmin, ymin, xmax, ymax = geometria.bounds
    if not (-180 <= xmin <= xmax <= 180 and -90 <= ymin <= ymax <= 90):
        return None, "O polígono possui coordenadas geográficas inválidas."
    # Evita que um desenho acidental cubra toda a cidade e congestione o mapa.
    if xmax - xmin > 0.075 or ymax - ymin > 0.075:
        return None, "Área muito extensa. Desenhe um polígono menor para manter o mapa responsivo."
    if geometria.area < 1e-11:
        return None, "Polígono muito pequeno ou degenerado."
    return geometria, None


def _local_tag(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _pontos_linha_kml(texto: str | None) -> list[tuple[float, float]]:
    """KML sempre fornece longitude, latitude[,altitude] com ponto decimal."""
    pontos = []
    for item in (texto or "").split():
        partes = item.split(",")
        if len(partes) < 2:
            continue
        try:
            lon, lat = float(partes[0]), float(partes[1])
        except (ValueError, OverflowError):
            continue
        if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
            pontos.append((lon, lat))
    return pontos


def _linhas_de_geometria(geom):
    """Ignora pontos isolados resultantes do recorte; a rede é linear."""
    if geom.is_empty:
        return
    if geom.geom_type == "LineString":
        if len(geom.coords) >= 2 and geom.length > 0:
            yield geom
    elif geom.geom_type in {"MultiLineString", "GeometryCollection"}:
        for parte in geom.geoms:
            yield from _linhas_de_geometria(parte)


# Campos do KML de rede: coletar apenas atributos operacionais necessários
# e somente para trechos que cruzaram a área desenhada.
REDE_ATRIBUTOS = (
    "gid", "trecho_atual", "trecho_antigo", "diametro_nominal",
    "material", "tipo_pavimento", "extensao", "profundidade",
    "funcao", "situacao", "data_implantacao",
)


def _atributos_trecho_kml(placemark, titulo=""):
    """Lê ExtendedData/SchemaData/SimpleData (e Data/value), sem importar toda a rede."""
    dados = {}
    for campo in placemark.iter():
        etiqueta = _local_tag(campo.tag)
        if etiqueta not in {"SimpleData", "Data"}:
            continue
        chave = str(campo.attrib.get("name") or "").strip().lower()
        if chave not in REDE_ATRIBUTOS:
            continue
        if etiqueta == "SimpleData":
            valor = campo.text
        else:
            valor = next((filho.text for filho in campo
                          if _local_tag(filho.tag) == "value"), None)
        if valor is not None:
            # GeoJsonPopup monta HTML: escapar valores oriundos do KML.
            dados[chave] = html_escape(str(valor).strip()[:180])

    identificacao = (dados.get("gid") or dados.get("trecho_atual")
                     or dados.get("trecho_antigo") or html_escape(str(titulo)[:110])
                     or "Não informado")
    resultado = {campo: dados.get(campo) or "Não informado" for campo in REDE_ATRIBUTOS}
    resultado["identificacao"] = identificacao
    return resultado


@st.cache_data(show_spinner=False, max_entries=4)
def _rede_recortada_kmz(assinatura: tuple[str, int, int], area_wkb: bytes,
                         limite_trechos: int = LIMITE_REDE_TRECHOS,
                         limite_vertices: int = LIMITE_REDE_VERTICES) -> dict:
    """Leitura incremental do KMZ no servidor, somente após pedido explícito.

    Não descompacta o arquivo inteiro no disco nem carrega a rede da cidade no
    navegador. Cada trecho é intersectado com o polígono real; só o recorte
    simplificado chega ao Folium. Resultados são cacheados por arquivo + área.
    """
    from shapely import wkb
    from shapely.geometry import LineString, box, mapping
    from shapely.prepared import prep

    area = wkb.loads(area_wkb)
    area_box = box(*area.bounds)
    selecionada = prep(area)
    feicoes = []
    vertices = 0
    total_trechos = 0
    atingiu_limite = False
    quantidade_kml = 0

    with zipfile.ZipFile(assinatura[0], "r") as pacote:
        fontes = [nome for nome in pacote.namelist() if nome.lower().endswith(".kml")]
        if not fontes:
            raise ValueError("O arquivo de rede não contém um KML interno.")
        for nome_kml in fontes:
            quantidade_kml += 1
            with pacote.open(nome_kml) as arquivo:
                # stack permite remover Placemarks processados de seus respectivos
                # pais; dessa forma, a RAM não cresce com o número de feições.
                pilha = []
                for evento, elemento in ET.iterparse(arquivo, events=("start", "end")):
                    if evento == "start":
                        pilha.append(elemento)
                        continue
                    if _local_tag(elemento.tag) == "Placemark":
                        titulo = ""
                        for filho in elemento:
                            if _local_tag(filho.tag) == "name" and filho.text:
                                titulo = filho.text.strip()[:110]
                                break
                        for linha_xml in elemento.iter():
                            if _local_tag(linha_xml.tag) != "LineString":
                                continue
                            coordenadas = next((e.text for e in linha_xml.iter()
                                                if _local_tag(e.tag) == "coordinates"), None)
                            pontos = _pontos_linha_kml(coordenadas)
                            if len(pontos) < 2:
                                continue
                            x0 = min(p[0] for p in pontos)
                            x1 = max(p[0] for p in pontos)
                            y0 = min(p[1] for p in pontos)
                            y1 = max(p[1] for p in pontos)
                            if x1 < area.bounds[0] or x0 > area.bounds[2] or y1 < area.bounds[1] or y0 > area.bounds[3]:
                                continue
                            linha = LineString(pontos)
                            if not selecionada.intersects(linha):
                                continue
                            recorte = linha.intersection(area)
                            # Só analisa atributos quando o segmento cruza a área.
                            atributos = _atributos_trecho_kml(elemento, titulo)
                            for segmento in _linhas_de_geometria(recorte):
                                # ~1 metro, apenas para renderização no navegador.
                                simples = segmento.simplify(0.000009, preserve_topology=False)
                                if simples.geom_type != "LineString" or len(simples.coords) < 2:
                                    simples = segmento
                                n = len(simples.coords)
                                if len(feicoes) >= limite_trechos or vertices + n > limite_vertices:
                                    atingiu_limite = True
                                    break
                                feicoes.append({"type": "Feature", "properties": atributos,
                                                "geometry": mapping(simples)})
                                vertices += n
                                total_trechos += 1
                            if atingiu_limite:
                                break
                        # Libera a memória de Placemarks já tratados, inclusive
                        # quando todos estão aninhados em um único Folder.
                        if len(pilha) >= 2:
                            try:
                                pilha[-2].remove(elemento)
                            except ValueError:
                                pass
                        elemento.clear()
                    elif _local_tag(elemento.tag) in {"Folder", "Document"}:
                        elemento.clear()
                    pilha.pop()
                    if atingiu_limite:
                        break
            if atingiu_limite:
                break

    return {"geojson": {"type": "FeatureCollection", "features": feicoes},
            "trechos": total_trechos, "vertices": vertices,
            "limitado": atingiu_limite, "arquivos_kml": quantidade_kml}


@st.cache_data(show_spinner=False, max_entries=12)
def _consultar_cadastro_no_poligono(assinatura_indice: tuple[str, int, int], area_wkb: bytes) -> pd.DataFrame:
    """Consulta espacial somente leitura no MESMO índice da pesquisa de matrícula.

    A planilha dados/matriculas.xlsx (ou o índice SQLite dela) é a única fonte.
    Usa envelope na consulta SQL para evitar transferir o cadastro inteiro para
    o Python, seguido de teste geométrico exato, incluindo pontos na borda.
    Não abre planilha do Farol, não cria O.S. e não escreve dados de produção.
    """
    from shapely import wkb
    from shapely.geometry import Point
    from shapely.prepared import prep

    area = wkb.loads(area_wkb)
    xmin, ymin, xmax, ymax = area.bounds
    # O índice original guarda coordenadas como texto e recebe dados com
    # ponto ou vírgula. A conversão SQL faz uma pré-seleção rápida; coordenadas
    # de formatação mista (ambos separadores) são conferidas no Python, evitando
    # falsos negativos por conversão simplificada no SQLite.
    consulta_sql = """
        SELECT CATEGORIA, NUM_LIGACAO, BAIRRO,
               COD_LATITUDE, COD_LONGITUDE, END_LIGACAO
        FROM matriculas
        WHERE (
            CAST(REPLACE(COD_LATITUDE, ',', '.') AS REAL) BETWEEN ? AND ?
            AND CAST(REPLACE(COD_LONGITUDE, ',', '.') AS REAL) BETWEEN ? AND ?
        ) OR (
            (INSTR(COD_LATITUDE, ',') > 0 AND INSTR(COD_LATITUDE, '.') > 0)
            OR (INSTR(COD_LONGITUDE, ',') > 0 AND INSTR(COD_LONGITUDE, '.') > 0)
        )
    """
    registros = []
    preparado = prep(area)
    with _conexao_leitura(Path(assinatura_indice[0])) as conexao:
        # Chunks evitam ocupar a memória com candidatos volumosos.
        cursor = conexao.execute(consulta_sql, (ymin, ymax, xmin, xmax))
        while True:
            lote = cursor.fetchmany(6000)
            if not lote:
                break
            for categoria, matricula, bairro, latitude, longitude, endereco in lote:
                lat = _coordenada(latitude, "lat")
                lon = _coordenada(longitude, "lon")
                if lat is None or lon is None:
                    continue
                if not (ymin <= lat <= ymax and xmin <= lon <= xmax):
                    continue
                if not preparado.covers(Point(lon, lat)):
                    continue
                registros.append((str(categoria or ""), str(matricula or ""),
                                  str(bairro or ""), str(latitude or ""),
                                  str(longitude or ""), str(endereco or "")))
    return pd.DataFrame(registros, columns=list(COLUNAS_MATRICULAS))


def _matriculas_na_area(area):
    """Reutiliza o índice e metadados já empregados na pesquisa individual."""
    indice, metadados = _indice_matriculas()
    return _consultar_cadastro_no_poligono(_assinatura(indice), area.wkb), metadados




# ============================================================
# COTAS TOPOGRÁFICAS (mesmo mecanismo do Módulo 1/2)
# ============================================================

@st.cache_data(show_spinner=False, max_entries=128)
def _m8_limites_dem(caminho: str, versao: int, tamanho: int) -> tuple:
    """Limites geográficos do raster, cacheados pela versão do arquivo."""
    _ = (versao, tamanho)
    import rasterio
    from rasterio.warp import transform_bounds
    with rasterio.open(caminho) as ds:
        if ds.crs is None or ds.count < 1:
            raise ValueError("Modelo topográfico sem referência geográfica.")
        return tuple(float(v) for v in transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21))


def _m8_catalogo_topografia():
    if not PASTA_TOPOGRAFIA.is_dir():
        return (), ()
    catalogo, erros = [], []
    for arquivo in PASTA_TOPOGRAFIA.rglob("*"):
        if not arquivo.is_file() or arquivo.suffix.lower() not in {".tif", ".tiff"}:
            continue
        try:
            info = arquivo.stat()
            oeste, sul, leste, norte = _m8_limites_dem(str(arquivo), info.st_mtime_ns, info.st_size)
            if (oeste < leste and sul < norte and
                    all(math.isfinite(v) for v in (oeste, sul, leste, norte))):
                catalogo.append((str(arquivo), info.st_mtime_ns, oeste, sul, leste, norte))
        except (OSError, ValueError, TypeError, ImportError, RuntimeError) as erro:
            erros.append(f"{arquivo.name}: {erro}")
    # Prefere raster de cobertura menor, possivelmente com maior detalhamento.
    catalogo.sort(key=lambda x: ((x[4] - x[2]) * (x[5] - x[3]), x[0]))
    return tuple(catalogo), tuple(erros)


@st.cache_data(show_spinner=False, max_entries=64)
def _m8_amostrar_dem(caminho: str, versao: int, coordenadas: tuple) -> tuple:
    """Lê em lote somente os pixels nas posições solicitadas, sem rasterizar o mapa."""
    _ = versao
    if not coordenadas:
        return ()
    import rasterio
    import numpy as np
    from rasterio.warp import transform
    with rasterio.open(caminho) as ds:
        if ds.crs is None or ds.count < 1:
            raise ValueError("GeoTIFF sem referência geográfica.")
        xs, ys = transform("EPSG:4326", ds.crs,
                           [lon for lat, lon in coordenadas],
                           [lat for lat, lon in coordenadas])
        b = ds.bounds
        posicoes = [i for i, (x, y) in enumerate(zip(xs, ys))
                    if math.isfinite(x) and math.isfinite(y)
                    and b.left <= x < b.right and b.bottom < y <= b.top]
        resultado = [None] * len(coordenadas)
        amostras = ds.sample(((xs[i], ys[i]) for i in posicoes), indexes=1, masked=True)
        for indice, amostra in zip(posicoes, amostras):
            valor = amostra[0]
            if np.ma.is_masked(valor):
                continue
            cota = float(valor)
            if not math.isfinite(cota):
                continue
            if ds.nodata is not None and np.isclose(cota, ds.nodata):
                continue
            resultado[indice] = cota
        return tuple(resultado)


def _m8_cotas_automaticas(coordenadas: tuple):
    """Busca a cota DSM/MDE. Sem cobertura: None (nunca inventa altitude)."""
    if not coordenadas:
        return (), ()
    catalogo, erros = _m8_catalogo_topografia()
    resultado, alertas = [None] * len(coordenadas), list(erros)
    for caminho, versao, oeste, sul, leste, norte in catalogo:
        pendentes = [i for i, (lat, lon) in enumerate(coordenadas)
                     if resultado[i] is None and sul <= lat <= norte and oeste <= lon <= leste]
        if not pendentes:
            continue
        try:
            amostras = _m8_amostrar_dem(caminho, versao, tuple(coordenadas[i] for i in pendentes))
            for i, cota in zip(pendentes, amostras):
                if cota is not None:
                    resultado[i] = cota
        except (OSError, ValueError, TypeError, ImportError, RuntimeError) as erro:
            alertas.append(f"{Path(caminho).name}: {erro}")
        if all(v is not None for v in resultado):
            break
    return tuple(resultado), tuple(alertas)


def _m8_formatar_cota(cota):
    return f"{float(cota):.0f} m" if cota is not None and math.isfinite(float(cota)) else ""


def _m8_area_desenhada(desenhos):
    """Converte o último polígono/retângulo do Leaflet para Shapely (WGS84)."""
    from shapely.geometry import shape
    if not desenhos:
        return None
    for item in reversed(desenhos):
        try:
            geometria = shape(item.get("geometry", item))
            geometria, erro = _validar_area_selecionada(geometria)
            if erro is None:
                return geometria
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def _m8_ativos_na_area(area, bases):
    """Conserva os ativos dentro do polígono, inclusive na borda."""
    if area is None:
        return []
    from shapely.geometry import Point
    from shapely.prepared import prep
    oeste, sul, leste, norte = area.bounds
    prepared = prep(area)
    saida = []
    for base in bases.values():
        if base.empty:
            continue
        candidatos = base.loc[base["_lat"].between(sul, norte) & base["_lon"].between(oeste, leste)]
        for registro in candidatos.to_dict("records"):
            if prepared.covers(Point(float(registro["_lon"]), float(registro["_lat"]))):
                saida.append(registro)
    return saida


def _m8_unir_ativos(ativos_proximos, ativos_area):
    """União com chave coordenada e identificador; evita marcadores duplicados."""
    saida = {}
    for ativo in [*ativos_proximos, *ativos_area]:
        tipo = str(ativo.get("_tipo", ""))
        identificador = str(ativo.get("ID_POCO") or ativo.get("ID_LOGGER") or
                            ativo.get("IDENTIFICACAO_ATIVO", ""))
        chave = (tipo, identificador, round(float(ativo["_lat"]), 6), round(float(ativo["_lon"]), 6))
        saida[chave] = ativo
    return list(saida.values())


def _m8_rótulo_html(texto, tipo="matricula"):
    classe = "m8-label-equip" if tipo == "ativo" else "m8-label-mat"
    return f'<div class="{classe}">{html_escape(str(texto))}</div>'


def _m8_adicionar_rotulo(mapa_grupo, lat, lon, texto, tipo="matricula", deslocamento=15):
    """Rótulo Leaflet não interativo: clique continua no marcador original."""
    folium.Marker(
        [lat, lon],
        icon=folium.DivIcon(
            html=_m8_rótulo_html(texto, tipo),
            icon_size=(0, 0),
            icon_anchor=(-deslocamento, 15),
            class_name="m8-label-anchor",
        ),
        interactive=False,
    ).add_to(mapa_grupo)


def _m8_mapa_unificado(clientes, ativos, raio, bairros, influencias, area,
                        rede, cotas, exibir_rotulos=True):
    """Mapa único: só matrículas pesquisadas; demais matrículas apenas na tabela."""
    from shapely.geometry import mapping

    lats = clientes["_lat"].astype(float).tolist()
    lons = clientes["_lon"].astype(float).tolist()
    # Mapa independente da pesquisa de matrícula.
    if lats:
        centro = [sum(lats) / len(lats), sum(lons) / len(lons)]
        zoom_inicial = 16 if len(clientes) == 1 else 13
    elif area is not None:
        centro = [float(area.centroid.y), float(area.centroid.x)]
        zoom_inicial = 13
    else:
        centro = [-5.089, -42.801]
        zoom_inicial = 12
    mapa = folium.Map(location=centro, zoom_start=zoom_inicial,
                      tiles="OpenStreetMap", control_scale=True)
    # Tela cheia nativa do Leaflet: mantém ferramentas de desenho, rede,
    # marcadores, pop-ups e camadas no mesmo mapa (sem novo componente).
    Fullscreen(
        position="topright",
        title="Expandir mapa para tela cheia",
        title_cancel="Sair da tela cheia",
        force_separate_button=True,
    ).add_to(mapa)
    mapa.get_root().header.add_child(Element("""
    <style>
    .m8-label-anchor {background:transparent!important; border:0!important;}
    .m8-label-mat,.m8-label-equip {
      white-space:nowrap; display:inline-block; pointer-events:none;
      padding:3px 6px; border-radius:5px;
      background:rgba(255,255,255,.96); color:#18354a;
      box-shadow:0 1px 5px rgba(12,42,64,.28);
      border:1px solid rgba(22,84,128,.25);
      font:600 11px/1.2 Arial,sans-serif;
    }
    .m8-label-mat{border-left:3px solid #16934b;}
    .m8-label-equip{border-left:3px solid #e68e23;font-size:10px;}
    .leaflet-control-layers-expanded {max-height:260px;overflow-y:auto;
       font:11px/1.25 Arial,sans-serif;}
    </style>
    """))

    if bairros:
        grupo = folium.FeatureGroup(name="Limites dos bairros", show=True)
        for bairro in bairros:
            folium.Polygon(locations=bairro["coords"], color="#6387a3", weight=1.15,
                           fill=True, fill_color="#88b8de", fill_opacity=.035,
                           tooltip=f"Bairro: {html_escape(str(bairro['nome']))}").add_to(grupo)
        grupo.add_to(mapa)
    if influencias:
        grupo = folium.FeatureGroup(name="Áreas de influência", show=True)
        for zona in influencias:
            folium.Polygon(locations=([zona["contorno"]] + zona["buracos"]
                                      if zona["buracos"] else zona["contorno"]),
                           color="#9871b2", weight=1.5, fill=True,
                           fill_opacity=.06, tooltip=html_escape(str(zona["nome"]))).add_to(grupo)
        grupo.add_to(mapa)
    if area is not None:
        folium.GeoJson(mapping(area), name="Área desenhada", show=True,
                       style_function=lambda _: {"color":"#087d8c", "weight":3,
                                                  "fillColor":"#22b8b8", "fillOpacity":.09}).add_to(mapa)
    if rede and rede.get("trechos", 0):
        folium.GeoJson(
            rede["geojson"], name="Rede de água — recorte", show=True,
            style_function=lambda _: {"color": "#0874bc", "weight": 2.6, "opacity": .9},
            tooltip=folium.GeoJsonTooltip(
                fields=["identificacao", "diametro_nominal", "material", "tipo_pavimento"],
                aliases=["Trecho (GID):", "Diâmetro nominal:", "Material:", "Pavimento:"],
                sticky=True, labels=True,
            ),
            popup=folium.GeoJsonPopup(
                fields=["identificacao", "trecho_atual", "trecho_antigo",
                        "diametro_nominal", "material", "tipo_pavimento",
                        "extensao", "profundidade", "funcao", "situacao",
                        "data_implantacao"],
                aliases=["Trecho (GID):", "Trecho atual:", "Trecho antigo:",
                         "Diâmetro nominal:", "Material:", "Tipo de pavimento:",
                         "Extensão:", "Profundidade:", "Função:", "Situação:",
                         "Data de implantação:"],
                labels=True,
                style="font-family:Segoe UI,Arial,sans-serif;font-size:12px;",
            ),
        ).add_to(mapa)

    # Os demais registros do polígono são apresentados apenas na tabela e no CSV.
    # Nunca criar marcadores ou rótulos para centenas de matrículas da área.
    # Assim, a leitura visual prioriza a matrícula pesquisada e os equipamentos.

    grupo_clientes = folium.FeatureGroup(name="Matrículas consultadas", show=True)
    for _, linha in clientes.iterrows():
        lat, lon = float(linha["_lat"]), float(linha["_lon"])
        matricula = str(linha["NUM_LIGACAO"])
        cota = cotas.get((lat, lon))
        elevacao = _m8_formatar_cota(cota)
        pop = _popup_cliente(linha)
        if elevacao:
            pop += f"<div><b>Cota da superfície:</b> {elevacao}</div>"
        folium.Marker([lat, lon], tooltip=f"Matrícula {matricula}" +
                      (f" • {elevacao}" if elevacao else ""),
                      popup=folium.Popup(pop, max_width=380),
                      icon=folium.Icon(color="green", icon="home", prefix="fa")).add_to(grupo_clientes)
        folium.Circle([lat, lon], radius=raio, color="#397cba", fill=False,
                      opacity=.32, weight=1, tooltip=f"Proximidade: {raio} m").add_to(grupo_clientes)
        if exibir_rotulos:
            _m8_adicionar_rotulo(grupo_clientes, lat, lon,
                                 f"{matricula} • {elevacao}" if elevacao else matricula,
                                 deslocamento=19)
    grupo_clientes.add_to(mapa)

    for tipo, cor, icone in (("Poço / Ativo", "blue", "tint"), ("Logger", "orange", "signal")):
        grupo = folium.FeatureGroup(name=tipo + "(s)", show=True)
        filtrados = [ativo for ativo in ativos if ativo.get("_tipo") == tipo]
        for i, ativo in enumerate(filtrados[:LIMITE_EQUIPAMENTOS_MAPA]):
            lat, lon = float(ativo["_lat"]), float(ativo["_lon"])
            nome = str(ativo.get("IDENTIFICACAO_ATIVO") or ativo.get("_nome") or "").strip()
            elevacao = _m8_formatar_cota(cotas.get((lat, lon)))
            # A cota é do terreno (DSM/MDE), nunca a altitude hidráulica do ativo.
            popup = _popup_ativo(ativo)
            if elevacao:
                popup += f"<div><b>Cota da superfície:</b> {elevacao}</div>"
            tooltip = f"{tipo}: {nome}" + (f" • {elevacao}" if elevacao else "")
            folium.Marker([lat, lon], popup=folium.Popup(popup, max_width=360),
                          tooltip=tooltip,
                          icon=folium.Icon(color=cor, icon=icone, prefix="fa")).add_to(grupo)
            if exibir_rotulos and i < 110 and nome:
                texto_rotulo = f"{nome[:36]} • {elevacao}" if elevacao else nome[:36]
                _m8_adicionar_rotulo(grupo, lat, lon, texto_rotulo,
                                     tipo="ativo", deslocamento=17)
        grupo.add_to(mapa)

    Draw(export=False, position="topleft",
         draw_options={"polygon": {"allowIntersection": False, "showArea": True},
                       "rectangle": True, "polyline": False, "circle": False,
                       "circlemarker": False, "marker": False},
         edit_options={"edit": False, "remove": True}).add_to(mapa)
    if area is not None:
        oeste, sul, leste, norte = area.bounds
        mapa.fit_bounds([[sul, oeste], [norte, leste]], padding=(35, 35), max_zoom=17)
    elif len(clientes) > 1:
        mapa.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]],
                        padding=(60, 60), max_zoom=16)
    folium.LayerControl(collapsed=True, position="topright").add_to(mapa)
    return mapa


def exibir_consulta_operacional() -> None:
    """Mapa livre por área ou pesquisa por matrícula, mantendo ferramentas integradas."""
    if (not verificar_autenticacao()
            or str(st.session_state.get("perfil", "")).strip().lower() not in PERFIS_CONSULTA):
        st.error("Acesso não autorizado. Entre pelo Hub Central.")
        st.stop()

    if st.button("⬅️ Voltar ao Menu Principal", key="m8_voltar_menu"):
        st.switch_page("app.py")
    st.title("🔍 Consulta Operacional — Matrículas e Rede")
    st.caption("Mapa unificado • Matrículas da área em tabela • Equipamentos com elevação • Somente leitura")
    base_matriculas_disponivel = (
        _arquivo_matriculas() is not None or _arquivo_sqlite_publicado() is not None
    )
    if not base_matriculas_disponivel:
        st.warning(
            "Base cadastral indisponível: mapa e rede disponíveis, "
            "mas sem consulta ou listagem de matrículas."
        )

    if "m8_integrado_inicializou" not in st.session_state:
        st.session_state.pop("consulta_operacional_entrada", None)
        st.session_state["m8_integrado_inicializou"] = True

    def _m8_limpar_pesquisa():
        """Remove a busca de matrículas, mas mantém o desenho e a rede da área."""
        st.session_state["consulta_operacional_entrada"] = ""
        st.session_state.pop("m8_integrado_consulta", None)
        # Remonta o mapa sem marcadores da consulta anterior.
        st.session_state["m8_integrado_versao_mapa"] = (
            st.session_state.get("m8_integrado_versao_mapa", 0) + 1
        )

    with st.form("m8_busca_integrada"):
        texto = st.text_area("Matrícula(s)", key="consulta_operacional_entrada", height=78,
                             placeholder="Informe uma ou mais matrículas, separadas por vírgula, espaço ou linha")
        col1, col2 = st.columns([2, 1])
        with col1:
            raio = st.number_input("Raio para identificar equipamentos próximos (m)",
                                   min_value=50, max_value=5000, value=750, step=50,
                                   key="m8_raio_integrado")
        with col2:
            mostrar_bairros = st.checkbox("Limites dos bairros", value=True,
                                         key="m8_bairros_integrado")
        pesquisar = st.form_submit_button(
            "🔎 Consultar matrículas / Atualizar mapa", type="primary",
            use_container_width=True,
        )

    st.button(
        "🧹 Limpar pesquisa", key="m8_limpar_pesquisa",
        on_click=_m8_limpar_pesquisa,
        help="Apaga as matrículas consultadas e seus resultados sem remover a área desenhada.",
    )

    if pesquisar:
        codigos, invalidos = _interpretar_pesquisa(texto)
        if invalidos:
            st.error("Matrículas inválidas: " + ", ".join(invalidos[:12]))
        elif len(codigos) > 200:
            st.warning("Para preservar o desempenho, consulte até 200 matrículas de cada vez.")
        elif codigos and not base_matriculas_disponivel:
            st.warning("A base de matrículas está indisponível para pesquisa.")
        else:
            # Sem códigos, o mapa permanece livre e o desenho atual é preservado.
            st.session_state["m8_integrado_consulta"] = (
                codigos, int(raio), bool(mostrar_bairros)
            )
            if codigos:
                st.session_state.pop("m8_integrado_area", None)
                st.session_state.pop("m8_integrado_rede_id", None)
                st.session_state["m8_integrado_versao_mapa"] = (
                    st.session_state.get("m8_integrado_versao_mapa", 0) + 1
                )
            st.rerun()

    atual = st.session_state.get("m8_integrado_consulta")
    codigos, raio, mostrar_bairros = (
        atual if atual is not None else ([], int(raio), bool(mostrar_bairros))
    )
    validos = pd.DataFrame(columns=[*COLUNAS_MATRICULAS, "_lat", "_lon"])
    if codigos and base_matriculas_disponivel:
        try:
            with st.spinner("Consultando índice cadastral..."):
                clientes, _ = _buscar_matriculas(codigos)
            presentes = set(clientes["NUM_LIGACAO"].astype(str))
            faltantes = [v for v in codigos if v not in presentes]
            validos = clientes.loc[
                clientes["_lat"].notna() & clientes["_lon"].notna()
            ].copy()
            c1, c2, c3 = st.columns(3)
            c1.metric("Matrículas encontradas", len(presentes))
            c2.metric("Com coordenadas", validos["NUM_LIGACAO"].nunique())
            c3.metric("Não encontradas", len(faltantes))
            if faltantes:
                st.warning("Matrículas ausentes: " + ", ".join(faltantes))
            if validos.empty:
                st.info(
                    "Nenhuma matrícula pesquisada possui coordenadas válidas. "
                    "O mapa continua disponível para análise da área."
                )
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as erro:
            st.error(f"Não foi possível consultar as matrículas: {erro}")
    elif not codigos:
        st.caption(
            "Consulta livre: desenhe um polígono ou retângulo no mapa. "
            "Não é necessário informar matrícula."
        )

    from shapely import wkb
    area = None
    if st.session_state.get("m8_integrado_area"):
        try:
            area = wkb.loads(st.session_state["m8_integrado_area"])
            area, invalida = _validar_area_selecionada(area)
            if invalida:
                st.warning(invalida)
                area = None
        except (ValueError, TypeError):
            area = None
    area_id = hashlib.sha256(area.wkb).hexdigest() if area is not None else None

    # Leitura das camadas é restrita ao contexto da consulta. Nunca exibe a rede
    # da cidade inteira nem todas as matrículas na interface.
    influencias = []
    arquivo_influencias = _caminho_influencias()
    if arquivo_influencias and (area is not None or not validos.empty):
        try:
            todas_influencias = _ler_influencias(_assinatura(arquivo_influencias))
            if not validos.empty:
                validos["_influencia"] = validos.apply(
                    lambda linha: "; ".join(_identificar_influencias(float(linha["_lat"]), float(linha["_lon"]), todas_influencias))
                    or "Área de influência não identificada", axis=1)
            # No mapa, apenas feições próximas; o catálogo completo não é enviado.
            from shapely.geometry import box
            if area is not None:
                contexto = box(*area.bounds).buffer(.008)
            else:
                contexto = box(min(validos["_lon"])-.008, min(validos["_lat"])-.008,
                               max(validos["_lon"])+.008, max(validos["_lat"])+.008)
            influencias = [z for z in todas_influencias if z["geometria"].intersects(contexto)][:90]
        except Exception as erro:
            st.warning(f"Áreas de influência indisponíveis: {erro}")
            validos["_influencia"] = "Não disponível"
    else:
        validos["_influencia"] = "Não disponível"

    bairros = []
    if mostrar_bairros:
        caminho_bairros = _caminho_bairros_teresina()
        if caminho_bairros:
            try:
                todas = _bairros_geograficos(_assinatura(caminho_bairros))
                if area is not None:
                    bx = area.bounds
                    oeste, sul, leste, norte = bx[0]-.008, bx[1]-.008, bx[2]+.008, bx[3]+.008
                elif not validos.empty:
                    oeste, sul = min(validos["_lon"])-.008, min(validos["_lat"])-.008
                    leste, norte = max(validos["_lon"])+.008, max(validos["_lat"])+.008
                else:
                    oeste, sul, leste, norte = -42.94, -5.23, -42.66, -4.96
                bairros = [b for b in todas if not (b["limites"][2] < oeste or b["limites"][0] > leste
                                                      or b["limites"][3] < sul or b["limites"][1] > norte)][:90]
            except Exception as erro:
                st.warning(f"Limites de bairros não puderam ser carregados: {erro}")

    cadastro_area = pd.DataFrame(columns=list(COLUNAS_MATRICULAS))
    if area is not None and base_matriculas_disponivel:
        try:
            with st.spinner("Localizando as matrículas dentro do desenho..."):
                cadastro_area, _ = _matriculas_na_area(area)
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as erro:
            st.error(f"Não foi possível consultar as matrículas do polígono: {erro}")

    bases = {}
    # Evita carregar milhares de ativos antes da primeira consulta/desenho.
    if area is not None or not validos.empty:
        for tipo, caminho in (("Poço / Ativo", ARQUIVO_POCOS), ("Logger", ARQUIVO_LOGGERS)):
            if not caminho.is_file():
                st.warning(f"Cadastro ausente: {caminho.name}.")
                continue
            try:
                bases[tipo] = _ler_ativos_csv(_assinatura(caminho), tipo)
            except (OSError, ValueError, pd.errors.ParserError) as erro:
                st.warning(f"Não foi possível carregar {caminho.name}: {erro}")
    if validos.empty:
        # No modo livre, não constrói índice de proximidade sem matrículas.
        proximidade = pd.DataFrame(columns=[
            "Matrícula", "Tipo", "Identificação", "Nome / endereço", "Distância (m)"
        ])
        ativos_perto = []
    else:
        proximidade, ativos_perto = _consultar_proximidade(validos, bases, raio)
    ativos_area = _m8_ativos_na_area(area, bases)
    ativos = _m8_unir_ativos(ativos_perto, ativos_area)

    # As cotas do DSM/MDE são calculadas apenas para matrículas consultadas
    # e equipamentos que serão desenhados no mapa. Matrículas adicionais do
    # polígono ficam exclusivamente na tabela, evitando leituras desnecessárias.
    coords_selecionadas = [(float(r["_lat"]), float(r["_lon"])) for _, r in validos.iterrows()]
    coords_equipamentos = []
    for tipo in ("Poço / Ativo", "Logger"):
        equipamento_tipo = [a for a in ativos if a.get("_tipo") == tipo]
        coords_equipamentos.extend(
            (float(a["_lat"]), float(a["_lon"]))
            for a in equipamento_tipo[:LIMITE_EQUIPAMENTOS_MAPA]
        )
    coords_para_cotas = tuple(dict.fromkeys([*coords_selecionadas, *coords_equipamentos]))
    try:
        cotas, avisos_cota = _m8_cotas_automaticas(coords_para_cotas)
        mapa_cotas = dict(zip(coords_para_cotas, cotas))
        if avisos_cota:
            st.caption("Alguns arquivos topográficos não puderam ser consultados; "+"; ".join(avisos_cota[:2]))
    except (ImportError, OSError, ValueError, RuntimeError) as erro:
        st.warning(f"Elevação indisponível: {erro}")
        mapa_cotas = {}

    col_mapa, col_rede, col_limpar = st.columns([3.5, 1.4, 1.2], vertical_alignment="bottom")
    with col_mapa:
        st.subheader("🗺️ Mapa integrado")
        st.caption("Use as ferramentas de polígono ou retângulo no canto superior esquerdo do mapa. A listagem será atualizada ao terminar o desenho.")
    with col_rede:
        rede_ativa = bool(area_id and st.session_state.get("m8_integrado_rede_id") == area_id)
        rotulo_botao = "Ocultar rede" if rede_ativa else "Exibir rede da área"
        if st.button(rotulo_botao, key="m8_integrado_botao_rede", disabled=area is None,
                     use_container_width=True):
            if rede_ativa:
                st.session_state.pop("m8_integrado_rede_id", None)
            else:
                st.session_state["m8_integrado_rede_id"] = area_id
            st.rerun()
    with col_limpar:
        if st.button("Limpar desenho", key="m8_integrado_limpar_area",
                     disabled=area is None, use_container_width=True):
            st.session_state.pop("m8_integrado_area", None)
            st.session_state.pop("m8_integrado_rede_id", None)
            st.session_state["m8_integrado_versao_mapa"] = st.session_state.get("m8_integrado_versao_mapa", 0) + 1
            st.rerun()

    rede = None
    if area is not None and st.session_state.get("m8_integrado_rede_id") == area_id:
        if ARQUIVO_REDE_AGUA.is_file():
            try:
                with st.spinner("Recortando a rede apenas para a área desenhada..."):
                    rede = _rede_recortada_kmz(_assinatura(ARQUIVO_REDE_AGUA), area.wkb)
                st.caption(f"Rede visível: {rede['trechos']:,} trecho(s), {rede['vertices']:,} vértices".replace(",", "."))
                if rede["limitado"]:
                    st.warning("A rede foi limitada para manter o mapa fluido. Desenhe uma área menor para ver todos os trechos.")
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, ET.ParseError) as erro:
                st.error(f"Não foi possível carregar o recorte da rede: {erro}")
        else:
            st.warning("Arquivo dados/Rede_Agua_THE.kmz não encontrado no repositório.")

    rotulos = st.checkbox("Exibir rótulos das matrículas consultadas e equipamentos", value=True,
                         key="m8_integrado_rotulos")
    mapa = _m8_mapa_unificado(validos, ativos, raio, bairros, influencias, area,
                              rede, mapa_cotas, rotulos)
    retorno = st_folium(
        mapa, width=None, height=660, returned_objects=["all_drawings"],
        key=f"m8_mapa_integrado_{st.session_state.get('m8_integrado_versao_mapa', 0)}",
    )
    if isinstance(retorno, dict):
        nova_area = _m8_area_desenhada(retorno.get("all_drawings") or [])
        if nova_area is not None:
            novo_wkb = nova_area.wkb
            if novo_wkb != st.session_state.get("m8_integrado_area"):
                st.session_state["m8_integrado_area"] = novo_wkb
                st.session_state.pop("m8_integrado_rede_id", None)
                st.rerun()

    if rede and rede.get("trechos", 0):
        with st.expander("Atributos da rede dentro da área", expanded=False):
            colunas_rede = {
                "identificacao": "Trecho (GID)",
                "trecho_atual": "Trecho atual",
                "trecho_antigo": "Trecho antigo",
                "diametro_nominal": "Diâmetro nominal",
                "material": "Material",
                "tipo_pavimento": "Tipo de pavimento",
                "extensao": "Extensão",
                "profundidade": "Profundidade",
                "funcao": "Função",
                "situacao": "Situação",
                "data_implantacao": "Data de implantação",
            }
            tabela_rede = pd.DataFrame(
                [feicao["properties"] for feicao in rede["geojson"]["features"]]
            ).drop_duplicates(subset=["identificacao"])
            tabela_rede = tabela_rede[list(colunas_rede)].rename(columns=colunas_rede)
            st.caption(
                f"{len(tabela_rede)} trecho(s) distinto(s) no recorte. "
                "Os valores são os atributos originais do KML; "
                "o desenho continua simplificado apenas para renderização."
            )
            st.dataframe(tabela_rede, hide_index=True, use_container_width=True, height=300)
            st.download_button(
                "Exportar atributos da rede (CSV)",
                data=tabela_rede.to_csv(index=False, sep=";").encode("utf-8-sig"),
                file_name="rede_agua_atributos_PRISMA.csv",
                mime="text/csv", key="m8_exportar_atributos_rede",
            )

    if area is None:
        st.info("Desenhe uma área no mapa para listar todas as matrículas do perímetro e habilitar o recorte da rede, sem abrir outro mapa.")
    else:
        x1, x2, x3 = st.columns(3)
        x1.metric("Registros no polígono", len(cadastro_area))
        x2.metric("Matrículas distintas", cadastro_area["NUM_LIGACAO"].nunique() if not cadastro_area.empty else 0)
        x3.metric("Equipamentos no polígono", len(ativos_area))
        st.markdown("#### Matrículas dentro da área desenhada")
        if cadastro_area.empty:
            st.info("Nenhuma matrícula com coordenadas válidas foi localizada no polígono.")
        else:
            colunas = list(COLUNAS_MATRICULAS)
            st.dataframe(cadastro_area[colunas].rename(columns={
                "CATEGORIA": "Categoria", "NUM_LIGACAO": "Matrícula", "BAIRRO": "Bairro",
                "COD_LATITUDE": "Latitude", "COD_LONGITUDE": "Longitude", "END_LIGACAO": "Endereço"
            }), hide_index=True, use_container_width=True, height=315)
            st.download_button("⬇️ Exportar matrículas do polígono (CSV)",
                data=cadastro_area[colunas].to_csv(index=False, sep=";").encode("utf-8-sig"),
                file_name="matriculas_perimetro_PRISMA.csv", mime="text/csv",
                key="m8_exportar_poligono")
            st.caption("As matrículas adicionais do polígono são exibidas somente nesta tabela e no CSV. "
                       "O mapa mantém os marcadores das matrículas pesquisadas e dos equipamentos.")

    st.markdown("#### Equipamentos próximos")
    if ativos:
        tipos = [str(a.get("_tipo", "")) for a in ativos]
        st.caption(f"{tipos.count('Poço / Ativo')} poço(s)/ativo(s) e {tipos.count('Logger')} logger(s) "
                   "próximos às matrículas consultadas ou dentro da área desenhada.")
        resumo_ativos = pd.DataFrame([{
            "Tipo": a.get("_tipo", ""), "Identificação": a.get("IDENTIFICACAO_ATIVO", ""),
            "Nome / Endereço": a.get("_nome", ""),
            "Latitude": round(float(a["_lat"]), 6), "Longitude": round(float(a["_lon"]), 6),
        } for a in ativos])
        st.dataframe(resumo_ativos, hide_index=True, use_container_width=True, height=230)
    else:
        st.info("Nenhum equipamento encontrado no raio de pesquisa ou na área desenhada.")

    if not validos.empty:
        with st.expander("Dados cadastrais e áreas de influência das matrículas consultadas", expanded=False):
            st.dataframe(validos.loc[:, list(COLUNAS_MATRICULAS) + ["_influencia"]].rename(columns={
                "NUM_LIGACAO": "Matrícula", "_influencia": "Área de Influência",
            }), hide_index=True, use_container_width=True)
            if not proximidade.empty:
                st.markdown("**Distâncias dos equipamentos às matrículas consultadas**")
                st.dataframe(proximidade, hide_index=True, use_container_width=True)

    st.caption("As cotas são da superfície do modelo digital de elevação (DSM/MDE), não da tubulação. "
               "A proximidade da rede e dos ativos não comprova vínculo hidráulico. "
               "Os registros da área vêm exclusivamente do cadastro de matrículas, sem vínculo com o Farol.")


exibir_consulta_operacional()
