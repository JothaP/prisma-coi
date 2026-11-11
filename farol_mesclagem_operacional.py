"""Mesclagem operacional de concentrações por evento no Módulo 3.

Geometria desenhada pelo analista, filtro de evento confirmado por O.S. e
janela temporal, e uma ficha de acompanhamento por chave de evento estável.
Somente o backend Streamlit (service_role) pode gravar. Nunca apaga fichas.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import md5
from html import escape as html_escape
from zoneinfo import ZoneInfo
import json
import math
import re
import unicodedata

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import Point, shape, mapping

from farol_acomp_supabase_backend import _request, _autor, fonte_supabase

FUSO = ZoneInfo("America/Fortaleza")
CHAVE_MODO = "farol_mescla_modo"
CHAVE_POLIGONO = "farol_mescla_poligono"
CHAVE_EVENTO = "farol_mescla_evento_selecionado"


def normalizar(valor):
    s = unicodedata.normalize("NFKD", str(valor or "").upper().strip())
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))


def chave_evento(evento):
    """ID estável durante mudanças de status e áreas impactadas."""
    texto = "|".join(normalizar(evento.get(c, "")) for c in (
        "Cidade", "Protocolo", "Início", "Código do Ativo", "Serviço",
    ))
    return md5(texto.encode("utf-8")).hexdigest()


def chave_acompanhamento_mesclado(evento_chave):
    return md5(("FAROL_MESCLA|" + str(evento_chave)).encode()).hexdigest()


def validar_poligono(geometria):
    """Somente Polygon simples, área plausível e WGS84; sem aceitar desenhos livres."""
    if not isinstance(geometria, dict) or geometria.get("type") != "Polygon":
        raise ValueError("Desenhe um polígono fechado no mapa.")
    geo = shape(geometria)
    if geo.is_empty or not geo.is_valid or not geo.geom_type == "Polygon":
        raise ValueError("Polígono inválido ou com auto-intersecção.")
    if len(geo.exterior.coords) < 4 or len(geo.exterior.coords) > 2000:
        raise ValueError("O polígono deve conter entre 3 e 1999 vértices.")
    oeste, sul, leste, norte = geo.bounds
    if not (-180 <= oeste < leste <= 180 and -90 <= sul < norte <= 90):
        raise ValueError("Coordenadas geográficas inválidas.")
    # ~ 20 km por lado na latitude do Piauí; proteção contra seleção da cidade inteira.
    if geo.area <= 0 or geo.area > 0.05:
        raise ValueError("Polígono amplo demais para uma mesclagem operacional.")
    return mapping(geo)


def extrair_desenho_folium(desenho):
    if not isinstance(desenho, dict):
        return None
    geom = desenho.get("geometry", desenho)
    if not isinstance(geom, dict):
        return None
    if geom.get("type") != "Polygon":
        return None
    return validar_poligono(geom)


def contagem_no_poligono(poligono, concentracao, grupo_os):
    """Retorna quantidade de O.S. contidas e se centro está dentro."""
    if not poligono:
        return len(grupo_os), True
    area = shape(poligono)
    centro = Point(float(concentracao["LONGITUDE"]), float(concentracao["LATITUDE"]))
    dentro = 0
    for _, osr in grupo_os.iterrows():
        try:
            lat = float(osr.get("_lat", osr.get("Latitude")))
            lon = float(osr.get("_lon", osr.get("Longitude")))
            if math.isfinite(lat) and math.isfinite(lon) and area.covers(Point(lon, lat)):
                dentro += 1
        except (ValueError, TypeError, KeyError):
            continue
    return dentro, area.covers(centro)


@st.cache_data(ttl=30, max_entries=5, show_spinner=False)
def listar_mesclas():
    if not fonte_supabase():
        return []
    registros = _request(
        "GET", "/rest/v1/farol_mesclas_operacionais",
        params={
            "ativo": "eq.true",
            "select": "evento_chave,municipio,evento_rotulo,codigo_mescla,chave_acompanhamento,codigos_origem,os_origem,poligono,alterado_em",
            "limit": "1000",
        },
    )
    if not isinstance(registros, list):
        raise RuntimeError("Supabase retornou mesclagens inválidas.")
    return registros


def salvar_mescla(evento, codigos, os_numeros, chaves_origem, poligono=None):
    if not fonte_supabase():
        raise RuntimeError("Mesclagem requer Supabase habilitado no PRISMA.")
    if len(set(codigos)) < 2 or len(set(os_numeros)) < 6:
        raise ValueError("São necessárias pelo menos duas concentrações e seis O.S.")
    if poligono is not None:
        poligono = validar_poligono(poligono)
    municipio = str(evento.get("Cidade", "")).strip()
    if not municipio or not str(evento.get("Início", "")).strip():
        raise ValueError("Evento sem município ou início não pode ser mesclado.")
    chave = chave_evento(evento)
    unidade = ("THE" if normalizar(municipio) == "TERESINA"
               else "TIM" if normalizar(municipio).startswith("TIMON")
               else "API")
    codigo = f"{unidade} M{int(chave[:9], 16):06d}"
    titulo = f"{evento.get('Protocolo') or evento.get('Serviço') or 'Evento'} — {municipio}"
    autor = _autor("editar")
    resposta = _request(
        "POST", "/rest/v1/rpc/farol_salvar_mescla_tx",
        dados={
            "p_evento_chave": chave,
            "p_municipio": municipio,
            "p_evento_rotulo": str(titulo)[:240],
            "p_codigo": codigo,
            "p_chave_acomp": chave_acompanhamento_mesclado(chave),
            "p_codigos": sorted(set(str(v) for v in codigos)),
            "p_os": sorted(set(str(v) for v in os_numeros if str(v).strip())),
            "p_poligono": poligono,
            "p_chaves_origem": sorted(set(chaves_origem)),
            "p_autor": autor,
        },
    )
    if not isinstance(resposta, dict) or resposta.get("evento_chave") != chave:
        raise RuntimeError("Mesclagem não confirmada pelo Supabase.")
    listar_mesclas.clear()
    return resposta


def aplicar_mesclas(concentracoes, grupos, mesclas):
    """Produz uma linha consolidada e um grupo de O.S. sem tocar na base original.

    Grupos originais associados por O.S. persistentes, nunca pelo ID C001.
    Uma O.S. não pode integrar dois grupos mesclados no mesmo recorte.
    """
    if concentracoes is None or concentracoes.empty or grupos is None or grupos.empty or not mesclas:
        return concentracoes, grupos
    conc = concentracoes.copy()
    pontos = grupos.copy()
    pontos["_grupo"] = pontos["_grupo"].astype("object")
    novos = []
    ocupados = set()
    for mescla in mesclas:
        os_anteriores = {str(x).strip() for x in mescla.get("os_origem", [])}
        municipio = normalizar(mescla.get("municipio", ""))
        grupos_atuais = []
        for _, c in conc.iterrows():
            grupo = c["GRUPO"]
            sub = pontos[pontos["_grupo"] == grupo]
            cidades = {normalizar(x) for x in sub.get("Cidade", []) if str(x).strip()}
            identificadores = {str(x).strip() for x in sub.get("Nº da O.S", []) if str(x).strip()}
            if cidades == {municipio} and identificadores & os_anteriores:
                if grupo in ocupados:
                    grupos_atuais = []
                    break
                grupos_atuais.append(grupo)
        if not grupos_atuais:
            continue
        ocupados.update(grupos_atuais)
        bloco = pontos[pontos["_grupo"].isin(grupos_atuais)].copy()
        if bloco.empty:
            continue
        # A geometria é a área real, mas a marcação de centro no mapa é apenas
        # um ponto de referência visual, sem raio operacional fictício.
        def num(s):
            return pd.to_numeric(s, errors="coerce").mean()
        lat = num(bloco["_lat"] if "_lat" in bloco else bloco["Latitude"])
        lon = num(bloco["_lon"] if "_lon" in bloco else bloco["Longitude"])
        nomes = {str(x).strip() for x in bloco.get("Bairro", []) if str(x).strip()}
        gid = "MESCLA_" + mescla["evento_chave"]
        pontos.loc[pontos["_grupo"].isin(grupos_atuais), "_grupo"] = gid
        novos.append({
            "ID_CONCENTRACAO": mescla["codigo_mescla"],
            "GRUPO": gid, "LATITUDE": float(lat), "LONGITUDE": float(lon),
            "QTD_OS": len(bloco),
            "QTD_MATRICULAS": bloco["Matrícula"].replace("", pd.NA).dropna().nunique(),
            "BAIRROS": ", ".join(sorted(nomes)),
            "CHAVE_MESCLA": mescla["chave_acompanhamento"],
            "AREA_MESCLADA": mescla.get("poligono"),
            "EVENTO_MESCLA": mescla["evento_rotulo"],
            "CODIGOS_ORIGEM": mescla.get("codigos_origem", []),
        })
    if not novos:
        return concentracoes, grupos
    conc = conc[~conc["GRUPO"].isin(ocupados)].copy()
    conc = pd.concat([conc, pd.DataFrame(novos)], ignore_index=True)
    conc = conc.sort_values(
        ["QTD_OS", "QTD_MATRICULAS"], ascending=False, kind="stable"
    ).reset_index(drop=True)
    return conc, pontos


def desenhar_areas_mescladas(mapa, concentracoes):
    if concentracoes is None or concentracoes.empty:
        return
    for _, c in concentracoes.iterrows():
        geom = c.get("AREA_MESCLADA")
        if not isinstance(geom, dict):
            continue
        folium.GeoJson(
            geom, name=f"Mesclagem {c['ID_CONCENTRACAO']}",
            style_function=lambda _: {
                "color": "#8b5cf6", "weight": 3,
                "fillColor": "#a78bfa", "fillOpacity": 0.12,
            },
            tooltip=f"Concentração mesclada: {html_escape(str(c['ID_CONCENTRACAO']))}",
        ).add_to(mapa)


def adicionar_desenho_mapa(mapa, poligono=None):
    """Ferramenta de desenho disponível somente no modo Mesclar."""
    if poligono:
        folium.GeoJson(
            poligono, name="Área em preparação",
            style_function=lambda _: {
                "color": "#16a34a", "weight": 3,
                "fillColor": "#22c55e", "fillOpacity": 0.13,
            }, tooltip="Área desenhada para mesclagem",
        ).add_to(mapa)
    Draw(
        export=False, position="topleft",
        draw_options={
            "polygon": {"allowIntersection": False, "showArea": True},
            "rectangle": False, "polyline": False, "circle": False,
            "marker": False, "circlemarker": False,
        },
        edit_options={"edit": False, "remove": False},
    ).add_to(mapa)


def eventos_e_concentracoes(concentracoes, grupos, proximidades, eventos, bairros,
                            calcular, classificar):
    """Usa o MESMO vínculo espacial e temporal existente no Farol de produção."""
    resultado = {}
    if concentracoes.empty or eventos.empty:
        return resultado
    for _, c in concentracoes.iterrows():
        codigo = str(c["ID_CONCENTRACAO"])
        grupo, _, eventos_grupo = calcular(c, grupos, proximidades, eventos, bairros)
        if eventos_grupo.empty:
            continue
        cidades_grupo = {
            normalizar(valor) for valor in grupo.get("Cidade", []) if str(valor).strip()
        }
        # Grupos da API podem atravessar municípios; nunca mesclar um grupo
        # misto integralmente com um evento declarado para um só município.
        if len(cidades_grupo) != 1:
            continue
        for _, evt in eventos_grupo.iterrows():
            if normalizar(evt.get("Cidade", "")) not in cidades_grupo:
                continue
            vinculadas, _ = classificar(grupo, pd.DataFrame([evt]))
            if vinculadas.empty:
                continue
            chave = chave_evento(evt)
            item = resultado.setdefault(chave, {"evento": evt.to_dict(), "grupos": {}})
            item["grupos"][codigo] = {
                "concentracao": c.to_dict(),
                "total": len(grupo),
                "os_vinculadas": len(vinculadas),
                "numero_os_evento": sorted({str(x).strip() for x in vinculadas["Nº da O.S"]}),
            }
    return {k: v for k, v in resultado.items() if len(v["grupos"]) >= 2}


def renderizar_painel_mescla(concentracoes, pontos_cluster, proximidades, eventos,
                            bairros, calcular, classificar, contextos_acomp,
                            vinculos, fichas, pode_editar):
    st.markdown("#### 🔗 Mesclar concentrações de um mesmo evento")
    st.caption(
        "Selecione um evento e suas concentrações ou desenhe um polígono no panorama "
        "acima. A mesclagem usa uma única ficha, preservando vínculos e históricos "
        "originais. Sem mudança automática do raio operacional de 750 m."
    )
    if not fonte_supabase():
        st.warning("Mesclagem compartilhada requer acompanhamento no Supabase.")
        return
    indice = eventos_e_concentracoes(
        concentracoes, pontos_cluster, proximidades, eventos, bairros,
        calcular, classificar,
    )
    if not indice:
        st.info("Nenhum evento tem duas ou mais concentrações vinculadas, "
                "considerando município, território e horários das O.S.")
        return
    chaves = list(indice)
    def rotulo(chave):
        evento = indice[chave]["evento"]
        return (
            f"{evento.get('Protocolo') or evento.get('Serviço') or 'Evento'} "
            f"— {evento.get('Cidade', '')} — "
            f"{len(indice[chave]['grupos'])} concentrações"
        )
    event_key = st.selectbox(
        "Evento", chaves, format_func=rotulo,
        key=CHAVE_EVENTO,
    )
    escolhido = indice[event_key]
    grupo_candidatos = escolhido["grupos"]
    codigos = list(grupo_candidatos)
    poligono = st.session_state.get(CHAVE_POLIGONO)
    if poligono:
        selecionados_auto = []
        for cod, item in grupo_candidatos.items():
            cc = item["concentracao"]
            sub = pontos_cluster[pontos_cluster["_grupo"] == cc["GRUPO"]]
            dentro, centro = contagem_no_poligono(poligono, cc, sub)
            if centro and dentro:
                selecionados_auto.append(cod)
        st.caption(
            f"Polígono desenhado: {len(selecionados_auto)} concentração(ões) "
            "do evento com centro e O.S. dentro da área."
        )
        elegiveis = selecionados_auto
    else:
        elegiveis = codigos
        st.caption("Sem polígono: selecione manualmente as concentrações "
                   "relacionadas ao mesmo evento.")
    if len(elegiveis) < 2:
        st.warning(
            "Selecione ou desenhe uma área que contenha pelo menos duas "
            "concentrações vinculadas ao mesmo evento."
        )
        return
    st.dataframe(pd.DataFrame([
        {
            "Concentração": c, "Bairros": grupo_candidatos[c]["concentracao"]["BAIRROS"],
            "O.S. do grupo": grupo_candidatos[c]["total"],
            "O.S. vinculadas ao evento": grupo_candidatos[c]["os_vinculadas"],
        } for c in elegiveis
    ]), hide_index=True, use_container_width=True)
    chave_multi = f"farol_merge_grupos_{event_key}"
    if chave_multi in st.session_state:
        antigos = st.session_state[chave_multi]
        if not isinstance(antigos, list) or any(x not in elegiveis for x in antigos):
            del st.session_state[chave_multi]
    selecionadas = st.multiselect(
        "Concentrações para um único acompanhamento", elegiveis,
        default=elegiveis, key=chave_multi,
    )
    if len(selecionadas) < 2:
        return
    grupos_ids = {grupo_candidatos[c]["concentracao"]["GRUPO"] for c in selecionadas}
    os_unidas = pontos_cluster[pontos_cluster["_grupo"].isin(grupos_ids)]
    numeros = sorted({
        str(v).strip() for v in os_unidas["Nº da O.S"] if str(v).strip()
    })
    # Não ignorar fichas cujas concentrações mudaram de chave após
    # importações: localizar sobreposições consistentes nas O.S. históricas.
    conjunto_os = set(numeros)
    chaves_fichas_candidatas = {
        contextos_acomp[c]["chave"]
        for c in selecionadas if c in contextos_acomp
    }
    for chave_v, v in vinculos.items():
        if normalizar(v.get("municipio", "")) != normalizar(
                escolhido["evento"].get("Cidade", "")):
            continue
        anteriores = {
            x.strip() for x in str(v.get("os_origem", "")).split(";") if x.strip()
        }
        comum = anteriores & conjunto_os
        if (len(comum) >= 2 and len(comum)*2 >= len(anteriores)
                and len(comum)*2 >= len(conjunto_os)/len(selecionadas)):
            chaves_fichas_candidatas.add(chave_v)
    ids_existentes = sorted({
        str(vinculos[k]["evento_id"]) for k in chaves_fichas_candidatas
        if k in vinculos
    })
    st.caption(
        f"Total consolidado: {len(numeros)} O.S. distintas de "
        f"{len(selecionadas)} concentrações. "
        "O polígono é a abrangência visual; não representa um novo raio hidráulico."
    )
    if len(ids_existentes) > 1:
        st.error(
            "As concentrações já possuem fichas diferentes ("
            + ", ".join(ids_existentes)
            + "). Para preservar os históricos, esta mesclagem está bloqueada "
              "até ser definida uma ficha principal."
        )
    elif ids_existentes:
        ficha = fichas.get(ids_existentes[0], {})
        st.info(
            f"A ficha existente #{ids_existentes[0]} "
            f"({ficha.get('titulo','')}) será reutilizada, com histórico intacto."
        )
    else:
        st.info("Será criada uma única ficha quando o analista iniciar "
                "o acompanhamento da concentração mesclada.")
    confirmado = st.checkbox(
        "Confirmo que estas concentrações pertencem ao mesmo evento e "
        "que desejo um único acompanhamento.",
        key=f"farol_merge_confirmar_{event_key}",
        disabled=not pode_editar,
    )
    if st.button(
        "Mesclar em uma única concentração",
        type="primary",
        disabled=(not pode_editar or not confirmado or len(ids_existentes) > 1
                  or len(numeros) < 6),
        key=f"farol_merge_confirmar_salvar_{event_key}",
    ):
        try:
            origens = sorted(chaves_fichas_candidatas)
            retorno = salvar_mescla(
                escolhido["evento"], selecionadas, numeros, origens, poligono
            )
            st.session_state[CHAVE_MODO] = False
            st.session_state.pop(CHAVE_POLIGONO, None)
            st.success(
                f"Mesclagem salva: {retorno['qtd_concentracoes']} concentrações, "
                f"{retorno['qtd_os']} O.S. e um único acompanhamento."
            )
            st.rerun()
        except Exception as erro:
            st.error(f"Não foi possível salvar a mesclagem: {erro}")
