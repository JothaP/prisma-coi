"""Integração Farol V2 / Acompanhamento Operacional (apenas teste)."""
import hashlib
import json
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials

CAMPOS_VINCULO = ["chave", "evento_id", "municipio", "bairros", "latitude", "longitude", "os_origem", "criado_em"]
PLANILHA_ACOMP = "1nIs2qMlgZNj53zj8hg867O-NLpeDqHozSpCo_QBKUHY"

def planilha_id():
    return str(st.secrets.get("acompanhamento_operacional_spreadsheet_id", PLANILHA_ACOMP)).strip()

@st.cache_resource(show_spinner=False)
def _planilha(identificador):
    dados = st.secrets["gcp_json"]
    dados = json.loads(dados) if isinstance(dados, str) else dict(dados)
    cred = Credentials.from_service_account_info(dados, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(cred).open_by_key(identificador)

def _aba(nome, cabecalho, criar=False):
    try:
        aba = _planilha(planilha_id()).worksheet(nome)
    except gspread.WorksheetNotFound:
        if not criar:
            return None
        aba = _planilha(planilha_id()).add_worksheet(title=nome, rows=1000, cols=max(len(cabecalho), 8))
        aba.update(range_name="A1", values=[cabecalho], value_input_option="RAW")
    primeira = aba.row_values(1)
    if primeira and primeira != cabecalho:
        raise ValueError(f"Cabeçalho incompatível na aba {nome}; nenhuma alteração foi feita.")
    if not primeira and criar:
        aba.update(range_name="A1", values=[cabecalho], value_input_option="RAW")
    return aba

def chave_concentracao(municipio, latitude, longitude, numeros_os):
    # Identidade persistente pelo conjunto de O.S. de origem, independente do código C001.
    # O.S. que entram depois não são vinculadas automaticamente: exige conferência humana.
    base = json.dumps([str(municipio).strip().upper(), sorted({str(x).strip() for x in numeros_os if str(x).strip()})], ensure_ascii=False)
    if not json.loads(base)[1]:
        base += f"|{float(latitude):.5f}|{float(longitude):.5f}"
    return hashlib.sha256(base.encode()).hexdigest()[:32]

def listar_vinculos():
    aba = _aba("vinculos_farol_teste", CAMPOS_VINCULO)
    if aba is None:
        return []
    return [dict(zip(CAMPOS_VINCULO, (linha + [""]*len(CAMPOS_VINCULO))[:len(CAMPOS_VINCULO)])) for linha in aba.get_all_values()[1:] if linha and linha[0]]

def localizar_vinculo(chave, municipio, latitude, longitude, numeros_os):
    vinculos = listar_vinculos()
    exato = next((v for v in vinculos if v["chave"] == chave), None)
    if exato:
        return exato
    # NÃO associa automaticamente por proximidade ou ID C001 mutável.
    return None

def registrar_vinculo(contexto, evento_id):
    aba = _aba("vinculos_farol_teste", CAMPOS_VINCULO, criar=True)
    existentes = listar_vinculos()
    chave = contexto["chave"]
    outro = next((v for v in existentes if v["chave"] == chave), None)
    if outro:
        if str(outro["evento_id"]) != str(evento_id):
            raise ValueError("Esta concentração já está vinculada a outro acompanhamento.")
        return
    from datetime import datetime
    aba.append_row([chave, str(evento_id), contexto["municipio"], contexto["bairros"], str(contexto["latitude"]), str(contexto["longitude"]), ";".join(contexto["os"]), datetime.now().strftime("%d/%m/%Y %H:%M")], value_input_option="RAW")

def dados_acompanhamento():
    cab = ["id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao", "unidade", "regional", "municipio", "data_identificacao", "descricao", "arcgis", "sia", "loggers", "retorno_operacao", "finalizado", "data_finalizacao"]
    aba = _aba("eventos", cab)
    if aba is None:
        return {}
    return {str(r["id"]):r for r in [dict(zip(cab,(linha+[""]*len(cab))[:len(cab)])) for linha in aba.get_all_values()[1:] if linha and linha[0]]}

def historico_evento(evento_id):
    cab = ["id", "evento_id", "data_hora", "descricao", "status_momento"]
    aba = _aba("movimentacoes", cab)
    if aba is None:
        return []
    return [dict(zip(cab,(linha+[""]*len(cab))[:len(cab)])) for linha in aba.get_all_values()[1:] if len(linha)>1 and str(linha[1])==str(evento_id)]

def texto_pdf(vinculo, dados, movimentos):
    if not vinculo:
        return "Acompanhamento operacional: não iniciado."
    if not dados:
        return "Acompanhamento vinculado, mas ficha não localizada."
    linhas = [f"Acompanhamento operacional #{dados['id']}", f"Título: {dados['titulo']}", f"Status: {dados['status']}", f"Última atualização: {dados['ultima_atualizacao']}", f"Descrição: {dados['descricao']}", f"ArcGIS: {dados['arcgis']} | SIA: {dados['sia']} | Loggers: {dados['loggers']} | Retorno: {dados['retorno_operacao']}", f"Finalizado: {dados['finalizado']} | Data: {dados['data_finalizacao']}"]
    linhas.extend(f"{m['data_hora']} [{m['status_momento']}]: {m['descricao']}" for m in movimentos)
    return "\n".join(linhas)
