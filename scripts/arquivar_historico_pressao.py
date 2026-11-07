"""Arquivamento seguro, manual, do mês encerrado da aba baixa_pressao.

Somente lê Google Sheets. Nunca apaga ou altera registros.
Por padrão simula; para enviar é necessário --modo arquivar.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill

FUSO = ZoneInfo("America/Sao_Paulo")
ABA = "baixa_pressao"
PASTA = "modulo_1_baixa_pressao"
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIMITE_BYTES = 20 * 1024 * 1024


def nome_normalizado(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", str(valor).strip().lower())
    return "".join(c for c in texto if not unicodedata.combining(c)).replace(" ", "_")


def analisar_data(valor: str) -> date | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    if re.fullmatch(r"\d{4,5}(?:\.0+)?", texto):
        try:
            resultado = date(1899, 12, 30) + timedelta(days=int(float(texto)))
            return resultado if 2000 <= resultado.year <= 2100 else None
        except (ValueError, OverflowError):
            pass
    return None


def competencia_padrao() -> str:
    hoje = datetime.now(FUSO).date()
    primeiro_dia = hoje.replace(day=1)
    anterior = primeiro_dia - timedelta(days=1)
    return anterior.strftime("%Y-%m")


def selecionar_competencia(valores: list[list[str]], competencia: str):
    if not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])", competencia):
        raise ValueError("Competência inválida: informe AAAA-MM entre 2000 e 2099.")
    if len(valores) < 2:
        raise ValueError("A aba está vazia ou contém somente cabeçalho.")
    cabecalho = [str(v or "").strip() for v in valores[0]]
    while cabecalho and not cabecalho[-1]:
        cabecalho.pop()
    if not cabecalho or len(cabecalho) > 40 or any(not v for v in cabecalho):
        raise ValueError("Cabeçalho vazio, incompleto ou muito extenso.")
    normalizados = [nome_normalizado(v) for v in cabecalho]
    for campo in ("id", "data", "municipio", "bairro", "latitude", "longitude", "pressao_mca", "matricula"):
        if campo not in normalizados:
            raise ValueError("Cabeçalho obrigatório da aba baixa_pressao não reconhecido.")
    if len(set(normalizados)) != len(normalizados):
        raise ValueError("Colunas duplicadas no cabeçalho da aba.")
    coluna_data = normalizados.index("data")
    linhas, invalidas = [], 0
    for valores_linha in valores[1:]:
        linha = [str(v) if v is not None else "" for v in valores_linha]
        if not any(c.strip() for c in linha):
            continue
        if len(linha) > len(cabecalho) and any(v.strip() for v in linha[len(cabecalho):]):
            raise ValueError("Existem dados fora das colunas do cabeçalho; arquivamento bloqueado.")
        linha = (linha + [""] * len(cabecalho))[:len(cabecalho)]
        data_registro = analisar_data(linha[coluna_data])
        if data_registro is None:
            invalidas += 1
            continue
        if data_registro.strftime("%Y-%m") == competencia:
            linhas.append(linha)
    if invalidas:
        raise ValueError(f"Há {invalidas} registro(s) com data não interpretável; arquivamento bloqueado.")
    if not linhas:
        raise ValueError(f"Nenhuma medição encontrada em {competencia}; nada foi enviado.")
    return cabecalho, linhas


def assinatura_dados(cabecalho: list[str], linhas: list[list[str]]) -> str:
    payload = json.dumps([cabecalho, linhas], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def criar_xlsx(cabecalho: list[str], linhas: list[list[str]]) -> bytes:
    """Exportação fiel ao texto recebido do Sheets; IDs e zeros à esquerda intactos."""
    livro = Workbook(write_only=True)
    aba = livro.create_sheet("Medicoes")
    aba.freeze_panes = "A2"
    titulo = []
    for valor in cabecalho:
        celula = WriteOnlyCell(aba, value=valor)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="155B84")
        celula.data_type = "s"
        titulo.append(celula)
    aba.append(titulo)
    for linha in linhas:
        celulas = []
        for valor in linha:
            celula = WriteOnlyCell(aba, value=valor)
            celula.data_type = "s"  # Não avalia observações que comecem por '='.
            celulas.append(celula)
        aba.append(celulas)
    saida = io.BytesIO()
    livro.save(saida)
    conteudo = saida.getvalue()
    if len(conteudo) > LIMITE_BYTES:
        raise ValueError("Arquivo mensal excedeu 20 MB; revisão necessária.")
    return conteudo


def conferir_xlsx(conteudo: bytes, cabecalho: list[str], linhas: list[list[str]]):
    livro = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    try:
        aba = livro["Medicoes"]
        registros = list(aba.iter_rows(values_only=True))
        recuperados = [["" if v is None else str(v) for v in linha] for linha in registros]
        esperado = [cabecalho] + linhas
        if recuperados != esperado:
            raise ValueError("Conteúdo lido do XLSX difere do Google Sheets; não considerar arquivado.")
    finally:
        livro.close()


def ler_google():
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["SAI_GCP_JSON"])
    credenciais = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    planilha = gspread.authorize(credenciais).open_by_key(os.environ["SAI_SHEET_ID"])
    return planilha.worksheet(ABA).get_all_values()


def storage_request(base: str, chave: str, bucket: str, caminho: str, metodo: str,
                    dados: bytes | None = None) -> bytes | None:
    rota = "/".join(quote(parte, safe="") for parte in [bucket, *caminho.split("/")])
    prefixo = "object/authenticated" if metodo == "GET" else "object"
    url = f"{base}/storage/v1/{prefixo}/{rota}"
    cabecalhos = {"apikey": chave}
    if dados is not None:
        cabecalhos.update({"Content-Type": TIPO_XLSX, "x-upsert": "false"})
    req = Request(url, data=dados, headers=cabecalhos, method=metodo)
    try:
        with urlopen(req, timeout=60) as resposta:
            return resposta.read()
    except HTTPError as erro:
        # Algumas versões do Supabase Storage retornam HTTP 400 com
        # "error": "not_found" e "message": "Object not found" para arquivo
        # inexistente. Esse caso pode iniciar um upload novo; todos os demais
        # erros devem interromper o processamento (incluindo autenticação/RLS).
        try:
            resposta_erro = json.loads(erro.read(2048).decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            resposta_erro = {}
        if not isinstance(resposta_erro, dict):
            resposta_erro = {}
        codigo = str(resposta_erro.get("code") or resposta_erro.get("error") or "").lower()
        mensagem = str(resposta_erro.get("message") or "").lower()
        ausencia_confirmada = (
            erro.code in (400, 404)
            and (
                codigo in ("nosuchkey", "not_found", "404")
                and ("object not found" in mensagem or "no such key" in mensagem)
            )
        )
        if metodo == "GET" and ausencia_confirmada:
            return None
        # Não colocar no log a resposta original: pode incluir parâmetros
        # confidenciais. Exibe apenas um código padronizado e conhecido.
        codigos_permitidos = {
            "invalidjwt", "invalid_jwt", "accessdenied", "unauthorized",
            "invalidrequest", "nosuchbucket", "invalidapikey", "invalid_api_key",
            "not_found", "nosuchkey",
        }
        detalhe = f"; código={codigo}" if codigo in codigos_permitidos else ""
        raise RuntimeError(
            f"Supabase Storage retornou HTTP {erro.code} na operação {metodo}{detalhe}."
        ) from erro
    except URLError as erro:
        raise RuntimeError("Não foi possível conectar ao Supabase Storage.") from erro


def executar(competencia: str, modo: str):
    cabecalho, linhas = selecionar_competencia(ler_google(), competencia)
    assinatura = assinatura_dados(cabecalho, linhas)
    conteudo = criar_xlsx(cabecalho, linhas)
    conferir_xlsx(conteudo, cabecalho, linhas)
    print(f"Competência={competencia}; módulo=1; registros={len(linhas)}; colunas={len(cabecalho)}")
    print(f"Integridade local verificada; SHA256 dos registros={assinatura[:16]}…")
    if modo == "simular":
        print("SIMULAÇÃO CONCLUÍDA: nada foi enviado e Google Sheets não foi alterado.")
        return
    if modo != "arquivar":
        raise ValueError("Modo inválido.")
    base = os.environ["PRISMA_HISTORICO_SUPABASE_URL"].rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co"):
        raise ValueError("URL do Supabase inválida ou fora de *.supabase.co.")
    chave = os.environ["PRISMA_HISTORICO_SUPABASE_SECRET_KEY"]
    if not chave.startswith("sb_secret_"):
        raise ValueError("Configure uma secret key sb_secret_ no GitHub Actions.")
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
    print("Google Sheets preservado; exclusão e agendamento permanecem desativados.")


def main():
    p = argparse.ArgumentParser(description="Arquivamento manual e seguro de baixas pressões")
    p.add_argument("--competencia", default="", help="AAAA-MM; em branco, mês anterior")
    p.add_argument("--modo", choices=["simular", "arquivar"], default="simular")
    args = p.parse_args()
    executar(args.competencia.strip() or competencia_padrao(), args.modo)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Logs não devem expor tokens, matrícula ou conteúdo das medições.
        print(f"FALHA NO ARQUIVAMENTO: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
