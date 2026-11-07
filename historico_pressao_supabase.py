"""Leitura SOMENTE-LEITURA do histórico mensal PRISMA em Supabase Storage.

Nenhuma rotina deste arquivo grava, modifica ou exclui arquivos.
Requer ativação explícita no Streamlit: PRISMA_HISTORICO_LEITURA_ATIVA = true.
O cliente Supabase é executado no servidor Streamlit; a chave administrativa
NUNCA é exposta no HTML, navegador, URLs ou logs.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse
import math
import re
import unicodedata

import requests
import streamlit as st
from openpyxl import load_workbook

BUCKET = "historico-prisma"
PASTAS = {
    "1": "modulo_1_baixa_pressao",
    "2": "modulo_2_historico_pressoes",
}
COLUNAS_M2 = ["ID", "Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observação"]
LIMITE_ARQUIVO = 20 * 1024 * 1024
INICIO_ANO = 2026
CACHE_SEGUNDOS = 300


class ErroHistorico(RuntimeError):
    """Evita interpretar uma leitura parcial como histórico completo."""


def ativo() -> bool:
    try:
        flag = st.secrets.get("PRISMA_HISTORICO_LEITURA_ATIVA", False)
    except (OSError, KeyError):
        return False
    return str(flag).strip().lower() in {"true", "1", "yes", "sim"}


def _normalizar(nome: object) -> str:
    texto = unicodedata.normalize("NFKD", str(nome or "").strip().lower())
    return "".join(ch for ch in texto if not unicodedata.combining(ch) and ch.isalnum())


def _configuracao() -> tuple[str, str]:
    try:
        url = str(st.secrets.get("PRISMA_SUPABASE_URL", "")).strip().rstrip("/")
        chave = str(
            st.secrets.get("PRISMA_SUPABASE_SECRET_KEY", "")
            or st.secrets.get("PRISMA_SUPABASE_SERVICE_ROLE_KEY", "")
        ).strip()
    except (OSError, KeyError) as exc:
        raise ErroHistorico("Supabase não configurado nos secrets do Streamlit.") from exc
    parsed = urlparse(url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".supabase.co"):
        raise ErroHistorico("URL do Supabase inválida no Streamlit.")
    if not chave:
        raise ErroHistorico("Chave administrativa do Supabase ausente no Streamlit.")
    return url, chave


def _requisicao(method: str, url: str, chave: str, **kwargs):
    # Não coloca a chave em query string, cookies ou texto de erro.
    try:
        resposta = requests.request(
            method, url, headers={"apikey": chave, **kwargs.pop("headers", {})},
            timeout=25, **kwargs
        )
        if resposta.status_code >= 400:
            raise ErroHistorico(
                f"Não foi possível consultar o histórico no Supabase (HTTP {resposta.status_code})."
            )
        return resposta
    except requests.RequestException as exc:
        raise ErroHistorico("Falha de conexão com o histórico do Supabase.") from exc


@st.cache_data(ttl=CACHE_SEGUNDOS, max_entries=32, show_spinner=False)
def _listar_ano(url: str, chave: str, pasta: str, ano: int) -> list[str]:
    """Listagem restrita a arquivos de um ano, sem baixar o conteúdo do bucket."""
    prefixo = f"{pasta}/{ano}/"
    encontrados = []
    pagina = 0
    while True:
        payload = {"prefix": prefixo, "limit": 100, "offset": pagina * 100,
                   "sortBy": {"column": "name", "order": "asc"}}
        resposta = _requisicao(
            "POST", f"{url}/storage/v1/object/list/{BUCKET}", chave,
            json=payload, headers={"Content-Type": "application/json"},
        )
        try:
            itens = resposta.json()
        except ValueError as exc:
            raise ErroHistorico("Resposta inválida ao listar históricos.") from exc
        if not isinstance(itens, list):
            raise ErroHistorico("Resposta inesperada na listagem do Storage.")
        for item in itens:
            nome = str(item.get("name") or "") if isinstance(item, dict) else ""
            if re.fullmatch(rf"{ano}-(0[1-9]|1[0-2])\.xlsx", nome):
                encontrados.append(f"{prefixo}{nome}")
        if len(itens) < 100:
            break
        pagina += 1
        if pagina >= 10:
            raise ErroHistorico("Listagem de histórico excessivamente extensa.")
    return sorted(set(encontrados))


def _mes_data(texto: str) -> str | None:
    valor = str(texto or "").strip().strip("'")
    for formato in (
        "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d",
        "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(valor, formato).strftime("%Y-%m")
        except ValueError:
            continue
    if re.fullmatch(r"\d{5}(?:[.,]\d+)?", valor):
        try:
            serial = float(valor.replace(",", "."))
            if math.isfinite(serial) and 20000 <= serial <= 90000:
                return (date(1899, 12, 30) + timedelta(days=int(serial))).strftime("%Y-%m")
        except (ValueError, OverflowError):
            pass
    return None


@st.cache_data(ttl=CACHE_SEGUNDOS, max_entries=96, show_spinner=False)
def _ler_mes(url: str, chave: str, caminho: str) -> tuple[list[str], list[list[str]]]:
    rota = "/".join(quote(p, safe="") for p in [BUCKET, *caminho.split("/")])
    resposta = _requisicao("GET", f"{url}/storage/v1/object/authenticated/{rota}", chave)
    if len(resposta.content) > LIMITE_ARQUIVO:
        raise ErroHistorico("Arquivo de histórico excede o limite de segurança.")
    try:
        livro = load_workbook(BytesIO(resposta.content), read_only=True, data_only=True)
        try:
            aba = livro["Medicoes"] if "Medicoes" in livro.sheetnames else livro.active
            linhas = list(aba.iter_rows(values_only=True))
        finally:
            livro.close()
    except (OSError, ValueError, KeyError) as exc:
        raise ErroHistorico("Arquivo histórico inválido ou corrompido.") from exc
    if len(linhas) < 2:
        raise ErroHistorico("Arquivo mensal sem registros; confira o arquivamento.")
    cabecalho = ["" if x is None else str(x).strip() for x in linhas[0]]
    if len(set(map(_normalizar, cabecalho))) != len(cabecalho):
        raise ErroHistorico("O cabeçalho do arquivo contém campos duplicados.")
    registros = [
        ["" if v is None else str(v) for v in linha[:len(cabecalho)]]
        + [""] * max(0, len(cabecalho) - len(linha))
        for linha in linhas[1:]
    ]
    chaves = list(map(_normalizar, cabecalho))
    if "data" not in chaves:
        raise ErroHistorico("Arquivo mensal sem coluna de data.")
    competencia = PurePosixPath(caminho).stem
    for linha in registros:
        if _mes_data(linha[chaves.index("data")]) != competencia:
            raise ErroHistorico("Arquivo histórico contém medições de outra competência ou data inválida.")
    return cabecalho, registros


def _arquivos(modulo: str) -> list[tuple[str, list[str], list[list[str]]]]:
    if modulo not in PASTAS:
        raise ErroHistorico("Módulo não reconhecido.")
    url, chave = _configuracao()
    ano_atual = datetime.now().year
    if ano_atual < INICIO_ANO or ano_atual > INICIO_ANO + 20:
        raise ErroHistorico("Período do histórico fora da faixa suportada.")
    resultados = []
    for ano in range(INICIO_ANO, ano_atual + 1):
        for caminho in _listar_ano(url, chave, PASTAS[modulo], ano):
            cabecalho, registros = _ler_mes(url, chave, caminho)
            resultados.append((caminho, cabecalho, registros))
    return resultados


def _linha_dict(cabecalho: list[str], valores: list[str]) -> dict[str, str]:
    return {
        _normalizar(nome): (str(valores[i]) if i < len(valores) else "")
        for i, nome in enumerate(cabecalho)
    }


def _assinatura_conteudo(linha: dict[str, str], campos: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(linha.get(_normalizar(campo), "").strip() for campo in campos)


def combinar_modulo1(valores_sheet: list[list[str]]) -> tuple[list[list[str]], list[bool]]:
    """O Sheets tem preferência; acrescenta apenas medições ausentes, sem perder duplicidades."""
    if not ativo():
        return valores_sheet, [False] * max(0, len(valores_sheet) - 1)
    if not valores_sheet or not valores_sheet[0]:
        raise ErroHistorico("Módulo 1: cabeçalho do Google Sheets indisponível.")
    cabecalho = [str(v or "") for v in valores_sheet[0]]
    campos = ["ID", "Data", "Municipio", "Bairro", "Latitude", "Longitude",
              "Pressao_MCA", "Observacao", "Matricula"]
    if not set(map(_normalizar, campos)).issubset(set(map(_normalizar, cabecalho))):
        raise ErroHistorico("Módulo 1: cabeçalho do Sheets divergente.")
    atuais = [list(r) for r in valores_sheet[1:]]
    marcadores = [False] * len(atuais)
    # Mesma ID de medição prevalece no Sheets; sem ID, identidade por conteúdo.
    def chave(linha: dict[str, str]):
        identificador = linha.get("id", "").strip()
        return ("id", identificador) if identificador else (
            "conteudo", *_assinatura_conteudo(
                linha, ("Data", "Municipio", "Bairro", "Latitude", "Longitude",
                        "Pressao_MCA", "Observacao", "Matricula")
            )
        )
    presentes = Counter(chave(_linha_dict(cabecalho, r)) for r in atuais if any(str(v).strip() for v in r))
    ja_arquivados = Counter()
    for caminho, colunas, registros in _arquivos("1"):
        if not set(map(_normalizar, campos)).issubset(set(map(_normalizar, colunas))):
            raise ErroHistorico("Módulo 1: cabeçalho de arquivo histórico divergente.")
        for dados in registros:
            linha = _linha_dict(colunas, dados)
            identidade = chave(linha)
            ja_arquivados[identidade] += 1
            if ja_arquivados[identidade] <= presentes[identidade]:
                continue
            atuais.append([linha.get(_normalizar(coluna), "") for coluna in cabecalho])
            marcadores.append(True)
    return [cabecalho, *atuais], marcadores


def adicionar_modulo2(registros_sheet: list[dict]) -> list[dict]:
    """Acrescenta arquivos M2 como entradas sem linha física editável."""
    if not ativo():
        return registros_sheet
    campos = COLUNAS_M2[1:]
    colunas_exigidas = set(map(_normalizar, COLUNAS_M2))
    ids_atuais = set()
    conteudos_atuais = Counter()
    conteudos_legados = Counter()
    for r in registros_sheet:
        # Os dicionários do Módulo 2 têm colunas com acentos e maiúsculas;
        # normalizar chaves é necessário para comparar com os XLSX arquivados.
        normalizado = {_normalizar(campo): str(valor or "")
                       for campo, valor in r.items()}
        conteudo = _assinatura_conteudo(normalizado, tuple(campos))
        if r.get("_historico_id"):
            ids_atuais.add(str(r["_historico_id"]).strip())
        else:
            conteudos_legados[conteudo] += 1
        conteudos_atuais[conteudo] += 1
    ids_arquivo = set()
    vistos_sem_id = Counter()
    vistos_com_id = Counter()
    adicionais = []
    for _, cabecalho, linhas in _arquivos("2"):
        if not colunas_exigidas.issubset(set(map(_normalizar, cabecalho))):
            raise ErroHistorico("Módulo 2: cabeçalho de arquivo histórico divergente.")
        for valores in linhas:
            dados = _linha_dict(cabecalho, valores)
            identificador = dados.get("id", "").strip()
            conteudo = _assinatura_conteudo(dados, tuple(campos))
            if identificador:
                if identificador in ids_arquivo:
                    raise ErroHistorico("Módulo 2: ID duplicado entre os meses arquivados.")
                ids_arquivo.add(identificador)
                if identificador in ids_atuais:
                    continue
                vistos_com_id[conteudo] += 1
                if vistos_com_id[conteudo] <= conteudos_legados[conteudo]:
                    continue
            else:
                vistos_sem_id[conteudo] += 1
                if vistos_sem_id[conteudo] <= conteudos_atuais[conteudo]:
                    continue
            item = {
                destino: dados.get(_normalizar(destino), "")
                for destino in campos
            }
            item["_linha_real"] = -1
            item["_layout_linha"] = "arquivo"
            item["_arquivo_historico"] = True
            adicionais.append(item)
    return registros_sheet + adicionais
