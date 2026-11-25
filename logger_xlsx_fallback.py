"""Leitor de contingência XLSX para exportações com metadados incompatíveis.

Não modifica arquivos de origem. Tenta extrair somente valores das células
da primeira aba de dados, sem confiar em estilos/fontes do Excel.
Usado somente após falha do leitor convencional.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd


NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MAX_XML_BYTES = 180 * 1024 * 1024
MAX_ROWS = 600_000
MAX_COLS = 200


def _indice_coluna(ref):
    letras = re.match(r"^[A-Z]+", str(ref).upper())
    if not letras:
        return None
    indice = 0
    for letra in letras.group(0):
        indice = indice * 26 + ord(letra) - ord("A") + 1
    return indice - 1


def _texto_celula(celula, strings):
    tipo = celula.attrib.get("t", "")
    if tipo == "inlineStr":
        return "".join(t.text or "" for t in celula.iter(NS + "t"))
    node = celula.find(NS + "v")
    if node is None:
        return ""
    valor = node.text or ""
    if tipo == "s":
        try:
            n = int(valor)
            return strings[n] if 0 <= n < len(strings) else ""
        except (ValueError, OverflowError):
            return ""  # sharedStrings com referência danificada
    return valor


def ler_xlsx_xml(conteudo):
    """Recupera dados de exportações incompatíveis com openpyxl.

    O leitor de contingência não avalia fórmulas, nem infere datas de estilos.
    Datas armazenadas como número de série são tratadas depois pelo núcleo.
    """
    try:
        arquivo = zipfile.ZipFile(io.BytesIO(conteudo))
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        raise ValueError("O arquivo não contém uma estrutura XLSX válida.") from exc
    with arquivo:
        nomes = set(arquivo.namelist())
        shared = []
        if "xl/sharedStrings.xml" in nomes:
            membro = arquivo.getinfo("xl/sharedStrings.xml")
            if membro.file_size > MAX_XML_BYTES:
                raise ValueError("Tabela de textos da planilha excede o limite.")
            with arquivo.open(membro) as stream:
                tree = ET.parse(stream)
            for item in tree.getroot().iter(NS + "si"):
                shared.append("".join(
                    t.text or "" for t in item.iter(NS + "t")
                ))
        abas = sorted(
            (nome for nome in nomes if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", nome)),
            key=lambda nome: int(re.search(r"sheet(\d+)\.xml", nome).group(1)),
        )
        if not abas:
            raise ValueError("Nenhuma aba de dados encontrada no XLSX.")
        for caminho in abas:
            membro = arquivo.getinfo(caminho)
            if membro.file_size > MAX_XML_BYTES:
                raise ValueError("A planilha excede o limite de leitura segura.")
            with arquivo.open(membro) as stream:
                tree = ET.parse(stream)
            linhas = []
            cabecalhos = None
            for node in tree.getroot().iter(NS + "row"):
                celulas = {}
                for celula in node.findall(NS + "c"):
                    n = _indice_coluna(celula.attrib.get("r"))
                    if n is None or n >= MAX_COLS:
                        continue
                    celulas[n] = _texto_celula(celula, shared)
                if not celulas or not any(str(v).strip() for v in celulas.values()):
                    continue
                if cabecalhos is None:
                    cabecalhos = [
                        str(celulas.get(i, "")).strip() or f"Coluna_{i+1}"
                        for i in range(max(celulas) + 1)
                    ]
                    continue
                if len(linhas) >= MAX_ROWS:
                    raise ValueError("Arquivo com mais de 600 mil linhas; divida a exportação.")
                linhas.append({
                    cabecalhos[i]: valor for i, valor in celulas.items()
                    if i < len(cabecalhos)
                })
            if cabecalhos:
                df = pd.DataFrame(linhas, columns=cabecalhos)
                # Escolhe a primeira aba que parece conter loggers.
                colunas = [re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in df.columns]
                if any("galaxia" in c or c == "idlogger" for c in colunas):
                    return df
        raise ValueError("Nenhuma aba contém cabeçalhos de loggers reconhecíveis.")
