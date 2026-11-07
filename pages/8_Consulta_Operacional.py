"""Módulo 8 — Consulta Operacional (Teresina).

Consulta de matrículas e seleção territorial com rede de água sob demanda.
- Acesso aos perfis autenticados admin, usuario e operador.
- Base de matrículas XLS/XLSX ou SQLite indexado, somente leitura.
- Índice SQLite local criado SOMENTE na primeira pesquisa caso necessário.
- Nenhum histórico de consultas, nem escrita no Excel ou em dados de produção.
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

import folium
from folium.plugins import MarkerCluster, Draw
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
LIMITE_MARCADORES_OS = 800

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


def _mapa_consulta(clientes: pd.DataFrame, ativos: list[dict], raio: int,
                   bairros: list[dict], mostrar_bairros: bool, areas: list[dict]) -> folium.Map:
    latitudes = clientes["_lat"].astype(float).tolist()
    longitudes = clientes["_lon"].astype(float).tolist()
    mapa = folium.Map(location=[sum(latitudes) / len(latitudes), sum(longitudes) / len(longitudes)],
                      zoom_start=14, control_scale=True, tiles="OpenStreetMap")

    if mostrar_bairros and bairros:
        grupo = folium.FeatureGroup(name="Limites de bairros", show=True)
        for bairro in bairros:
            folium.Polygon(
                locations=bairro["coords"], color="#578bba", weight=1.5,
                fill=True, fill_color="#88b8de", fill_opacity=0.06,
                tooltip=f"Bairro: {bairro['nome']}",
            ).add_to(grupo)
        grupo.add_to(mapa)

    # Nova camada separada dos bairros. Transparência para não ocultar marcadores.
    if areas:
        grupo_influencias = folium.FeatureGroup(name="Áreas de influência", show=True)
        for area in areas:
            folium.Polygon(
                locations=([area["contorno"]] + area["buracos"]
                           if area["buracos"] else area["contorno"]),
                color="#8762ab", weight=1.7, fill=True,
                fill_color="#8762ab", fill_opacity=0.11,
                tooltip=f"Área de influência: {area['nome']}",
            ).add_to(grupo_influencias)
        grupo_influencias.add_to(mapa)

    grupo_clientes = folium.FeatureGroup(name="Matrículas", show=True)
    aglomerador = MarkerCluster(name="Matrículas", overlay=False, control=False).add_to(grupo_clientes)
    for _, linha in clientes.iterrows():
        lat, lon = float(linha["_lat"]), float(linha["_lon"])
        folium.Marker(
            [lat, lon], tooltip=f"Matrícula: {linha['NUM_LIGACAO']}",
            popup=folium.Popup(_popup_cliente(linha), max_width=370),
            icon=folium.Icon(color="green", icon="home", prefix="fa"),
        ).add_to(aglomerador)
        folium.Circle([lat, lon], radius=raio, color="#227ab6", fill=False,
                      weight=1, opacity=0.30, tooltip=f"Raio de busca: {raio} m").add_to(grupo_clientes)
    grupo_clientes.add_to(mapa)

    # Mesmos ícones. Mudam SOMENTE as cores dos marcadores.
    for tipo, cor, icone in (("Poço / Ativo", "blue", "tint"), ("Logger", "orange", "signal")):
        grupo = folium.FeatureGroup(name=tipo + " próximo(s)", show=True)
        for ativo in ativos:
            if ativo.get("_tipo") != tipo:
                continue
            folium.Marker(
                [float(ativo["_lat"]), float(ativo["_lon"])],
                tooltip=f"{tipo}: {ativo.get('IDENTIFICACAO_ATIVO', '')}",
                popup=folium.Popup(_popup_ativo(ativo), max_width=360),
                icon=folium.Icon(color=cor, icon=icone, prefix="fa"),
            ).add_to(grupo)
        grupo.add_to(mapa)

    if len(clientes) > 1:
        mapa.fit_bounds([[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]], padding=(45, 45))
    folium.LayerControl(collapsed=False).add_to(mapa)
    return mapa



# ============================================================
# SELEÇÃO DE ÁREA — REDE DE ÁGUA E ORDENS DE SERVIÇO
# ============================================================

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
    if xmax - xmin > 0.28 or ymax - ymin > 0.28:
        return None, "Área muito extensa. Selecione um bairro ou desenhe um polígono menor."
    if geometria.area < 1e-11:
        return None, "Polígono muito pequeno ou degenerado."
    return geometria, None


def _mapa_selecao_area(geometria=None, permitir_desenho=False) -> folium.Map:
    """Mapa leve: não contém a camada de rede nem os registros do Farol."""
    mapa = folium.Map(location=[-5.0892, -42.8019], zoom_start=12,
                      control_scale=True, tiles="OpenStreetMap")
    if geometria is not None:
        from shapely.geometry import mapping
        folium.GeoJson(mapping(geometria), name="Área selecionada",
                       style_function=lambda _: {"color": "#007d8c", "weight": 3,
                                                  "fillColor": "#24b7b5", "fillOpacity": .14}).add_to(mapa)
        xmin, ymin, xmax, ymax = geometria.bounds
        mapa.fit_bounds([[ymin, xmin], [ymax, xmax]], padding=(22, 22))
    if permitir_desenho:
        Draw(export=False, position="topleft",
             draw_options={"polygon": True, "rectangle": True, "polyline": False,
                           "circle": False, "marker": False, "circlemarker": False},
             edit_options={"edit": False}).add_to(mapa)
    return mapa


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
                            for segmento in _linhas_de_geometria(recorte):
                                # ~1 metro, apenas para renderização no navegador.
                                simples = segmento.simplify(0.000009, preserve_topology=False)
                                if simples.geom_type != "LineString" or len(simples.coords) < 2:
                                    simples = segmento
                                n = len(simples.coords)
                                if len(feicoes) >= limite_trechos or vertices + n > limite_vertices:
                                    atingiu_limite = True
                                    break
                                feicoes.append({"type": "Feature", "properties": {"nome": titulo},
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


@st.cache_data(ttl=120, max_entries=2, show_spinner=False)
def _ler_os_farol_para_area(id_planilha: str) -> pd.DataFrame:
    """Lê somente a aba PONTOS do Farol, sem gravar ou modificar registros."""
    import gspread
    from google.oauth2.service_account import Credentials

    conf = st.secrets["gcp_json"]
    if isinstance(conf, str):
        conf = json.loads(conf)
    else:
        conf = dict(conf)
    creds = Credentials.from_service_account_info(
        conf, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    registros = (gspread.authorize(creds).open_by_key(id_planilha)
                 .worksheet("PONTOS").get_all_values())
    if not registros:
        return pd.DataFrame()
    cabecalho = [str(c).strip() for c in registros[0]]
    dados = [linha[:len(cabecalho)] + [""] * max(0, len(cabecalho) - len(linha))
             for linha in registros[1:] if any(str(c).strip() for c in linha)]
    return pd.DataFrame(dados, columns=cabecalho)


def _filtrar_os_no_poligono(base: pd.DataFrame, area):
    """Usa coordenadas reais; inclui pontos na borda e todas as situações de O.S."""
    from shapely.geometry import Point
    from shapely.prepared import prep

    if base is None or base.empty:
        return pd.DataFrame(), 0
    essenciais = {"Latitude", "Longitude", "Nº da O.S"}
    ausentes = essenciais - set(base.columns)
    if ausentes:
        raise ValueError("Aba PONTOS sem coluna(s): " + ", ".join(sorted(ausentes)))
    lat = base["Latitude"].map(lambda v: _coordenada(v, "lat"))
    lon = base["Longitude"].map(lambda v: _coordenada(v, "lon"))
    validas = lat.notna() & lon.notna()
    ignoradas = int((~validas).sum())
    xmin, ymin, xmax, ymax = area.bounds
    candidatas = base.loc[validas & lat.between(ymin, ymax) & lon.between(xmin, xmax)].copy()
    if candidatas.empty:
        return candidatas, ignoradas
    preparado = prep(area)
    dentro = [preparado.covers(Point(float(lon.at[i]), float(lat.at[i])))
              for i in candidatas.index]
    return candidatas.loc[dentro].copy().reset_index(drop=True), ignoradas


def _mapa_rede_e_os(area, rede: dict | None, os_area: pd.DataFrame) -> folium.Map:
    """Apresenta apenas feições já recortadas e amostra limitada de marcadores."""
    from shapely.geometry import mapping

    xmin, ymin, xmax, ymax = area.bounds
    mapa = folium.Map(location=[(ymin + ymax) / 2, (xmin + xmax) / 2],
                      zoom_start=15, tiles="OpenStreetMap", control_scale=True)
    folium.GeoJson(mapping(area), name="Área selecionada",
                   style_function=lambda _: {"color": "#087c8f", "weight": 2.6,
                                              "fillColor": "#32bec3", "fillOpacity": 0.07}).add_to(mapa)
    if rede is not None and rede.get("trechos", 0):
        folium.GeoJson(rede["geojson"], name="Rede de água (recorte)", show=True,
                       style_function=lambda _: {"color": "#0868b3", "weight": 2.5,
                                                  "opacity": .85}).add_to(mapa)
    grupo = folium.FeatureGroup(name="O.S. dentro do polígono", show=True)
    for _, linha in os_area.head(LIMITE_MARCADORES_OS).iterrows():
        lat, lon = _coordenada(linha.get("Latitude"), "lat"), _coordenada(linha.get("Longitude"), "lon")
        if lat is None or lon is None:
            continue
        numero = html_escape(str(linha.get("Nº da O.S", "")))
        status = html_escape(str(linha.get("Status OS", "")))
        matricula = html_escape(str(linha.get("Matrícula", "")))
        folium.CircleMarker([lat, lon], radius=4.5, weight=1,
                            color="#ffffff", fill=True, fill_color="#f18b22",
                            fill_opacity=.95, tooltip=f"O.S.: {numero}",
                            popup=folium.Popup(f"<b>O.S.:</b> {numero}<br>"
                                               f"<b>Matrícula:</b> {matricula}<br>"
                                               f"<b>Status:</b> {status}", max_width=320)).add_to(grupo)
    grupo.add_to(mapa)
    mapa.fit_bounds([[ymin, xmin], [ymax, xmax]], padding=(20, 20))
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def exibir_selecao_area_rede_os() -> None:
    """Área selecionada, O.S. no polígono e rede sob demanda, sem depender de matrícula."""
    st.subheader("🗺️ Seleção de área — Rede de água e O.S.")
    st.caption("Selecione um bairro de Teresina ou desenhe um polígono. "
               "As O.S. são filtradas pelas coordenadas, incluindo pontos na borda. "
               "A rede só será lida quando você solicitar sua exibição.")

    modo = st.radio("Como deseja selecionar a área?",
                    ("Selecionar bairro", "Desenhar polígono"), horizontal=True,
                    key="m8_modo_area")
    bairro_area = None
    area = None
    if modo == "Selecionar bairro":
        caminho = _caminho_bairros_teresina()
        if caminho is None:
            st.warning("Arquivo de bairros de Teresina não encontrado. Use a opção Desenhar polígono.")
        else:
            try:
                bairros = _bairros_geograficos(_assinatura(caminho))
                opcoes = sorted({str(b["nome"]).strip() for b in bairros if str(b["nome"]).strip()},
                                key=_normalizar_texto)
                bairro_area = st.selectbox("Bairro", options=opcoes, index=None,
                                           placeholder="Escolha o bairro para consultar",
                                           key="m8_bairro_selecionado")
                if bairro_area:
                    area = _geometria_area_bairro(bairros, bairro_area)
            except Exception as erro:
                st.warning(f"Não foi possível carregar os bairros: {erro}")
    else:
        st.info("Use a ferramenta de polígono ou retângulo no canto superior esquerdo "
                "do mapa. Ao finalizar o desenho, clique em Consultar O.S. da área.")
        wkb_desenho = st.session_state.get("m8_poligono_desenhado")
        if wkb_desenho:
            from shapely import wkb
            area = wkb.loads(wkb_desenho)
        if st.button("Limpar desenho", key="m8_limpar_desenho"):
            st.session_state.pop("m8_poligono_desenhado", None)
            # Novo identificador reinicia também o estado da ferramenta Draw.
            st.session_state["m8_versao_mapa_desenho"] = (
                st.session_state.get("m8_versao_mapa_desenho", 0) + 1
            )
            st.rerun()

    # Não depende das matrículas e não lê o KMZ de rede nesta etapa.
    vista = _mapa_selecao_area(area, permitir_desenho=(modo == "Desenhar polígono"))
    retorno = st_folium(vista, width=None, height=490,
                        returned_objects=["all_drawings"] if modo == "Desenhar polígono" else [],
                        key=(f"m8_mapa_escolha_area_{'desenho' if modo == 'Desenhar polígono' else 'bairro'}_"
                             f"{st.session_state.get('m8_versao_mapa_desenho', 0)}"))
    if modo == "Desenhar polígono" and isinstance(retorno, dict):
        desenhos = retorno.get("all_drawings") or []
        for item in reversed(desenhos):
            try:
                from shapely.geometry import shape
                candidato = shape(item.get("geometry", item))
                candidato, erro = _validar_area_selecionada(candidato)
                if erro is None:
                    if candidato.wkb != st.session_state.get("m8_poligono_desenhado"):
                        st.session_state["m8_poligono_desenhado"] = candidato.wkb
                    area = candidato
                    break
            except (ValueError, TypeError, KeyError):
                continue

    if area is None:
        st.caption("Selecione a área antes de consultar as O.S. ou a rede.")
        return
    area, erro_area = _validar_area_selecionada(area)
    if erro_area:
        st.warning(erro_area)
        return

    nome = bairro_area or "Área desenhada"
    area_id = hashlib.sha256(area.wkb).hexdigest()
    st.caption(f"Área selecionada: **{nome}**")
    if st.button("🔎 Consultar O.S. dentro da área", key="m8_consultar_os_area", type="primary"):
        st.session_state["m8_area_consultada"] = {"nome": nome, "wkb": area.wkb, "id": area_id}
        st.session_state.pop("m8_rede_carregada_id", None)

    consulta = st.session_state.get("m8_area_consultada")
    if not consulta or consulta["id"] != area_id:
        st.info("Clique em Consultar O.S. dentro da área para obter a listagem correspondente.")
        return

    id_farol = str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip()
    os_area = pd.DataFrame()
    if not id_farol:
        st.warning("Configure 'farol_producao_spreadsheet_id' nos Secrets do PRISMA "
                   "para consultar a aba PONTOS do Farol.")
    else:
        try:
            with st.spinner("Localizando as O.S. do Farol dentro do polígono..."):
                base_os = _ler_os_farol_para_area(id_farol)
                os_area, sem_coords = _filtrar_os_no_poligono(base_os, area)
            c1, c2, c3 = st.columns(3)
            c1.metric("O.S. dentro da área", len(os_area))
            c2.metric("Matrículas distintas", os_area["Matrícula"].astype(str).loc[
                lambda x: x.str.strip().ne("")].nunique() if "Matrícula" in os_area else 0)
            c3.metric("O.S. sem coordenadas na base", sem_coords)
            st.caption("Fonte: aba PONTOS do Módulo Farol. A listagem compreende "
                       "os registros atualmente mantidos nessa aba (janela operacional de até 8 dias, "
                       "quando a retenção automática estiver ativa), não todo o histórico do BI. "
                       "Registros sem coordenadas não podem ser classificados espacialmente.")
        except Exception as erro:
            st.error(f"Não foi possível consultar O.S. do Farol: {erro}")

    if os_area.empty:
        st.info("Nenhuma O.S. com coordenadas dentro do polígono na base disponível.")
    else:
        st.markdown("#### O.S. encontradas no polígono")
        preferidas = ("Nº da O.S", "Matrícula", "Cidade", "Bairro", "Status OS",
                      "Dt. Emissão", "Serviço Executado", "Latitude", "Longitude")
        exibidas = [col for col in preferidas if col in os_area.columns]
        st.dataframe(os_area.loc[:, exibidas], hide_index=True, use_container_width=True)
        csv = os_area.loc[:, exibidas].to_csv(index=False, sep=";").encode("utf-8-sig")
        st.download_button("⬇️ Exportar O.S. da área (CSV)", data=csv,
                           file_name="os_area_selecionada.csv", mime="text/csv",
                           key="m8_baixar_os_area")

    st.markdown("#### Rede de água da área selecionada")
    rede = None
    if not ARQUIVO_REDE_AGUA.is_file():
        st.warning("Arquivo não encontrado: dados/Rede_Agua_THE.kmz. "
                   "A listagem de O.S. continua disponível sem a rede.")
    else:
        ativo = st.session_state.get("m8_rede_carregada_id") == area_id
        col1, col2 = st.columns([2, 1])
        with col1:
            if st.button("🧭 Carregar rede apenas desta área", key="m8_carregar_rede",
                         disabled=ativo, use_container_width=True):
                st.session_state["m8_rede_carregada_id"] = area_id
                ativo = True
        with col2:
            if st.button("Ocultar rede", key="m8_ocultar_rede", disabled=not ativo,
                         use_container_width=True):
                st.session_state.pop("m8_rede_carregada_id", None)
                ativo = False
        if ativo:
            try:
                with st.spinner("Lendo a rede KMZ e recortando ao perímetro selecionado..."):
                    rede = _rede_recortada_kmz(_assinatura(ARQUIVO_REDE_AGUA), area.wkb)
                st.caption(f"Trechos de rede exibidos: {rede['trechos']:,} · "
                           f"Vértices enviados ao navegador: {rede['vertices']:,}".replace(",", "."))
                if rede["limitado"]:
                    st.warning("A rede foi limitada para preservar o desempenho. "
                               "Selecione um polígono menor para visualizar todos os trechos da área.")
                elif not rede["trechos"]:
                    st.info("O KMZ foi lido, mas nenhuma linha de rede intersecta a área escolhida. "
                            "Verifique se o arquivo contém geometrias do tipo LineString.")
            except Exception as erro:
                st.error(f"Não foi possível ler o KMZ da rede: {erro}")

    st_folium(_mapa_rede_e_os(area, rede, os_area), width=None, height=610,
              returned_objects=[], key="m8_mapa_resultado_area")
    if len(os_area) > LIMITE_MARCADORES_OS:
        st.caption(f"Para manter o mapa responsivo, foram desenhadas somente as primeiras "
                   f"{LIMITE_MARCADORES_OS} O.S. A tabela e o CSV contêm todas as O.S. localizadas.")
    st.caption("Rede recortada por interseção geográfica, sem editar o KMZ original. "
               "A presença de uma tubulação no polígono não comprova que ela abasteça cada matrícula.")


def exibir_consulta_operacional() -> None:
    """Pesquisa e mapa são gerados só depois de o usuário clicar em Pesquisar."""
    if (not verificar_autenticacao()
            or str(st.session_state.get("perfil", "")).strip().lower() not in PERFIS_CONSULTA):
        st.error("Acesso não autorizado. Entre pelo Hub Central.")
        st.stop()

    # Retorno ao Hub Central sem encerrar a sessão autenticada.
    if st.button("⬅️ Voltar ao Menu Principal", key="voltar_menu_principal_consulta_operacional"):
        st.switch_page("app.py")

    st.title("🔍 Consulta Operacional")
    st.caption("Teresina • Consultas somente leitura • Sem histórico de consultas")
    ferramenta = st.radio("Ferramenta de consulta",
                          ("Pesquisa de matrículas", "Seleção de área — Rede e O.S."),
                          horizontal=True, key="m8_ferramenta")
    if ferramenta == "Seleção de área — Rede e O.S.":
        exibir_selecao_area_rede_os()
    else:
        exibir_consulta_por_matricula()


def exibir_consulta_por_matricula() -> None:
    """Consulta V3 existente, mantida sem alterações na lógica de busca."""
    fonte = _arquivo_matriculas()
    indice = _arquivo_sqlite_publicado()
    if fonte is None and indice is None:
        st.error("Base ausente. Adicione dados/matriculas.xls, dados/matriculas.xlsx "
                 "ou dados/matriculas.sqlite a um repositório privado.")
        st.stop()
    if fonte is not None:
        st.caption(f"Base de consulta: dados/{fonte.name} • leitura somente após pesquisar")
    else:
        st.caption(f"Base de consulta: dados/{indice.name} • índice de pesquisa")

    # Descarta apenas o valor de demonstração herdado da versão anterior,
    # sem limpar a consulta atual em futuras atualizações da interface.
    if "consulta_operacional_v3_inicializou" not in st.session_state:
        st.session_state.pop("consulta_operacional_entrada", None)
        st.session_state.pop("consulta_operacional_consulta", None)
        st.session_state["consulta_operacional_v3_inicializou"] = True

    with st.form("consulta_operacional_busca"):
        pesquisa = st.text_area("Matrícula(s)", value="", placeholder="Digite uma ou várias matrículas",
                                help="Informe uma ou várias matrículas, separadas por vírgula, espaço ou quebra de linha.",
                                height=105, key="consulta_operacional_entrada")
        c1, c2 = st.columns([2, 1])
        with c1:
            raio = st.number_input("Raio de busca dos ativos e loggers (metros)",
                                   min_value=50, max_value=5000, value=750, step=50,
                                   key="consulta_operacional_raio")
        with c2:
            exibir_bairros = st.checkbox("Exibir limites dos bairros", value=True,
                                        key="consulta_operacional_bairros")
        executar = st.form_submit_button("🔎 Pesquisar matrículas", type="primary", use_container_width=True)

    if executar:
        codigos, invalidos_pesquisa = _interpretar_pesquisa(pesquisa)
        if invalidos_pesquisa:
            st.error("Valores inválidos na pesquisa: " + ", ".join(invalidos_pesquisa[:10]) + ". Use apenas números de matrícula.")
            st.session_state.pop("consulta_operacional_consulta", None)
        elif not codigos:
            st.warning("Informe ao menos uma matrícula para consultar.")
            st.session_state.pop("consulta_operacional_consulta", None)
        elif len(codigos) > 200:
            st.warning("Para manter o mapa responsivo, pesquise até 200 matrículas por consulta.")
            st.session_state.pop("consulta_operacional_consulta", None)
        else:
            # Somente o estado da consulta corrente no navegador; NÃO registra histórico.
            st.session_state["consulta_operacional_consulta"] = (codigos, int(raio), bool(exibir_bairros))

    consulta = st.session_state.get("consulta_operacional_consulta")
    if not consulta:
        st.info("Informe a(s) matrícula(s) e clique em Pesquisar para visualizar o mapa e os ativos próximos.")
        return

    matriculas, raio_consulta, mostrar_bairros = consulta
    try:
        with st.spinner("Consultando cadastro de matrículas... Na primeira pesquisa, a criação do índice pode demorar."):
            encontrados, meta = _buscar_matriculas(matriculas)
    except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as erro:
        st.error(f"Não foi possível consultar a base de matrículas: {erro}")
        return

    st.caption(f"Cadastro indexado: {int(meta.get('quantidade', 0)):,} registros".replace(",", "."))
    presentes = set(encontrados["NUM_LIGACAO"])
    ausentes = [m for m in matriculas if m not in presentes]
    validos = encontrados.loc[encontrados["_lat"].notna() & encontrados["_lon"].notna()].copy()
    invalidos = encontrados.loc[encontrados["_lat"].isna() | encontrados["_lon"].isna()].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("Matrículas localizadas", len(presentes))
    c2.metric("Com coordenadas válidas", int(validos["NUM_LIGACAO"].nunique()))
    c3.metric("Não encontradas", len(ausentes))

    if ausentes:
        st.warning("Matrícula(s) não encontrada(s): " + ", ".join(ausentes))
    if not invalidos.empty:
        st.warning("Encontradas sem coordenadas válidas: " + ", ".join(sorted(invalidos["NUM_LIGACAO"].unique())))
    duplicadas = encontrados["NUM_LIGACAO"][encontrados["NUM_LIGACAO"].duplicated(keep=False)].unique()
    if len(duplicadas):
        st.warning("Matrículas com registros duplicados na base: " + ", ".join(duplicadas))

    if not encontrados.empty:
        st.subheader("📋 Dados cadastrais das matrículas")
        st.dataframe(encontrados.loc[:, list(COLUNAS_MATRICULAS)], hide_index=True,
                     use_container_width=True)
    if validos.empty:
        st.info("Nenhuma matrícula com coordenadas geográficas válidas para exibir no mapa.")
        return

    # Independente do filtro de limites de bairros: influência sempre é calculada.
    areas = []
    caminho_areas = _caminho_influencias()
    if caminho_areas:
        try:
            areas = _ler_influencias(_assinatura(caminho_areas))
        except Exception as erro:
            st.warning(f"Não foi possível carregar áreas de influência: {erro}")
    else:
        st.warning("KMZ de influência não encontrado: adicione geodados/areas_influencia/Area_de_influencia_Teresina.kmz.")

    if areas:
        validos["_influencia"] = validos.apply(
            lambda linha: "; ".join(_identificar_influencias(float(linha["_lat"]), float(linha["_lon"]), areas))
            or "Área de influência não identificada", axis=1)
    else:
        validos["_influencia"] = "Área de influência indisponível" if caminho_areas is None or not areas else "Área de influência não identificada"

    bases: dict[str, pd.DataFrame] = {}
    for tipo, caminho in (("Poço / Ativo", ARQUIVO_POCOS), ("Logger", ARQUIVO_LOGGERS)):
        if not caminho.is_file():
            st.warning(f"{caminho.relative_to(RAIZ)} não encontrado; {tipo.lower()}(s) não serão exibidos.")
            continue
        try:
            bases[tipo] = _ler_ativos_csv(_assinatura(caminho), tipo)
        except (OSError, ValueError, pd.errors.ParserError) as erro:
            st.warning(f"Não foi possível carregar {caminho.name}: {erro}")

    proximidade, ativos_mapa = _consultar_proximidade(validos, bases, raio_consulta)
    qtd_pocos = sum(a.get("_tipo") == "Poço / Ativo" for a in ativos_mapa)
    qtd_loggers = sum(a.get("_tipo") == "Logger" for a in ativos_mapa)
    c4, c5, c6 = st.columns(3)
    c4.metric("Poços / ativos próximos", qtd_pocos)
    c5.metric("Loggers próximos", qtd_loggers)
    c6.metric("Raio de pesquisa", f"{raio_consulta} m")

    bairros = []
    if mostrar_bairros:
        arquivo_bairros = _caminho_bairros_teresina()
        if arquivo_bairros:
            try:
                bairros = _bairros_geograficos(_assinatura(arquivo_bairros))
            except Exception as erro:
                st.warning(f"Não foi possível carregar os limites dos bairros: {erro}")
        else:
            st.caption("Camada de bairros indisponível: nenhum arquivo geográfico de Teresina foi encontrado.")

    st.subheader("🗺️ Contexto geográfico das matrículas")
    st.caption("Verde: matrículas • Azul: poços / ativos • Laranja: loggers • "
               "Círculos: raio de proximidade. Proximidade não comprova abastecimento pelo ativo.")
    mapa = _mapa_consulta(validos, ativos_mapa, raio_consulta, bairros, mostrar_bairros, areas)
    st_folium(mapa, width=None, height=640,
              returned_objects=[], key="consulta_operacional_mapa")

    st.subheader("📍 Ativos e loggers próximos por matrícula")
    if proximidade.empty:
        st.info(f"Nenhum ativo ou logger encontrado dentro de {raio_consulta} m das matrículas localizadas.")
    else:
        st.dataframe(proximidade, hide_index=True, use_container_width=True)
        st.caption("As distâncias são medidas em linha reta entre coordenadas; "
                   "o mesmo equipamento pode aparecer vinculado a mais de uma matrícula.")

    st.subheader("🌐 Região de influência do abastecimento")
    if areas:
        st.dataframe(validos[["NUM_LIGACAO", "_influencia"]].rename(columns={
            "NUM_LIGACAO": "Matrícula", "_influencia": "Área de Influência"
        }), hide_index=True, use_container_width=True)
        st.caption("A associação resulta da interseção geográfica com os polígonos do KMZ. "
                   "Quando há sobreposições, todas as áreas correspondentes são apresentadas.")
    else:
        st.info("A base de áreas de influência ainda não está disponível para cruzamento geográfico.")
    st.caption("Para visualizar a rede de água e O.S. por perímetro, use Seleção de área — Rede e O.S.")


exibir_consulta_operacional()
