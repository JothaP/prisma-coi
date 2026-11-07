"""Auditoria somente leitura do histórico de pressões antes de qualquer limpeza.

Nunca remove ou edita medições no Google Sheets e nunca grava no Supabase.
Exibe apenas contagens, sem matrículas, coordenadas, nomes ou identificadores.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import os
import re
import sys
import unicodedata

from openpyxl import load_workbook

from arquivar_historico_pressao import storage_request, analisar_data as data_m1
from arquivar_historico_pressao_modulo2 import (
    _interpretar_linha as interpretar_m2,
    _data as data_m2,
    ID_PLANILHA_ATUAL,
)

M1_CAMPOS = (
    "ID", "Data", "Municipio", "Bairro", "Latitude", "Longitude",
    "Pressao_MCA", "Observacao", "Matricula",
)
M2_CAMPOS = (
    "ID", "Data", "Município", "Pontos", "Latitude", "Longitude",
    "MCA", "Observação",
)
CAMINHOS = {
    "1": ("baixa_pressao", "modulo_1_baixa_pressao"),
    "2": ("mapeamento_pressao", "modulo_2_historico_pressoes"),
}


class FalhaAuditoria(RuntimeError):
    pass


def chave(nome):
    texto = unicodedata.normalize("NFKD", str(nome or "").strip().lower())
    return "".join(c for c in texto if not unicodedata.combining(c) and c.isalnum())


def numero(texto, casas=None):
    valor = str(texto or "").strip()
    if not valor:
        return ""
    if "," in valor and "." in valor:
        valor = (valor.replace(".", "").replace(",", ".")
                 if valor.rfind(",") > valor.rfind(".") else valor.replace(",", ""))
    else:
        valor = valor.replace(",", ".")
    try:
        decimal = Decimal(valor)
    except (InvalidOperation, ValueError):
        return "INVALIDO"
    if not decimal.is_finite():
        return "INVALIDO"
    if casas is not None:
        decimal = decimal.quantize(Decimal(1).scaleb(-casas))
    return format(decimal.normalize(), "f")


def canonico_m1(registro: dict[str, str], incluir_protocolo: bool) -> tuple:
    valores = []
    for campo in M1_CAMPOS + (("Cod_Protocolo",) if incluir_protocolo else ()):
        texto = str(registro.get(chave(campo), "") or "").strip()
        if campo == "Data":
            data = data_m1(texto)
            texto = data.isoformat() if data else "DATA_INVALIDA"
        elif campo in ("Latitude", "Longitude"):
            texto = numero(texto, 6)
        elif campo == "Pressao_MCA":
            texto = numero(texto, 2)
        valores.append(texto)
    return tuple(valores)


def canonico_m2(registro: list[str]) -> tuple:
    valores = []
    for i, campo in enumerate(M2_CAMPOS):
        texto = str(registro[i] or "").strip()
        if campo == "Data":
            data = data_m2(texto)
            texto = data.isoformat() if data else "DATA_INVALIDA"
        elif campo in ("Latitude", "Longitude"):
            texto = numero(texto, 6)
        elif campo == "MCA":
            texto = numero(texto, 2)
        valores.append(texto)
    return tuple(valores)


def _mapear(cabecalho: list[str], linha: list[str], importantes: tuple) -> dict[str, str]:
    resultado = {}
    for i, nome in enumerate(cabecalho):
        campo = chave(nome)
        if not campo:
            continue
        valor = str(linha[i] or "").strip() if i < len(linha) else ""
        if campo in resultado and campo in importantes:
            anterior = resultado[campo]
            if anterior and valor and anterior != valor:
                raise FalhaAuditoria("Campos operacionais conflitantes; limpeza bloqueada.")
        if campo not in resultado or (not resultado[campo] and valor):
            resultado[campo] = valor
    return resultado


def _m1_registros(cabecalho, linhas, mes, incluir_protocolo) -> list[tuple]:
    essenciais = tuple(chave(v) for v in M1_CAMPOS)
    nomes = [chave(v) for v in cabecalho]
    if not set(essenciais).issubset(set(nomes)):
        raise FalhaAuditoria("Módulo 1: cabeçalho obrigatório ausente.")
    resultado = []
    for linha in linhas:
        if not any(str(x or "").strip() for x in linha):
            continue
        registro = _mapear(cabecalho, linha, essenciais + ("codprotocolo",))
        data = data_m1(registro.get("data", ""))
        if not data:
            raise FalhaAuditoria("Módulo 1: registro com data inválida; nenhuma limpeza é segura.")
        if data.strftime("%Y-%m") == mes:
            resultado.append(canonico_m1(registro, incluir_protocolo))
    return resultado


def _m2_registros(linhas, mes, formato_arquivo=False, cabecalho=None) -> list[tuple]:
    resultado = []
    for linha in linhas:
        if not any(str(x or "").strip() for x in linha):
            continue
        dados = [str(x) if x is not None else "" for x in linha]
        if formato_arquivo:
            if not cabecalho or set(map(chave, M2_CAMPOS)) != set(map(chave, cabecalho)):
                raise FalhaAuditoria("Módulo 2: cabeçalho do arquivo inesperado.")
            mapa = _mapear(cabecalho, dados, tuple(map(chave, M2_CAMPOS)))
            campos = [mapa.get(chave(nome), "") for nome in M2_CAMPOS]
        else:
            interpretado = interpretar_m2(dados)
            if interpretado is None:
                raise FalhaAuditoria("Módulo 2: linha legada inválida; nenhuma limpeza é segura.")
            campos = interpretado[1]
        data = data_m2(campos[1])
        if not data:
            raise FalhaAuditoria("Módulo 2: registro com data inválida.")
        if data.strftime("%Y-%m") == mes:
            resultado.append(canonico_m2(campos))
    return resultado


def conciliar(modulo: str, mes: str, cab_sheet, linhas_sheet, cab_arquivo, linhas_arquivo) -> dict:
    if modulo == "1":
        protocolo = ("codprotocolo" in {chave(v) for v in cab_sheet}
                     and "codprotocolo" in {chave(v) for v in cab_arquivo})
        planilha = _m1_registros(cab_sheet, linhas_sheet, mes, protocolo)
        arquivo = _m1_registros(cab_arquivo, linhas_arquivo, mes, protocolo)
    elif modulo == "2":
        planilha = _m2_registros(linhas_sheet, mes)
        arquivo = _m2_registros(linhas_arquivo, mes, True, cab_arquivo)
    else:
        raise FalhaAuditoria("Módulo desconhecido.")
    atuais, arquivados = Counter(planilha), Counter(arquivo)
    em_ambos = sum((atuais & arquivados).values())
    resultado = {
        "competencia": mes,
        "modulo": int(modulo),
        "planilha": len(planilha),
        "supabase": len(arquivo),
        "registros_conciliados": em_ambos,
        "somente_planilha": sum((atuais - arquivados).values()),
        "somente_supabase": sum((arquivados - atuais).values()),
        "limpeza_executada": False,
    }
    return resultado


def google_ler(modulo):
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["SAI_GCP_JSON"])
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    documento = (
        os.environ["SAI_SHEET_ID"]
        if modulo == "1"
        else os.environ.get("PRISMA_HISTORICO_M2_SHEET_ID") or ID_PLANILHA_ATUAL
    )
    return gspread.authorize(creds).open_by_key(documento).worksheet(CAMINHOS[modulo][0]).get_all_values()


def supabase_ler(modulo, mes):
    base = os.environ["PRISMA_HISTORICO_SUPABASE_URL"].rstrip("/")
    chave_secreta = os.environ["PRISMA_HISTORICO_SUPABASE_SECRET_KEY"]
    if not base.startswith("https://") or not chave_secreta:
        raise FalhaAuditoria("Configuração Supabase ausente.")
    bucket = os.environ.get("PRISMA_HISTORICO_BUCKET") or "historico-prisma"
    if bucket != "historico-prisma":
        raise FalhaAuditoria("Bucket inesperado.")
    caminho = f"{CAMINHOS[modulo][1]}/{mes[:4]}/{mes}.xlsx"
    dados = storage_request(base, chave_secreta, bucket, caminho, "GET")
    if dados is None:
        raise FalhaAuditoria(f"Arquivo de histórico não encontrado para Módulo {modulo}, {mes}.")
    if len(dados) > 20 * 1024 * 1024:
        raise FalhaAuditoria("Arquivo histórico acima do limite de segurança.")
    livro = load_workbook(BytesIO(dados), read_only=True, data_only=True)
    try:
        aba = livro["Medicoes"] if "Medicoes" in livro.sheetnames else livro.active
        linhas = list(aba.iter_rows(values_only=True))
    finally:
        livro.close()
    if len(linhas) < 2:
        raise FalhaAuditoria("Histórico sem registros.")
    cabecalho = ["" if x is None else str(x) for x in linhas[0]]
    registros = [["" if x is None else str(x) for x in l] for l in linhas[1:]]
    return cabecalho, registros


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--modulo", choices=["1", "2"], required=True)
    parser.add_argument("--competencias", required=True,
                        help="Uma ou mais competências AAAA-MM, separadas por vírgula")
    args = parser.parse_args()
    meses = [m.strip() for m in args.competencias.split(",")]
    if not meses or len(meses) > 12 or len(meses) != len(set(meses)):
        raise FalhaAuditoria("Informe de 1 a 12 competências sem repetição.")
    if any(not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])", m) for m in meses):
        raise FalhaAuditoria("Competências inválidas.")
    # Lê cada aba uma única vez para reduzir operações Google Sheets.
    sheet = google_ler(args.modulo)
    if not sheet:
        raise FalhaAuditoria("Google Sheets vazio.")
    for mes in meses:
        cabecalho, registros = supabase_ler(args.modulo, mes)
        resumo = conciliar(args.modulo, mes, sheet[0], sheet[1:], cabecalho, registros)
        print("AUDITORIA SOMENTE LEITURA: " + json.dumps(resumo, ensure_ascii=False))
    print("CONCLUÍDO: nenhum arquivo ou registro foi alterado/excluído.")


if __name__ == "__main__":
    try:
        main()
    except FalhaAuditoria as exc:
        # Mensagens de controle sem valores operacionais.
        print(f"FALHA NA AUDITORIA: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Bibliotecas externas podem incluir URLs e parâmetros em mensagens.
        # Não divulgar dados operacionais nos logs de repositório público.
        print(f"FALHA NA AUDITORIA: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
