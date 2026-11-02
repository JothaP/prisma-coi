"""Gráficos de pressão compartilhados por dia para o Farol Operacional.

Armazenamento na aba independente FAROL_GRAFICOS_DIA da planilha cujo ID é
recebido pelo módulo chamador (produção ou teste). Expiração pelo dia civil
no fuso America/Fortaleza; limpeza física no primeiro acesso do novo dia.
Não gravar as imagens em PONTOS, EVENTOS ou outras abas operacionais.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from PIL import Image as PILImage, ImageOps, UnidentifiedImageError

FUSO = ZoneInfo("America/Fortaleza")
ABA = "FAROL_GRAFICOS_DIA"
CABECALHO = ["DATA", "CHAVE", "LOGGER", "ATUALIZADO_EM", "IMAGEM_01"]
TAMANHO_PARTE = 40000  # Abaixo do limite por célula do Sheets.
TAMANHO_MAX_UPLOAD = 6 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_PARTES = 70


def data_hoje():
    return datetime.now(FUSO).strftime("%Y-%m-%d")


def chave_logger(registro):
    """Identifica um logger independentemente do ID dinâmico da concentração."""
    identificador = str(registro.get("ID Logger", "") or "").strip()
    if not identificador:
        identificador = str(registro.get("Identificação", "") or "").strip()
    if not identificador:
        identificador = str(registro.get("Nome", "") or "").strip()
    municipio = str(registro.get("Município", "") or "").strip().casefold()
    if not identificador:
        raise ValueError("Logger sem identificador ou endereço cadastrado.")
    return hashlib.sha256(f"{municipio}|{identificador.casefold()}".encode("utf-8")).hexdigest()[:28]


def rotulo_logger(registro):
    valor = str(registro.get("Identificação", "") or registro.get("ID Logger", "") or "").strip()
    return valor or str(registro.get("Nome", "") or "Logger sem identificação")


def filtrar_loggers(ativos):
    if ativos is None or ativos.empty or "Tipo" not in ativos:
        return []
    unicos = {}
    for registro in ativos.loc[ativos["Tipo"].astype(str).str.casefold() == "logger"].to_dict("records"):
        unicos.setdefault(chave_logger(registro), registro)
    return list(unicos.values())


@st.cache_resource(show_spinner=False)
def _cliente():
    config = st.secrets["gcp_json"]
    config = json.loads(config) if isinstance(config, str) else dict(config)
    creds = Credentials.from_service_account_info(
        config, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


@st.cache_resource(ttl=300, show_spinner=False)
def _planilha(id_planilha):
    return _cliente().open_by_key(id_planilha)


def _aba(id_planilha, criar=False):
    planilha = _planilha(id_planilha)
    try:
        aba = planilha.worksheet(ABA)
    except gspread.WorksheetNotFound:
        if not criar:
            return None
        aba = planilha.add_worksheet(title=ABA, rows=100, cols=12)
        aba.update(range_name="A1:E1", values=[CABECALHO], value_input_option="RAW")
    return aba


def _linhas(id_planilha, criar=False):
    aba = _aba(id_planilha, criar=criar)
    return aba, (aba.get_all_values() if aba is not None else [])


def _limpar_expirados(id_planilha, aba, linhas, hoje):
    """Mantém apenas registros do dia de Fortaleza. Sem histórico."""
    if aba is None or not linhas:
        return linhas
    if not any(linha and linha[0] != hoje for linha in linhas[1:]):
        return linhas
    validos = [linha for linha in linhas[1:] if linha and linha[0] == hoje]
    aba.clear()
    aba.update(range_name="A1", values=[CABECALHO] + validos, value_input_option="RAW")
    return [CABECALHO] + validos


@st.cache_data(ttl=25, show_spinner=False, max_entries=6)
def indice_do_dia(id_planilha, hoje):
    """Cache curto compartilhado por sessões; nunca retorna imagens vencidas."""
    aba, linhas = _linhas(id_planilha)
    linhas = _limpar_expirados(id_planilha, aba, linhas, hoje)
    registros = {}
    for linha in linhas[1:]:
        if len(linha) < 5 or linha[0] != hoje or not linha[1]:
            continue
        dados_b64 = "".join(linha[4:])
        if not dados_b64:
            continue
        try:
            imagem = base64.b64decode(dados_b64, validate=True)
        except Exception:
            continue
        registros[linha[1]] = {
            "logger": linha[2],
            "atualizado_em": linha[3],
            "imagem": imagem,
        }
    return registros


def imagem_do_logger(id_planilha, registro, indice=None):
    indice = indice if indice is not None else indice_do_dia(id_planilha, data_hoje())
    return indice.get(chave_logger(registro))


def _normalizar_imagem(dados):
    if not dados or len(dados) > TAMANHO_MAX_UPLOAD:
        raise ValueError("Cole uma imagem PNG ou JPG de até 6 MB.")
    try:
        with PILImage.open(io.BytesIO(dados)) as original:
            if original.format not in {"PNG", "JPEG"}:
                raise ValueError("Formato inválido: utilize PNG ou JPG.")
            if original.width * original.height > MAX_PIXELS:
                raise ValueError("A imagem possui resolução excessiva.")
            imagem = ImageOps.exif_transpose(original)
            imagem.thumbnail((1900, 1450), resample=PILImage.Resampling.LANCZOS)
            # Fundo branco evita quadriculados ou fundo preto com PNG transparente.
            if imagem.mode in {"RGBA", "LA", "P"}:
                fundo = PILImage.new("RGB", imagem.size, "white")
                if imagem.mode == "P":
                    imagem = imagem.convert("RGBA")
                fundo.paste(imagem, mask=imagem.getchannel("A") if "A" in imagem.getbands() else None)
                imagem = fundo
            else:
                imagem = imagem.convert("RGB")
            saida = io.BytesIO()
            imagem.save(saida, format="JPEG", quality=91, optimize=True, subsampling=0)
            normalizados = saida.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Não foi possível abrir a imagem colada.") from exc
    if math.ceil(len(base64.b64encode(normalizados)) / TAMANHO_PARTE) > MAX_PARTES:
        raise ValueError("Imagem grande demais para a base temporária após otimização.")
    return normalizados


def salvar_grafico(id_planilha, registro, imagem_original):
    """Substitui o gráfico do dia para o logger em aba isolada, sem tocar PONTOS/EVENTOS."""
    imagem = _normalizar_imagem(imagem_original)
    hoje = data_hoje()
    chave = chave_logger(registro)
    data_hora = datetime.now(FUSO).strftime("%d/%m/%Y %H:%M")
    conteudo = base64.b64encode(imagem).decode("ascii")
    partes = [conteudo[i:i + TAMANHO_PARTE] for i in range(0, len(conteudo), TAMANHO_PARTE)]
    nova = [hoje, chave, rotulo_logger(registro), data_hora] + partes
    aba, linhas = _linhas(id_planilha, criar=True)
    linhas = _limpar_expirados(id_planilha, aba, linhas, hoje)
    colunas = max(aba.col_count, len(nova), 5)
    if colunas > aba.col_count:
        aba.resize(cols=colunas)
    correspondencias = [
        (linha_num, antiga)
        for linha_num, antiga in enumerate(linhas[1:], start=2)
        if len(antiga) > 1 and antiga[0] == hoje and antiga[1] == chave
    ]
    if correspondencias:
        # O índice usa a última linha do logger; atualizá-la garante que a
        # substituição apareça mesmo se uploads simultâneos geraram duplicatas.
        linha_num, antiga = correspondencias[-1]
        tam = max(len(nova), len(antiga))
        aba.update(range_name=f"A{linha_num}", values=[nova + [""] * (tam - len(nova))],
                   value_input_option="RAW")
        # Elimina duplicatas antigas deste logger sem interferir em outros.
        for duplicata_num, _ in reversed(correspondencias[:-1]):
            aba.delete_rows(duplicata_num)
        indice_do_dia.clear()
        return "substituído"
    aba.append_row(nova, value_input_option="RAW")
    indice_do_dia.clear()
    return "enviado"


def excluir_grafico(id_planilha, registro, imagem_esperada=None):
    """Exclui a captura do logger no dia atual, incluindo eventuais duplicatas.

    A exclusão se restringe à aba temporária de gráficos; outras bases
    operacionais nunca são modificadas. A comparação opcional com a imagem
    que o usuário visualizou impede excluir acidentalmente uma atualização
    recente feita por outro analista.
    """
    hoje = data_hoje()
    chave = chave_logger(registro)
    aba, linhas = _linhas(id_planilha, criar=False)
    if aba is None:
        return False

    linhas = _limpar_expirados(id_planilha, aba, linhas, hoje)
    numeros_linha = [
        n for n, linha in enumerate(linhas[1:], start=2)
        if len(linha) >= 2 and linha[0] == hoje and linha[1] == chave
    ]
    if not numeros_linha:
        indice_do_dia.clear()
        return False

    if imagem_esperada is not None:
        # O índice do dia mantém a última entrada quando houver duplicatas.
        ultima = linhas[numeros_linha[-1] - 1]
        try:
            imagem_atual = base64.b64decode("".join(ultima[4:]), validate=True)
        except Exception as exc:
            raise ValueError(
                "A imagem armazenada está inválida. Atualize a página antes de excluí-la."
            ) from exc
        if hashlib.sha256(imagem_atual).digest() != hashlib.sha256(imagem_esperada).digest():
            indice_do_dia.clear()
            raise ValueError(
                "Outro analista atualizou este gráfico. Recarregue a página "
                "e confira a imagem antes de confirmar a exclusão."
            )

    # Excluir de baixo para cima mantém estáveis os números das linhas restantes.
    for numero in reversed(numeros_linha):
        aba.delete_rows(numero)
    indice_do_dia.clear()
    return True


def status(id_planilha):
    """Diagnóstico sem gravação, para mostrar ao usuário a fonte efetiva."""
    try:
        _planilha(id_planilha)
        return True, "Aba de gráficos compartilhados pronta para uso (criada no primeiro upload)."
    except Exception as exc:
        return False, str(exc)
