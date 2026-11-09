#!/usr/bin/env python3
"""PRISMA: replica assincrona Supabase -> Google Sheets, sem alterar o Farol.

- Identifica alteracoes na fila transacional do Supabase.
- Com --full, reconcilia todo o estado atual (primeira execucao).
- Atualiza / insere linhas no Google Sheets em RAW, sem apagar historico.
- Excluidos geram registro de exclusao; os dados antigos sao preservados.
- Confirma eventos APENAS depois de gravacao completa nas planilhas.
- --backup-only: snapshot historico no Google Drive privado (pasta opcional).

Proibido logar payloads, fichas, matriculas ou credenciais. A origem e sempre
Supabase; nenhuma operacao escreve nas tabelas das fichas no Supabase.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict

import gspread
import requests
from google.oauth2.service_account import Credentials

FONTES = {
    "farol_acomp_fichas": (
        "eventos", "id",
        ("id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao",
         "unidade", "regional", "municipio", "data_identificacao",
         "descricao", "arcgis", "sia", "loggers", "retorno_operacao",
         "finalizado", "data_finalizacao", "causa_identificada"),
    ),
    "farol_acomp_vinculos": (
        "vinculos_farol_producao", "chave",
        ("chave", "evento_id", "municipio", "bairros", "latitude",
         "longitude", "os_origem", "criado_em"),
    ),
    "farol_acomp_movimentacoes": (
        "movimentacoes", "id",
        ("id", "evento_id", "data_hora", "descricao", "status_momento"),
    ),
}
FILA = "farol_acomp_replica_fila"
STATUS = "farol_acomp_replica_status"
CHAVES_SEGURAS = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def agora() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def obrigatorio(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        raise RuntimeError(f"Configuracao obrigatoria ausente: {nome}")
    return valor


def celula(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return str(valor)


class Supabase:
    def __init__(self):
        self.base = obrigatorio("PRISMA_SUPABASE_URL").rstrip("/")
        self.chave = obrigatorio("PRISMA_SUPABASE_SERVICE_ROLE_KEY")
        if not self.base.startswith("https://"):
            raise RuntimeError("PRISMA_SUPABASE_URL deve utilizar HTTPS.")
        self.sessao = requests.Session()
        headers = {"apikey": self.chave, "Accept": "application/json",
                   "Content-Type": "application/json"}
        if not self.chave.startswith("sb_secret_"):
            headers["Authorization"] = "Bearer " + self.chave
        self.sessao.headers.update(headers)

    def requisitar(self, metodo, tabela, *, parametros=None, dados=None, headers=None):
        if tabela not in set(FONTES) | {FILA, STATUS}:
            raise RuntimeError("Tabela de replica nao autorizada.")
        try:
            r = self.sessao.request(
                metodo, f"{self.base}/rest/v1/{tabela}", params=parametros,
                json=dados, headers=headers, timeout=(10, 45),
            )
        except requests.RequestException as exc:
            raise RuntimeError("Falha de conexao com o Supabase.") from exc
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase rejeitou {metodo} na tabela {tabela} (HTTP {r.status_code}).")
        if r.content and "json" in r.headers.get("content-type", ""):
            return r.json(), r.headers
        return None, r.headers

    def selecionar(self, tabela, parametros):
        dados, _ = self.requisitar("GET", tabela, parametros=parametros)
        if not isinstance(dados, list):
            raise RuntimeError("Formato inesperado de consulta do Supabase.")
        return dados

    def paginas(self, tabela, *, filtro=None):
        resultados = []
        campo_pk = FONTES[tabela][1]
        while True:
            parametros = {"select": "*", "order": f"{campo_pk}.asc",
                          "limit": 1000, "offset": len(resultados)}
            if filtro:
                parametros.update(filtro)
            parte = self.selecionar(tabela, parametros)
            resultados.extend(parte)
            if len(parte) < 1000:
                return resultados
            if len(resultados) > 300000:
                raise RuntimeError("Limite de seguranca de consulta excedido.")

    def fila_pendente(self, limite=500):
        return self.selecionar(FILA, {
            "select": "id,tabela,chave,operacao",
            "processado_em": "is.null", "order": "id.asc", "limit": limite,
        })

    def pendentes(self) -> int:
        _, cab = self.requisitar(
            "GET", FILA, parametros={"select": "id", "processado_em": "is.null", "limit": 1},
            headers={"Prefer": "count=exact"},
        )
        cr = cab.get("Content-Range", "")
        try:
            return int(cr.split("/")[-1])
        except (ValueError, IndexError):
            return 0

    def confirmar(self, ids):
        for inicio in range(0, len(ids), 150):
            parte = [int(v) for v in ids[inicio:inicio + 150]]
            self.requisitar("PATCH", FILA,
                parametros={"id": "in.(" + ",".join(map(str, parte)) + ")",
                            "processado_em": "is.null"},
                dados={"processado_em": agora()},
                headers={"Prefer": "return=minimal"})

    def salvar_status(self, sucesso, criados=0, atualizados=0, erro=""):
        # Upsert nunca apaga o ultimo_sucesso diante de uma falha.
        linha = {"id": 1, "ultima_execucao": agora(),
                 "situacao": "SUCESSO" if sucesso else "FALHA",
                 "pendentes": self.pendentes(),
                 "enviados": criados, "atualizados": atualizados,
                 "ultimo_erro": erro[:220], "atualizado_em": agora()}
        if sucesso:
            linha["ultimo_sucesso"] = agora()
        self.requisitar("POST", STATUS, parametros={"on_conflict": "id"},
                        dados=linha,
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def por_chaves(self, tabela, chaves):
        if not chaves:
            return []
        campo = FONTES[tabela][1]
        registros = []
        for inicio in range(0, len(chaves), 40):
            grupo = chaves[inicio:inicio + 40]
            if any(not CHAVES_SEGURAS.fullmatch(str(c)) for c in grupo):
                raise RuntimeError("Chave de replica inesperada; nenhuma gravacao confirmada.")
            filtro = "in.(" + ",".join('"' + str(c) + '"' for c in grupo) + ")"
            registros.extend(self.paginas(tabela, filtro={campo: filtro}))
        return registros


def cliente_google():
    credenciais = json.loads(obrigatorio("FAROL_GCP_JSON"))
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive.file"]
    google = Credentials.from_service_account_info(credenciais, scopes=scopes)
    planilha = gspread.authorize(google).open_by_key(
        obrigatorio("FAROL_ACOMP_SPREADSHEET_ID"))
    return google, planilha


def conferir_aba(ws, nome, pk, colunas):
    linhas = ws.get_all_values()
    if not linhas or tuple(map(str.strip, linhas[0])) != tuple(colunas):
        raise RuntimeError(f"Cabecalho da aba {nome} diferente do esperado. Replica interrompida.")
    indice = {}
    for numero, linha in enumerate(linhas[1:], start=2):
        if not any(str(v).strip() for v in linha):
            continue
        chave = linha[colunas.index(pk)].strip() if len(linha) > colunas.index(pk) else ""
        if not chave or chave in indice:
            raise RuntimeError(f"Chave ausente/duplicada na aba {nome}. Replica interrompida.")
        if any(str(v).strip() for v in linha[len(colunas):]):
            raise RuntimeError(f"Colunas extras com dados na aba {nome}. Replica interrompida.")
        indice[chave] = (numero, list(linha)[:len(colunas)] + [""] * max(0, len(colunas)-len(linha)))
    return indice


def atualizar_aba(ws, nome, pk, colunas, registros, *, simulacao=False):
    """Reconcilia por PK, conservando linhas preexistentes; reexecucao idempotente."""
    indice = conferir_aba(ws, nome, pk, colunas)
    vistos = set()
    mudancas, insercoes = [], []
    for registro in registros:
        chave = celula(registro.get(pk)).strip()
        if not chave or chave in vistos:
            raise RuntimeError(f"Chave invalida/duplicada no Supabase para {nome}.")
        vistos.add(chave)
        valores = [celula(registro.get(col)) for col in colunas]
        antigo = indice.get(chave)
        if antigo is None:
            insercoes.append(valores)
        elif antigo[1] != valores:
            # Colunas esperadas: ate Q; nao toca em colunas externas.
            coluna_fim = "Q" if len(colunas) == 17 else ("H" if len(colunas) == 8 else "E")
            mudancas.append({
                "range": f"A{antigo[0]}:{coluna_fim}{antigo[0]}",
                "values": [valores],
            })
    if simulacao:
        return len(insercoes), len(mudancas)
    # Mais de 500 ranges por batch pode exceder o tamanho da requisicao.
    for i in range(0, len(mudancas), 100):
        ws.batch_update(mudancas[i:i + 100], value_input_option="RAW")
    for i in range(0, len(insercoes), 150):
        ws.append_rows(insercoes[i:i + 150], value_input_option="RAW")
    return len(insercoes), len(mudancas)


def registrar_exclusoes(planilha, exclusoes):
    """Nao apaga do Sheets: mantem tombstones e retencao para recuperacao."""
    if not exclusoes:
        return
    nome = "replica_exclusoes"
    cab = ["EVENTO_FILA", "TABELA", "CHAVE", "REGISTRADO_EM"]
    try:
        ws = planilha.worksheet(nome)
    except gspread.WorksheetNotFound:
        ws = planilha.add_worksheet(title=nome, rows=500, cols=4)
        ws.update(range_name="A1:D1", values=[cab], value_input_option="RAW")
    indice = conferir_aba(ws, nome, "EVENTO_FILA", cab)
    novos = []
    for evento in exclusoes:
        chave = str(evento["id"])
        if chave not in indice:
            novos.append([chave, evento["tabela"], evento["chave"], agora()])
    if novos:
        ws.append_rows(novos, value_input_option="RAW")


def executar(full=False, simulacao=False):
    sb = Supabase()
    try:
        google, planilha = cliente_google()
        del google
        pendentes = sb.fila_pendente()
        por_tabela = defaultdict(dict)
        for evento in pendentes:
            tabela = evento["tabela"]
            if tabela not in FONTES:
                raise RuntimeError("Fila contem tabela nao autorizada.")
            por_tabela[tabela][evento["chave"]] = evento
        totais = {"criados": 0, "atualizados": 0}
        exclusoes = []
        for tabela, (aba_nome, pk, colunas) in FONTES.items():
            if full:
                registros = sb.paginas(tabela)
            else:
                chaves = list(por_tabela[tabela])
                if not chaves:
                    continue
                registros = sb.por_chaves(tabela, chaves)
            estado = {celula(r[pk]): r for r in registros}
            if len(estado) != len(registros):
                raise RuntimeError(f"Chave duplicada recebida em {tabela}.")
            if not full:
                referencias = set(por_tabela[tabela])
                if not set(estado).issubset(referencias):
                    raise RuntimeError("Consulta retornou chave nao solicitada.")
            ws = planilha.worksheet(aba_nome)
            novos, alterados = atualizar_aba(
                ws, aba_nome, pk, colunas, registros, simulacao=simulacao)
            totais["criados"] += novos
            totais["atualizados"] += alterados
            for chave, evento in por_tabela[tabela].items():
                if chave not in estado and evento["operacao"] == "DELETE":
                    exclusoes.append(evento)
                elif chave not in estado:
                    raise RuntimeError(
                        "Fila indica insercao/edicao mas registro nao existe; revise a origem.")
        if simulacao:
            print("SIMULACAO (nenhum dado alterado): seriam inseridas {} "
                  "e atualizadas {} linhas. Exclusoes identificadas: {}."
                  .format(totais["criados"], totais["atualizados"], len(exclusoes)))
            return
        registrar_exclusoes(planilha, exclusoes)
        # Acks apenas depois de atualizar as tres planilhas e registrar exclusoes.
        sb.confirmar([v["id"] for v in pendentes])
        sb.salvar_status(True, totais["criados"], totais["atualizados"])
        print("Replica concluida. Inseridos={}, atualizados={}, pendentes={}."
              .format(totais["criados"], totais["atualizados"], sb.pendentes()))
    except Exception as exc:
        # Nunca publicar textos de registros nos logs do CI.
        if not simulacao:
            try:
                sb.salvar_status(False, erro=type(exc).__name__)
            except Exception:
                pass
        raise RuntimeError("Replica nao concluida. Consulte o status administrativo e os logs protegidos do job.") from exc


def backup_drive():
    """Exporta snapshot para pasta PRIVADA de Drive previamente compartilhada."""
    folder = obrigatorio("FAROL_BACKUP_DRIVE_FOLDER_ID")
    sb = Supabase()
    cred_dict = json.loads(obrigatorio("FAROL_GCP_JSON"))
    cred = Credentials.from_service_account_info(
        cred_dict, scopes=["https://www.googleapis.com/auth/drive.file"])
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    instante = dt.datetime.now(dt.timezone.utc)
    nome = "farol_acomp_supabase_{}.zip".format(instante.strftime("%Y%m%d_%H%M%SZ"))
    buf = io.BytesIO()
    contagens = {}
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        for tabela in FONTES:
            registros = sb.paginas(tabela)
            contagens[tabela] = len(registros)
            arquivo.writestr(
                tabela + ".json",
                json.dumps(registros, ensure_ascii=False, indent=2),
            )
        arquivo.writestr("manifesto.json", json.dumps({
            "gerado_em_utc": instante.isoformat(),
            "origem": "Supabase PRISMA",
            "contagens": contagens,
            "observacao": "Backup historico; nao publicar no GitHub.",
        }, ensure_ascii=False, indent=2))
    buf.seek(0)
    drive = build("drive", "v3", credentials=cred, cache_discovery=False)
    media = MediaIoBaseUpload(buf, mimetype="application/zip", resumable=False)
    drive.files().create(body={"name": nome, "parents": [folder],
                               "mimeType": "application/zip"},
                         media_body=media, fields="id", supportsAllDrives=True).execute()
    print("Backup historico enviado para a pasta privada configurada.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                        help="Reconciliacao inicial/total dos dados do Supabase")
    parser.add_argument("--backup-only", action="store_true",
                        help="Exporta snapshot no Google Drive privado configurado")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula reconciliacao sem gravar na replica")
    args = parser.parse_args()
    try:
        if args.backup_only:
            backup_drive()
        else:
            executar(full=args.full, simulacao=args.dry_run)
    except Exception:
        print("ERRO: rotina de replica/backup nao concluida. Verifique as configuracoes e o monitor.",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
