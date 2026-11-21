"""Limpeza opcional do acompanhamento do Farol, somente por administrador.

Não executa limpezas na importação deste módulo; a ação parte exclusivamente
da confirmação explícita no Módulo 3. Cópias arquivadas/backup não são apagadas.
"""
from __future__ import annotations

import gspread
import streamlit as st

from auth import pode_executar_acao, verificar_autenticacao
from farol_acomp_supabase_backend import (
    FalhaAcompanhamentoSupabase,
    _request,
    fonte_supabase,
    limpar_cache as limpar_cache_supabase,
)


def _exigir_administrador():
    if (
        not verificar_autenticacao()
        or str(st.session_state.get("perfil", "")).strip().lower() != "admin"
        or not pode_executar_acao("3", "excluir")
        or not str(st.session_state.get("prisma_usuario_id", "")).strip()
    ):
        raise PermissionError(
            "Somente administradores ativos podem excluir fichas de acompanhamento."
        )
    return str(st.session_state["prisma_usuario_id"])


def _limpar_fichas_google():
    """Limpa a fonte legada de acompanhamento, preservando abas e cabeçalhos.

    Valida todos os cabeçalhos antes de iniciar qualquer exclusão. Os vínculos
    da ficha ficam na mesma planilha de acompanhamento, não na base de O.S.
    """
    from acompanhamento_operacional_farol import (
        CAMPOS_EVENTOS, CAMPOS_MOVIMENTACOES, ler_registros,
    )
    from integracao_acompanhamento_producao import (
        CAMPOS_VINCULO, _planilha, planilha_id,
        invalidar_cache_acompanhamento,
    )

    planilha = _planilha(planilha_id())
    layouts = (
        ("vinculos_farol_producao", CAMPOS_VINCULO),
        ("movimentacoes", CAMPOS_MOVIMENTACOES),
        ("eventos", CAMPOS_EVENTOS),
    )
    abas = []
    for nome, cabecalho in layouts:
        try:
            aba = planilha.worksheet(nome)
        except gspread.WorksheetNotFound:
            continue
        valores = aba.get_all_values()
        cabecalho_existente = [str(c).strip() for c in valores[0]] if valores else []
        legada = nome == "eventos" and cabecalho_existente == list(cabecalho[:-1])
        if valores and cabecalho_existente != list(cabecalho) and not legada:
            raise ValueError(
                "Cabeçalho inesperado na aba de acompanhamento "
                + nome + ". Nenhuma exclusão iniciada."
            )
        # Não cria novas abas ou campos que nunca existiram.
        abas.append((nome, aba, cabecalho_existente or list(cabecalho), max(0, len(valores) - 1)))

    resultado = {"fonte": "Google Sheets", "fichas": 0,
                 "movimentacoes": 0, "vinculos": 0}
    para_contagem = {
        "eventos": "fichas",
        "movimentacoes": "movimentacoes",
        "vinculos_farol_producao": "vinculos",
    }
    try:
        for nome, aba, cabecalho, quantidade in abas:
            aba.clear()
            aba.update(range_name="A1", values=[cabecalho], value_input_option="RAW")
            resultado[para_contagem[nome]] = quantidade
    finally:
        # Mesmo com uma falha intermediária, não exibir dados antigos em cache.
        ler_registros.clear()
        invalidar_cache_acompanhamento()
    return resultado


def excluir_fichas_acompanhamento_admin():
    """Apaga fichas, movimentações e vínculos somente da fonte ativa.

    Em Supabase, uma única RPC aplica todas as exclusões e a auditoria na
    mesma transação, incluindo âncoras de eventos e incorporações.
    A réplica histórica externa no Google permanece como backup.
    """
    autor = _exigir_administrador()
    if not fonte_supabase():
        return _limpar_fichas_google()

    retorno = _request(
        "POST", "/rest/v1/rpc/farol_acomp_limpar_tudo_admin_tx",
        dados={"p_autor": autor},
    )
    if not isinstance(retorno, dict) or retorno.get("concluido") is not True:
        raise FalhaAcompanhamentoSupabase(
            "O Supabase não confirmou a exclusão das fichas."
        )
    limpar_cache_supabase()
    # O Módulo 3 e o Relatório Integrado usam leituras auxiliares cacheadas.
    from acompanhamento_operacional_farol import ler_registros
    from integracao_acompanhamento_producao import invalidar_cache_acompanhamento
    ler_registros.clear()
    invalidar_cache_acompanhamento()
    return {"fonte": "Supabase", **retorno}
