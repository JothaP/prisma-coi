"""Laboratório de influência: consulta geográfica independente, somente leitura.

Não importa páginas de produção nem modifica bases ou tabelas.
A geometria desenhada é mantida apenas na sessão e exportada pelo navegador.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

import folium
from folium.plugins import Draw, Fullscreen
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from shapely.geometry import box, shape, mapping, LineString
from streamlit_folium import st_folium

RAIZ = Path(__file__).resolve().parent
PASTA_DADOS = RAIZ / "dados"
ARQUIVO_REDE = PASTA_DADOS / "Rede_Agua_THE.kmz"
ARQUIVO_DEM = PASTA_DADOS / "topografia" / "teresina_dem.tif"
CENTRO_PADRAO = (-5.0892, -42.8016)
COLUNAS_MEDICAO = ["id", "data", "municipio", "bairro", "latitude",
                   "longitude", "pressaomca", "observacao", "matricula"]


def _assinatura(arquivo: Path):
    if not arquivo.is_file():
        return None
    s = arquivo.stat()
    return (str(arquivo), s.st_mtime_ns, s.st_size)


def _normalizar(valor):
    txt = unicodedata.normalize("NFKD", str(valor or "").strip().upper())
    return "".join(c for c in txt if not unicodedata.combining(c))


def _float(valor):
    txt = str(valor or "").strip().replace(" ", "")
    if not txt or txt.lower() in {"nan", "none", "null", "-"}:
        return None
    if "." in txt and "," in txt:
        txt = txt.replace(".", "").replace(",", ".") if txt.rfind(",") > txt.rfind(".") else txt.replace(",", "")
    else:
        txt = txt.replace(",", ".")
    try:
        x = float(txt)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _coords_validas(lat, lon):
    return (lat is not None and lon is not None and
            -90 <= lat <= 90 and -180 <= lon <= 180)


def _haversine(lat1, lon1, lat2, lon2):
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2
    a += (math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
          * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12742000 * math.asin(min(1, math.sqrt(a)))


@st.cache_data(show_spinner=False, max_entries=3)
def _carregar_ativos(assinaturas):
    registros = []
    fontes = [
        (PASTA_DADOS / "pocos.csv", "Poço / Ativo", "NOME_POCO"),
        (PASTA_DADOS / "loggers.csv", "Logger", "ENDERECO"),
    ]
    for caminho, tipo, campo_nome in fontes:
        if not caminho.is_file():
            continue
        df = pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        essenciais = {"IDENTIFICACAO_ATIVO", "MUNICIPIO", "LATITUDE", "LONGITUDE", campo_nome}
        if not essenciais.issubset(df.columns):
            raise ValueError(f"{caminho.name}: colunas ausentes: {sorted(essenciais - set(df.columns))}")
        for item in df.to_dict("records"):
            if _normalizar(item.get("MUNICIPIO")) != "TERESINA":
                continue
            lat = _float(item.get("LATITUDE"))
            lon = _float(item.get("LONGITUDE"))
            if not _coords_validas(lat, lon):
                continue
            identificacao = str(item.get("IDENTIFICACAO_ATIVO") or "").strip()
            nome = str(item.get(campo_nome) or "").strip()
            id_extra = str(item.get("ID_POCO") or item.get("ID_LOGGER") or "").strip()
            registros.append({
                "tipo": tipo, "codigo": identificacao, "nome": nome,
                "id": id_extra, "lat": lat, "lon": lon,
                "pesquisa": _normalizar(" ".join((nome, identificacao, id_extra))),
                "rotulo": f"{nome or identificacao or id_extra} · {identificacao or tipo}",
            })
    # Não eliminar ativos distintos por coincidirem na posição geográfica.
    return registros


@st.cache_data(ttl=300, show_spinner=False, max_entries=2)
def _carregar_medicoes():
    """Usa a MESMA fonte híbrida de leitura do módulo 1, sem suas rotinas de escrita."""
    conf = st.secrets["gcp_json"]
    conf = json.loads(conf) if isinstance(conf, str) else dict(conf)
    cred = Credentials.from_service_account_info(
        conf, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    gc = gspread.authorize(cred)
    planilha = str(st.secrets.get(
        "modulo_1_spreadsheet_id", "15iN3YEGyxk3l1ZKaHJJp-BvTfVHqpd7gL1GX3RbAKUU"
    )).strip()
    valores = gc.open_by_key(planilha).worksheet("baixa_pressao").get_all_values()
    from historico_pressao_supabase import combinar_modulo1
    valores, _ = combinar_modulo1(valores)
    if len(valores) < 2:
        return pd.DataFrame(columns=COLUNAS_MEDICAO + ["lat", "lon", "mca", "dt"])
    cabecalho = [re.sub(r"[^a-z0-9]", "", _normalizar(c).lower()) for c in valores[0]]
    indices = {campo: i for i, campo in enumerate(cabecalho) if campo}
    essenciais = {"data", "municipio", "latitude", "longitude", "pressaomca"}
    if not essenciais.issubset(indices):
        raise ValueError("Fonte de pressão: faltam campos obrigatórios.")
    linhas = []
    for row in valores[1:]:
        if not any(str(v).strip() for v in row):
            continue
        get = lambda campo: str(row[indices[campo]] if campo in indices and indices[campo] < len(row) else "")
        if _normalizar(get("municipio")) != "TERESINA":
            continue
        lat, lon, mca = _float(get("latitude")), _float(get("longitude")), _float(get("pressaomca"))
        if not _coords_validas(lat, lon) or mca is None or mca < 0:
            continue
        dados = {campo: get(campo) for campo in COLUNAS_MEDICAO}
        dados.update({"lat": lat, "lon": lon, "mca": mca})
        linhas.append(dados)
    df = pd.DataFrame(linhas)
    if df.empty:
        return pd.DataFrame(columns=COLUNAS_MEDICAO + ["lat", "lon", "mca", "dt"])
    datas = pd.to_datetime(df["data"].astype(str), format="mixed", dayfirst=True, errors="coerce")
    numericos = pd.to_numeric(df["data"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    corrige = datas.isna() & numericos.between(20000, 90000)
    if corrige.any():
        datas.loc[corrige] = pd.to_datetime(numericos.loc[corrige], unit="D", origin="1899-12-30")
    df["dt"] = datas
    return df


@st.cache_data(show_spinner=False, max_entries=24)
def _cotas_dem(assinatura, coordenadas):
    """Altura da superfície no DEM, e NÃO carga hidráulica ou cota do equipamento."""
    if not assinatura or not coordenadas:
        return {}
    import rasterio
    import numpy as np
    from rasterio.warp import transform

    with rasterio.open(assinatura[0]) as ds:
        if not ds.crs or ds.count < 1:
            return {}
        xs, ys = transform("EPSG:4326", ds.crs,
                           [lon for lat, lon in coordenadas],
                           [lat for lat, lon in coordenadas])
        resultados = {}
        for (lat, lon), pixel in zip(
            coordenadas, ds.sample(zip(xs, ys), indexes=1, masked=True)
        ):
            z = pixel[0]
            if np.ma.is_masked(z):
                continue
            valor = float(z)
            if math.isfinite(valor) and (ds.nodata is None or not np.isclose(valor, ds.nodata)):
                resultados[(lat, lon)] = valor
        return resultados


def _coordenadas_kml(texto):
    pontos = []
    for token in (texto or "").split():
        partes = token.split(",")
        if len(partes) >= 2:
            lon, lat = _float(partes[0]), _float(partes[1])
            if _coords_validas(lat, lon):
                pontos.append((lon, lat))
    return pontos


@st.cache_data(show_spinner=False, max_entries=6)
def _recorte_rede(assinatura, limites, max_trechos=2300, max_vertices=33000):
    """Extrai incrementalmente apenas o trecho da rede no enquadramento escolhido."""
    if not assinatura:
        return [], False
    xmin, ymin, xmax, ymax = limites
    visao = box(xmin, ymin, xmax, ymax)
    feicoes = []
    vertices = 0
    limitado = False
    with zipfile.ZipFile(assinatura[0]) as pacote:
        arquivos = [n for n in pacote.namelist() if n.lower().endswith(".kml")]
        for nome in arquivos:
            with pacote.open(nome) as stream:
                pilha = []
                for evento, el in ET.iterparse(stream, events=("start", "end")):
                    if evento == "start":
                        pilha.append(el)
                        continue
                    if el.tag.rsplit("}", 1)[-1] == "Placemark":
                        titulo = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "name" and c.text), "")
                        for linha in el.iter():
                            if linha.tag.rsplit("}", 1)[-1] != "LineString":
                                continue
                            coord_text = next((c.text for c in linha.iter()
                                               if c.tag.rsplit("}", 1)[-1] == "coordinates"), "")
                            coords = _coordenadas_kml(coord_text)
                            if len(coords) < 2:
                                continue
                            minx = min(p[0] for p in coords)
                            maxx = max(p[0] for p in coords)
                            miny = min(p[1] for p in coords)
                            maxy = max(p[1] for p in coords)
                            if maxx < xmin or minx > xmax or maxy < ymin or miny > ymax:
                                continue
                            geom = LineString(coords)
                            if geom.is_empty or not geom.intersects(visao):
                                continue
                            geom = geom.intersection(visao)
                            geoms = list(geom.geoms) if geom.geom_type in ("MultiLineString", "GeometryCollection") else [geom]
                            for parte in geoms:
                                if parte.geom_type != "LineString" or len(parte.coords) < 2:
                                    continue
                                simples = parte.simplify(0.000009, preserve_topology=False)
                                pontos = list(simples.coords)
                                if vertices + len(pontos) > max_vertices or len(feicoes) >= max_trechos:
                                    limitado = True
                                    break
                                feicoes.append({"type": "Feature", "properties": {"trecho": str(titulo or "")[:100]},
                                                "geometry": mapping(simples)})
                                vertices += len(pontos)
                            if limitado:
                                break
                        if len(pilha) > 1:
                            try:
                                pilha[-2].remove(el)
                            except ValueError:
                                pass
                        el.clear()
                    elif el.tag.rsplit("}", 1)[-1] in ("Folder", "Document"):
                        el.clear()
                    pilha.pop()
                    if limitado:
                        break
            if limitado:
                break
    return feicoes, limitado


def _poligono_desenhado(desenho):
    try:
        geo = shape(desenho.get("geometry", desenho))
        if geo.geom_type not in {"Polygon", "MultiPolygon"} or geo.is_empty:
            return None
        if not geo.is_valid or geo.area == 0:
            return None
        if not all(-180 <= v <= 180 for v in (geo.bounds[0], geo.bounds[2])):
            return None
        if not all(-90 <= v <= 90 for v in (geo.bounds[1], geo.bounds[3])):
            return None
        return mapping(geo)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _kmz_poligonos(poligonos, titulo, ativo):
    """Gera KMZ compatível com Google Earth/QGIS, coordenadas KML lon,lat,0."""
    ns = "http://www.opengis.net/kml/2.2"
    root = ET.Element("kml", xmlns=ns)
    doc = ET.SubElement(root, "Document")
    ET.SubElement(doc, "name").text = titulo

    def ring(pai, tipo, coords):
        elem = ET.SubElement(pai, tipo)
        anel = ET.SubElement(elem, "LinearRing")
        ET.SubElement(anel, "coordinates").text = " ".join(
            f"{float(x):.9f},{float(y):.9f},0" for x, y, *_ in coords
        )

    def add_poly(pai, geom):
        poly = ET.SubElement(pai, "Polygon")
        ET.SubElement(poly, "tessellate").text = "1"
        ring(poly, "outerBoundaryIs", geom.exterior.coords)
        for interior in geom.interiors:
            ring(poly, "innerBoundaryIs", interior.coords)

    for i, item in enumerate(poligonos, 1):
        geom = shape(item)
        pm = ET.SubElement(doc, "Placemark")
        ET.SubElement(pm, "name").text = f"{titulo} — Área {i}"
        ET.SubElement(pm, "description").text = (
            f"Investigação experimental. Ativo de referência: {ativo}. "
            "Limite desenhado manualmente; não representa influência hidráulica comprovada."
        )
        if geom.geom_type == "Polygon":
            add_poly(pm, geom)
        elif geom.geom_type == "MultiPolygon":
            multi = ET.SubElement(pm, "MultiGeometry")
            for parte in geom.geoms:
                add_poly(multi, parte)
    destino = BytesIO()
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as pacote:
        pacote.writestr("doc.kml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return destino.getvalue()


def _rotulo(mapa_grupo, lat, lon, texto, cor, dx=14):
    html = (
        "<div style='white-space:nowrap;display:inline-block;background:#fff;"
        "border:1px solid #c8d5e1;border-left:3px solid " + cor + ";"
        "border-radius:4px;padding:2px 5px;font:10px Segoe UI,Arial,sans-serif;"
        "color:#203044;box-shadow:0 1px 4px #0002'>"
        + escape(texto) + "</div>"
    )
    folium.Marker([lat, lon], icon=folium.DivIcon(
        icon_size=(1, 1), icon_anchor=(-dx, 6), html=html
    )).add_to(mapa_grupo)


def renderizar_investigacao():
    st.title("🗺️ Laboratório — Áreas de influência")
    st.caption(
        "Pesquisa de ativos • vizinhança sem raio de corte • medições históricas "
        "do Módulo 1 • elevação do terreno • desenho e exportação KMZ. Somente leitura."
    )
    if st.button("← Voltar ao Hub Central", key="inv_voltar_hub"):
        st.switch_page("app.py")

    assinaturas = tuple(_assinatura(PASTA_DADOS / nome) for nome in ("pocos.csv", "loggers.csv"))
    if not any(assinaturas):
        st.error("Cadastros de ativos não encontrados em dados/.")
        return
    try:
        ativos = _carregar_ativos(assinaturas)
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Não foi possível carregar os ativos: {exc}")
        return
    if not ativos:
        st.warning("Nenhum ativo georreferenciado encontrado em Teresina.")
        return

    with st.form("inv_busca_form"):
        c1, c2 = st.columns([4, 1])
        with c1:
            termo = st.text_input(
                "Pesquisar ativo por nome, identificação ou código",
                value=st.session_state.get("inv_termo", ""),
                placeholder="Ex.: THE-075, PL-THE-PCO0091 ou código do logger",
            )
        with c2:
            st.write("")
            st.write("")
            buscar = st.form_submit_button("Pesquisar", type="primary", use_container_width=True)
    if buscar:
        st.session_state["inv_termo"] = termo.strip()
        # A rede precisa seguir o novo ativo, não um enquadramento de busca anterior.
        st.session_state.pop("inv_rede_limites", None)
    busca = _normalizar(st.session_state.get("inv_termo", ""))
    selecionado = None
    if busca:
        resultados = [i for i, ativo in enumerate(ativos) if busca in ativo["pesquisa"]]
        if not resultados:
            st.warning("Nenhum ativo corresponde à pesquisa.")
        else:
            escolhido = st.selectbox("Ativo de referência", resultados,
                format_func=lambda i: ativos[i]["rotulo"], key="inv_selecao")
            selecionado = ativos[escolhido]
    else:
        st.info("Pesquise um ativo para centralizar o mapa e ordenar os equipamentos mais próximos.")

    ativo_referencia = (
        (selecionado["tipo"], selecionado["codigo"], selecionado["lat"], selecionado["lon"])
        if selecionado else None
    )
    if st.session_state.get("inv_ativo_referencia") != ativo_referencia:
        st.session_state["inv_ativo_referencia"] = ativo_referencia
        st.session_state.pop("inv_mapa_centro", None)
        st.session_state.pop("inv_mapa_zoom", None)
        st.session_state.pop("inv_rede_limites", None)
        st.session_state["inv_mapa_versao"] = st.session_state.get("inv_mapa_versao", 0) + 1

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        quantidade_ativos = st.select_slider(
            "Ativos mais próximos (sem raio)", options=[10, 20, 40, 80, 150], value=40,
            key="inv_quantidade_ativos",
        )
    with c2:
        quantidade_medicoes = st.select_slider(
            "Medições exibidas (mais próximas)", options=[100, 250, 500, 1000], value=250,
            key="inv_quantidade_medicoes",
        )
    with c3:
        mostrar_rede = st.checkbox("Exibir rede de distribuição (recorte)", value=False,
                                   key="inv_mostrar_rede")
    mostrar_rotulos = st.checkbox("Exibir rótulos permanentes nas medições", value=True,
                                  key="inv_rotulos_medicoes")

    medicoes = pd.DataFrame(columns=COLUNAS_MEDICAO + ["lat", "lon", "mca", "dt"])
    # Uma consulta ao histórico pode ser cara: só realizá-la depois da escolha
    # do ativo; as visitas à tela inicial não devem carregar toda a base.
    if selecionado:
        try:
            with st.spinner("Consultando a fonte histórica de pressões, somente leitura..."):
                medicoes = _carregar_medicoes()
        except Exception as exc:
            st.warning(f"Medições temporariamente indisponíveis: {type(exc).__name__}: {exc}")

    if not medicoes.empty and medicoes["dt"].notna().any():
        minimo = medicoes["dt"].min().date()
        maximo = medicoes["dt"].max().date()
        datas = st.date_input(
            "Período das medições", value=(minimo, maximo), min_value=minimo,
            max_value=maximo, format="DD/MM/YYYY", key="inv_periodo",
        )
        if isinstance(datas, (tuple, list)) and len(datas) == 2:
            inicio, fim = datas
            medicoes = medicoes.loc[medicoes["dt"].dt.date.between(inicio, fim)]
        elif isinstance(datas, (tuple, list)) and len(datas) != 2:
            st.caption("Selecione a data final para aplicar o filtro.")
    centro = (selecionado["lat"], selecionado["lon"]) if selecionado else CENTRO_PADRAO

    proximos = []
    if selecionado:
        proximos = sorted(
            ((a, _haversine(centro[0], centro[1], a["lat"], a["lon"]))
             for a in ativos if a is not selecionado),
            key=lambda pair: pair[1]
        )[:quantidade_ativos]

    if not medicoes.empty:
        medicoes = medicoes.copy()
        # Classificação por distância APENAS para limitar a quantidade enviada ao navegador.
        # Nunca é aplicado um raio para decidir inclusão ou influência.
        # Haversine vetorizado: não executa um loop Python por medição.
        import numpy as np
        la1 = np.radians(centro[0])
        la2 = np.radians(medicoes["lat"].to_numpy(dtype=float))
        dlat = la2 - la1
        dlon = np.radians(medicoes["lon"].to_numpy(dtype=float) - centro[1])
        a = np.sin(dlat / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(dlon / 2) ** 2
        medicoes["_dist"] = 12742000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        medicoes = medicoes.nsmallest(quantidade_medicoes, "_dist")

    posicoes = []
    if selecionado:
        posicoes.append((selecionado["lat"], selecionado["lon"]))
    posicoes.extend((a["lat"], a["lon"]) for a, _ in proximos)
    posicoes.extend((float(r.lat), float(r.lon)) for r in medicoes.itertuples())
    try:
        cotas = _cotas_dem(_assinatura(ARQUIVO_DEM), tuple(dict.fromkeys(posicoes)))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        cotas = {}
        st.warning(f"Modelo de elevação não pôde ser amostrado: {exc}")

    centro_visual = st.session_state.get("inv_mapa_centro") or centro
    zoom_visual = st.session_state.get("inv_mapa_zoom") or (13 if selecionado else 12)
    mapa = folium.Map(location=centro_visual, zoom_start=zoom_visual,
                      tiles="OpenStreetMap", control_scale=True, prefer_canvas=True)
    folium.TileLayer("CartoDB positron", name="Mapa claro").add_to(mapa)
    Fullscreen(position="topright").add_to(mapa)

    if mostrar_rede and ARQUIVO_REDE.is_file():
        # Envelope da vista inicial: serve somente para controlar o peso do mapa,
        # não constitui raio de influência nem critério de proximidade dos ativos.
        lim = st.session_state.get("inv_rede_limites")
        if not lim:
            lim = (centro[1] - .043, centro[0] - .033,
                   centro[1] + .043, centro[0] + .033)
        try:
            trechos, limitado = _recorte_rede(_assinatura(ARQUIVO_REDE), tuple(lim))
            if trechos:
                folium.GeoJson(
                    {"type": "FeatureCollection", "features": trechos},
                    name="Rede de distribuição", show=True,
                    style_function=lambda _: {"color": "#327caa", "weight": 2, "opacity": .75},
                    tooltip=folium.GeoJsonTooltip(fields=["trecho"], aliases=["Trecho:"]),
                ).add_to(mapa)
            st.caption(f"Rede no enquadramento de consulta: {len(trechos)} trechos."
                       + (" Limite visual atingido; amplie o mapa para estudar uma área menor." if limitado else ""))
        except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
            st.warning(f"Não foi possível carregar o recorte da rede: {exc}")
    elif mostrar_rede:
        st.warning("Arquivo dados/Rede_Agua_THE.kmz não encontrado.")

    if selecionado:
        cor = "blue" if selecionado["tipo"] == "Poço / Ativo" else "orange"
        folium.Marker(centro,
            icon=folium.Icon(color=cor, icon="tint" if cor == "blue" else "signal", prefix="fa"),
            popup=folium.Popup(escape(selecionado["rotulo"]) + "<br><b>Ativo pesquisado</b>", max_width=350),
            tooltip="Ativo de referência: " + selecionado["rotulo"]).add_to(mapa)
        grupo_ativos = folium.FeatureGroup(name="Ativos mais próximos", show=True)
        for a, distancia in proximos:
            cor = "blue" if a["tipo"] == "Poço / Ativo" else "orange"
            ele = cotas.get((a["lat"], a["lon"]))
            elevacao = f"{ele:.1f} m" if ele is not None else "indisponível"
            popup = (
                "<b>" + escape(a["rotulo"]) + "</b><br>"
                + "Tipo: " + escape(a["tipo"]) + "<br>"
                + f"Distância: {distancia:,.0f} m<br>"
                + "Elevação do terreno: " + elevacao
            )
            folium.Marker([a["lat"], a["lon"]],
                icon=folium.Icon(color=cor, icon="tint" if cor == "blue" else "signal", prefix="fa"),
                popup=folium.Popup(popup, max_width=360),
                tooltip=f"{a['rotulo']} · {distancia:,.0f} m").add_to(grupo_ativos)
        grupo_ativos.add_to(mapa)

    grupo_medicoes = folium.FeatureGroup(name="Medições do Módulo 1", show=True)
    grupo_rotulos = folium.FeatureGroup(name="Rótulos das medições", show=mostrar_rotulos)
    for r in medicoes.itertuples():
        lat, lon, mca = float(r.lat), float(r.lon), float(r.mca)
        ele = cotas.get((lat, lon))
        elevacao = f"{ele:.1f} m" if ele is not None else "indisponível"
        dt = r.dt.strftime("%d/%m/%Y %H:%M") if pd.notna(r.dt) else str(r.data)
        cor = "#bd343e" if mca <= 5 else "#e39819" if mca <= 10 else "#2c80b7"
        texto = f"{mca:g} mca · {elevacao} · {dt}"
        popup = (
            f"<b>{mca:g} mca</b><br>Elevação do terreno: {escape(elevacao)}"
            f"<br>Data: {escape(dt)}<br>Bairro: {escape(str(r.bairro))}"
            f"<br>Matrícula: {escape(str(r.matricula))}"
        )
        folium.CircleMarker([lat, lon], radius=5, color=cor,
            weight=1, fill=True, fill_color=cor, fill_opacity=.9,
            tooltip=texto, popup=folium.Popup(popup, max_width=330)).add_to(grupo_medicoes)
        _rotulo(grupo_rotulos, lat, lon, texto, cor)
    grupo_medicoes.add_to(mapa)
    grupo_rotulos.add_to(mapa)

    salvos = st.session_state.setdefault("inv_poligonos", [])
    if salvos:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"nome": f"Área {i}"},
                 "geometry": poly} for i, poly in enumerate(salvos, 1)
            ]},
            name="Polígonos da investigação", show=True,
            style_function=lambda _: {"color": "#17846b", "weight": 3,
                                      "fillColor": "#15a889", "fillOpacity": .12},
        ).add_to(mapa)

    Draw(export=False, position="topleft",
         draw_options={"polygon": {"allowIntersection": False, "showArea": True},
                       "rectangle": True, "polyline": False, "circle": False,
                       "circlemarker": False, "marker": False},
         edit_options={"edit": True, "remove": True}).add_to(mapa)
    folium.LayerControl(collapsed=True, position="topright").add_to(mapa)

    st.caption("Desenhe um polígono ou retângulo pelo controle à esquerda do mapa. "
               "Rótulos podem ser ligados/desligados no controle de camadas.")
    evento = st_folium(mapa, height=670, width=None,
        returned_objects=["all_drawings", "last_active_drawing", "bounds", "center", "zoom"],
        key=f"inv_mapa_investigacao_{st.session_state.get('inv_mapa_versao', 0)}")

    if isinstance(evento, dict):
        visao = evento.get("center")
        if isinstance(visao, dict):
            try:
                clat, clon = float(visao["lat"]), float(visao["lng"])
                if _coords_validas(clat, clon):
                    st.session_state["inv_mapa_centro"] = (clat, clon)
            except (ValueError, TypeError, KeyError):
                pass
        zoom = evento.get("zoom")
        if isinstance(zoom, (int, float)) and 1 <= zoom <= 19:
            st.session_state["inv_mapa_zoom"] = int(zoom)
        desenhos = evento.get("all_drawings") or []
        conhecidos = {json.dumps(x, sort_keys=True) for x in salvos}
        for item in desenhos:
            geom = _poligono_desenhado(item)
            if geom is None:
                continue
            chave = json.dumps(geom, sort_keys=True)
            if chave not in conhecidos:
                salvos.append(geom)
                conhecidos.add(chave)
        limites = evento.get("bounds")
        if isinstance(limites, dict) and {"_southWest", "_northEast"}.issubset(limites):
            sw, ne = limites["_southWest"], limites["_northEast"]
            try:
                st.session_state["inv_proxima_vista"] = (
                    float(sw["lng"]), float(sw["lat"]), float(ne["lng"]), float(ne["lat"]))
            except (TypeError, ValueError, KeyError):
                pass

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        titulo = st.text_input("Nome da área investigada", value="Área de influência em investigação",
                               key="inv_titulo")
    with c2:
        if st.button("Desfazer último polígono", disabled=not salvos, use_container_width=True):
            salvos.pop()
            st.session_state["inv_mapa_versao"] = st.session_state.get("inv_mapa_versao", 0) + 1
            st.rerun()
    with c3:
        if st.button("Limpar áreas", disabled=not salvos, use_container_width=True):
            st.session_state["inv_poligonos"] = []
            st.session_state["inv_mapa_versao"] = st.session_state.get("inv_mapa_versao", 0) + 1
            st.rerun()

    st.caption(f"Polígonos registrados nesta sessão: {len(salvos)}. "
               "Desenhos manuais são hipóteses de influência, não delimitações hidráulicas validadas.")
    if salvos:
        arquivo = _kmz_poligonos(salvos, titulo.strip() or "Investigação de área",
                                selecionado["rotulo"] if selecionado else "Não selecionado")
        nome_arquivo = re.sub(r"[^A-Za-z0-9_-]+", "_", _normalizar(titulo).lower()).strip("_") or "area_investigada"
        st.download_button("⬇️ Exportar polígonos em KMZ", data=arquivo,
            file_name=nome_arquivo + ".kmz",
            mime="application/vnd.google-earth.kmz", type="primary")
    else:
        st.info("Após desenhar uma área no mapa, a exportação em KMZ ficará disponível.")

    if mostrar_rede and st.session_state.get("inv_proxima_vista"):
        if st.button("Carregar rede no enquadramento visível", key="inv_atualizar_rede"):
            st.session_state["inv_rede_limites"] = st.session_state["inv_proxima_vista"]
            st.rerun()

    if selecionado:
        with st.expander(f"Equipamentos mais próximos — {len(proximos)} ativos", expanded=False):
            linhas = [{
                "Tipo": a["tipo"], "Ativo": a["nome"], "Identificação": a["codigo"],
                "Distância (m)": round(d, 1), "Elevação do terreno (m)": (
                    round(cotas[(a["lat"], a["lon"])], 1)
                    if (a["lat"], a["lon"]) in cotas else None
                )
            } for a, d in proximos]
            st.dataframe(pd.DataFrame(linhas), hide_index=True, use_container_width=True)
    st.caption(
        f"Medições renderizadas: {len(medicoes)} (limite visual escolhido). "
        "Sem raio operacional: a ordenação por proximidade não significa influência comprovada. "
        "A elevação vem do MDE/DSM, não é a altura piezométrica medida."
    )
