"""PRISMA — acesso isolado ao cadastro de Gestão de Ativos no Supabase.

Nunca lê nem modifica os CSVs utilizados pelo Farol ou pela Consulta Operacional.
Todas as operações usam credenciais apenas no servidor Streamlit.
"""
from __future__ import annotations

import re
import uuid

import streamlit as st

from auth import pode_executar_acao, tem_acesso_modulo
from prisma_backend import ErroPRISMA, _tabela

MODULO = "9"
CAMPOS_EDITAVEIS = (
    "planta", "nome", "matricula", "status",
    "ultima_os_operacao", "data_ultima_os_operacao",
    "ultima_os_eletromecanica", "data_ultima_os_eletromecanica",
    "observacao", "municipio",
)
STATUS = {"operando", "parado", "em_manutencao", "inativo"}
MAX_MARCADORES = 450


def _autor(acao):
    if not pode_executar_acao(MODULO, acao):
        raise ErroPRISMA("Você não tem autorização para realizar esta ação.")
    autor = str(st.session_state.get("prisma_usuario_id") or "")
    try:
        return str(uuid.UUID(autor))
    except ValueError as exc:
        raise ErroPRISMA("Sessão sem identificação do usuário.") from exc


def _uuid(valor):
    try:
        return str(uuid.UUID(str(valor)))
    except ValueError as exc:
        raise ErroPRISMA("Identificador de ativo inválido.") from exc


def _exigir_leitura():
    if not tem_acesso_modulo(MODULO):
        raise ErroPRISMA("Acesso à Gestão de Ativos não autorizado.")


def validar_historico():
    """Sem a migração de auditoria (SQL 03) a página fica somente para leitura."""
    _exigir_leitura()
    try:
        _tabela("prisma_ativos_historico", params={"select": "id", "limit": "1"})
        return True
    except ErroPRISMA:
        return False


def municipios():
    _exigir_leitura()
    nomes = set()
    inicio = 0
    while True:
        lote = _tabela("prisma_ativos", params={
            "select": "municipio", "order": "municipio.asc",
            "limit": "1000", "offset": str(inicio),
        })
        nomes.update(str(r.get("municipio") or "").strip() for r in lote)
        if len(lote) < 1000:
            break
        inicio += 1000
    return sorted((n for n in nomes if n), key=str.casefold)


def listar_ativos(municipio=None, pesquisa="", status=None):
    _exigir_leitura()
    params = {
        "select": ("id,tipo,planta,nome,municipio,latitude,longitude,"
                   "matricula,status,ultima_os_operacao,data_ultima_os_operacao,"
                   "ultima_os_eletromecanica,data_ultima_os_eletromecanica,"
                   "observacao,atualizado_em"),
        "order": "planta.asc",
        "limit": str(MAX_MARCADORES + 1),
    }
    if municipio and municipio != "Todos":
        params["municipio"] = "eq." + municipio
    if status and status != "Todos":
        params["status"] = "eq." + status
    termo = re.sub(r"[^\w\s-]", "", pesquisa or "", flags=re.UNICODE).strip()[:70]
    if termo:
        params["or"] = f"(planta.ilike.*{termo}*,nome.ilike.*{termo}*)"
    return _tabela("prisma_ativos", params=params)


def obter_ativo(ativo_id):
    _exigir_leitura()
    registros = _tabela("prisma_ativos", params={
        "id": "eq." + _uuid(ativo_id), "select": "*", "limit": "1",
    })
    return registros[0] if registros else None


def historico_ativo(ativo_id):
    _exigir_leitura()
    return _tabela("prisma_ativos_historico", params={
        "ativo_id": "eq." + _uuid(ativo_id),
        "select": ("operacao,usuario_nome,usuario_email,registrado_em,"
                   "campos_alterados,dados_anteriores,dados_novos"),
        "order": "registrado_em.desc,id.desc", "limit": "60",
    })


def _validar_campos(campos):
    dados = {chave: campos.get(chave) for chave in CAMPOS_EDITAVEIS}
    dados["planta"] = str(dados["planta"] or "").strip()
    if not dados["planta"] or len(dados["planta"]) > 100:
        raise ErroPRISMA("Informe uma Planta válida (até 100 caracteres).")
    if dados["status"] not in STATUS and dados["status"] is not None:
        raise ErroPRISMA("Status inválido.")
    for campo in ("nome", "matricula", "ultima_os_operacao",
                  "ultima_os_eletromecanica", "observacao", "municipio"):
        dados[campo] = str(dados[campo]).strip() if dados[campo] is not None else None
        if dados[campo] == "":
            dados[campo] = None
    return dados


def criar_ativo(campos, latitude, longitude):
    autor = _autor("criar")
    latitude, longitude = float(latitude), float(longitude)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ErroPRISMA("Coordenadas inválidas.")
    dados = _validar_campos(campos)
    dados.update({
        "tipo": "poco", "latitude": latitude, "longitude": longitude,
        "origem": "cadastro_prisma", "criado_por": autor, "atualizado_por": autor,
    })
    registros = _tabela("prisma_ativos", method="POST",
                        payload=dados, prefer="return=representation")
    if not registros:
        raise ErroPRISMA("Não foi possível confirmar a criação do ativo.")
    return registros[0]


def editar_ativo(ativo_id, campos, versao):
    autor = _autor("editar")
    if not versao:
        raise ErroPRISMA("Reabra a ficha para obter a versão atual do ativo.")
    dados = _validar_campos(campos)
    dados["atualizado_por"] = autor
    registros = _tabela("prisma_ativos", method="PATCH", params={
        "id": "eq." + _uuid(ativo_id),
        "atualizado_em": "eq." + str(versao),
    }, payload=dados, prefer="return=representation")
    if not registros:
        raise ErroPRISMA(
            "Este ativo foi alterado por outro usuário. Reabra a ficha e tente novamente."
        )
    return registros[0]
