"""Pesquisa por plantas/loggers e preparação dos pontos cadastrais do Módulo 8.

Funções puras: não acessam arquivos, autenticação, banco ou serviço externo.
O módulo Streamlit decide quando ler os cadastros sob demanda.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd


LIMITE_RESULTADOS_EQUIPAMENTOS = 40
LIMITE_PONTOS_POLIGONO = 2500


def _normalizar(valor: object) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().upper())
    return re.sub(r"[^A-Z0-9]", "", "".join(
        c for c in texto if not unicodedata.combining(c)
    ))


def interpretar_termos_equipamentos(texto: str) -> list[str]:
    """Aceita um código ou vários separados por vírgula, ponto e vírgula/linha."""
    itens = [s.strip() for s in re.split(r"[,;\n]+", str(texto or "")) if s.strip()]
    if not itens:
        raise ValueError("Informe ao menos uma identificação de ativo ou logger.")
    if len(itens) > 20:
        raise ValueError("Pesquise no máximo 20 identificações por vez.")
    if any(len(s) > 100 or not _normalizar(s) for s in itens):
        raise ValueError("Uma das identificações está vazia ou excede 100 caracteres.")
    unicos = []
    vistos = set()
    for item in itens:
        chave = _normalizar(item)
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)
    return unicos


def buscar_equipamentos(bases: dict, tipo: str, termos: list[str],
                        limite: int = LIMITE_RESULTADOS_EQUIPAMENTOS):
    """Procura identificação e nome do cadastro do tipo solicitado.

    Prioriza identificação EXATA (ignorando hífens, espaços e acentos).
    Aceita início de código/nome com pelo menos 4 caracteres caso não
    haja correspondência exata. Não mistura o cadastro de outro tipo.
    """
    if tipo not in ("Poço / Ativo", "Logger"):
        raise ValueError("Tipo de equipamento desconhecido.")
    base = bases.get(tipo)
    if base is None or base.empty:
        return [], list(termos), 0
    campos = (
        ("IDENTIFICACAO_ATIVO", "ID_POCO", "NOME_POCO")
        if tipo == "Poço / Ativo" else
        ("IDENTIFICACAO_ATIVO", "ID_LOGGER")
    )
    indices = {
        nome: base[nome].map(_normalizar)
        for nome in campos if nome in base.columns
    }
    encontrados = []
    nao_encontrados = []
    ids_vistos = set()
    limite = max(1, int(limite))
    excedentes = 0
    for item in termos:
        termo = _normalizar(item)
        mask = pd.Series(False, index=base.index)
        for coluna in indices.values():
            mask |= coluna.eq(termo)
        if not mask.any() and len(termo) >= 4:
            for coluna in indices.values():
                mask |= coluna.str.startswith(termo, na=False)
        candidatos = base.loc[mask]
        if candidatos.empty:
            nao_encontrados.append(item)
        for registro in candidatos.to_dict("records"):
            chave = (
                str(registro.get("_tipo", tipo)),
                str(registro.get("IDENTIFICACAO_ATIVO", "")),
                str(registro.get("ID_POCO", registro.get("ID_LOGGER", ""))),
                str(registro.get("_lat", "")),
                str(registro.get("_lon", "")),
            )
            if chave in ids_vistos:
                continue
            ids_vistos.add(chave)
            if len(encontrados) < limite:
                encontrados.append(registro)
            else:
                excedentes += 1
    return encontrados, nao_encontrados, excedentes


def pontos_matriculas_poligono(
    cadastro_area: pd.DataFrame, converter_coordenada,
    limite: int = LIMITE_PONTOS_POLIGONO,
):
    """Retorna (pontos visualizáveis, total válidos) sem omitir silently dados.

    A seleção geométrica real já ocorreu na consulta SQL/SHAPELY; esta
    função apenas converte coordenadas, remove duplicatas e limita a
    quantidade de marcadores enviados ao Leaflet para proteger o navegador.
    A tabela/CSV permanecem com todas as matrículas da área.
    """
    campos = ["NUM_LIGACAO", "BAIRRO", "END_LIGACAO", "_lat", "_lon"]
    if cadastro_area is None or cadastro_area.empty:
        return pd.DataFrame(columns=campos), 0
    colunas = ("NUM_LIGACAO", "BAIRRO", "END_LIGACAO",
               "COD_LATITUDE", "COD_LONGITUDE")
    faltantes = [c for c in colunas if c not in cadastro_area.columns]
    if faltantes:
        raise ValueError("Cadastro da área incompleto: " + ", ".join(faltantes))
    pontos = cadastro_area.loc[:, colunas].copy()
    pontos["_lat"] = pontos["COD_LATITUDE"].map(
        lambda valor: converter_coordenada(valor, "lat")
    )
    pontos["_lon"] = pontos["COD_LONGITUDE"].map(
        lambda valor: converter_coordenada(valor, "lon")
    )
    pontos = pontos.dropna(subset=["_lat", "_lon"]).drop_duplicates(
        subset=["NUM_LIGACAO", "_lat", "_lon"],
        keep="first",
    )
    total = len(pontos)
    pontos = pontos.iloc[:max(1, int(limite))]
    return pontos.loc[:, campos].reset_index(drop=True), total
