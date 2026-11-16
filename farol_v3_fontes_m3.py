"""Leituras read-only das fontes originais do Farol Operacional (Módulo 3).

PONTOS e EVENTOS vêm da planilha do M3. Nenhum método de escrita é usado.
A leitura de PONTOS retorna somente O.S. da data de emissão corrente ao V3.
"""
from __future__ import annotations

from datetime import date
import json

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

from farol_v3_nucleo_teste import coluna, data_valida


ABA_PONTOS = "PONTOS"
ABA_EVENTOS = "EVENTOS"
ID_DEMONSTRACAO_LEGADA = "1plvaCyZ23zAIjasrFnJxZm9Q-jB85AbCtSjIizhuoKg"


def _exigir_permissao_m3():
    """Um usuário do laboratório não ganha implicitamente acesso ao Módulo 3."""
    from auth import tem_acesso_modulo
    if not tem_acesso_modulo("3"):
        raise PermissionError(
            "Seu usuário precisa ter permissão para consultar o Módulo 3 "
            "antes de utilizar suas bases no Ambiente de Teste."
        )


def _identificador_planilha():
    spreadsheet_id = str(
        st.secrets.get("farol_producao_spreadsheet_id", "")
    ).strip()
    if not spreadsheet_id or spreadsheet_id == ID_DEMONSTRACAO_LEGADA:
        raise ValueError(
            "A fonte de dados do Módulo 3 não está configurada corretamente."
        )
    return spreadsheet_id


@st.cache_resource(show_spinner=False)
def _cliente_sheets_readonly():
    dados = st.secrets["gcp_json"]
    config = json.loads(dados) if isinstance(dados, str) else dict(dados)
    credenciais = Credentials.from_service_account_info(
        config,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return gspread.authorize(credenciais)


def _valores_aba(nome):
    if nome not in (ABA_PONTOS, ABA_EVENTOS):
        raise ValueError("Aba de leitura não permitida no laboratório.")
    planilha = _cliente_sheets_readonly().open_by_key(_identificador_planilha())
    return planilha.worksheet(nome).get_all_values()


def _linhas_com_cabecalho(valores):
    if not valores:
        return [], []
    cabecalho = [str(c).strip() for c in valores[0]]
    linhas = [
        (list(linha[:len(cabecalho)]) + [""] * max(0, len(cabecalho)-len(linha)))
        for linha in valores[1:]
        if any(str(c).strip() for c in linha)
    ]
    return cabecalho, linhas


def filtrar_registros_do_m3(valores, dia: date):
    """Recorta a data antes de criar DataFrame e devolve contagens verificáveis."""
    cabecalho, linhas = _linhas_com_cabecalho(valores)
    if not cabecalho:
        return pd.DataFrame(), {"lidos": 0, "do_dia": 0, "fora_do_dia": 0}
    coluna_data = coluna(
        pd.DataFrame(columns=cabecalho),
        ["Dt. Emissão", "Data Emissão", "Dt Emissao", "Data de Abertura", "Data"],
    )
    indice_data = cabecalho.index(coluna_data)
    somente_dia = []
    for linha in linhas:
        data_emissao = data_valida(linha[indice_data])
        if pd.notna(data_emissao) and data_emissao.date() == dia:
            somente_dia.append(linha)
    estatisticas = {
        "lidos": len(linhas),
        "do_dia": len(somente_dia),
        "fora_do_dia": len(linhas) - len(somente_dia),
    }
    return pd.DataFrame(somente_dia, columns=cabecalho), estatisticas


@st.cache_data(ttl=120, show_spinner=False, max_entries=3)
def _ler_pontos_m3_dia(dia_iso: str):
    """Cacheia apenas a fatia do dia, não o acumulado de oito dias."""
    return filtrar_registros_do_m3(_valores_aba(ABA_PONTOS), date.fromisoformat(dia_iso))


def ler_pontos_m3_dia(dia: date):
    _exigir_permissao_m3()
    return _ler_pontos_m3_dia(dia.isoformat())


@st.cache_data(ttl=120, show_spinner=False, max_entries=2)
def _ler_eventos_m3():
    """EVENTOS: consulta inteira só quando uma investigação é solicitada."""
    cabecalho, linhas = _linhas_com_cabecalho(_valores_aba(ABA_EVENTOS))
    return pd.DataFrame(linhas, columns=cabecalho)


def ler_eventos_m3():
    _exigir_permissao_m3()
    return _ler_eventos_m3()
