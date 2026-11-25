"""Agrupamento responsivo de medições do mapa de Baixa Pressão (Módulo 1).

A categoria de um grupo é a MAIS CRÍTICA entre suas medições, jamais a média.
O mapa individual mantém os marcadores e popups originais; este arquivo contém
somente prioridades e a função visual do Leaflet.markercluster.
"""
from __future__ import annotations

import math

# Mesmas faixas já adotadas no Módulo 1, da melhor para a mais crítica.
# 0 é um caso especial; não misturar zero com outras pressões na média.
CORES_FAIXAS = {
    1: "#22C55E",  # alta, > 15
    2: "#2563EB",  # normal, 10 a 15
    3: "#D946EF",  # atenção, >5 e <10
    4: "#F59E0B",  # baixa, >0 e <=5
    5: "#EF4444",  # sem pressão, zero
}
ZOOM_ROTULOS_INDIVIDUAIS = 15
RAIO_CLUSTER_PX = 65


def prioridade_pressao_mca(valor: float) -> int:
    """Classifica valor já normalizado conforme as faixas existentes no M1."""
    pressao = float(valor)
    if not math.isfinite(pressao) or pressao < 0:
        raise ValueError("Pressão inválida para o agrupamento.")
    if pressao == 0:
        return 5
    if pressao <= 5:
        return 4
    if pressao < 10:
        return 3
    if pressao <= 15:
        return 2
    return 1


# MarkerCluster recebe a função em JavaScript; somente inteiros de prioridade
# são transferidos via marker.options.bp_pressure_rank. Os nomes, endereços
# e dados dos consumidores nunca entram no HTML dos agrupamentos.
FUNCAO_ICONE_CLUSTER = r"""
function (cluster) {
    var filhos = cluster.getAllChildMarkers();
    var piorFaixa = 1;
    var semPressao = 0;
    var baixaPressao = 0;
    for (var i = 0; i < filhos.length; i++) {
        var valor = Number(filhos[i].options.bp_pressure_rank || 1);
        if (!Number.isFinite(valor) || valor < 1 || valor > 5) valor = 1;
        piorFaixa = Math.max(piorFaixa, valor);
        if (valor === 5) semPressao++;
        else if (valor === 4) baixaPressao++;
    }
    var cores = {
        1: "#22C55E", 2: "#2563EB", 3: "#D946EF",
        4: "#F59E0B", 5: "#EF4444"
    };
    var nomes = {
        1: "Alta pressão", 2: "Normal", 3: "Em atenção",
        4: "Baixa pressão", 5: "Sem pressão"
    };
    var total = cluster.getChildCount();
    var tamanho = total >= 100 ? 54 : total >= 20 ? 48 : 42;
    var texto = total + " medições; pior faixa: " + nomes[piorFaixa] +
        "; 0 MCA: " + semPressao + "; baixa pressão: " + baixaPressao +
        ". Clique para aproximar.";
    var html = '<span class="bp-cluster-badge" style="--bp-cluster-color:' +
        cores[piorFaixa] + '" title="' + texto + '">' +
        '<span class="bp-cluster-numero">' + total + '</span></span>';
    return L.divIcon({
        html: html,
        className: "bp-cluster-icon",
        iconSize: L.point(tamanho, tamanho),
        iconAnchor: L.point(tamanho / 2, tamanho / 2)
    });
}
"""


def opcoes_cluster():
    """Opções JS Leaflet (camelCase), com expansão e spiderfy nativos."""
    return {
        "maxClusterRadius": RAIO_CLUSTER_PX,
        "zoomToBoundsOnClick": True,
        "spiderfyOnMaxZoom": True,
        "spiderfyDistanceMultiplier": 1.8,
        "showCoverageOnHover": False,
        "removeOutsideVisibleBounds": True,
        "chunkedLoading": True,
        "animate": True,
    }
