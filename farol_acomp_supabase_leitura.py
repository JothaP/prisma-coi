"""PRISMA / Farol — leitura isolada do Acompanhamento no Supabase.

Etapa de validação: SOMENTE SELECT. Não altera o Módulo 3 nem faz gravações.
Credenciais são lidas exclusivamente dos Secrets do Streamlit no servidor.
"""
from __future__ import annotations

import re

import requests
import streamlit as st

TABELAS = {
    "fichas": ("farol_acomp_fichas", "id", (
        "id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao",
        "unidade", "regional", "municipio", "data_identificacao", "descricao",
        "arcgis", "sia", "loggers", "retorno_operacao", "finalizado",
        "data_finalizacao", "causa_identificada")),
    "vinculos": ("farol_acomp_vinculos", "chave", (
        "chave", "evento_id", "municipio", "bairros", "latitude", "longitude",
        "os_origem", "criado_em")),
    "movimentacoes": ("farol_acomp_movimentacoes", "id", (
        "id", "evento_id", "data_hora", "descricao", "status_momento")),
}


class ErroLeituraFarol(RuntimeError):
    pass


def _configuracao() -> tuple[str, str]:
    url = str(st.secrets.get("PRISMA_SUPABASE_URL", "")).strip().rstrip("/")
    chave = str(st.secrets.get("PRISMA_SUPABASE_SECRET_KEY", "") or
                st.secrets.get("PRISMA_SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    if not url.startswith("https://") or not chave:
        raise ErroLeituraFarol("Credenciais de serviço do Supabase não configuradas.")
    return url, chave


def _ler(tipo: str, filtros: dict[str, str], *, limite: int = 500) -> list[dict]:
    """Nunca aceita nomes de tabela ou filtros arbitrários fornecidos pelo navegador."""
    if tipo not in TABELAS:
        raise ErroLeituraFarol("Conjunto de dados não autorizado.")
    tabela, _, colunas = TABELAS[tipo]
    if not filtros or any(coluna not in colunas or not isinstance(valor, str)
                          for coluna, valor in filtros.items()):
        raise ErroLeituraFarol("Filtro inválido ou ausente.")
    url, chave = _configuracao()
    cabecalhos = {"apikey": chave, "Accept": "application/json"}
    if not chave.startswith("sb_secret_"):
        cabecalhos["Authorization"] = "Bearer " + chave
    parametros = {"select": ",".join(colunas) + ",origem_hash", "limit": max(1, min(int(limite), 1000))}
    parametros.update({campo: "eq." + valor for campo, valor in filtros.items()})
    try:
        resposta = requests.get(f"{url}/rest/v1/{tabela}",
                                params=parametros, headers=cabecalhos, timeout=20)
    except requests.RequestException as exc:
        raise ErroLeituraFarol("Falha de comunicação com o Supabase.") from exc
    if resposta.status_code != 200:
        raise ErroLeituraFarol(
            f"Falha na leitura da tabela {tabela} (HTTP {resposta.status_code})."
        )
    try:
        dados = resposta.json()
    except ValueError as exc:
        raise ErroLeituraFarol("Resposta inválida do Supabase.") from exc
    if not isinstance(dados, list):
        raise ErroLeituraFarol("Formato inesperado da consulta.")
    return dados


def _id_seguro(valor: object) -> str:
    identificador = str(valor or "").strip()
    if not identificador or len(identificador) > 160 or re.search(r"[\x00-\x1f]", identificador):
        raise ErroLeituraFarol("Identificador inválido.")
    return identificador


def ler_ficha(evento_id: object) -> dict | None:
    registros = _ler("fichas", {"id": _id_seguro(evento_id)}, limite=2)
    if len(registros) > 1:
        raise ErroLeituraFarol("ID de ficha duplicado no destino.")
    return registros[0] if registros else None


def ler_vinculos(evento_id: object) -> list[dict]:
    registros = _ler("vinculos", {"evento_id": _id_seguro(evento_id)})
    if len(registros) >= 500:
        raise ErroLeituraFarol("Muitos vínculos; consulta interrompida para revisão.")
    return registros


def ler_movimentacoes(evento_id: object) -> list[dict]:
    registros = _ler("movimentacoes", {"evento_id": _id_seguro(evento_id)})
    if len(registros) >= 500:
        raise ErroLeituraFarol("Muitas movimentações; consulta interrompida para revisão.")
    return sorted(registros, key=lambda r: (int(r["id"]) if str(r["id"]).isdigit()
                                              else str(r["id"])))
