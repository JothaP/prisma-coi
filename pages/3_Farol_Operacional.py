import io
import csv
import json
import os
import re
import math
import hashlib
import zipfile
import sqlite3
import threading
import tempfile
import time as tempo_execucao
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from html import escape as html_escape
from zoneinfo import ZoneInfo
import unicodedata
from datetime import datetime, date, timedelta, time as horario
import xml.etree.ElementTree as ET

import gspread
import pandas as pd
import numpy as np
import streamlit as st
import folium
import plotly.express as px
from shapely import wkb
from shapely.ops import transform as transformar_geometria
from pyproj import CRS, Transformer
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Image, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.graphics.shapes import Drawing, Circle, String, Rect, PolyLine
from reportlab.pdfbase.ttfonts import TTFont

from google.oauth2.service_account import Credentials
from streamlit_folium import st_folium
from branca.element import Element

# Importações de módulos auxiliares na raiz do repositório Streamlit.
_RAIZ_IMPORT = str(Path(__file__).resolve().parent.parent)
if _RAIZ_IMPORT not in sys.path:
    sys.path.insert(0, _RAIZ_IMPORT)

from auth import verificar_autenticacao, tem_acesso_modulo, pode_executar_acao
from farol_graficos_temporarios import (
    data_hoje as dia_graficos, indice_do_dia, salvar_grafico, excluir_grafico,
    chave_logger, rotulo_logger, filtrar_loggers,
)
from integracao_acompanhamento_producao import (chave_concentracao, localizar_vinculo, listar_vinculos, dados_acompanhamento, historico_evento, texto_pdf, sugerir_recuperacoes, recuperar_vinculo_confirmado, identificar_continuidades_automaticas, continuar_vinculo_automaticamente)
from farol_acomp_supabase_backend import fonte_supabase
from acompanhamento_operacional_farol import popup_acompanhamento_farol
from farol_mesclagem_operacional import (
    CHAVE_MODO as CHAVE_MODO_MESCLAGEM,
    CHAVE_POLIGONO as CHAVE_POLIGONO_MESCLAGEM,
    listar_mesclas as listar_mesclas_farol,
    aplicar_mesclas as aplicar_mesclas_farol,
    desenhar_areas_mescladas,
    adicionar_desenho_mapa,
    extrair_desenho_folium,
    renderizar_painel_mescla,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

st.set_page_config(
    page_title="Farol Operacional — Módulo 3",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("3")

# Módulo 3 — produção. Perfil operador: somente leitura.
if not verificar_autenticacao():
    st.error("Sessão não autenticada. Entre pelo Hub Central.")
    st.stop()

PERFIL_FAROL = str(st.session_state.get("perfil", "")).strip().lower()
if PERFIL_FAROL not in {"admin", "usuario", "operador"}:
    st.error("Seu perfil não está autorizado a acessar o Farol Operacional.")
    st.stop()

PODE_EDITAR_FAROL = pode_executar_acao("3", "editar")

def exigir_edicao_farol():
    """Bloqueia mutações mesmo quando funções são chamadas sem seus botões."""
    if not PODE_EDITAR_FAROL:
        raise PermissionError("Seu perfil permite apenas consultar o Farol Operacional.")

# Fontes independentes: Farol (mapa/O.S./eventos) e Acompanhamento (fichas/histórico).
SPREADSHEET_ID = str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip()
SPREADSHEET_ID_DEMO_CONHECIDO = "1plvaCyZ23zAIjasrFnJxZm9Q-jB85AbCtSjIizhuoKg"
ID_PLANILHA_ACOMP = str(st.secrets.get("acompanhamento_operacional_spreadsheet_id", "")).strip()
if not SPREADSHEET_ID or SPREADSHEET_ID == SPREADSHEET_ID_DEMO_CONHECIDO:
    st.error("Configure farol_producao_spreadsheet_id com o ID da planilha de produção.")
    st.stop()
if not ID_PLANILHA_ACOMP or ID_PLANILHA_ACOMP == SPREADSHEET_ID:
    st.error("Configure acompanhamento_operacional_spreadsheet_id com uma OUTRA planilha Google Sheets, independente da planilha do Farol.")
    st.stop()

# A ficha deve ser aberta exclusivamente pelo popup da concentração.
# Remove sinalizadores de navegação da versão antiga (tela com abas).
# Eles podem sobreviver na sessão após a implantação de uma nova versão.
st.session_state.pop("farol_exibir_acompanhamento", None)
st.session_state.pop("acomp_farol_contexto", None)

NOME_ABA_POCOS = "POCOS"
NOME_ABA_LOGGERS = "LOGGERS"
NOME_ABA_PONTOS = "PONTOS"
NOME_ABA_EVENTOS = "EVENTOS"
NOME_ABA_CONTROLE = "CONTROLE_FAROL"
NOME_ABA_OBSERVACOES = "OBS_CONCENTRACOES"
NOME_ABA_AREAS_PROTEGIDAS = "FAROL_AREAS_EDITADAS"
NOME_ABA_ATUALIZACOES = "FAROL_ATUALIZACOES"
NOME_ABA_HISTORICO_V2 = "FAROL_HISTORICO_V2"
CABECALHO_HISTORICO_V2 = ["Chave", "Valor"]
DIAS_HISTORICO_V2 = 8
FUSO_FAROL = ZoneInfo("America/Fortaleza")  # Teresina: UTC-3
RESET_HORARIO = horario(0, 1)
_RESET_LOCK = threading.Lock()

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRETORIO_KMZ = os.path.join(RAIZ_PROJETO, "geodados", "municipios")
DIRETORIO_DADOS = os.path.join(RAIZ_PROJETO, "dados")
ARQUIVO_POCOS_CSV = os.path.join(DIRETORIO_DADOS, "pocos.csv")
ARQUIVO_LOGGERS_CSV = os.path.join(DIRETORIO_DADOS, "loggers.csv")
ARQUIVO_KMZ_PADRAO = os.path.join(RAIZ_PROJETO, "TERESINA.kmz")  # Compatibilidade com instalação anterior
RAIO_OPERACIONAL_PADRAO = 750
RAIO_CONCENTRACAO_PADRAO = 750
MINIMO_OS_CONCENTRACAO_PADRAO = 3

CABECALHO_POCOS = [
    "ID_POCO",
    "IDENTIFICACAO_ATIVO",
    "NOME_POCO",
    "MUNICIPIO",
    "LATITUDE",
    "LONGITUDE",
]

CABECALHO_LOGGERS = [
    "ID_LOGGER",
    "IDENTIFICACAO_ATIVO",
    "ENDERECO",
    "MUNICIPIO",
    "LATITUDE",
    "LONGITUDE",
]

CABECALHO_PONTOS = [
    "Nº da O.S",
    "Matrícula",
    "Cidade",
    "Bairro",
    "Latitude",
    "Longitude",
    "Status OS",
    "Dt. Emissão",
    "Serviço Executado",
    "Endereço",
]

CABECALHO_EVENTOS = [
    "Data",
    "Controlador",
    "Status",
    "Unidade",
    "Cidade",
    "Regional",
    "Código do Ativo",
    "Serviço",
    "Equipe",
    "Supervisor",
    "Líder",
    "Oracle Field",
    "SAP",
    "Protocolo",
    "Link de Localização",
    "Início",
    "Prev. Término",
    "Término Real",
    "Tempo de Atendimento",
    "Prazo de Normalização",
    "Áreas Impactadas",
    "Economias Afetadas",
    "% Economias",
    "Governança",
    "Descrição do Serviço",
    "Data de Criação",
]


# ============================================================
# NORMALIZAÇÃO / UTILITÁRIOS
# ============================================================

def normalizar_texto(valor):
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def normalizar_cabecalho(valor):
    texto = normalizar_texto(valor)
    texto = texto.replace("º", "O")
    texto = re.sub(r"[^A-Z0-9]+", "", texto)
    return texto


def limpar_valor(valor):
    if pd.isna(valor):
        return ""
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return valor.strftime("%d/%m/%Y %H:%M:%S") if hasattr(valor, "hour") else valor.strftime("%d/%m/%Y")
    texto = str(valor).strip()
    if texto.lower() in {"nan", "nat", "none"}:
        return ""
    return texto


def parse_float(valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    texto = str(valor).strip().replace(" ", "")
    if not texto:
        return None
    try:
        # Trata 5,1234 e 5.1234, além de 1.234,56.
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        else:
            texto = texto.replace(",", ".")
        return float(texto)
    except Exception:
        return None


def novo_id(prefixo):
    return f"{prefixo}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


def distancia_metros(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except Exception:
        return None

    raio = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * raio * math.asin(min(1, math.sqrt(a)))


def distancia_formatada(metros):
    if metros is None:
        return ""
    if metros < 1000:
        return f"{metros:.0f} m"
    return f"{metros / 1000:.2f} km"


def slug_hash(*valores):
    base = "|".join(normalizar_texto(v) for v in valores)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def limpar_nome_bairro(valor):
    """Chave única para O.S., eventos e feições, sem o rótulo genérico BAIRRO.

    Preserva o nome original de exibição em `bairro["nome"]` e remove apenas
    o prefixo isolado no começo, nunca a palavra dentro do nome do bairro.
    """
    texto = normalizar_texto(valor)
    texto = re.sub(r"\s*\(\s*PARCIAL\s*\)\s*$", "", texto)
    texto = re.sub(
        r"^(?:BAIRRO|SUB[\s-]*AREA|SUB[\s-]*REGIAO)"
        r"(?:\s*[-–—:]\s*|\s+)", "", texto
    )
    # Trata Villa Inglesa e Vila Inglesa como a mesma localidade, sem
    # aproximar nomes de bairros diferentes nem alterar o rótulo original.
    texto = re.sub(r"\bVILLA\b", "VILA", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" \t.,;:!?")
    return texto


def normalizar_localidade_busca(valor):
    """Normaliza separadores e grafia para comparar nomes completos no endereço."""
    texto = re.sub(r"[^A-Z0-9]+", " ", normalizar_texto(valor))
    texto = re.sub(r"\bVILLA\b", "VILA", texto)
    return re.sub(r"\s+", " ", texto).strip()


def endereco_contem_localidade(endereco, localidade):
    """Busca nome COMPLETO em limites de palavras, evitando fragmentos e ruas homônimas.

    Não confunde 'Dom Avelar' com 'Dom Avelar II' nem 'Vila Inglesa' com
    'Vila Inglesa Nova' quando a localidade exata é outra: para o primeiro
    caso, a subárea adicional requer identificação explícita. O nome de uma
    rua/avenida, sozinho, não estabelece que a O.S. pertence à localidade.
    """
    area = normalizar_localidade_busca(limpar_nome_bairro(localidade))
    texto = normalizar_localidade_busca(endereco)
    if not area or not texto or len(area) < 5:
        return False
    # Rejeita tokens genéricos que não individualizam uma localidade.
    if area in {"BAIRRO", "REGIAO", "AREA", "SETOR", "TODOS", "PARCIAL"}:
        return False
    padrao = r"(?<![A-Z0-9])" + re.escape(area) + r"(?![A-Z0-9])"
    for achado in re.finditer(padrao, texto):
        anteriores = texto[:achado.start()].split()
        posteriores = texto[achado.end():].split()
        if anteriores and anteriores[-1] in {
            "RUA", "R", "AV", "AVENIDA", "TRAVESSA", "TV",
            "ESTRADA", "RODOVIA", "ALAMEDA", "PRACA", "PCA",
        }:
            continue
        # Não confundir um conjunto com sua extensão explicitamente nomeada.
        if posteriores and posteriores[0] in {
            "I", "II", "III", "IV", "V", "1", "2", "3", "4", "5",
            "NOVO", "NOVA",
        }:
            continue
        return True
    return False


def nomes_reconhecidos_feicao(item):
    """Nomes exatos de um polígono: subárea, bairro e aliases cadastrais.

    Permite reconhecer o campo 'name' de feições adicionadas no PRISMA-GEO,
    além de NM_BAIRRO, sem aproximação textual entre localidades diferentes.
    """
    if item.get("sem_nome"):
        return set()
    valores = [
        item.get("nome_normalizado") or item.get("nome"),
        *(item.get("nomes_alternativos") or []),
    ]
    return {chave for valor in valores if (chave := limpar_nome_bairro(valor))}


def indice_nomes_bairros(bairros_geograficos, municipio):
    """Catálogo de bairros e subáreas do município, por nomes exatos."""
    cidade = normalizar_municipio(municipio)
    nomes = set()
    for item in (bairros_geograficos or []):
        if normalizar_municipio(item.get("municipio", "")) == cidade:
            nomes.update(nomes_reconhecidos_feicao(item))
    return nomes


def resolver_nome_bairro(nome, nomes_disponiveis):
    """Equivalência conservadora: nomes exatos ou prefixo genérico 'Bairro'."""
    chave = limpar_nome_bairro(nome)
    if chave in nomes_disponiveis:
        return chave
    sem_prefixo = re.sub(r"^BAIRRO\s+", "", chave).strip()
    if sem_prefixo and sem_prefixo in nomes_disponiveis:
        return sem_prefixo
    return chave


def evento_abrange_municipio(valor):
    """Identifica abrangência municipal explícita, sem inferir por bairro vazio."""
    texto = normalizar_texto(valor)
    return bool(re.search(
        r"\b(?:TODO\s+O\s+MUNICIPIO|MUNICIPIO\s+INTEIRO|"
        r"TODA\s+A\s+CIDADE|CIDADE\s+INTEIRA|"
        r"TODO\s+O\s+TERRITORIO\s+MUNICIPAL|"
        r"TODOS\s+OS\s+BAIRROS)\b", texto
    ))


def extrair_bairros_evento(valor, municipio="", bairros_geograficos=None):
    """Lê bairros informados em Áreas Impactadas e valida contra a base local.

    Reconhece o rótulo 'Áreas Afetadas' mesmo precedido pelo município.
    Desdobra 'Itararé 1 e 2' em dois bairros, independentemente da cobertura
    geográfica: cada bairro é validado separadamente ao desenhar o mapa.
    Preserva rótulos sem polígono no diagnóstico, sem inventar geometrias.
    """
    if valor is None or str(valor).strip().lower() in ("", "nan", "none"):
        return []
    nomes = indice_nomes_bairros(bairros_geograficos, municipio)
    if evento_abrange_municipio(valor):
        return [{"bairro": nome, "parcial": False, "original": "Todo o município"}
                for nome in sorted(nomes)]
    texto = str(valor).replace("\r", "\n")
    texto = re.sub(r"(?i)\bBAIRROS?\s*(?:IMPACTADOS?|AFETADOS?)?\s*:", "\n", texto)
    texto = re.sub(
        r"(?i)\bSUB[\s-]*[ÁA]REAS?\s*(?:IMPACTADAS?|AFETADAS?)?\s*:", "\n", texto
    )
    texto = re.sub(r"(?i)\b(?:ÁREAS?|AREAS?)\s+(?:IMPACTADAS?|AFETADAS?)\s*:", "\n", texto)
    partes = re.split(r"[,;\n]+", texto)
    saida = []
    vistos = set()
    for parte in partes:
        original = parte.strip(" \t-*•.,;:!?")
        if not original:
            continue
        # Cidade isolada não é automaticamente interpretada como todos os bairros.
        if municipio and normalizar_municipio(original) == normalizar_municipio(municipio):
            continue
        parcial = bool(re.search(r"\(\s*PARCIAL\s*\)", original, flags=re.I))
        chave = resolver_nome_bairro(original, nomes)
        candidatos = [(chave, original)]
        if chave not in nomes:
            # A enumeração numérica explícita é inequívoca, mesmo se um ou
            # ambos os polígonos estiverem ausentes do cadastro cartográfico.
            # Isso permite marcar o bairro disponível e diagnosticar o ausente.
            abreviado = re.fullmatch(r"(.+?)\s+(\d+)\s+E\s+(\d+)", chave)
            if abreviado:
                radical, numero_1, numero_2 = abreviado.groups()
                candidatos = [(f"{radical} {numero_1}", f"{radical} {numero_1}"),
                              (f"{radical} {numero_2}", f"{radical} {numero_2}")]
            else:
                # Separar 'Bairro São Domingos e Guabiraba' é uma intenção
                # explícita do campo; nos demais nomes só separar se TODOS os
                # componentes existirem no catálogo do mesmo município.
                pedacos = [resolver_nome_bairro(p, nomes) for p in re.split(r"\s+E\s+", chave)]
                lista_explicita = bool(re.match(r"^BAIRRO\b", normalizar_texto(original)))
                if len(pedacos) >= 2 and (lista_explicita or
                                          (nomes and all(p in nomes for p in pedacos))):
                    candidatos = [(p, p) for p in pedacos]
        for bairro, origem in candidatos:
            if bairro and bairro not in vistos:
                vistos.add(bairro)
                saida.append({"bairro": bairro, "parcial": parcial, "original": origem})
    return saida


# ============================================================
# GOOGLE SHEETS
# ============================================================

@st.cache_resource(show_spinner=False)
def obter_cliente_google():
    dados = st.secrets["gcp_json"]
    if isinstance(dados, str):
        dados = json.loads(dados)
    else:
        dados = dict(dados)
    credenciais = Credentials.from_service_account_info(
        dados,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(credenciais)


@st.cache_resource(show_spinner=False)
def obter_planilha(spreadsheet_id):
    planilha = obter_cliente_google().open_by_key(spreadsheet_id)

    return planilha


def obter_aba(nome, cabecalho):
    planilha = obter_planilha(SPREADSHEET_ID)
    if planilha.id != SPREADSHEET_ID:
        st.error("Conexão bloqueada: planilha diferente da configurada para o Farol.")
        st.stop()

    try:
        aba = planilha.worksheet(nome)

    except gspread.WorksheetNotFound:
        st.error(
            f"ABA NÃO ENCONTRADA: {nome} | "
            f"PLANILHA: {planilha.title} | "
            f"ID: {planilha.id}"
        )
        st.stop()

    valores = aba.get_all_values()
    if not valores:
        if PODE_EDITAR_FAROL:
            aba.update("A1", [cabecalho])
    elif [normalizar_cabecalho(x) for x in valores[0]] != [normalizar_cabecalho(x) for x in cabecalho]:
        # Não apaga dados existentes. Apenas garante que as colunas mínimas estejam presentes.
        cab_atual = valores[0]
        faltantes = [c for c in cabecalho if normalizar_cabecalho(c) not in {normalizar_cabecalho(x) for x in cab_atual}]
        if faltantes and PODE_EDITAR_FAROL:
            nova_linha = cab_atual + faltantes
            aba.resize(cols=max(aba.col_count, len(nova_linha)))
            aba.update("A1", [nova_linha])
    return aba


@st.cache_resource(ttl=600, show_spinner=False)
def obter_aba_para_leitura(nome, spreadsheet_id):
    """Reutiliza o objeto Worksheet; não lê toda a planilha para conferir cabeçalhos."""
    planilha = obter_planilha(SPREADSHEET_ID)
    if planilha.id != SPREADSHEET_ID:
        raise PermissionError("A planilha conectada não é a planilha autorizada do Farol.")
    return planilha.worksheet(nome)


@st.cache_data(ttl=60, show_spinner=False)
def carregar_aba(nome, cabecalho, spreadsheet_id=None):
    aba = obter_aba_para_leitura(nome, spreadsheet_id or SPREADSHEET_ID)
    # Preserva as vírgulas decimais das coordenadas (ex.: -5,18817).
    # A conversão automática do gspread interpreta a vírgula como milhar
    # e transforma -5,18817 em -518817, invalidando os marcadores.
    registros = aba.get_all_records(numericise_ignore=["all"])
    if not registros:
        return pd.DataFrame(columns=cabecalho)
    return pd.DataFrame(registros)


@st.cache_data(show_spinner=False, max_entries=8)
def carregar_cadastro_local(caminho, assinatura, cabecalho):
    """Lê o CSV versionado no GitHub; a assinatura atualiza o cache após um deploy."""
    del assinatura
    try:
        dados = pd.read_csv(caminho, sep=",", encoding="utf-8-sig", dtype=str, keep_default_na=False)
    except UnicodeDecodeError as erro:
        raise ValueError(f"Arquivo CSV precisa estar em UTF-8: {os.path.basename(caminho)}") from erro
    faltantes = [col for col in cabecalho if col not in dados.columns]
    if faltantes:
        raise ValueError(f"{os.path.basename(caminho)}: colunas ausentes: {', '.join(faltantes)}")
    return dados.reindex(columns=cabecalho).fillna("")


def carregar_cadastro_github(caminho, cabecalho):
    if not os.path.isfile(caminho):
        raise FileNotFoundError(
            f"Arquivo não encontrado: dados/{os.path.basename(caminho)}. "
            "Envie os dois CSV à pasta dados/ na raiz do repositório."
        )
    info = os.stat(caminho)
    return carregar_cadastro_local(caminho, (info.st_size, info.st_mtime_ns), tuple(cabecalho))


def invalidar_cache():
    carregar_aba.clear()
    criar_concentracoes.clear()
    resumir_acionamentos_farol.clear()
    pontos_proximos_concentracoes.clear()
    eventos_georreferenciados.clear()
    filtrar_ativos_proximos.clear()


# O reset automático de PONTOS e EVENTOS foi desativado.
# As duas abas permanecem preservadas até que um usuário autorizado
# faça uma operação explícita de importação/atualização.

@st.cache_data(ttl=30, show_spinner=False)
def carregar_observacoes_concentracoes():
    """Lê anotações persistentes; operador não cria nem altera planilhas."""
    try:
        aba = obter_planilha(SPREADSHEET_ID).worksheet(NOME_ABA_OBSERVACOES)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=["CHAVE", "MUNICIPIO", "LATITUDE", "LONGITUDE", "OBSERVACAO", "ATUALIZADO_EM", "AUTOR"])
    registros = aba.get_all_records(numericise_ignore=["all"])
    return pd.DataFrame(registros) if registros else pd.DataFrame(columns=["CHAVE", "MUNICIPIO", "LATITUDE", "LONGITUDE", "OBSERVACAO", "ATUALIZADO_EM", "AUTOR"])


def identificar_observacao(concentracao, os_grupo, observacoes, raio_m):
    """Associa observações por município e centro próximo, mesmo que o ID C001 mude."""
    cidades = sorted({normalizar_municipio(v) for v in os_grupo.get("Cidade", []) if str(v).strip()})
    municipio = cidades[0] if cidades else ""
    lat, lon = float(concentracao["LATITUDE"]), float(concentracao["LONGITUDE"])
    melhor = None
    menor_distancia = float("inf")
    for _, r in observacoes.iterrows():
        if normalizar_municipio(r.get("MUNICIPIO", "")) != municipio:
            continue
        olat = normalizar_coordenada(r.get("LATITUDE"), "lat")
        olon = normalizar_coordenada(r.get("LONGITUDE"), "lon")
        if olat is None or olon is None:
            continue
        distancia = distancia_metros(lat, lon, olat, olon)
        if distancia is not None and distancia < menor_distancia and distancia <= min(float(raio_m), 300):
            menor_distancia, melhor = distancia, r
    return municipio, (str(melhor.get("CHAVE", "")) if melhor is not None else ""), (str(melhor.get("OBSERVACAO", "")) if melhor is not None else "")


def salvar_observacao_concentracao(chave, municipio, lat, lon, observacao):
    exigir_edicao_farol()
    planilha = obter_planilha(SPREADSHEET_ID)
    cab = ["CHAVE", "MUNICIPIO", "LATITUDE", "LONGITUDE", "OBSERVACAO", "ATUALIZADO_EM", "AUTOR"]
    try:
        aba = planilha.worksheet(NOME_ABA_OBSERVACOES)
    except gspread.WorksheetNotFound:
        aba = planilha.add_worksheet(title=NOME_ABA_OBSERVACOES, rows=1000, cols=len(cab))
        aba.update(range_name="A1:G1", values=[cab], value_input_option="RAW")
    if not chave:
        chave = hashlib.sha256(f"{municipio}|{lat:.5f}|{lon:.5f}|{datetime.now(FUSO_FAROL).isoformat()}".encode()).hexdigest()[:20]
    valores = [chave, municipio, str(lat), str(lon), observacao.strip(), datetime.now(FUSO_FAROL).strftime("%d/%m/%Y %H:%M"), st.session_state.get("usuario_logado", "")]
    linhas = aba.get_all_values()
    for i, linha in enumerate(linhas[1:], start=2):
        if linha and linha[0] == chave:
            aba.update(range_name=f"A{i}:G{i}", values=[valores], value_input_option="RAW")
            carregar_observacoes_concentracoes.clear()
            return
    aba.append_row(valores, value_input_option="RAW")
    carregar_observacoes_concentracoes.clear()


@st.cache_data(ttl=60, show_spinner=False)
def carregar_datas_atualizacao():
    """Consulta apenas duas linhas de metadados; nunca lê as bases para obter datas."""
    try:
        aba = obter_planilha(SPREADSHEET_ID).worksheet(NOME_ABA_ATUALIZACOES)
        linhas = aba.get_all_values()
    except gspread.WorksheetNotFound:
        return {}
    except Exception:
        return {}
    return {linha[0].strip().upper(): linha[1].strip()
            for linha in linhas[1:] if len(linha) >= 2 and linha[0].strip()}


def registrar_atualizacao_base(tipo):
    """Persiste data/hora real de importação, inclusive entre sessões e reinícios."""
    exigir_edicao_farol()
    if tipo not in {"PONTOS", "EVENTOS"}:
        raise ValueError("Tipo de base desconhecido.")
    planilha = obter_planilha(SPREADSHEET_ID)
    try:
        aba = planilha.worksheet(NOME_ABA_ATUALIZACOES)
    except gspread.WorksheetNotFound:
        aba = planilha.add_worksheet(title=NOME_ABA_ATUALIZACOES, rows=10, cols=2)
        aba.update(range_name="A1:B1", values=[["BASE", "ATUALIZADO_EM"]], value_input_option="RAW")
    linhas = aba.get_all_values()
    momento = datetime.now(FUSO_FAROL).strftime("%d/%m/%Y %H:%M:%S")
    for numero_linha, linha in enumerate(linhas[1:], start=2):
        if linha and linha[0].strip().upper() == tipo:
            aba.update(range_name=f"A{numero_linha}:B{numero_linha}",
                       values=[[tipo, momento]], value_input_option="RAW")
            carregar_datas_atualizacao.clear()
            return
    aba.append_row([tipo, momento], value_input_option="RAW")
    carregar_datas_atualizacao.clear()


def exibir_atualizacoes_abaixo_mapa():
    datas = carregar_datas_atualizacao()
    os_em = datas.get("PONTOS", "Sem importação registrada")
    eventos_em = datas.get("EVENTOS", "Sem importação registrada")
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;gap:8px 26px;margin:4px 0 14px;'
        'font-size:13px;font-weight:600;color:#1558b0;">'
        f'<span>O.S. — Atualizado em {os_em}</span>'
        f'<span>Eventos — Atualizado em {eventos_em}</span>'
        '</div>', unsafe_allow_html=True,
    )


def limpar_dados_operacionais_admin():
    """Limpa PONTOS, EVENTOS e correções manuais de áreas, preservando cabeçalhos."""
    if not verificar_autenticacao() or str(st.session_state.get("perfil", "")).strip().lower() != "admin" or not pode_executar_acao("3", "excluir"):
        raise PermissionError("Somente administradores podem limpar os dados operacionais.")
    planilha = obter_planilha(SPREADSHEET_ID)
    resultados = []
    abas = (
        (NOME_ABA_PONTOS, CABECALHO_PONTOS),
        (NOME_ABA_EVENTOS, CABECALHO_EVENTOS),
        (NOME_ABA_AREAS_PROTEGIDAS, ["CHAVE_EVENTO", "AREAS_IMPACTADAS", "ATUALIZADO_EM", "AUTOR"]),
        (NOME_ABA_ATUALIZACOES, ["BASE", "ATUALIZADO_EM"]),
    )
    for nome, cabecalho in abas:
        try:
            aba = planilha.worksheet(nome)
        except gspread.WorksheetNotFound:
            # A aba auxiliar só existe após a primeira edição manual.
            if nome in {NOME_ABA_AREAS_PROTEGIDAS, NOME_ABA_ATUALIZACOES}:
                continue
            raise
        # Apaga registros, não a aba, e mantém os cabeçalhos para futuras importações.
        aba.clear()
        aba.update(range_name="A1", values=[cabecalho], value_input_option="RAW")
        resultados.append(nome)
    invalidar_cache()
    carregar_datas_atualizacao.clear()
    return resultados


@st.dialog("🗑️ Limpar dados operacionais")
def modal_limpar_dados_operacionais():
    if str(st.session_state.get("perfil", "")).strip().lower() != "admin" or not verificar_autenticacao():
        st.error("Operação exclusiva do administrador.")
        st.stop()
    st.warning(
        "Esta ação excluirá todas as O.S. de PONTOS, os eventos de EVENTOS e "
        "as correções manuais de FAROL_AREAS_EDITADAS e os registros de atualização. "
        "Não é possível desfazer pela ferramenta."
    )
    st.caption("Poços, loggers, observações das concentrações e demais abas serão preservados.")
    confirmado = st.checkbox(
        "Confirmo que desejo apagar os registros de PONTOS, EVENTOS e FAROL_AREAS_EDITADAS.",
        key="confirmar_limpeza_farol",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancelar", width="stretch", key="cancelar_limpeza_farol"):
            st.rerun()
    with c2:
        if st.button("🗑️ Limpar agora", type="primary", disabled=not confirmado,
                     width="stretch", key="executar_limpeza_farol"):
            try:
                abas = limpar_dados_operacionais_admin()
                st.session_state["farol_mensagem_limpeza"] = "Dados removidos das abas: " + ", ".join(abas) + "."
                st.rerun()
            except Exception as erro:
                invalidar_cache()
                st.error(f"Falha durante a limpeza: {erro}. Confira o conteúdo das abas antes de repetir.")


def garantir_cabecalho_exato(nome, cabecalho):
    aba = obter_aba(nome, cabecalho)
    valores = aba.get_all_values()
    if not valores:
        if PODE_EDITAR_FAROL:
            aba.update("A1", [cabecalho])
    return aba


def append_dataframe(nome, cabecalho, df):
    exigir_edicao_farol()
    if df is None or df.empty:
        return 0
    aba = garantir_cabecalho_exato(nome, cabecalho)
    df = df.reindex(columns=cabecalho).fillna("")
    linhas = [[limpar_valor(v) for v in row] for row in df.itertuples(index=False, name=None)]
    if linhas:
        aba.append_rows(linhas, value_input_option="USER_ENTERED")
    invalidar_cache()
    return len(linhas)


def substituir_aba(nome, cabecalho, df):
    exigir_edicao_farol()
    aba = garantir_cabecalho_exato(nome, cabecalho)
    df = df.reindex(columns=cabecalho).fillna("")
    linhas = [cabecalho] + [[limpar_valor(v) for v in row] for row in df.itertuples(index=False, name=None)]
    aba.clear()
    aba.resize(rows=max(1000, len(linhas) + 20), cols=max(len(cabecalho) + 5, 20))
    # PONTOS: RAW preserva o ponto decimal como texto literal.
    # USER_ENTERED interpreta o ponto como separador de milhar em planilhas pt-BR,
    # convertendo -5.038017 em -5.038.017 e invalidando a coordenada.
    modo_escrita = "RAW" if nome == NOME_ABA_PONTOS else "USER_ENTERED"
    aba.update("A1", linhas, value_input_option=modo_escrita)
    invalidar_cache()


# ============================================================
# HISTÓRICO V2 — ETAPA 1 (apenas O.S.; eventos preservados)
# ============================================================

def data_os_historico(valor):
    """Lê Dt. Emissão sem descartar datas sem hora; não inventa datas ausentes."""
    if isinstance(valor, (datetime, date, pd.Timestamp)):
        try:
            return pd.Timestamp(valor).date()
        except (ValueError, TypeError):
            return None
    texto = str(valor or "").strip()
    if not texto or texto.lower() in {"nan", "none", "nat", "-"}:
        return None
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    try:
        valor_data = pd.to_datetime(texto, dayfirst=True, errors="coerce")
        return valor_data.date() if pd.notna(valor_data) else None
    except (ValueError, TypeError, OverflowError):
        return None


def janela_historico_v2(hoje=None):
    hoje = hoje or datetime.now(FUSO_FAROL).date()
    return hoje - timedelta(days=DIAS_HISTORICO_V2 - 1), hoje


@st.cache_data(ttl=180, show_spinner=False)
def ler_controle_historico_v2():
    """Lê controle do histórico com cache para evitar exceder a quota do Sheets.

    Não confunde erros de acesso/quota com a ausência da aba: retornar {}
    em caso de falha poderia desabilitar indevidamente as regras de importação.
    """
    try:
        # Worksheet compartilhado em cache: evita consultar metadados a cada rerun.
        aba = obter_aba_para_leitura(NOME_ABA_HISTORICO_V2, SPREADSHEET_ID)
        linhas = aba.get_all_values()
        return {r[0]: r[1] for r in linhas[1:] if len(r) >= 2}
    except gspread.exceptions.WorksheetNotFound:
        return {}


def gravar_controle_historico_v2(controle):
    exigir_edicao_farol()
    planilha = obter_planilha(SPREADSHEET_ID)
    try:
        aba = planilha.worksheet(NOME_ABA_HISTORICO_V2)
    except gspread.exceptions.WorksheetNotFound:
        aba = planilha.add_worksheet(title=NOME_ABA_HISTORICO_V2, rows=30, cols=3)
    linhas = [CABECALHO_HISTORICO_V2] + [[k, str(v)] for k, v in sorted(controle.items())]
    aba.update("A1", linhas, value_input_option="RAW")
    # A próxima consulta precisa refletir a mudança recém-gravada.
    ler_controle_historico_v2.clear()


def validar_importacao_diaria_v2(df, hoje=None):
    """Aceita O.S. de qualquer dia da janela móvel de oito dias (inclusive).

    Valida o lote completo antes de gravar PONTOS. Assim, dias sem operação
    podem ser importados depois, inclusive em um mesmo lote com datas mistas.
    Rejeita datas ausentes/inválidas, anteriores à janela ou futuras.
    """
    hoje = hoje or datetime.now(FUSO_FAROL).date()
    inicio, fim = janela_historico_v2(hoje)
    if df.empty:
        raise ValueError("O arquivo não contém O.S. para importar.")
    datas = df["Dt. Emissão"].map(data_os_historico)
    invalidas = int(datas.isna().sum())
    anteriores = int(datas.map(lambda d: d is not None and d < inicio).sum())
    futuras = int(datas.map(lambda d: d is not None and d > fim).sum())
    if invalidas or anteriores or futuras:
        raise ValueError(
            f"Importação interrompida: {invalidas} O.S. sem data válida, "
            f"{anteriores} anterior(es) à janela e {futuras} com data futura. "
            f"Período permitido: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y} "
            "(8 dias corridos, inclusive). Nenhuma O.S. deste lote foi gravada."
        )
    return df


def filtrar_pontos_historico_v2(df, inicio, fim):
    if df.empty:
        return df.copy()
    datas = df["Dt. Emissão"].map(data_os_historico)
    return df.loc[datas.map(lambda d: d is not None and inicio <= d <= fim)].copy()


def executar_limpeza_historico_v2(controle, hoje=None):
    """Executa no máximo uma vez por dia lógico, sem apagar datas inválidas.

    Em caso de dados sem data válida, falha fechado: não exclui nada.
    EVENTOS nunca são excluídos nesta etapa.
    """
    exigir_edicao_farol()
    hoje = hoje or datetime.now(FUSO_FAROL).date()
    if controle.get("ativo") != "SIM" or controle.get("ultima_limpeza") == hoje.isoformat():
        return 0
    inicio, _ = janela_historico_v2(hoje)
    base = carregar_aba(NOME_ABA_PONTOS, CABECALHO_PONTOS)
    if base.empty:
        removidos = 0
    else:
        datas = base["Dt. Emissão"].map(data_os_historico)
        if datas.isna().any():
            raise ValueError("Existem O.S. sem data válida em PONTOS; limpeza suspensa para evitar perda de dados.")
        manter = datas.map(lambda d: inicio <= d <= hoje)
        removidos = int((~manter).sum())
        if removidos:
            substituir_aba(NOME_ABA_PONTOS, list(base.columns), base.loc[manter].copy())
    controle = dict(controle)
    controle["ultima_limpeza"] = hoje.isoformat()
    gravar_controle_historico_v2(controle)
    return removidos


def ativar_historico_v2():
    """Ativação explícita: marca início da retenção automática, sem bloquear retroativos."""
    exigir_edicao_farol()
    hoje = datetime.now(FUSO_FAROL).date()
    controle = ler_controle_historico_v2()
    if controle.get("ativo") == "SIM":
        return
    controle.update({"ativo": "SIM", "inicio_operacao": hoje.isoformat(),
                     "ultima_limpeza": hoje.isoformat()})
    gravar_controle_historico_v2(controle)

# ============================================================
# LEITURA DE ARQUIVOS
# ============================================================

def _ler_xlsx_exportacao_simplificada(dados):
    """Compatibilidade restrita com exportações OOXML sem a pasta xl/.

    Algumas exportações de atividades geram workbook.xml e sheet1.xml na
    raiz do ZIP. O pandas/openpyxl não reconhece esse pacote como XLSX
    convencional. Lê as células e preserva textos, sem modificar o fluxo
    de leitura das planilhas normais do Power BI.
    """
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(dados)) as pacote:
        nomes = set(pacote.namelist())
        if "workbook.xml" not in nomes or "sheet1.xml" not in nomes:
            raise ValueError("Este arquivo não corresponde à exportação XLSX alternativa conhecida.")

        compartilhados = []
        if "sharedStrings.xml" in nomes:
            raiz_strings = ET.fromstring(pacote.read("sharedStrings.xml"))
            compartilhados = ["".join(n.text or "" for n in si.iter(namespace + "t"))
                            for si in raiz_strings.findall(namespace + "si")]
        cabecalho = None
        registros = []
        with pacote.open("sheet1.xml") as arquivo_xml:
            for _evento, elemento in ET.iterparse(arquivo_xml, events=("end",)):
                if elemento.tag != namespace + "row":
                    continue
                valores = {}
                for celula in elemento.findall(namespace + "c"):
                    referencia = celula.get("r", "")
                    letras = re.match(r"[A-Z]+", referencia)
                    if letras is None:
                        continue
                    coluna = 0
                    for letra in letras.group():
                        coluna = coluna * 26 + ord(letra) - ord("A") + 1
                    indice = coluna - 1
                    tipo = celula.get("t")
                    if tipo == "inlineStr":
                        valor = "".join(n.text or "" for n in celula.iter(namespace + "t"))
                    else:
                        campo_valor = celula.find(namespace + "v")
                        valor = campo_valor.text if campo_valor is not None and campo_valor.text else ""
                        if tipo == "s" and valor:
                            valor = compartilhados[int(valor)]
                    valores[indice] = valor
                if cabecalho is None:
                    if valores:
                        tamanho = max(valores) + 1
                        cabecalho = [str(valores.get(i, "")).strip() or f"Coluna_{i + 1}"
                                     for i in range(tamanho)]
                elif valores and any(str(v).strip() for v in valores.values()):
                    registros.append([str(valores.get(i, "")) for i in range(len(cabecalho))])
                elemento.clear()
        if not cabecalho:
            raise ValueError("A exportação não contém cabeçalho de colunas.")
        return pd.DataFrame(registros, columns=cabecalho)


def ler_planilha_upload(upload):
    if upload is None:
        return pd.DataFrame()
    nome = upload.name.lower()
    dados = upload.getvalue()
    if nome.endswith(".csv"):
        for encoding in ("utf-8-sig", "latin1", "cp1252"):
            try:
                return pd.read_csv(io.BytesIO(dados), sep=None, engine="python", encoding=encoding, dtype=str)
            except Exception:
                pass
        raise ValueError("Não foi possível ler o CSV.")
    try:
        return pd.read_excel(io.BytesIO(dados), dtype=str)
    except Exception as erro:
        # Alguns relatórios XLSX exportados pelo sistema de atividades usam
        # layout OOXML simplificado; não interferir nos demais formatos.
        if nome.endswith(".xlsx") and zipfile.is_zipfile(io.BytesIO(dados)):
            with zipfile.ZipFile(io.BytesIO(dados)) as pacote:
                if "workbook.xml" in pacote.namelist() and "sheet1.xml" in pacote.namelist():
                    return _ler_xlsx_exportacao_simplificada(dados)
        raise ValueError(f"Não foi possível ler o arquivo Excel: {erro}") from erro


def localizar_coluna(df, candidatos, obrigatoria=False):
    mapa = {normalizar_cabecalho(c): c for c in df.columns}
    for candidato in candidatos:
        n = normalizar_cabecalho(candidato)
        if n in mapa:
            return mapa[n]
    for col in df.columns:
        n = normalizar_cabecalho(col)
        for candidato in candidatos:
            nc = normalizar_cabecalho(candidato)
            if nc and nc in n:
                return col
    if obrigatoria:
        raise ValueError(f"Coluna não encontrada. Procuradas: {', '.join(candidatos)}")
    return None


def mapear_colunas(df, destino):
    resultado = pd.DataFrame(index=df.index)
    for col_destino in destino:
        candidatos = [col_destino]
        if col_destino == "Nº da O.S":
            candidatos += ["N O S", "Nº OS", "OS", "NUMERO OS", "NUMERO DA OS", "ORDEM DE SERVICO"]
        elif col_destino == "Dt. Emissão":
            candidatos += ["DATA EMISSAO", "DATA DE EMISSAO", "DT EMISSAO"]
        elif col_destino == "Matrícula":
            candidatos += ["MATRICULA", "MATRÍCULA"]
        elif col_destino == "Latitude":
            candidatos += ["LAT", "LATITUDE"]
        elif col_destino == "Longitude":
            candidatos += ["LON", "LONG", "LONGITUDE"]
        elif col_destino == "Endereço":
            candidatos += ["ENDERECO DA OS", "ENDERECO DA O S",
                           "ENDERECO DE LIGACAO", "ENDERECO DA LIGACAO",
                           "LOGRADOURO"]
        elif col_destino == "Código do Ativo":
            candidatos += ["CODIGO DO ATIVO", "ATIVO", "COD ATIVO"]
        elif col_destino == "Áreas Impactadas":
            candidatos += ["AREAS IMPACTADAS", "ÁREA IMPACTADA", "BAIRROS AFETADOS"]
        elif col_destino == "Descrição do Serviço":
            candidatos += ["DESCRICAO DO SERVICO", "DESCRIÇÃO", "DESCRICAO"]

        origem = localizar_coluna(df, candidatos)
        resultado[col_destino] = df[origem].map(limpar_valor) if origem else ""
    return resultado


# Correspondência EXATA da exportação alternativa. O fluxo legado Power BI
# permanece inalterado; nomes parecidos (ex.: Nome do Solicitante) nunca são
# considerados protocolos na importação da base de atividades.
MAPA_COLUNAS_ATIVIDADES = {
    "Nº da O.S": "Cód. Protocolo Origem",
    "Matrícula": "Matrícula",
    "Cidade": "Cidade",
    "Bairro": "Bairro",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
    "Status OS": "Status da Atividade",
    "Dt. Emissão": "Início do SLA",
    "Serviço Executado": "Código/Descrição",
}


def identificar_formato_pontos(df):
    """Reconhece a origem pelas colunas específicas, sem aproximação arriscada."""
    cabecalhos = {normalizar_cabecalho(c) for c in df.columns}
    marcadores = {normalizar_cabecalho(c) for c in (
        "Cód. Protocolo Origem", "Início do SLA", "Código/Descrição")}
    reconhecidos = marcadores & cabecalhos
    if len(reconhecidos) >= 2:
        obrigatorios = {normalizar_cabecalho(c) for c in MAPA_COLUNAS_ATIVIDADES.values()}
        ausentes = [nome for nome in MAPA_COLUNAS_ATIVIDADES.values()
                    if normalizar_cabecalho(nome) not in cabecalhos]
        if ausentes:
            raise ValueError(
                "Base alternativa de atividades identificada, mas faltam colunas: "
                + ", ".join(ausentes)
                + ". Nenhuma O.S. foi importada."
            )
        return "Atividades (base alternativa)"
    return "Power BI (formato original)"


def preparar_pontos(df):
    formato = identificar_formato_pontos(df)
    if formato == "Atividades (base alternativa)":
        origens = {normalizar_cabecalho(c): c for c in df.columns}
        out = pd.DataFrame(index=df.index)
        for destino, esperado in MAPA_COLUNAS_ATIVIDADES.items():
            origem = origens[normalizar_cabecalho(esperado)]
            out[destino] = df[origem].map(limpar_valor)
        # Endereço é opcional na base alternativa e nunca substitui o
        # protocolo como chave estável de deduplicação.
        origem_endereco = localizar_coluna(
            df, ["Endereço", "Endereço da O.S.", "Endereço de Ligação", "Logradouro"]
        )
        out["Endereço"] = (
            df[origem_endereco].map(limpar_valor) if origem_endereco else ""
        )
        # A chave estável é o protocolo de origem. Não criar chaves por nome
        # do solicitante ou por endereço; isso impediria deduplicação segura.
        sem_protocolo = int(out["Nº da O.S"].eq("").sum())
        if sem_protocolo:
            raise ValueError(
                f"Base alternativa: {sem_protocolo} O.S. sem Cód. Protocolo Origem. "
                "Importação interrompida para evitar duplicações."
            )
    else:
        # Caminho Power BI original. Algumas exportações da BASE ANALÍTICA
        # incluem, depois das O.S., uma linha com o texto "Filtros aplicados:".
        # Não é O.S.: não possui protocolo nem nenhum outro campo operacional.
        # Remover SOMENTE esse rodapé evita que a validação dos oito dias
        # interprete a descrição dos filtros como uma data inválida.
        out = mapear_colunas(df, CABECALHO_PONTOS)
        outros_campos = [col for col in CABECALHO_PONTOS if col != "Dt. Emissão"]
        texto_rodape = out["Dt. Emissão"].map(
            lambda valor: normalizar_texto(valor).startswith("FILTROS APLICADOS:")
        )
        linha_sem_os = out["Nº da O.S"].eq("")
        campos_vazios = out[outros_campos].apply(
            lambda linha: all(not str(valor).strip() for valor in linha), axis=1
        )
        out = out.loc[~(texto_rodape & linha_sem_os & campos_vazios)].copy()
    for coluna, tipo in (("Latitude", "lat"), ("Longitude", "lon")):
        out[coluna] = out[coluna].map(lambda valor: normalizar_coordenada(valor, tipo))
    # Preserva as O.S. sem geolocalização para consulta e diagnóstico.
    colunas_conteudo = [c for c in CABECALHO_PONTOS if c not in ("Latitude", "Longitude")]
    out = out.loc[out[colunas_conteudo].apply(
        lambda linha: any(str(v).strip() for v in linha), axis=1
    )].copy()
    return out


def chave_ponto(row):
    os_num = normalizar_texto(row.get("Nº da O.S", ""))
    if os_num:
        return f"OS:{os_num}"
    return "HASH:" + slug_hash(*[row.get(c, "") for c in CABECALHO_PONTOS])


def preparar_eventos(df):
    out = mapear_colunas(df, CABECALHO_EVENTOS)
    out = out.loc[~out.apply(lambda r: all(not str(x).strip() for x in r), axis=1)].copy()
    return out


def chave_evento(row):
    """Protocolo identifica o evento mesmo após alterações de status e horários.

    Na ausência de protocolo, usa campos de identificação relativamente estáveis.
    """
    protocolo = normalizar_texto(row.get("Protocolo", ""))
    if protocolo:
        return "PROTOCOLO:" + protocolo
    campos = ["Data", "Código do Ativo", "Serviço", "Início", "Cidade", "Unidade"]
    return "SEM_PROTOCOLO:" + slug_hash(*[row.get(c, "") for c in campos])



def consolidar_arquivos_importacao(arquivos, tipo, validar_janela=False):
    """Normaliza vários uploads antes de qualquer escrita no Sheets.

    O.S.: a última ocorrência do protocolo prevalece, como no upsert existente.
    Eventos: linhas repetidas são mescladas sem apagar campos já preenchidos.
    Se um arquivo falhar, interrompe o lote inteiro (nenhuma escrita parcial).
    Com histórico ativo, valida todas as linhas ANTES de remover duplicatas:
    nenhum registro fora da janela poderá ser ocultado pela consolidação.
    """
    if tipo not in {"PONTOS", "EVENTOS"}:
        raise ValueError("Tipo de importação desconhecido.")
    if not arquivos:
        raise ValueError("Selecione pelo menos um arquivo.")

    tabelas, detalhes = [], []
    for arquivo in arquivos:
        try:
            original = ler_planilha_upload(arquivo)
            if original.empty:
                raise ValueError("não contém registros")
            if tipo == "PONTOS":
                formato = identificar_formato_pontos(original)
                normalizada = preparar_pontos(original)
            else:
                formato = "Eventos"
                normalizada = preparar_eventos(original)
            if normalizada.empty:
                raise ValueError("não contém registros aproveitáveis após a padronização")
            if tipo == "PONTOS" and validar_janela:
                validar_importacao_diaria_v2(normalizada)
            tabelas.append(normalizada)
            detalhes.append({
                "Arquivo": arquivo.name,
                "Formato": formato,
                "Linhas lidas": len(original),
                "Registros válidos": len(normalizada),
            })
        except Exception as erro:
            raise ValueError(f"Arquivo '{arquivo.name}': {erro}. Nenhum arquivo deste lote foi importado.") from erro

    combinado = pd.concat(tabelas, ignore_index=True)
    total_linhas = len(combinado)
    if tipo == "PONTOS":
        # chave_ponto é a mesma chave usada pelo upsert PONTOS.
        chaves = combinado.apply(chave_ponto, axis=1)
        repetidos = int(chaves.duplicated(keep="last").sum())
        # Em arquivos mistos, a última O.S. vence, mas um endereço vazio no
        # arquivo posterior não apaga o último endereço conhecido do lote.
        enderecos_preenchidos = {}
        for chave, endereco in zip(chaves, combinado["Endereço"]):
            valor = limpar_valor(endereco)
            if valor:
                enderecos_preenchidos[chave] = valor
        combinado = combinado.loc[~chaves.duplicated(keep="last")].copy()
        ultimas_chaves = chaves.loc[combinado.index]
        combinado["Endereço"] = [
            limpar_valor(valor) or enderecos_preenchidos.get(chave, "")
            for chave, valor in zip(ultimas_chaves, combinado["Endereço"])
        ]
        combinado = combinado.reset_index(drop=True)
    else:
        # A política de atualização dos eventos já existentes é preservar o
        # campo anterior quando o novo vier vazio. Reproduz essa regra entre
        # arquivos do próprio lote, mantendo a ordem escolhida no upload.
        unicos = {}
        for _, linha in combinado.iterrows():
            chave = chave_evento(linha)
            novo = linha.to_dict()
            if chave in unicos:
                anterior = unicos[chave]
                unicos[chave] = {
                    campo: novo[campo] if str(novo[campo]).strip() else anterior.get(campo, "")
                    for campo in CABECALHO_EVENTOS
                }
            else:
                unicos[chave] = novo
        combinado = pd.DataFrame(list(unicos.values()), columns=CABECALHO_EVENTOS)
        repetidos = total_linhas - len(combinado)

    return combinado, pd.DataFrame(detalhes), total_linhas, repetidos


def upsert_pontos(df_novo):
    exigir_edicao_farol()
    atual = carregar_aba(NOME_ABA_PONTOS, CABECALHO_PONTOS)
    # Preserva colunas extras já existentes na aba PONTOS.
    colunas = list(dict.fromkeys(list(atual.columns) + list(CABECALHO_PONTOS)))
    atual = atual.reindex(columns=colunas).fillna("")
    novo = df_novo.reindex(columns=colunas).fillna("")

    if atual.empty:
        final = novo.copy()
    else:
        mapa = {}
        for _, r in atual.iterrows():
            mapa[chave_ponto(r)] = r.to_dict()
        for _, r in novo.iterrows():
            chave = chave_ponto(r)
            entrada = r.to_dict()
            anterior = mapa.get(chave)
            if anterior is not None:
                # Não apagar endereço já conhecido se a próxima exportação
                # não trouxer a coluna ou apresentar a célula vazia.
                if not limpar_valor(entrada.get("Endereço", "")):
                    entrada["Endereço"] = limpar_valor(anterior.get("Endereço", ""))
                # Preservar também campos extras preexistentes na aba PONTOS.
                for campo in colunas:
                    if campo not in CABECALHO_PONTOS and not limpar_valor(entrada.get(campo, "")):
                        entrada[campo] = limpar_valor(anterior.get(campo, ""))
            mapa[chave] = entrada
        final = pd.DataFrame(list(mapa.values()), columns=colunas)

    # Padroniza as coordenadas de O.S. para texto decimal com ponto antes
    # de enviar ao Sheets em modo RAW, sem depender da região da planilha.
    # Valores inválidos permanecem vazios; a O.S. continua armazenada.
    for coluna, tipo in (("Latitude", "lat"), ("Longitude", "lon")):
        final[coluna] = final[coluna].map(
            lambda valor: (
                format(n, ".6f") if (n := normalizar_coordenada(valor, tipo)) is not None else ""
            )
        )

    # Mantém ordem cronológica aproximada, sem exigir datas válidas.
    if not final.empty:
        final["_ord"] = pd.to_datetime(final["Dt. Emissão"], dayfirst=True, errors="coerce")
        final = final.sort_values("_ord", na_position="last").drop(columns="_ord")
    substituir_aba(NOME_ABA_PONTOS, colunas, final)
    return len(novo), len(final)


def carregar_areas_editadas():
    """Correções explícitas sobrevivem à reimportação de EVENTOS.

    Aba separada também permite proteger uma correção para campo vazio.
    """
    try:
        aba = obter_planilha(SPREADSHEET_ID).worksheet(NOME_ABA_AREAS_PROTEGIDAS)
    except gspread.WorksheetNotFound:
        return {}
    linhas = aba.get_all_values()
    if not linhas:
        return {}
    cab = linhas[0]
    if "CHAVE_EVENTO" not in cab or "AREAS_IMPACTADAS" not in cab:
        raise ValueError("Cabeçalho inválido em FAROL_AREAS_EDITADAS.")
    i_chave, i_area = cab.index("CHAVE_EVENTO"), cab.index("AREAS_IMPACTADAS")
    return {linha[i_chave]: linha[i_area] if i_area < len(linha) else ""
            for linha in linhas[1:] if i_chave < len(linha) and linha[i_chave]}


def registrar_area_editada(chave, area):
    """Registra correção humana com chave estável do evento, mesmo quando vazia."""
    exigir_edicao_farol()
    if not chave:
        raise ValueError("Não foi possível identificar o evento para proteger a área.")
    planilha = obter_planilha(SPREADSHEET_ID)
    try:
        aba = planilha.worksheet(NOME_ABA_AREAS_PROTEGIDAS)
    except gspread.WorksheetNotFound:
        aba = planilha.add_worksheet(title=NOME_ABA_AREAS_PROTEGIDAS, rows=1000, cols=4)
        aba.update(range_name="A1:D1", values=[["CHAVE_EVENTO", "AREAS_IMPACTADAS", "ATUALIZADO_EM", "AUTOR"]], value_input_option="RAW")
    linhas = aba.get_all_values()
    cab = linhas[0] if linhas else ["CHAVE_EVENTO", "AREAS_IMPACTADAS", "ATUALIZADO_EM", "AUTOR"]
    if "CHAVE_EVENTO" not in cab or "AREAS_IMPACTADAS" not in cab:
        raise ValueError("Cabeçalho inválido em FAROL_AREAS_EDITADAS.")
    dados = {"CHAVE_EVENTO": chave, "AREAS_IMPACTADAS": str(area),
             "ATUALIZADO_EM": datetime.now(FUSO_FAROL).strftime("%d/%m/%Y %H:%M"),
             "AUTOR": str(st.session_state.get("usuario_logado", ""))}
    valores = [dados.get(c, "") for c in cab]
    col_chave = cab.index("CHAVE_EVENTO")
    for n, linha in enumerate(linhas[1:], start=2):
        if col_chave < len(linha) and linha[col_chave] == chave:
            from gspread.utils import rowcol_to_a1
            aba.update(range_name=f"A{n}:{rowcol_to_a1(n, len(cab))}", values=[valores], value_input_option="RAW")
            return
    aba.append_row(valores, value_input_option="RAW")


def upsert_eventos(df_novo):
    exigir_edicao_farol()
    atual = carregar_aba(NOME_ABA_EVENTOS, CABECALHO_EVENTOS)
    atual = atual.reindex(columns=CABECALHO_EVENTOS).fillna("")
    novo = df_novo.reindex(columns=CABECALHO_EVENTOS).fillna("")
    areas_protegidas = carregar_areas_editadas()

    mapa = {}
    for _, registro in atual.iterrows():
        mapa[chave_evento(registro)] = registro.to_dict()
    for _, registro in novo.iterrows():
        chave = chave_evento(registro)
        dados_novos = registro.to_dict()
        if chave in mapa:
            # Atualizações parciais não apagam campos antigos preenchidos.
            anterior = mapa[chave]
            mapa[chave] = {
                campo: dados_novos[campo] if str(dados_novos[campo]).strip() else anterior.get(campo, "")
                for campo in CABECALHO_EVENTOS
            }
        else:
            mapa[chave] = dados_novos
    # Reaplica a correção humana APÓS a mesclagem de dados da importação.
    for chave, evento in mapa.items():
        if chave in areas_protegidas:
            evento["Áreas Impactadas"] = areas_protegidas[chave]
    final = pd.DataFrame(list(mapa.values()), columns=CABECALHO_EVENTOS)
    substituir_aba(NOME_ABA_EVENTOS, CABECALHO_EVENTOS, final)
    return len(novo), len(final)


# ============================================================
# KMZ / POLÍGONOS DOS BAIRROS
# ============================================================

@st.cache_data(show_spinner=False)
def carregar_kmz_bytes(kmz_bytes):
    with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
        nomes_kml = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not nomes_kml:
            raise ValueError("O KMZ não contém arquivo KML.")
        data = z.read(nomes_kml[0])
    return carregar_kml_bytes(data)


@st.cache_data(show_spinner=False)
def carregar_kml_bytes(kml_bytes):
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    raiz = ET.fromstring(kml_bytes)
    bairros = []

    for placemark in raiz.findall(".//k:Placemark", ns):
        nome_el = placemark.find("k:name", ns)
        nome = nome_el.text.strip() if nome_el is not None and nome_el.text else ""
        if not nome:
            continue

        poligonos = []
        for poly in placemark.findall(".//k:Polygon", ns):
            outer = poly.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", ns)
            if outer is None or not outer.text:
                continue
            coords = []
            for item in outer.text.strip().split():
                partes = item.split(",")
                if len(partes) >= 2:
                    try:
                        lon = float(partes[0])
                        lat = float(partes[1])
                        coords.append((lat, lon))
                    except ValueError:
                        pass
            if len(coords) >= 3:
                poligonos.append(coords)

        if poligonos:
            bairros.append({
                "nome": nome,
                "nome_normalizado": limpar_nome_bairro(nome),
                "poligonos": poligonos,
            })

    return bairros



def normalizar_municipio(valor):
    """Usa a mesma chave para Cidade da planilha e nome do arquivo KMZ."""
    return re.sub(r"[^A-Z0-9]", "", normalizar_texto(valor))


def unidade_da_cidade_farol(valor):
    """Define a unidade pela cidade da O.S., sem misturar Timon à API."""
    cidade = normalizar_municipio(valor)
    if cidade == "TERESINA":
        return "THE"
    if cidade in {"TIMON", "TIMONMA", "TIMONMARANHAO"}:
        return "TIM"
    return "API"


def carregar_gpkg(caminho):
    """Lê TODAS as geometrias poligonais do GPKG, sem exigir nome de bairro.

    As coordenadas são a fonte de verdade. Os atributos são opcionais e
    servem para nomear a feição e relacioná-la a eventos quando disponíveis.
    Preserva anéis internos (furos) e converte o SRC original para WGS84.
    """
    registros = []
    with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as conexao:
        camadas = conexao.execute(
            "SELECT table_name, srs_id FROM gpkg_contents WHERE data_type='features'"
        ).fetchall()
        for tabela, srs_id in camadas:
            info = conexao.execute(
                "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?",
                (tabela,),
            ).fetchone()
            if not info:
                continue
            campo_geometria, srs_geom = info
            quoted = lambda x: '"' + x.replace('"', '""') + '"'
            nomes = [
                r[1] for r in conexao.execute(f"PRAGMA table_info({quoted(tabela)})")
            ]
            campos = {normalizar_cabecalho(c): c for c in nomes}
            campo_bairro = next(
                (campos[k] for k in ("NMBAIRRO", "NOMEBAIRRO", "BAIRRO", "NOME")
                 if k in campos), None,
            )
            # No GPKG, NM_BAIRRO pode identificar o bairro principal,
            # enquanto name/NM_SUBAREA descrevem uma subárea desenhada
            # posteriormente. Priorizar só NM_BAIRRO torna a subárea invisível.
            campo_subarea = next(
                (campos[k] for k in (
                    "NMSUBAREA", "NOMESUBAREA", "SUBAREA",
                    "NOMESUBREGIAO", "SUBREGIAO",
                    "NAME", "NOMELOCALIDADE", "LOCALIDADE",
                ) if k in campos and campos[k] != campo_bairro), None,
            )
            campo_nome_aux = next(
                (campos[k] for k in ("NAME", "LOCALIDADE", "NOMELOCALIDADE")
                 if k in campos and campos[k] not in (campo_bairro, campo_subarea)), None,
            )
            campo_mun = next(
                (campos[k] for k in ("NMMUN", "MUNICIPIO", "NOMEMUNICIPIO", "CIDADE", "NMRGI")
                 if k in campos), None,
            )
            src_id = srs_geom or srs_id
            linha_srs = conexao.execute(
                "SELECT organization, organization_coordsys_id, definition "
                "FROM gpkg_spatial_ref_sys WHERE srs_id=?", (src_id,),
            ).fetchone()
            if not linha_srs:
                continue
            org, cod, definicao = linha_srs
            try:
                origem = CRS.from_user_input(f"{org}:{cod}") if org and cod else CRS.from_wkt(definicao)
            except Exception:
                origem = CRS.from_wkt(definicao)
            conversor = None if origem.equals(CRS.from_epsg(4326)) else Transformer.from_crs(
                origem, 4326, always_xy=True
            )
            # Não descartar a camada quando não há campo de nome:
            # a geometria pode ser desenhada normalmente.
            colunas = [campo_geometria]
            for campo in (campo_bairro, campo_subarea, campo_nome_aux, campo_mun):
                if campo and campo not in colunas:
                    colunas.append(campo)
            consulta = f"SELECT {', '.join(quoted(c) for c in colunas)} FROM {quoted(tabela)}"
            for numero_feicao, row in enumerate(conexao.execute(consulta), start=1):
                atributos = dict(zip(colunas, row))
                bruto = atributos.get(campo_geometria)
                if not bruto:
                    continue
                try:
                    bruto = bytes(bruto)
                    if len(bruto) < 8 or bruto[:2] != b"GP":
                        continue
                    envelope = (bruto[3] >> 1) & 7
                    tamanhos = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
                    if envelope not in tamanhos:
                        continue
                    geometria = wkb.loads(bruto[8 + tamanhos[envelope]:])
                    if conversor:
                        geometria = transformar_geometria(conversor.transform, geometria)
                    if geometria.is_empty:
                        continue
                    if geometria.geom_type == "Polygon":
                        partes = [geometria]
                    elif geometria.geom_type == "MultiPolygon":
                        partes = list(geometria.geoms)
                    else:
                        continue
                    poligonos, aneis_por_poligono = [], []
                    for parte in partes:
                        exterior = [(float(y), float(x)) for x, y in parte.exterior.coords]
                        if len(exterior) < 4 or not all(
                            math.isfinite(lat) and math.isfinite(lon)
                            and -90 <= lat <= 90 and -180 <= lon <= 180
                            for lat, lon in exterior
                        ):
                            continue
                        aneis = [exterior]
                        for interno in parte.interiors:
                            furo = [(float(y), float(x)) for x, y in interno.coords]
                            if len(furo) >= 4:
                                aneis.append(furo)
                        poligonos.append(exterior)
                        aneis_por_poligono.append(aneis)
                except (ValueError, TypeError, OverflowError, AttributeError):
                    # Uma feição malformada não impede a leitura das demais.
                    continue
                if not poligonos:
                    continue

                def nome_valido(valor):
                    texto = str(valor or "").strip()
                    return "" if texto.upper() in {
                        "", "NAN", "NONE", "NULL", "NOVO ELEMENTO", "NEW FEATURE"
                    } else texto

                nome_bairro = nome_valido(atributos.get(campo_bairro)) if campo_bairro else ""
                nome_subarea = nome_valido(atributos.get(campo_subarea)) if campo_subarea else ""
                nome_auxiliar = nome_valido(atributos.get(campo_nome_aux)) if campo_nome_aux else ""
                # O nome específico prevalece sobre o bairro de origem; ambos
                # continuam pesquisáveis, inclusive em eventos já cadastrados.
                nome_real = nome_subarea or nome_bairro or nome_auxiliar
                nomes_alternativos = list(dict.fromkeys(
                    nome for nome in (nome_bairro, nome_subarea, nome_auxiliar)
                    if nome and limpar_nome_bairro(nome)
                ))
                subarea_explicita = bool(
                    nome_subarea
                    and (
                        campo_subarea and normalizar_cabecalho(campo_subarea) in {
                            "NMSUBAREA", "NOMESUBAREA", "SUBAREA",
                            "NOMESUBREGIAO", "SUBREGIAO"
                        }
                        or nome_bairro and
                        limpar_nome_bairro(nome_subarea) != limpar_nome_bairro(nome_bairro)
                    )
                )
                rotulo = nome_real or f"Área sem nome ({tabela} #{numero_feicao})"
                municipio_atributo = atributos.get(campo_mun) if campo_mun else ""
                municipio = municipio_atributo or nome_municipio_arquivo(caminho)
                registros.append({
                    "nome": rotulo,
                    "nome_normalizado": limpar_nome_bairro(nome_real) if nome_real else "",
                    "nomes_alternativos": nomes_alternativos,
                    "tipo_area": "Subárea" if subarea_explicita else "Bairro",
                    "sem_nome": not bool(nome_real),
                    "municipio": normalizar_municipio(municipio),
                    "poligonos": poligonos,
                    "aneis_por_poligono": aneis_por_poligono,
                })
    return registros

def nome_municipio_arquivo(caminho):
    nome = os.path.splitext(os.path.basename(caminho))[0]
    return re.sub(r"^(?:NM_MUN|NM_RGI)[_\s-]*", "", nome, flags=re.I)


def preparar_poligonos_para_mapa(registros, tolerancia=0.00007):
    """Simplifica as geometrias apenas para desenhar, preservando todos os anéis.

    Os polígonos e furos ORIGINAIS permanecem disponíveis para os cruzamentos
    espaciais. A geometria não depende da presença de atributos cadastrais.
    """
    from shapely.geometry import Polygon

    for bairro in registros:
        visuais, aneis_visuais = [], []
        origem = bairro.get("aneis_por_poligono") or [
            [coords] for coords in bairro.get("poligonos", [])
        ]
        for aneis in origem:
            if not aneis or len(aneis[0]) < 4:
                continue
            atual = aneis
            if len(aneis[0]) >= 25:
                try:
                    geom = Polygon(
                        [(lon, lat) for lat, lon in aneis[0]],
                        holes=[
                            [(lon, lat) for lat, lon in furo]
                            for furo in aneis[1:]
                        ],
                    )
                    simplificado = geom.simplify(tolerancia, preserve_topology=True)
                    if not simplificado.is_empty and simplificado.geom_type == "Polygon":
                        novos = [
                            [(float(lat), float(lon)) for lon, lat in simplificado.exterior.coords]
                        ]
                        novos.extend(
                            [(float(lat), float(lon)) for lon, lat in ring.coords]
                            for ring in simplificado.interiors
                        )
                        if len(novos[0]) >= 4:
                            atual = novos
                except (ValueError, TypeError):
                    pass
            visuais.append(atual[0])
            aneis_visuais.append(atual)
        bairro["poligonos_mapa"] = visuais
        bairro["aneis_mapa"] = aneis_visuais
    return registros

@st.cache_data(show_spinner=False, ttl=300)
def carregar_geometrias_municipais(arquivos_assinados):
    """Carrega GeoPackage, KMZ e KML por município, sem impedir uso do mapa em caso de erro."""
    todos = []
    for caminho, _tamanho, _mtime in arquivos_assinados:
        extensao = os.path.splitext(caminho)[1].lower()
        municipio = normalizar_municipio(nome_municipio_arquivo(caminho))
        try:
            if extensao == ".gpkg":
                registros = carregar_gpkg(caminho)
            elif extensao in (".kmz", ".kml"):
                with open(caminho, "rb") as arquivo:
                    dados = arquivo.read()
                registros = carregar_kmz_bytes(dados) if extensao == ".kmz" else carregar_kml_bytes(dados)
                registros = [{**bairro, "municipio": municipio} for bairro in registros]
            else:
                continue
            todos.extend(preparar_poligonos_para_mapa(registros))
        except Exception:
            # Não exibe avisos de arquivos sem cobertura, mantendo os eventos na planilha.
            continue
    return todos


@st.cache_data(ttl=60, show_spinner=False)
def descobrir_kmz():
    """Compatibilidade com nome antigo; descobre KMZ, KML e GPKG da pasta."""
    arquivos = []
    if os.path.isdir(DIRETORIO_KMZ):
        for nome in sorted(os.listdir(DIRETORIO_KMZ)):
            if nome.lower().endswith((".gpkg", ".kmz", ".kml")):
                caminho = os.path.join(DIRETORIO_KMZ, nome)
                if os.path.isfile(caminho):
                    arquivos.append(caminho)
    # Para municípios com dois formatos, prefere o GPKG por conter NM_BAIRRO/NM_MUN.
    prioridades = {".gpkg": 0, ".kmz": 1, ".kml": 2}
    escolhidos = {}
    for caminho in arquivos:
        municipio = normalizar_municipio(nome_municipio_arquivo(caminho))
        if municipio and (municipio not in escolhidos or
                          prioridades[os.path.splitext(caminho)[1].lower()] < prioridades[os.path.splitext(escolhidos[municipio])[1].lower()]):
            escolhidos[municipio] = caminho
    if os.path.isfile(ARQUIVO_KMZ_PADRAO) and "TERESINA" not in escolhidos:
        escolhidos["TERESINA"] = ARQUIVO_KMZ_PADRAO
    assinaturas = []
    for caminho in escolhidos.values():
        try:
            info = os.stat(caminho)
            assinaturas.append((caminho, info.st_size, info.st_mtime_ns))
        except OSError:
            continue
    # Recalcula a chave de cada feição inclusive se o GeoPackage/KMZ tiver
    # sido recuperado de um cache criado antes da normalização do prefixo.
    # `nome` permanece intacto para rótulos e pop-ups do mapa.
    geometrias = carregar_geometrias_municipais(tuple(assinaturas))
    for bairro in geometrias:
        bairro["nome_normalizado"] = limpar_nome_bairro(
            bairro.get("nome") or bairro.get("nome_normalizado", "")
        )
    return geometrias


def ponto_em_poligono(lat, lon, poligono):
    # Ray casting. poligono: [(lat, lon), ...]
    dentro = False
    x = lon
    y = lat
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i]
        yj, xj = poligono[j]
        cruza = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi)
        if cruza:
            dentro = not dentro
        j = i
    return dentro


def ponto_em_bairro(lat, lon, bairro):
    aneis_por_poligono = bairro.get("aneis_por_poligono") or [
        [p] for p in bairro["poligonos"]
    ]
    for aneis in aneis_por_poligono:
        if (ponto_em_poligono(lat, lon, aneis[0])
                and not any(ponto_em_poligono(lat, lon, furo) for furo in aneis[1:])):
            return True
    return False


def identificar_bairro_por_ponto(lat, lon, bairros):
    if lat is None or lon is None:
        return ""
    for bairro in bairros:
        if not bairro.get("sem_nome") and ponto_em_bairro(lat, lon, bairro):
            return bairro["nome_normalizado"]
    return ""


# ============================================================
# CONCENTRAÇÕES / ANÁLISE ESPACIAL
# ============================================================

# Contrato da tabela de concentrações, inclusive quando nenhum grupo atende
# ao mínimo de O.S. Evita KeyError em mapas, seleção e relatórios.
CABECALHO_CONCENTRACOES = [
    "ID_CONCENTRACAO", "GRUPO", "LATITUDE", "LONGITUDE",
    "QTD_OS", "QTD_MATRICULAS", "BAIRROS",
]

@st.cache_data(ttl=180, show_spinner=False, max_entries=12)
def criar_concentracoes(df_pontos, raio_m=RAIO_CONCENTRACAO_PADRAO, minimo_os=MINIMO_OS_CONCENTRACAO_PADRAO):
    df = df_pontos.copy()
    df["_lat"] = df["Latitude"].map(parse_float)
    df["_lon"] = df["Longitude"].map(parse_float)
    df = df.dropna(subset=["_lat", "_lon"]).reset_index(drop=True)
    if df.empty:
        # Há O.S. no período, mas nenhuma coordenada utilizável.
        # Mesmo sem registros, ambas as tabelas precisam manter seu esquema.
        df["_grupo"] = pd.Series(dtype="int64")
        return pd.DataFrame(columns=CABECALHO_CONCENTRACOES), df
    # Não agrupa pontos de unidades diferentes (THE / API / TIM).
    # Preserva a lógica de proximidade entre municípios da mesma unidade.
    unidades_os = df["Cidade"].fillna("").map(unidade_da_cidade_farol).tolist()

    # Grade geográfica para evitar comparação de todos contra todos.
    lat_step = raio_m / 111320.0
    lon_step = raio_m / (111320.0 * max(0.2, math.cos(math.radians(float(df["_lat"].mean())))))

    buckets = {}
    for idx, r in df.iterrows():
        cell = (int(math.floor(r["_lat"] / lat_step)), int(math.floor(r["_lon"] / lon_step)))
        buckets.setdefault(cell, []).append(idx)

    parent = list(range(len(df)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for cell, indices in buckets.items():
        cx, cy = cell
        candidatos = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidatos.extend(buckets.get((cx + dx, cy + dy), []))
        for i in indices:
            for j in candidatos:
                if j <= i or unidades_os[i] != unidades_os[j]:
                    continue
                d = distancia_metros(df.at[i, "_lat"], df.at[i, "_lon"], df.at[j, "_lat"], df.at[j, "_lon"])
                if d is not None and d <= raio_m:
                    union(i, j)

    df["_grupo"] = [find(i) for i in range(len(df))]
    contagens = df.groupby("_grupo").size()
    grupos_validos = contagens[contagens >= minimo_os].index.tolist()
    df = df[df["_grupo"].isin(grupos_validos)].copy()

    if df.empty:
        # Coordenadas válidas, mas nenhum grupo atingiu minimo_os.
        # Retornar colunas conhecidas para que a interface não falhe.
        return pd.DataFrame(columns=CABECALHO_CONCENTRACOES), df

    concentracoes = []
    for grupo, bloco in df.groupby("_grupo"):
        lat = bloco["_lat"].mean()
        lon = bloco["_lon"].mean()
        os_count = len(bloco)
        matriculas = bloco["Matrícula"].replace("", pd.NA).dropna().nunique()
        bairros = sorted({limpar_nome_bairro(x) for x in bloco["Bairro"] if str(x).strip()})
        concentracoes.append({
            "ID_CONCENTRACAO": f"C{len(concentracoes) + 1:03d}",
            "GRUPO": grupo,
            "LATITUDE": lat,
            "LONGITUDE": lon,
            "QTD_OS": os_count,
            "QTD_MATRICULAS": matriculas,
            "BAIRROS": ", ".join(bairros),
        })

    cdf = (pd.DataFrame(concentracoes, columns=CABECALHO_CONCENTRACOES)
           .sort_values(["QTD_OS", "QTD_MATRICULAS"], ascending=False)
           .reset_index(drop=True))
    # Numeração independente THE / API / TIM. O GRUPO é preservado para
    # vínculos das O.S., eventos, ativos e acompanhamentos já existentes.
    contador = {"THE": 0, "API": 0, "TIM": 0}
    novos_ids = []
    for _, concentracao in cdf.iterrows():
        grupo = df[df["_grupo"] == concentracao["GRUPO"]]
        cidade = str(grupo.iloc[0].get("Cidade", "")) if not grupo.empty else ""
        abrangencia = unidade_da_cidade_farol(cidade)
        contador[abrangencia] += 1
        novos_ids.append(f"{abrangencia} C{contador[abrangencia]:03d}")
    cdf["ID_CONCENTRACAO"] = novos_ids
    return cdf, df


def _indice_espacial_ativos(df, tamanho_celula=0.01):
    """Grade espacial simples, sem dependências adicionais.

    Retém posições por índice posicional para preservar a ordem e índices repetidos.
    """
    grade = {}
    registros = list(df.to_dict("records"))
    for pos, r in enumerate(registros):
        lat = normalizar_coordenada(r.get("LATITUDE"), "lat")
        lon = normalizar_coordenada(r.get("LONGITUDE"), "lon")
        if lat is None or lon is None:
            continue
        celula = (math.floor(lat / tamanho_celula), math.floor(lon / tamanho_celula))
        grade.setdefault(celula, []).append((pos, lat, lon))
    return grade, registros


def _candidatos_no_raio(grade, lat, lon, raio_m, tamanho_celula=0.01):
    """Busca por caixa conservadora; a decisão final usa Haversine original."""
    if lat is None or lon is None or raio_m < 0:
        return []
    # A margem é deliberadamente conservadora para evitar falsos negativos.
    delta_lat = raio_m / 110000.0 + 1e-9
    limite_lat = min(89.999999, abs(lat) + delta_lat)
    cos_min = max(0.000001, math.cos(math.radians(limite_lat)))
    delta_lon = raio_m / (110000.0 * cos_min) + 1e-9
    x0 = math.floor((lat - delta_lat) / tamanho_celula)
    x1 = math.floor((lat + delta_lat) / tamanho_celula)
    y0 = math.floor((lon - delta_lon) / tamanho_celula)
    y1 = math.floor((lon + delta_lon) / tamanho_celula)
    candidatos = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            for pos, alat, alon in grade.get((x, y), ()):
                d = distancia_metros(lat, lon, alat, alon)
                if d is not None and d <= raio_m:
                    candidatos.append((pos, d))
    candidatos.sort(key=lambda item: item[0])
    return candidatos


def ativos_proximos_os(df_ativos, latitude, longitude, raio_m):
    """Seleciona equipamentos pela distância REAL à O.S. (independente de concentrações)."""
    vazios = df_ativos.iloc[0:0].copy()
    vazios["_DISTANCIA_M"] = pd.Series(dtype="float64")
    if df_ativos.empty or latitude is None or longitude is None:
        return vazios
    grade, _ = _indice_espacial_ativos(df_ativos)
    encontrados = _candidatos_no_raio(grade, latitude, longitude, float(raio_m))
    if not encontrados:
        return vazios
    selecionados = df_ativos.iloc[[pos for pos, _ in encontrados]].copy()
    selecionados["_DISTANCIA_M"] = [round(dist, 1) for _, dist in encontrados]
    return selecionados.sort_values("_DISTANCIA_M", kind="stable")


def ordens_proximas_os(df_pontos, latitude, longitude, raio_m):
    """O.S. exibidas no detalhe; NÃO cria concentração nem altera seus critérios."""
    if df_pontos.empty:
        return df_pontos.iloc[0:0].copy()
    lat = pd.to_numeric(df_pontos["Latitude"], errors="coerce").to_numpy(dtype=float)
    lon = pd.to_numeric(df_pontos["Longitude"], errors="coerce").to_numpy(dtype=float)
    lat_ref, lon_ref = math.radians(latitude), math.radians(longitude)
    delta_lat = np.radians(lat) - lat_ref
    delta_lon = np.radians(lon) - lon_ref
    h = (np.sin(delta_lat / 2) ** 2
         + math.cos(lat_ref) * np.cos(np.radians(lat)) * np.sin(delta_lon / 2) ** 2)
    distancia = 2 * 6371000.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    mascara = np.isfinite(distancia) & (distancia <= float(raio_m))
    return df_pontos.iloc[np.flatnonzero(mascara)].copy()


def localizar_os_por_chave(df_pontos, chave):
    """Resolve a seleção na data consultada, evitando usar O.S. de outra data."""
    if not chave or df_pontos.empty:
        return None
    for registro in df_pontos.to_dict("records"):
        if chave_ponto(registro) == chave:
            return registro
    return None


def localizar_os_clicada_panorama(estado, df_pontos, tolerancia_m=8):
    """Identifica um marcador de O.S. clicado, sem confundir pontos distantes.

    Usa o tooltip quando disponível no streamlit-folium; mantém fallback espacial
    porque instalações antigas podem devolver somente last_object_clicked.
    """
    clique = (estado or {}).get("last_object_clicked")
    if not isinstance(clique, dict) or df_pontos.empty:
        return None
    latitude = parse_float(clique.get("lat"))
    longitude = parse_float(clique.get("lng"))
    if latitude is None or longitude is None:
        return None
    tooltip = str((estado or {}).get("last_object_clicked_tooltip") or "")
    melhor = None
    melhor_criterio = (2, float("inf"))
    for registro in df_pontos.to_dict("records"):
        lat = normalizar_coordenada(registro.get("Latitude"), "lat")
        lon = normalizar_coordenada(registro.get("Longitude"), "lon")
        if lat is None or lon is None:
            continue
        distancia = distancia_metros(latitude, longitude, lat, lon)
        if distancia is None or distancia > tolerancia_m:
            continue
        etiqueta = f"O.S. {str(registro.get('Nº da O.S', '')).strip()} — selecionar"
        criterio = (0 if tooltip == etiqueta else 1, distancia)
        if criterio < melhor_criterio:
            melhor, melhor_criterio = registro, criterio
    return melhor


@st.cache_data(ttl=180, show_spinner=False, max_entries=12)
def pontos_proximos_concentracoes(concentracoes, df_pocos, df_loggers, raio=RAIO_OPERACIONAL_PADRAO):
    registros = []
    grade_pocos, pocos = _indice_espacial_ativos(df_pocos)
    grade_loggers, loggers = _indice_espacial_ativos(df_loggers)
    for _, c in concentracoes.iterrows():
        lat = normalizar_coordenada(c.get("LATITUDE"), "lat")
        lon = normalizar_coordenada(c.get("LONGITUDE"), "lon")
        for grade, ativos, tipo, nome_coluna in (
            (grade_pocos, pocos, "Poço / Ativo", "NOME_POCO"),
            (grade_loggers, loggers, "Logger", "ENDERECO"),
        ):
            for pos, d in _candidatos_no_raio(grade, lat, lon, raio):
                p = ativos[pos]
                registros.append({
                    "Concentração": c["ID_CONCENTRACAO"],
                    "Tipo": tipo,
                    "Identificação": p.get("IDENTIFICACAO_ATIVO", ""),
                    "ID Logger": p.get("ID_LOGGER", "") if tipo == "Logger" else "",
                    "Nome": p.get(nome_coluna, ""),
                    "Município": p.get("MUNICIPIO", ""),
                    "Distância (m)": round(d, 1),
                    "Distância": distancia_formatada(d),
                })
    return pd.DataFrame(registros)


def relacionar_eventos_pontos(df_eventos, df_pontos, bairros):
    """Contabiliza cada O.S. uma vez por evento, respeitando polígonos sobrepostos.

    Primeiro usa geometria da área informada; se ela NÃO tiver polígono,
    tenta bairro cadastrado e nome completo da localidade no Endereço.
    Esta tela conserva a contagem territorial (sem filtro temporal), como antes.
    """
    if df_eventos.empty or df_pontos.empty:
        return pd.DataFrame()

    pontos = df_pontos.copy()
    pontos["_cidade_cruzamento"] = pontos["Cidade"].map(normalizar_municipio)
    # Índices construídos uma única vez; evita pesquisar todas as O.S. e
    # feições do município novamente para cada evento da tabela.
    pontos_por_cidade = {
        chave: grupo for chave, grupo in pontos.groupby("_cidade_cruzamento")
        if chave
    }
    geometria_por_localidade = {}
    for poligono in (bairros or []):
        chave = (
            normalizar_municipio(poligono.get("municipio", "")),
            limpar_nome_bairro(poligono.get("nome_normalizado") or poligono.get("nome", "")),
        )
        geometria_por_localidade.setdefault(chave, []).append(poligono)
    registros = []
    for indice_evento, evento in df_eventos.reset_index(drop=True).iterrows():
        cidade = normalizar_municipio(evento.get("Cidade", ""))
        if not cidade:
            continue
        bloco = pontos_por_cidade.get(cidade)
        if bloco is None or bloco.empty:
            continue
        areas_texto = evento.get("Áreas Impactadas", "")
        municipal = evento_abrange_municipio(areas_texto)
        comum = {
            "_indice_evento": indice_evento,
            "Cidade": evento.get("Cidade", ""),
            "Evento": evento.get("Descrição do Serviço", "") or evento.get("Serviço", ""),
            "Protocolo": evento.get("Protocolo", ""),
            "Status": evento.get("Status", ""),
            "Data": evento.get("Data", ""),
        }
        if municipal:
            registros.append({
                **comum, "Bairro": "Todo o município", "Parcial": "Não",
                "O.S. encontradas": len(bloco),
                "Matrículas": bloco["Matrícula"].replace("", pd.NA).dropna().nunique(),
                "Critérios de associação": f"Município: {len(bloco)}",
            })
            continue

        areas = extrair_bairros_evento(areas_texto, evento.get("Cidade", ""), bairros)
        if not areas:
            continue
        indice_geometrias = {
            area["bairro"]: geometria_por_localidade.get((cidade, area["bairro"]), [])
            for area in areas
        }
        por_area = {}
        # Avaliação por O.S., não por polígono, para evitar dupla contagem de
        # uma mesma ordem nas áreas oficiais e subáreas que se sobrepõem.
        for _, os_reg in bloco.iterrows():
            area, metodo = identificar_correspondencia_territorial_evento_os(
                os_reg, cidade, areas, indice_geometrias=indice_geometrias
            )
            if area is None:
                continue
            resumo = por_area.setdefault(
                area, {"total": 0, "matriculas": set(), "metodos": {}}
            )
            resumo["total"] += 1
            matricula = limpar_valor(os_reg.get("Matrícula", ""))
            if matricula:
                resumo["matriculas"].add(matricula)
            resumo["metodos"][metodo] = resumo["metodos"].get(metodo, 0) + 1

        for area in areas:
            dados = por_area.get(area["bairro"])
            if not dados:
                continue
            ordem_metodos = ("Polígono", "Bairro cadastrado", "Endereço")
            criterios = ", ".join(
                f"{nome}: {dados['metodos'][nome]}"
                for nome in ordem_metodos if dados["metodos"].get(nome)
            )
            registros.append({
                **comum,
                "Bairro": area["bairro"],
                "Parcial": "Sim" if area["parcial"] else "Não",
                "O.S. encontradas": dados["total"],
                "Matrículas": len(dados["matriculas"]),
                "Critérios de associação": criterios,
            })
    return pd.DataFrame(registros)

# ============================================================
# EVENTOS GEORREFERENCIADOS — SOMENTE ONDE HÁ POLÍGONOS
# ============================================================

def classificar_status_evento(valor):
    status = normalizar_texto(valor)
    if any(p in status for p in ("FINALIZ", "ENCERR", "CONCLUI", "CONCLUID", "NORMALIZ", "RESOLVID", "CANCELAD")):
        return "Finalizado", "#2eaa59"
    if "PENDENTE" in status:
        return "Pendente", "#FDE68A"  # Amarelo claro para eventos pendentes.
    if any(p in status for p in ("PROGRAM", "AGEND", "PREVIST", "PLANEJ")):
        return "Programado", "#f2b233"
    if any(p in status for p in ("ANDAMENTO", "EXECU", "INICIAD", "ATIVO", "ABERTO", "OCORRENCIA", "EM CURSO")):
        return "Em andamento", "#e04747"
    return "Status não classificado", "#8b95a7"


@st.cache_data(ttl=180, show_spinner=False, max_entries=12)
def eventos_georreferenciados(eventos, bairros_kmz):
    """Relaciona polígonos por município + bairro, sem misturar cidades."""
    saida = []
    sem_geometria = 0
    if eventos is None or eventos.empty:
        return saida, 0
    indice = {}
    for bairro in bairros_kmz:
        chave = (bairro.get("municipio", "TERESINA"), bairro["nome_normalizado"])
        indice.setdefault(chave, []).append(bairro)
    for _, evento in eventos.iterrows():
        # Eventos encerrados permanecem na base, mas não geram feições no mapa.
        categoria_status, _ = classificar_status_evento(evento.get("Status", ""))
        if categoria_status == "Finalizado":
            continue
        cidade = normalizar_municipio(evento.get("Cidade", ""))
        areas = extrair_bairros_evento(evento.get("Áreas Impactadas", ""), evento.get("Cidade", ""), bairros_kmz)
        if not cidade or not areas:
            sem_geometria += 1
            continue
        identificados = set()
        encontrou = False
        faltou = False
        for area in areas:
            nome = area["bairro"]
            if nome in identificados:
                continue
            identificados.add(nome)
            correspondencias = indice.get((cidade, nome), [])
            if not correspondencias:
                faltou = True
                continue
            encontrou = True
            categoria, cor = classificar_status_evento(evento.get("Status", ""))
            for bairro in correspondencias:
                saida.append({
                    "bairro": bairro,
                    "evento": evento.to_dict(),
                    "parcial": area["parcial"],
                    "categoria": categoria,
                    "cor": cor,
                })
        if not encontrou or faltou:
            sem_geometria += 1
    return saida, sem_geometria


def adicionar_camada_eventos(mapa, eventos_mapeados):
    fg = folium.FeatureGroup(name="Eventos de desabastecimento", show=True)
    for item in eventos_mapeados:
        evento = item["evento"]
        # Proteção adicional contra feições finalizadas vindas de outra origem.
        if classificar_status_evento(evento.get("Status", ""))[0] == "Finalizado":
            continue
        bairro = item["bairro"]
        categoria = item["categoria"]
        parcial = item["parcial"]
        def esc(campo):
            return html_escape(str(evento.get(campo, "") or "—"))
        abrangencia = ("Parcial (polígono do bairro inteiro como referência)" if parcial
                       else "Todo o município" if evento_abrange_municipio(evento.get("Áreas Impactadas", ""))
                       else "Bairro informado como impactado")
        html = (
            f"<div style='min-width:250px;max-width:400px'>"
            f"<b>Evento — {html_escape(bairro['nome'])}</b><br>"
            f"<b>Situação no mapa:</b> {html_escape(categoria)}<br>"
            f"<b>Status original:</b> {esc('Status')}<br>"
            f"<b>Protocolo:</b> {esc('Protocolo')}<br>"
            f"<b>Serviço:</b> {esc('Serviço')}<br>"
            f"<b>Início:</b> {esc('Início')}<br>"
            f"<b>Previsão de término:</b> {esc('Prev. Término')}<br>"
            f"<b>Término real:</b> {esc('Término Real')}<br>"
            f"<b>Código do Ativo:</b> {esc('Código do Ativo')}<br>"
            f"<b>Abrangência:</b> {html_escape(abrangencia)}"
            f"</div>"
        )
        geometrias = bairro.get("aneis_mapa") or [
            [pol] for pol in bairro.get("poligonos_mapa", bairro["poligonos"])
        ]
        for aneis in geometrias:
            folium.Polygon(
                locations=aneis[0] if len(aneis) == 1 else aneis,
                color=item["cor"], weight=2.5, opacity=0.9,
                fill=True, fill_color=item["cor"], fill_opacity=0.23,
                tooltip=f"Evento: {bairro['nome']} — {categoria}",
                popup=folium.Popup(html, max_width=430),
            ).add_to(fg)
    fg.add_to(mapa)


# ============================================================
# MAPA
# ============================================================

@st.cache_data(ttl=180, show_spinner=False, max_entries=12)
def filtrar_ativos_proximos(concentracoes, df_pocos, df_loggers, raio_m):
    """Exibe ativos perto de qualquer concentração, sem duplicatas, na ordem original."""
    if concentracoes.empty:
        return df_pocos.iloc[0:0].copy(), df_loggers.iloc[0:0].copy()
    centros = [
        (normalizar_coordenada(r.get("LATITUDE"), "lat"), normalizar_coordenada(r.get("LONGITUDE"), "lon"))
        for _, r in concentracoes.iterrows()
    ]
    centros = [(lat, lon) for lat, lon in centros if lat is not None and lon is not None]
    if not centros:
        return df_pocos.iloc[0:0].copy(), df_loggers.iloc[0:0].copy()

    def filtrar(df):
        grade, _ = _indice_espacial_ativos(df)
        posicoes = set()
        for lat, lon in centros:
            posicoes.update(pos for pos, _ in _candidatos_no_raio(grade, lat, lon, raio_m))
        return df.iloc[sorted(posicoes)].copy()

    return filtrar(df_pocos), filtrar(df_loggers)


def icone_bandeira_concentracao():
    """Bandeira central da concentração, distinta dos pontos de O.S."""
    return folium.DivIcon(
        html=("<div style=\"font-size:25px;line-height:29px;"
              "width:30px;height:30px;text-align:center;"
              "filter:drop-shadow(0 1px 2px rgba(0,0,0,.35));\">🚩</div>"),
        icon_size=(30, 30),
        icon_anchor=(15, 25),
        class_name="farol-bandeira-concentracao",
    )


def icone_alvo_concentracao_panorama():
    """Marcador circular vermelho compacto, exclusivo do mapa panorâmico."""
    return folium.DivIcon(
        html=(
            '<div style="width:28px;height:28px;display:flex;align-items:center;'
            'justify-content:center;border-radius:50%;border:3px solid #DC2626;'
            'background:transparent;box-sizing:border-box;'
            'filter:drop-shadow(0 1px 2px rgba(0,0,0,.25));">'
            '<div style="width:16px;height:16px;border-radius:50%;'
            'background:#DC2626;"></div></div>'
        ),
        icon_size=(28, 28),
        icon_anchor=(14, 14),
        class_name="farol-alvo-concentracao-panorama",
    )


def municipios_das_concentracoes(concentracoes, pontos_cluster):
    """Municípios reais por grupo; o cadastro de concentrações não possui cidade."""
    resultado = {}
    if pontos_cluster is None or pontos_cluster.empty or "Cidade" not in pontos_cluster.columns:
        return resultado
    por_grupo = pontos_cluster.groupby("_grupo")["Cidade"].agg(
        lambda valores: {normalizar_municipio(v) for v in valores if str(v).strip()}
    ).to_dict()
    for _, c in concentracoes.iterrows():
        resultado[str(c["ID_CONCENTRACAO"])] = por_grupo.get(c["GRUPO"], set())
    return resultado


def bairros_dos_municipios(bairros, municipios):
    """Todos os bairros cadastrados dos municípios solicitados, sem recorte por raio."""
    if not municipios:
        return []
    return [b for b in bairros if normalizar_municipio(b.get("municipio", "")) in municipios]


def adicionar_limites_bairros(mapa, bairros):
    """Camada única GeoJSON: mais leve que criar um Polygon Leaflet por bairro."""
    features = []
    for b in bairros:
        geometrias = b.get("aneis_mapa") or [
            [pol] for pol in b.get("poligonos_mapa", b["poligonos"])
        ]
        for aneis in geometrias:
            if not aneis or len(aneis[0]) < 4:
                continue
            features.append({
                "type": "Feature",
                "properties": {"bairro": str(b.get("nome", "")), "municipio": str(b.get("municipio", ""))},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[float(lon), float(lat)] for lat, lon in ring]
                        for ring in aneis
                    ],
                },
            })
    if not features:
        return
    fg = folium.FeatureGroup(name="Limites dos bairros", show=True)
    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        name="Bairros", smooth_factor=1.0,
        style_function=lambda _feature: {"color": "#65758b", "weight": 1.15, "opacity": 0.75, "fillOpacity": 0},
        highlight_function=lambda _feature: {"color": "#334155", "weight": 2, "fillOpacity": 0.06},
        tooltip=folium.GeoJsonTooltip(fields=["bairro", "municipio"], aliases=["Bairro:", "Município:"], sticky=True),
    ).add_to(fg)
    fg.add_to(mapa)


def criar_mapa_panorama_v2(concentracoes, raio_m=RAIO_CONCENTRACAO_PADRAO, bairros=None,
                           eventos_mapeados=None, pontos_os=None):
    """Panorama: todas as O.S. georreferenciadas, com ou sem concentrações."""
    mapa = folium.Map(location=[-5.09, -42.80], zoom_start=12,
                      control_scale=True, tiles="OpenStreetMap", prefer_canvas=True)
    adicionar_limites_bairros(mapa, bairros or [])
    adicionar_camada_eventos(mapa, eventos_mapeados or [])
    desenhar_areas_mescladas(mapa, concentracoes)
    limites = []

    # O.S. independentes: nunca dependem de pontos_cluster, que contém apenas
    # os grupos que atingiram o mínimo de três O.S.
    fg_os = folium.FeatureGroup(name="O.S. individuais", show=True)
    if pontos_os is not None and not pontos_os.empty:
        for registro in pontos_os.to_dict("records"):
            lat = normalizar_coordenada(registro.get("Latitude"), "lat")
            lon = normalizar_coordenada(registro.get("Longitude"), "lon")
            if lat is None or lon is None:
                continue
            os_id = str(registro.get("Nº da O.S", "") or "").strip()
            servico = str(registro.get("Serviço Executado", "") or "")
            eh_informacao = bool(re.search(r"(?<!\d)146005(?!\d)", servico))
            cor = "#F28C28" if eh_informacao else "#1677D2"
            categoria = ("Informação de Falta de água (146005)" if eh_informacao
                         else "Reclamação de Falta de água")
            def seguro(campo):
                return html_escape(str(registro.get(campo, "") or "—"))
            popup = (
                "<div style='min-width:230px;max-width:340px'>"
                f"<b>O.S.: {html_escape(os_id or 'Sem número')}</b><br>"
                f"<b>Matrícula:</b> {seguro('Matrícula')}<br>"
                f"<b>Cidade:</b> {seguro('Cidade')}<br>"
                f"<b>Bairro:</b> {seguro('Bairro')}<br>"
                f"<b>Status:</b> {seguro('Status OS')}<br>"
                f"<b>Serviço:</b> {seguro('Serviço Executado')}<br>"
                f"<b>Classificação:</b> {html_escape(categoria)}<br>"
                "<small style='display:block;margin-top:8px'>"
                "Selecione o ponto para abrir a opção de análise do raio operacional abaixo do mapa."
                "</small></div>"
            )
            folium.CircleMarker(
                [lat, lon], radius=5, color=cor, weight=1.5,
                fill=True, fill_color=cor, fill_opacity=.88,
                tooltip=f"O.S. {os_id} — selecionar",
                popup=folium.Popup(popup, max_width=380),
            ).add_to(fg_os)
            limites.append([lat, lon])
    fg_os.add_to(mapa)

    for _, c in concentracoes.iterrows():
        lat, lon = float(c["LATITUDE"]), float(c["LONGITUDE"])
        codigo = html_escape(str(c["ID_CONCENTRACAO"]))
        bairros_txt = html_escape(str(c.get("BAIRROS", "") or "Não informado"))
        resumo = (f"<b>{codigo}</b><br>{bairros_txt}<br>"
                  f"{int(c['QTD_OS'])} O.S. · {int(c['QTD_MATRICULAS'])} matrículas<br>"
                  "<small>Clique no ponto central para abrir a análise detalhada.</small>")
        if not (isinstance(c.get("CHAVE_MESCLA"), str) and c.get("CHAVE_MESCLA")):
            folium.Circle([lat, lon], radius=float(raio_m), color="#7c3aed",
                          weight=2, fill=True, fill_opacity=.07,
                          interactive=False).add_to(mapa)
        folium.Marker([lat, lon], icon=icone_alvo_concentracao_panorama(),
                      popup=folium.Popup(resumo, max_width=320),
                      tooltip=f"{codigo} — Abrir concentração").add_to(mapa)
        limites.append([lat, lon])
    if len(limites) > 1:
        mapa.fit_bounds(limites, padding=(35, 35), max_zoom=15)
    elif len(limites) == 1:
        mapa.location = limites[0]
        mapa.options["zoom"] = 15
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def criar_mapa(bairros, df_pontos, df_pocos, df_loggers, concentracoes, proximidades, mostrar_bairros=True, eventos_mapeados=None, raio_circulo=None, outras_concentracoes=None, rotulos_equipamentos=False, ponto_foco_operacional=None, raio_foco_operacional=None):
    mapa = folium.Map(location=[-5.09, -42.80], zoom_start=12, control_scale=True, tiles=None, zoom_snap=0.5, zoom_delta=0.5, wheel_px_per_zoom_level=120, prefer_canvas=True)
    folium.TileLayer(tiles="OpenStreetMap", name="OpenStreetMap", control=False).add_to(mapa)

    if mostrar_bairros:
        adicionar_limites_bairros(mapa, bairros)

    adicionar_camada_eventos(mapa, eventos_mapeados or [])
    desenhar_areas_mescladas(mapa, concentracoes)

    fg_os = folium.FeatureGroup(name="O.S.", show=True)
    if not df_pontos.empty:
        for r in df_pontos.to_dict("records"):
            lat = normalizar_coordenada(r.get("Latitude"), "lat")
            lon = normalizar_coordenada(r.get("Longitude"), "lon")
            if lat is None or lon is None:
                continue
            # Cor definida pelo serviço executado, independentemente do status da O.S.
            servico_os = str(r.get("Serviço Executado", "") or "")
            eh_informacao = bool(re.search(r"(?<!\d)146005(?!\d)", servico_os))
            cor = "#F28C28" if eh_informacao else "#1677D2"
            classificacao = ("Informação de Falta de água (146005)" if eh_informacao
                             else "Reclamação de Falta de água")
            popup = folium.Popup(
                f"O.S.: {r.get('Nº da O.S','')}<br>"
                f"<b>Matrícula:</b> {r.get('Matrícula','')}<br>"
                f"<b>Bairro:</b> {r.get('Bairro','')}<br>"
                f"<b>Endereço:</b> {html_escape(str(r.get('Endereço', '') or ''))}<br>"
                f"<b>Status:</b> {r.get('Status OS','')}<br>"
                f"<b>Serviço:</b> {r.get('Serviço Executado','')}<br>"
                f"<b>Classificação:</b> {classificacao}",
                max_width=350,
            )
            folium.CircleMarker(
                [lat, lon], radius=4, color=cor, fill=True, fill_opacity=0.75, popup=popup
            ).add_to(fg_os)
    fg_os.add_to(mapa)

    # Rótulos independentes, controlados manualmente no menu de camadas.
    # Esta camada só existe no detalhamento operacional e inicia desmarcada.
    fg_rotulos = (folium.FeatureGroup(name="Rótulos de ativos e loggers", show=False)
                  if rotulos_equipamentos else None)
    if fg_rotulos is not None:
        mapa.get_root().header.add_child(Element("""
        <style>
          .farol-etiqueta-equipamento {
            display: inline-block; width: max-content;
            padding: 2px 5px; border-radius: 4px;
            background: rgba(255,255,255,.95); color: #1e293b;
            border: 1px solid #cbd5e1; font: 600 11px/1.25 Arial,sans-serif;
            box-shadow: 0 1px 3px rgba(0,0,0,.18);
            white-space: nowrap; pointer-events: none;
          }
        </style>
        """))

    def adicionar_rotulo_equipamento(lat, lon, nome):
        if fg_rotulos is None:
            return
        folium.Marker(
            [lat, lon],
            icon=folium.DivIcon(
                html=f'<span class="farol-etiqueta-equipamento">{html_escape(str(nome))}</span>',
                icon_size=(0, 0), icon_anchor=(-12, 24),
            ),
        ).add_to(fg_rotulos)

    fg_loggers = folium.FeatureGroup(name="Loggers", show=True)
    for _, r in df_loggers.iterrows():
        lat = normalizar_coordenada(r.get("LATITUDE"), "lat")
        lon = normalizar_coordenada(r.get("LONGITUDE"), "lon")
        if lat is None or lon is None:
            continue
        adicionar_rotulo_equipamento(lat, lon, r.get("IDENTIFICACAO_ATIVO", "") or "Logger")
        folium.Marker(
            [lat, lon],
            popup=folium.Popup(
                f"<b>Logger:</b> {r.get('IDENTIFICACAO_ATIVO','')}<br>"
                f"<b>Endereço:</b> {r.get('ENDERECO','')}", max_width=300
            ),
            icon=folium.Icon(icon="signal", prefix="fa", color="orange"),
        ).add_to(fg_loggers)
    fg_loggers.add_to(mapa)

    fg_pocos = folium.FeatureGroup(name="Poços / Ativos", show=True)
    for _, r in df_pocos.iterrows():
        lat = normalizar_coordenada(r.get("LATITUDE"), "lat")
        lon = normalizar_coordenada(r.get("LONGITUDE"), "lon")
        if lat is None or lon is None:
            continue
        adicionar_rotulo_equipamento(lat, lon, r.get("IDENTIFICACAO_ATIVO", "") or r.get("NOME_POCO", "") or "Ativo")
        folium.Marker(
            [lat, lon],
            popup=folium.Popup(
                f"<b>Poço / Ativo:</b> {r.get('IDENTIFICACAO_ATIVO','')}<br>"
                f"<b>Nome:</b> {r.get('NOME_POCO','')}<br>"
                f"Município: {r.get('MUNICIPIO','')}", max_width=300
            ),
            icon=folium.Icon(icon="tint", prefix="fa", color="blue"),
            z_index_offset=1000,  # Mantém ativos acima dos loggers coincidentes.
        ).add_to(fg_pocos)
    fg_pocos.add_to(mapa)
    if fg_rotulos is not None:
        fg_rotulos.add_to(mapa)

    # Consulta individual: círculo real centrado na O.S., não em uma concentração.
    if ponto_foco_operacional is not None:
        lat_foco = normalizar_coordenada(ponto_foco_operacional.get("Latitude"), "lat")
        lon_foco = normalizar_coordenada(ponto_foco_operacional.get("Longitude"), "lon")
        if lat_foco is not None and lon_foco is not None:
            raio_foco = float(raio_foco_operacional or RAIO_OPERACIONAL_PADRAO)
            numero = html_escape(str(ponto_foco_operacional.get("Nº da O.S", "") or "—"))
            camada_raio = folium.FeatureGroup(name="Raio operacional da O.S.", show=True)
            folium.Circle(
                [lat_foco, lon_foco], radius=raio_foco,
                color="#0F766E", weight=2.5, fill=True,
                fill_color="#14B8A6", fill_opacity=.10,
                interactive=False,  # Não intercepta cliques nos equipamentos dentro do círculo.
                tooltip=f"Raio operacional: {raio_foco:.0f} m",
            ).add_to(camada_raio)
            folium.CircleMarker(
                [lat_foco, lon_foco], radius=9,
                color="#065F46", weight=3, fill=True,
                fill_color="#34D399", fill_opacity=1,
                tooltip=f"O.S. {numero} — ponto analisado",
                popup=folium.Popup(f"<b>O.S. analisada:</b> {numero}<br>"
                                   f"<b>Raio operacional:</b> {raio_foco:.0f} m", max_width=280),
            ).add_to(camada_raio)
            camada_raio.add_to(mapa)

    fg_conc = folium.FeatureGroup(name="Concentrações", show=True)
    for _, c in concentracoes.iterrows():
        lat, lon = float(c["LATITUDE"]), float(c["LONGITUDE"])
        codigo = html_escape(str(c["ID_CONCENTRACAO"]))
        bairros_html = html_escape(str(c.get("BAIRROS", "") or "Não informados"))
        # Sem rótulos permanentes: os dados aparecem somente ao clicar na concentração.
        conteudo_popup = (
            "<div style='min-width:230px;max-width:340px;font-size:13px'>"
            f"<b>Concentração {codigo}</b>"
            "<details open style='margin-top:7px'>"
            "<summary style='cursor:pointer;font-weight:600'>Informações da concentração</summary>"
            f"<div style='padding-top:7px'>O.S.: {int(c['QTD_OS'])}<br>"
            f"<b>Matrículas:</b> {int(c['QTD_MATRICULAS'])}<br>"
            f"Bairros: {bairros_html}</div>"
            "</details>"
            "<small style='display:block;margin-top:8px'>O painel completo da concentração também será expandido abaixo do mapa.</small>"
            "</div>"
        )
        popup_concentracao = folium.Popup(conteudo_popup, max_width=370)
        if not (isinstance(c.get("CHAVE_MESCLA"), str) and c.get("CHAVE_MESCLA")):
            folium.Circle(
                [lat, lon], radius=(raio_circulo if raio_circulo is not None else RAIO_CONCENTRACAO_PADRAO),
                color="purple", fill=True, fill_opacity=0.08,
                popup=popup_concentracao,
            ).add_to(fg_conc)
        # Ponto central discreto e clicável; sem código, contagens ou texto sobre o mapa.
        folium.Marker(
            [lat, lon], icon=icone_bandeira_concentracao(),
            tooltip="🚩 Clique para consultar a concentração",
            popup=folium.Popup(conteudo_popup, max_width=370),
        ).add_to(fg_conc)
    fg_conc.add_to(mapa)

    # As outras concentrações do mesmo município ficam sinalizadas, mesmo
    # quando estão distantes da concentração aberta. Sem carregar suas O.S.
    if outras_concentracoes is not None and not outras_concentracoes.empty:
        fg_outras = folium.FeatureGroup(name="Outras concentrações do município", show=True)
        for _, c in outras_concentracoes.iterrows():
            codigo = html_escape(str(c["ID_CONCENTRACAO"]))
            lat, lon = float(c["LATITUDE"]), float(c["LONGITUDE"])
            resumo = (f"<b>{codigo}</b><br>"
                      f"{html_escape(str(c.get('BAIRROS', '')))}<br>"
                      f"{int(c['QTD_OS'])} O.S. · {int(c['QTD_MATRICULAS'])} matrículas")
            folium.Marker([lat, lon], icon=icone_bandeira_concentracao(),
                          tooltip=f"🚩 {codigo} — outra concentração",
                          popup=folium.Popup(resumo, max_width=320)).add_to(fg_outras)
        fg_outras.add_to(mapa)

    # Centraliza o mapa nos registros efetivamente desenhados, como no Módulo 1.
    coordenadas_visiveis = []
    for quadro, lat_col, lon_col in (
        (df_pontos, "Latitude", "Longitude"),
        (df_pocos, "LATITUDE", "LONGITUDE"),
        (df_loggers, "LATITUDE", "LONGITUDE"),
    ):
        if quadro is not None and not quadro.empty:
            for _, registro in quadro.iterrows():
                lat = normalizar_coordenada(registro.get(lat_col), "lat")
                lon = normalizar_coordenada(registro.get(lon_col), "lon")
                if lat is not None and lon is not None:
                    coordenadas_visiveis.append([lat, lon])
    if coordenadas_visiveis:
        if len(coordenadas_visiveis) == 1:
            mapa.location = coordenadas_visiveis[0]
            mapa.options["zoom"] = 15
        else:
            mapa.fit_bounds(coordenadas_visiveis, padding=(25, 25), max_zoom=15)
    # Menu de camadas compacto: mantém todas as opções acessíveis sem
    # cobrir permanentemente parte do mapa no detalhamento operacional.
    # O controle se expande ao clicar ou passar o mouse sobre o ícone.
    mapa.get_root().header.add_child(Element("""
    <style>
      .leaflet-control-layers-expanded {
        padding: 5px 8px !important;
        max-width: 245px !important;
        max-height: 52vh !important;
        overflow-y: auto !important;
        box-sizing: border-box;
      }
      .leaflet-control-layers-list {
        font-size: 12px !important;
        line-height: 1.25 !important;
      }
      .leaflet-control-layers label {
        margin-bottom: 2px !important;
      }
      .leaflet-control-layers label span {
        white-space: normal;
      }
    </style>
    """))
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def normalizar_texto_series(serie):
    return serie.fillna("").astype(str).map(normalizar_texto)



# ============================================================
# CADASTRO DE POÇOS — OPERAÇÕES PRESERVADAS DO MÓDULO ORIGINAL
# ============================================================
def normalizar_coordenada(valor, tipo="lat"):
    n = parse_float(valor)
    if n is None or not math.isfinite(n) or n == 0:
        return None
    if tipo == "lat" and not (-90 <= n <= 90):
        return None
    if tipo == "lon" and not (-180 <= n <= 180):
        return None
    return round(float(n), 6)


def identificacao_exibicao(row):
    nome = str(row.get("NOME_POCO", "")).strip()
    ativo = str(row.get("IDENTIFICACAO_ATIVO", "")).strip()
    return f"{nome} — {ativo}" if nome and ativo else nome or ativo or str(row.get("ID_POCO", ""))


def carregar_pocos():
    return carregar_cadastro_github(ARQUIVO_POCOS_CSV, CABECALHO_POCOS)


def preparar_pocos(df):
    df = df.copy()
    for col in ("LATITUDE", "LONGITUDE"):
        if col in df:
            df[col] = df[col].map(lambda v: normalizar_coordenada(v, "lat" if col == "LATITUDE" else "lon"))
    return df


def invalidar_cache_dados():
    invalidar_cache()


# Os cadastros de poços e loggers são arquivos fixos no GitHub.
# Não há rotinas de cadastro, edição ou exclusão pelo Farol.


# ============================================================
# RELATÓRIOS INDIVIDUAIS DAS CONCENTRAÇÕES
# ============================================================

def evento_pertinente_data_analise(evento):
    """Seleciona eventos cuja janela operacional cruza o dia consultado.

    Inclui a tolerância de 1h antes do início e 3h após o término real.
    A previsão de término NÃO é confundida com o término efetivo.
    """
    hoje = datetime.now(FUSO_FAROL).date()
    referencia = st.session_state.get("farol_v2_data_consulta", hoje)
    if isinstance(referencia, datetime):
        referencia = referencia.date()
    if not isinstance(referencia, date):
        referencia = hoje

    inicio = interpretar_data_operacional(evento.get("Início", ""))
    termino = interpretar_data_operacional(evento.get("Término Real", ""))
    encerrado = classificar_status_evento(evento.get("Status", ""))[0] == "Finalizado"
    if encerrado and pd.isna(termino):
        # Um encerramento sem horário real não define uma janela confiável.
        return False

    dia_inicio = pd.Timestamp(referencia)
    dia_fim_exclusivo = dia_inicio + pd.Timedelta(days=1)
    if pd.notna(inicio) and inicio - pd.Timedelta(hours=1) >= dia_fim_exclusivo:
        return False
    if pd.notna(termino) and termino + pd.Timedelta(hours=3) < dia_inicio:
        return False
    # Não descarta territorialmente um evento ativo com início impreciso;
    # a classificação de O.S. exige, de qualquer modo, início com data e hora.
    return True


def indexar_geometrias_evento(bairros_geograficos, cidade_evento, areas_evento):
    """Relaciona somente polígonos das áreas deste evento e deste município."""
    nomes = {area["bairro"] for area in areas_evento}
    indice = {nome: [] for nome in nomes}
    for bairro in (bairros_geograficos or []):
        if bairro.get("sem_nome") or normalizar_municipio(bairro.get("municipio", "")) != cidade_evento:
            continue
        nome = limpar_nome_bairro(bairro.get("nome_normalizado") or bairro.get("nome", ""))
        if nome in indice:
            indice[nome].append(bairro)
    return indice


def identificar_correspondencia_territorial_evento_os(
        os_reg, cidade_evento, areas_evento, bairros_geograficos=None,
        municipal=False, indice_geometrias=None):
    """Retorna (área, critério), sem aproximar localidades distintas.

    Se a área afetada tem polígono e a O.S. possui coordenadas, a geometria
    é decisiva: estar FORA impede vínculo pelo texto do bairro/endereço.
    Sem polígono cadastrado, usa bairro exato ou, em última instância,
    localidade completa no Endereço. Nunca cruza municípios diferentes.
    """
    if normalizar_municipio(os_reg.get("Cidade", "")) != cidade_evento:
        return None, None
    if municipal:
        return "Todo o município", "Município"
    if not areas_evento:
        return None, None

    indice = (
        indice_geometrias if indice_geometrias is not None
        else indexar_geometrias_evento(bairros_geograficos, cidade_evento, areas_evento)
    )
    lat = normalizar_coordenada(os_reg.get("Latitude", ""), "lat")
    lon = normalizar_coordenada(os_reg.get("Longitude", ""), "lon")
    tem_coordenadas = lat is not None and lon is not None

    # Prioridade global: todas as áreas deste evento com polígonos,
    # inclusive subáreas sobrepostas a bairros maiores.
    if tem_coordenadas:
        for area in areas_evento:
            nome = area["bairro"]
            if any(ponto_em_bairro(lat, lon, poligono) for poligono in indice.get(nome, [])):
                return nome, "Polígono"

    bairro_textual = limpar_nome_bairro(os_reg.get("Bairro", ""))
    if bairro_textual:
        for area in areas_evento:
            nome = area["bairro"]
            # Não usar texto para contrariar o polígono quando a posição
            # geográfica permite verificar que a O.S. está fora da área.
            if bairro_textual == nome and (not indice.get(nome) or not tem_coordenadas):
                return nome, "Bairro cadastrado"

    endereco = os_reg.get("Endereço", "")
    for area in areas_evento:
        nome = area["bairro"]
        if not indice.get(nome) and endereco_contem_localidade(endereco, nome):
            return nome, "Endereço"
    return None, None


def correspondencia_territorial_evento_os(os_reg, cidade_evento, areas_evento,
                                          bairros_geograficos=None, municipal=False):
    """Compatibilidade: retorna apenas o nome da área territorialmente reconhecida."""
    nome, _ = identificar_correspondencia_territorial_evento_os(
        os_reg, cidade_evento, areas_evento, bairros_geograficos, municipal
    )
    return nome

def dados_relatorio_concentracao(concentracao, pontos_cluster, proximidades, eventos, bairros_geograficos=None):
    """Associa eventos às O.S. individualmente, por município, território e data.

    Preserva o índice de cada O.S. associada para a classificação temporal
    posterior, evitando atribuir um evento a todo o grupo indevidamente.
    """
    grupo = concentracao["GRUPO"]
    os_grupo = pontos_cluster[pontos_cluster["_grupo"] == grupo].copy()
    codigo = concentracao["ID_CONCENTRACAO"]
    ativos = (
        proximidades[proximidades["Concentração"] == codigo].copy()
        if not proximidades.empty else pd.DataFrame()
    )
    relacionados = []
    if (eventos is not None and not eventos.empty and not os_grupo.empty
            and "Cidade" in eventos.columns and "Cidade" in os_grupo.columns):
        # Um evento de outro município jamais será associado às O.S. do grupo.
        # Filtrar primeiro evita reprocessar todas as áreas e geometrias dos
        # eventos de API/THE/TIM para cada concentração, especialmente em reruns.
        cidades_do_grupo = {
            normalizar_municipio(valor) for valor in os_grupo["Cidade"]
        } - {""}
        eventos_candidatos = eventos.loc[
            eventos["Cidade"].map(normalizar_municipio).isin(cidades_do_grupo)
        ]
        for _, evento in eventos_candidatos.iterrows():
            if not evento_pertinente_data_analise(evento):
                continue
            cidade = normalizar_municipio(evento.get("Cidade", ""))
            if not cidade:
                continue
            area_texto = evento.get("Áreas Impactadas", "")
            municipal = evento_abrange_municipio(area_texto)
            areas_evento = extrair_bairros_evento(
                area_texto, evento.get("Cidade", ""), bairros_geograficos
            )
            # Restringe a busca geométrica aos polígonos deste evento.
            # Evita percorrer todos os bairros do município para cada O.S.
            chaves_areas = {a["bairro"] for a in areas_evento}
            geometrias_evento = [b for b in (bairros_geograficos or [])
                if (normalizar_municipio(b.get("municipio", "")) == cidade
                    and b.get("nome_normalizado", "") in chaves_areas)] if not municipal else []
            indice_geometrias = indexar_geometrias_evento(
                geometrias_evento, cidade, areas_evento
            )
            indices_compativeis, nomes_compativeis = [], set()
            criterios_por_os = {}
            for idx_os, os_reg in os_grupo.iterrows():
                nome, criterio = identificar_correspondencia_territorial_evento_os(
                    os_reg, cidade, areas_evento, municipal=municipal,
                    indice_geometrias=indice_geometrias,
                )
                if nome is not None:
                    indices_compativeis.append(idx_os)
                    nomes_compativeis.add(nome)
                    criterios_por_os[idx_os] = criterio
            if indices_compativeis:
                item = evento.to_dict()
                item["Bairros coincidentes"] = ", ".join(sorted(nomes_compativeis))
                # Metadados temporários; não são gravados na aba EVENTOS.
                item["_indices_os_correspondentes"] = tuple(indices_compativeis)
                item["_criterios_os_correspondentes"] = criterios_por_os
                relacionados.append(item)
    return os_grupo, ativos, pd.DataFrame(relacionados)


def interpretar_data_operacional(valor):
    """Converte horários de eventos, inclusive '24/10/2026 09:30h'.

    Uma data sem hora não é tratada como meia-noite: sem horário não se pode
    confirmar vínculo temporal com O.S. Nenhuma data é deslocada entre fusos.
    """
    if valor is None or str(valor).strip().lower() in ("", "nan", "none", "nat", "-", "--"):
        return pd.NaT
    if isinstance(valor, (datetime, pd.Timestamp)):
        t = pd.Timestamp(valor)
        return t.tz_localize(None) if t.tzinfo is not None else t
    texto = str(valor).strip()
    # Exportação de eventos: hh:mmh ou hh:mm:ssh.
    texto = re.sub(r"(?i)(?<=\d)\s*h\.?\s*$", "", texto).strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{1,2}-\d{1,2}", texto):
        return pd.NaT
    try:
        resultado = pd.to_datetime(texto, dayfirst=True, errors="coerce")
        if pd.isna(resultado):
            return pd.NaT
        t = pd.Timestamp(resultado)
        return t.tz_localize(None) if t.tzinfo is not None else t
    except (ValueError, TypeError, OverflowError):
        return pd.NaT


def classificar_os_por_evento(os_grupo, eventos_relacionados):
    """Associa cada O.S. a TODOS os eventos elegíveis separadamente.

    Intervalo por evento: [início - 1h, término real + 3h].
    Na falta de término, apenas eventos ainda abertos vão até o momento atual.
    Correspondência territorial calculada individualmente com geometrias
    no momento de montar os dados da concentração.
    """
    vinculadas, nao_vinculadas = [], []
    agora = pd.Timestamp.now(tz=FUSO_FAROL).tz_localize(None)
    for indice_os, os_reg in os_grupo.iterrows():
        data_os = interpretar_data_operacional(os_reg.get("Dt. Emissão", ""))
        cidade_os = normalizar_municipio(os_reg.get("Cidade", ""))
        bairro_os = limpar_nome_bairro(os_reg.get("Bairro", ""))
        protocolos = []
        criterios_vinculo = []
        if pd.notna(data_os) and eventos_relacionados is not None and not eventos_relacionados.empty:
            for _, evento in eventos_relacionados.iterrows():
                if cidade_os != normalizar_municipio(evento.get("Cidade", "")):
                    continue
                # Quando há resultado territorial georreferenciado, ele é
                # específico da O.S.: não generalizar para toda a concentração.
                if "_indices_os_correspondentes" in evento.index:
                    ids_territoriais = evento.get("_indices_os_correspondentes")
                    if indice_os not in (ids_territoriais or ()):
                        continue
                else:
                    # Compatibilidade com chamadas sem os metadados de geometria.
                    bairros = [limpar_nome_bairro(b) for b in
                               str(evento.get("Bairros coincidentes", "")).split(",")]
                    if ("Todo o município" not in str(evento.get("Bairros coincidentes", ""))
                            and bairro_os not in bairros):
                        continue
                inicio = interpretar_data_operacional(evento.get("Início", ""))
                fim = interpretar_data_operacional(evento.get("Término Real", ""))
                encerrado = classificar_status_evento(evento.get("Status", ""))[0] == "Finalizado"
                if pd.isna(inicio):
                    continue
                if pd.isna(fim):
                    if encerrado:
                        continue
                    # Evento ainda aberto: não estender a janela para o futuro.
                    limite_superior = agora
                else:
                    if fim < inicio:
                        continue
                    limite_superior = fim + pd.Timedelta(hours=3)
                if inicio - pd.Timedelta(hours=1) <= data_os <= limite_superior:
                    protocolo = str(evento.get("Protocolo", "") or "").strip()
                    if protocolo.lower() in {"", "nan", "none", "-", "--"}:
                        # Mantém eventos sem protocolo distinguíveis por ativo.
                        protocolo = str(evento.get("Código do Ativo", "") or evento.get("Serviço", "") or "Evento").strip()
                    protocolos.append(protocolo)
                    if "_criterios_os_correspondentes" in evento.index:
                        criterio = evento.get("_criterios_os_correspondentes")
                        if isinstance(criterio, dict) and criterio.get(indice_os):
                            criterios_vinculo.append(criterio[indice_os])
        linha = os_reg.copy()
        linha["Evento(s) vinculado(s)"] = ", ".join(dict.fromkeys(protocolos))
        linha["Critério do vínculo"] = ", ".join(dict.fromkeys(criterios_vinculo))
        if protocolos:
            vinculadas.append(linha)
        else:
            nao_vinculadas.append(linha)
    cols = list(os_grupo.columns) + ["Evento(s) vinculado(s)", "Critério do vínculo"]
    def dataframe(linhas):
        return pd.DataFrame(linhas).reindex(columns=cols) if linhas else pd.DataFrame(columns=cols)
    return dataframe(vinculadas), dataframe(nao_vinculadas)


def descricao_eventos_pdf(eventos_relacionados):
    """Resumo Serviço / Código do Ativo dos eventos efetivamente relacionados à concentração."""
    if eventos_relacionados is None or eventos_relacionados.empty:
        return "Nenhum evento relacionado"
    descricoes = []
    for _, evento in eventos_relacionados.iterrows():
        servico = str(evento.get("Serviço", "") or "").strip()
        ativo = str(evento.get("Código do Ativo", "") or "").strip()
        if servico.lower() in ("nan", "none", "nat"):
            servico = ""
        if ativo.lower() in ("nan", "none", "nat"):
            ativo = ""
        descricao = " / ".join(parte for parte in (servico, ativo) if parte)
        if descricao and descricao not in descricoes:
            descricoes.append(descricao)
    return "; ".join(descricoes) if descricoes else "Evento sem serviço/ativo informado"


def texto_relatorio(valor):
    if valor is None or pd.isna(valor):
        return "—"
    return str(valor).strip() or "—"


def mapa_exclusivo_concentracao(concentracao, os_grupo, ativos):
    """Mapa compacto do agrupamento; não depende do enquadramento do mapa principal."""
    lat, lon = float(concentracao["LATITUDE"]), float(concentracao["LONGITUDE"])
    mapa = folium.Map(location=[lat, lon], zoom_start=16, tiles="OpenStreetMap",
                      control_scale=True, zoom_control=False, attribution_control=True)
    raio_grupo = RAIO_CONCENTRACAO_PADRAO
    folium.Circle([lat, lon], radius=raio_grupo, color="#8522a5", weight=3,
                  fill=True, fill_color="#8522a5", fill_opacity=0.07).add_to(mapa)
    folium.Marker([lat, lon], icon=icone_alvo_concentracao_panorama()).add_to(mapa)
    for _, registro in os_grupo.iterrows():
        y = normalizar_coordenada(registro.get("Latitude"), "lat")
        x = normalizar_coordenada(registro.get("Longitude"), "lon")
        if y is None or x is None:
            continue
        servico = str(registro.get("Serviço Executado", "") or "")
        cor = "#F28C28" if re.search(r"(?<!\d)146005(?!\d)", servico) else "#1677D2"
        folium.CircleMarker([y, x], radius=4.5, color=cor, weight=1,
                            fill=True, fill_color=cor, fill_opacity=.9).add_to(mapa)
    # A tabela de proximidades não possui coordenadas: recuperá-las dos cadastros locais.
    cadastros = {"LOGGER": carregar_cadastro_github(ARQUIVO_LOGGERS_CSV, CABECALHO_LOGGERS), "ATIVO": carregar_pocos()}
    for _, ativo in ativos.iterrows():
        tipo = str(ativo.get("Tipo", ""))
        eh_logger = "LOGGER" in tipo.upper()
        cadastro = cadastros["LOGGER" if eh_logger else "ATIVO"]
        identificacao = str(ativo.get("Identificação", "") or "").strip()
        correspondencias = cadastro[cadastro["IDENTIFICACAO_ATIVO"].astype(str).str.strip() == identificacao]
        if correspondencias.empty:
            continue
        registro_geo = correspondencias.iloc[0]
        y = normalizar_coordenada(registro_geo.get("LATITUDE"), "lat")
        x = normalizar_coordenada(registro_geo.get("LONGITUDE"), "lon")
        if y is None or x is None:
            continue
        identificacao = identificacao or str(ativo.get("Nome", "") or tipo)
        folium.Marker(
            [y, x], icon=folium.Icon(icon="signal" if eh_logger else "tint",
                                    prefix="fa", color="orange" if eh_logger else "blue"),
            tooltip=folium.Tooltip(html_escape(identificacao), permanent=True,
                                  direction="top", offset=(0, -12),
                                  class_name="farol-rotulo-relatorio"),
            z_index_offset=0 if eh_logger else 1000,
        ).add_to(mapa)
    mapa.get_root().header.add_child(Element("""<style>
    .leaflet-tooltip.farol-rotulo-relatorio {background:rgba(255,255,255,.96);color:#1e293b;
      border:1px solid #cbd5e1;border-radius:4px;padding:2px 5px;
      font:600 11px Arial,sans-serif;box-shadow:0 1px 3px #0002;white-space:nowrap}
    </style>"""))
    # O círculo inteiro deve caber no enquadramento; pequena margem para ruas e rótulos.
    delta_lat = (raio_grupo * 1.13) / 111320.0
    delta_lon = (raio_grupo * 1.13) / (111320.0 * max(.2, math.cos(math.radians(lat))))
    mapa.fit_bounds([[lat-delta_lat, lon-delta_lon], [lat+delta_lat, lon+delta_lon]],
                    padding=(12, 12))
    return mapa


def secao_pdf_acompanhamento(concentracao, largura=26.6*cm):
    """Acompanhamento compacto abaixo da ficha com mapa, sem alterar a cartografia.

    Organização em grade aproveita a largura do A4 paisagem, com fonte legível.
    Histórico de movimentações não é incluído no PDF, liberando espaço
    para a descrição e para as evidências dos loggers.
    """
    chave = str(concentracao.get("CHAVE_ACOMPANHAMENTO", "") or "")
    vinculo = next((v for v in listar_vinculos() if v["chave"] == chave), None) if chave else None
    dados = dados_acompanhamento().get(str(vinculo["evento_id"])) if vinculo else None

    azul = colors.HexColor("#193C65")
    azul_escuro = colors.HexColor("#25354B")
    cinza = colors.HexColor("#53657C")
    fundo = colors.HexColor("#F1F5FA")
    borda = colors.HexColor("#DCE4EE")
    corpo = ParagraphStyle("AcompCompactoCorpo", fontName="Helvetica", fontSize=9.0,
                           leading=12, textColor=azul_escuro, splitLongWords=True)
    titulo = ParagraphStyle("AcompCompactoTitulo", parent=corpo, fontName="Helvetica-Bold",
                            fontSize=10.5, leading=13, textColor=colors.white)
    nome = ParagraphStyle("AcompCompactoNome", parent=corpo, fontName="Helvetica-Bold",
                          fontSize=8, leading=10, textColor=cinza)
    texto = ParagraphStyle("AcompCompactoTexto", parent=corpo, fontSize=9.0, leading=12)
    meta = ParagraphStyle("AcompCompactoMeta", parent=corpo, fontName="Helvetica-Bold",
                         fontSize=8.4, leading=11, textColor=azul)

    def par(v, estilo=texto):
        if v is None or str(v).strip() == "":
            v = "Não informado"
        return Paragraph(xml_escape(str(v)).replace("\n", "<br/>"), estilo)

    def campo(rotulo, valor):
        return [par(rotulo, nome), par(valor)]

    def faixa(rotulo):
        tabela = Table([[par(rotulo, titulo)]], colWidths=[largura], hAlign="LEFT")
        tabela.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), azul),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ]))
        return tabela

    saida = [Spacer(1, 4), faixa("ACOMPANHAMENTO OPERACIONAL"), Spacer(1, 3)]
    if not vinculo or not dados:
        saida.append(par("Nenhum acompanhamento operacional iniciado para esta concentração."
                         if not vinculo else "Ficha vinculada não localizada."))
        return saida

    # Apenas dados essenciais na ficha impressa: os campos administrativos
    # (número, título, identificação e atualização) continuam no Sheets,
    # mas não ocupam espaço no PDF.
    campos_resumo = [
        ("Status / Finalizado", f"{dados.get('status', '')} · {dados.get('finalizado', '')}"),
        ("Município / Regional", f"{dados.get('municipio', '')} · {dados.get('regional', '')}"),
        ("Reclamações / Unidade", f"{dados.get('qtd_reclamacoes', '')} · {dados.get('unidade', '')}"),
    ]
    if str(dados.get("data_finalizacao", "") or "").strip():
        campos_resumo.append(("Data de finalização", dados["data_finalizacao"]))

    grade = Table([[campo(k, v) for k, v in campos_resumo]],
                  colWidths=[largura / len(campos_resumo)] * len(campos_resumo),
                  hAlign="LEFT")
    grade.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), fundo),
        ("LINEBELOW", (0, 0), (-1, -1), .3, borda),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    saida.extend([grade, Spacer(1, 4)])

    validacoes = [("ArcGIS", dados.get("arcgis", "")),
                  ("SIA", dados.get("sia", "")),
                  ("Loggers", dados.get("loggers", "")),
                  ("Retorno Operação/Controle", dados.get("retorno_operacao", ""))]
    val = Table([[campo(k, v) for k, v in validacoes]],
                colWidths=[largura/4]*4, hAlign="LEFT")
    val.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), fundo),
        ("LINEBELOW", (0, 0), (-1, -1), .3, borda),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    # Histórico de movimentações permanece na fonte do acompanhamento, mas
    # não ocupa espaço nas fichas PDF (individual e consolidada).
    saida.extend([par("VALIDAÇÕES OPERACIONAIS", meta), Spacer(1, 2), val,
                  Spacer(1, 4), par("DESCRIÇÃO DA OCORRÊNCIA", meta),
                  Spacer(1, 2), par(dados.get("descricao", ""))])
    return saida


def texto_acompanhamento_relatorio(concentracao):
    """Consulta em tempo de geração, sem reutilizar dados de sessão obsoletos."""
    chave = str(concentracao.get("CHAVE_ACOMPANHAMENTO", "") or "")
    if not chave:
        return "Acompanhamento operacional: não iniciado."
    vinculo = next((v for v in listar_vinculos() if v["chave"] == chave), None)
    if not vinculo:
        return "Acompanhamento operacional: não iniciado."
    dados = dados_acompanhamento().get(str(vinculo["evento_id"]))
    return texto_pdf(vinculo, dados, historico_evento(vinculo["evento_id"]))


def evidencias_graficas_pdf(ativos, indice_imagens, fonte="Helvetica", largura=26.6*cm):
    """Inclui no PDF somente loggers próximos com gráfico válido no dia.

    Loggers sem evidência não geram seção, espaço reservado nem página vazia.
    A identificação dos demais equipamentos nos mapas/listagens é preservada.
    """
    # Verifica a evidência antes de criar a seção.
    # Um cadastro sem imagem, ou uma captura ilegível, não deve criar
    # blocos vazios no relatório.
    evidencias = []
    for logger in filtrar_loggers(ativos):
        registro = (indice_imagens or {}).get(chave_logger(logger))
        if not registro or not registro.get("imagem"):
            continue
        foto = registro["imagem"]
        try:
            from PIL import Image as ImagemPIL
            with ImagemPIL.open(io.BytesIO(foto)) as imagem_pil:
                iw, ih = imagem_pil.size
                imagem_pil.verify()
            if iw <= 0 or ih <= 0:
                continue
            img_w = min(largura, 25.4*cm)
            img_h = img_w * ih / iw
            if img_h > 9.8*cm:
                fator = 9.8*cm / img_h
                img_w *= fator
                img_h *= fator
            evidencia = Image(io.BytesIO(foto), width=img_w, height=img_h)
            evidencias.append((logger, registro, evidencia))
        except Exception:
            # Imagens inválidas não criam espaços nem títulos no PDF.
            continue

    if not evidencias:
        return []

    estilo = ParagraphStyle(
        "LoggerGraficoCorpo", fontName=fonte, fontSize=9, leading=13,
        textColor=colors.HexColor("#273449"), spaceAfter=5,
    )
    subtitulo = ParagraphStyle(
        "LoggerGraficoTitulo", parent=estilo, fontSize=13, leading=17,
        spaceAfter=10, textColor=colors.HexColor("#17365D"),
    )
    # Sem PageBreak forçado: o ReportLab aproveita o espaço livre após
    # as O.S. e a descrição. Se um gráfico não couber, ele continua na
    # próxima página, sem criar uma página só para a quebra de seção.
    conteudo = [Spacer(1, 0.16*cm)]
    for i, (logger, _registro, evidencia) in enumerate(evidencias, 1):
        identificacao = rotulo_logger(logger)
        distancia = str(logger.get("Distância", "") or "Não informada")
        elementos = []
        if i == 1:
            # Não deixar o título isolado ao final de uma página.
            elementos.append(Paragraph("Evidências gráficas dos loggers próximos", subtitulo))
        elementos.extend([
            Paragraph(xml_escape(f"Logger {i} — {identificacao} | Distância: {distancia}"), estilo),
            Spacer(1, 0.12*cm),
            evidencia,
            Spacer(1, 0.22*cm),
        ])
        # Rótulo e imagem ficam juntos; apenas a paginação necessária
        # (em especial quando existem vários gráficos) é permitida.
        conteudo.append(KeepTogether(elementos))
    return conteudo


def invalidar_pdfs_apos_mudanca_grafico():
    """Impede baixar PDFs individuais/consolidados obsoletos desta sessão."""
    for nome_chave in list(st.session_state):
        if nome_chave.startswith("farol_pdf_individual_"):
            st.session_state.pop(nome_chave, None)
    st.session_state.pop("farol_pdf_operacional_pronto", None)


def interface_graficos_logger(ativos, codigo):
    """Permite colar, substituir e excluir capturas temporárias por logger.

    Edições são exclusivas de admin/usuario; perfil Consulta permanece
    somente leitura. Evidências são compartilhadas no dia corrente.
    """
    loggers = filtrar_loggers(ativos)
    if not loggers:
        return

    st.markdown("#### 📈 Evidências gráficas dos loggers")
    st.caption(
        f"{len(loggers)} logger(s) dentro do raio operacional. "
        "Gráficos compartilhados entre analistas até 23h59 (horário de Teresina)."
    )
    with st.expander("📋 Colar gráficos de pressão dos loggers", expanded=False):
        st.info(
            "**Como adicionar ou substituir um gráfico:** na ferramenta de "
            "monitoramento, pressione **Win + Shift + S**, selecione o gráfico, "
            "clique no campo do logger abaixo, pressione **Ctrl + V** e envie. "
            "Para remover a imagem anterior, use **Excluir gráfico**."
        )
        st.caption(
            "Apenas PNG ou JPG, até 6 MB por imagem. O novo envio substitui "
            "o gráfico compartilhado do mesmo logger no dia."
        )
        aviso = st.session_state.pop("farol_confirmacao_print", None)
        if aviso and aviso[0] == codigo:
            st.success(aviso[1])
        try:
            graficos = indice_do_dia(SPREADSHEET_ID, dia_graficos())
        except Exception as erro:
            st.error(f"Não foi possível consultar os gráficos compartilhados: {erro}")
            return

        for logger in loggers:
            chave = chave_logger(logger)
            atual = graficos.get(chave)
            nome = rotulo_logger(logger)
            chave_confirmacao = f"farol_confirmar_exclusao_{codigo}_{chave}"
            chave_versao = f"farol_versao_colagem_{chave}"

            st.markdown(
                f"**Logger {html_escape(nome)}** — "
                f"{html_escape(str(logger.get('Distância', '')))}"
            )
            if atual:
                st.success(f"Gráfico disponível — atualizado em {atual.get('atualizado_em', '')}")
                st.image(atual["imagem"], width=550)
            else:
                # Se outro analista excluiu o gráfico, descarta confirmação obsoleta.
                st.session_state.pop(chave_confirmacao, None)
                st.info(
                    "Gráfico ainda não adicionado hoje. "
                    "Este logger não aparecerá na seção de evidências do PDF."
                )

            if PODE_EDITAR_FAROL and atual:
                if st.button(
                    "🗑️ Excluir gráfico",
                    key=f"farol_btn_excluir_grafico_{codigo}_{chave}",
                ):
                    st.session_state[chave_confirmacao] = True
                    st.rerun()

                if st.session_state.get(chave_confirmacao):
                    st.warning(
                        f"Excluir a imagem do logger {nome} para todos os analistas? "
                        "O gráfico deixará de aparecer nos novos relatórios."
                    )
                    col_confirmar, col_cancelar = st.columns(2)
                    with col_confirmar:
                        if st.button(
                            "✅ Confirmar exclusão",
                            key=f"farol_confirmar_excluir_{codigo}_{chave}",
                            type="primary",
                        ):
                            try:
                                exigir_edicao_farol()
                                removido = excluir_grafico(
                                    SPREADSHEET_ID, logger,
                                    imagem_esperada=atual["imagem"],
                                )
                                st.session_state.pop(chave_confirmacao, None)
                                st.session_state[chave_versao] = (
                                    st.session_state.get(chave_versao, 0) + 1
                                )
                                # PDFs previamente preparados devem ser regenerados
                                # para não oferecer a imagem removida como evidência.
                                invalidar_pdfs_apos_mudanca_grafico()
                                st.session_state["farol_confirmacao_print"] = (
                                    codigo,
                                    (f"Imagem do logger {nome} excluída. "
                                     "Você já pode colar outra captura.")
                                    if removido else
                                    f"O logger {nome} já estava sem imagem.",
                                )
                                st.rerun()
                            except Exception as erro:
                                st.error(f"Não foi possível excluir o gráfico de {nome}: {erro}")
                    with col_cancelar:
                        if st.button(
                            "Cancelar", key=f"farol_cancelar_exclusao_{codigo}_{chave}"
                        ):
                            st.session_state.pop(chave_confirmacao, None)
                            st.rerun()

            if PODE_EDITAR_FAROL:
                # Renovar a chave após envio/exclusão evita reutilização do
                # widget de colagem anterior durante uma nova captura Ctrl+V.
                versao = st.session_state.get(chave_versao, 0)
                with st.container():
                    captura = st.chat_input(
                        placeholder=f"Clique aqui, pressione Ctrl+V e envie o print de {nome}",
                        accept_file=True,
                        file_type=["png", "jpg", "jpeg"],
                        key=f"farol_colar_logger_{codigo}_{chave}_v{versao}",
                    )
                if captura is not None:
                    arquivos = list(captura.files)
                    if not arquivos:
                        st.warning(
                            "Nenhuma imagem foi colada. Faça a captura e "
                            "pressione Ctrl+V neste campo."
                        )
                    elif len(arquivos) != 1:
                        st.warning("Cole somente um gráfico por vez neste logger.")
                    else:
                        try:
                            exigir_edicao_farol()
                            arquivo = arquivos[0]
                            if arquivo.size > 6 * 1024 * 1024:
                                raise ValueError("A captura não pode ultrapassar 6 MB.")
                            resultado = salvar_grafico(
                                SPREADSHEET_ID, logger, arquivo.getvalue()
                            )
                            st.session_state.pop(chave_confirmacao, None)
                            st.session_state[chave_versao] = versao + 1
                            invalidar_pdfs_apos_mudanca_grafico()
                            st.session_state["farol_confirmacao_print"] = (
                                codigo,
                                f"Gráfico do logger {nome} {resultado} e compartilhado "
                                "com os analistas. Gere novamente o PDF para "
                                "visualizar a alteração.",
                            )
                            st.rerun()
                        except Exception as erro:
                            st.error(f"Não foi possível adicionar o gráfico de {nome}: {erro}")
            st.divider()


def gerar_pdf_concentracao(concentracao, os_grupo, ativos, eventos_relacionados, raio, imagem_mapa=None, indice_imagens=None):
    """Gera PDF em memória, sem gravar ou alterar informações no Sheets."""
    fonte_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fonte_negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.isfile(fonte_regular) and "FarolDejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("FarolDejaVu", fonte_regular))
        pdfmetrics.registerFont(TTFont("FarolDejaVu-Bold", fonte_negrito))
        pdfmetrics.registerFontFamily("FarolDejaVu", normal="FarolDejaVu", bold="FarolDejaVu-Bold")
    fonte = "FarolDejaVu" if "FarolDejaVu" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.4*cm,
                            rightMargin=1.4*cm, topMargin=1.3*cm, bottomMargin=1.3*cm)
    estilos = getSampleStyleSheet()
    normal = ParagraphStyle("FarolNormal", parent=estilos["Normal"], fontName=fonte, fontSize=8.5, leading=12)
    pequeno = ParagraphStyle("FarolPequeno", parent=normal, fontSize=7, leading=10)
    titulo = ParagraphStyle("FarolTitulo", parent=normal, fontSize=17, leading=22, spaceAfter=8)
    subtitulo = ParagraphStyle("FarolSubtitulo", parent=normal, fontSize=11, leading=16, spaceBefore=12, spaceAfter=6)
    historia = []
    def p(valor, estilo=pequeno):
        return Paragraph(xml_escape(texto_relatorio(valor)), estilo)
    def tabela(cabecalho, linhas, larguras):
        dados = [[p(v) for v in cabecalho]] + [[p(v) for v in linha] for linha in linhas]
        t = Table(dados, colWidths=larguras, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EFF7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D4DCE5")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t
    historia.append(p("Farol Operacional — " + str(concentracao["ID_CONCENTRACAO"]), titulo))
    historia.append(p("RELATÓRIO INDIVIDUAL DE CONCENTRAÇÃO | VISÃO TERRITORIAL", pequeno))
    historia.append(Spacer(1, .25*cm))
    if imagem_mapa:
        # Ficha em duas colunas: 55% mapa e 45% dados. O restante do PDF permanece tabular.
        largura_mapa, largura_info = 14.15*cm, 11.15*cm
        altura_mapa = 12.1*cm
        mapa_pdf = Image(io.BytesIO(imagem_mapa), width=largura_mapa, height=altura_mapa)
        mapa_pdf.hAlign = "LEFT"
        cidades_ficha = ", ".join(sorted({str(v).strip() for v in os_grupo["Cidade"] if str(v).strip()}))
        info = [
            p("RESUMO OPERACIONAL", subtitulo), Spacer(1, .15*cm),
            p(f"O.S.: {len(os_grupo)}", normal), Spacer(1, .18*cm),
            p(f"Matrículas distintas: {concentracao['QTD_MATRICULAS']}", normal), Spacer(1, .18*cm),
            p(f"Município: {cidades_ficha or 'Não informado'}", normal), Spacer(1, .18*cm),
            p(f"Bairros: {texto_relatorio(concentracao['BAIRROS'])}", normal), Spacer(1, .18*cm),
            p(f"Equipamentos próximos: {len(ativos)}", normal), Spacer(1, .18*cm),
            p(f"Eventos relacionados: {descricao_eventos_pdf(eventos_relacionados)}", normal), Spacer(1, .18*cm),
            p(f"Raio operacional: {raio} m", normal), Spacer(1, .25*cm),
            p("A imagem mostra a concentração e seus equipamentos próximos. A proximidade não comprova vínculo hidráulico ou causalidade.", pequeno),
        ]
        ficha = Table([[mapa_pdf, info]], colWidths=[largura_mapa+.35*cm, largura_info],
                      hAlign="LEFT")
        ficha.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("BACKGROUND", (1,0), (1,0), colors.HexColor("#F4F7FB")),
            ("LEFTPADDING", (0,0), (0,0), 0),
            ("RIGHTPADDING", (0,0), (0,0), 6),
            ("LEFTPADDING", (1,0), (1,0), 12),
            ("TOPPADDING", (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ]))
        historia.append(ficha)
        historia.append(Spacer(1, .25*cm))
    cidades = ", ".join(sorted({str(v).strip() for v in os_grupo["Cidade"] if str(v).strip()}))
    resumo = (
        f"Município(s): {cidades or 'Não informado'} | Bairros: {texto_relatorio(concentracao['BAIRROS'])} | "
        f"O.S.: {len(os_grupo)} | Matrículas: {concentracao['QTD_MATRICULAS']} | "
        f"Raio operacional: {raio} m"
    )
    historia.append(p(resumo, normal))
    historia.append(p(f"Centro: {float(concentracao['LATITUDE']):.6f}, {float(concentracao['LONGITUDE']):.6f}", normal))
    vinculadas, nao_vinculadas = classificar_os_por_evento(os_grupo, eventos_relacionados)
    colunas = ["Nº da O.S", "Matrícula", "Cidade", "Bairro", "Status OS", "Dt. Emissão", "Serviço Executado"]
    for nome_grupo, base in (("Ordens vinculadas aos eventos", vinculadas),
                             ("Ordens não vinculadas", nao_vinculadas)):
        historia.append(p(f"{nome_grupo} ({len(base)})", subtitulo))
        if base.empty:
            historia.append(p("Nenhuma O.S. nesta categoria.", pequeno))
        else:
            linhas = [[r.get(c, "") for c in colunas] for _, r in base.iterrows()]
            historia.append(tabela(colunas, linhas, [2.1*cm, 2.2*cm, 2.5*cm, 3.0*cm, 3.0*cm, 3.3*cm, 10.0*cm]))
    historia.append(p("Poços / ativos e loggers próximos", subtitulo))
    if ativos.empty:
        historia.append(p("Nenhum equipamento encontrado no raio operacional.", normal))
    else:
        cols = ["Tipo", "Identificação", "Nome", "Município", "Distância"]
        historia.append(tabela(cols, [[r.get(c, "") for c in cols] for _, r in ativos.iterrows()],
                                [3.0*cm, 4.0*cm, 10.0*cm, 5.0*cm, 4.1*cm]))
    historia.append(p("Eventos relacionados por município e bairro", subtitulo))
    if eventos_relacionados.empty:
        historia.append(p("Nenhum evento relacionado por correspondência de município e bairro.", normal))
    else:
        cols = ["Protocolo", "Status", "Cidade", "Bairros coincidentes", "Início", "Término Real", "Serviço"]
        historia.append(tabela(cols, [[r.get(c, "") for c in cols] for _, r in eventos_relacionados.iterrows()],
                                [3.1*cm, 2.5*cm, 3.0*cm, 4.5*cm, 4.2*cm, 4.2*cm, 4.6*cm]))
    historia.extend(secao_pdf_acompanhamento(concentracao, largura=26.6*cm))
    historia.extend(evidencias_graficas_pdf(ativos, indice_imagens, fonte=fonte))
    historia.append(Spacer(1, 0.3*cm))
    historia.append(p("Nota: a relação de eventos é indicativa, baseada na coincidência de bairros e município; não comprova causalidade.", pequeno))
    doc.build(historia)
    return buffer.getvalue()


def exibir_relatorio_concentracao(codigo, concentracoes, pontos_cluster, proximidades, eventos,
                                  raio, bairros_geograficos=None, dados_preparados=None):
    """Renderiza somente a ficha solicitada; reaproveita o cruzamento já calculado.

    Nenhuma outra concentração deve criar tabelas, widgets de logger ou relatórios
    durante um rerun decorrente da edição do acompanhamento operacional.
    """
    registro = concentracoes[concentracoes["ID_CONCENTRACAO"] == codigo]
    if registro.empty:
        st.warning("Concentração não encontrada com os filtros atuais.")
        return
    concentracao = registro.iloc[0]
    if dados_preparados is None:
        os_grupo, ativos, eventos_relacionados = dados_relatorio_concentracao(
            concentracao, pontos_cluster, proximidades, eventos, bairros_geograficos
        )
    else:
        os_grupo, ativos, eventos_relacionados = dados_preparados
    cidades = sorted({str(c).strip() for c in os_grupo["Cidade"] if str(c).strip()})
    st.subheader(f"{codigo} — {', '.join(cidades) or 'Município não informado'}")
    st.caption(f"Bairros: {concentracao['BAIRROS'] or 'Não informados'}")
    a, b, c = st.columns(3)
    a.metric("O.S.", len(os_grupo))
    b.metric("Matrículas distintas", int(concentracao["QTD_MATRICULAS"]))
    c.metric("Equipamentos próximos", len(ativos))
    st.markdown(
        f"**Eventos relacionados:** {len(eventos_relacionados)} evento(s) identificado(s)"
        if not eventos_relacionados.empty else "**Eventos relacionados:** nenhum evento identificado"
    )
    st.caption(
        f"Centro geográfico: {float(concentracao['LATITUDE']):.6f}, "
        f"{float(concentracao['LONGITUDE']):.6f} | Raio operacional: {raio} m"
    )
    vinculadas, nao_vinculadas = classificar_os_por_evento(os_grupo, eventos_relacionados)
    colunas_os = ["Nº da O.S", "Matrícula", "Cidade", "Bairro", "Endereço",
                  "Status OS", "Dt. Emissão", "Serviço Executado",
                  "Evento(s) vinculado(s)", "Critério do vínculo"]
    for nome_grupo, base in (("Ordens vinculadas aos eventos", vinculadas),
                             ("Ordens não vinculadas", nao_vinculadas)):
        st.markdown(f"#### {nome_grupo} ({len(base)})")
        st.dataframe(base.reindex(columns=colunas_os).fillna(""), width="stretch", hide_index=True)
    st.markdown("#### Poços / ativos e loggers próximos")
    if ativos.empty:
        st.info("Nenhum equipamento dentro do raio operacional desta concentração.")
    else:
        st.dataframe(ativos[["Tipo", "Identificação", "Nome", "Município", "Distância"]],
                     width="stretch", hide_index=True)
    st.markdown("#### Eventos relacionados")
    st.caption("Correspondência por município e bairro impactado; não estabelece relação causal.")
    if eventos_relacionados.empty:
        st.info("Nenhum evento com município e bairro correspondente.")
    else:
        colunas_eventos = ["Protocolo", "Status", "Cidade", "Bairros coincidentes", "Início", "Término Real", "Serviço", "Descrição do Serviço"]
        st.dataframe(eventos_relacionados.reindex(columns=colunas_eventos).fillna(""),
                     width="stretch", hide_index=True)
    # A colagem de prints não altera os dados operacionais do Farol.
    interface_graficos_logger(ativos, codigo)
    # Captura cartográfica apenas sob demanda; evita Chromium em cada rerun do Streamlit.
    if st.button("📸 Preparar PDF com mapa da concentração", key=f"preparar_pdf_{codigo}"):
        try:
            with st.spinner("Capturando mapa aproximado e preparando relatório..."):
                mapa_ficha = mapa_exclusivo_concentracao(concentracao, os_grupo, ativos)
                imagem_ficha = capturar_mapa_folium(mapa_ficha, largura=800, altura=690)
                st.session_state[f"farol_pdf_individual_{codigo}"] = gerar_pdf_concentracao(
                    concentracao, os_grupo, ativos, eventos_relacionados, raio, imagem_ficha,
                    indice_imagens=indice_do_dia(SPREADSHEET_ID, dia_graficos()),
                )
        except Exception as erro:
            st.error(f"Não foi possível gerar o PDF cartográfico: {erro}")
    if st.session_state.get(f"farol_pdf_individual_{codigo}"):
        st.download_button("📄 Baixar relatório em PDF", data=st.session_state[f"farol_pdf_individual_{codigo}"],
                           file_name=f"Farol_{codigo}_{datetime.now(FUSO_FAROL):%Y%m%d}.pdf",
                           mime="application/pdf", key=f"baixar_pdf_{codigo}")


@st.dialog("📥 Importação de bases", width="large")
def modal_importacao():
    if not pode_executar_acao("3", "importar"):
        st.error("Sem permissão para importar dados.")
        return
    if not PODE_EDITAR_FAROL:
        st.error("Acesso somente para visualização.")
        return
    st.subheader("Importação das bases")
    if historico_v2_ativo:
        inicio_importacao, fim_importacao = janela_historico_v2()
        st.info(
            f"Histórico ativo: importe O.S. emitidas de "
            f"{inicio_importacao:%d/%m/%Y} a {fim_importacao:%d/%m/%Y} "
            "(8 dias corridos, inclusive). É permitido reunir diferentes dias "
            "e arquivos em um mesmo lote. Datas inválidas ou fora da janela "
            "interrompem a importação completa. EVENTOS não são afetados."
        )
    else:
        st.info(
            "O histórico de 8 dias passa a valer após ativação explícita. "
            "Quando ativado, permite importar retroativamente O.S. dos 8 dias "
            "corridos mais recentes. EVENTOS permanecem preservados."
        )

    c1, c2 = st.columns(2)
    with c1:
        arquivos_os = st.file_uploader(
            "Bases diárias de O.S. (um ou vários arquivos)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="upload_os",
            help="Permite misturar arquivos Power BI e bases alternativas. Todos são consolidados antes da importação.",
        )
        if arquivos_os:
            try:
                preparados, detalhes, linhas, duplicados = consolidar_arquivos_importacao(
                    arquivos_os, "PONTOS", validar_janela=historico_v2_ativo
                )
                st.caption(f"{len(arquivos_os)} arquivo(s) selecionado(s)")
                st.dataframe(detalhes, width="stretch", hide_index=True)
                st.write(f"**{linhas:,}** registros lidos · **{len(preparados):,}** O.S. únicas")
                if duplicados:
                    st.warning(
                        f"{duplicados:,} registro(s) repetido(s) no lote. "
                        "Para o mesmo protocolo, prevalece a última ocorrência na ordem dos arquivos enviados."
                    )
                st.dataframe(preparados.head(10).fillna(""), width="stretch", hide_index=True)
                sem_geo = int(preparados[["Latitude", "Longitude"]].isna().any(axis=1).sum())
                if sem_geo:
                    st.warning(
                        f"{sem_geo} O.S. sem coordenadas válidas serão armazenadas, "
                        "mas não aparecerão no mapa."
                    )
                st.caption(
                    "A prévia já reúne todos os arquivos. A importação gravará uma única vez "
                    "na aba PONTOS, respeitando a janela móvel de 8 dias se o histórico estiver ativo."
                )
                if st.button("Importar O.S. para PONTOS", type="primary", key="btn_importar_os"):
                    # Valida o lote completo ANTES de executar qualquer escrita.
                    if ler_controle_historico_v2().get("ativo") == "SIM":
                        preparados = validar_importacao_diaria_v2(preparados)
                    inseridas, total = upsert_pontos(preparados)
                    registrar_atualizacao_base("PONTOS")
                    st.success(
                        f"Importação concluída: {inseridas:,} O.S. únicas processadas "
                        f"de {len(arquivos_os)} arquivo(s). PONTOS contém {total:,} registros."
                    )
                    st.rerun()
            except Exception as erro:
                st.error(f"Erro no lote de O.S.: {erro}")

    with c2:
        arquivos_eventos = st.file_uploader(
            "Bases de eventos (um ou vários arquivos)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="upload_eventos",
        )
        if arquivos_eventos:
            try:
                preparados_e, detalhes_e, linhas_e, duplicados_e = consolidar_arquivos_importacao(
                    arquivos_eventos, "EVENTOS"
                )
                st.caption(f"{len(arquivos_eventos)} arquivo(s) selecionado(s)")
                st.dataframe(detalhes_e, width="stretch", hide_index=True)
                st.write(
                    f"**{linhas_e:,}** registros lidos · "
                    f"**{len(preparados_e):,}** eventos únicos"
                )
                if duplicados_e:
                    st.info(
                        f"{duplicados_e:,} registro(s) repetido(s) foram mesclados "
                        "pelo protocolo/chave do evento, preservando campos preenchidos."
                    )
                st.dataframe(preparados_e.head(10).fillna(""), width="stretch", hide_index=True)
                st.caption("A importação consolidada será gravada uma única vez na aba EVENTOS.")
                if st.button("Importar eventos para EVENTOS", type="primary", key="btn_importar_eventos"):
                    processados, total = upsert_eventos(preparados_e)
                    registrar_atualizacao_base("EVENTOS")
                    st.success(
                        f"Importação concluída: {processados:,} evento(s) único(s) "
                        f"de {len(arquivos_eventos)} arquivo(s). EVENTOS contém {total:,} registros."
                    )
                    st.rerun()
            except Exception as erro:
                st.error(f"Erro no lote de eventos: {erro}")

    st.divider()
    st.subheader("Resumo do armazenamento")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Poços / Ativos", len(df_pocos))
    m2.metric("Loggers", len(df_loggers))
    m3.metric("O.S. em PONTOS", len(df_pontos))
    m4.metric("Eventos", len(df_eventos))


def atualizar_evento_cadastrado(indice, original, alteracoes):
    """Atualiza somente as células editadas, com proteção contra edição concorrente."""
    exigir_edicao_farol()
    aba = obter_aba_para_leitura(NOME_ABA_EVENTOS, SPREADSHEET_ID)
    valores = aba.get_all_values()
    if not valores or indice + 1 >= len(valores):
        raise ValueError("Evento não encontrado. Reabra a janela e tente novamente.")
    cabecalho = valores[0]
    linha_atual = valores[indice + 1]
    mapa_atual = dict(zip(cabecalho, linha_atual))
    # Garante que outra pessoa não alterou o evento desde sua abertura.
    for campo in CABECALHO_EVENTOS:
        if str(mapa_atual.get(campo, "")) != str(original.get(campo, "")):
            raise ValueError("Este evento foi alterado na planilha. Atualize a página antes de salvar.")
    # A proteção fica em aba independente, para sobreviver à substituição
    # completa da aba EVENTOS durante futuras importações.
    area_antiga = str(original.get("Áreas Impactadas", ""))
    area_nova = str(alteracoes.get("Áreas Impactadas", area_antiga))
    if area_nova != area_antiga:
        registrar_area_editada(chave_evento(original), area_nova)
    for campo, novo_valor in alteracoes.items():
        if campo not in cabecalho:
            raise ValueError(f"Coluna não encontrada na planilha: {campo}")
        pos = cabecalho.index(campo)
        antigo = str(original.get(campo, ""))
        if novo_valor != antigo:
            from gspread.utils import rowcol_to_a1
            aba.update(range_name=rowcol_to_a1(indice + 2, pos + 1),
                       values=[[novo_valor]], value_input_option="RAW")
    invalidar_cache()
    eventos_georreferenciados.clear()


@st.dialog("⚡ Eventos x O.S.", width="large")
def modal_eventos():
    """Uma tabela de eventos, editável por seleção, e um gráfico filtrado por município."""
    st.subheader("Eventos x O.S. por bairro")
    if df_eventos.empty:
        st.info("A aba EVENTOS ainda não possui dados.")
        return

    # Selecionar uma cidade evita misturar, no mesmo gráfico, bairros homônimos
    # de municípios diferentes. O mesmo filtro alimenta a tabela de edição.
    eventos_base = df_eventos.reset_index(drop=True)
    cidades = sorted({
        str(c).strip() for c in eventos_base["Cidade"].fillna("")
        if str(c).strip()
    }, key=normalizar_municipio)
    if cidades:
        municipio_selecionado = st.selectbox(
            "Município — eventos e gráfico",
            cidades,
            key="farol_eventos_municipio_filtro",
        )
        cidade_chave = normalizar_municipio(municipio_selecionado)
        eventos_visiveis = eventos_base.loc[
            eventos_base["Cidade"].map(normalizar_municipio) == cidade_chave
        ].copy()
    else:
        municipio_selecionado = ""
        cidade_chave = ""
        eventos_visiveis = eventos_base.copy()
        st.caption("Os eventos cadastrados não possuem município informado.")

    # Cada linha do relacionamento corresponde a um bairro impactado por evento.
    # A análise é calculada uma única vez e exibida na própria tabela de eventos.
    rel = (
        relacionar_eventos_pontos(eventos_base, df_pontos, bairros)
        if not df_pontos.empty else pd.DataFrame()
    )
    if not rel.empty:
        rel_municipio = rel.loc[
            rel["Cidade"].map(normalizar_municipio) == cidade_chave
        ].copy() if cidade_chave else rel.copy()
        resumo_os = (
            rel.groupby("_indice_evento", as_index=True)["O.S. encontradas"]
            .sum().astype(int)
        )
        resumo_bairros = (
            rel.groupby("_indice_evento", as_index=True)["Bairro"]
            .agg(lambda col: ", ".join(dict.fromkeys(str(v) for v in col if str(v).strip())))
        )
        resumo_criterios = (
            rel.groupby("_indice_evento", as_index=True)["Critérios de associação"]
            .agg(lambda col: " | ".join(str(v) for v in col if str(v).strip()))
        )
    else:
        rel_municipio = pd.DataFrame()
        resumo_os = pd.Series(dtype=int)
        resumo_bairros = pd.Series(dtype=str)
        resumo_criterios = pd.Series(dtype=str)

    st.markdown("### Eventos armazenados e O.S. relacionadas")
    colunas_evento = [c for c in [
        "Data", "Status", "Cidade", "Serviço", "Protocolo",
        "Áreas Impactadas", "Descrição do Serviço"
    ] if c in eventos_visiveis.columns]
    # Preserva o índice de linha original da aba EVENTOS para salvar a edição
    # correta, mesmo quando a tabela estiver filtrada por município.
    indices_visiveis = list(eventos_visiveis.index)
    tabela = eventos_visiveis.loc[:, colunas_evento].copy()
    tabela["O.S. encontradas"] = [int(resumo_os.get(i, 0)) for i in indices_visiveis]
    tabela["Bairros com O.S."] = [str(resumo_bairros.get(i, "")) for i in indices_visiveis]
    tabela["Critérios de associação"] = [
        str(resumo_criterios.get(i, "")) for i in indices_visiveis
    ]
    tabela = tabela.reset_index(drop=True)

    if PODE_EDITAR_FAROL:
        tabela.insert(0, "Selecionar", False)
        selecao = st.data_editor(
            tabela,
            hide_index=True,
            width="stretch",
            height=330,
            disabled=[c for c in tabela.columns if c != "Selecionar"],
            column_config={
                "Selecionar": st.column_config.CheckboxColumn(
                    "Selecionar", help="Marque um único evento para editar.",
                    default=False, required=True,
                ),
                "O.S. encontradas": st.column_config.NumberColumn("O.S. encontradas", format="%d"),
            },
            key=f"farol_selecao_eventos_{cidade_chave or 'SEM_CIDADE'}",
        )
        marcados = [int(i) for i in selecao.index[
            selecao["Selecionar"].fillna(False).astype(bool)
        ].tolist()]
        if len(marcados) > 1:
            st.warning("Marque somente um evento para editar.")
        indice_evento = (
            indices_visiveis[marcados[0]] if len(marcados) == 1 else None
        )
        # Botão diretamente abaixo da ÚNICA tabela.
        if st.button(
            "✏️ Editar evento selecionado",
            key="farol_abrir_edicao_evento_tabela",
            disabled=indice_evento is None,
            type="primary",
        ):
            st.session_state["farol_evento_em_edicao"] = indice_evento

        indice_edicao = st.session_state.get("farol_evento_em_edicao")
        if indice_edicao not in indices_visiveis:
            indice_edicao = None
        if isinstance(indice_edicao, int) and 0 <= indice_edicao < len(eventos_base):
            registro = eventos_base.iloc[indice_edicao].to_dict()
            st.markdown("### ✏️ Corrigir evento cadastrado")
            st.caption(
                f"Evento selecionado: {registro.get('Protocolo') or 'Sem protocolo'} · "
                f"{registro.get('Cidade', '')} · {registro.get('Serviço', '')}. "
                "A correção de Áreas Impactadas será preservada nas próximas importações."
            )
            with st.form(f"farol_edicao_evento_{indice_edicao}"):
                area_editada = st.text_area(
                    "Áreas Impactadas",
                    value=str(registro.get("Áreas Impactadas", "") or ""),
                    height=110,
                    help="Somente este campo pode ser alterado.",
                )
                salvar = st.form_submit_button("💾 Salvar área impactada", width="stretch")
            if salvar:
                try:
                    atualizar_evento_cadastrado(
                        indice_edicao, registro, {"Áreas Impactadas": area_editada}
                    )
                    st.session_state.pop("farol_evento_em_edicao", None)
                    st.success("Evento atualizado. O mapa e as associações serão recalculados.")
                    st.rerun()
                except Exception as erro:
                    st.error(f"Não foi possível salvar o evento: {erro}")
    else:
        st.dataframe(tabela, width="stretch", hide_index=True, height=330)

    # Diagnóstico espacial dos nomes deste evento — usa SOMENTE as feições
    # já carregadas. Não adiciona leituras ao Google Sheets nem recria o mapa.
    with st.expander("🔎 Diagnóstico dos bairros não reconhecidos", expanded=False):
        if eventos_visiveis.empty:
            st.info("Nenhum evento disponível neste município.")
        else:
            opcoes_diag = list(eventos_visiveis.index)
            selecao_diag = st.selectbox(
                "Evento para conferir a abrangência geográfica",
                opcoes_diag,
                format_func=lambda idx: (
                    f"{str(eventos_base.iloc[idx].get('Protocolo', '') or 'Sem protocolo')} — "
                    f"{str(eventos_base.iloc[idx].get('Serviço', '') or 'Sem serviço')} "
                    f"({str(eventos_base.iloc[idx].get('Data', '') or 'Sem data')})"
                ),
                key=f"farol_diag_bairros_{cidade_chave or 'SEM_CIDADE'}",
            )
            evento_diag = eventos_base.iloc[selecao_diag]
            cidade_diag = evento_diag.get("Cidade", "")
            valor_areas = str(evento_diag.get("Áreas Impactadas", "") or "")
            st.caption("Texto efetivamente gravado em EVENTOS → Áreas Impactadas:")
            st.code(valor_areas if valor_areas.strip() else "(campo vazio)", language=None)
            municipal_diag = evento_abrange_municipio(valor_areas)
            areas_diag = extrair_bairros_evento(valor_areas, cidade_diag, bairros)
            catalogo_diag = indice_nomes_bairros(bairros, cidade_diag)
            if municipal_diag:
                st.info("Evento declarado para todo o município. Os polígonos existentes são usados como referência.")
            if not areas_diag:
                st.warning(
                    "Nenhum bairro foi extraído do campo Áreas Impactadas. "
                    "Se os bairros estiverem apenas na mensagem do WhatsApp ou na "
                    "Descrição do Serviço, informe a lista em Áreas Impactadas na edição do evento."
                )
            else:
                diagnostico = pd.DataFrame([
                    {
                        "Bairro interpretado": a["bairro"],
                        "Polígono no município": (
                            "Encontrado" if a["bairro"] in catalogo_diag else "Não encontrado"
                        ),
                    }
                    for a in areas_diag
                ])
                st.dataframe(diagnostico, hide_index=True, width="stretch")
                encontrados_diag = int((diagnostico["Polígono no município"] == "Encontrado").sum())
                st.caption(
                    f"Bairros reconhecidos no arquivo cartográfico: "
                    f"{encontrados_diag} de {len(diagnostico)} informados no evento."
                )
                ausentes = diagnostico.loc[
                    diagnostico["Polígono no município"] == "Não encontrado",
                    "Bairro interpretado",
                ].tolist()
                if ausentes:
                    st.warning(
                        "Sem correspondência exata na base geográfica de "
                        f"{cidade_diag}: {', '.join(ausentes)}. "
                        "Confirme a nomenclatura no arquivo GPKG/KMZ/KML do município. "
                        "O Farol não marca bairros por aproximação de nomes."
                    )
                else:
                    st.success("Todos os bairros interpretados têm polígonos carregados no município.")
            st.caption(
                f"Cobertura cartográfica carregada: {len(catalogo_diag)} "
                "nome(s) distinto(s) de bairro neste município."
            )
            with st.expander("Ver nomes de bairros existentes na base cartográfica", expanded=False):
                if catalogo_diag:
                    st.write(", ".join(sorted(catalogo_diag)))
                else:
                    st.warning(
                        "Nenhum polígono foi carregado para este município. "
                        "Confira os arquivos de geodados/municipios."
                    )

    # O gráfico utiliza APENAS a cidade selecionada, nunca mistura municípios.
    st.markdown("### Distribuição das O.S. nos bairros impactados")
    if rel_municipio.empty:
        st.info("Nenhuma O.S. encontrada nos bairros dos eventos deste município.")
    else:
        fig = px.bar(
            rel_municipio.sort_values("O.S. encontradas", ascending=False),
            x="Bairro",
            y="O.S. encontradas",
            color="Status",
            hover_data=["Protocolo", "Matrículas", "Parcial"],
            title=f"O.S. nos bairros impactados — {municipio_selecionado}" if municipio_selecionado
                  else "O.S. nos bairros impactados",
        )
        st.plotly_chart(fig, width="stretch")


def capturar_mapa_folium(mapa, largura=1100, altura=600):
    """Renderiza o Folium em Chromium e captura o mapa com as camadas visíveis.

    Requer `playwright` no requirements.txt e Chromium no sistema (packages.txt).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as erro:
        raise RuntimeError("Instale playwright no requirements.txt para gerar o relatório cartográfico.") from erro

    html = mapa.get_root().render()
    # A captura deve ter uma proporção próxima da visualização operacional.
    html = html.replace('</head>', f"""<style>
        html, body {{margin:0 !important; padding:0 !important; width:{largura}px !important; height:{altura}px !important; overflow:hidden !important;}}
        .folium-map {{width:{largura}px !important; height:{altura}px !important;}}
        </style></head>""", 1)
    with tempfile.TemporaryDirectory(prefix="farol_mapa_") as pasta:
        arquivo = os.path.join(pasta, "mapa.html")
        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(html)
        with sync_playwright() as playwright:
            executavel = (os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
                          or shutil.which("chromium-browser") or shutil.which("google-chrome"))
            kwargs = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if executavel:
                kwargs["executable_path"] = executavel
            try:
                browser = playwright.chromium.launch(**kwargs)
            except Exception as erro:
                raise RuntimeError("Chromium indisponível. Configure packages.txt com 'chromium' e requirements.txt com 'playwright'.") from erro
            try:
                pagina = browser.new_page(viewport={"width": largura, "height": altura}, device_scale_factor=1.5)
                pagina.goto(Path(arquivo).as_uri(), wait_until="load", timeout=45000)
                pagina.wait_for_function("typeof L !== 'undefined' && document.querySelector('.leaflet-map-pane') !== null", timeout=30000)
                pagina.wait_for_timeout(2400)
                # A imagem não é utilizável se o mapa de fundo não carregou.
                tiles = pagina.locator(".leaflet-tile-loaded")
                if tiles.count() == 0:
                    raise RuntimeError("As imagens de fundo do OpenStreetMap não carregaram no navegador de captura. Verifique a conectividade do servidor.")
                return pagina.locator(".folium-map").first.screenshot(type="png")
            finally:
                browser.close()


def cidades_da_concentracao(concentracao, pontos_cluster):
    if "_grupo" not in pontos_cluster.columns or "Cidade" not in pontos_cluster.columns:
        return []
    grupo = pontos_cluster[pontos_cluster["_grupo"] == concentracao["GRUPO"]]
    return sorted({str(v).strip() for v in grupo["Cidade"] if str(v).strip()}, key=normalizar_texto)


def filtrar_concentracoes_relatorio(concentracoes, pontos_cluster, cidades):
    if not cidades:
        return concentracoes.iloc[0:0].copy()
    nomes = {normalizar_municipio(c) for c in cidades}
    indices = [i for i, c in concentracoes.iterrows()
               if any(normalizar_municipio(nome) in nomes
                      for nome in cidades_da_concentracao(c, pontos_cluster))]
    return concentracoes.loc[indices].copy()


def limites_cidades_relatorio(bairros, cidades):
    """Obtém o enquadramento a partir das feições cartográficas disponíveis."""
    selecionadas = {normalizar_municipio(c) for c in cidades}
    coordenadas = []
    for bairro in bairros:
        if normalizar_municipio(bairro.get("municipio", "")) not in selecionadas:
            continue
        for poligono in bairro.get("poligonos", []):
            for ponto in poligono:
                try:
                    coordenadas.append((float(ponto[0]), float(ponto[1])))
                except (ValueError, TypeError, IndexError):
                    continue
    if not coordenadas:
        return None
    return [[min(y for y, x in coordenadas), min(x for y, x in coordenadas)],
            [max(y for y, x in coordenadas), max(x for y, x in coordenadas)]]


def protocolo_os_relatorio(registro):
    """Número de O.S. com ano da emissão, sem fabricar ano quando ausente."""
    numero = str(registro.get("Nº da O.S", "") or "").strip()
    if not numero or numero.lower() == "nan":
        return ""
    if re.search(r"/\d{4}-1$", numero):
        return numero
    emissao = str(registro.get("Dt. Emissão", "") or "").strip()
    ano = None
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            ano = datetime.strptime(emissao, formato).year
            break
        except ValueError:
            pass
    if ano is None:
        encontrado = re.search(r"(?<!\d)(20\d{2})(?!\d)", emissao)
        if encontrado:
            ano = int(encontrado.group(1))
    return f"{numero}/{ano}-1" if ano else numero


def planilha_os_relatorio(concentracoes, pontos_cluster):
    """CSV tabular, pronto para Excel e para selecionar O.S. por concentração."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\n")
    escritor.writerow(["Concentração", "Município", "Bairro", "O.S. / Ano", "Matrícula", "Status", "Dt. Emissão", "Serviço Executado"])
    for _, c in concentracoes.iterrows():
        if "_grupo" not in pontos_cluster.columns:
            continue
        grupo = pontos_cluster[pontos_cluster["_grupo"] == c["GRUPO"]]
        for _, registro in grupo.iterrows():
            escritor.writerow([str(c["ID_CONCENTRACAO"]), str(registro.get("Cidade", "")),
                              str(registro.get("Bairro", "")), protocolo_os_relatorio(registro),
                              str(registro.get("Matrícula", "")), str(registro.get("Status OS", "")),
                              str(registro.get("Dt. Emissão", "")), str(registro.get("Serviço Executado", ""))])
    return ("\ufeff" + saida.getvalue()).encode("utf-8")


def nome_arquivo_relatorio(cidades=None, extensao="pdf"):
    data = datetime.now(FUSO_FAROL).strftime("%d-%m-%Y")
    if not cidades:
        base = "Relatorio_Geral"
    else:
        nomes = [re.sub(r"[^A-Za-z0-9_-]", "_", unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode("ascii")) for c in cidades]
        base = "Relatorio_" + "_".join(nomes)
    return f"Farol_{base}_{data}.{extensao}"


def mapa_relatorio_concentracoes(concentracoes, pontos_cluster, concentracao=None,
                                  cidades=None, bairros=None, pocos=None, loggers=None,
                                  panoramico=False, pontos=None, raio_ativos=RAIO_OPERACIONAL_PADRAO):
    """Panorama completo com O.S. e infraestrutura; fichas com equipamentos próximos."""
    mapa = folium.Map(location=[-5.09, -42.80], zoom_start=13, tiles="OpenStreetMap",
                      control_scale=True, zoom_control=False)
    selecionadas = (concentracoes if concentracao is None else
                    concentracoes[concentracoes["ID_CONCENTRACAO"] == concentracao["ID_CONCENTRACAO"]])
    desenhar_areas_mescladas(mapa, selecionadas)
    coordenadas = []
    # No panorama, a camada de O.S. será desenhada uma única vez, incluindo
    # registros dentro e fora das concentrações.
    if panoramico and pontos is not None and not pontos.empty:
        nomes_os = {normalizar_municipio(v) for v in cidades} if cidades else None
        for _, r in pontos.iterrows():
            if nomes_os is not None and normalizar_municipio(r.get("Cidade", "")) not in nomes_os:
                continue
            y = normalizar_coordenada(r.get("Latitude"), "lat")
            x = normalizar_coordenada(r.get("Longitude"), "lon")
            if y is None or x is None:
                continue
            servico = str(r.get("Serviço Executado", "") or "")
            cor = "#F28C28" if re.search(r"(?<!\d)146005(?!\d)", servico) else "#1677D2"
            folium.CircleMarker([y, x], radius=4, color=cor, weight=1,
                                fill=True, fill_color=cor, fill_opacity=.85).add_to(mapa)
    for _, c in selecionadas.iterrows():
        lat, lon = float(c["LATITUDE"]), float(c["LONGITUDE"])
        raio = RAIO_CONCENTRACAO_PADRAO
        if not panoramico:
            if not (isinstance(c.get("CHAVE_MESCLA"), str) and c.get("CHAVE_MESCLA")):
                folium.Circle([lat, lon], radius=raio, color="#8522A5", weight=2.5,
                              fill=True, fill_color="#8522A5", fill_opacity=.09).add_to(mapa)
            folium.Marker([lat, lon], icon=icone_alvo_concentracao_panorama()).add_to(mapa)
        coordenadas.append((lat, lon))
        if "_grupo" not in pontos_cluster.columns:
            continue
        grupo = pontos_cluster[pontos_cluster["_grupo"] == c["GRUPO"]]
        if panoramico:
            continue
        for _, r in grupo.iterrows():
            y = normalizar_coordenada(r.get("Latitude"), "lat")
            x = normalizar_coordenada(r.get("Longitude"), "lon")
            if y is None or x is None:
                continue
            servico = str(r.get("Serviço Executado", "") or "")
            cor = "#F28C28" if re.search(r"(?<!\d)146005(?!\d)", servico) else "#1677D2"
            folium.CircleMarker([y, x], radius=4.5, color=cor, weight=1,
                                fill=True, fill_color=cor, fill_opacity=.92).add_to(mapa)
            coordenadas.append((y, x))
    # Panorama: equipamentos do(s) município(s) selecionado(s), sem mostrar
    # cadastros nacionais inteiros. Individual: apenas equipamentos próximos.
    nomes = {normalizar_municipio(v) for v in cidades} if cidades else None
    if panoramico and pocos is not None and loggers is not None:
        # Exatamente como o mapa operacional: apenas equipamentos próximos
        # das concentrações, nunca o cadastro inteiro da cidade.
        pocos, loggers = filtrar_ativos_proximos(selecionadas, pocos, loggers, raio_ativos)
    for cadastro, eh_logger in ((loggers, True), (pocos, False)):
        if cadastro is None or cadastro.empty:
            continue
        for _, r in cadastro.iterrows():
            y = normalizar_coordenada(r.get("LATITUDE"), "lat")
            x = normalizar_coordenada(r.get("LONGITUDE"), "lon")
            if y is None or x is None:
                continue
            if concentracao is not None:
                # Distância geodésica aproximada em metros, suficiente para filtro visual.
                lat0, lon0 = float(concentracao["LATITUDE"]), float(concentracao["LONGITUDE"])
                dy = (y-lat0)*111320.0
                dx = (x-lon0)*111320.0*math.cos(math.radians(lat0))
                if math.hypot(dx, dy) > raio_ativos:
                    continue
            elif nomes is not None and normalizar_municipio(r.get("MUNICIPIO", "")) not in nomes:
                continue
            nome = str(r.get("IDENTIFICACAO_ATIVO", "") or r.get("NOME_POCO", "") or "Equipamento")
            if panoramico:
                # Panorama: poços/ativos azuis, mantendo a gota; loggers inalterados.
                icone = (folium.Icon(icon="signal", prefix="fa", color="orange") if eh_logger
                         else folium.Icon(icon="tint", prefix="fa", color="blue"))
                folium.Marker([y, x], icon=icone,
                              z_index_offset=0 if eh_logger else 1000).add_to(mapa)
            else:
                # Mesma cor para poços/ativos e loggers somente nos mapas do relatório.
                cor = "#FF8C00"
                folium.CircleMarker([y, x], radius=5, color="#663600", weight=1.2,
                                    fill=True, fill_color=cor, fill_opacity=.95).add_to(mapa)
            if concentracao is not None:
                # Rótulo permanente nas fichas, sem alterar o mapa interativo.
                folium.Marker([y, x], icon=folium.DivIcon(
                    html='<div style="position:relative;left:9px;top:-12px;white-space:nowrap;'
                         'font: bold 11px Arial;background:rgba(255,255,255,.94);'
                         'border:1px solid #aab7c4;border-radius:3px;padding:2px 4px;'
                         'color:#193348;display:inline-block">'+html_escape(nome)+'</div>')).add_to(mapa)
    if panoramico:
        # Desenha os círculos DEPOIS dos equipamentos, garantindo que as
        # concentrações continuem visíveis como na referência enviada.
        for _, c in selecionadas.iterrows():
            lat, lon = float(c["LATITUDE"]), float(c["LONGITUDE"])
            folium.Circle([lat, lon], radius=RAIO_CONCENTRACAO_PADRAO,
                          color="#8522A5", weight=3.2, fill=True,
                          fill_color="#8522A5", fill_opacity=.055).add_to(mapa)
            folium.Marker([lat, lon], icon=icone_alvo_concentracao_panorama()).add_to(mapa)
    if coordenadas:
        if concentracao is not None:
            lat, lon = float(concentracao["LATITUDE"]), float(concentracao["LONGITUDE"])
            raio = RAIO_CONCENTRACAO_PADRAO * 1.08
            dy = raio / 111320.0
            dx = raio / (111320.0 * max(.2, math.cos(math.radians(lat))))
            extremos = coordenadas + [(lat-dy, lon-dx), (lat+dy, lon+dx)]
            bounds = [[min(y for y, _ in extremos), min(x for _, x in extremos)],
                      [max(y for y, _ in extremos), max(x for _, x in extremos)]]
        else:
            extremos = list(coordenadas)
            for _, c in selecionadas.iterrows():
                y, x = float(c["LATITUDE"]), float(c["LONGITUDE"])
                dy = RAIO_CONCENTRACAO_PADRAO / 111320.0
                dx = RAIO_CONCENTRACAO_PADRAO / (111320.0 * max(.2, math.cos(math.radians(y))))
                extremos.extend([(y-dy, x-dx), (y+dy, x+dx)])
            bounds = [[min(y for y, _ in extremos), min(x for _, x in extremos)],
                      [max(y for y, _ in extremos), max(x for _, x in extremos)]]
            if cidades:
                bounds_cidades = limites_cidades_relatorio(bairros or [], cidades)
                if bounds_cidades:
                    bounds = bounds_cidades
        mapa.fit_bounds(bounds, padding=(18, 18))
    return mapa


def gerar_pdf_operacional(concentracoes, pontos, pontos_cluster, proximidades, eventos,
                           bairros, raio, observacoes, mapa_relatorio=None,
                           cidades=None, pocos=None, loggers=None):
    """Panorama territorial seguido de fichas compactas, com O.S. ao lado do mapa."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.2*cm,
                            rightMargin=1.2*cm, topMargin=1.2*cm, bottomMargin=1.2*cm)
    estilos = getSampleStyleSheet()
    normal = ParagraphStyle("FarolGeralNormal2", parent=estilos["Normal"], fontSize=8.4, leading=11.5)
    pequeno = ParagraphStyle("FarolGeralPequeno2", parent=normal, fontSize=7.1, leading=9.3)
    titulo = ParagraphStyle("FarolGeralTitulo2", parent=estilos["Heading2"], fontSize=12.5, leading=16, spaceAfter=8)
    historia = [Paragraph("Farol Operacional — Relatório de concentrações", estilos["Title"]),
                Paragraph("Gerado em " + datetime.now(FUSO_FAROL).strftime("%d/%m/%Y %H:%M") +
                          (" · Municípios: " + ", ".join(cidades) if cidades else " · Visão geral"), normal),
                Spacer(1, 10)]
    if concentracoes.empty:
        historia.append(Paragraph("Nenhuma concentração identificada para a seleção.", normal))
    else:
        mapa_geral = mapa_relatorio_concentracoes(concentracoes, pontos_cluster,
                    cidades=cidades, bairros=bairros, pocos=pocos, loggers=loggers,
                    panoramico=True, pontos=pontos, raio_ativos=raio)
        imagem_geral = capturar_mapa_folium(mapa_geral, largura=1280, altura=700)
        # Mantém título, imagem e legenda na MESMA página.
        # Altura calculada para caber no A4 paisagem com cabeçalho e margens.
        historia.append(KeepTogether([
            Paragraph("Panorama territorial das concentrações", titulo),
            Image(io.BytesIO(imagem_geral), width=26.0*cm, height=14.22*cm),
            Spacer(1, 4),
            Paragraph(f"Concentrações: {len(concentracoes)} · O.S., concentrações e equipamentos operacionais.", pequeno),
        ]))
        historia.append(PageBreak())
        # Imagens do dia: lidas uma única vez para todo o relatório operacional.
        indice_graficos = indice_do_dia(SPREADSHEET_ID, dia_graficos())
        for indice, (_, c) in enumerate(concentracoes.iterrows()):
            grupo = (pontos_cluster[pontos_cluster["_grupo"] == c["GRUPO"]]
                     if "_grupo" in pontos_cluster.columns else pd.DataFrame())
            codigo = str(c["ID_CONCENTRACAO"])
            nomes_cidades = cidades_da_concentracao(c, pontos_cluster)
            bairros_concentracao = str(c.get("BAIRROS", "") or "").replace(";", ", ")
            cabecalho = " - ".join([codigo, ", ".join(nomes_cidades) or "MUNICÍPIO NÃO INFORMADO",
                                     bairros_concentracao or "BAIRRO NÃO INFORMADO"])
            historia.append(Paragraph(xml_escape(cabecalho), titulo))
            imagem_foco = capturar_mapa_folium(
                mapa_relatorio_concentracoes(concentracoes, pontos_cluster, concentracao=c,
                                            pocos=pocos, loggers=loggers, raio_ativos=raio),
                largura=760, altura=515)
            imagem = Image(io.BytesIO(imagem_foco), width=13.4*cm, height=9.08*cm)
            resumo = [
                Paragraph(xml_escape(f"{int(c['QTD_OS'])} O.S. · {int(c['QTD_MATRICULAS'])} matrículas distintas"), normal),
                Spacer(1, 5),
            ]
            # Usar os registros dos eventos associados à concentração, e não somente sua quantidade.
            _, ativos_da_concentracao, eventos_da_concentracao = dados_relatorio_concentracao(
                c, pontos_cluster, proximidades, eventos, bairros
            )
            resumo.extend([
                Spacer(1, 4),
                Paragraph("<b>Eventos relacionados:</b> " +
                          xml_escape("; ".join(
                              f"{r.get('Protocolo', '')} — {r.get('Serviço', '')} / {r.get('Código do Ativo', '')} "
                              f"[{r.get('Status', '')}] {r.get('Início', '')} até {r.get('Término Real', '') or 'em andamento'}"
                              for _, r in eventos_da_concentracao.iterrows()
                          ) or "Nenhum evento relacionado"), pequeno),
            ])
            vinculadas, nao_vinculadas = classificar_os_por_evento(grupo, eventos_da_concentracao)
            # Distribuir as duas tabelas dentro da área cinza ao lado do mapa.
            # O espaço abaixo do mapa só recebe linhas que não couberem no painel.
            def linhas_pdf(base):
                return [[protocolo_os_relatorio(r), str(r.get("Matrícula", "")),
                         str(r.get("Status OS", ""))] for _, r in base.iterrows()]

            def tabela_os(linhas, largura_total=11.85*cm, titulo_continuacao=None):
                cab = [[Paragraph("<b>O.S. / Ano</b>", pequeno),
                        Paragraph("<b>Matrícula</b>", pequeno),
                        Paragraph("<b>Status</b>", pequeno)]]
                cab.extend([[Paragraph(xml_escape(str(v)), pequeno) for v in linha] for linha in linhas])
                larguras = [largura_total * 0.37, largura_total * 0.27, largura_total * 0.36]
                # Continuação: incorporar o título ao cabeçalho da tabela,
                # evitando que o ReportLab envie uma tabela longa inteira
                # para a página seguinte só para manter o título junto dela.
                # O cabeçalho da categoria e o das colunas são repetidos.
                linha_cabecalho = 0
                estilos_extras = []
                if titulo_continuacao:
                    cab.insert(0, [Paragraph("<b>" + xml_escape(titulo_continuacao) + "</b>", pequeno), "", ""])
                    linha_cabecalho = 1
                    estilos_extras.extend([
                        ("SPAN", (0, 0), (-1, 0)),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F7FB")),
                        ("TOPPADDING", (0, 0), (-1, 0), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                    ])
                t = Table(cab, colWidths=larguras, repeatRows=linha_cabecalho+1, hAlign="LEFT")
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, linha_cabecalho), (-1, linha_cabecalho), colors.HexColor("#DEE8F2")),
                    ("ROWBACKGROUNDS", (0, linha_cabecalho+1), (-1, -1),
                     [colors.white, colors.HexColor("#F4F7FB")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ] + estilos_extras))
                return t

            categorias = [
                ("Ordens vinculadas aos eventos", linhas_pdf(vinculadas)),
                ("Ordens não vinculadas", linhas_pdf(nao_vinculadas)),
            ]

            def construir_resumo(contagens):
                conteudo = list(resumo)
                for (nome, linhas), quantidade in zip(categorias, contagens):
                    conteudo.extend([Spacer(1, 5),
                                     Paragraph(f"<b>{nome} ({len(linhas)})</b>", pequeno)])
                    if linhas:
                        if quantidade:
                            conteudo.append(tabela_os(linhas[:quantidade]))
                    else:
                        conteudo.append(Paragraph("Nenhuma O.S. nesta categoria.", pequeno))
                return conteudo

            # Medir o conteúdo com o próprio motor de layout do ReportLab.
            # Reservar alguns pontos para as margens internas da célula.
            largura_painel = 12.8*cm - 16
            altura_painel = 9.08*cm - 12
            def altura_resumo(elementos):
                altura = 0
                for elemento in elementos:
                    _, h = elemento.wrap(largura_painel, altura_painel)
                    altura += h + elemento.getSpaceBefore() + elemento.getSpaceAfter()
                return altura

            contagens = [0, 0]
            # Preencher primeiro a tabela de vinculadas e depois a de não vinculadas.
            # O cabeçalho de cada categoria é sempre preservado.
            for i, (_, linhas) in enumerate(categorias):
                for _ in linhas:
                    proposta = contagens.copy()
                    proposta[i] += 1
                    if altura_resumo(construir_resumo(proposta)) <= altura_painel:
                        contagens = proposta
                    else:
                        break
            conteudo_painel = construir_resumo(contagens)
            ficha = Table([[imagem, conteudo_painel]], colWidths=[13.8*cm, 12.8*cm], hAlign="LEFT")
            ficha.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F4F7FB")),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 10),
                ("RIGHTPADDING", (1, 0), (1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            historia.append(ficha)
            historia.append(Spacer(1, 7))

            # A tabela de continuação fica diretamente na história do PDF:
            # não é embrulhada em uma célula única (causa de LayoutError).
            # Com cabeçalhos integrados e repeatRows=2, o conteúdo é dividido
            # por linhas, a categoria acompanha suas O.S. e não se perde
            # nenhum registro, mesmo quando há centenas de O.S. por ficha.
            for (nome, linhas), exibidas in zip(categorias, contagens):
                restantes = linhas[exibidas:]
                if not restantes:
                    continue
                historia.append(tabela_os(
                    restantes,
                    largura_total=25.6*cm,
                    titulo_continuacao=f"{nome} — continuação",
                ))
            # O acompanhamento vem DEPOIS de todo o conteúdo original,
            # preservando o mapa e as O.S. que continuam abaixo dele.
            historia.extend(secao_pdf_acompanhamento(c, largura=26.6*cm))
            historia.extend(evidencias_graficas_pdf(
                ativos_da_concentracao, indice_graficos, largura=26.6*cm
            ))
            if indice < len(concentracoes) - 1:
                historia.append(PageBreak())
    doc.build(historia)
    return buffer.getvalue()


@st.dialog("📄 Configurar relatório operacional", width="large")
def modal_configurar_relatorio(concentracoes, pontos, pontos_cluster, proximidades,
                               eventos, bairros, raio, observacoes, pocos, loggers):
    st.caption("THE: Teresina · API: demais municípios do Piauí · TIM: Timon. Os filtros do mapa principal não são alterados.")
    tipo = st.radio("Relatório", ["THE", "API", "TIM"], horizontal=True,
                    key="farol_tipo_relatorio_the_api_tim")
    disponiveis = sorted({cidade for _, c in concentracoes.iterrows()
                         for cidade in cidades_da_concentracao(c, pontos_cluster)},
                        key=normalizar_texto)
    cidades = [cidade for cidade in disponiveis if unidade_da_cidade_farol(cidade) == tipo]
    selecionadas = filtrar_concentracoes_relatorio(concentracoes, pontos_cluster, cidades)
    st.caption(f"{len(selecionadas)} concentração(ões) incluída(s) em {tipo}.")
    gerar = st.button("📸 Gerar relatório em PDF", type="primary", width="stretch",
                      disabled=selecionadas.empty)
    if gerar:
        if selecionadas.empty:
            st.warning("Nenhuma concentração encontrada para a seleção.")
        else:
            try:
                # O panorama usa somente as O.S. da unidade selecionada.
                cidades_normalizadas = {normalizar_municipio(c) for c in cidades}
                pontos_relatorio = pontos.loc[
                    pontos["Cidade"].map(normalizar_municipio).isin(cidades_normalizadas)
                ].copy()
                with st.spinner("Gerando mapa panorâmico e fichas das concentrações..."):
                    pdf = gerar_pdf_operacional(selecionadas, pontos_relatorio, pontos_cluster,
                          proximidades, eventos, bairros, raio, observacoes,
                          cidades=cidades, pocos=pocos, loggers=loggers)
                st.session_state["farol_pdf_operacional_pronto"] = pdf
                st.session_state["farol_pdf_operacional_nome"] = f"Farol_Relatorio_{tipo}.pdf"
                st.session_state["farol_os_relatorio_csv"] = planilha_os_relatorio(selecionadas, pontos_cluster)
                st.session_state["farol_os_relatorio_nome"] = f"Farol_OS_{tipo}.csv"
                st.success(f"Relatório {tipo} gerado. Faça o download abaixo.")
            except Exception as erro:
                st.error(f"Não foi possível gerar o relatório: {erro}")
    if st.session_state.get("farol_pdf_operacional_pronto"):
        st.download_button("⬇️ Baixar relatório gerado",
            data=st.session_state["farol_pdf_operacional_pronto"],
            file_name=st.session_state.get("farol_pdf_operacional_nome", "Farol_Relatorio.pdf"),
            mime="application/pdf", width="stretch",
            key="farol_download_relatorio_modal")
        if st.session_state.get("farol_os_relatorio_csv"):
            st.download_button("📊 Baixar planilha das O.S. (Excel/CSV)",
                data=st.session_state["farol_os_relatorio_csv"],
                file_name=st.session_state.get("farol_os_relatorio_nome", "Farol_OS.csv"),
                mime="text/csv", width="stretch",
                key="farol_download_os_relatorio_csv")

# ============================================================
# PAINEL LATERAL DIREITO — RESUMO CACHEADO E NAVEGAÇÃO ISOLADA
# ============================================================

@st.cache_data(ttl=180, show_spinner=False, max_entries=12)
def resumir_acionamentos_farol(df_cidade_bairro):
    """Agrupa O.S. uma só vez por base/data, incluindo as sem coordenadas.

    Normaliza valores únicos (não cada linha em todo rerun), e calcula uma
    única tabela de municípios e outra de bairros. A navegação no painel
    usa somente estas duas tabelas pequenas.
    """
    cab_cidades = ["_cidade_chave", "_cidade_nome", "QTD_OS"]
    cab_bairros = ["_cidade_chave", "_bairro_chave", "_bairro_nome", "QTD_OS"]
    if df_cidade_bairro is None or df_cidade_bairro.empty:
        return (pd.DataFrame(columns=cab_cidades),
                pd.DataFrame(columns=cab_bairros), 0)

    campos = df_cidade_bairro.reindex(columns=["Cidade", "Bairro"], fill_value="")
    cidades = campos["Cidade"].fillna("").astype(str).str.strip()
    bairros = campos["Bairro"].fillna("").astype(str).str.strip()

    # Municípios e bairros repetem-se em milhares de O.S.; evite normalizações
    # Unicode/regulares para cada linha de cada interação.
    mapa_cidades = {}
    for nome in cidades.unique():
        limpo = limpar_valor(nome)
        mapa_cidades[nome] = (
            normalizar_municipio(limpo) or "__SEM_MUNICIPIO__",
            normalizar_texto(limpo) or "NÃO INFORMADO",
        )
    mapa_bairros = {}
    for nome in bairros.unique():
        normalizado = limpar_nome_bairro(limpar_valor(nome))
        mapa_bairros[nome] = (
            normalizado or "__SEM_BAIRRO__",
            normalizado or "NÃO INFORMADO",
        )

    base = pd.DataFrame({
        "_cidade_chave": cidades.map(lambda nome: mapa_cidades[nome][0]),
        "_cidade_nome": cidades.map(lambda nome: mapa_cidades[nome][1]),
        "_bairro_chave": bairros.map(lambda nome: mapa_bairros[nome][0]),
        "_bairro_nome": bairros.map(lambda nome: mapa_bairros[nome][1]),
    })
    # Um agrupamento único permite extrair ambos os rankings sem reler as O.S.
    por_bairro = (base.groupby(["_cidade_chave", "_bairro_chave"], sort=False, as_index=False)
                  .agg(_cidade_nome=("_cidade_nome", "first"),
                       _bairro_nome=("_bairro_nome", "first"),
                       QTD_OS=("_bairro_chave", "size")))
    por_cidade = (por_bairro.groupby("_cidade_chave", as_index=False)
                  .agg(_cidade_nome=("_cidade_nome", "first"),
                       QTD_OS=("QTD_OS", "sum"))
                  .sort_values(["QTD_OS", "_cidade_nome"], ascending=[False, True], kind="stable")
                  .reset_index(drop=True))
    por_bairro = (por_bairro[cab_bairros]
                  .sort_values(["_cidade_chave", "QTD_OS", "_bairro_nome"],
                               ascending=[True, False, True], kind="stable")
                  .reset_index(drop=True))
    return por_cidade, por_bairro, len(base)


def _farol_painel_selecionar_municipio(chave):
    st.session_state["farol_painel_cidade_ativa"] = chave


def _farol_painel_voltar_municipios():
    st.session_state.pop("farol_painel_cidade_ativa", None)


@st.fragment
def renderizar_painel_acionamentos_farol(municipios, bairros, total_os):
    """Roda apenas este painel ao clicar: NÃO reconstrói Folium ou concentrações."""
    cidade_ativa = st.session_state.get("farol_painel_cidade_ativa", "")
    if cidade_ativa and cidade_ativa not in set(municipios["_cidade_chave"]):
        cidade_ativa = ""
        st.session_state.pop("farol_painel_cidade_ativa", None)

    st.markdown("""
        <style>
        .st-key-farol_acionamentos_lateral .stButton > button {
            text-align: left; justify-content: flex-start;
            min-height: 1.85rem; padding: .16rem .27rem;
            line-height: 1.12; font-size: .75rem;
            font-weight: 600; border-radius: 4px;
        }
        .st-key-farol_acionamentos_lateral .stButton > button:hover {
            color: #0A35B8; background: rgba(13, 67, 190, .07);
        }
        .st-key-farol_acionamentos_lateral [data-testid="stHorizontalBlock"] {gap: .28rem;}
        </style>
    """, unsafe_allow_html=True)

    with st.container(height=650, border=True, key="farol_acionamentos_lateral"):
        titulo = "Acionamentos por Bairro" if cidade_ativa else "Acionamentos por Cidade"
        st.markdown(
            '<div style="background:#0634BD;color:#FFF;text-align:center;'
            'padding:8px 5px;border-radius:5px;font-size:15px;font-weight:700;'
            'line-height:1.2;margin-bottom:6px;">'
            + titulo + '</div>', unsafe_allow_html=True,
        )
        if not total_os:
            st.caption("Nenhuma O.S. para a data consultada.")
            return

        if cidade_ativa:
            cidade = municipios.loc[municipios["_cidade_chave"] == cidade_ativa]
            nome_cidade = str(cidade["_cidade_nome"].iloc[0])
            st.caption(f"Município: {nome_cidade}")
            st.button("← Voltar aos municípios", type="tertiary", width="stretch",
                      key="farol_painel_voltar_cidades",
                      on_click=_farol_painel_voltar_municipios)
            ranking = bairros.loc[bairros["_cidade_chave"] == cidade_ativa]
            quantidade_cidade = int(cidade["QTD_OS"].iloc[0])
            st.markdown(
                f'<div style="font-size:12px;margin:2px 0 9px;">'
                f'<b>{quantidade_cidade:,}</b> O.S. · <b>{len(ranking)}</b> bairro(s)</div>'
                .replace(",", "."), unsafe_allow_html=True,
            )
        else:
            ranking = municipios
            st.markdown(
                f'<div style="font-size:12px;margin:2px 0 4px;">'
                f'<b>{total_os:,}</b> O.S. · <b>{len(municipios)}</b> município(s)</div>'
                .replace(",", "."), unsafe_allow_html=True,
            )
            st.caption("Selecione um município para ver os bairros.")

        st.markdown(
            '<div style="font-size:10px;display:flex;align-items:center;'
            'gap:5px;color:#596579;margin-bottom:7px;">'
            '<span style="width:8px;height:8px;border-radius:50%;'
            'background:#0634BD;display:inline-block;"></span>'
            'O.S. do período · Todas as classificações</div>',
            unsafe_allow_html=True,
        )
        maior = int(ranking["QTD_OS"].max()) if not ranking.empty else 1
        if cidade_ativa:
            # Bairros são somente leitura: um único HTML, sem dezenas de
            # st.columns/st.markdown, é muito mais leve no navegador.
            linhas = []
            for _, item in ranking.iterrows():
                nome = html_escape(str(item["_bairro_nome"]))
                qtd = int(item["QTD_OS"])
                proporcao = max(3.0, 100 * qtd / max(maior, 1))
                linhas.append(
                    '<div style="display:flex;align-items:center;gap:6px;'
                    'min-height:26px;margin:1px 0;">'
                    f'<div title="{nome}" style="width:48%;font-size:11px;font-weight:600;'
                    f'line-height:1.16;overflow-wrap:anywhere;">{nome}</div>'
                    '<div style="flex:1;display:flex;align-items:center;gap:5px;">'
                    '<div style="flex:1;height:13px;background:#E9EDF7;overflow:hidden;">'
                    f'<div style="height:100%;width:{proporcao:.1f}%;background:#0634BD;"></div>'
                    '</div>'
                    f'<b style="font-size:11px;min-width:20px;">{qtd}</b></div></div>'
                )
            st.markdown(''.join(linhas), unsafe_allow_html=True)
            st.caption("Contagem por bairro informado na O.S.; registros sem bairro entram em ‘Não informado’.")
        else:
            # Há muitas cidades em algumas datas. Não criar centenas de widgets
            # no primeiro carregamento. Todos continuam acessíveis no seletor.
            limite_inicial = 25
            if len(ranking) > limite_inicial:
                mostrar_todos = st.toggle(
                    f"Mostrar todos os {len(ranking)} municípios",
                    value=False, key="farol_painel_exibir_todos_municipios",
                )
                if not mostrar_todos:
                    ranking = ranking.head(limite_inicial)
            for indice, item in ranking.iterrows():
                nome = str(item["_cidade_nome"])
                qtd = int(item["QTD_OS"])
                proporcao = max(3.0, 100 * qtd / max(maior, 1))
                col_nome, col_barra = st.columns([1.12, 1], gap="small", vertical_alignment="center")
                with col_nome:
                    st.button(nome, key=f"farol_painel_municipio_{indice}",
                              help=f"Ver O.S. por bairro em {nome}",
                              type="tertiary", width="stretch",
                              on_click=_farol_painel_selecionar_municipio,
                              args=(str(item["_cidade_chave"]),))
                with col_barra:
                    st.markdown(
                        '<div style="display:flex;align-items:center;gap:5px;min-height:26px;">'
                        '<div style="flex:1;height:13px;background:#E9EDF7;border-radius:1px;overflow:hidden;">'
                        f'<div style="height:100%;width:{proporcao:.1f}%;background:#0634BD;"></div>'
                        '</div>'
                        f'<b style="font-size:11px;min-width:20px;color:inherit;">{qtd}</b>'
                        '</div>', unsafe_allow_html=True,
                    )
            st.caption("A contagem inclui O.S. sem coordenadas. O painel não altera o mapa nem as concentrações.")


# ============================================================
# INTERFACE
# ============================================================

st.title("Farol Operacional")

# Carregamento automático de polígonos de todos os municípios disponíveis.
# A ausência de arquivo geográfico é normal e não gera avisos no mapa.
_inicio_geometria = tempo_execucao.perf_counter()
bairros = descobrir_kmz()
_tempo_geometria = tempo_execucao.perf_counter() - _inicio_geometria

# A ativação inicial é explícita. Depois disso, limpeza na primeira execução do dia.
try:
    controle_historico_v2 = ler_controle_historico_v2()
except gspread.exceptions.APIError as erro_historico:
    # Falha fechada: sem controle confiável, não executar limpeza nem importar
    # O.S. sob regras possivelmente incorretas.
    codigo_http = getattr(getattr(erro_historico, "response", None), "status_code", None)
    if codigo_http == 429:
        st.error("O Google Sheets atingiu o limite temporário de consultas. Aguarde aproximadamente um minuto e atualize a página.")
    else:
        st.error("Não foi possível consultar o controle de histórico do Farol no Google Sheets. Verifique o acesso à planilha e os logs do aplicativo.")
    st.stop()
historico_v2_ativo = controle_historico_v2.get("ativo") == "SIM"
if historico_v2_ativo and PODE_EDITAR_FAROL:
    try:
        removidos_historico_v2 = executar_limpeza_historico_v2(controle_historico_v2)
        if removidos_historico_v2:
            st.toast(f"Histórico atualizado: {removidos_historico_v2} O.S. fora da janela excluídas.")
    except Exception as erro_historico:
        st.error(f"Limpeza automática suspensa: {erro_historico}")
# EVENTOS permanecem intactos na Etapa 1.

# Os cadastros são lidos localmente; apenas PONTOS e EVENTOS usam Google Sheets.
_inicio_planilhas = tempo_execucao.perf_counter()
with st.spinner("Carregando dados operacionais..."):
    try:
        df_pocos = carregar_pocos()
        df_loggers = carregar_cadastro_github(ARQUIVO_LOGGERS_CSV, CABECALHO_LOGGERS)
    except (FileNotFoundError, ValueError, pd.errors.ParserError, OSError) as erro:
        st.error(f"Não foi possível carregar os cadastros locais: {erro}")
        st.info("Confira dados/pocos.csv e dados/loggers.csv no GitHub. As abas originais do Google Sheets permanecem como backup.")
        st.stop()
    df_pontos = carregar_aba(NOME_ABA_PONTOS, CABECALHO_PONTOS)
    df_eventos = carregar_aba(NOME_ABA_EVENTOS, CABECALHO_EVENTOS)
_tempo_planilhas = tempo_execucao.perf_counter() - _inicio_planilhas

# Normaliza tipos para análise.
if not df_pontos.empty:
    df_pontos = df_pontos.copy()
    for coluna in CABECALHO_PONTOS:
        if coluna not in df_pontos.columns:
            df_pontos[coluna] = ""
    df_pontos["Latitude"] = df_pontos["Latitude"].map(lambda v: normalizar_coordenada(v, "lat"))
    df_pontos["Longitude"] = df_pontos["Longitude"].map(lambda v: normalizar_coordenada(v, "lon"))

if not df_pocos.empty:
    df_pocos = df_pocos.reindex(columns=CABECALHO_POCOS).fillna("")
if not df_loggers.empty:
    df_loggers = df_loggers.reindex(columns=CABECALHO_LOGGERS).fillna("")

# Conserva a base original somente para contagem e indicadores da importação.
df_pontos_armazenados_v2 = df_pontos
inicio_historico_v2, hoje_historico_v2 = janela_historico_v2()
if historico_v2_ativo:
    df_pontos = filtrar_pontos_historico_v2(df_pontos, inicio_historico_v2, hoje_historico_v2)

# ============================================================
# ABAS
# ============================================================

with st.sidebar:
    st.markdown("### 🚰 Farol — Módulo 3")
    st.markdown("### 🧭 Navegação")
    if "farol_tela" not in st.session_state:
        st.session_state["farol_tela"] = "mapa"
    if st.button("🗺️ Mapa operacional", width="stretch", type="primary" if st.session_state["farol_tela"] == "mapa" else "secondary"):
        st.session_state["farol_tela"] = "mapa"
        st.rerun()
    if PODE_EDITAR_FAROL:
        if st.button("📥 Importação", width="stretch"):
            modal_importacao()
    if st.button("⚡ Eventos", width="stretch"):
        modal_eventos()
    if st.button("📍 Cadastros", width="stretch", type="primary" if st.session_state["farol_tela"] == "cadastros" else "secondary"):
        st.session_state["farol_tela"] = "cadastros"
        st.rerun()
    if not PODE_EDITAR_FAROL:
        st.caption("🔒 Perfil Operador: somente visualização e downloads.")

    st.divider()
    st.markdown("### 📅 Calendário")
    if historico_v2_ativo:
        # A importação retroativa pode trazer O.S. anteriores à data em que
        # o histórico foi ativado. O calendário deve permitir visualizá-las.
        primeira_data = inicio_historico_v2
        # Ao entrar no módulo, a data padrão é hoje. Se o usuário escolher
        # outro dia, a escolha é mantida enquanto estiver dentro da janela.
        data_anterior = st.session_state.get("farol_v2_data_consulta")
        if isinstance(data_anterior, datetime):
            data_anterior = data_anterior.date()
        if not isinstance(data_anterior, date) or not (primeira_data <= data_anterior <= hoje_historico_v2):
            st.session_state["farol_v2_data_consulta"] = hoje_historico_v2
        data_consulta_v2 = st.date_input(
            "Data de consulta",
            min_value=primeira_data,
            max_value=hoje_historico_v2,
            key="farol_v2_data_consulta",
            format="DD/MM/YYYY",
        )
        df_pontos = filtrar_pontos_historico_v2(df_pontos, data_consulta_v2, data_consulta_v2)
    else:
        st.caption("O calendário estará disponível após a ativação do histórico.")
        if PODE_EDITAR_FAROL:
            confirmar_inicio_v2 = st.checkbox(
                "Confirmo o início do histórico a partir de hoje",
                key="farol_v2_confirmar_inicio_historico",
            )
            if st.button("Ativar histórico", disabled=not confirmar_inicio_v2,
                         key="farol_v2_ativar_historico"):
                try:
                    ativar_historico_v2()
                    st.success("Histórico ativado. O calendário e a importação aceitam os últimos 8 dias. A limpeza começa na primeira execução dos próximos dias.")
                    st.rerun()
                except Exception as erro:
                    st.error(f"Não foi possível ativar o histórico: {erro}")
    st.divider()
    st.markdown("### ⚙️ Configuração")
    raio_operacional = st.number_input(
        "Raio operacional (m)", min_value=50, max_value=5000,
        value=RAIO_OPERACIONAL_PADRAO, step=50,
        help="Distância máxima entre uma concentração ou uma O.S. individual e um Poço/Ativo ou Logger.",
    )
    # Parâmetros estruturais fixos: alterar o raio operacional não modifica
    # a formação das concentrações nem o mínimo necessário de O.S.
    raio_concentracao = RAIO_CONCENTRACAO_PADRAO
    minimo_os = MINIMO_OS_CONCENTRACAO_PADRAO
    st.caption(
        f"Parâmetros fixos: concentração de {raio_concentracao} m e "
        f"mínimo de {minimo_os} O.S. por concentração."
    )

    # Desativado por padrão: o navegador move o mapa sem enviar centro/zoom
    # ao Python a cada arrasto. Ative apenas para registrar o enquadramento
    # exato que será usado no relatório PDF.
    sincronizar_mapa_pdf = st.checkbox(
        "Sincronizar enquadramento para PDF",
        value=False,
        key="farol_sincronizar_mapa_pdf",
        help="Ative antes de ajustar o mapa para o PDF. Enquanto ativo, arrastar/zoom pode recarregar a página.",
    )
    if sincronizar_mapa_pdf:
        st.caption("Modo PDF ativo: centro e zoom serão registrados. Para navegar sem recarregamentos, desmarque esta opção.")

    if st.session_state.pop("farol_mensagem_limpeza", None):
        st.success("Dados operacionais limpos com sucesso.")
    espaco_relatorio_operacional = st.empty()
    if PERFIL_FAROL == "admin":
        if st.button("🗑️ Limpar dados (PONTOS e EVENTOS)",
                     width="stretch", key="abrir_limpeza_farol"):
            modal_limpar_dados_operacionais()
    if PERFIL_FAROL == "admin":
        espaco_diagnostico_farol = st.empty()
    st.divider()
    if st.button("🏠 Voltar ao Menu Principal", width="stretch"):
        try:
            st.switch_page("app.py")
        except Exception as erro:
            st.error(f"Não foi possível voltar ao Hub. Confira o nome do arquivo principal (app.py): {erro}")
    # O seletor de tema permanece no fim da barra lateral.
    modo_escuro = st.toggle("🌙 Modo escuro", key="farol_modo_escuro")

if modo_escuro:
    st.markdown("""
    <style>
      .stApp { background-color: #0e1117; color: #fafafa; }
      .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3,
      .stApp h4, .stApp h5, .stApp h6 { color: #f0f0f0 !important; }
      .stButton > button { background-color: #20252d; color: #fafafa; border-color: #444c56; }
    </style>
    """, unsafe_allow_html=True)


def ativos_da_cidade(df, campo, cidade, habilitado):
    """Evita enviar ao Folium milhares de equipamentos de outros municípios."""
    if not habilitado or cidade == "Todas" or df.empty or campo not in df.columns:
        return df.iloc[0:0].copy()
    return df.loc[normalizar_texto_series(df[campo]) == normalizar_texto(cidade)].copy()

# Camadas adicionais por cidade removidas da sidebar; permanecem os equipamentos próximos.
ativos_pocos_cidade = df_pocos.iloc[0:0].copy()
ativos_loggers_cidade = df_loggers.iloc[0:0].copy()


if st.session_state.get("farol_tela", "mapa") == "mapa":
    # O resumo é preparado só no rerun completo e fica congelado para o
    # fragmento de navegação. Clicar cidade/bairro não lê Sheets nem refaz mapa.
    _inicio_resumo_acionamentos = tempo_execucao.perf_counter()
    _resumo_cidades, _resumo_bairros, _total_acionamentos = resumir_acionamentos_farol(
        df_pontos.reindex(columns=["Cidade", "Bairro"], fill_value="")
    )
    _tempo_resumo_acionamentos = tempo_execucao.perf_counter() - _inicio_resumo_acionamentos
    eventos_mapeados, eventos_sem_geometria = [], []  # V2: eventos geográficos somente no detalhe
    st.subheader("🗺️ Farol V2 — Panorama e concentrações")

    st.caption("Eventos: vermelho = em andamento · amarelo = programado · verde = finalizado · cinza = outro status. "
               "As áreas destacadas representam os bairros inteiros dos arquivos geográficos, inclusive quando o evento informa impacto parcial.")

    if df_pontos.empty:
        st.warning("A aba PONTOS ainda não possui dados.")
        ativos_pocos = ativos_pocos_cidade
        ativos_loggers = ativos_loggers_cidade
        col_mapa_vazio, col_acionamentos_vazio = st.columns([4.15, 1.65], gap="small")
        with col_mapa_vazio:
            mapa = criar_mapa(
                bairros, df_pontos, ativos_pocos, ativos_loggers,
                pd.DataFrame(), pd.DataFrame(), mostrar_bairros=bool(bairros),
                eventos_mapeados=eventos_mapeados,
            )
            if st.session_state.get("farol_foco_concentracao"):
                lat_foco, lon_foco = st.session_state["farol_foco_concentracao"]
                mapa.location = [lat_foco, lon_foco]
                mapa.options["zoom"] = 15
                mapa.fit_bounds([[lat_foco - .007, lon_foco - .007], [lat_foco + .007, lon_foco + .007]])
            estado_mapa = st_folium(
                mapa, width=None, height=650,
                returned_objects=["center", "zoom"] if sincronizar_mapa_pdf else [],
                key="farol_mapa_principal",
            )
            exibir_atualizacoes_abaixo_mapa()
            if sincronizar_mapa_pdf and estado_mapa:
                if estado_mapa.get("center") is not None:
                    st.session_state["farol_ultimo_centro_mapa"] = estado_mapa["center"]
                if estado_mapa.get("zoom") is not None:
                    st.session_state["farol_ultimo_zoom_mapa"] = estado_mapa["zoom"]
        with col_acionamentos_vazio:
            renderizar_painel_acionamentos_farol(
                _resumo_cidades, _resumo_bairros, _total_acionamentos
            )
    else:
        # Filtros manuais removidos: o panorama utiliza todas as O.S.
        # georreferenciadas do período selecionado no histórico.
        # Preserva o comportamento anterior de exigir coordenadas válidas.
        analise = df_pontos.dropna(subset=["Latitude", "Longitude"]).copy()

        _inicio_analise = tempo_execucao.perf_counter()
        concentracoes, pontos_cluster = criar_concentracoes(
            analise,
            raio_m=raio_concentracao,
            minimo_os=int(minimo_os),
        )
        # Mesclagens são aplicadas apenas à apresentação e à análise atuais:
        # a base de PONTOS do Field/BI e a regra original dos 750 m não mudam.
        concentracoes_originais = concentracoes.copy()
        pontos_cluster_originais = pontos_cluster.copy()
        try:
            mesclas_ativas_farol = listar_mesclas_farol() if fonte_supabase() else []
            concentracoes, pontos_cluster = aplicar_mesclas_farol(
                concentracoes, pontos_cluster, mesclas_ativas_farol
            )
            erro_mesclagem_farol = None
        except Exception as erro:
            mesclas_ativas_farol = []
            erro_mesclagem_farol = str(erro)
        proximidades = pontos_proximos_concentracoes(
            concentracoes,
            df_pocos,
            df_loggers,
            raio=int(raio_operacional),
        )
        municipios_por_concentracao = municipios_das_concentracoes(concentracoes, pontos_cluster)
        municipios_panorama = set().union(*municipios_por_concentracao.values()) if municipios_por_concentracao else set()
        municipios_panorama.update({
            normalizar_municipio(cidade) for cidade in analise["Cidade"].fillna("").astype(str)
            if str(cidade).strip()
        })
        bairros_panorama = bairros_dos_municipios(bairros, municipios_panorama)
        eventos_mapeados_todos, eventos_sem_geometria = eventos_georreferenciados(df_eventos, bairros)
        eventos_panorama = [e for e in eventos_mapeados_todos
                            if normalizar_municipio(e["bairro"].get("municipio", "")) in municipios_panorama]


        _tempo_analise = tempo_execucao.perf_counter() - _inicio_analise

        k1, k2, k3, k4 = st.columns(4)
        # Mostrar separadamente o volume recebido do Field/BI e os pontos
        # georreferenciados, para identificar importações com coordenadas vazias.
        k1.metric("O.S. no período", len(df_pontos))
        k2.metric("O.S. com coordenada", len(analise))
        k3.metric("Concentrações", len(concentracoes))
        k4.metric("Pontos operacionais próximos", len(proximidades))

        # As O.S. são exibidas no panorama independentemente de agrupamentos.
        st.caption("Panorama: O.S. individuais (azul = reclamação; laranja = informação 146005), "
                   "concentrações e áreas de eventos. Clique em uma O.S. para analisar o raio operacional; "
                   "clique no centro de uma concentração para abrir seu detalhamento.")
        _inicio_renderizacao = tempo_execucao.perf_counter()
        # A seleção anterior pode ter deixado um ID em sessão mesmo quando
        # o dia consultado já não contém nenhuma concentração.
        codigos_validos = (set(concentracoes["ID_CONCENTRACAO"].astype(str))
                           if not concentracoes.empty else set())
        selecionada = str(st.session_state.get("farol_v2_concentracao", "") or "")
        if selecionada not in codigos_validos:
            selecionada = ""
            st.session_state["farol_v2_concentracao"] = ""

        # A seleção individual só é válida enquanto a O.S. pertencer ao dia
        # escolhido no calendário. Não reaproveitar pontos de outra data.
        chave_os_aberta = str(st.session_state.get("farol_v2_os_raio_aberto", "") or "")
        os_aberta = localizar_os_por_chave(analise, chave_os_aberta)
        if chave_os_aberta and os_aberta is None:
            st.session_state.pop("farol_v2_os_raio_aberto", None)
            chave_os_aberta = ""

        col_mapa_farol, col_acionamentos_farol = st.columns([4.15, 1.65], gap="small")
        with col_mapa_farol:
            if os_aberta is not None:
                lat_os = normalizar_coordenada(os_aberta.get("Latitude"), "lat")
                lon_os = normalizar_coordenada(os_aberta.get("Longitude"), "lon")
                numero_os = str(os_aberta.get("Nº da O.S", "") or "—")
                st.markdown(f"### 📍 O.S. {numero_os} — Análise operacional individual")
                if st.button("⬅️ Voltar ao panorama", key="farol_v2_voltar_os"):
                    st.session_state.pop("farol_v2_os_raio_aberto", None)
                    st.session_state.pop("farol_v2_os_selecionada", None)
                    # Nova instância: descarta último clique antigo no marcador.
                    st.session_state["farol_v2_panorama_versao"] = (
                        st.session_state.get("farol_v2_panorama_versao", 0) + 1
                    )
                    st.rerun()

                raio_os = int(raio_operacional)
                pocos_os = ativos_proximos_os(df_pocos, lat_os, lon_os, raio_os)
                loggers_os = ativos_proximos_os(df_loggers, lat_os, lon_os, raio_os)
                pontos_entorno = ordens_proximas_os(analise, lat_os, lon_os, raio_os)
                cidade_os = normalizar_municipio(os_aberta.get("Cidade", ""))
                bairros_os = bairros_dos_municipios(bairros, {cidade_os} if cidade_os else set())
                eventos_os = [e for e in eventos_mapeados_todos
                              if normalizar_municipio(e["bairro"].get("municipio", "")) == cidade_os]
                mapa = criar_mapa(
                    bairros_os, pontos_entorno, pocos_os, loggers_os,
                    pd.DataFrame(columns=CABECALHO_CONCENTRACOES), pd.DataFrame(),
                    mostrar_bairros=True, eventos_mapeados=eventos_os,
                    rotulos_equipamentos=True,
                    ponto_foco_operacional=os_aberta, raio_foco_operacional=raio_os,
                )
                delta_lat = raio_os / 111320.0
                delta_lon = raio_os / (111320.0 * max(.2, math.cos(math.radians(lat_os))))
                mapa.fit_bounds([[lat_os - delta_lat, lon_os - delta_lon],
                                 [lat_os + delta_lat, lon_os + delta_lon]],
                                padding=(35, 35))
                estado_mapa = st_folium(
                    mapa, width=None, height=650,
                    returned_objects=["center", "zoom"] if sincronizar_mapa_pdf else [],
                    key="farol_v2_mapa_raio_os",
                )
                if sincronizar_mapa_pdf and estado_mapa:
                    if estado_mapa.get("center") is not None:
                        st.session_state["farol_ultimo_centro_mapa"] = estado_mapa["center"]
                    if estado_mapa.get("zoom") is not None:
                        st.session_state["farol_ultimo_zoom_mapa"] = estado_mapa["zoom"]
                st.caption(f"Raio operacional de {raio_os} m centrado na O.S. {numero_os}; "
                           "as concentrações não são recalculadas nem alteradas.")
                m1, m2, m3 = st.columns(3)
                m1.metric("O.S. no raio", len(pontos_entorno))
                m2.metric("Poços / ativos no raio", len(pocos_os))
                m3.metric("Loggers no raio", len(loggers_os))
                with st.expander("📍 Equipamentos e ativos identificados no raio", expanded=True):
                    if pocos_os.empty and loggers_os.empty:
                        st.info(f"Nenhum poço, ativo ou logger cadastrado a até {raio_os} m da O.S.")
                    if not pocos_os.empty:
                        st.markdown("**Poços / Ativos**")
                        tabela_pocos = pocos_os[["IDENTIFICACAO_ATIVO", "NOME_POCO", "MUNICIPIO", "_DISTANCIA_M"]].copy()
                        tabela_pocos.columns = ["Identificação", "Nome", "Município", "Distância (m)"]
                        st.dataframe(tabela_pocos, hide_index=True, width="stretch")
                    if not loggers_os.empty:
                        st.markdown("**Loggers**")
                        tabela_loggers = loggers_os[["IDENTIFICACAO_ATIVO", "ENDERECO", "MUNICIPIO", "_DISTANCIA_M"]].copy()
                        tabela_loggers.columns = ["Identificação", "Endereço", "Município", "Distância (m)"]
                        st.dataframe(tabela_loggers, hide_index=True, width="stretch")
            elif selecionada:
                c_atual = concentracoes.loc[concentracoes["ID_CONCENTRACAO"].astype(str) == selecionada].iloc[0]
                st.markdown(f"### 📍 {selecionada} — Detalhamento operacional")
                if st.button("⬅️ Voltar ao panorama", key="farol_v2_voltar"):
                    st.session_state["farol_v2_concentracao"] = ""
                    st.session_state["farol_concentracao_expandida"] = ""
                    st.session_state["farol_v2_panorama_versao"] = (
                        st.session_state.get("farol_v2_panorama_versao", 0) + 1
                    )
                    st.rerun()
                grupo_atual = pontos_cluster.loc[pontos_cluster["_grupo"] == c_atual["GRUPO"]].copy()
                lat_c, lon_c = float(c_atual["LATITUDE"]), float(c_atual["LONGITUDE"])

                # Exibir as demais concentrações cujos círculos se tocam ou ficam
                # muito próximos. A proximidade é VISUAL: não altera a formação,
                # a numeração nem as contagens das concentrações originais.
                raio_feicao = float(raio_concentracao)
                tolerancia_vizinhanca_m = 75.0
                cidades_atual = {
                    normalizar_texto(x) for x in grupo_atual["Cidade"].dropna().astype(str)
                    if str(x).strip()
                } if "Cidade" in grupo_atual.columns else set()
                concentracoes_visiveis = [c_atual]
                for _, candidata in concentracoes.iterrows():
                    if str(candidata["ID_CONCENTRACAO"]) == selecionada:
                        continue
                    # Não aproximar concentrações de municípios distintos.
                    grupo_candidata = pontos_cluster.loc[
                        pontos_cluster["_grupo"] == candidata["GRUPO"]
                    ]
                    cidades_candidata = {
                        normalizar_texto(x) for x in grupo_candidata["Cidade"].dropna().astype(str)
                        if str(x).strip()
                    } if "Cidade" in grupo_candidata.columns else set()
                    if cidades_atual and cidades_candidata and not (cidades_atual & cidades_candidata):
                        continue
                    distancia = distancia_metros(lat_c, lon_c, candidata["LATITUDE"], candidata["LONGITUDE"])
                    if distancia is not None and distancia <= 2 * raio_feicao + tolerancia_vizinhanca_m:
                        concentracoes_visiveis.append(candidata)

                concentracoes_detalhe = pd.DataFrame(concentracoes_visiveis)
                ids_detalhe = set(concentracoes_detalhe["ID_CONCENTRACAO"].astype(str))
                vizinhas = [str(c["ID_CONCENTRACAO"]) for c in concentracoes_visiveis[1:]]

                # Inclui os pontos das concentrações vizinhas e O.S. no entorno,
                # sem carregar as O.S. do município inteiro.
                raio_visual = max(raio_feicao * 1.5, 650.0)
                lat_os = pd.to_numeric(analise["Latitude"], errors="coerce").to_numpy(dtype=float)
                lon_os = pd.to_numeric(analise["Longitude"], errors="coerce").to_numpy(dtype=float)
                mascara_os = np.zeros(len(analise), dtype=bool)
                for c in concentracoes_visiveis:
                    lat_ref = math.radians(float(c["LATITUDE"]))
                    lon_ref = math.radians(float(c["LONGITUDE"]))
                    dlat = np.radians(lat_os) - lat_ref
                    dlon = np.radians(lon_os) - lon_ref
                    h = (np.sin(dlat / 2) ** 2
                         + np.cos(lat_ref) * np.cos(np.radians(lat_os)) * np.sin(dlon / 2) ** 2)
                    distancias = 2 * 6371000.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
                    mascara_os |= np.isfinite(distancias) & (distancias <= raio_visual)
                pontos_mapa = analise.loc[mascara_os].copy()

                # Equipamentos próximos de TODAS as feições visíveis, sem mudar
                # os equipamentos vinculados ao painel da concentração principal.
                proximidades_atuais = proximidades.loc[
                    proximidades["Concentração"].astype(str).isin(ids_detalhe)
                ].copy() if not proximidades.empty else proximidades.copy()
                def _ativos_detalhe(base, tipo):
                    if base.empty or proximidades_atuais.empty:
                        return base.iloc[0:0].copy()
                    ids = set(proximidades_atuais.loc[
                        proximidades_atuais["Tipo"] == tipo, "Identificação"
                    ].fillna("").astype(str).map(normalizar_texto))
                    return base.loc[base["IDENTIFICACAO_ATIVO"].fillna("").astype(str).map(normalizar_texto).isin(ids)].copy()
                ativos_pocos = _ativos_detalhe(df_pocos, "Poço / Ativo")
                ativos_loggers = _ativos_detalhe(df_loggers, "Logger")
                # O filtro opcional da cidade acrescenta equipamentos, sem duplicações.
                if not ativos_pocos_cidade.empty:
                    ativos_pocos = df_pocos.loc[df_pocos.index.isin(ativos_pocos.index.union(ativos_pocos_cidade.index))].copy()
                if not ativos_loggers_cidade.empty:
                    ativos_loggers = df_loggers.loc[df_loggers.index.isin(ativos_loggers.index.union(ativos_loggers_cidade.index))].copy()
                # Todas as feições dos bairros do município, não apenas as
                # próximas ao círculo ou com evento cadastrado.
                cidades_detalhe = municipios_por_concentracao.get(selecionada, set())
                bairros_detalhe = bairros_dos_municipios(bairros, cidades_detalhe)
                eventos_mapeados = [e for e in eventos_mapeados_todos
                                    if normalizar_municipio(e["bairro"].get("municipio", "")) in cidades_detalhe]
                # As concentrações distantes permanecem identificadas com bandeira.
                # As vizinhas, cujos círculos se tocam, já são desenhadas completas.
                outras_concentracoes = concentracoes.loc[
                    concentracoes["ID_CONCENTRACAO"].astype(str).map(
                        lambda cod: cod not in ids_detalhe and bool(
                            municipios_por_concentracao.get(cod, set()) & cidades_detalhe)
                    )
                ].copy()
                mapa = criar_mapa(
                    bairros_detalhe, pontos_mapa, ativos_pocos, ativos_loggers,
                    concentracoes_detalhe,
                    proximidades_atuais, mostrar_bairros=True,
                    eventos_mapeados=eventos_mapeados,
                    raio_circulo=raio_concentracao,
                    outras_concentracoes=outras_concentracoes,
                    rotulos_equipamentos=True,
                )
                # Ajusta o enquadramento para mostrar integralmente os círculos
                # próximos, com margem para O.S. e equipamentos.
                limites = []
                for c in concentracoes_visiveis:
                    lat_ref, lon_ref = float(c["LATITUDE"]), float(c["LONGITUDE"])
                    delta_lat = raio_visual / 111320.0
                    delta_lon = raio_visual / (111320.0 * max(.2, math.cos(math.radians(lat_ref))))
                    limites.extend([[lat_ref - delta_lat, lon_ref - delta_lon],
                                    [lat_ref + delta_lat, lon_ref + delta_lon]])
                mapa.fit_bounds(limites, padding=(25, 25))
                estado_mapa = st_folium(
                    mapa, width=None, height=650,
                    returned_objects=["center", "zoom"] if sincronizar_mapa_pdf else [],
                    key="farol_v2_mapa_detalhe",
                )
                st.caption(f"Mapa detalhado: {len(pontos_mapa)} O.S. na área, "
                           f"{len(ativos_pocos)} poço(s)/ativo(s) e {len(ativos_loggers)} logger(s).")
                if vizinhas:
                    st.info("Concentração(ões) próxima(s) também visível(is): "
                            + ", ".join(vizinhas)
                            + ". Os agrupamentos permanecem independentes; selecione outro registro na lista para analisar seus dados.")
            else:
                # O desenho é iniciado no próprio panorama, sem exigir que o
                # analista desça até a lista de concentrações.
                col_desenho, col_instrucao = st.columns([1, 3], vertical_alignment="center")
                with col_desenho:
                    if st.button(
                        "✏️ Desenhar polígono" if not st.session_state.get(CHAVE_MODO_MESCLAGEM)
                        else "✖ Encerrar desenho",
                        key="farol_mescla_atalho_mapa",
                        disabled=not PODE_EDITAR_FAROL,
                        use_container_width=True,
                    ):
                        ativar_desenho = not bool(st.session_state.get(CHAVE_MODO_MESCLAGEM))
                        st.session_state[CHAVE_MODO_MESCLAGEM] = ativar_desenho
                        if not ativar_desenho:
                            st.session_state.pop(CHAVE_POLIGONO_MESCLAGEM, None)
                        st.session_state["farol_v2_panorama_versao"] = (
                            st.session_state.get("farol_v2_panorama_versao", 0) + 1
                        )
                        st.rerun()
                with col_instrucao:
                    st.caption(
                        "Desenhe a área das O.S. no mapa e confirme a união na seção "
                        "**Análise das concentrações**. Somente concentrações do mesmo evento "
                        "podem formar um único acompanhamento."
                    )
                mapa = criar_mapa_panorama_v2(
                    concentracoes, raio_m=raio_concentracao,
                    bairros=bairros_panorama, eventos_mapeados=eventos_panorama,
                    pontos_os=analise,
                )
                mesclagem_desenhando = bool(st.session_state.get(CHAVE_MODO_MESCLAGEM))
                if mesclagem_desenhando:
                    adicionar_desenho_mapa(
                        mapa, st.session_state.get(CHAVE_POLIGONO_MESCLAGEM)
                    )
                estado_mapa = st_folium(
                    mapa, width=None, height=650,
                    returned_objects=(
                        ["last_active_drawing", "last_object_clicked", "last_object_clicked_tooltip"]
                        if mesclagem_desenhando
                        else ["last_object_clicked", "last_object_clicked_tooltip"]
                    ),
                    key=(f"farol_v2_mapa_panorama_{st.session_state.get('farol_v2_panorama_versao', 0)}"
                         f"_{int(mesclagem_desenhando)}_"
                         f"{hashlib.md5(json.dumps(st.session_state.get(CHAVE_POLIGONO_MESCLAGEM), sort_keys=True).encode()).hexdigest()[:8]}"),
                )
                if mesclagem_desenhando and estado_mapa:
                    desenho = estado_mapa.get("last_active_drawing")
                    if desenho:
                        try:
                            geometria_nova = extrair_desenho_folium(desenho)
                            if geometria_nova != st.session_state.get(CHAVE_POLIGONO_MESCLAGEM):
                                st.session_state[CHAVE_POLIGONO_MESCLAGEM] = geometria_nova
                                st.rerun()
                        except ValueError as erro_desenho:
                            st.warning(f"Polígono não aceito: {erro_desenho}")
                if mesclagem_desenhando:
                    st.caption(
                        "Ferramenta Mesclar: clique no ícone de polígono à esquerda do mapa, "
                        "desenhe a área que engloba as O.S. do evento e, abaixo, confirme "
                        "quais concentrações pertencem à mesma ocorrência."
                    )
                clique = (estado_mapa or {}).get("last_object_clicked")
                tooltip_clique = str((estado_mapa or {}).get("last_object_clicked_tooltip") or "")
                if isinstance(clique, dict) and not mesclagem_desenhando:
                    lat_clique = parse_float(clique.get("lat"))
                    lon_clique = parse_float(clique.get("lng"))
                    melhor_concentracao, menor_centro = "", float("inf")
                    if lat_clique is not None and lon_clique is not None and not concentracoes.empty:
                        for _, centro in concentracoes.iterrows():
                            d = distancia_metros(lat_clique, lon_clique,
                                                 centro["LATITUDE"], centro["LONGITUDE"])
                            if d is not None and d < menor_centro:
                                menor_centro, melhor_concentracao = d, str(centro["ID_CONCENTRACAO"])
                    # O tooltip diferencia o centro da concentração de uma O.S.
                    # espacialmente coincidente, se a versão do componente informar.
                    if melhor_concentracao and menor_centro <= 4 and not tooltip_clique.startswith("O.S. "):
                        st.session_state["farol_v2_concentracao"] = melhor_concentracao
                        st.session_state["farol_v2_detalhe_aberto"] = melhor_concentracao
                        st.session_state["farol_concentracao_expandida"] = melhor_concentracao
                        st.session_state.pop("farol_v2_os_selecionada", None)
                        st.rerun()
                    os_clicada = localizar_os_clicada_panorama(estado_mapa, analise)
                    if os_clicada is not None:
                        st.session_state["farol_v2_os_selecionada"] = chave_ponto(os_clicada)

                os_selecionada = localizar_os_por_chave(
                    analise, st.session_state.get("farol_v2_os_selecionada", "")
                )
                if os_selecionada is not None:
                    numero_selecionado = str(os_selecionada.get("Nº da O.S", "") or "—")
                    cidade_selecionada = str(os_selecionada.get("Cidade", "") or "—")
                    bairro_selecionado = str(os_selecionada.get("Bairro", "") or "—")
                    st.info(f"**O.S. selecionada:** {numero_selecionado} · "
                            f"{cidade_selecionada} · {bairro_selecionado}")
                    if st.button(
                        f"📍 Abrir raio operacional de {int(raio_operacional)} m desta O.S.",
                        key="farol_v2_abrir_raio_os", type="primary",
                    ):
                        st.session_state["farol_v2_os_raio_aberto"] = chave_ponto(os_selecionada)
                        st.session_state["farol_v2_concentracao"] = ""
                        st.session_state["farol_v2_detalhe_aberto"] = ""
                        st.session_state["farol_concentracao_expandida"] = ""
                        st.rerun()
                # A lista tradicional de concentrações permanece abaixo do mapa.
            exibir_atualizacoes_abaixo_mapa()
            if sincronizar_mapa_pdf and estado_mapa and selecionada:
                if estado_mapa.get("center") is not None:
                    st.session_state["farol_ultimo_centro_mapa"] = estado_mapa["center"]
                if estado_mapa.get("zoom") is not None:
                    st.session_state["farol_ultimo_zoom_mapa"] = estado_mapa["zoom"]

        _inicio_painel_acionamentos = tempo_execucao.perf_counter()
        with col_acionamentos_farol:
            renderizar_painel_acionamentos_farol(
                _resumo_cidades, _resumo_bairros, _total_acionamentos
            )
        _tempo_painel_acionamentos = tempo_execucao.perf_counter() - _inicio_painel_acionamentos

        _tempo_mapa = tempo_execucao.perf_counter() - _inicio_renderizacao
        if PERFIL_FAROL == "admin":
            with espaco_diagnostico_farol.container():
                with st.expander("⏱️ Diagnóstico de desempenho", expanded=False):
                    st.caption("Tempo desta interação; valores baixos podem indicar uso do cache.")
                    st.write(f"Geometrias: {_tempo_geometria:.2f} s")
                    st.write(f"Google Sheets: {_tempo_planilhas:.2f} s")
                    st.write(f"Análise espacial: {_tempo_analise:.2f} s")
                    st.write(f"Resumo de acionamentos (cache): {_tempo_resumo_acionamentos:.2f} s")
                    st.write(f"Renderização do painel: {_tempo_painel_acionamentos:.2f} s")
                    st.write(f"Mapa e painel: {_tempo_mapa:.2f} s")
        if concentracoes.empty:
            if analise.empty:
                st.warning(
                    "Nenhuma O.S. do período consultado possui latitude e longitude válidas. "
                    "Confira as coordenadas da base importada do Field/BI na aba PONTOS."
                )
            else:
                st.warning(
                    f"Nenhuma concentração identificada com pelo menos {int(minimo_os)} O.S. "
                    f"interligadas a até {int(raio_concentracao)} m no período consultado."
                )
                st.caption(
                    f"Foram encontradas {len(analise)} O.S. com coordenadas válidas. "
                    "Os pontos permanecem visíveis no panorama e cada O.S. pode ser "
                    "analisada individualmente, mesmo sem concentração."
                )
        else:
            st.markdown("### 📋 Análise das Concentrações")
            st.caption("Expanda uma concentração e clique em **Carregar detalhes** para consultar O.S., equipamentos, eventos e PDFs. Apenas a ficha escolhida é processada.")
            if erro_mesclagem_farol:
                st.warning("As mesclagens compartilhadas estão temporariamente indisponíveis: "
                           + erro_mesclagem_farol)
            if st.button(
                "🔗 Mesclar concentrações" if not st.session_state.get(CHAVE_MODO_MESCLAGEM)
                else "✖ Encerrar mesclagem",
                key="farol_abrir_mesclagem", disabled=not PODE_EDITAR_FAROL,
            ):
                ativar = not bool(st.session_state.get(CHAVE_MODO_MESCLAGEM))
                st.session_state[CHAVE_MODO_MESCLAGEM] = ativar
                st.session_state["farol_v2_concentracao"] = ""
                st.session_state["farol_v2_detalhe_aberto"] = ""
                st.session_state.pop("farol_v2_os_raio_aberto", None)
                st.session_state["farol_v2_panorama_versao"] = (
                    st.session_state.get("farol_v2_panorama_versao", 0) + 1
                )
                if not ativar:
                    st.session_state.pop(CHAVE_POLIGONO_MESCLAGEM, None)
                st.rerun()
            if st.session_state.get(CHAVE_MODO_MESCLAGEM):
                if st.button("Limpar polígono", key="farol_mescla_limpar_poligono"):
                    st.session_state.pop(CHAVE_POLIGONO_MESCLAGEM, None)
                    st.session_state["farol_v2_panorama_versao"] = (
                        st.session_state.get("farol_v2_panorama_versao", 0) + 1
                    )
                    st.rerun()
            observacoes_df = carregar_observacoes_concentracoes()
            resumo_pdf = concentracoes.copy()
            grupos_os = {chave: quadro for chave, quadro in pontos_cluster.groupby("_grupo", sort=False)} if not pontos_cluster.empty else {}
            # Uma consulta compartilhada para todas as concentrações, em vez de
            # repetir uma leitura para cada cartão na tela.
            try:
                todos_vinculos_acomp = listar_vinculos()
                vinculos_acomp_visiveis = {v["chave"]: v for v in todos_vinculos_acomp}
                fichas_acomp_visiveis = dados_acompanhamento()
                recuperar_supabase_ativo = fonte_supabase()
                erro_acomp_visivel = None
            except Exception as erro:
                todos_vinculos_acomp = []
                vinculos_acomp_visiveis = {}
                fichas_acomp_visiveis = {}
                recuperar_supabase_ativo = False
                erro_acomp_visivel = erro
                st.warning(f"Acompanhamentos temporariamente indisponíveis: {erro}")

            # Concentrações mudam após cada importação. Indexar suas chaves
            # atuais permite identificar fichas cujo vínculo antigo ficou órfão,
            # sem associar automaticamente uma ficha a outra concentração.
            contextos_ativos_acomp = {}
            for _, c_atual in concentracoes.iterrows():
                os_atuais = grupos_os.get(c_atual["GRUPO"], pontos_cluster.iloc[0:0])
                cidades_atuais = sorted({
                    str(v).strip() for v in os_atuais.get("Cidade", []) if str(v).strip()
                })
                cidade_atual = cidades_atuais[0] if cidades_atuais else ""
                numeros_atuais = [
                    str(x).strip() for x in os_atuais.get("Nº da O.S", []) if str(x).strip()
                ]
                chave_mescla = c_atual.get("CHAVE_MESCLA")
                chave_atual = (
                    str(chave_mescla) if isinstance(chave_mescla, str) and chave_mescla
                    else chave_concentracao(
                        cidade_atual, c_atual["LATITUDE"], c_atual["LONGITUDE"],
                        numeros_atuais,
                    )
                )
                contextos_ativos_acomp[str(c_atual["ID_CONCENTRACAO"])] = {
                    "chave": chave_atual,
                    "municipio": cidade_atual,
                    "os": numeros_atuais,
                    "bairros": str(c_atual.get("BAIRROS", "") or ""),
                    "multiplos_municipios": len(cidades_atuais) != 1,
                    "latitude": float(c_atual["LATITUDE"]),
                    "longitude": float(c_atual["LONGITUDE"]),
                }

            chaves_ativas = {c["chave"] for c in contextos_ativos_acomp.values()}

            # Continuidade automática SOMENTE quando as O.S. anteriores
            # permanecem integralmente na concentração, novas O.S. são
            # acrescentadas e a correspondência é única e geograficamente segura.
            # O mapeamento em memória permite CONSULTA imediata também ao
            # operador; a gravação persistente só ocorre sob perfil com edição.
            continuidades_automaticas = {}
            continuidades_persistidas = set()
            continuidades_pendentes = set()
            if recuperar_supabase_ativo and not erro_acomp_visivel:
                continuidades_automaticas = identificar_continuidades_automaticas(
                    contextos_ativos_acomp, todos_vinculos_acomp,
                    fichas_acomp_visiveis,
                )
                tentativas_auto = st.session_state.setdefault(
                    "farol_auto_vinculo_tentativas", {}
                )
                agora_tentativa = tempo_execucao.monotonic()
                # Não repetir chamadas de gravação em reruns simultâneos após
                # falhas temporárias, nem sobrecarregar o banco num único rerun.
                gravacoes_nesta_interacao = 0
                for codigo_auto, candidato_auto in continuidades_automaticas.items():
                    ctx_auto = contextos_ativos_acomp[codigo_auto]
                    chave_auto = ctx_auto["chave"]
                    vinculo_virtual = {
                        "chave": chave_auto,
                        "evento_id": candidato_auto["evento_id"],
                        "municipio": ctx_auto["municipio"],
                        "bairros": ctx_auto["bairros"],
                        "latitude": str(ctx_auto["latitude"]),
                        "longitude": str(ctx_auto["longitude"]),
                        "os_origem": ";".join(ctx_auto["os"]),
                        "continuidade_automatica": True,
                    }
                    vinculos_acomp_visiveis[chave_auto] = vinculo_virtual
                    if not PODE_EDITAR_FAROL:
                        continuidades_pendentes.add(codigo_auto)
                        continue
                    ultima = tentativas_auto.get(chave_auto)
                    if (ultima is not None and agora_tentativa - ultima < 600
                            or gravacoes_nesta_interacao >= 12):
                        continuidades_pendentes.add(codigo_auto)
                        continue
                    tentativas_auto[chave_auto] = agora_tentativa
                    gravacoes_nesta_interacao += 1
                    try:
                        continuar_vinculo_automaticamente(
                            ctx_auto, candidato_auto["chave_anterior"],
                            candidato_auto["evento_id"],
                        )
                        continuidades_persistidas.add(codigo_auto)
                        tentativas_auto.pop(chave_auto, None)
                    except Exception:
                        # Nunca inventar ficha nova nem perder a continuidade
                        # reconhecida para consulta se o banco ficar indisponível.
                        continuidades_pendentes.add(codigo_auto)
                if continuidades_pendentes and PODE_EDITAR_FAROL:
                    st.warning(
                        "Algumas continuidades de acompanhamento foram reconhecidas, "
                        "mas ainda não tiveram o novo vínculo gravado no Supabase. "
                        "A associação pode ser consultada nesta sessão; "
                        "a gravação será tentada novamente após alguns minutos."
                    )

            ids_fichas_ativas = {
                str(vinculos_acomp_visiveis[chave]["evento_id"])
                for chave in chaves_ativas if chave in vinculos_acomp_visiveis
            }
            candidatos_acomp_por_codigo = {}
            if recuperar_supabase_ativo and not erro_acomp_visivel:
                for codigo_atual, ctx in contextos_ativos_acomp.items():
                    if (ctx["chave"] in vinculos_acomp_visiveis
                            or ctx["multiplos_municipios"]):
                        continue
                    opcoes = sugerir_recuperacoes(
                        todos_vinculos_acomp, ctx["municipio"],
                        ctx["latitude"], ctx["longitude"], ctx["os"],
                        chaves_ativas=chaves_ativas,
                    )
                    opcoes = [
                        op for op in opcoes
                        if op["evento_id"] in fichas_acomp_visiveis
                        and op["evento_id"] not in ids_fichas_ativas
                    ]
                    if opcoes:
                        candidatos_acomp_por_codigo[codigo_atual] = opcoes
            # Uma ficha que combina com duas concentrações atuais pode
            # indicar divisão/fusão: bloquear a recuperação até revisão.
            uso_candidato = {}
            for opcoes in candidatos_acomp_por_codigo.values():
                for opcao in opcoes:
                    eid = opcao["evento_id"]
                    uso_candidato[eid] = uso_candidato.get(eid, 0) + 1
            if st.session_state.get(CHAVE_MODO_MESCLAGEM):
                # O painel de seleção trabalha com as concentrações ANTES
                # das mesclagens; reconstruir também suas chaves originais.
                contextos_originais_mescla = {}
                for _, original in concentracoes_originais.iterrows():
                    codigo_original = str(original["ID_CONCENTRACAO"])
                    grupo_original = pontos_cluster_originais.loc[
                        pontos_cluster_originais["_grupo"] == original["GRUPO"]
                    ]
                    cidades_original = sorted({
                        str(c).strip() for c in grupo_original.get("Cidade", [])
                        if str(c).strip()
                    })
                    lista_os_original = sorted({
                        str(os_id).strip() for os_id in grupo_original.get("Nº da O.S", [])
                        if str(os_id).strip()
                    })
                    if len(cidades_original) != 1:
                        continue
                    contextos_originais_mescla[codigo_original] = {
                        "chave": chave_concentracao(
                            cidades_original[0], original["LATITUDE"],
                            original["LONGITUDE"], lista_os_original,
                        ),
                        "municipio": cidades_original[0],
                        "os": lista_os_original,
                    }
                st.divider()
                renderizar_painel_mescla(
                    concentracoes_originais, pontos_cluster_originais,
                    proximidades, df_eventos, bairros,
                    dados_relatorio_concentracao, classificar_os_por_evento,
                    contextos_originais_mescla, vinculos_acomp_visiveis,
                    fichas_acomp_visiveis, PODE_EDITAR_FAROL,
                    conferir_data=evento_pertinente_data_analise,
                )
                st.divider()
            # Três unidades separadas, com ordenação THE / API / TIM.
            aba_the, aba_api, aba_tim = st.tabs([
                "📍 THE — Teresina", "🌐 API — Piauí", "📍 TIM — Timon"
            ])
            for aba, abrangencia in ((aba_the, "THE"), (aba_api, "API"), (aba_tim, "TIM")):
                with aba:
                    concentracoes_abrangencia = concentracoes.loc[
                        concentracoes["ID_CONCENTRACAO"].astype(str).str.startswith(f"{abrangencia} ")
                    ].copy()
                    st.caption(f"{len(concentracoes_abrangencia)} concentração(ões) em {abrangencia}.")
                    if concentracoes_abrangencia.empty:
                        st.info("Nenhuma concentração encontrada nesta abrangência.")
                    for _, concentracao in concentracoes_abrangencia.iterrows():
                        codigo = concentracao["ID_CONCENTRACAO"]
                        os_grupo = grupos_os.get(concentracao["GRUPO"], pontos_cluster.iloc[0:0])
                        cidades_grupo = sorted({str(v).strip() for v in os_grupo["Cidade"] if str(v).strip()})
                        qtd_ativos = (int((proximidades["Concentração"] == codigo).sum())
                                      if not proximidades.empty else 0)
                        # Calcular o vínculo de eventos uma única vez por concentração.
                        # A ficha detalhada reutiliza este resultado quando for aberta.
                        os_eventos, ativos_eventos, eventos_do_grupo = dados_relatorio_concentracao(
                            concentracao, pontos_cluster, proximidades, df_eventos, bairros
                        )
                        indicador_eventos = (
                            f"⚡ {len(eventos_do_grupo)} evento(s) relacionado(s)"
                            if not eventos_do_grupo.empty else "Sem evento relacionado"
                        )
                        municipio_nota = cidades_grupo[0] if cidades_grupo else ""
                        numeros_os = [str(x).strip() for x in os_grupo.get("Nº da O.S", []) if str(x).strip()]
                        municipio_acomp = cidades_grupo[0] if cidades_grupo else municipio_nota
                        contexto_atual = contextos_ativos_acomp[str(codigo)]
                        chave_acomp = contexto_atual["chave"]
                        opcoes_recuperar = candidatos_acomp_por_codigo.get(str(codigo), [])
                        concentracoes.loc[concentracoes["ID_CONCENTRACAO"] == codigo, "CHAVE_ACOMPANHAMENTO"] = chave_acomp
                        resumo_pdf.loc[resumo_pdf["ID_CONCENTRACAO"] == codigo, "CHAVE_ACOMPANHAMENTO"] = chave_acomp
                        vinculo_acomp = vinculos_acomp_visiveis.get(chave_acomp)
                        resumo_pdf.loc[resumo_pdf["ID_CONCENTRACAO"] == codigo, "EVENTOS_RELACIONADOS"] = len(eventos_do_grupo)
                        ficha_acomp = fichas_acomp_visiveis.get(str(vinculo_acomp.get("evento_id", ""))) if vinculo_acomp else None
                        if ficha_acomp and (str(ficha_acomp.get("finalizado", "")).strip().casefold() == "sim"
                                             or str(ficha_acomp.get("status", "")).strip().casefold().startswith("concluído")):
                            indicador_nota = "✅ Acompanhamento concluído"
                        elif vinculo_acomp and ficha_acomp:
                            indicador_nota = "🔄 Acompanhamento em andamento"
                        elif vinculo_acomp:
                            indicador_nota = "Acompanhamento vinculado — ficha indisponível"
                        elif opcoes_recuperar:
                            indicador_nota = "🔎 Possível acompanhamento existente — revisar vínculo"
                        else:
                            indicador_nota = "Sem vínculo exato identificado" if not erro_acomp_visivel else "Acompanhamento indisponível"
                        bairros_grupo = str(concentracao.get("BAIRROS", "") or "").strip()
                        titulo = (
                            f"{codigo} — {', '.join(cidades_grupo) or 'Município não informado'}"
                            f" — {bairros_grupo or 'Bairro não informado'} | "
                            f"{int(concentracao['QTD_OS'])} O.S. · "
                            f"{int(concentracao['QTD_MATRICULAS'])} matrículas · "
                            f"{qtd_ativos} ativo(s) próximo(s) · {indicador_eventos} · {indicador_nota}"
                        )
                        # st.expander não faz carregamento preguiçoso: mesmo FECHADO,
                        # o Streamlit executa todo o conteúdo dentro dele. O detalhamento
                        # (tabelas, loggers, PDFs) só é construído para UM código escolhido.
                        # A seleção cartográfica continua determinando qual expansor
                        # abre inicialmente, sem alterar os dados do acompanhamento.
                        codigo_texto = str(codigo)
                        detalhe_escolhido = st.session_state.get("farol_v2_detalhe_aberto")
                        if detalhe_escolhido is None:
                            detalhe_escolhido = st.session_state.get("farol_v2_concentracao", "")
                        detalhe_ativo = codigo_texto == str(detalhe_escolhido or "")
                        mapa_ativo = codigo_texto == str(st.session_state.get("farol_v2_concentracao", ""))
                        with st.expander(titulo, expanded=detalhe_ativo or mapa_ativo):
                            if vinculo_acomp:
                                st.success(indicador_nota)
                                if str(codigo) in continuidades_automaticas:
                                    st.caption(
                                        "Continuidade reconhecida automaticamente: as O.S. "
                                        "anteriores permanecem na concentração. "
                                        "Ficha e movimentações originais preservadas."
                                    )
                            elif opcoes_recuperar:
                                st.warning(
                                    "Há acompanhamento(s) anterior(es) com O.S. em comum. "
                                    "Nenhuma ficha foi movida; confirme o vínculo antes de criar outra."
                                )
                            else:
                                st.caption(
                                    "Nenhum vínculo exato encontrado para esta composição de O.S. "
                                    "Se já existia uma ficha, confira o histórico antes de criar outra."
                                )

                            contexto_popup = {
                                "chave": chave_acomp, "codigo": str(codigo),
                                "municipio": municipio_acomp, "bairros": bairros_grupo,
                                "latitude": float(concentracao["LATITUDE"]),
                                "longitude": float(concentracao["LONGITUDE"]),
                                "os": numeros_os, "qtd": int(concentracao["QTD_OS"]),
                                "evento_id": str(vinculo_acomp["evento_id"]) if vinculo_acomp else "",
                                "origem": "producao",
                            }
                            if opcoes_recuperar and not vinculo_acomp:
                                if st.button(
                                    "🔗 Revisar acompanhamento existente",
                                    key=f"farol_revisar_vinculo_{abrangencia}_{codigo}",
                                    disabled=not PODE_EDITAR_FAROL,
                                ):
                                    st.session_state["farol_codigo_recuperacao"] = str(codigo)
                                    st.session_state["farol_v2_detalhe_aberto"] = str(codigo)
                                    st.rerun()
                                if st.session_state.get("farol_codigo_recuperacao") == str(codigo):
                                    with st.form(f"farol_form_recuperar_{abrangencia}_{codigo}"):
                                        escolha = st.selectbox(
                                            "Ficha anterior para esta concentração",
                                            list(range(len(opcoes_recuperar))),
                                            format_func=lambda i: (
                                                f"Ficha #{opcoes_recuperar[i]['evento_id']} — "
                                                f"{str(fichas_acomp_visiveis[opcoes_recuperar[i]['evento_id']].get('titulo', ''))[:75]} "
                                                f"— {opcoes_recuperar[i]['os_em_comum']} O.S. em comum "
                                                f"— {opcoes_recuperar[i]['distancia_m']} m"
                                            ),
                                            key=f"farol_escolha_recuperacao_{abrangencia}_{codigo}",
                                        )
                                        candidato = opcoes_recuperar[escolha]
                                        ficha_candidata = fichas_acomp_visiveis[candidato["evento_id"]]
                                        st.caption(
                                            f"Registro de {candidato['criado_em']} · "
                                            f"Status: {ficha_candidata.get('status', '')} · "
                                            f"{candidato['os_em_comum']} de {candidato['os_anteriores']} O.S. "
                                            f"anteriores e {candidato['os_atuais']} O.S. atuais · "
                                            f"Distância entre centros: {candidato['distancia_m']} m"
                                        )
                                        ambiguo = uso_candidato.get(candidato["evento_id"], 0) > 1
                                        if ambiguo:
                                            st.error(
                                                "Esta ficha também corresponde a outra concentração atual "
                                                "(possível divisão/fusão). Recuperação bloqueada para evitar associação incorreta."
                                            )
                                        confirmado = st.checkbox(
                                            "Confirmei que a concentração atual corresponde à ficha selecionada. "
                                            "O vínculo antigo e todo o histórico serão preservados.",
                                            key=f"farol_confirmar_recuperacao_{abrangencia}_{codigo}",
                                        )
                                        recuperar = st.form_submit_button(
                                            "Confirmar recuperação do vínculo",
                                            type="primary",
                                            disabled=ambiguo or not PODE_EDITAR_FAROL,
                                        )
                                    if recuperar:
                                        if not confirmado:
                                            st.warning("Confirme a correspondência com a ficha anterior antes de recuperar.")
                                        else:
                                            try:
                                                recuperar_vinculo_confirmado(
                                                    contexto_popup, candidato["chave_anterior"],
                                                    candidato["evento_id"],
                                                )
                                                st.session_state.pop("farol_codigo_recuperacao", None)
                                                st.toast("Vínculo recuperado; ficha e histórico preservados.")
                                                st.rerun()
                                            except Exception as erro_recuperar:
                                                st.error(f"Não foi possível recuperar o vínculo: {erro_recuperar}")
                            else:
                                rotulo_acomp = (
                                    ("✏️ Abrir / Editar Acompanhamento" if PODE_EDITAR_FAROL else "📋 Consultar Acompanhamento")
                                    if vinculo_acomp
                                    else ("📝 Iniciar Acompanhamento" if PODE_EDITAR_FAROL else "📋 Consultar Acompanhamento")
                                )
                                if st.button(
                                    rotulo_acomp, key=f"farol_acomp_{abrangencia}_{codigo}",
                                    disabled=bool(erro_acomp_visivel),
                                ):
                                    popup_acompanhamento_farol(
                                        contexto_popup, pode_editar=PODE_EDITAR_FAROL
                                    )
                            if st.button("🗺️ Abrir mapa desta concentração" if not mapa_ativo else "📍 Mapa desta concentração aberto",
                                         key=f"farol_v2_foco_{abrangencia}_{codigo}", disabled=mapa_ativo):
                                st.session_state["farol_v2_concentracao"] = codigo_texto
                                st.session_state["farol_v2_detalhe_aberto"] = codigo_texto
                                st.session_state["farol_concentracao_expandida"] = codigo_texto
                                st.session_state.pop("farol_v2_os_raio_aberto", None)
                                st.rerun()

                            # Evita criar vários st.dataframe e executar uma consulta aos
                            # gráficos dos loggers para CADA expansor em todo rerun.
                            if detalhe_ativo:
                                if st.button("▴ Ocultar detalhes desta concentração",
                                             key=f"farol_v2_ocultar_detalhe_{abrangencia}_{codigo}"):
                                    # String vazia: não reabrir o detalhe apenas por haver
                                    # uma concentração selecionada no mapa.
                                    st.session_state["farol_v2_detalhe_aberto"] = ""
                                    st.rerun()
                                exibir_relatorio_concentracao(
                                    codigo, concentracoes, pontos_cluster, proximidades,
                                    df_eventos, int(raio_operacional), bairros,
                                    dados_preparados=(os_eventos, ativos_eventos, eventos_do_grupo),
                                )
                            else:
                                st.caption("As tabelas, os loggers e o PDF são carregados somente quando solicitados.")
                                if st.button("📋 Carregar detalhes desta concentração",
                                             key=f"farol_v2_carregar_detalhe_{abrangencia}_{codigo}"):
                                    st.session_state["farol_v2_detalhe_aberto"] = codigo_texto
                                    st.rerun()
        # Disponível também quando não há concentrações no filtro atual.
        if concentracoes.empty:
            resumo_pdf = concentracoes.copy()
            observacoes_df = pd.DataFrame()
        with espaco_relatorio_operacional.container():
            st.caption("Relatórios THE (Teresina), API (demais municípios do Piauí) ou TIM (Timon), com panorama e fichas individuais.")
            if st.button("📄 Configurar e gerar relatório (PDF)",
                         width="stretch", key="abrir_configuracao_relatorio"):
                modal_configurar_relatorio(resumo_pdf, analise, pontos_cluster,
                    proximidades, df_eventos, bairros, int(raio_operacional),
                    observacoes_df, df_pocos, df_loggers)
    # Sem dados de O.S.: manter a opção visível, porém desabilitada.
    if df_pontos.empty:
        espaco_relatorio_operacional.button(
            "📄 Gerar relatório operacional (PDF)", disabled=True,
            width="stretch", key="relatorio_sem_pontos"
        )



if st.session_state.get("farol_tela", "mapa") == "cadastros":
    espaco_relatorio_operacional.caption("📄 Relatório disponível na tela Mapa operacional.")
    st.subheader("Consulta de Poços / Ativos e Loggers")
    st.caption("Cadastros fixos carregados dos arquivos dados/pocos.csv e dados/loggers.csv do GitHub. Para atualizar, substitua os arquivos no repositório.")
    c_poco, c_logger = st.columns(2)
    with c_poco:
        st.markdown("### Poços / Ativos")
        st.metric("Registros", len(df_pocos))
        st.dataframe(df_pocos, width="stretch", hide_index=True)
    with c_logger:
        st.markdown("### Loggers")
        st.metric("Registros", len(df_loggers))
        st.dataframe(df_loggers, width="stretch", hide_index=True)
