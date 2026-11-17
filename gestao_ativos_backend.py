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
    "protocolo_equatorial", "data_protocolo_equatorial",
    "observacao", "municipio",
)
STATUS = {"operando", "parado", "em_manutencao", "em_observacao", "inativo", "validar"}
MAX_MARCADORES = 450
CONFIRMACAO_RESET_ATIVOS = "RESETAR FICHAS E HISTORICO"


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
                   "protocolo_equatorial,data_protocolo_equatorial,"
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



def listar_fichas_para_relatorio(municipio: str) -> list[dict]:
    """Consulta SOMENTE fichas do município, sem modificar o banco de ativos.

    A leitura é paginada; não usa MAX_MARCADORES do mapa, para não
    omitir fichas que estejam fora do recorte visual.
    """
    _exigir_leitura()
    municipio = str(municipio or "").strip()
    if not municipio or len(municipio) > 100 or "*" in municipio:
        raise ErroPRISMA("Selecione um município válido para gerar o relatório.")
    registros = []
    limite = 1000
    inicio = 0
    while True:
        lote = _tabela("prisma_ativos", params={
            "select": ("id,planta,nome,tipo,municipio,status,observacao,"
                       "ultima_os_operacao,ultima_os_eletromecanica,"
                       "protocolo_equatorial,atualizado_em"),
            "municipio": "ilike." + municipio,
            "order": "planta.asc",
            "limit": str(limite),
            "offset": str(inicio),
        })
        registros.extend(lote)
        if len(lote) < limite:
            break
        inicio += limite
        if inicio >= 10000:
            raise ErroPRISMA(
                "Município com mais de 10.000 ativos; refine a consulta antes do relatório."
            )
    return registros

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
                  "ultima_os_eletromecanica", "protocolo_equatorial",
                  "observacao", "municipio"):
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


def resetar_fichas_e_historico(confirmacao):
    """Limpa campos operacionais e histórico sem excluir/alterar identidade dos ativos.

    Executa UMA transação no Supabase, com auditoria geral atômica. A chamada é
    exclusiva do servidor Streamlit; a RPC só permite service_role e verifica
    novamente se o autor é um administrador ativo na própria base.
    """
    from auth import verificar_autenticacao

    if not verificar_autenticacao(forcar=True):
        raise ErroPRISMA("Sessão inválida ou expirada. Entre novamente.")
    if st.session_state.get("perfil") != "admin":
        raise ErroPRISMA("O reset está disponível somente para administradores.")
    if not pode_executar_acao(MODULO, "excluir"):
        raise ErroPRISMA("Seu usuário não possui permissão para executar o reset.")
    if confirmacao != CONFIRMACAO_RESET_ATIVOS:
        raise ErroPRISMA("Digite a frase de confirmação exatamente como apresentada.")

    autor = _autor("excluir")
    resultado = _tabela(
        "rpc/prisma_ativos_resetar_fichas_historico_tx",
        method="POST",
        payload={"p_autor": autor, "p_confirmacao": confirmacao},
    )
    if not isinstance(resultado, dict) or not all(
        isinstance(resultado.get(chave), int) and resultado[chave] >= 0
        for chave in ("ativos_preservados", "fichas_limpas", "historicos_excluidos")
    ):
        raise ErroPRISMA("O servidor não confirmou o resultado do reset.")
    return resultado
