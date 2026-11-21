"""Central de Atualização de Bases — integrações administrativas com GitHub e Supabase.

Somente o backend Streamlit utiliza as credenciais. Não há execução de scripts,
SQL arbitrário ou substituição de arquivos de código pelo painel.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import mimetypes
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

from auth import verificar_autenticacao, pode_executar_acao
from prisma_backend import ErroPRISMA, auditar, configuracao, perfil_de_usuario, _tabela

REPO_PADRAO = "JothaP/prisma-coi"
EXTENSOES_DADOS = frozenset({
    ".csv", ".xlsx", ".xls", ".gpkg", ".kmz", ".kml",
    ".tif", ".tiff", ".geojson", ".zip",
})
MAX_GITHUB = 35 * 1024 * 1024
MAX_STORAGE = 35 * 1024 * 1024
MAX_REGISTROS_TABELA = 10000
COLUNAS_POCOS = (
    "ID_POCO", "IDENTIFICACAO_ATIVO", "NOME_POCO",
    "MUNICIPIO", "LATITUDE", "LONGITUDE",
)
TABELAS_CONSULTA = {
    "prisma_ativos": "Cadastro operacional central de ativos (consulta/exportação)",
    "prisma_ativos_importacao": "Área de importação legada de poços (substituição e restauração)",
    "prisma_fechamentos_rede": "Fechamentos de rede (consulta/exportação)",
    "prisma_manutencoes": "Manutenções (consulta/exportação)",
    "farol_acomp_fichas": "Fichas de acompanhamento (consulta/exportação)",
    "farol_acomp_vinculos": "Vínculos de acompanhamentos (consulta/exportação)",
    "farol_acomp_movimentacoes": "Movimentações (consulta/exportação)",
}
BACKUP_STORAGE = "_backup_atualizacoes"


class ErroAtualizacao(RuntimeError):
    pass


def administrador_atual() -> str:
    """Dupla verificação, inclusive quando uma função é chamada sem a UI."""
    if (not verificar_autenticacao(forcar=True)
            or st.session_state.get("perfil") != "admin"
            or not pode_executar_acao("gestao", "editar")):
        raise PermissionError("Central de Atualizações exclusiva de administradores.")
    autor = str(st.session_state.get("prisma_usuario_id") or "").strip()
    if not autor:
        raise PermissionError("Administrador sem identificação de sessão.")
    perfil = perfil_de_usuario(autor)
    if (not perfil or perfil.get("perfil") != "admin"
            or perfil.get("status") != "ativo"):
        raise PermissionError("Somente administradores ativos podem atualizar bases.")
    return autor


def _auditar_base(autor, acao, origem, caminho, detalhe=""):
    auditar(autor, acao,
           detalhes=f"Origem={origem}; arquivo={caminho[:190]}; {detalhe}"[:495],
           modulo="gestao", tipo="ALTERACAO", registro_id=caminho[:150])


def caminho_permitido(caminho: str, *, storage=False):
    if not isinstance(caminho, str):
        return False
    if (not caminho or caminho.startswith("/") or "\\" in caminho
            or any(parte in (".", "..", "") for parte in caminho.split("/"))
            or any(ord(x) < 32 for x in caminho)):
        return False
    if PurePosixPath(caminho).suffix.lower() not in EXTENSOES_DADOS:
        return False
    if storage:
        return not caminho.startswith(BACKUP_STORAGE + "/")
    return caminho.startswith(("dados/", "geodados/"))


def _sha(payload: bytes):
    return hashlib.sha256(payload).hexdigest()


def _csv_ler(payload: bytes):
    try:
        texto = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErroAtualizacao("CSV deve estar em UTF-8 (com ou sem BOM).") from exc
    try:
        dialect = csv.Sniffer().sniff(texto[:8192], delimiters=",;\t")
        leitor = csv.DictReader(io.StringIO(texto), dialect=dialect)
    except csv.Error as exc:
        raise ErroAtualizacao("Cabeçalho CSV inválido.") from exc
    colunas = [str(c).strip() for c in (leitor.fieldnames or [])]
    if not colunas or len(set(colunas)) != len(colunas):
        raise ErroAtualizacao("CSV sem cabeçalho ou com colunas duplicadas.")
    return colunas, leitor


def _validar_pontos(df):
    # Verifica até 10.000 registros do recorte; evita trocar coordenadas inválidas.
    for campo, minimo, maximo in (("LATITUDE", -90, 90), ("LONGITUDE", -180, 180)):
        if campo not in df:
            raise ErroAtualizacao("Coluna obrigatória ausente: " + campo)
        for valor in df[campo]:
            texto = str(valor or "").strip().replace(",", ".")
            try:
                num = float(texto)
            except (TypeError, ValueError) as exc:
                raise ErroAtualizacao("Coordenada não numérica em " + campo) from exc
            if not minimo <= num <= maximo or num == 0:
                raise ErroAtualizacao("Coordenada fora dos limites em " + campo)


def preparar_linhas_pocos(payload: bytes, extensao: str):
    """Converte CSV/XLSX de poços para as seis colunas da tabela de importação."""
    extensao = extensao.lower()
    if extensao == ".csv":
        colunas, leitor = _csv_ler(payload)
        if tuple(colunas) != COLUNAS_POCOS:
            raise ErroAtualizacao("O CSV de poços deve ter exatamente as seis colunas do cadastro.")
        linhas = []
        for registro in leitor:
            if len(linhas) >= MAX_REGISTROS_TABELA:
                raise ErroAtualizacao("Importação limitada a 10.000 registros por operação.")
            if None in registro:
                raise ErroAtualizacao("CSV possui mais campos que o cabeçalho.")
            linhas.append({c: str(registro.get(c) or "").strip() for c in COLUNAS_POCOS})
    elif extensao == ".xlsx":
        try:
            import openpyxl
            planilha = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
            try:
                aba = planilha.active
                valores = aba.iter_rows(values_only=True)
                colunas = [str(v).strip() if v is not None else "" for v in next(valores)]
                if tuple(colunas) != COLUNAS_POCOS:
                    raise ErroAtualizacao("A primeira aba deve conter exatamente as seis colunas de poços.")
                linhas = []
                for linha in valores:
                    if not any(valor is not None and str(valor).strip() for valor in linha):
                        continue
                    if len(linhas) >= MAX_REGISTROS_TABELA:
                        raise ErroAtualizacao("Importação limitada a 10.000 registros por operação.")
                    linhas.append({campo: str(valor).strip() if valor is not None else ""
                                   for campo, valor in zip(COLUNAS_POCOS, linha)})
            finally:
                planilha.close()
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise ErroAtualizacao("Planilha XLSX inválida ou corrompida.") from exc
    else:
        raise ErroAtualizacao("O banco aceita somente CSV ou XLSX nesta operação.")

    if not linhas:
        raise ErroAtualizacao("A base enviada está vazia.")
    ids = [r["IDENTIFICACAO_ATIVO"].casefold() for r in linhas]
    if any(not v for v in ids) or len(set(ids)) != len(ids):
        raise ErroAtualizacao("Identificações de ativos vazias ou duplicadas.")
    _validar_pontos(pd.DataFrame(linhas))
    return linhas


def validar_arquivo(caminho: str, nome_upload: str, payload: bytes, *, storage=False):
    if not caminho_permitido(caminho, storage=storage):
        raise ErroAtualizacao("Arquivo não autorizado para substituição.")
    if PurePosixPath(nome_upload).name != nome_upload:
        raise ErroAtualizacao("Nome do upload inválido.")
    extensao = PurePosixPath(caminho).suffix.lower()
    if PurePosixPath(nome_upload).suffix.lower() != extensao:
        raise ErroAtualizacao("A extensão do novo arquivo deve ser igual à atual.")
    if not isinstance(payload, bytes) or not payload:
        raise ErroAtualizacao("O novo arquivo não pode estar vazio.")
    limite = MAX_STORAGE if storage else MAX_GITHUB
    if len(payload) > limite:
        raise ErroAtualizacao("Arquivo acima do limite de 35 MB do painel.")
    resultado = {"bytes": len(payload), "sha256": _sha(payload), "extensao": extensao}
    if extensao == ".csv":
        colunas, leitor = _csv_ler(payload)
        if caminho.endswith("loggers.csv"):
            obrigatorias = ("ID_LOGGER","IDENTIFICACAO_ATIVO","ENDERECO",
                            "MUNICIPIO","LATITUDE","LONGITUDE")
        elif caminho.endswith("pocos.csv"):
            obrigatorias = COLUNAS_POCOS
        else:
            obrigatorias = ()
        if obrigatorias and tuple(colunas) != obrigatorias:
            raise ErroAtualizacao("Cabeçalhos incompatíveis com a base operacional selecionada.")
        amostra, total = [], 0
        ids = set()
        for item in leitor:
            if None in item:
                raise ErroAtualizacao("Linha CSV com quantidade inválida de colunas.")
            total += 1
            if total > 500000:
                raise ErroAtualizacao("O CSV excede 500.000 registros; utilize importação dedicada.")
            if len(amostra) < 5:
                amostra.append(item)
            if obrigatorias:
                codigo = str(item.get("IDENTIFICACAO_ATIVO") or "").strip().casefold()
                if not codigo or codigo in ids:
                    raise ErroAtualizacao("Identificador do equipamento ausente ou duplicado no CSV.")
                ids.add(codigo)
                for campo, minimo, maximo in (("LATITUDE",-90,90),("LONGITUDE",-180,180)):
                    try:
                        coord = float(str(item.get(campo) or "").strip().replace(",", "."))
                    except (TypeError, ValueError) as exc:
                        raise ErroAtualizacao("Coordenada inválida em " + campo) from exc
                    if not minimo <= coord <= maximo or coord == 0:
                        raise ErroAtualizacao("Coordenada fora do intervalo em " + campo)
        if total == 0:
            raise ErroAtualizacao("O CSV não contém dados.")
        if obrigatorias:
            _validar_pontos(pd.DataFrame(
                [dict(zip(obrigatorias, [str(row.get(k) or "") for k in obrigatorias]))
                 for row in amostra]
            ))
        resultado.update({"registros": total, "colunas": colunas, "amostra": amostra})
    elif extensao == ".xlsx":
        try:
            import openpyxl
            pasta = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
            try:
                if not pasta.sheetnames:
                    raise ErroAtualizacao("Planilha sem abas.")
                aba = pasta.active
                primeiras = list(next(aba.iter_rows(values_only=True), []))
                if not any(c is not None for c in primeiras):
                    raise ErroAtualizacao("Planilha sem cabeçalho.")
                resultado["abas"] = pasta.sheetnames
                resultado["colunas"] = [str(c) if c is not None else "" for c in primeiras[:40]]
            finally:
                pasta.close()
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise ErroAtualizacao("XLSX inválido ou corrompido.") from exc
    elif extensao == ".gpkg":
        if not payload.startswith(b"SQLite format 3\x00"):
            raise ErroAtualizacao("GeoPackage inválido: assinatura SQLite não encontrada.")
    elif extensao in {".kmz", ".zip"}:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                infos = z.infolist()
                if not infos or len(infos) > 5000 or sum(i.file_size for i in infos) > 250_000_000:
                    raise ErroAtualizacao("Arquivo compactado vazio ou de tamanho expandido excessivo.")
                if extensao == ".kmz" and not any(i.filename.lower().endswith(".kml") for i in infos):
                    raise ErroAtualizacao("KMZ sem arquivo KML interno.")
        except zipfile.BadZipFile as exc:
            raise ErroAtualizacao("Arquivo compactado inválido.") from exc
    elif extensao == ".kml":
        if b"<kml" not in payload[:5000].lower():
            raise ErroAtualizacao("Conteúdo não parece um arquivo KML válido.")
    elif extensao == ".geojson":
        try:
            item = json.loads(payload)
            if not isinstance(item, dict) or item.get("type") not in {
                "FeatureCollection","Feature","Point","Polygon",
                "MultiPolygon","LineString","MultiLineString",
            }:
                raise ValueError
        except (ValueError, UnicodeDecodeError) as exc:
            raise ErroAtualizacao("GeoJSON inválido.") from exc
    elif extensao in {".tif",".tiff"}:
        if payload[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
            raise ErroAtualizacao("Arquivo TIFF inválido.")
    elif extensao == ".xls":
        if payload[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
            raise ErroAtualizacao("Arquivo XLS inválido.")
    return resultado


def github_config():
    nome = str(st.secrets.get("PRISMA_GITHUB_REPO", REPO_PADRAO)).strip()
    branch = str(st.secrets.get("PRISMA_GITHUB_BRANCH", "main")).strip()
    token = str(st.secrets.get("PRISMA_GITHUB_TOKEN", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", nome):
        raise ErroAtualizacao("Repositório GitHub configurado incorretamente.")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch:
        raise ErroAtualizacao("Branch GitHub inválida.")
    return nome, branch, token


def _github(method, endpoint, *, token=None, payload=None):
    headers = {"Accept":"application/vnd.github+json",
               "X-GitHub-Api-Version":"2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        resposta = requests.request(method,
            "https://api.github.com" + endpoint, headers=headers,
            json=payload, timeout=65)
    except requests.RequestException as exc:
        raise ErroAtualizacao("GitHub indisponível.") from exc
    if resposta.status_code >= 400:
        raise ErroAtualizacao(
            f"GitHub retornou HTTP {resposta.status_code}. "
            "Verifique token, permissão Contents e existência do arquivo."
        )
    try:
        return resposta.json()
    except ValueError as exc:
        raise ErroAtualizacao("Resposta inválida da API do GitHub.") from exc


def arquivos_github():
    administrador_atual()
    nome, branch, token = github_config()
    arvore = _github("GET",f"/repos/{nome}/git/trees/{quote(branch,safe='')}?recursive=1",
                     token=token)
    if arvore.get("truncated"):
        raise ErroAtualizacao("Listagem incompleta no GitHub. Nenhuma substituição habilitada.")
    resultado = []
    for item in arvore.get("tree", []):
        if item.get("type") == "blob":
            caminho = str(item.get("path",""))
            resultado.append({"caminho":caminho, "tamanho":item.get("size",0),
                              "sha":item.get("sha",""),
                              "editavel":caminho_permitido(caminho)})
    return sorted(resultado, key=lambda v: v["caminho"].casefold())


def comparar_csv_github(caminho, payload_novo, sha_atual):
    """Compara cadastros por identificação sem depender do raio/concentrações.

    A análise é disponibilizada para loggers e poços; não lê bases sensíveis
    de código e limita o blob antigo a 4 MB para preservar desempenho.
    """
    administrador_atual()
    if caminho not in ("dados/loggers.csv","dados/pocos.csv"):
        return None
    repo, branch, token = github_config()
    if not token or not re.fullmatch(r"[a-f0-9]{40}",str(sha_atual)):
        raise ErroAtualizacao("Não é possível consultar a versão anterior do CSV.")
    metadados = _github("GET",f"/repos/{repo}/git/blobs/{sha_atual}",token=token)
    if metadados.get("encoding") != "base64" or int(metadados.get("size",0)) > 4*1024*1024:
        raise ErroAtualizacao("Não foi possível comparar o cadastro anterior.")
    try:
        payload_antigo=base64.b64decode(metadados["content"],validate=False)
    except (ValueError,KeyError) as exc:
        raise ErroAtualizacao("Blob anterior inválido.") from exc
    colunas_antigas, linhas_antigas = _csv_ler(payload_antigo)
    colunas_novas, linhas_novas = _csv_ler(payload_novo)
    if colunas_antigas != colunas_novas:
        raise ErroAtualizacao("As colunas da base antiga e nova não correspondem.")
    identificador="IDENTIFICACAO_ATIVO"
    def indexar(registros):
        indice={}
        for reg in registros:
            chave=str(reg.get(identificador) or "").strip().casefold()
            if not chave or chave in indice:
                raise ErroAtualizacao("Cadastro anterior ou novo possui identificações duplicadas.")
            indice[chave]=tuple(str(reg.get(c) or "").strip() for c in colunas_antigas)
        return indice
    antigo=indexar(linhas_antigas)
    novo=indexar(linhas_novas)
    adicionados=set(novo)-set(antigo)
    removidos=set(antigo)-set(novo)
    comuns=set(antigo)&set(novo)
    alterados={c for c in comuns if antigo[c]!=novo[c]}
    return {
        "anteriores":len(antigo),"novos":len(novo),
        "adicionados":len(adicionados),"removidos":len(removidos),
        "modificados":len(alterados),"inalterados":len(comuns)-len(alterados),
        "amostra_adicionados":sorted(adicionados)[:8],
        "amostra_removidos":sorted(removidos)[:8],
        "amostra_modificados":sorted(alterados)[:8],
    }


def substituir_github(caminho, nome_upload, payload, sha_esperado):
    autor = administrador_atual()
    validar_arquivo(caminho,nome_upload,payload)
    nome, branch, token = github_config()
    if not token:
        raise ErroAtualizacao(
            "Configure PRISMA_GITHUB_TOKEN no Streamlit Secrets antes de substituir arquivos."
        )
    metadados = _github("GET",
        f"/repos/{nome}/contents/{quote(caminho,safe='/')}?ref={quote(branch,safe='')}",
        token=token)
    if (not isinstance(metadados,dict) or metadados.get("type") != "file"
            or metadados.get("sha") != sha_esperado):
        raise ErroAtualizacao("O arquivo foi alterado desde a seleção. Atualize a lista.")
    resposta = _github("PUT",
        f"/repos/{nome}/contents/{quote(caminho,safe='/')}",
        token=token,
        payload={"message":f"data(PRISMA): atualizar {caminho}",
                 "content":base64.b64encode(payload).decode("ascii"),
                 "sha":sha_esperado,"branch":branch})
    commit = (resposta.get("commit") or {}).get("sha")
    if not commit:
        raise ErroAtualizacao("O GitHub não confirmou o commit.")
    _auditar_base(autor,"atualizar_base_github","GitHub",caminho,
                   f"commit={commit[:16]}; bytes={len(payload)}")
    return {"commit":commit,"url":f"https://github.com/{nome}/commit/{commit}"}


def _sb_config():
    cfg = configuracao()
    url = str(cfg.get("PRISMA_SUPABASE_URL") or "").rstrip("/")
    chave = str(cfg.get("PRISMA_SUPABASE_SERVICE_ROLE_KEY") or "")
    if not url.startswith("https://") or not chave:
        raise ErroAtualizacao("Credenciais administrativas do Supabase não configuradas.")
    return url,chave


def _storage(method, endpoint, *, dados=None, json_dados=None, upsert=False):
    url,key=_sb_config()
    cab={"apikey":key,"Accept":"application/json"}
    if not key.startswith("sb_secret_"):
        cab["Authorization"]="Bearer "+key
    if dados is not None:
        cab["Content-Type"]="application/octet-stream"
        cab["cache-control"]="max-age=60"
    if json_dados is not None:
        cab["Content-Type"]="application/json"
    if upsert:
        cab["x-upsert"]="true"
    try:
        r=requests.request(method,url+"/storage/v1"+endpoint,
                           headers=cab,data=dados,json=json_dados,timeout=75)
    except requests.RequestException as exc:
        raise ErroAtualizacao("Supabase Storage indisponível.") from exc
    if r.status_code >= 400:
        raise ErroAtualizacao(f"Supabase Storage retornou HTTP {r.status_code}.")
    return r


def buckets_storage():
    administrador_atual()
    data=_storage("GET","/bucket").json()
    if not isinstance(data,list):
        raise ErroAtualizacao("Não foi possível listar os buckets do Storage.")
    return [{"id":x.get("id"),"public":bool(x.get("public"))}
            for x in data if x.get("id")]


def objetos_storage(bucket, limite=3000):
    administrador_atual()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+",bucket):
        raise ErroAtualizacao("Bucket inválido.")
    fila=[""]
    objetos=[]
    while fila and len(objetos)<limite:
        pasta=fila.pop(0)
        offset=0
        while True:
            itens=_storage("POST", "/object/list/"+quote(bucket,safe=""),
                json_dados={"prefix":pasta,"limit":100,"offset":offset}).json()
            if not isinstance(itens,list):
                raise ErroAtualizacao("Listagem inválida de objetos do Storage.")
            for item in itens:
                caminho=pasta+str(item.get("name",""))
                if item.get("id") is None:
                    if caminho and not caminho.startswith(BACKUP_STORAGE):
                        fila.append(caminho+"/")
                elif not caminho.startswith(BACKUP_STORAGE+"/"):
                    objetos.append({"caminho":caminho,
                        "tamanho":(item.get("metadata") or {}).get("size",0),
                        "atualizado_em":item.get("updated_at"),
                        "editavel":caminho_permitido(caminho,storage=True)})
            if len(itens)<100 or len(objetos)>=limite:
                break
            offset+=100
    return sorted(objetos,key=lambda x:x["caminho"].casefold())


def baixar_storage(bucket, caminho):
    administrador_atual()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bucket):
        raise ErroAtualizacao("Bucket inválido.")
    if (not caminho or caminho.startswith("/") or
            ".." in caminho.split("/") or "\\" in caminho):
        raise ErroAtualizacao("Caminho inválido.")
    return _storage("GET",f"/object/authenticated/{quote(bucket,safe='')}/{quote(caminho,safe='/')}").content


def _enviar_storage(bucket,caminho,payload,*,upsert=False):
    return _storage("POST",
        f"/object/{quote(bucket,safe='')}/{quote(caminho,safe='/')}",
        dados=payload,upsert=upsert)


def substituir_storage(bucket,caminho,nome_upload,payload):
    autor=administrador_atual()
    validar_arquivo(caminho,nome_upload,payload,storage=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+",bucket):
        raise ErroAtualizacao("Bucket inválido.")
    atual=baixar_storage(bucket,caminho)
    if len(atual)>MAX_STORAGE:
        raise ErroAtualizacao("Objeto atual supera 35 MB. Backup obrigatório indisponível.")
    if _sha(atual)==_sha(payload):
        raise ErroAtualizacao("Novo arquivo é idêntico ao atual.")
    instante=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup=f"{BACKUP_STORAGE}/{instante}_{uuid.uuid4().hex[:10]}/{caminho}"
    _enviar_storage(bucket,backup,atual)
    _enviar_storage(bucket,caminho,payload,upsert=True)
    _auditar_base(autor,"atualizar_base_storage","Storage",caminho,
                  f"bucket={bucket}; backup={backup[:120]}; bytes={len(payload)}")
    return {"backup":backup, "bucket":bucket, "caminho":caminho}


def backups_storage(bucket,caminho,limite=100):
    administrador_atual()
    if not caminho_permitido(caminho,storage=True):
        raise ErroAtualizacao("Caminho de origem inválido.")
    # Lê a estrutura de backup e procura somente versões deste arquivo.
    diretorios=_storage("POST","/object/list/"+quote(bucket,safe=""),
        json_dados={"prefix":BACKUP_STORAGE+"/","limit":100,"offset":0}).json()
    resultado=[]
    for pasta in diretorios:
        if pasta.get("id") is not None:
            continue
        prefixo=BACKUP_STORAGE+"/"+str(pasta.get("name",""))+"/"
        try:
            item=_storage("POST","/object/list/"+quote(bucket,safe=""),
                json_dados={"prefix":prefixo+caminho.rsplit("/",1)[0]+"/" if "/" in caminho else prefixo,
                            "limit":100,"offset":0}).json()
            if any(prefixo+caminho == (
                    (prefixo+caminho.rsplit("/",1)[0]+"/" if "/" in caminho else prefixo)
                    + x.get("name","")) for x in item if x.get("id")):
                resultado.append(prefixo+caminho)
        except ErroAtualizacao:
            continue
    return resultado[:limite]


def restaurar_storage(bucket,caminho,backup):
    autor=administrador_atual()
    if not caminho_permitido(caminho,storage=True):
        raise ErroAtualizacao("Caminho inválido.")
    if (not backup.startswith(BACKUP_STORAGE+"/") or
            not backup.endswith("/"+caminho) or
            ".." in backup.split("/")):
        raise ErroAtualizacao("Backup não corresponde ao objeto selecionado.")
    antigo=baixar_storage(bucket,backup)
    atual=baixar_storage(bucket,caminho)
    if len(antigo)>MAX_STORAGE or len(atual)>MAX_STORAGE:
        raise ErroAtualizacao("Objeto ultrapassa o limite seguro de restauração.")
    instante=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    novo_backup=f"{BACKUP_STORAGE}/{instante}_{uuid.uuid4().hex[:10]}/{caminho}"
    _enviar_storage(bucket,novo_backup,atual)
    _enviar_storage(bucket,caminho,antigo,upsert=True)
    _auditar_base(autor,"restaurar_base_storage","Storage",caminho,
                   f"bucket={bucket}; backup_origem={backup[:110]}")
    return novo_backup


def exportar_tabela(tabela,limite=10000):
    administrador_atual()
    if tabela not in TABELAS_CONSULTA:
        raise ErroAtualizacao("Tabela não autorizada para consulta.")
    registros=[]
    for offset in range(0,limite,1000):
        grupo=_tabela(tabela,params={"select":"*","limit":"1000","offset":str(offset)})
        if not isinstance(grupo,list):
            raise ErroAtualizacao("Resposta inválida do banco.")
        registros.extend(grupo)
        if len(grupo)<1000:
            break
    return registros


def substituir_tabela_pocos(linhas):
    autor=administrador_atual()
    if not isinstance(linhas,list) or not 1<=len(linhas)<=MAX_REGISTROS_TABELA:
        raise ErroAtualizacao("Quantidade inválida de registros.")
    if any(set(linha)!=set(COLUNAS_POCOS) for linha in linhas):
        raise ErroAtualizacao("Colunas da importação não correspondem ao cadastro de poços.")
    retorno=_tabela("rpc/prisma_atualizacoes_substituir_pocos_tx",method="POST",
                    payload={"p_autor":autor,"p_linhas":linhas})
    if not isinstance(retorno,dict) or retorno.get("concluido") is not True:
        raise ErroAtualizacao("Banco não confirmou a substituição.")
    return retorno


def listar_backups_tabela():
    administrador_atual()
    return _tabela("prisma_atualizacoes_backups",params={
        "select":"id,tabela,total_registros,motivo,criado_em",
        "order":"id.desc","limit":"30"})


def restaurar_tabela_pocos(backup_id):
    autor=administrador_atual()
    try:
        numero=int(backup_id)
    except (ValueError,TypeError) as exc:
        raise ErroAtualizacao("Identificador de backup inválido.") from exc
    if numero<1:
        raise ErroAtualizacao("Backup inválido.")
    resultado=_tabela("rpc/prisma_atualizacoes_restaurar_pocos_tx",method="POST",
        payload={"p_autor":autor,"p_backup_id":numero})
    if not isinstance(resultado,dict) or resultado.get("concluido") is not True:
        raise ErroAtualizacao("Restauração não confirmada.")
    return resultado
