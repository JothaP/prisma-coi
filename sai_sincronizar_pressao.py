"""SAI — importacao semiautomatica de medicoes de pressao.

Origem: XLSX/CSV recebidos em pasta privada do Google Drive via Power Automate.
Destino: worksheet 'baixa_pressao' ja utilizada pelo Modulo 1, mais o diario
         'sai_sync_pressao' no mesmo Google Sheets.

Execução: serviço Python do SAI no GitHub Actions a cada 30 minutos; não depende do app aberto.
Este codigo NAO precisa ser executado no notebook do analista.

Nunca enviar credenciais, planilhas brutas ou dados de clientes ao repositório GitHub.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Mesmo conversor e mesmas regras de duplicidade do upload no Modulo 1.
from sai_pressao_importacao import (
    COLUNAS_SAI, Resultado, IndiceDuplicidades, converter_bruta,
    ler_arquivo, chave_medicao, normalizar_cabecalho,
)

# O conversor compartilhado e a deduplicação continuam usando as 9 colunas A:I.
# A coluna J é adicionada apenas DEPOIS de selecionar os registros novos.
FUSO = ZoneInfo("America/Sao_Paulo")
COLUNA_PROTOCOLO = "Cod_Protocolo"
COLUNAS_PLANILHA = list(COLUNAS_SAI) + [COLUNA_PROTOCOLO]


def _nome_campo(valor):
    """Reconhece variações de acentos e pontuação no cabeçalho original."""
    texto = unicodedata.normalize("NFKD", str(valor or "").lower())
    return re.sub(r"[^a-z0-9]", "", "".join(
        c for c in texto if not unicodedata.combining(c)
    ))


def _campo(df, *nomes):
    colunas = {_nome_campo(nome): nome for nome in df.columns}
    return next((colunas[_nome_campo(nome)] for nome in nomes
                 if _nome_campo(nome) in colunas), None)


def _texto(valor):
    if valor is None or pd.isna(valor):
        return ""
    saida = str(valor).strip()
    if saida.lower() in ("none", "nan", "nat"):
        return ""
    if re.fullmatch(r"\d+\.0", saida):
        return saida[:-2]
    return saida


def _numero(valor):
    try:
        txt = _texto(valor).replace(",", ".")
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:mca)?\s*", txt, re.I)
        numero = float(match.group(1)) if match else None
        return round(numero, 6) if numero is not None and pd.notna(numero) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _data(valor):
    if isinstance(valor, (datetime, date, pd.Timestamp)):
        return valor.strftime("%Y-%m-%d")
    txt = _texto(valor)
    if not txt:
        return ""
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _matricula(valor):
    return _texto(valor).replace(" ", "").upper()


def _atividade_observacao(observacao):
    match = re.search(r"\bSAI_ATIVIDADE\s*=\s*([A-Z0-9_-]+)", _texto(observacao), re.I)
    return match.group(1).upper() if match else ""


def _chaves_registro(lat, lon, matricula, data, pressao):
    """Chaves auxiliares somente para localizar o protocolo, NUNCA para deduplicar."""
    lat, lon = _numero(lat), _numero(lon)
    if lat is None or lon is None:
        return []
    mat = _matricula(matricula)
    data = _data(data)
    pressao = _numero(pressao)
    chaves = []
    if data and pressao is not None:
        chaves.append(("completa", lat, lon, mat, data, pressao))
    if data:
        chaves.append(("com_data", lat, lon, mat, data))
    if pressao is not None:
        chaves.append(("com_pressao", lat, lon, mat, pressao))
    chaves.append(("local", lat, lon, mat))
    return chaves


def _indice_protocolos(df):
    """Extrai o protocolo da exportação original antes do descarte no conversor.

    São indexadas também as linhas com protocolo vazio, para impedir que o
    protocolo de outra atividade no mesmo endereço seja copiado por engano.
    """
    col_proto = _campo(df, "Cód. Protocolo Origem", "Cod. Protocolo Origem",
                       "Protocolo Origem", "Cod_Protocolo", "Protocolo")
    if col_proto is None:
        return False, {}, {}
    col_id = _campo(df, "ID Atividade", "ID da Atividade", "ID Ativ",
                    "Código Atividade", "Código da Atividade", "Cod Atividade",
                    "Número da Atividade", "Numero da Atividade", "Nº Atividade",
                    "Activity ID", "ID")
    col_lat = _campo(df, "Latitude", "Lat")
    col_lon = _campo(df, "Longitude", "Lon", "Long")
    col_mat = _campo(df, "Matrícula", "Matricula", "Matricula do Imovel",
                     "Matrícula do Imóvel", "Nr Matricula", "Número da Matrícula",
                     "Matrícula do Cliente")
    col_data = _campo(df, "Data", "Data da atividade", "Data da Atividade")
    col_press = _campo(df, "Qual A Pressão Da Rede (Mca)", "Pressao_MCA",
                       "Pressão (MCA)", "Pressao")
    por_atividade, por_local = {}, {}
    for _, row in df.iterrows():
        proto = _texto(row.get(col_proto))
        if col_id:
            atv = _texto(row.get(col_id)).upper()
            if atv:
                por_atividade.setdefault(atv, set()).add(proto)
        for chave in _chaves_registro(
            row.get(col_lat) if col_lat else None,
            row.get(col_lon) if col_lon else None,
            row.get(col_mat) if col_mat else "",
            row.get(col_data) if col_data else "",
            row.get(col_press) if col_press else None,
        ):
            por_local.setdefault(chave, set()).add(proto)
    return True, por_atividade, por_local


def anexar_protocolos(novos, df_bruto):
    """Acrescenta J aos registros JÁ deduplicados; não altera A:I.

    Somente aceita associação unívoca pelo ID da atividade ou pelos campos
    geográficos originais. Em caso de dúvida, bloqueia o lote antes de gravar:
    a deduplicação impediria recuperar o protocolo em reexecuções futuras.
    """
    tem_coluna, por_atividade, por_local = _indice_protocolos(df_bruto)
    linhas, preenchidos, ambiguos = [], 0, 0
    for row in novos:
        linha = list(row)
        if len(linha) == 10:
            protocolo = _texto(linha[9])
            linhas.append(linha[:9] + [protocolo])
            preenchidos += bool(protocolo)
            continue
        if len(linha) != 9:
            raise ValueError("Conversor retornou linha com quantidade inesperada de colunas")
        if not tem_coluna:
            # Arquivo legado: não modifica o processamento nem inventa protocolo.
            linhas.append(linha + [""])
            continue
        atividade = _atividade_observacao(linha[7])
        candidatos = por_atividade.get(atividade) if atividade else None
        if candidatos is None:
            for chave in _chaves_registro(linha[4], linha[5], linha[8], linha[1], linha[6]):
                valores = por_local.get(chave)
                if valores:
                    candidatos = valores
                    break
        if candidatos is None or len(candidatos) != 1:
            ambiguos += 1
            linhas.append(linha + [""])
            continue
        protocolo = next(iter(candidatos))
        preenchidos += bool(protocolo)
        linhas.append(linha + [protocolo])
    if ambiguos:
        raise ValueError(
            f"Protocolo: {ambiguos} medição(ões) sem vínculo único com a origem. "
            "Nenhum registro deste lote foi gravado."
        )
    return linhas, preenchidos


def conectar_google():
    """Usa a mesma conta de serviço do SAI; não escreve arquivos no Drive."""
    import gspread
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    credenciais = json.loads(os.environ['SAI_GCP_JSON'])
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.readonly',
    ]
    cred = Credentials.from_service_account_info(credenciais, scopes=scopes)
    sheets = gspread.authorize(cred).open_by_key(os.environ['SAI_SHEET_ID'])
    drive = build('drive', 'v3', credentials=cred, cache_discovery=False)
    return sheets, drive


COLUNAS_DIARIO = [
    'DataHora', 'ArquivoID', 'MD5Drive', 'HashSHA256', 'Arquivo',
    'LinhasOrigem', 'Elegiveis', 'Inseridos', 'JaExistentes',
    'SemPressaoNumerica', 'Status', 'Detalhes',
]
SUCESSOS = {'IMPORTADO', 'SEM_NOVOS', 'SEM_ELEGIVEIS', 'SEM_ALTERACOES'}


def abrir_abas(spreadsheet, dry_run=False):
    """Em homologação, nunca cria abas nem modifica a planilha."""
    from gspread.exceptions import WorksheetNotFound
    ws = spreadsheet.worksheet('baixa_pressao')
    cab = ws.row_values(1)
    # As primeiras 9 colunas continuam com o contrato original, sem mudança.
    if [normalizar_cabecalho(x) for x in cab[:len(COLUNAS_SAI)]] != [
        normalizar_cabecalho(x) for x in COLUNAS_SAI
    ]:
        raise ValueError('Cabeçalho A:I de baixa_pressao divergente; nenhuma importação realizada')
    if len(cab) < 10 or _nome_campo(cab[9]) != _nome_campo(COLUNA_PROTOCOLO):
        raise ValueError('Adicione Cod_Protocolo em J1 da aba baixa_pressao antes de sincronizar')
    try:
        diario = spreadsheet.worksheet('sai_sync_pressao')
    except WorksheetNotFound:
        if dry_run:
            diario = None
        else:
            diario = spreadsheet.add_worksheet(title='sai_sync_pressao', rows=1000, cols=12)
            diario.append_row(COLUNAS_DIARIO, value_input_option='RAW')
    else:
        if diario.row_values(1) != COLUNAS_DIARIO:
            raise ValueError('Cabeçalho de sai_sync_pressao divergente; verifique a aba')
    return ws, diario


def ler_diario(diario):
    if diario is None:
        return set(), set()
    dados = diario.get_all_values()
    finalizados = set()
    hashes_finalizados = set()
    for linha in dados[1:]:
        if len(linha) > 10 and linha[10] in SUCESSOS:
            finalizados.add((linha[1], linha[2]))
            hashes_finalizados.add(linha[3])
    return finalizados, hashes_finalizados


def logar(diario, *, meta, hash_sha, resumo, novos, repetidos, status, detalhes=''):
    now = datetime.now(FUSO).strftime('%d/%m/%Y %H:%M:%S')
    diario.append_row([
        now, meta['id'], meta.get('md5Checksum') or meta.get('modifiedTime',''),
        hash_sha, meta['name'], resumo.origem, len(resumo.linhas),
        novos, repetidos, resumo.sem_pressao_numerica, status, detalhes[:350],
    ], value_input_option='RAW')


def listar_arquivos_drive(drive, folder_id):
    """Só XLSX/CSV reais da pasta; não lê planilhas Google nativas."""
    q = "'{}' in parents and trashed = false".format(folder_id.replace("'", "\\'"))
    pagina = None
    dados = []
    while True:
        resposta = drive.files().list(
            q=q, fields='nextPageToken,files(id,name,size,mimeType,md5Checksum,modifiedTime)',
            pageSize=100, pageToken=pagina, supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        for item in resposta.get('files', []):
            name = item.get('name', '')
            if name.lower().endswith(('.xlsx', '.csv')) and not name.startswith('~$'):
                dados.append(item)
        pagina = resposta.get('nextPageToken')
        if not pagina:
            break
    return sorted(dados, key=lambda x: (x.get('modifiedTime',''), x.get('name','')))


def baixar_drive(drive, meta):
    tamanho_max = int(os.getenv('SAI_MAX_FILE_MB','20')) * 1024 * 1024
    if int(meta.get('size') or 0) > tamanho_max:
        raise ValueError('Tamanho superior ao limite permitido')
    from googleapiclient.http import MediaIoBaseDownload
    buff = io.BytesIO()
    request = drive.files().get_media(fileId=meta['id'], supportsAllDrives=True)
    downloader = MediaIoBaseDownload(buff, request, chunksize=1024*1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if buff.tell() > tamanho_max:
            raise ValueError('Arquivo excede o limite configurado')
    return buff.getvalue()


def carregar_chaves_sheet(ws):
    """Uma leitura do Sheets para identificar medições já registradas."""
    return IndiceDuplicidades(ws.get_all_values()[1:])


def executar_sincronizacao():
    import hashlib
    sheet, drive = conectar_google()
    dry_run = os.getenv('SAI_DRY_RUN', 'false').lower() in ('true','1','yes')
    ws, diario = abrir_abas(sheet, dry_run=dry_run)
    finalizados, hashes_finalizados = ler_diario(diario)
    metas = listar_arquivos_drive(drive, os.environ['SAI_DRIVE_FOLDER_ID'])
    print(f'SAI: {len(metas)} arquivo(s) elegível(is) na pasta de entrada')
    # Não há credenciais Microsoft nem consultas diretas ao OneDrive.
    pendentes = [meta for meta in metas if (meta['id'], meta.get('md5Checksum') or meta.get('modifiedTime','')) not in finalizados]
    limite = int(os.getenv('SAI_MAX_FILES_PER_RUN', '20'))
    chaves = None
    falhas = 0
    for meta in pendentes[:limite]:
        try:
            content = baixar_drive(drive, meta)
            hash_sha = hashlib.sha256(content).hexdigest()
            if (meta['id'], meta.get('md5Checksum') or meta.get('modifiedTime','')) in finalizados:
                continue
            if hash_sha in hashes_finalizados:
                resumo = Resultado(origem=0, linhas=[])
                if not dry_run:
                    logar(diario, meta=meta, hash_sha=hash_sha, resumo=resumo,
                          novos=0, repetidos=0, status='SEM_ALTERACOES',
                          detalhes='Arquivo de conteúdo idêntico já processado')
                finalizados.add((meta['id'], meta.get('md5Checksum') or meta.get('modifiedTime','')))
                print('SAI: conteúdo já processado, ignorado')
                continue
            df = ler_arquivo(meta['name'], content)
            if _campo(df, "Cód. Protocolo Origem", "Cod. Protocolo Origem",
                      "Protocolo Origem", "Cod_Protocolo", "Protocolo") is None:
                print('SAI: AVISO: origem sem coluna de protocolo; novos registros terão J vazio')
            resultado = converter_bruta(df, meta['name'])
            if not resultado.linhas:
                status, novos_qtd, repetidos = 'SEM_ELEGIVEIS', 0, 0
            else:
                if chaves is None:
                    chaves = carregar_chaves_sheet(ws)
                # Usa a mesma regra de duplicidade do upload do Módulo 1.
                novos, repetidos, correspondencias_legadas = chaves.selecionar(resultado.linhas)
                repetidos_planilha = repetidos
                repetidos_arquivo = 0  # Incluídos no total de repetidos nesta versão.
                if dry_run:
                    _, protocolos_vinculados = anexar_protocolos(novos, df)
                    print(f'SAI: simulação: {protocolos_vinculados} protocolo(s) vinculados aos novos registros')
                    print(
                        f'SAI: SIMULAÇÃO (SEM GRAVAÇÃO) | arquivo={meta["name"]} '
                        f'| atividades={resultado.origem} '
                        f'| elegiveis={len(resultado.linhas)} '
                        f'| ja_na_planilha={repetidos_planilha} '
                        f'| repetidos_no_arquivo={repetidos_arquivo} '
                        f'| novos={len(novos)} '
                        f'| sem_pressao_numerica={resultado.sem_pressao_numerica} '
                        f'| sem_coordenadas={resultado.sem_coordenadas} '
                        f'| sem_data={resultado.sem_data} '
                        f'| descartados={resultado.origem-len(resultado.linhas)}'
                    )
                    continue
                # Protocolo é acrescentado após a deduplicação; a seleção continua
                # usando exclusivamente o conversor compartilhado (A:I).
                novos_com_protocolo, protocolos_vinculados = anexar_protocolos(novos, df)
                for inicio in range(0, len(novos_com_protocolo), 200):
                    lote = novos_com_protocolo[inicio:inicio+200]
                    ws.append_rows(lote, value_input_option='RAW')
                    # O índice já foi atualizado ao selecionar o lote.
                novos_qtd = len(novos)
                status = 'IMPORTADO' if novos_qtd else 'SEM_NOVOS'
                print(f'SAI: protocolos vinculados nesta importação: {protocolos_vinculados}')
            if dry_run:
                print(f'SAI: SIMULAÇÃO: arquivo={meta["name"]}; elegíveis={len(resultado.linhas)}; sem escrita')
                continue
            logar(diario, meta=meta, hash_sha=hash_sha, resumo=resultado,
                  novos=novos_qtd, repetidos=repetidos, status=status,
                  detalhes=(f'Coord. inválidas: {resultado.sem_coordenadas}; '
                            f'sem data: {resultado.sem_data}'))
            hashes_finalizados.add(hash_sha)
            finalizados.add((meta['id'], meta.get('md5Checksum') or meta.get('modifiedTime','')))
            print(f'SAI: {meta["name"]}: {status}; novos={novos_qtd}; repetidos={repetidos}')
        except Exception as exc:
            falhas += 1
            # Não expor dados de clientes, nomes de matrículas nem conteúdo da base em logs.
            print(f'SAI: falha de processamento: {exc.__class__.__name__}', file=sys.stderr)
            try:
                if dry_run:
                    continue
                logar(diario, meta=meta, hash_sha=meta.get('md5Checksum',''),
                      resumo=Resultado(origem=0,linhas=[]), novos=0,repetidos=0,
                      status='ERRO', detalhes=exc.__class__.__name__)
            except Exception:
                pass
    if falhas:
        raise RuntimeError(f'{falhas} arquivo(s) apresentaram erro; consultar log sai_sync_pressao')


def validar_local(path):
    arquivo = Path(path)
    df = ler_arquivo(arquivo.name, arquivo.read_bytes())
    r = converter_bruta(df, arquivo.name)
    _, protocolos = anexar_protocolos(r.linhas, df)
    print(f'SAI validação local: atividades={r.origem}; elegíveis={len(r.linhas)}; '
          f'0 MCA={sum(row[6] == 0 for row in r.linhas)}; '
          f'sem cota numérica={r.sem_pressao_numerica}; sem data={r.sem_data}; '
          f'protocolos encontrados={protocolos}')
    return r


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--arquivo-local', help='Validação sem acesso a Drive/Sheets')
    args = parser.parse_args()
    if args.arquivo_local:
        validar_local(args.arquivo_local)
    else:
        executar_sincronizacao()


if __name__ == '__main__':
    main()
