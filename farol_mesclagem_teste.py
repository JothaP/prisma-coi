"""PRISMA — piloto isolado de mesclagem de concentrações por evento.

Somente usado por pages/Ambiente_de_Teste.py. Lê PONTOS, EVENTOS e GPKG
em modo leitura. Mesclagens e acompanhamentos ficam APENAS na sessão
Streamlit do analista: nunca gravar vínculos, fichas ou auditoria de produção.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from html import escape as html_escape
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re
import sqlite3
import unicodedata

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
from pyproj import CRS, Transformer
from shapely import wkb
from shapely.geometry import Point, mapping
from shapely.ops import transform, unary_union

PASTA_GEO = Path(__file__).resolve().parent / "geodados" / "municipios"
FUSO = ZoneInfo("America/Fortaleza")
RAIO_M = 750
MINIMO_OS = 3
ESTADOS = ["Pendente", "Em Andamento", "Concluído"]


def normalizar(texto):
    valor = unicodedata.normalize("NFKD", str(texto or "").upper().strip())
    return re.sub(r"\s+", " ", "".join(c for c in valor if not unicodedata.combining(c))).strip()


def nome_area(texto):
    valor = normalizar(texto)
    valor = re.sub(r"^BAIRRO(?:\s*[-:]\s*|\s+)", "", valor)
    return re.sub(r"\s+", " ", valor.replace("VILLA", "VILA")).strip(" ,;:-")


def nomes_areas_evento(texto):
    """Lista explícita de bairros do evento; não presume áreas por proximidade."""
    bruto = normalizar(texto)
    bruto = re.sub(r"\b(?:AREAS?|BAIRROS?)\s+(?:AFETADOS?|IMPACTADOS?)\s*:", ";", bruto)
    bruto = re.sub(r"\bBAIRROS?\s*:", ";", bruto)
    areas = set()
    for item in re.split(r"[,;\n]+", bruto):
        item = re.sub(r"\(\s*PARCIAL\s*\)", "", item)
        area = nome_area(item)
        if area and not re.fullmatch(r"(?:AREAS?|BAIRROS?|IMPACTADOS?|AFETADOS?)", area):
            areas.add(area)
    return areas


def evento_municipal(evento):
    texto = normalizar(evento.get("Áreas Impactadas", ""))
    return bool(re.search(
        r"\b(?:TODO O MUNICIPIO|MUNICIPIO INTEIRO|TODA A CIDADE|"
        r"CIDADE INTEIRA|TODOS OS BAIRROS)\b", texto
    ))


def parse_horario(valor):
    """Só datas com hora: date-only não pode justificar vínculo temporal."""
    if isinstance(valor, (datetime, pd.Timestamp)):
        dado = pd.Timestamp(valor)
    else:
        texto = str(valor or "").strip()
        if not re.search(r"\d{1,2}:\d{2}", texto):
            return pd.NaT
        dado = pd.to_datetime(texto, dayfirst=True, errors="coerce")
    if pd.isna(dado):
        return pd.NaT
    if dado.tzinfo is not None:
        dado = dado.tz_convert(FUSO).tz_localize(None)
    return dado


def chave_evento(evento):
    """Independe de mudança de status/áreas; diferencia ocorrências da cidade."""
    campos = [
        "Cidade", "Protocolo", "Início", "Serviço", "Código do Ativo", "Data"
    ]
    dados = [normalizar(evento.get(c, "")) for c in campos]
    return hashlib.sha256(
        json.dumps(dados, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:24]


def caminho_gpkg(municipio, pasta=PASTA_GEO):
    alvo = re.sub(r"[^A-Z0-9]", "", normalizar(municipio))
    if not alvo or not pasta.is_dir():
        return None
    candidatos = []
    for arquivo in pasta.glob("*.gpkg"):
        nome = re.sub(r"^(?:NM_MUN|NM_RGI)[_\s-]*", "", arquivo.stem, flags=re.I)
        chave = re.sub(r"[^A-Z0-9]", "", normalizar(nome))
        if chave == alvo:
            candidatos.append(arquivo)
    return sorted(candidatos)[0] if candidatos else None


@st.cache_data(ttl=300, show_spinner=False, max_entries=12)
def ler_gpkg_evento(assinatura):
    """Carrega polígonos originais (incluindo furos) por coordenadas e SRC."""
    path = Path(assinatura[0])
    por_nome = {}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        camadas = db.execute(
            "SELECT table_name, srs_id FROM gpkg_contents WHERE data_type='features'"
        ).fetchall()
        for tabela, srs in camadas:
            campo = db.execute(
                "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?",
                (tabela,)
            ).fetchone()
            if not campo:
                continue
            geom_campo, geom_srs = campo
            q = lambda x: '"' + x.replace('"', '""') + '"'
            colunas = [a[1] for a in db.execute(f"PRAGMA table_info({q(tabela)})")]
            padrao = {re.sub(r"[^A-Z0-9]", "", normalizar(c)): c for c in colunas}
            nome_campo = next(
                (padrao[k] for k in ("NMBAIRRO", "NOMEBAIRRO", "BAIRRO", "NOME")
                 if k in padrao), None
            )
            if not nome_campo:
                continue   # Sem nome não se pode associar a um evento nominal
            srs_info = db.execute(
                "SELECT organization, organization_coordsys_id, definition "
                "FROM gpkg_spatial_ref_sys WHERE srs_id=?", (geom_srs or srs,)
            ).fetchone()
            if not srs_info:
                continue
            org, cod, wkt_crs = srs_info
            try:
                crs = CRS.from_user_input(f"{org}:{cod}") if org and cod else CRS.from_wkt(wkt_crs)
            except Exception:
                crs = CRS.from_wkt(wkt_crs)
            proj = None if crs.equals(CRS.from_epsg(4326)) else Transformer.from_crs(
                crs, 4326, always_xy=True
            )
            for blob, nome in db.execute(
                f"SELECT {q(geom_campo)}, {q(nome_campo)} FROM {q(tabela)}"
            ):
                nm = nome_area(nome)
                if not nm or not blob or len(blob) < 8 or blob[:2] != b"GP":
                    continue
                try:
                    envelope = (blob[3] >> 1) & 7
                    offset = {0:0,1:32,2:48,3:48,4:64}.get(envelope)
                    if offset is None:
                        continue
                    geometria = wkb.loads(bytes(blob)[8+offset:])
                    if proj:
                        geometria = transform(proj.transform, geometria)
                    partes = (
                        [geometria] if geometria.geom_type == "Polygon"
                        else list(geometria.geoms) if geometria.geom_type == "MultiPolygon"
                        else []
                    )
                    por_nome.setdefault(nm, []).extend(p for p in partes if not p.is_empty)
                except (ValueError, TypeError, OverflowError):
                    continue
    return por_nome


def relacao_concentracoes_evento(evento, concentracoes, grupos_os, poligonos,
                                 agora=None):
    """Associa grupos à mesma ocorrência por município, espaço e janela.

    Pelo menos uma O.S. de cada grupo deve estar numa das áreas declaradas e
    na janela [início -1h, término +3h]. Na existência de polígono nomeado,
    a geometria prevalece sobre o texto do bairro.
    """
    if concentracoes.empty or grupos_os.empty:
        return []
    municipio = normalizar(evento.get("Cidade", ""))
    inicio = parse_horario(evento.get("Início", ""))
    if not municipio or pd.isna(inicio):
        return []
    fim = parse_horario(evento.get("Término Real", ""))
    finalizado = any(p in normalizar(evento.get("Status", ""))
                     for p in ("FINALIZ", "CONCLUID", "ENCERRAD"))
    if pd.isna(fim):
        if finalizado:
            return []
        fim = pd.Timestamp(agora or datetime.now(FUSO).replace(tzinfo=None))
    if fim < inicio:
        return []
    de = inicio - pd.Timedelta(hours=1)
    ate = fim + pd.Timedelta(hours=3)
    municipal = evento_municipal(evento)
    areas = set() if municipal else nomes_areas_evento(evento.get("Áreas Impactadas", ""))
    geos = [geo for area in areas for geo in poligonos.get(area, [])]
    sem_poligono = {area for area in areas if not poligonos.get(area)}
    if not municipal and not geos and not sem_poligono:
        return []
    saida = []
    for _, c in concentracoes.iterrows():
        ids = grupos_os[grupos_os["_grupo"] == c["_grupo"]]
        vinculadas = []
        for _, osr in ids.iterrows():
            if normalizar(osr.get("Cidade", "")) != municipio:
                continue
            momento = parse_horario(osr.get("Dt. Emissão", ""))
            if pd.isna(momento) or not de <= momento <= ate:
                continue
            if municipal:
                dentro = True
            else:
                lat, lon = osr.get("_lat"), osr.get("_lon")
                pt = (Point(float(lon), float(lat))
                      if pd.notna(lat) and pd.notna(lon) else None)
                dentro = (pt is not None and any(geo.covers(pt) for geo in geos))
                # Texto somente para áreas SEM geometria disponível.
                if not dentro and nome_area(osr.get("Bairro", "")) in sem_poligono:
                    dentro = True
            if dentro:
                vinculadas.append(str(osr.get("Nº da O.S", "")).strip())
        if vinculadas:
            saida.append({
                "codigo": str(c["Concentração"]),
                "grupo": c["_grupo"],
                "municipio": municipio,
                "total_os": int(c["O.S."]),
                "vinculadas": len(set(vinculadas)),
                "os_evento": sorted(set(vinculadas)),
                "latitude": float(c["Latitude"]),
                "longitude": float(c["Longitude"]),
                "bairros": str(c["Bairros"]),
            })
    return sorted(saida, key=lambda x: (-x["vinculadas"], x["codigo"]))


def unir_grupos_os(grupos_os, ids_grupos):
    """Uma única lista de O.S. para a mesclagem, sem duplicar matrículas/OS."""
    lista = grupos_os[grupos_os["_grupo"].isin(set(ids_grupos))].copy()
    if lista.empty:
        return lista
    lista["_id_merge"] = lista.apply(
        lambda r: (
            f"{normalizar(r.get('Cidade'))}:{str(r.get('Nº da O.S', '')).strip()}"
            if str(r.get("Nº da O.S", "")).strip()
            else f"SEM_OS:{normalizar(r.get('Cidade'))}:{str(r.get('Matrícula',''))}:"
                 f"{r.get('_lat')}:{r.get('_lon')}"
        ), axis=1
    )
    return lista.drop_duplicates(subset=["_id_merge"]).drop(columns="_id_merge")


def mesclagem_valida(candidatas, selecionadas):
    """Impede uma fusão fora do evento ou com menos de 2 grupos distintos."""
    mapa = {x["codigo"]: x for x in candidatas}
    escolhidos = list(dict.fromkeys(selecionadas))
    if len(escolhidos) < 2 or any(x not in mapa for x in escolhidos):
        return False
    return len({mapa[x]["municipio"] for x in escolhidos}) == 1


def agora_texto():
    return datetime.now(FUSO).strftime("%d/%m/%Y %H:%M")


def mapa_mesclagem(candidatas, marcados, os_unidas, poligonos, evento,
                   raio=RAIO_M):
    """Antes: círculos individuais. Depois: união visual com um único centro."""
    agrupados = [c for c in candidatas if c["codigo"] in set(marcados)]
    centros = [[c["latitude"], c["longitude"]] for c in candidatas]
    if not centros:
        return folium.Map(location=[-5.089,-42.80],zoom_start=12)
    lat_centro = sum(p[0] for p in centros)/len(centros)
    lon_centro = sum(p[1] for p in centros)/len(centros)
    mapa = folium.Map(location=[lat_centro,lon_centro],zoom_start=12,
                      tiles="OpenStreetMap",control_scale=True)
    if poligonos:
        feats = [
            {"type":"Feature", "properties":{"bairro":nome},
             "geometry":mapping(g)}
            for nome,geos in poligonos.items() for g in geos
        ]
        if feats:
            folium.GeoJson(
                {"type":"FeatureCollection","features":feats},
                name="Áreas do evento", style_function=lambda _: {
                    "color":"#d54139","weight":2.4,"fillColor":"#db4135",
                    "fillOpacity":0.11,
                }, tooltip=folium.GeoJsonTooltip(
                    fields=["bairro"],aliases=["Área impactada:"]
                )
            ).add_to(mapa)
    for c in candidatas:
        selecionado = c["codigo"] in set(marcados)
        cor = "#6d36c5" if selecionado else "#607b91"
        folium.Circle(
            [c["latitude"],c["longitude"]],radius=raio,color=cor,
            weight=2 if selecionado else 1.25,opacity=0.85,
            fill=False,dash_array="6,5" if selecionado else "3,6",
            tooltip=(f"{c['codigo']} | {c['total_os']} O.S. "
                     f"| {c['vinculadas']} vinculada(s) ao evento"),
        ).add_to(mapa)
        folium.CircleMarker(
            [c["latitude"],c["longitude"]],radius=7 if selecionado else 5,
            color=cor,fill=True,fill_color=cor,fill_opacity=0.92,
            tooltip=f"{c['codigo']} — {'selecionada' if selecionado else 'candidata'}",
        ).add_to(mapa)
    if len(agrupados)>=2 and not os_unidas.empty:
        # Uma feição MultiPolygon representa toda a área operacional combinada.
        # Não cria um "novo raio" fictício ao redor do centro médio.
        ida=Transformer.from_crs(4326,3857,always_xy=True)
        volta=Transformer.from_crs(3857,4326,always_xy=True)
        cobertura=unary_union([
            Point(*ida.transform(c["longitude"],c["latitude"])).buffer(raio)
            for c in agrupados
        ])
        geometria=transform(volta.transform,cobertura)
        folium.GeoJson(mapping(geometria),name="Concentração mesclada (áreas originais)",
            style_function=lambda _:{"color":"#5925a4","weight":3,
                                    "fillColor":"#7844d7","fillOpacity":0.13},
            tooltip="Uma concentração operacional — cobertura dos grupos originais",
        ).add_to(mapa)
        lat=float(os_unidas["_lat"].mean())
        lon=float(os_unidas["_lon"].mean())
        folium.Marker([lat,lon],
            icon=folium.Icon(color="purple",icon="link",prefix="fa"),
            tooltip=(f"Concentração mesclada: {len(agrupados)} grupos, "
                     f"{len(os_unidas)} O.S. · um acompanhamento"),
        ).add_to(mapa)
    if len(centros)>1:
        mapa.fit_bounds([[min(p[0] for p in centros),min(p[1] for p in centros)],
                         [max(p[0] for p in centros),max(p[1] for p in centros)]],
                        padding=(55,55),max_zoom=15)
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def renderizar_mesclagem_teste(ler_base, preparar_os, concentracoes_farol,
                              id_farol):
    st.subheader("Mesclagem de concentrações por evento")
    st.caption(
        "Protótipo isolado: lê as O.S. e os eventos reais do Farol, "
        "mas NÃO grava vínculos ou acompanhamentos no Supabase nem em planilhas. "
        "Os registros de teste ficam nesta sessão."
    )
    hoje=datetime.now(FUSO).date()
    with st.form("farol_merge_consulta"):
        c1,c2,c3=st.columns([1,1,1.2])
        inicio=c1.date_input("De",value=hoje-timedelta(days=7),
                             format="DD/MM/YYYY",key="merge_inicio")
        fim=c2.date_input("Até",value=hoje,format="DD/MM/YYYY",key="merge_fim")
        municipio=c3.text_input("Município",value="Teresina",
                                key="merge_cidade")
        carregar=st.form_submit_button("Carregar concentrações e eventos",
                                        type="primary")
    if carregar:
        if inicio>fim or not municipio.strip():
            st.error("Informe um município e um período válido.")
        else:
            try:
                with st.spinner("Consultando O.S. e eventos para o piloto..."):
                    pontos=preparar_os(ler_base(id_farol,"PONTOS"))
                    pontos=pontos[
                        (pontos["_municipio"] == normalizar(municipio))
                        & pontos["_data"].dt.date.between(inicio,fim)
                    ].copy()
                    concs,grupos=concentracoes_farol(pontos,RAIO_M,MINIMO_OS)
                    eventos=ler_base(id_farol,"EVENTOS")
                    eventos=eventos[
                        eventos["Cidade"].map(normalizar)==normalizar(municipio)
                    ].copy() if not eventos.empty else eventos
                    st.session_state["farol_merge_dados"]={
                        "municipio":municipio,"inicio":inicio,"fim":fim,
                        "concentracoes":concs,"grupos":grupos,
                        "eventos":eventos,
                    }
                st.success("Bases carregadas para o ambiente experimental.")
            except (ValueError, KeyError, OSError, RuntimeError) as exc:
                st.error(f"Não foi possível preparar o piloto: {exc}")
    dados=st.session_state.get("farol_merge_dados")
    if not dados:
        st.info("Carregue os dados para selecionar um evento e as concentrações.")
        return
    concs,grupos,evs=dados["concentracoes"],dados["grupos"],dados["eventos"]
    if concs.empty:
        st.warning("Nenhuma concentração com 3 ou mais O.S. neste recorte.")
        return
    if evs.empty:
        st.warning("Nenhum evento cadastrado para esse município.")
        return
    validos=[]
    for i,evento in evs.iterrows():
        inicio_evt=parse_horario(evento.get("Início",""))
        final_evt=parse_horario(evento.get("Término Real",""))
        if pd.isna(inicio_evt):
            continue
        if inicio_evt.date()>dados["fim"] or (
            pd.notna(final_evt) and final_evt.date()<dados["inicio"]
        ):
            continue
        validos.append((i,evento))
    if not validos:
        st.info("Nenhum evento com horário de início nesse período.")
        return
    ordens=sorted(validos,
        key=lambda t: str(t[1].get("Início","")),reverse=True)
    indices=[i for i,_ in ordens]
    idx=st.selectbox("Evento responsável pelas concentrações", indices,
        format_func=lambda k: (
            f"{str(evs.loc[k].get('Protocolo','') or 'Sem protocolo')} · "
            f"{str(evs.loc[k].get('Serviço','') or 'Evento')[:75]} · "
            f"{str(evs.loc[k].get('Início',''))}"
        ),key="farol_merge_evento")
    evento=evs.loc[idx].to_dict()
    cidade=normalizar(evento.get("Cidade",""))
    arquivo=caminho_gpkg(cidade)
    por_nome={}
    if arquivo:
        try:
            info=arquivo.stat()
            por_nome=ler_gpkg_evento((str(arquivo),info.st_size,info.st_mtime_ns))
        except (OSError,ValueError,sqlite3.Error) as err:
            st.warning(f"Os polígonos não puderam ser lidos: {err}. "
                       "Somente áreas sem geometria poderão ser relacionadas pelo bairro.")
    candidatas=relacao_concentracoes_evento(evento,concs,grupos,por_nome)
    if not candidatas:
        st.warning("Nenhuma concentração apresenta O.S. vinculada a esse evento "
                   "por município, área impactada e janela de tempo.")
        return
    st.caption(
        f"{len(candidatas)} concentração(ões) com O.S. vinculadas a este evento. "
        "Selecione duas ou mais; os agrupamentos originais continuam independentes no Farol."
    )
    resumo=pd.DataFrame([{
        "Concentração":c["codigo"],"Município":c["municipio"],
        "Bairros":c["bairros"],"O.S. do grupo":c["total_os"],
        "O.S. do evento":c["vinculadas"]
    } for c in candidatas])
    st.dataframe(resumo,hide_index=True,use_container_width=True)
    por_codigo={c["codigo"]:c for c in candidatas}
    key=chave_evento(evento)
    registro=st.session_state.setdefault("farol_merge_acompanhamentos",{}).get(key)
    opcoes=[c["codigo"] for c in candidatas]
    selecionadas=st.multiselect(
        "Concentrações que serão mescladas neste evento",opcoes,
        default=[x for x in (registro or {}).get("grupos",[]) if x in opcoes],
        format_func=lambda c: (
            f"{c} — {por_codigo[c]['total_os']} O.S. "
            f"({por_codigo[c]['vinculadas']} vinculadas ao evento)"
        ),key=f"farol_merge_sel_{key}"
    )
    grupos_selecionados=[por_codigo[x]["grupo"] for x in selecionadas]
    unidas=unir_grupos_os(grupos,grupos_selecionados)
    detalhe=st.columns(3)
    detalhe[0].metric("Concentrações selecionadas",len(selecionadas))
    detalhe[1].metric("O.S. distintas",len(unidas))
    detalhe[2].metric("Matrículas distintas",(
        unidas["Matrícula"].replace("",pd.NA).dropna().nunique()
        if not unidas.empty else 0
    ))
    geos_evento=(
        {a:por_nome[a] for a in nomes_areas_evento(evento.get("Áreas Impactadas",""))
         if a in por_nome}
        if not evento_municipal(evento) else {}
    )
    mapa=mapa_mesclagem(candidatas,selecionadas,unidas,
                        geos_evento,evento)
    st_folium(mapa,height=460,width=None,returned_objects=[],
              key=f"farol_merge_mapa_{key}_{hash(tuple(sorted(selecionadas)))}")
    if len(selecionadas)>=2 and not unidas.empty:
        col_a,col_b=st.columns([2,1])
        with col_a:
            st.caption("As O.S. abaixo formarão um único conjunto operacional.")
            st.dataframe(
                unidas.reindex(columns=[
                    "Nº da O.S","Matrícula","Cidade","Bairro",
                    "Dt. Emissão","Status OS"
                ]),hide_index=True,use_container_width=True,height=240
            )
        with col_b:
            st.info(
                "A mesclagem reúne os pontos de todas as concentrações "
                "selecionadas. Não redefine o raio original de 750 m "
                "nem presume uma causa hidráulica comum."
            )
    if st.button(
        "🔗 Criar / atualizar concentração mesclada de teste",
        type="primary",disabled=not mesclagem_valida(candidatas,selecionadas),
        key=f"farol_merge_salvar_{key}",
    ):
        registros=st.session_state["farol_merge_acompanhamentos"]
        # Impede que um mesmo agrupamento ganhe dois acompanhamentos de teste.
        utilizados={
            c for outra,ch in registros.items() if outra!=key
            for c in ch.get("grupos",[])
        }
        if any(c in utilizados for c in selecionadas):
            st.error("Uma concentração selecionada já pertence a outra "
                     "mesclagem de teste. Desfaça aquela associação antes.")
        else:
            momento=agora_texto()
            anterior=registros.get(key)
            item=anterior.copy() if anterior else {
                "evento_id":key,
                "titulo":f"Evento {evento.get('Protocolo') or 'operacional'} — {cidade}",
                "status":"Em Andamento","descricao":"",
                "historico":[],"criado_em":momento,
            }
            if not anterior or set(item.get("grupos",[]))!=set(selecionadas):
                item["historico"]=item.get("historico",[])+[{
                    "data_hora":momento,"status":item["status"],
                    "descricao":(
                        f"Mesclagem {'atualizada' if anterior else 'criada'}: "
                        + ", ".join(selecionadas)
                    ),
                }]
            item.update({
                "grupos":list(selecionadas),
                "os":sorted(set(unidas["Nº da O.S"].astype(str))),
                "qtd_os":len(unidas),
                "municipio":cidade,
                "protocolo":str(evento.get("Protocolo","")),
                "ultima_atualizacao":momento,
            })
            registros[key]=item
            st.success("Mesclagem registrada somente nesta sessão de teste.")
            st.rerun()
    if registro:
        st.divider()
        st.subheader("Acompanhamento único — teste")
        st.caption(
            f"Evento {registro['protocolo'] or 'sem protocolo'} · "
            f"{len(registro['grupos'])} concentrações · "
            f"{registro['qtd_os']} O.S. · "
            "sem gravações na produção"
        )
        with st.form(f"farol_merge_acomp_form_{key}"):
            titulo=st.text_input("Título",value=registro["titulo"])
            status=st.selectbox("Situação",ESTADOS,
                index=ESTADOS.index(registro["status"]))
            descricao=st.text_area("Descrição / relato operacional",
                                   value=registro.get("descricao",""),height=120)
            movimento=st.text_area(
                "Nova movimentação (opcional)",height=75,
                placeholder="Descreva a atualização realizada..."
            )
            salvar_ficha=st.form_submit_button("💾 Salvar acompanhamento de teste",
                                               type="primary")
        if salvar_ficha:
            if not titulo.strip():
                st.error("Informe um título para o acompanhamento.")
            else:
                momento=agora_texto()
                mudou=(titulo!=registro["titulo"] or status!=registro["status"]
                       or descricao!=registro.get("descricao",""))
                if mudou or movimento.strip():
                    detalhe=movimento.strip() or "Campos da ficha atualizados."
                    registro["historico"].append({
                        "data_hora":momento,"descricao":detalhe,
                        "status":status,
                    })
                    registro.update({
                        "titulo":titulo.strip(),"status":status,
                        "descricao":descricao,"ultima_atualizacao":momento,
                    })
                st.success("Acompanhamento atualizado nesta sessão de teste.")
                st.rerun()
        with st.expander("Histórico do acompanhamento mesclado",expanded=False):
            for item in reversed(registro.get("historico",[])):
                st.markdown(
                    f"**{item['data_hora']} — {item['status']}**"
                )
                st.write(item["descricao"])
        exportar=json.dumps(registro,ensure_ascii=False,indent=2)
        a,b=st.columns(2)
        a.download_button(
            "⬇️ Exportar acompanhamento de teste (JSON)",
            data=exportar.encode("utf-8"),mime="application/json",
            file_name=f"acompanhamento_teste_{key}.json"
        )
        if b.button("Desfazer mesclagem de teste",key=f"farol_merge_desfazer_{key}"):
            st.session_state["farol_merge_acompanhamentos"].pop(key,None)
            st.rerun()
