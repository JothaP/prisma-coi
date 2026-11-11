"""PRISMA/Farol: adaptador de acompanhamento Supabase.

A fonte é Google Sheets por padrão. O modo Supabase só funciona após dois
Secrets de liberação. Nunca escreve silenciosamente no Google em uma falha.
Somente o servidor Streamlit detém a chave de serviço.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import streamlit as st
from auth import pode_executar_acao, verificar_autenticacao

CAMPOS_FICHA = (
    "id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao", "unidade",
    "regional", "municipio", "data_identificacao", "descricao", "arcgis", "sia",
    "loggers", "retorno_operacao", "finalizado", "data_finalizacao",
    "causa_identificada",
)
CAMPOS_VINCULO = (
    "chave", "evento_id", "municipio", "bairros", "latitude",
    "longitude", "os_origem", "criado_em",
)
CAMPOS_MOVIMENTACAO = ("id", "evento_id", "data_hora", "descricao", "status_momento")
TABELAS = {
    "fichas": ("farol_acomp_fichas", CAMPOS_FICHA + ("versao",)),
    "vinculos": ("farol_acomp_vinculos", CAMPOS_VINCULO),
    "movimentacoes": ("farol_acomp_movimentacoes", CAMPOS_MOVIMENTACAO),
}


class FalhaAcompanhamentoSupabase(RuntimeError):
    pass


def fonte_supabase() -> bool:
    """Não alterna a fonte por sessão; a configuração é global do servidor."""
    fonte = str(st.secrets.get("FAROL_ACOMP_FONTE", "google")).strip().lower()
    if fonte not in ("google", "supabase"):
        raise FalhaAcompanhamentoSupabase("FAROL_ACOMP_FONTE deve ser 'google' ou 'supabase'.")
    if fonte == "google":
        return False
    liberado = st.secrets.get("FAROL_ACOMP_SUPABASE_LIBERADO", False)
    if liberado is not True:
        raise FalhaAcompanhamentoSupabase(
            "Modo Supabase solicitado, mas a segunda trava de liberação não está ativada.")
    return True


def _config():
    url = str(st.secrets.get("PRISMA_SUPABASE_URL", "")).strip().rstrip("/")
    key = str(st.secrets.get("PRISMA_SUPABASE_SECRET_KEY", "") or
              st.secrets.get("PRISMA_SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    if not url.startswith("https://") or not key:
        raise FalhaAcompanhamentoSupabase("Credenciais do Supabase não configuradas.")
    return url, key


def _request(method, caminho, *, params=None, dados=None):
    url, key = _config()
    headers = {"apikey": key, "Accept": "application/json",
               "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    try:
        res = requests.request(method, url + caminho, headers=headers,
                               params=params, json=dados, timeout=25)
    except requests.RequestException as erro:
        raise FalhaAcompanhamentoSupabase(
            "Supabase indisponível. Nenhuma gravação alternativa foi realizada."
        ) from erro
    if res.status_code >= 400:
        mensagem = "Falha no Supabase (HTTP {}).".format(res.status_code)
        if res.status_code in (400, 409):
            # O corpo da API é usado somente para diferenciar conflitos; não vaza dados.
            try:
                corpo = str(res.json().get("message", ""))
            except (ValueError, TypeError, AttributeError):
                corpo = ""
            if "CONFLITO_DE_VERSAO" in corpo:
                mensagem = ("Conflito de edição: outro analista alterou esta ficha. "
                            "Reabra-a antes de salvar.")
            elif "Concentração já vinculada" in corpo:
                mensagem = "Esta concentração já possui acompanhamento."
        raise FalhaAcompanhamentoSupabase(mensagem)
    try:
        return res.json() if res.content else None
    except ValueError as erro:
        raise FalhaAcompanhamentoSupabase("Resposta inválida do Supabase.") from erro


def _filtros(tipo: str, filtros=None):
    if tipo not in TABELAS:
        raise ValueError("Tabela não autorizada.")
    tabela, campos = TABELAS[tipo]
    permitidos = set(campos)
    filtros = filtros or {}
    if any(col not in permitidos or not isinstance(valor, str)
           for col, valor in filtros.items()):
        raise ValueError("Filtro do acompanhamento inválido.")
    return tabela, campos, {c: "eq." + v for c, v in filtros.items()}


def _buscar(tipo: str, filtros=None, *, limite=None):
    tabela, campos, parametros = _filtros(tipo, filtros)
    parametros["select"] = ",".join(campos)
    # Paginação ordenada por PK para não repetir/omitir registros entre páginas.
    parametros["order"] = "chave.asc" if tipo == "vinculos" else "id.asc"
    if limite is not None:
        parametros["limit"] = int(limite)
        retorno = _request("GET", "/rest/v1/" + tabela, params=parametros)
        return retorno if isinstance(retorno, list) else []
    # Paginar corretamente: o PostgREST limita resultados por página.
    total = []
    while True:
        parametros["limit"], parametros["offset"] = 1000, len(total)
        grupo = _request("GET", "/rest/v1/" + tabela, params=parametros)
        if not isinstance(grupo, list):
            raise FalhaAcompanhamentoSupabase("Resposta de consulta inválida.")
        total.extend(grupo)
        if len(grupo) < 1000:
            break
        if len(total) > 250000:
            raise FalhaAcompanhamentoSupabase("Consulta ampla demais; revise filtros.")
    return total


@st.cache_data(ttl=15, max_entries=4, show_spinner=False)
def listar(tipo: str) -> list[dict]:
    """Leitura agregada para panorama e relatórios; invalidação após mutações."""
    return _buscar(tipo)


def buscar_ficha(evento_id) -> dict | None:
    dados = _buscar("fichas", {"id": str(evento_id)}, limite=2)
    if len(dados) > 1:
        raise FalhaAcompanhamentoSupabase("ID duplicado.")
    return dados[0] if dados else None


def buscar_por_evento(tipo: str, evento_id) -> list[dict]:
    if tipo not in ("vinculos", "movimentacoes"):
        raise ValueError("Tipo inválido.")
    return _buscar(tipo, {"evento_id": str(evento_id)})


def limpar_cache():
    listar.clear()


def _autor(acao):
    if not verificar_autenticacao() or not pode_executar_acao("3", acao):
        raise PermissionError("Sem permissão para alterar acompanhamentos do Farol.")
    autor = str(st.session_state.get("prisma_usuario_id", "")).strip()
    if not autor:
        raise FalhaAcompanhamentoSupabase("Usuário não identificado.")
    return autor


def hora_operacional():
    return datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")


def dados_ficha(**campos):
    """Normaliza ao contrato exato de 16 propriedades usado pelas funções SQL."""
    dados = {campo: str(campos.get(campo, "") if campos.get(campo, "") is not None else "")
             for campo in CAMPOS_FICHA if campo != "id"}
    for obrigatorio in ("titulo", "municipio"):
        if not dados[obrigatorio].strip():
            raise ValueError("Título e município são obrigatórios.")
    return dados


def criar(dados: dict, contexto: dict) -> str:
    autor = _autor("criar")
    if not isinstance(contexto, dict):
        raise ValueError("Não é permitido criar ficha sem concentração vinculada.")
    os_origem = contexto.get("os", [])
    if isinstance(os_origem, (list, tuple, set)):
        os_origem = ";".join(str(item).strip() for item in os_origem if str(item).strip())
    contexto_sql = {
        "chave": str(contexto.get("chave", "")),
        "municipio": str(contexto.get("municipio", "")),
        "bairros": str(contexto.get("bairros", "")),
        "latitude": str(contexto.get("latitude", "")),
        "longitude": str(contexto.get("longitude", "")),
        "os_origem": str(os_origem),
        "criado_em": hora_operacional(),
    }
    resultado = _request("POST", "/rest/v1/rpc/farol_acomp_criar_tx",
                         dados={"p_dados": dados, "p_contexto": contexto_sql,
                                "p_autor": autor})
    if not isinstance(resultado, dict) or not resultado.get("id"):
        raise FalhaAcompanhamentoSupabase("Gravação sem confirmação de identificador.")
    limpar_cache()
    return str(resultado["id"])


def editar(evento_id, versao_esperada, dados: dict, movimentacao: str) -> dict:
    autor = _autor("editar")
    try:
        versao = int(versao_esperada)
    except (TypeError, ValueError) as erro:
        raise FalhaAcompanhamentoSupabase(
            "Versão de abertura ausente. Reabra a ficha antes de salvar.") from erro
    if versao < 1:
        raise FalhaAcompanhamentoSupabase("Versão inválida.")
    resultado = _request("POST", "/rest/v1/rpc/farol_acomp_editar_tx",
                         dados={"p_id": str(evento_id), "p_versao": versao,
                                "p_dados": dados, "p_movimentacao": str(movimentacao),
                                "p_autor": autor})
    if not isinstance(resultado, dict) or int(resultado.get("versao", 0)) <= versao:
        raise FalhaAcompanhamentoSupabase("Edição sem confirmação da nova versão.")
    limpar_cache()
    return resultado


def recuperar_vinculo(chave_anterior: str, evento_id: str, contexto: dict) -> dict:
    """Acrescenta vínculo à ficha existente sem substituir o vínculo histórico.

    Só deve ser chamado após confirmação explícita no Módulo 3; a função SQL
    valida usuário, município, interseção de O.S. e concorrência.
    """
    autor = _autor("editar")
    if not isinstance(contexto, dict) or not isinstance(contexto.get("os"), (list, tuple, set)):
        raise ValueError("Contexto da concentração inválido para recuperação.")
    nova_chave = str(contexto.get("chave", "")).strip()
    if nova_chave == str(chave_anterior).strip():
        raise ValueError("A concentração já possui a chave original.")
    dados_contexto = {
        "chave": nova_chave,
        "municipio": str(contexto.get("municipio", "")).strip(),
        "bairros": str(contexto.get("bairros", "")).strip(),
        "latitude": str(contexto.get("latitude", "")),
        "longitude": str(contexto.get("longitude", "")),
        "os_origem": ";".join(sorted({
            str(item).strip() for item in contexto["os"] if str(item).strip()
        })),
    }
    resposta = _request(
        "POST", "/rest/v1/rpc/farol_acomp_recuperar_vinculo_tx",
        dados={
            "p_chave_anterior": str(chave_anterior).strip(),
            "p_evento_id": str(evento_id).strip(),
            "p_contexto": dados_contexto,
            "p_autor": autor,
        },
    )
    if (not isinstance(resposta, dict) or str(resposta.get("id")) != str(evento_id)
            or str(resposta.get("chave")) != nova_chave):
        raise FalhaAcompanhamentoSupabase("Recuperação sem confirmação de vínculo.")
    limpar_cache()
    return resposta
