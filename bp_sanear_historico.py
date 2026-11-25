"""Consulta historica de pressoes nas areas SANEAR — sem ordens de servico.

Funcoes puras e somente leitura. Geometria e periodo sao avaliados
sobre medicoes existentes no Modulo 1, inclusive historico arquivado.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd
from shapely.geometry import Point, shape
from shapely.prepared import prep


TODAS_AREAS = "Todas as áreas Sanear"


def opcoes_areas_sanear(geojson: dict) -> dict[str, tuple[int, ...]]:
    """Agrupa geometrias de mesma area, bacia e municipio sem perder partes."""
    grupos: dict[str, list[int]] = defaultdict(list)
    feicoes = geojson.get("features", [])
    for i, feicao in enumerate(feicoes):
        prop = feicao.get("properties") or {}
        nome = str(prop.get("Área") or "Não identificada").strip()
        bacia = str(prop.get("Bacia") or "Não informada").strip()
        municipio = str(prop.get("Município") or "Não informado").strip()
        chave = f"{nome} | Bacia: {bacia} | Município: {municipio}"
        grupos[chave].append(i)
    opcoes = {TODAS_AREAS: tuple(range(len(feicoes)))}
    opcoes.update({nome: tuple(indices) for nome, indices in sorted(grupos.items())})
    return opcoes


def filtrar_historico_sanear(
    medicoes: pd.DataFrame,
    geojson: dict,
    indices: tuple[int, ...],
    inicio: date,
    fim: date,
) -> pd.DataFrame:
    """Seleciona registros dentro/sobre poligonos, sem duplicar sobreposicoes.

    Datas inclusivas, coordenadas validas e furos respeitados. As coordenadas
    sao apenas das medicoes; o criterio nao e bairro nem raio aproximado.
    """
    obrigatorias = ("Data", "Latitude", "Longitude", "Pressao_MCA")
    if any(col not in medicoes.columns for col in obrigatorias):
        raise ValueError("A base de pressão não possui as colunas necessárias.")
    if inicio > fim:
        raise ValueError("A data inicial não pode ser posterior à data final.")

    vazia = medicoes.iloc[0:0].copy()
    vazia["DataObjSanear"] = pd.Series(dtype="datetime64[ns]")
    vazia["Área Sanear"] = pd.Series(dtype="str")
    if medicoes.empty or not indices:
        return vazia

    datas = pd.to_datetime(medicoes["Data"], format="%d/%m/%Y", errors="coerce")
    intervalo = datas.between(pd.Timestamp(inicio), pd.Timestamp(fim))
    base = medicoes.loc[intervalo].copy().reset_index(drop=True)
    if base.empty:
        return vazia
    base["DataObjSanear"] = pd.to_datetime(
        base["Data"], format="%d/%m/%Y", errors="coerce"
    )
    latitudes = pd.to_numeric(base["Latitude"], errors="coerce")
    longitudes = pd.to_numeric(base["Longitude"], errors="coerce")
    validas = (
        latitudes.between(-90, 90) & longitudes.between(-180, 180)
        & latitudes.notna() & longitudes.notna()
    )
    feicoes = geojson.get("features", [])
    associacoes: dict[int, set[str]] = defaultdict(set)

    for indice in dict.fromkeys(indices):
        if not (0 <= indice < len(feicoes)):
            continue
        feicao = feicoes[indice]
        try:
            poligono = shape(feicao.get("geometry") or {})
        except (TypeError, ValueError, KeyError):
            continue
        if poligono.is_empty or not poligono.is_valid:
            continue
        oeste, sul, leste, norte = poligono.bounds
        candidatas = base.index[
            validas
            & latitudes.between(sul, norte)
            & longitudes.between(oeste, leste)
        ]
        if len(candidatas) == 0:
            continue
        preparada = prep(poligono)
        nome = str((feicao.get("properties") or {}).get("Área") or "Não identificada")
        localizacoes_testadas = {}
        for pos in candidatas:
            lat, lon = float(latitudes.iat[pos]), float(longitudes.iat[pos])
            ponto = (lat, lon)
            if ponto not in localizacoes_testadas:
                localizacoes_testadas[ponto] = preparada.covers(Point(lon, lat))
            if localizacoes_testadas[ponto]:
                associacoes[pos].add(nome)

    if not associacoes:
        return vazia
    selecionadas = sorted(associacoes)
    resultado = base.loc[selecionadas].copy()
    resultado["Área Sanear"] = [
        "; ".join(sorted(associacoes[pos])) for pos in selecionadas
    ]
    return resultado.sort_values(
        ["DataObjSanear", "Área Sanear"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
