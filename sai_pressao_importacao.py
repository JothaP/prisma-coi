"""Regras únicas para importação de medições de pressão no SAI.

Usado tanto pelo upload manual no Módulo 1 quanto pelo GitHub Actions.
Sem dependência do Streamlit, Google Sheets ou credenciais.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import io
import math
from pathlib import Path
import re
import unicodedata
import uuid

import pandas as pd

COLUNAS_SAI = [
    "ID", "Data", "Municipio", "Bairro", "Latitude", "Longitude",
    "Pressao_MCA", "Observacao", "Matricula",
]
TERMOS_INTERVENCAO = ("vazamento", "obstrucao", "entupido", "entupimento", "quebrado", "quebrada")
MARCADOR_ATIVIDADE = "SAI_ATIVIDADE="


def texto_limpo(valor: object) -> str:
    if valor is None:
        return ""
    try:
        if bool(pd.isna(valor)):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(valor).strip()
    return "" if s.lower() in ("nan", "none", "nat", "<na>") else s


def sem_acentos(valor: object) -> str:
    s = unicodedata.normalize("NFKD", texto_limpo(valor))
    return "".join(c for c in s if not unicodedata.combining(c))


def normalizar_cabecalho(valor: object) -> str:
    s = sem_acentos(valor).casefold()
    return re.sub(r"\s+", " ", s).strip()


def encontrar_coluna(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    mapa = {normalizar_cabecalho(c): c for c in df.columns}
    for opcao in opcoes:
        val = mapa.get(normalizar_cabecalho(opcao))
        if val is not None:
            return val
    return None


def normalizar_localidade(valor: object, padrao: str = "") -> str:
    return sem_acentos(texto_limpo(valor) or padrao).upper().strip()


def numero(valor: object, permitir_negativo: bool = False) -> float | None:
    """Aceita número simples ou número explicitamente acompanhado de MCA."""
    s = texto_limpo(valor).replace(",", ".")
    if not s:
        return None
    s = re.sub(r"\s*(?:mca|m\.c\.a\.)\s*$", "", s, flags=re.IGNORECASE).strip()
    try:
        n = float(s)
    except (ValueError, TypeError, OverflowError):
        return None
    return n if math.isfinite(n) and (n >= 0 or permitir_negativo) else None


def parse_data(valor: object) -> str | None:
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return valor.strftime("%d/%m/%Y")
    s = texto_limpo(valor)
    if not s:
        return None
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, formato).strftime("%d/%m/%Y")
        except ValueError:
            continue
    # Horário em formato ISO da exportação do sistema.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return None


def data_pelo_nome(nome: str) -> str | None:
    # Ex.: Atividades-ÁGUAS DE TERESINA_01_11_26.xlsx
    # Ancorar o sufixo para não capturar carimbo yyyyMMdd_HHmmss do Power Automate.
    limpo = Path(nome).stem
    match = re.search(r"(?<!\d)(\d{2})[_-](\d{2})[_-](\d{2,4})(?!\d)", limpo)
    return parse_data("/".join(match.groups())) if match else None


MCA_REGEX = re.compile(r"(?<![\w\d])(?P<valor>\d{1,4}(?:[.,]\d{1,2})?)\s*(?:mca|m\.c\.a\.?)\b", re.IGNORECASE)
PRESSAO_REGEX = re.compile(
    r"press[aã]o(?:\s+(?:da|de)\s+(?:rede|[áa]gua))?\s*[:=\-]?\s*(?P<valor>\d{1,3}(?:[.,]\d{1,2})?)\b",
    re.IGNORECASE,
)


def medicoes_do_parecer(parecer: str) -> list[float]:
    """Identifica leituras explícitas, sem capturar protocolos, imóveis ou datas."""
    valores = []
    for padrao in (MCA_REGEX, PRESSAO_REGEX):
        for match in padrao.finditer(texto_limpo(parecer)):
            p = numero(match.group("valor"))
            if p is not None:
                valores.append(p)
    return valores


def analisar_parecer(row: pd.Series, col_pressao: str | None, col_parecer: str | None):
    """Retorna uma única medição por atividade, sempre com evidência numérica.

    Nos pareceres que mencionam intervenção (vazamento/obstrução/etc.),
    seleciona a MAIOR medição explícita entre campo estruturado e parecer.
    Nos demais, prioriza o valor estruturado, recorrendo ao parecer.
    """
    p_estruturada = numero(row.get(col_pressao)) if col_pressao else None
    parecer = texto_limpo(row.get(col_parecer)) if col_parecer else ""
    medidas_parecer = medicoes_do_parecer(parecer)
    tem_intervencao = any(termo in sem_acentos(parecer).lower() for termo in TERMOS_INTERVENCAO)
    if tem_intervencao:
        todas = ([p_estruturada] if p_estruturada is not None else []) + medidas_parecer
        return max(todas) if todas else None
    return p_estruturada if p_estruturada is not None else (medidas_parecer[0] if medidas_parecer else None)


def id_atividade_da_observacao(obs: object) -> str:
    texto = texto_limpo(obs)
    m = re.search(r"(?:^|\s|\|)SAI_ATIVIDADE=([^\s|;]+)", texto)
    return m.group(1) if m else ""


def normalizar_id_atividade(val: object) -> str:
    s = texto_limpo(val).replace("|", "_").replace(";", "_")
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    # Valores muito longos ou com quebra de linha não são marcadores seguros.
    return re.sub(r"\s+", "_", s)[:120]


@dataclass
class Resultado:
    origem: int
    linhas: list[list]
    sem_pressao_numerica: int = 0
    sem_coordenadas: int = 0
    sem_data: int = 0
    excluidas_ou_fora_da_faixa: int = 0


def converter_bruta(df: pd.DataFrame, nome_arquivo: str) -> Resultado:
    """Importa planilha bruta ou modelo SAI, preservando cada medição distinta.

    Nenhum texto 'baixa pressão' é convertido em 0: zero precisa ser medido.
    Não aplica corte 10–15 MCA: toda medição numérica não negativa é válida.
    """
    col_mun = encontrar_coluna(df, ["Cidade", "Município", "Municipio"])
    col_bairro = encontrar_coluna(df, ["Bairro"])
    col_lat = encontrar_coluna(df, ["Latitude", "Lat", "COD_LATITUDE"])
    col_lon = encontrar_coluna(df, ["Longitude", "Lon", "Long", "COD_LONGITUDE"])
    col_pressao = encontrar_coluna(df, ["Qual A Pressão Da Rede (Mca)", "Pressao_MCA", "Pressão (MCA)", "Pressão"])
    col_parecer = encontrar_coluna(df, ["Parecer De Campo", "Parecer de Campo"])
    col_obs = encontrar_coluna(df, ["Observacao", "Observação"])
    col_matricula = encontrar_coluna(df, [
        "Matrícula", "Matricula", "Matrícula do Imóvel", "Matricula do Imovel",
        "Nr Matricula", "Número da Matrícula", "Numero da Matricula", "Matrícula do Cliente",
    ])
    col_data = encontrar_coluna(df, ["Data", "Data da atividade", "Data da Atividade"])
    col_atividade = encontrar_coluna(df, ["ID da Atividade", "ID da atividade", "ID Atividade"])
    col_protocolo = encontrar_coluna(df, ["Cód. Protocolo Origem", "Cod. Protocolo Origem", "Protocolo Origem"])
    if not col_bairro or not col_lat or not col_lon or not (col_pressao or col_parecer):
        raise ValueError("Colunas obrigatórias ausentes: Bairro, Latitude, Longitude, Pressao_MCA ou Parecer De Campo.")
    fallback_data = data_pelo_nome(nome_arquivo)
    resultado = Resultado(origem=len(df), linhas=[])
    for _, row in df.iterrows():
        bairro = texto_limpo(row.get(col_bairro))
        if not bairro:
            resultado.excluidas_ou_fora_da_faixa += 1
            continue
        pressao = analisar_parecer(row, col_pressao, col_parecer)
        if pressao is None:
            resultado.sem_pressao_numerica += 1
            continue
        lat = numero(row.get(col_lat), permitir_negativo=True)
        lon = numero(row.get(col_lon), permitir_negativo=True)
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or lat == 0 or lon == 0:
            resultado.sem_coordenadas += 1
            continue
        data = (parse_data(row.get(col_data)) if col_data else None) or fallback_data
        if data is None:
            resultado.sem_data += 1
            continue
        mun = normalizar_localidade(row.get(col_mun), "TERESINA") if col_mun else "TERESINA"
        mat = texto_limpo(row.get(col_matricula)) if col_matricula else ""
        # A exportação bruta contém observações que podem trazer telefones e
        # dados pessoais; não copiamos esse campo para o mapa operacional.
        # No modelo SAI, o campo Observacao é preservado normalmente.
        obs = (
            texto_limpo(row.get(col_obs))
            if col_obs and not col_parecer else ""
        )
        atividade = normalizar_id_atividade(row.get(col_atividade)) if col_atividade else ""
        # A matrícula/protocolo não é ID único de atividade: pode haver medições distintas.
        _ = col_protocolo
        if atividade and not id_atividade_da_observacao(obs):
            obs = (obs + " | " if obs else "") + MARCADOR_ATIVIDADE + atividade
        resultado.linhas.append([
            uuid.uuid4().hex[:8].upper(), data, mun, normalizar_localidade(bairro),
            round(lat, 6), round(lon, 6), round(pressao, 2), obs, mat,
        ])
    if not resultado.linhas and resultado.sem_data:
        raise ValueError("Planilha sem data válida nos registros ou nome do arquivo; não foi importada.")
    return resultado


def ler_arquivo(nome: str, conteudo: bytes) -> pd.DataFrame:
    """Evita erro 'No such keys(s): io.excel.zip.reader' na leitura de XLSX."""
    extensao = Path(nome).suffix.lower()
    if extensao == ".xlsx":
        return pd.read_excel(io.BytesIO(conteudo), dtype=str, engine="openpyxl")
    if extensao == ".csv":
        for codificacao in ("utf-8-sig", "cp1252", "latin1"):
            try:
                return pd.read_csv(io.BytesIO(conteudo), sep=None, engine="python", dtype=str, encoding=codificacao)
            except UnicodeDecodeError:
                continue
    raise ValueError("Formato não aceito. Utilize arquivos XLSX ou CSV.")


def chave_secundaria(linha: list) -> tuple[str, ...]:
    """Identificação de conteúdo usada apenas quando faltar ID de atividade."""
    return (
        "conteudo",
        parse_data(linha[1]) or texto_limpo(linha[1]),
        normalizar_localidade(linha[2]),
        normalizar_localidade(linha[3]),
        f"{float(str(linha[4]).replace(',', '.')):.6f}",
        f"{float(str(linha[5]).replace(',', '.')):.6f}",
        f"{float(str(linha[6]).replace(',', '.')):.2f}",
        normalizar_localidade(linha[8]) if len(linha) > 8 else "",
    )


def chave_medicao(linha: list) -> tuple[str, ...]:
    """Prioriza atividade identificada para não descartar aferições distintas."""
    if len(linha) > 7:
        atividade = id_atividade_da_observacao(linha[7])
        if atividade:
            return ("atividade", atividade)
    return chave_secundaria(linha)


def filtrar_ja_existentes(linhas: list[list], linhas_sheet: list[list]) -> tuple[list[list], int]:
    existentes = set()
    for r in linhas_sheet:
        if len(r) >= 9:
            try:
                existentes.add(chave_medicao(r))
            except (TypeError, ValueError):
                pass
    novos, repetidos = [], 0
    for r in linhas:
        chave = chave_medicao(r)
        if chave in existentes:
            repetidos += 1
        else:
            novos.append(r)
            existentes.add(chave)
    return novos, repetidos


class IndiceDuplicidades:
    """Evita reprocessar atividades e compara legados sem ID por conteúdo.

    Para dados legados (antes do armazenamento de ID da atividade), cada linha
    histórica pode absorver apenas UMA correspondência. Assim, dois IDs de
    atividade distintos não desaparecem só por terem pressão/local iguais.
    """
    def __init__(self, linhas_existentes=()):
        from collections import Counter
        self.ids = set()
        self.legadas = Counter()
        self.legadas_consumidas = Counter()
        self.com_id_por_conteudo = set()
        self.novas_sem_id = set()
        for row in linhas_existentes:
            if len(row) < 7 or not any(str(x).strip() for x in row):
                continue
            row = list(row) + [""] * max(0, 9 - len(row))
            try:
                secundaria = chave_secundaria(row)
                aid = id_atividade_da_observacao(row[7])
                if aid:
                    self.ids.add(aid)
                    self.com_id_por_conteudo.add(secundaria)
                else:
                    self.legadas[secundaria] += 1
            except (ValueError, TypeError):
                continue

    def selecionar(self, linhas: list[list]) -> tuple[list[list], int, int]:
        novas, repetidas, legadas_combinadas = [], 0, 0
        for row in linhas:
            secundaria = chave_secundaria(row)
            aid = id_atividade_da_observacao(row[7])
            if aid:
                if aid in self.ids:
                    repetidas += 1
                    continue
                if self.legadas_consumidas[secundaria] < self.legadas[secundaria]:
                    self.legadas_consumidas[secundaria] += 1
                    self.ids.add(aid)
                    legadas_combinadas += 1
                    repetidas += 1
                    continue
                self.ids.add(aid)
                self.com_id_por_conteudo.add(secundaria)
                novas.append(row)
            else:
                if self.legadas_consumidas[secundaria] < self.legadas[secundaria]:
                    self.legadas_consumidas[secundaria] += 1
                    repetidas += 1
                elif secundaria in self.novas_sem_id or secundaria in self.com_id_por_conteudo:
                    repetidas += 1
                else:
                    self.novas_sem_id.add(secundaria)
                    novas.append(row)
        return novas, repetidas, legadas_combinadas
