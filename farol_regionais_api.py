"""Classificação visual das concentrações API pelas regionais do Módulo 5.

Somente leitura da correspondência MUNICIPIO -> REGIONAL. Esta classificação
não muda os grupos, O.S., vínculos, cálculos, códigos ou fichas do Farol.
"""
from __future__ import annotations

import re
import unicodedata

# Mesma planilha de "Municípios, Bases e Regionais" do Módulo 5.
PLANILHA_CADASTROS_COI = "15iN3YEGyxk3l1ZKaHJJp-BvTfVHqpd7gL1GX3RbAKUU"
ABA_MUNICIPIOS_REGIONAIS = "municipios_regionais"
REGIONAL_NAO_CADASTRADA = "Regional não cadastrada"
MULTIPLAS_REGIONAIS = "Múltiplas regionais"


def chave_municipio(valor):
    """Compara municípios ignorando acentos, pontuação, caixa e espaços."""
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor).strip().upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", texto)


def mapa_regionais_de_linhas(linhas):
    """Lê a aba do Módulo 5 sem modificar os dados nem presumir regionais.

    MUNICIPIO e REGIONAL podem estar em posições diferentes no cabeçalho.
    Em município duplicado com regionais conflitantes, não escolhe
    arbitrariamente uma regional: deixa o município sem classificação.
    """
    if not linhas:
        return {}
    cabecalho = [chave_municipio(x) for x in linhas[0]]
    if "MUNICIPIO" not in cabecalho or "REGIONAL" not in cabecalho:
        raise ValueError(
            "A aba municipios_regionais deve conter MUNICIPIO e REGIONAL."
        )
    indice_municipio = cabecalho.index("MUNICIPIO")
    indice_regional = cabecalho.index("REGIONAL")
    mapa = {}
    ambiguos = set()
    for linha in linhas[1:]:
        if len(linha) <= max(indice_municipio, indice_regional):
            continue
        municipio = chave_municipio(linha[indice_municipio])
        regional = " ".join(str(linha[indice_regional] or "").strip().split())
        if not municipio or not regional or municipio in ambiguos:
            continue
        if municipio in mapa and chave_municipio(mapa[municipio]) != chave_municipio(regional):
            ambiguos.add(municipio)
            mapa.pop(municipio, None)
        else:
            mapa[municipio] = regional
    return mapa


def regional_da_concentracao_api(grupo_os, mapa_regionais):
    """Não inventa a regional quando uma concentração cruza municípios."""
    if grupo_os is None or grupo_os.empty or "Cidade" not in grupo_os:
        return REGIONAL_NAO_CADASTRADA
    cidades = {
        chave_municipio(cidade) for cidade in grupo_os["Cidade"]
        if chave_municipio(cidade)
    }
    if not cidades:
        return REGIONAL_NAO_CADASTRADA
    # Se uma das cidades não está cadastrada, classificar todas como
    # "não cadastrada" evita atribuir uma regional potencialmente falsa.
    if any(not mapa_regionais.get(cidade) for cidade in cidades):
        return REGIONAL_NAO_CADASTRADA
    regionais = {mapa_regionais[cidade] for cidade in cidades}
    if len({chave_municipio(r) for r in regionais}) > 1:
        return MULTIPLAS_REGIONAIS
    return sorted(regionais, key=lambda x: (chave_municipio(x), x))[0]


def ordenar_concentracoes_api_por_regional(concentracoes, grupos_os, mapa_regionais):
    """Retorna (regional, concentração), uma vez por concentração.

    Usa a ordem original dentro de cada regional e alfabética entre
    regionais, sem renumerar concentração nem tocar em qualquer DataFrame.
    """
    if concentracoes is None or concentracoes.empty:
        return []
    itens = []
    for pos, (_, concentracao) in enumerate(concentracoes.iterrows()):
        grupo = grupos_os.get(concentracao["GRUPO"])
        regional = regional_da_concentracao_api(grupo, mapa_regionais)
        prioridade = (
            2 if regional == REGIONAL_NAO_CADASTRADA else
            1 if regional == MULTIPLAS_REGIONAIS else 0
        )
        itens.append((prioridade, chave_municipio(regional), pos, regional, concentracao))
    itens.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(regional, concentracao) for _, _, _, regional, concentracao in itens]
