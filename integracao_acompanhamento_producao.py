"""Integração Farol de produção / Acompanhamento Operacional."""
import hashlib
import json
import gspread
import random
import time
import streamlit as st
from google.oauth2.service_account import Credentials

CAMPOS_VINCULO = ["chave", "evento_id", "municipio", "bairros", "latitude", "longitude", "os_origem", "criado_em"]
PLANILHA_ACOMP = "1nIs2qMlgZNj53zj8hg867O-NLpeDqHozSpCo_QBKUHY"

def planilha_id():
    identificador = str(st.secrets.get("acompanhamento_operacional_spreadsheet_id", "")).strip()
    farol = str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip()
    if not identificador or identificador == farol:
        raise ValueError("A planilha do acompanhamento deve estar configurada e ser diferente da planilha do Farol.")
    return identificador

@st.cache_resource(show_spinner=False)
def _planilha(identificador):
    dados = st.secrets["gcp_json"]
    dados = json.loads(dados) if isinstance(dados, str) else dict(dados)
    cred = Credentials.from_service_account_info(dados, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(cred).open_by_key(identificador)

@st.cache_resource(show_spinner=False)
def _aba_existente(identificador, nome):
    """Consulta a metadata da aba apenas uma vez por processo."""
    try:
        return _planilha(identificador).worksheet(nome)
    except gspread.WorksheetNotFound:
        return None


def _aba(nome, cabecalho, criar=False):
    identificador = planilha_id()
    aba = _aba_existente(identificador, nome)
    if aba is None and criar:
        try:
            aba = _planilha(identificador).add_worksheet(
                title=nome, rows=1000, cols=max(len(cabecalho), 8))
        except gspread.exceptions.APIError as erro:
            # Outra sessão pode ter criado a aba simultaneamente.
            if getattr(getattr(erro, "response", None), "status_code", None) != 400:
                raise
            _aba_existente.clear()
            aba = _aba_existente(identificador, nome)
            if aba is None:
                raise
        else:
            aba.update(range_name="A1", values=[cabecalho], value_input_option="RAW")
        _aba_existente.clear()
    return aba


def _ler_tabela(nome, cabecalho):
    aba = _aba(nome, cabecalho)
    if aba is None:
        return []
    for tentativa in range(4):
        try:
            linhas = aba.get_all_values()
            if linhas and linhas[0][:len(cabecalho)] != cabecalho:
                raise ValueError(f"Cabeçalho incompatível na aba {nome}; nenhuma alteração foi feita.")
            return [dict(zip(cabecalho, (linha + [""] * len(cabecalho))[:len(cabecalho)]))
                    for linha in linhas[1:] if linha and str(linha[0]).strip()]
        except gspread.exceptions.APIError as erro:
            status = getattr(getattr(erro, "response", None), "status_code", None)
            if status not in (429, 500, 502, 503, 504) or tentativa == 3:
                raise
            time.sleep(min(2 ** tentativa + random.uniform(0, 0.6), 8))


@st.cache_data(ttl=60, show_spinner=False)
def _todas_tabelas(identificador):
    """Um único lote de leituras compartilhado por todas as concentrações e PDFs.

    `batch_get` recupera as três abas em uma única requisição HTTP quando disponíveis.
    """
    planilha = _planilha(identificador)
    nomes = ("vinculos_farol_producao", "eventos", "movimentacoes")
    cabecalhos = (CAMPOS_VINCULO, CAMPOS_EVENTOS, CAMPOS_MOVIMENTACOES)
    existentes = [(nome, cab) for nome, cab in zip(nomes, cabecalhos)
                  if _aba(nome, cab) is not None]
    resultado = {nome: [] for nome in nomes}
    if not existentes:
        return resultado
    for tentativa in range(4):
        try:
            valores = planilha.values_batch_get([f"'{nome}'!A:Z" for nome, _ in existentes])
            ranges = valores.get("valueRanges", [])
            for (nome, cab), faixa in zip(existentes, ranges):
                linhas = faixa.get("values", [])
                if linhas and linhas[0][:len(cab)] != cab:
                    raise ValueError(f"Cabeçalho incompatível na aba {nome}.")
                resultado[nome] = [dict(zip(cab, (linha + [""] * len(cab))[:len(cab)]))
                                   for linha in linhas[1:] if linha and str(linha[0]).strip()]
            return resultado
        except gspread.exceptions.APIError as erro:
            status = getattr(getattr(erro, "response", None), "status_code", None)
            if status not in (429, 500, 502, 503, 504) or tentativa == 3:
                raise
            time.sleep(min(2 ** tentativa + random.uniform(0, 0.6), 8))
    return resultado


def invalidar_cache_acompanhamento():
    """Invalida leituras agregadas após gravações na planilha de acompanhamento."""
    _todas_tabelas.clear()


CAMPOS_EVENTOS = ["id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao", "unidade", "regional", "municipio", "data_identificacao", "descricao", "arcgis", "sia", "loggers", "retorno_operacao", "finalizado", "data_finalizacao"]
CAMPOS_MOVIMENTACOES = ["id", "evento_id", "data_hora", "descricao", "status_momento"]

def chave_concentracao(municipio, latitude, longitude, numeros_os):
    # Identidade persistente pelo conjunto de O.S. de origem, independente do código C001.
    # O.S. que entram depois não são vinculadas automaticamente: exige conferência humana.
    base = json.dumps([str(municipio).strip().upper(), sorted({str(x).strip() for x in numeros_os if str(x).strip()})], ensure_ascii=False)
    if not json.loads(base)[1]:
        base += f"|{float(latitude):.5f}|{float(longitude):.5f}"
    return hashlib.sha256(base.encode()).hexdigest()[:32]

def listar_vinculos():
    return _todas_tabelas(planilha_id())["vinculos_farol_producao"]

def localizar_vinculo(chave, municipio, latitude, longitude, numeros_os):
    vinculos = listar_vinculos()
    exato = next((v for v in vinculos if v["chave"] == chave), None)
    if exato:
        return exato
    # NÃO associa automaticamente por proximidade ou ID C001 mutável.
    return None

def registrar_vinculo(contexto, evento_id):
    aba = _aba("vinculos_farol_producao", CAMPOS_VINCULO, criar=True)
    existentes = listar_vinculos()
    chave = contexto["chave"]
    outro = next((v for v in existentes if v["chave"] == chave), None)
    if outro:
        if str(outro["evento_id"]) != str(evento_id):
            raise ValueError("Esta concentração já está vinculada a outro acompanhamento.")
        return
    from datetime import datetime
    aba.append_row([chave, str(evento_id), contexto["municipio"], contexto["bairros"], str(contexto["latitude"]), str(contexto["longitude"]), ";".join(contexto["os"]), datetime.now().strftime("%d/%m/%Y %H:%M")], value_input_option="RAW")
    invalidar_cache_acompanhamento()

def dados_acompanhamento():
    return {str(r["id"]): r for r in _todas_tabelas(planilha_id())["eventos"]}


def historico_evento(evento_id):
    return [r for r in _todas_tabelas(planilha_id())["movimentacoes"]
            if str(r.get("evento_id", "")) == str(evento_id)]

def texto_pdf(vinculo, dados, movimentos):
    if not vinculo:
        return "Acompanhamento operacional: não iniciado."
    if not dados:
        return "Acompanhamento vinculado, mas ficha não localizada."
    linhas = [f"Acompanhamento operacional #{dados['id']}", f"Título: {dados['titulo']}", f"Status: {dados['status']}", f"Última atualização: {dados['ultima_atualizacao']}", f"Descrição: {dados['descricao']}", f"ArcGIS: {dados['arcgis']} | SIA: {dados['sia']} | Loggers: {dados['loggers']} | Retorno: {dados['retorno_operacao']}", f"Finalizado: {dados['finalizado']} | Data: {dados['data_finalizacao']}"]
    linhas.extend(f"{m['data_hora']} [{m['status_momento']}]: {m['descricao']}" for m in movimentos)
    return "\n".join(linhas)
