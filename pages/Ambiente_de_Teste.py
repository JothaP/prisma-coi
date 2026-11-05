"""PRISMA GEO — Ambiente de Teste (protótipo funcional, projetos locais).

Não modifica o Módulo 8, o Supabase ou bases institucionais.
O sistema persiste projetos exclusivamente por download/upload .prismageo.
"""
from __future__ import annotations

import csv
import base64
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
            # Referências e visibilidade apenas; nenhuma cópia das bases oficiais.
            "official": {"pocos": False, "loggers": False, "rede": False,
                         "topografia": False, "topo_file": ""},
            "network_area": None,  # Polígono selecionado (somente coordenadas)
            "layers": [{"id": "desenhos", "name": "Meus desenhos", "source": "manual", "visible": True, "features": []}]}


def camada_geojson(nome, features, origem="importado"):
    return {"id": "c_" + uuid.uuid4().hex[:12], "name": str(nome)[:120], "source": origem,
            "visible": True, "features": features}


# ============================================================
# HISTÓRICO LOCAL DE EDIÇÃO — comandos reversíveis, sem duplicar projetos
# ============================================================
PG_LIMITE_HISTORICO = 30


def pg_registrar(operacao):
    """Registra só o objeto editado, evitando copiar todas as camadas."""
    st.session_state.setdefault("pg_desfazer", []).append(operacao)
    st.session_state["pg_desfazer"] = st.session_state["pg_desfazer"][-PG_LIMITE_HISTORICO:]
    st.session_state["pg_refazer"] = []


def pg_limpar_historico():
    st.session_state["pg_desfazer"] = []
    st.session_state["pg_refazer"] = []
    st.session_state.pop("pg_nome_elemento", None)
    st.session_state.prisma_geo_selecionado = None


def pg_encontrar_elemento(projeto, identificador):
    for camada in projeto.get("layers", []):
        for elemento in camada.get("features", []):
            if str(elemento.get("id", "")) == str(identificador):
                return camada, elemento
    return None, None


def pg_aplicar_operacao(projeto, operacao, desfazer):
    """Aplica inversão/retorno de uma operação registrada pelo usuário."""
    tipo = operacao["tipo"]
    if tipo == "criar_elemento":
        camada = next((c for c in projeto["layers"] if c["id"] == operacao["camada"]), None)
        if camada is None:
            return
        fid = str(operacao["elemento"]["id"])
        if desfazer:
            camada["features"] = [f for f in camada["features"] if str(f.get("id")) != fid]
        elif not any(str(f.get("id")) == fid for f in camada["features"]):
            camada["features"].insert(min(operacao["posicao"], len(camada["features"])), operacao["elemento"])
    elif tipo == "renomear_elemento":
        _, elemento = pg_encontrar_elemento(projeto, operacao["id"])
        if elemento is not None:
            elemento.setdefault("properties", {})["nome"] = operacao["antes"] if desfazer else operacao["depois"]
    elif tipo in ("adicionar_camada", "remover_camada"):
        incluir = (tipo == "adicionar_camada" and not desfazer) or (tipo == "remover_camada" and desfazer)
        lid = operacao["camada"]["id"]
        if incluir and not any(c["id"] == lid for c in projeto["layers"]):
            projeto["layers"].insert(min(operacao["posicao"], len(projeto["layers"])), operacao["camada"])
        elif not incluir:
            projeto["layers"] = [c for c in projeto["layers"] if c["id"] != lid]
    elif tipo == "limpar_desenhos":
        for camada in projeto["layers"]:
            if camada["source"] == "manual":
                camada["features"] = (operacao["anteriores"].get(camada["id"], []) if desfazer else [])
    elif tipo == "selecionar_rede":
        projeto["network_area"] = operacao["antes"] if desfazer else operacao["depois"]
    else:
        raise ValueError("Operação não reconhecida no histórico.")


def pg_historico(modo):
    origem, destino = ("pg_desfazer", "pg_refazer") if modo == "desfazer" else ("pg_refazer", "pg_desfazer")
    if not st.session_state.get(origem):
        return False
    comando = st.session_state[origem].pop()
    pg_aplicar_operacao(st.session_state.prisma_geo_projeto, comando, modo == "desfazer")
    st.session_state.setdefault(destino, []).append(comando)
    st.session_state.prisma_geo_selecionado = None
    st.session_state.pop("pg_nome_elemento", None)
    st.session_state.prisma_geo_foco = None
    return True


def pg_evento_novo(tipo, mensagem):
    """Protege contra reprocessar triggers de um componente após st.rerun."""
    if not isinstance(mensagem, dict):
        return False
    token = str(mensagem.get("event") or mensagem.get("id") or "")
    if not token:
        return False
    assinatura = tipo + ":" + token
    historico = st.session_state.setdefault("pg_eventos_processados", [])
    if assinatura in historico:
        return False
    historico.append(assinatura)
    st.session_state["pg_eventos_processados"] = historico[-100:]
    return True


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
    # Compatível com projetos V1 anteriores: official não é obrigatório.
    estado_oficial = projeto.get("official") if isinstance(projeto.get("official"), dict) else {}
    projeto["official"] = {"pocos": bool(estado_oficial.get("pocos", False)),
                           "loggers": bool(estado_oficial.get("loggers", False)),
                           "rede": bool(estado_oficial.get("rede", False)),
                           "topografia": bool(estado_oficial.get("topografia", False)),
                           "topo_file": str(estado_oficial.get("topo_file", ""))[:300]}
    center=projeto.get("center",HOME)
    if not isinstance(center,list) or len(center)!=2 or valor_coord(center[0],180) is None or valor_coord(center[1],90) is None:
        projeto["center"]=HOME[:]
    projeto["zoom"]=max(2,min(20,float(projeto.get("zoom",11))))
    area_salva = projeto.get("network_area")
    if area_salva is not None:
        try:
            _validar_area_rede(area_salva)
        except (ValueError, TypeError):
            projeto["network_area"] = None
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
                "pg_tipo": "Matrícula",
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
                    "pg_tipo": tipo,
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



# ============================================================
# PRISMA GEO — REDE DE ABASTECIMENTO (KMZ) E TOPOGRAFIA (GeoTIFF)
# Camadas estritamente sob demanda. Nenhum dado oficial é modificado.
# O SQLite abaixo é um ÍNDICE TÉCNICO DESCARTÁVEL em /tmp, não é
# armazenamento de projetos.
# ============================================================

_BLOQUEIO_REDE = threading.Lock()
REDE_MAX_TRECHOS_MAPA = 5500
REDE_MAX_VERTICES_MAPA = 90000
REDE_MAX_COORDENADAS_INDICE = 4_000_000
REDE_MAX_TRECHOS_INDICE = 500_000
TOPO_MAX_PIXELS = 700


def _pasta_topografias() -> list[Path]:
    """Descobre DEMs em dados/topografias; aceita também a pasta antiga topografia."""
    encontrados = []
    for pasta in (PASTA_DADOS / "topografias", PASTA_DADOS / "topografia"):
        if pasta.is_dir():
            encontrados.extend(p for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in (".tif", ".tiff"))
    return sorted(set(encontrados), key=lambda p: str(p).lower())


@st.cache_data(show_spinner=False, max_entries=2)
def _topografia_previa(caminho: str, tamanho: int, mtime_ns: int):
    """Produz uma imagem sombreada de até 700 px; nunca envia o DEM bruto ao navegador.

    Reprojeta para EPSG:4326 para encaixar a imagem no MapLibre; usa rasterio
    (já presente nas dependências do PRISMA). A imagem é apenas visualização.
    """
    _ = (tamanho, mtime_ns)
    import numpy as np
    from PIL import Image
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(caminho) as src:
        if src.crs is None:
            raise ValueError("GeoTIFF sem sistema de referência (CRS). Corrija a fonte antes de exibir.")
        with WarpedVRT(src, crs="EPSG:4326", resampling=Resampling.bilinear) as vrt:
            escala = min(1.0, TOPO_MAX_PIXELS / max(vrt.width, vrt.height))
            w = max(2, int(vrt.width * escala))
            h = max(2, int(vrt.height * escala))
            dados = vrt.read(1, out_shape=(h, w), masked=True, resampling=Resampling.bilinear)
            limite = vrt.bounds
    valores = dados.filled(np.nan).astype("float32", copy=False)
    validos = np.isfinite(valores) & (~np.ma.getmaskarray(dados))
    validos &= (valores > -1000) & (valores < 10000)
    if not np.any(validos):
        raise ValueError("O arquivo topográfico não possui elevações válidas.")
    elev = valores[validos]
    baixo, alto = np.percentile(elev, [2, 98])
    if alto - baixo < 1e-4:
        alto = baixo + 1.0
    z = np.clip((valores - baixo) / (alto - baixo), 0, 1)
    z = np.where(validos, z, 0.5)
    # Hillshade visual leve; esta prévia não substitui um MDE para cálculos.
    gy, gx = np.gradient(z)
    gradiente = np.sqrt(gx * gx + gy * gy)
    luz = np.clip(0.85 - gx * 5.0 - gy * 5.0, 0.35, 1.0)
    # Paleta discreta, para não esconder marcadores nem rede.
    r = np.clip(176 + z * 42, 0, 255) * luz
    g = np.clip(194 - z * 35, 0, 255) * luz
    b = np.clip(156 - z * 65, 0, 255) * luz
    a = np.where(validos, 205, 0).astype("uint8")
    rgba = np.stack([r, g, b, a], axis=-1).astype("uint8")
    img = Image.fromarray(rgba, "RGBA")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return {
        "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "coordinates": [
            [float(limite.left), float(limite.top)],
            [float(limite.right), float(limite.top)],
            [float(limite.right), float(limite.bottom)],
            [float(limite.left), float(limite.bottom)],
        ],
        "size": (w, h),
        "elevation_range": (round(float(np.min(elev)), 1), round(float(np.max(elev)), 1)),
    }


def _local_tag(elemento) -> str:
    return elemento.tag.rsplit("}", 1)[-1] if isinstance(elemento.tag, str) else ""


def _kml_linha(coordenadas: str) -> list[list[float]]:
    """Converte coordenadas KML lon,lat[,alt], descartando altitude para o mapa 2D."""
    saida = []
    for bloco in (coordenadas or "").split():
        partes = bloco.split(",")
        if len(partes) < 2:
            continue
        lon, lat = valor_coord(partes[0], 180), valor_coord(partes[1], 90)
        if lon is not None and lat is not None:
            ponto = [lon, lat]
            if not saida or ponto != saida[-1]:
                saida.append(ponto)
    return saida


def _db_rede(caminho: Path) -> Path:
    """Indexa linhas de um KMZ em SQLite/RTree local descartável sob demanda.

    Não carrega o conteúdo descompactado completo em memória.
    """
    info = caminho.stat()
    assinatura = f"{caminho.resolve()}:{info.st_size}:{info.st_mtime_ns}:rede_v2"
    chave = hashlib.sha256(assinatura.encode()).hexdigest()[:20]
    pasta = Path(tempfile.gettempdir()) / "prisma_geo_rede"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"rede_{chave}.sqlite"

    def pronto(db):
        if not db.is_file():
            return False
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
                return con.execute("SELECT valor FROM metadata WHERE chave='versao'").fetchone() == ("2",)
        except (sqlite3.Error, OSError):
            return False

    if pronto(destino):
        return destino

    with _BLOQUEIO_REDE:
        if pronto(destino):
            return destino
        tmp = pasta / f"{chave}_{uuid.uuid4().hex}.tmp"
        total_trechos = 0
        total_vertices = 0
        try:
            con = sqlite3.connect(tmp)
            try:
                con.execute("CREATE TABLE linhas (fid INTEGER PRIMARY KEY, nome TEXT, coordenadas TEXT)")
                con.execute("CREATE VIRTUAL TABLE limites USING rtree(fid, minx, maxx, miny, maxy)")
                con.execute("CREATE TABLE metadata(chave TEXT PRIMARY KEY, valor TEXT)")
                with zipfile.ZipFile(caminho, "r") as arquivo:
                    entradas = [i for i in arquivo.infolist() if i.filename.lower().endswith(".kml") and not i.is_dir()]
                    if not entradas:
                        raise ValueError("Rede_Agua_THE.kmz não contém arquivo KML.")
                    # A rede pode estar organizada em vários arquivos internos KML.
                    if sum(i.file_size for i in entradas) > 600 * 1024 * 1024:
                        raise ValueError("Rede muito grande para indexação no servidor de testes (limite 600 MB descompactados).")
                    for entrada in entradas:
                        with arquivo.open(entrada, "r") as fluxo:
                            # Limpa o nó-raiz periodicamente, evitando acumular
                            # centenas de milhares de Placemarks vazios na RAM.
                            contexto = ET.iterparse(fluxo, events=("start", "end"))
                            _, raiz_xml = next(contexto)
                            for evento, elem in contexto:
                                if evento != "end" or _local_tag(elem) != "Placemark":
                                    continue
                                nome = next((e.text or "" for e in elem.iter() if _local_tag(e) == "name"), "Trecho da rede")
                                for linha in elem.iter():
                                    if _local_tag(linha) != "LineString":
                                        continue
                                    texto = next((e.text or "" for e in linha.iter() if _local_tag(e) == "coordinates"), "")
                                    coordenadas = _kml_linha(texto)
                                    if len(coordenadas) < 2:
                                        continue
                                    total_trechos += 1
                                    total_vertices += len(coordenadas)
                                    if total_trechos > REDE_MAX_TRECHOS_INDICE or total_vertices > REDE_MAX_COORDENADAS_INDICE:
                                        raise ValueError("Rede acima do limite do protótipo. Será necessário gerar mosaicos vetoriais para esta base.")
                                    xs = [p[0] for p in coordenadas]
                                    ys = [p[1] for p in coordenadas]
                                    con.execute("INSERT INTO linhas(fid,nome,coordenadas) VALUES (?,?,?)",
                                                (total_trechos, nome[:140], json.dumps(coordenadas, separators=(",", ":"))))
                                    con.execute("INSERT INTO limites(fid,minx,maxx,miny,maxy) VALUES (?,?,?,?,?)",
                                                (total_trechos, min(xs), max(xs), min(ys), max(ys)))
                                elem.clear()
                                raiz_xml.clear()
                con.execute("INSERT INTO metadata VALUES ('versao','2')")
                con.execute("INSERT INTO metadata VALUES ('trechos',?)", (str(total_trechos),))
                con.execute("INSERT INTO metadata VALUES ('vertices',?)", (str(total_vertices),))
                con.commit()
            finally:
                con.close()
            os.replace(tmp, destino)
            return destino
        finally:
            if tmp.exists():
                tmp.unlink()


# Uma seleção espacial explícita deve preceder QUALQUER leitura do KMZ.
# Só índices técnicos locais em /tmp: nenhum banco de projetos.
REDE_AREA_MAX_KM2 = 30.0
REDE_MAX_TRECHOS_SELECAO = 2800
REDE_MAX_VERTICES_SELECAO = 48000


def _validar_area_rede(coordenadas):
    """Valida área desenhada, inclusive anéis e limite geodésico de segurança."""
    from shapely.geometry import Polygon
    from pyproj import Geod
    if not isinstance(coordenadas, list) or len(coordenadas) != 1:
        raise ValueError("A área da rede deve ter um polígono externo.")
    anel = coordenadas[0]
    if not isinstance(anel, list) or not 4 <= len(anel) <= 180:
        raise ValueError("Desenhe um polígono de 3 a 179 vértices.")
    vertices = []
    for pt in anel:
        if (not isinstance(pt, list) or len(pt) != 2 or
                not all(isinstance(x, (int, float)) and math.isfinite(x) for x in pt) or
                not (-180 <= pt[0] <= 180 and -90 <= pt[1] <= 90)):
            raise ValueError("A seleção contém coordenadas inválidas.")
        vertices.append((float(pt[0]), float(pt[1])))
    if vertices[0] != vertices[-1]:
        raise ValueError("O polígono precisa estar fechado.")
    poligono = Polygon(vertices)
    if not poligono.is_valid or poligono.is_empty or poligono.area <= 0:
        raise ValueError("O polígono possui cruzamentos ou limites inválidos; redesenhe a área.")
    geod = Geod(ellps="WGS84")
    area_m2, _ = geod.geometry_area_perimeter(poligono)
    area_km2 = abs(area_m2) / 1_000_000
    if area_km2 < 0.0001:
        raise ValueError("Selecione uma região maior para consultar a rede.")
    if area_km2 > REDE_AREA_MAX_KM2:
        raise ValueError(f"Área de {area_km2:.1f} km² excede o limite de {REDE_AREA_MAX_KM2:g} km². Selecione um bairro ou setor menor.")
    return poligono, area_km2


@st.cache_data(show_spinner=False, max_entries=4)
def _rede_por_poligono(db_caminho: str, area_json: str, zoom: int):
    """RTree por envelope + interseção REAL e recorte no polígono definido.

    Nunca retorna lista truncada como se a rede estivesse completa: se passar
    do limite, devolve vazio e solicita reduzir a área.
    """
    from shapely.geometry import LineString, Polygon
    from shapely.geometry import MultiLineString, GeometryCollection

    coordenadas = json.loads(area_json)
    area, _km2 = _validar_area_rede(coordenadas)
    minx, miny, maxx, maxy = area.bounds
    saida = []
    vertices_usados = 0
    trechos_encontrados = 0
    tolerancia = 0.0 if zoom >= 16 else 0.000005 * (2 ** max(0, 15-zoom))

    with sqlite3.connect(f"file:{Path(db_caminho).resolve()}?mode=ro", uri=True) as con:
        linhas = con.execute(
            "SELECT l.fid, l.nome, l.coordenadas FROM limites r "
            "JOIN linhas l ON l.fid=r.fid "
            "WHERE r.maxx>=? AND r.minx<=? AND r.maxy>=? AND r.miny<=?",
            (minx, maxx, miny, maxy),
        )
        for fid, nome, texto in linhas:
            linha = LineString(json.loads(texto))
            if not linha.intersects(area):
                continue
            recorte = linha.intersection(area)
            geometrias = ([recorte] if recorte.geom_type == "LineString" else
                           list(recorte.geoms) if hasattr(recorte, "geoms") else [])
            segmentos = []
            for geom in geometrias:
                if geom.geom_type != "LineString" or geom.is_empty:
                    continue
                if tolerancia and len(geom.coords) > 3:
                    geom = geom.simplify(tolerancia, preserve_topology=False)
                coords = [[round(x, 7), round(y, 7)] for x,y in geom.coords]
                if len(coords) >= 2:
                    segmentos.append(coords)
            if not segmentos:
                continue
            trechos_encontrados += 1
            vertices_usados += sum(len(v) for v in segmentos)
            if trechos_encontrados > REDE_MAX_TRECHOS_SELECAO or vertices_usados > REDE_MAX_VERTICES_SELECAO:
                return [], True, trechos_encontrados
            geom_saida = ({"type":"LineString", "coordinates":segmentos[0]} if len(segmentos)==1
                          else {"type":"MultiLineString","coordinates":segmentos})
            saida.append({
                "type":"Feature", "id":fid,
                "geometry":geom_saida,
                "properties":{"Nome":nome, "pg_tipo":"Rede de abastecimento"},
            })
    return saida, False, trechos_encontrados


# Componente V2: o mapa não recria milhares de marcadores em Folium a cada clique.
# Componente de mapa V2; tudo é renderizado pelo MapLibre, sem marcadores HTML.
GEO_HTML='''
<div class="pg-frame" tabindex="0">
 <div class="pg-tools">
  <button data-mode="pan">✋ Navegar</button>
  <button data-mode="point">📍 Ponto</button>
  <button data-mode="line">〰 Linha</button>
  <button data-mode="polygon">⬡ Polígono</button>
  <button data-mode="network-area" title="Delimite exatamente onde deseja visualizar a rede">▱ Selecionar área da rede</button>
  <button data-act="finish">✓ Concluir</button>
  <button data-act="cancel">✕ Cancelar</button>
  <button data-act="undo" title="Desfazer último vértice ou alteração (Ctrl+Z)">↶ Desfazer</button>
  <button data-act="redo" title="Refazer (Ctrl+Y ou Ctrl+Shift+Z)">↷ Refazer</button>
 </div>
 <div class="pg-map"></div>
 <div class="pg-status">Iniciando mapa…</div>
</div>
'''
GEO_CSS='''
.pg-frame{position:relative;width:100%;height:100%;font:13px system-ui,sans-serif;overflow:hidden;border-radius:12px;border:1px solid #dce3ed;outline:none}
.pg-map{height:100%;width:100%;min-height:480px}
.pg-tools{position:absolute;z-index:15;top:10px;left:10px;display:flex;flex-wrap:wrap;gap:5px;background:rgba(255,255,255,.97);padding:7px;border-radius:9px;box-shadow:0 3px 15px #0002;max-width:95%}
.pg-tools button{border:1px solid #cbd5e1;border-radius:6px;background:#fff;color:#152c42;padding:6px 9px;cursor:pointer;font-size:12px}
.pg-tools button.pg-active{background:#164e77;color:white}
.pg-tools button:disabled{opacity:.45;cursor:not-allowed}
.pg-status{position:absolute;bottom:6px;left:10px;z-index:12;padding:5px 9px;background:rgba(255,255,255,.96);color:#203348;border-radius:6px;max-width:85%;font-size:11px}
'''
GEO_JS=r'''
export default function(component) {
 const {parentElement, data, setTriggerValue} = component;
 const root=parentElement;
 const mapNode=root.querySelector('.pg-map');
 const status=root.querySelector('.pg-status');
 const css=document.createElement('link');css.rel='stylesheet';
 css.href='https://unpkg.com/maplibre-gl@5.6.2/dist/maplibre-gl.css';root.appendChild(css);
 let map=null, alive=true, mode='pan', draft=[], draftRedo=[];
 const modes=[...root.querySelectorAll('[data-mode]')];
 const undoButton=root.querySelector('[data-act="undo"]');
 const redoButton=root.querySelector('[data-act="redo"]');
 const fc=(features)=>({type:'FeatureCollection',features:features||[]});
 const sets=(id,geo)=>{if(map?.getSource(id)) map.getSource(id).setData(geo);};
 const eventId=()=>crypto.randomUUID();
 const viewport=()=>{const c=map.getCenter();return {center:[c.lng,c.lat],zoom:map.getZoom()};};
 const refreshDraft=()=>{
   if(!map?.getSource('pg-draft'))return;
   const feats=[];
   if(draft.length>1) feats.push({type:'Feature',geometry:{type:'LineString',coordinates:draft},properties:{}});
   if(draft.length>=3 && (mode==='polygon'||mode==='network-area'))
      feats.push({type:'Feature',geometry:{type:'Polygon',coordinates:[[...draft,draft[0]]]},properties:{}});
   for(const c of draft)feats.push({type:'Feature',geometry:{type:'Point',coordinates:c},properties:{}});
   sets('pg-draft',fc(feats));
 };
 const refreshButtons=()=>{
   undoButton.disabled=draft.length===0 && !(data.canUndo||false);
   redoButton.disabled=draftRedo.length===0 && !(data.canRedo||false);
 };
 const setMode=(v)=>{
   mode=v;draft=[];draftRedo=[];
   modes.forEach(b=>b.classList.toggle('pg-active',b.dataset.mode===v));
   if(map){map.doubleClickZoom[v==='pan'?'enable':'disable']();refreshDraft();}
   status.textContent= v==='pan'?'Mapa pronto. Selecione elementos ou use as ferramentas.':
     v==='point'?'Clique para posicionar um ponto.':
     v==='network-area'?'Delimite a rede por polígono; clique em Concluir. A rede só será carregada nessa área.':
     'Marque os vértices e clique em Concluir.';
   refreshButtons();
 };
 const add=(geometry)=>{
   const nome=geometry.type==='Polygon'?'Novo polígono':geometry.type==='LineString'?'Nova linha':'Novo ponto';
   const id='f_'+eventId();
   setTriggerValue('created',{type:'Feature',id,geometry,properties:{nome,pg_tipo:'Desenho'},viewport:viewport()});
   setMode('pan');
 };
 const finish=()=>{
   if(mode==='network-area' && draft.length>=3){
     const geom={type:'Polygon',coordinates:[[...draft,draft[0]]]};
     setTriggerValue('network_area',{event:eventId(),geometry:geom,viewport:viewport()});
     setMode('pan');return;
   }
   if(mode==='line' && draft.length>=2){add({type:'LineString',coordinates:draft});return;}
   if(mode==='polygon' && draft.length>=3){add({type:'Polygon',coordinates:[[...draft,draft[0]]]});return;}
   status.textContent='Marque mais vértices antes de concluir.';
 };
 const undo=()=>{
   if(draft.length){draftRedo.push(draft.pop());refreshDraft();refreshButtons();return;}
   if(data.canUndo)setTriggerValue('history_action',{event:eventId(),action:'undo',viewport:viewport()});
 };
 const redo=()=>{
   if(draftRedo.length){draft.push(draftRedo.pop());refreshDraft();refreshButtons();return;}
   if(data.canRedo)setTriggerValue('history_action',{event:eventId(),action:'redo',viewport:viewport()});
 };
 modes.forEach(b=>b.addEventListener('click',()=>setMode(b.dataset.mode)));
 root.querySelector('[data-act="finish"]').onclick=finish;
 root.querySelector('[data-act="cancel"]').onclick=()=>setMode('pan');
 undoButton.onclick=undo;
 redoButton.onclick=redo;
 const onKey=(e)=>{
   if((e.ctrlKey||e.metaKey)&&!e.altKey){
     if(e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}
     else if(e.key.toLowerCase()==='y'){e.preventDefault();redo();}
   }
 };
 root.addEventListener('keydown',onKey);
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
   if(data.topography?.url && data.topography?.coordinates){
     map.addSource('pg-topo',{type:'image',url:data.topography.url,coordinates:data.topography.coordinates});
     map.addLayer({id:'pg-topo-layer',type:'raster',source:'pg-topo',paint:{'raster-opacity':.35,'raster-fade-duration':0}});
   }
   const all=[...(data.project||[]),...(data.lookup||[])];
   const points=all.filter(f=>f.geometry?.type==='Point'||f.geometry?.type==='MultiPoint');
   const lines=all.filter(f=>f.geometry?.type==='LineString'||f.geometry?.type==='MultiLineString');
   const polygons=all.filter(f=>f.geometry?.type==='Polygon'||f.geometry?.type==='MultiPolygon');
   const area=data.networkArea?.length? [{type:'Feature',geometry:{type:'Polygon',coordinates:data.networkArea},properties:{}}]:[];
   map.addSource('pg-network-area',{type:'geojson',data:fc(area)});
   map.addLayer({id:'pg-area-fill',type:'fill',source:'pg-network-area',paint:{'fill-color':'#21BCA4','fill-opacity':.10}});
   map.addLayer({id:'pg-area-outline',type:'line',source:'pg-network-area',paint:{'line-color':'#059669','line-width':2.5,'line-dasharray':[3,2]}});
   map.addSource('pg-network',{type:'geojson',data:fc(data.network||[])});
   map.addLayer({id:'pg-network-layer',type:'line',source:'pg-network',paint:{
      'line-color':'#1279AE','line-opacity':.78,'line-width':['interpolate',['linear'],['zoom'],10,.8,14,1.5,17,2.3]}});
   map.addSource('pg-points',{type:'geojson',data:fc(points)});
   map.addSource('pg-lines',{type:'geojson',data:fc(lines)});
   map.addSource('pg-poly',{type:'geojson',data:fc(polygons)});
   map.addSource('pg-draft',{type:'geojson',data:fc([])});
   map.addLayer({id:'pg-fill',type:'fill',source:'pg-poly',paint:{'fill-color':'#18b3b1','fill-opacity':.2}});
   map.addLayer({id:'pg-outline',type:'line',source:'pg-poly',paint:{'line-color':'#00889b','line-width':2}});
   map.addLayer({id:'pg-lines-layer',type:'line',source:'pg-lines',paint:{'line-color':'#2563eb','line-width':2.5}});
   map.addLayer({id:'pg-points-layer',type:'circle',source:'pg-points',paint:{
      'circle-color':['match',['get','pg_tipo'],
       'Poço / Ativo','#2563EB','Logger','#F59E0B','Matrícula','#22C55E',
       'Desenho','#A855F7','#16A34A'],
      'circle-stroke-color':'#FFFFFF','circle-stroke-width':1.1,
      'circle-radius':['match',['get','pg_tipo'],'Matrícula',5.5,'Poço / Ativo',4.5,'Logger',4.5,4.5],
      'circle-opacity':.94}});
   map.addLayer({id:'pg-poly-names',type:'symbol',source:'pg-poly',minzoom:12,
     filter:['has','nome'],layout:{'text-field':['get','nome'],'text-size':11,'text-allow-overlap':false},
     paint:{'text-color':'#14566D','text-halo-color':'#FFFFFF','text-halo-width':1.7}});
   map.addLayer({id:'pg-draft-fill',type:'fill',source:'pg-draft',filter:['==',['geometry-type'],'Polygon'],
     paint:{'fill-color':'#E67E22','fill-opacity':.13}});
   map.addLayer({id:'pg-draft-line',type:'line',source:'pg-draft',filter:['==',['geometry-type'],'LineString'],
     paint:{'line-color':'#e67e22','line-width':2,'line-dasharray':[2,2]}});
   map.addLayer({id:'pg-draft-point',type:'circle',source:'pg-draft',filter:['==',['geometry-type'],'Point'],
     paint:{'circle-color':'#e67e22','circle-radius':4}});
   map.on('click',(e)=>{
     root.focus();
     if(mode!=='pan'){
       const p=[Number(e.lngLat.lng.toFixed(7)),Number(e.lngLat.lat.toFixed(7))];
       if(mode==='point')add({type:'Point',coordinates:p});
       else {draft.push(p);draftRedo=[];refreshDraft();refreshButtons();}
       return;
     }
     const hits=map.queryRenderedFeatures(e.point,{layers:['pg-points-layer','pg-lines-layer','pg-poly-names','pg-fill','pg-network-layer']});
     if(hits.length){
       const f=hits[0];
       setTriggerValue('selected',{event:eventId(),id:String(f.properties?.pg_id??f.id??''),properties:f.properties||{},viewport:viewport()});
     }
   });
   if(data.focus?.length===2)map.flyTo({center:data.focus,zoom:15});
   setMode('pan');
  });
  map.on('error',()=>{if(alive)status.textContent='Falha no mapa ou nos mosaicos. Verifique sua conexão.';});
  ro=new ResizeObserver(()=>map?.resize());ro.observe(mapNode);
 }).catch(()=>{if(alive)status.textContent='Não foi possível carregar MapLibre. Verifique a conexão.';});
 return ()=>{alive=false;root.removeEventListener('keydown',onKey);if(ro)ro.disconnect();if(map)map.remove();};
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

if "pg_desfazer" not in st.session_state:
    st.session_state.pg_desfazer=[]
if "pg_refazer" not in st.session_state:
    st.session_state.pg_refazer=[]
if "pg_alerta_area" not in st.session_state:
    st.session_state.pg_alerta_area=""
# A caixa pg_rede é um widget: seu valor só pode mudar ANTES da instanciação.
if st.session_state.pop("pg_ativar_rede_proxima_execucao", False):
    st.session_state["pg_rede"] = True

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
        pg_limpar_historico()
        st.session_state["pg_alerta_area"] = ""
        st.session_state.prisma_geo_revisao+=1
        st.session_state.pop("pg_nome", None)
        st.session_state.pop("pg_visible_desenhos", None)
        for chave_off in ("pg_pocos", "pg_loggers", "pg_rede", "pg_topografia", "pg_topo_arquivo"):
            st.session_state.pop(chave_off, None)
        st.rerun()
    abrir=st.file_uploader("Abrir projeto (.prismageo)",type=["prismageo"],key="pg_abertura")
    if st.button("📂 Abrir arquivo",disabled=abrir is None,use_container_width=True):
        try:
            st.session_state.prisma_geo_projeto=abrir_projeto(abrir.getvalue())
            st.session_state.prisma_geo_consulta=[]
            st.session_state.prisma_geo_foco=None
            pg_limpar_historico()
            st.session_state["pg_alerta_area"] = ""
            st.session_state.prisma_geo_revisao+=1
            st.session_state.pop("pg_nome", None)
            for kk in list(st.session_state):
                if kk.startswith("pg_visible_") or kk in ("pg_pocos", "pg_loggers", "pg_rede", "pg_topografia", "pg_topo_arquivo"):
                    st.session_state.pop(kk, None)
            st.success("Projeto aberto.")
            st.rerun()
        except (ValueError,KeyError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
            st.error(f"Falha ao abrir: {exc}")
    try:
        # Salva também referências às camadas oficiais habilitadas, sem
        # embutir milhares de marcadores nem arquivos topográficos no projeto.
        of=p.setdefault("official", {})
        for campo,chave in (("pocos","pg_pocos"),("loggers","pg_loggers"),
                            ("rede","pg_rede"),("topografia","pg_topografia")):
            of[campo] = bool(st.session_state.get(chave,of.get(campo,False)))
        caminho_topo_atual=st.session_state.get("pg_topo_arquivo")
        if isinstance(caminho_topo_atual, Path):
            try:
                of["topo_file"] = str(caminho_topo_atual.relative_to(ROOT))
            except ValueError:
                pass
        salvar=pacote_projeto(p)
        nome_arquivo=re.sub(r"[^a-zA-Z0-9_-]+","_",p["name"]).strip("_") or "meu_mapa"
        st.download_button("💾 Salvar projeto no computador",data=salvar,
                           file_name=f"{nome_arquivo}.prismageo",mime="application/zip",use_container_width=True)
    except ValueError as exc:
        st.warning(str(exc))
    st.caption("Salve o arquivo antes de sair: projetos não são gravados em banco de dados.")
    col_undo, col_refazer = st.columns(2)
    with col_undo:
        if st.button("↶ Desfazer", key="pg_desfazer_botao", disabled=not st.session_state.pg_desfazer,
                     use_container_width=True):
            pg_historico("desfazer")
            st.rerun()
    with col_refazer:
        if st.button("↷ Refazer", key="pg_refazer_botao", disabled=not st.session_state.pg_refazer,
                     use_container_width=True):
            pg_historico("refazer")
            st.rerun()
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
            pg_registrar({"tipo":"adicionar_camada", "camada":layer, "posicao":len(p["layers"])})
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
    if "official" not in p or not isinstance(p["official"], dict):
        p["official"] = novo_projeto()["official"]
    for campo,chave in (("pocos","pg_pocos"),("loggers","pg_loggers"),
                        ("rede","pg_rede"),("topografia","pg_topografia")):
        if chave not in st.session_state:
            st.session_state[chave] = bool(p["official"].get(campo,False))
    mostrar_pocos = st.checkbox("Mostrar poços e ativos", key="pg_pocos")
    mostrar_loggers = st.checkbox("Mostrar loggers", key="pg_loggers")
    mostrar_rede = st.checkbox("Exibir rede da área selecionada", key="pg_rede")
    st.caption("Para consultar a rede, clique no mapa em **▱ Selecionar área da rede**, desenhe o polígono e pressione **✓ Concluir**. A rede só será lida após essa seleção.")
    area_rede = p.get("network_area")
    if st.session_state.pg_alerta_area:
        st.warning(st.session_state.pg_alerta_area)
    if area_rede:
        try:
            _, km2 = _validar_area_rede(area_rede)
            st.caption(f"Área selecionada: {km2:.2f} km² (máximo: {REDE_AREA_MAX_KM2:g} km²).")
        except ValueError:
            st.warning("Área inválida. Selecione novamente.")
        if st.button("✕ Limpar área da rede", key="pg_limpar_area", use_container_width=True):
            pg_registrar({"tipo":"selecionar_rede", "antes":area_rede, "depois":None})
            p["network_area"] = None
            st.session_state["pg_alerta_area"] = ""
            st.rerun()
    elif mostrar_rede:
        st.info("Aguardando você selecionar um polígono no mapa. Nenhum trecho foi carregado.")
    topografias = _pasta_topografias()
    mostrar_topografia = st.checkbox("Mostrar relevo/topografia", key="pg_topografia")
    selecionada_topo = None
    if mostrar_topografia:
        if topografias:
            if "pg_topo_arquivo" not in st.session_state:
                salvo_topo = p["official"].get("topo_file", "")
                st.session_state["pg_topo_arquivo"] = next(
                    (arq for arq in topografias if str(arq.relative_to(ROOT)) == salvo_topo), topografias[0]
                )
            selecionada_topo = st.selectbox("Arquivo topográfico", topografias,
                                           format_func=lambda f: str(f.relative_to(ROOT)), key="pg_topo_arquivo")
        else:
            st.warning("Nenhum GeoTIFF encontrado em dados/topografias (ou dados/topografia).")
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
                if features:
                    p["center"] = list(st.session_state.prisma_geo_foco)
                    p["zoom"] = 15
                st.session_state.prisma_geo_revisao+=1
                st.success(f"{len(features)} registro(s) localizado(s).")
            except (sqlite3.Error,FileNotFoundError,ValueError,RuntimeError,ImportError,OSError) as exc:
                st.warning(str(exc))
                st.info("Confirme que dados/matriculas.xls ou dados/matriculas.xlsx está disponível. A consulta do Módulo 8 original continua independente.")
    st.caption("Pesquisar não altera o banco nem adiciona automaticamente os resultados ao projeto.")

left,right=st.columns([4.8,1.2],gap="medium")
with left:
    st.info("Use **▱ Selecionar área da rede** para delimitar os trechos. Para criar desenhos, use Ponto, Linha ou Polígono; conclua e nomeie o elemento no painel direito. **Desfazer/Refazer** funciona tanto durante o traçado quanto após salvar o desenho.")
    # pg_id é enviado apenas ao mapa: torna a seleção confiável, sem alterar o arquivo salvo.
    features=[{**feature, "properties":{**feature.get("properties", {}),
                                        "pg_id":str(feature.get("id", ""))}}
              for layer in p["layers"] if layer.get("visible") for feature in layer["features"]]
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
    # Bases grandes são carregadas somente quando habilitadas.
    dados_topografia = None
    if mostrar_topografia and selecionada_topo is not None:
        try:
            info_topo = selecionada_topo.stat()
            with st.spinner("Preparando prévia leve da topografia..."):
                dados_topografia = _topografia_previa(str(selecionada_topo), info_topo.st_size, info_topo.st_mtime_ns)
            st.caption(f"Topografia: {selecionada_topo.name} · amplitude: {dados_topografia['elevation_range'][0]}–{dados_topografia['elevation_range'][1]} m")
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            st.warning(f"Topografia indisponível: {exc}")

    dados_rede = []
    area_rede = p.get("network_area")
    if mostrar_rede and area_rede:
        arquivo_rede = PASTA_DADOS / "Rede_Agua_THE.kmz"
        if not arquivo_rede.is_file():
            st.warning("Rede não encontrada: dados/Rede_Agua_THE.kmz")
        else:
            try:
                _, _km2 = _validar_area_rede(area_rede)
                # A indexação só ocorre após uma área válida ter sido desenhada.
                with st.spinner("Consultando os trechos da rede na área selecionada..."):
                    db = _db_rede(arquivo_rede)
                    dados_rede, excesso, qtd = _rede_por_poligono(
                        str(db), json.dumps(area_rede, separators=(",", ":")), int(round(float(p["zoom"])))
                    )
                if excesso:
                    st.warning("A seleção contém trechos demais para exibir com segurança. Desenhe uma área menor; nenhum trecho foi exibido parcialmente.")
                else:
                    st.caption(f"Rede THE: {qtd:,} trecho(s) no polígono selecionado.".replace(",", "."))
            except (OSError, ValueError, TypeError, RuntimeError, zipfile.BadZipFile,
                    ET.ParseError, sqlite3.Error) as exc:
                st.warning(f"Não foi possível consultar a rede: {exc}")
    if len(features)>MAX_FEATURES*10:
        st.error("Mapa muito grande. Oculte camadas ou reduza os arquivos importados.")
        st.stop()
    try:
        comp=st.components.v2.component("prisma_geo_editor_v2",html=GEO_HTML,css=GEO_CSS,js=GEO_JS)
        response=comp(data={"project":features,"lookup":st.session_state.prisma_geo_consulta,
                             "network":dados_rede,"networkArea":p.get("network_area"),
                             "topography":dados_topografia,
                             "focus":st.session_state.prisma_geo_foco,
                             "center":p["center"],"zoom":p["zoom"],
                             "canUndo":bool(st.session_state.pg_desfazer),
                             "canRedo":bool(st.session_state.pg_refazer)},
                       key="prisma_geo_mapa_componente_v2",width="stretch",height=600,
                       on_created_change=lambda:None,on_selected_change=lambda:None,
                       on_network_area_change=lambda:None,on_history_action_change=lambda:None)
        if response.network_area and pg_evento_novo("area", response.network_area):
            evento = response.network_area
            geometria = evento.get("geometry", {})
            try:
                if geometria.get("type") != "Polygon":
                    raise ValueError("Área recebida não é um polígono.")
                coords = geometria.get("coordinates")
                _, km2 = _validar_area_rede(coords)
                pg_registrar({"tipo":"selecionar_rede", "antes":p.get("network_area"), "depois":coords})
                p["network_area"] = coords
                st.session_state["pg_ativar_rede_proxima_execucao"] = True
                st.session_state["pg_alerta_area"] = ""
                cam = evento.get("viewport") or {}
                centro, zoom = cam.get("center"), cam.get("zoom")
                if (isinstance(centro,list) and len(centro)==2 and
                    all(isinstance(v,(int,float)) and math.isfinite(v) for v in centro)):
                    p["center"] = [float(v) for v in centro]
                if isinstance(zoom,(int,float)) and math.isfinite(zoom):
                    p["zoom"] = max(2., min(20., float(zoom)))
                st.session_state.prisma_geo_foco = None
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.session_state["pg_alerta_area"] = str(exc)
                st.rerun()
        if response.history_action and pg_evento_novo("hist", response.history_action):
            evento = response.history_action
            modo = "desfazer" if evento.get("action") == "undo" else "refazer"
            cam = evento.get("viewport") or {}
            centro, zoom = cam.get("center"), cam.get("zoom")
            if (isinstance(centro,list) and len(centro)==2 and
                all(isinstance(v,(int,float)) and math.isfinite(v) for v in centro)):
                p["center"] = [float(v) for v in centro]
            if isinstance(zoom,(int,float)) and math.isfinite(zoom):
                p["zoom"] = max(2., min(20., float(zoom)))
            if pg_historico(modo):
                st.rerun()
        if response.created and pg_evento_novo("draw", response.created):
            cam = response.created.get("viewport") or {}
            centro, zoom = cam.get("center"), cam.get("zoom")
            if (isinstance(centro,list) and len(centro)==2 and
                all(isinstance(v,(int,float)) and math.isfinite(v) for v in centro)):
                p["center"] = [float(v) for v in centro]
            if isinstance(zoom,(int,float)) and math.isfinite(zoom):
                p["zoom"] = max(2., min(20., float(zoom)))
            st.session_state.prisma_geo_foco = None
            item=checar_geojson({"type":"FeatureCollection","features":[response.created]})[0]
            target=next((l for l in p["layers"] if l["id"]=="desenhos"),None)
            if not target:
                target=camada_geojson("Meus desenhos",[],"manual")
                p["layers"].insert(0,target)
            pg_registrar({"tipo":"criar_elemento","camada":target["id"],
                          "elemento":item,"posicao":len(target["features"])})
            target["features"].append(item)
            st.session_state.prisma_geo_selecionado={"id":str(item["id"]),"properties":item["properties"]}
            st.session_state.pop("pg_nome_elemento",None)
            st.toast("Elemento criado. Dê um nome no painel à direita e salve seu projeto.")
            st.rerun()
        if response.selected and pg_evento_novo("select", response.selected):
            cam = response.selected.get("viewport") or {}
            centro, zoom = cam.get("center"), cam.get("zoom")
            if (isinstance(centro,list) and len(centro)==2 and
                all(isinstance(v,(int,float)) and math.isfinite(v) for v in centro)):
                p["center"] = [float(v) for v in centro]
            if isinstance(zoom,(int,float)) and math.isfinite(zoom):
                p["zoom"] = max(2., min(20., float(zoom)))
            st.session_state.prisma_geo_foco = None
            st.session_state.prisma_geo_selecionado=response.selected
            st.session_state.pop("pg_nome_elemento",None)
            st.rerun()
    except AttributeError:
        st.error("Este protótipo exige Streamlit com suporte a Components V2.")
    st.caption("Mapa-base: OpenStreetMap · Marcadores: matrícula verde, poços azul, loggers laranja. A rede e a topografia são camadas somente de leitura.")
with right:
    st.subheader("Elementos")
    total=sum(len(l["features"]) for l in p["layers"])
    st.metric("No projeto",total)
    st.metric("Na consulta",len(st.session_state.prisma_geo_consulta))
    st.caption("Camadas oficiais aparecem apenas quando habilitadas. Rede carregada por área; topografia em imagem reduzida.")
    selecionado=st.session_state.prisma_geo_selecionado
    if selecionado:
        st.markdown("**Seleção no mapa**")
        id_selecionado = str(selecionado.get("id", ""))
        camada_editavel, elemento_editavel = pg_encontrar_elemento(p, id_selecionado)
        if camada_editavel is not None and elemento_editavel is not None:
            st.caption(f"Camada: {camada_editavel['name']}")
            nome_atual = str(elemento_editavel.get("properties", {}).get("nome") or
                             elemento_editavel.get("properties", {}).get("Nome") or "Sem nome")
            with st.form("pg_form_nome_elemento"):
                nome_novo = st.text_input("Nome do elemento", value=nome_atual,
                                         max_chars=120, key="pg_nome_elemento")
                if st.form_submit_button("💾 Aplicar nome", use_container_width=True):
                    nome_novo = nome_novo.strip()
                    if not nome_novo:
                        st.warning("Informe um nome para o elemento.")
                    elif nome_novo != nome_atual:
                        pg_registrar({"tipo":"renomear_elemento", "id":id_selecionado,
                                      "antes":nome_atual, "depois":nome_novo})
                        elemento_editavel.setdefault("properties", {})["nome"] = nome_novo
                        st.session_state.prisma_geo_selecionado = {
                            "id":id_selecionado, "properties":elemento_editavel["properties"]}
                        st.toast("Nome atualizado. Baixe o projeto para conservar a alteração.")
                        st.rerun()
                    else:
                        st.info("O nome não foi alterado.")
        else:
            st.caption("Elemento oficial ou resultado de consulta: somente leitura.")
        for k,v in list((selecionado.get("properties") or {}).items())[:8]:
            st.caption(f"{k}: {v}")
    st.divider()
    st.markdown("**Gerenciamento**")
    lista=[(l["id"],l["name"]) for l in p["layers"] if l["source"]!="manual"]
    if lista:
        alvo=st.selectbox("Remover camada importada",[v[0] for v in lista],format_func=lambda x:dict(lista)[x])
        if st.button("Remover camada",key="pg_excluir_camada"):
            camada_removida = next((l for l in p["layers"] if l["id"]==alvo), None)
            if camada_removida is not None:
                pg_registrar({"tipo":"remover_camada", "camada":camada_removida,
                              "posicao":p["layers"].index(camada_removida)})
            p["layers"]=[l for l in p["layers"] if l["id"]!=alvo]
            st.session_state.prisma_geo_revisao+=1
            st.rerun()
    if st.button("Limpar desenhos",use_container_width=True):
        anteriores={l["id"]:l["features"] for l in p["layers"]
                    if l["source"]=="manual" and l["features"]}
        if anteriores:
            pg_registrar({"tipo":"limpar_desenhos", "anteriores":anteriores})
        for layer in p["layers"]:
            if layer["source"]=="manual":layer["features"]=[]
        st.session_state.prisma_geo_selecionado=None
        st.session_state.pop("pg_nome_elemento", None)
        st.session_state.prisma_geo_revisao+=1
        st.rerun()

st.divider()
st.caption("PRISMA GEO · Ambiente de Teste · Projetos locais · Rede e relevo apenas para consulta · Nenhuma alteração nas bases oficiais.")
