"""PRISMA GEO — Ambiente de Teste (protótipo funcional, projetos locais).

Não modifica o Módulo 8, o Supabase ou bases institucionais.
O sistema persiste projetos exclusivamente por download/upload .prismageo.
"""
from __future__ import annotations

import csv
import hashlib
import os
import tempfile
import threading
import unicodedata

import pandas as pd
import io
import json
import math
import re
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from auth import exigir_acesso_modulo, verificar_autenticacao

st.set_page_config(page_title="PRISMA GEO | Ambiente de Teste", page_icon="🗺️", layout="wide", initial_sidebar_state="expanded")
exigir_acesso_modulo("teste")
if not verificar_autenticacao():
    st.error("Sessão expirada. Acesse o Hub Central.")
    st.stop()

MAX_UPLOAD = 15 * 1024 * 1024
MAX_PROJECT = 24 * 1024 * 1024
MAX_FEATURES = 4000
HOME = [-42.8019, -5.0892]

# Usa a mesma base do Módulo 8; o índice de matrículas é temporário e local.
# Nenhum banco de projetos é criado e a base original permanece intacta.
PASTA_DADOS = ROOT / "dados"
COLUNAS_MATRICULAS = (
    "CATEGORIA", "NUM_LIGACAO", "BAIRRO",
    "COD_LATITUDE", "COD_LONGITUDE", "END_LIGACAO",
)
_BLOQUEIO_INDICE = threading.Lock()


def novo_projeto(nome="Meu mapa"):
    return {"format": "prismageo", "version": 1, "name": nome,
            "center": HOME[:], "zoom": 11,
            "layers": [{"id": "desenhos", "name": "Meus desenhos", "source": "manual", "visible": True, "features": []}]}


def camada_geojson(nome, features, origem="importado"):
    return {"id": "c_" + uuid.uuid4().hex[:12], "name": str(nome)[:120], "source": origem,
            "visible": True, "features": features}


def valor_coord(v, limite):
    try:
        x = float(str(v).strip().replace(",", "."))
        return x if math.isfinite(x) and abs(x) <= limite else None
    except (TypeError, ValueError):
        return None


def checar_geojson(obj):
    if not isinstance(obj, dict):
        raise ValueError("GeoJSON inválido.")
    if obj.get("type") == "FeatureCollection":
        features = obj.get("features", [])
    elif obj.get("type") == "Feature":
        features = [obj]
    else:
        raise ValueError("Esperado Feature ou FeatureCollection.")
    if not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError(f"Limite de {MAX_FEATURES} elementos por importação.")
    validos = []
    tipos = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
    for item in features:
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise ValueError("A coleção contém um elemento inválido.")
        g = item.get("geometry")
        if not isinstance(g, dict) or g.get("type") not in tipos or "coordinates" not in g:
            raise ValueError("Geometria não suportada.")
        def validar_coords(c, depth=0):
            if depth > 8:
                raise ValueError("Geometria excessivamente profunda.")
            if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
                if not all(math.isfinite(v) for v in c[:2]) or not (-180 <= c[0] <= 180 and -90 <= c[1] <= 90):
                    raise ValueError("Coordenada fora do intervalo geográfico WGS84.")
                return
            if not isinstance(c, (list, tuple)) or not c:
                raise ValueError("Coordenadas inválidas.")
            for v in c:
                validar_coords(v, depth + 1)
        validar_coords(g["coordinates"])
        prop = item.get("properties") or {}
        if not isinstance(prop, dict):
            prop = {}
        prop = {str(k)[:70]: str(v)[:500] for k,v in list(prop.items())[:30]}
        validos.append({"type": "Feature", "id": str(item.get("id") or uuid.uuid4().hex),
                        "geometry": g, "properties": prop})
    return validos


def ler_kml(conteudo):
    if len(conteudo) > MAX_UPLOAD or b"<!DOCTYPE" in conteudo.upper() or b"<!ENTITY" in conteudo.upper():
        raise ValueError("KML muito grande ou com declarações XML não permitidas.")
    root = ET.fromstring(conteudo)
    elementos = []
    for placemark in root.iter():
        if placemark.tag.split("}")[-1] != "Placemark":
            continue
        nome = next((e.text or "" for e in placemark.iter() if e.tag.split("}")[-1] == "name"), "Sem nome")
        for item in placemark.iter():
            tipo = item.tag.split("}")[-1]
            if tipo not in ("Point", "LineString", "Polygon"):
                continue
            if tipo == "Polygon":
                outer = next((e for e in item.iter() if e.tag.split("}")[-1] == "outerBoundaryIs"), None)
                coords_nodes = [e for e in outer.iter() if e.tag.split("}")[-1] == "coordinates"] if outer is not None else []
            else:
                coords_nodes = [e for e in item.iter() if e.tag.split("}")[-1] == "coordinates"]
            if not coords_nodes or not coords_nodes[0].text:
                continue
            coords = []
            for raw in coords_nodes[0].text.split():
                v = raw.split(",")
                if len(v) >= 2:
                    lon,lat = valor_coord(v[0],180),valor_coord(v[1],90)
                    if lon is not None and lat is not None:
                        coords.append([lon,lat])
            if tipo == "Point" and coords:
                geometry={"type":"Point","coordinates":coords[0]}
            elif tipo == "LineString" and len(coords)>=2:
                geometry={"type":"LineString","coordinates":coords}
            elif tipo == "Polygon" and len(coords)>=3:
                if coords[0] != coords[-1]: coords.append(coords[0])
                geometry={"type":"Polygon","coordinates":[coords]}
            else:
                continue
            elementos.append({"type":"Feature", "properties":{"nome":nome[:200]}, "geometry":geometry})
            if len(elementos)>MAX_FEATURES:
                raise ValueError(f"Limite de {MAX_FEATURES} elementos por importação.")
    return checar_geojson({"type":"FeatureCollection","features":elementos})


def ler_tabular(nome, dados):
    if nome.lower().endswith(".csv"):
        texto = dados.decode("utf-8-sig")
        leitor = csv.DictReader(io.StringIO(texto), delimiter=";" if texto[:500].count(";") > texto[:500].count(",") else ",")
        rows = leitor
    else:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
        ws = wb.active
        iterator = ws.iter_rows(values_only=True)
        hdr = [str(v or "") for v in next(iterator, [])]
        rows = (dict(zip(hdr, values)) for values in iterator)
    features=[]
    try:
        for row in rows:
            norm = {re.sub(r"[^a-z]", "", str(k).lower()):v for k,v in row.items()}
            lat = next((norm[k] for k in ("latitude","codlatitude","lat") if k in norm),None)
            lon = next((norm[k] for k in ("longitude","codlongitude","lon","long") if k in norm),None)
            y,x=valor_coord(lat,90),valor_coord(lon,180)
            if x is None or y is None or x==0 or y==0:
                continue
            props={str(k)[:70]:str(v or "")[:300] for k,v in list(row.items())[:20]}
            features.append({"type":"Feature","geometry":{"type":"Point","coordinates":[x,y]},"properties":props})
            if len(features)>MAX_FEATURES:
                raise ValueError(f"Limite de {MAX_FEATURES} pontos por importação. Filtre a planilha.")
    finally:
        if nome.lower().endswith(".xlsx"):
            wb.close()
    return checar_geojson({"type":"FeatureCollection","features":features})


def importar_camadas(nome, dados):
    if len(dados)>MAX_UPLOAD:
        raise ValueError("Arquivo excede 15 MB.")
    ext = Path(nome).suffix.lower()
    if ext == ".geojson" or ext == ".json":
        features=checar_geojson(json.loads(dados.decode("utf-8-sig")))
    elif ext == ".kml":
        features=ler_kml(dados)
    elif ext == ".kmz":
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            kmls=[i for i in z.infolist() if i.filename.lower().endswith(".kml") and not i.is_dir()]
            if not kmls:
                raise ValueError("KMZ não contém um arquivo KML.")
            if kmls[0].file_size>MAX_UPLOAD:
                raise ValueError("KML descompactado excede 15 MB.")
            features=ler_kml(z.read(kmls[0]))
    elif ext in (".csv", ".xlsx"):
        features=ler_tabular(nome,dados)
    else:
        raise ValueError("Formato de arquivo não suportado.")
    if not features:
        raise ValueError("Nenhum elemento geográfico válido foi encontrado.")
    return camada_geojson(Path(nome).stem, features)


def pacote_projeto(projeto):
    conteudo=json.dumps(projeto,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
    if len(conteudo)>MAX_PROJECT:
        raise ValueError("Projeto muito grande; reduza camadas importadas.")
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr("projeto.json",conteudo)
    return b.getvalue()


def abrir_projeto(dados):
    if len(dados)>MAX_PROJECT:
        raise ValueError("Projeto excede o tamanho máximo permitido.")
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        if set(z.namelist())!={"projeto.json"} or z.getinfo("projeto.json").file_size>MAX_PROJECT:
            raise ValueError("Pacote de projeto inválido.")
        projeto=json.loads(z.read("projeto.json"))
    if not isinstance(projeto,dict) or projeto.get("format")!="prismageo" or projeto.get("version")!=1:
        raise ValueError("Arquivo não é um projeto PRISMA GEO V1.")
    layers=projeto.get("layers",[])
    if not isinstance(layers,list) or len(layers)>60:
        raise ValueError("Quantidade inválida de camadas no projeto.")
    for layer in layers:
        if not isinstance(layer,dict) or layer.get("source") not in ("manual","importado"):
            raise ValueError("Camada de projeto inválida.")
        layer["features"]=checar_geojson({"type":"FeatureCollection","features":layer.get("features",[])})
        layer["id"]=str(layer.get("id",uuid.uuid4().hex))[:80]
        layer["name"]=str(layer.get("name","Camada"))[:100]
        layer["visible"]=bool(layer.get("visible",True))
    projeto["name"]=str(projeto.get("name","Meu mapa"))[:120]
    center=projeto.get("center",HOME)
    if not isinstance(center,list) or len(center)!=2 or valor_coord(center[0],180) is None or valor_coord(center[1],90) is None:
        projeto["center"]=HOME[:]
    projeto["zoom"]=max(2,min(20,float(projeto.get("zoom",11))))
    return projeto


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



def matriculas_por_codigo(codigos):
    """Pesquisa pontual no mesmo cadastro do Módulo 8, sem varrer a planilha em cada busca.

    A primeira consulta pode criar índice local descartável em /tmp; as seguintes
    utilizam o índice já criado. A base XLS/XLSX nunca é alterada.
    """
    if not codigos:
        return []
    encontrados, _meta = _buscar_matriculas(codigos)
    features = []
    for _, linha in encontrados.iterrows():
        lat = linha["_lat"]
        lon = linha["_lon"]
        if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": {
                "Matrícula": str(linha["NUM_LIGACAO"]),
                "Bairro": str(linha["BAIRRO"]),
                "Endereço": str(linha["END_LIGACAO"]),
                "Categoria": str(linha["CATEGORIA"]),
            },
        })
    return checar_geojson({"type": "FeatureCollection", "features": features})


@st.cache_data(show_spinner=False, max_entries=6)
def ativos_sob_demanda(caminho: str, tamanho: int, mtime: int, tipo: str):
    """Consulta CSV oficial somente ao habilitar a camada; sem alterar a base.

    Os parâmetros tamanho e mtime invalidam o cache quando o CSV é atualizado.
    Coordenadas com vírgula decimal são aceitas pela função valor_coord.
    """
    _ = (tamanho, mtime)
    features = []
    with open(caminho, "r", encoding="utf-8-sig", newline="") as arquivo:
        amostra = arquivo.read(4096)
        arquivo.seek(0)
        delimitador = ";" if amostra.count(";") > amostra.count(",") else ","
        linhas = csv.DictReader(arquivo, delimiter=delimitador)
        for linha in linhas:
            colunas = {str(chave).strip().upper(): valor for chave, valor in linha.items()}
            lat = valor_coord(colunas.get("LATITUDE"), 90)
            lon = valor_coord(colunas.get("LONGITUDE"), 180)
            if lat is None or lon is None or lat == 0 or lon == 0:
                continue

            identificacao = str(colunas.get("IDENTIFICACAO_ATIVO") or "").strip()
            if tipo == "Logger":
                nome = identificacao or str(colunas.get("ID_LOGGER") or "").strip()
                if not nome:
                    nome = str(colunas.get("ENDERECO") or tipo).strip()
            else:
                nome = str(colunas.get("NOME_POCO") or colunas.get("NOME_POCOS") or "").strip()
                if not nome:
                    nome = identificacao or str(colunas.get("ENDERECO") or tipo).strip()

            chave = f"{tipo}:{lon},{lat}:{identificacao or nome}"
            features.append({
                "type": "Feature",
                "id": uuid.uuid5(uuid.NAMESPACE_URL, chave).hex,
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "Tipo": tipo,
                    "Nome": nome[:160],
                    "Código": identificacao[:120],
                },
            })
            if len(features) > MAX_FEATURES:
                raise ValueError(
                    f"A camada {tipo} ultrapassa {MAX_FEATURES} elementos. "
                    "Utilize um recorte espacial para manter o mapa leve."
                )
    return features


# Componente V2: o mapa não recria milhares de marcadores em Folium a cada clique.
GEO_HTML='''<div class="pg-frame"><div class="pg-tools"><button data-mode="pan">✋ Navegar</button><button data-mode="point">📍 Ponto</button><button data-mode="line">〰 Linha</button><button data-mode="polygon">⬡ Polígono</button><button data-act="finish">✓ Concluir</button><button data-act="cancel">✕ Cancelar</button></div><div class="pg-map"></div><div class="pg-status">Iniciando mapa…</div></div>'''
GEO_CSS='''
.pg-frame{position:relative;width:100%;height:100%;font:13px system-ui,sans-serif;overflow:hidden;border-radius:12px;border:1px solid #dce3ed}
.pg-map{height:100%;width:100%;min-height:480px}
.pg-tools{position:absolute;z-index:15;top:10px;left:10px;display:flex;flex-wrap:wrap;gap:5px;background:rgba(255,255,255,.96);padding:7px;border-radius:9px;box-shadow:0 3px 15px #0002;max-width:90%}
.pg-tools button{border:1px solid #cbd5e1;border-radius:6px;background:#fff;color:#152c42;padding:6px 9px;cursor:pointer;font-size:12px}
.pg-tools button.pg-active{background:#164e77;color:white}
.pg-status{position:absolute;bottom:6px;left:10px;z-index:12;padding:5px 9px;background:#fff;color:#203348;border-radius:6px;max-width:80%;font-size:11px}
'''
GEO_JS=r'''
export default function(component) {
 const {parentElement, data, setTriggerValue} = component;
 const mapNode=parentElement.querySelector('.pg-map');
 const status=parentElement.querySelector('.pg-status');
 const root=parentElement;
 const cssLink=document.createElement('link'); cssLink.rel='stylesheet';
 cssLink.href='https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css';
 root.appendChild(cssLink);
 let map=null, alive=true, mode='pan', draft=[], selected=null;
 const modes=[...root.querySelectorAll('[data-mode]')];
 const setMode=(v)=>{mode=v;draft=[];modes.forEach(b=>b.classList.toggle('pg-active',b.dataset.mode===v)); if(map)refreshDraft(); status.textContent= v==='pan'?'Mapa pronto. Clique em um elemento para selecioná-lo.': v==='point'?'Clique para posicionar um ponto.':'Clique nos vértices e depois em Concluir.';};
 const sets=(id,geodata)=>{const s=map.getSource(id); if(s)s.setData(geodata);};
 const fc=(features)=>({type:'FeatureCollection',features:features||[]});
 const refreshDraft=()=>{
    if(!map || !map.getSource('pg-draft'))return;
    const feats=[];
    if(draft.length>1)feats.push({type:'Feature',geometry:{type:'LineString',coordinates:draft},properties:{}});
    for(const c of draft)feats.push({type:'Feature',geometry:{type:'Point',coordinates:c},properties:{}});
    sets('pg-draft',fc(feats));
 };
 const add=(geometry)=>{
   setTriggerValue('created',{type:'Feature',id:'f_'+crypto.randomUUID(),geometry,properties:{nome:'Novo elemento'}});
   draft=[]; setMode('pan');
 };
 const finish=()=>{
  if(mode==='line' && draft.length>=2)add({type:'LineString',coordinates:draft});
  else if(mode==='polygon' && draft.length>=3)add({type:'Polygon',coordinates:[[...draft,draft[0]]]});
  else status.textContent='Marque mais vértices antes de concluir.';
 };
 modes.forEach(b=>b.addEventListener('click',()=>setMode(b.dataset.mode)));
 root.querySelector('[data-act="finish"]').onclick=finish;
 root.querySelector('[data-act="cancel"]').onclick=()=>setMode('pan');
 let ro=null;
 import('https://esm.sh/maplibre-gl@5.6.2').then(module=>{
  if(!alive)return;
  const gl=module.default||module;
  map=new gl.Map({container:mapNode,center:data.center||[-42.8019,-5.0892],zoom:data.zoom||11,
   style:{version:8,sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'osm',type:'raster',source:'osm'}]},
   attributionControl:true});
  map.addControl(new gl.NavigationControl(),'bottom-right');
  map.on('load',()=>{
   if(!alive)return;
   const project=data.project||[]; const lookup=data.lookup||[];
   const all=[...project,...lookup];
   const points=all.filter(f=>f.geometry?.type==='Point'||f.geometry?.type==='MultiPoint');
   const lines=all.filter(f=>f.geometry?.type==='LineString'||f.geometry?.type==='MultiLineString');
   const polygons=all.filter(f=>f.geometry?.type==='Polygon'||f.geometry?.type==='MultiPolygon');
   map.addSource('pg-points',{type:'geojson',data:fc(points),cluster:points.length>150,clusterRadius:45,clusterMaxZoom:13});
   map.addSource('pg-lines',{type:'geojson',data:fc(lines)});
   map.addSource('pg-poly',{type:'geojson',data:fc(polygons)});
   map.addSource('pg-draft',{type:'geojson',data:fc([])});
   map.addLayer({id:'pg-fill',type:'fill',source:'pg-poly',paint:{'fill-color':'#18b3b1','fill-opacity':0.2}});
   map.addLayer({id:'pg-outline',type:'line',source:'pg-poly',paint:{'line-color':'#00889b','line-width':3}});
   map.addLayer({id:'pg-lines-layer',type:'line',source:'pg-lines',paint:{'line-color':'#2563eb','line-width':3}});
   map.addLayer({id:'pg-clusters',type:'circle',source:'pg-points',filter:['has','point_count'],paint:{'circle-color':'#164e77','circle-radius':19}});
   map.addLayer({id:'pg-cluster-label',type:'symbol',source:'pg-points',filter:['has','point_count'],layout:{'text-field':['get','point_count_abbreviated'],'text-size':12},paint:{'text-color':'#ffffff'}});
   map.addLayer({id:'pg-points-layer',type:'circle',source:'pg-points',filter:['!',['has','point_count']],paint:{'circle-color':'#16a34a','circle-stroke-color':'#fff','circle-stroke-width':1.5,'circle-radius':6}});
   map.addLayer({id:'pg-draft-line',type:'line',source:'pg-draft',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#e67e22','line-width':2,'line-dasharray':[2,2]}});
   map.addLayer({id:'pg-draft-point',type:'circle',source:'pg-draft',filter:['==',['geometry-type'],'Point'],paint:{'circle-color':'#e67e22','circle-radius':5}});
   map.on('click',(e)=>{
    if(mode!=='pan'){
     const p=[Number(e.lngLat.lng.toFixed(7)),Number(e.lngLat.lat.toFixed(7))];
     if(mode==='point')add({type:'Point',coordinates:p}); else {draft.push(p);refreshDraft();}
     return;
    }
    const hits=map.queryRenderedFeatures(e.point,{layers:['pg-points-layer','pg-lines-layer','pg-fill']});
    if(hits.length){const f=hits[0];setTriggerValue('selected',{id:String(f.id||f.properties?.pg_id||''),properties:f.properties||{}});}
   });
   if(data.focus?.length===2) map.flyTo({center:data.focus,zoom:15});
   setMode('pan');
  });
  map.on('error',(err)=>{ if(alive)status.textContent='Falha no mapa ou nos mosaicos cartográficos. Verifique sua conexão.'; });
  ro=new ResizeObserver(()=>map?.resize());ro.observe(mapNode);
 }).catch(err=>{if(alive)status.textContent='Não foi possível carregar MapLibre. Verifique a conexão com o servidor de componentes.';});
 return ()=>{alive=false;if(ro)ro.disconnect();if(map)map.remove();};
}
'''

if "prisma_geo_projeto" not in st.session_state:
    st.session_state.prisma_geo_projeto=novo_projeto()
if "prisma_geo_consulta" not in st.session_state:
    st.session_state.prisma_geo_consulta=[]
if "prisma_geo_foco" not in st.session_state:
    st.session_state.prisma_geo_foco=None
if "prisma_geo_selecionado" not in st.session_state:
    st.session_state.prisma_geo_selecionado=None
if "prisma_geo_revisao" not in st.session_state:
    st.session_state.prisma_geo_revisao=0

p=st.session_state.prisma_geo_projeto
st.title("🗺️ PRISMA GEO")
st.caption("Ambiente de Teste · editor cartográfico leve · projetos individuais salvos em arquivo · nenhuma gravação nas bases oficiais")
with st.sidebar:
    if st.button("⬅ Voltar ao PRISMA", use_container_width=True):
        st.switch_page("app.py")
    st.subheader("Projeto")
    nome=st.text_input("Nome do mapa",value=p["name"],key="pg_nome")
    if nome.strip()!=p["name"]:
        p["name"]=nome.strip()[:120]
    if st.button("➕ Novo mapa",use_container_width=True):
        st.session_state.prisma_geo_projeto=novo_projeto()
        st.session_state.prisma_geo_consulta=[]
        st.session_state.prisma_geo_foco=None
        st.session_state.prisma_geo_revisao+=1
        st.session_state.pop("pg_nome", None)
        st.session_state.pop("pg_visible_desenhos", None)
        st.rerun()
    abrir=st.file_uploader("Abrir projeto (.prismageo)",type=["prismageo"],key="pg_abertura")
    if st.button("📂 Abrir arquivo",disabled=abrir is None,use_container_width=True):
        try:
            st.session_state.prisma_geo_projeto=abrir_projeto(abrir.getvalue())
            st.session_state.prisma_geo_consulta=[]
            st.session_state.prisma_geo_foco=None
            st.session_state.prisma_geo_revisao+=1
            st.session_state.pop("pg_nome", None)
            for kk in list(st.session_state):
                if kk.startswith("pg_visible_"):
                    st.session_state.pop(kk, None)
            st.success("Projeto aberto.")
            st.rerun()
        except (ValueError,KeyError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
            st.error(f"Falha ao abrir: {exc}")
    try:
        salvar=pacote_projeto(p)
        nome_arquivo=re.sub(r"[^a-zA-Z0-9_-]+","_",p["name"]).strip("_") or "meu_mapa"
        st.download_button("💾 Salvar projeto no computador",data=salvar,
                           file_name=f"{nome_arquivo}.prismageo",mime="application/zip",use_container_width=True)
    except ValueError as exc:
        st.warning(str(exc))
    st.caption("Salve o arquivo antes de sair: projetos não são gravados em banco de dados.")
    st.divider()
    st.subheader("Camadas de trabalho")
    for layer in p["layers"]:
        a,b=st.columns([.23,.77])
        with a:
            layer["visible"]=st.checkbox("Visível",value=bool(layer["visible"]),key="pg_visible_"+layer["id"],label_visibility="collapsed")
        with b:
            st.caption(f'{layer["name"]} ({len(layer["features"])} elementos)')
    adicionada=st.file_uploader("Importar camada",type=["geojson","json","kml","kmz","csv","xlsx"],key="pg_importar")
    if st.button("➕ Adicionar camada",use_container_width=True,disabled=adicionada is None):
        try:
            layer=importar_camadas(adicionada.name,adicionada.getvalue())
            p["layers"].append(layer)
            st.session_state.prisma_geo_revisao+=1
            st.success(f"Camada {layer['name']} adicionada.")
            st.rerun()
        except (ValueError,zipfile.BadZipFile,ET.ParseError,UnicodeDecodeError,json.JSONDecodeError) as exc:
            st.error(f"Não foi possível importar: {exc}")
    st.caption("Importações limitadas a 15 MB e 4.000 elementos por camada para proteger o carregamento.")
    st.divider()
    st.subheader("Camadas oficiais")
    st.caption("As bases oficiais não são carregadas automaticamente.")
    mostrar_pocos = st.checkbox("Mostrar poços e ativos", value=False, key="pg_pocos")
    mostrar_loggers = st.checkbox("Mostrar loggers", value=False, key="pg_loggers")
    st.caption("A pesquisa usa a base XLS/XLSX do Módulo 8. Um índice temporário é criado somente na primeira consulta, quando necessário.")

with st.expander("🔎 Consulta rápida por matrícula",expanded=False):
    busca=st.text_input("Matrículas (separadas por vírgula, espaço ou ponto e vírgula)",key="pg_matriculas",placeholder="Digite as matrículas")
    if st.button("Pesquisar",key="pg_pesquisar"):
        codigos=list(dict.fromkeys(re.findall(r"\b\d{6,15}\b",busca)))[:100]
        if not codigos:
            st.warning("Informe matrículas numéricas válidas.")
        else:
            try:
                with st.spinner("Consultando matrículas (a primeira pesquisa pode preparar o índice local)..."):
                    features=matriculas_por_codigo(codigos)
                st.session_state.prisma_geo_consulta=features
                st.session_state.prisma_geo_foco=features[0]["geometry"]["coordinates"] if features else None
                st.session_state.prisma_geo_revisao+=1
                st.success(f"{len(features)} registro(s) localizado(s).")
            except (sqlite3.Error,FileNotFoundError,ValueError,RuntimeError,ImportError,OSError) as exc:
                st.warning(str(exc))
                st.info("Confirme que dados/matriculas.xls ou dados/matriculas.xlsx está disponível. A consulta do Módulo 8 original continua independente.")
    st.caption("Pesquisar não altera o banco nem adiciona automaticamente os resultados ao projeto.")

left,right=st.columns([4.8,1.2],gap="medium")
with left:
    st.info("Ferramentas: Navegar · Ponto · Linha · Polígono. Para linhas e polígonos, clique nos vértices e em Concluir.")
    features=[feature for layer in p["layers"] if layer.get("visible") for feature in layer["features"]]
    for habilitada, arquivo, tipo in ((mostrar_pocos, "pocos.csv", "Poço / Ativo"), (mostrar_loggers, "loggers.csv", "Logger")):
        if habilitada:
            path = ROOT / "dados" / arquivo
            if path.is_file():
                info = path.stat()
                try:
                    features.extend(ativos_sob_demanda(str(path), info.st_size, info.st_mtime_ns, tipo))
                except (ValueError, UnicodeDecodeError, OSError) as exc:
                    st.warning(f"Camada {tipo}: {exc}")
            else:
                st.warning(f"Base oficial não localizada: dados/{arquivo}")
    if len(features)>MAX_FEATURES*10:
        st.error("Mapa muito grande. Oculte camadas ou reduza os arquivos importados.")
        st.stop()
    try:
        comp=st.components.v2.component("prisma_geo_editor_v1",html=GEO_HTML,css=GEO_CSS,js=GEO_JS)
        response=comp(data={"project":features,"lookup":st.session_state.prisma_geo_consulta,
                             "focus":st.session_state.prisma_geo_foco,
                             "center":p["center"],"zoom":p["zoom"]},
                       key="prisma_geo_mapa_componente",width="stretch",height=600,
                       on_created_change=lambda:None,on_selected_change=lambda:None)
        if response.created:
            item=checar_geojson({"type":"FeatureCollection","features":[response.created]})[0]
            target=next((l for l in p["layers"] if l["id"]=="desenhos"),None)
            if not target:
                target=camada_geojson("Meus desenhos",[],"manual")
                p["layers"].insert(0,target)
            target["features"].append(item)
            st.toast("Elemento adicionado. Salve seu projeto para conservar o trabalho.")
            st.rerun()
        if response.selected:
            st.session_state.prisma_geo_selecionado=response.selected
    except AttributeError:
        st.error("Este protótipo exige Streamlit com suporte a Components V2.")
    st.caption("Mapa-base: OpenStreetMap (somente para teste). O editor não grava dados oficiais.")
with right:
    st.subheader("Elementos")
    total=sum(len(l["features"]) for l in p["layers"])
    st.metric("No projeto",total)
    st.metric("Na consulta",len(st.session_state.prisma_geo_consulta))
    st.caption("Somente camadas ativas são enviadas ao mapa.")
    selecionado=st.session_state.prisma_geo_selecionado
    if selecionado:
        st.markdown("**Seleção no mapa**")
        for k,v in list((selecionado.get("properties") or {}).items())[:8]:
            st.caption(f"{k}: {v}")
    st.divider()
    st.markdown("**Gerenciamento**")
    lista=[(l["id"],l["name"]) for l in p["layers"] if l["source"]!="manual"]
    if lista:
        alvo=st.selectbox("Remover camada importada",[v[0] for v in lista],format_func=lambda x:dict(lista)[x])
        if st.button("Remover camada",key="pg_excluir_camada"):
            p["layers"]=[l for l in p["layers"] if l["id"]!=alvo]
            st.session_state.prisma_geo_revisao+=1
            st.rerun()
    if st.button("Limpar desenhos",use_container_width=True):
        for layer in p["layers"]:
            if layer["source"]=="manual":layer["features"]=[]
        st.session_state.prisma_geo_revisao+=1
        st.rerun()

st.divider()
st.caption("PRISMA GEO · protótipo inicial do Ambiente de Teste. Recursos avançados de edição, indexação espacial e análises serão integrados após validar o motor cartográfico e o desempenho.")
