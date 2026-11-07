"""Piloto de arquivamento do Módulo 2 no Supabase Storage.

Leitura SOMENTE do Google Sheets; simulação padrão; não agenda nem apaga dados.
Preserva linhas A:G legadas e B:H com ID em uma estrutura unificada de 8 campos.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta

from arquivar_historico_pressao import (
    competencia_padrao,
    criar_xlsx,
    conferir_xlsx,
    assinatura_dados,
    storage_request,
)

ABA = "mapeamento_pressao"
PASTA = "modulo_2_historico_pressoes"
# Mesmo ID utilizado atualmente em pages/2_Mapeamento_Pressao.py.
ID_PLANILHA_ATUAL = "1idCgabn5g5X48hmdE-gqWfrUKUf2XxWGB5M_yJFk0yM"
COLUNAS = ["ID", "Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observação"]


def _chave(valor: object) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().upper())
    return "".join(ch for ch in texto if ch.isalnum() and not unicodedata.combining(ch))


def _data(valor: object) -> date | None:
    texto = str(valor or "").strip().strip("'")
    if not texto or texto.lower() in ("nan", "none", "nat", "-"):
        return None
    for formato in (
        "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    if re.fullmatch(r"[0-9]{5}(?:[.,][0-9]+)?", texto):
        try:
            numero = float(texto.replace(",", "."))
            if 20000 <= numero <= 90000:
                return (datetime(1899, 12, 30) + timedelta(days=numero)).date()
        except (ValueError, OverflowError):
            pass
    return None


def _numero(valor: object, minimo: float, maximo: float) -> float | None:
    texto = str(valor or "").strip().replace(" ", "")
    if not texto or texto.lower() in ("nan", "none", "nat"):
        return None
    if "," in texto and "." in texto:
        texto = (texto.replace(".", "").replace(",", ".")
                 if texto.rfind(",") > texto.rfind(".") else texto.replace(",", ""))
    else:
        texto = texto.replace(",", ".")
    try:
        valor_num = float(texto)
    except (TypeError, ValueError, OverflowError):
        return None
    return valor_num if math.isfinite(valor_num) and minimo <= valor_num <= maximo else None


def _medicao_valida(campos: list[str]) -> bool:
    if len(campos) < 7:
        return False
    data, municipio, ponto, lat, lon, mca, _observacao = campos[:7]
    return (
        _data(data) is not None
        and bool(str(municipio).strip())
        and bool(str(ponto).strip())
        and _numero(lat, -90, 90) not in (None, 0)
        and _numero(lon, -180, 180) not in (None, 0)
        and _numero(mca, 0, float("inf")) is not None
    )


def _layout_cabecalho(linha: list[str]) -> str:
    chaves = [_chave(v) for v in linha]
    esperado = [_chave(v) for v in COLUNAS[1:]]
    if chaves[:7] == esperado and not any(chaves[7:]):
        return "sem_id"
    if chaves[:8] == ["ID", *esperado] and not any(chaves[8:]):
        return "com_id"
    raise ValueError("Cabeçalho Módulo 2 desconhecido; nenhum arquivamento realizado.")


def _interpretar_linha(raw: list[str]) -> tuple[str, list[str]] | None:
    # Reproduz a classificação segura do Módulo 2, sem alterar a origem.
    valores = raw + [""] * max(0, 8 - len(raw))
    if any(str(v).strip() for v in valores[8:]):
        return None
    sem_id = _medicao_valida(valores[:7])
    com_id = _medicao_valida(valores[1:8])
    if sem_id and not valores[7] and not com_id:
        return "sem_id", ["", *valores[:7]]
    if com_id and not sem_id:
        return "com_id", valores[:8]
    return None


def selecionar_competencia(valores: list[list[str]], competencia: str):
    if not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])", competencia):
        raise ValueError("Competência inválida: informe AAAA-MM.")
    if len(valores) < 2:
        raise ValueError("A aba do Módulo 2 não contém medições.")
    modo = _layout_cabecalho(valores[0])
    linhas, invalidas, com_id, sem_id = [], [], 0, 0
    for numero_linha, original in enumerate(valores[1:], start=2):
        raw = [str(v) if v is not None else "" for v in original]
        if not any(valor.strip() for valor in raw):
            continue
        interpretada = _interpretar_linha(raw)
        if interpretada is None:
            invalidas.append(numero_linha)
            continue
        layout, campos = interpretada
        if _data(campos[1]).strftime("%Y-%m") != competencia:
            continue
        linhas.append(campos)
        if layout == "com_id":
            com_id += 1
        else:
            sem_id += 1
    # Nunca ocultar uma linha que não conseguimos relacionar com um mês.
    if invalidas:
        amostra = ", ".join(str(numero) for numero in invalidas[:10])
        raise ValueError(
            f"Existem {len(invalidas)} linha(s) não interpretáveis na planilha "
            f"(linhas {amostra}). Revisão necessária; nenhuma gravação realizada."
        )
    if not linhas:
        raise ValueError(f"Não há medições válidas em {competencia}; nenhuma gravação realizada.")
    return COLUNAS[:], linhas, {"sem_id": sem_id, "com_id": com_id, "cabecalho": modo}


def ler_google() -> list[list[str]]:
    import gspread
    from google.oauth2.service_account import Credentials
    credenciais = Credentials.from_service_account_info(
        json.loads(os.environ["SAI_GCP_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    sheet_id = os.environ.get("PRISMA_HISTORICO_M2_SHEET_ID", "").strip() or ID_PLANILHA_ATUAL
    try:
        return gspread.authorize(credenciais).open_by_key(sheet_id).worksheet(ABA).get_all_values()
    except Exception as exc:
        raise RuntimeError(
            "Falha de leitura do Módulo 2; verifique se a conta de serviço do GitHub "
            "tem acesso de leitura à planilha mapeamento_pressao."
        ) from exc


def executar(competencia: str, modo: str) -> None:
    cabecalho, linhas, formatos = selecionar_competencia(ler_google(), competencia)
    assinatura = assinatura_dados(cabecalho, linhas)
    conteudo = criar_xlsx(cabecalho, linhas)
    conferir_xlsx(conteudo, cabecalho, linhas)
    print(
        f"Competência={competencia}; módulo=2; registros={len(linhas)}; "
        f"colunas={len(cabecalho)}; origem A:G={formatos['sem_id']}; "
        f"origem B:H={formatos['com_id']}; cabeçalho={formatos['cabecalho']}"
    )
    print(f"Integridade local validada; SHA256 dos registros={assinatura[:16]}…")
    if modo == "simular":
        print("SIMULAÇÃO CONCLUÍDA: nenhum envio ou alteração no Google Sheets.")
        return
    if modo != "arquivar":
        raise ValueError("Modo inválido.")
    from urllib.parse import urlparse
    base = os.environ["PRISMA_HISTORICO_SUPABASE_URL"].rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co"):
        raise ValueError("URL do Supabase inválida.")
    chave = os.environ["PRISMA_HISTORICO_SUPABASE_SECRET_KEY"]
    if not chave.startswith("sb_secret_"):
        raise ValueError("Secret key sb_secret_ não configurada no GitHub Actions.")
    bucket = os.environ.get("PRISMA_HISTORICO_BUCKET", "historico-prisma")
    if bucket != "historico-prisma":
        raise ValueError("Bucket inesperado; operação bloqueada.")
    caminho = f"{PASTA}/{competencia[:4]}/{competencia}.xlsx"
    atual = storage_request(base, chave, bucket, caminho, "GET")
    if atual is not None:
        conferir_xlsx(atual, cabecalho, linhas)
        print(f"ARQUIVO JÁ EXISTE E ESTÁ IDÊNTICO: {caminho}. Nenhuma substituição.")
        return
    storage_request(base, chave, bucket, caminho, "POST", conteudo)
    baixado = storage_request(base, chave, bucket, caminho, "GET")
    if baixado is None:
        raise RuntimeError("O arquivo não pôde ser recuperado após o envio.")
    conferir_xlsx(baixado, cabecalho, linhas)
    print(f"ARQUIVAMENTO VALIDADO: {caminho}; registros={len(linhas)}; bytes={len(baixado)}")
    print("Google Sheets preservado; sem agendamento e sem exclusões.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Arquivamento seguro do histórico Módulo 2")
    parser.add_argument("--competencia", default="", help="AAAA-MM, padrão mês anterior")
    parser.add_argument("--modo", choices=["simular", "arquivar"], default="simular")
    args = parser.parse_args()
    executar(args.competencia.strip() or competencia_padrao(), args.modo)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FALHA NO ARQUIVAMENTO M2: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
