"""Módulo 7 — Relatório Farol acrescido de medições do Módulo 1.
Fontes independentes e acesso somente leitura.
"""
import io
import base64
import json
import math
import re
import os
import shutil
import tempfile
from pathlib import Path
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from html import escape
import xml.etree.ElementTree as ET
from branca.element import Element
from xml.sax.saxutils import escape as xml_escape

import folium
import plotly.express as px
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Image, PageBreak
from reportlab.graphics.shapes import Drawing, Circle, String, Rect, Line, PolyLine
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from streamlit_folium import st_folium
from auth import verificar_autenticacao, tem_acesso_modulo
from farol_graficos_temporarios import chave_logger, filtrar_loggers, rotulo_logger

# Mesma abrangência operacional padrão do Módulo 3 (independente do raio
# usado para formar as concentrações ou associar medições do Módulo 1).
RAIO_OPERACIONAL_EVIDENCIAS = 750
RAIO_CONCENTRACOES_M = 750
RAIO_MEDICOES_M = 750
MINIMO_OS_CONCENTRACAO = 3
RI_BP_COLUNA_PROTOCOLO = "Cod_Protocolo"
FUSO_LOGGERS = ZoneInfo("America/Fortaleza")
ABA_GRAFICOS_DIA = "FAROL_GRAFICOS_DIA"


st.set_page_config(page_title="Relatórios Integrados — COI", page_icon="📊", layout="wide")

from auth import exigir_acesso_modulo as _prisma_exigir_acesso
_prisma_exigir_acesso("7")
if not verificar_autenticacao():
    st.error("Sessão não autenticada. Entre pelo Hub Central.")
    st.stop()
if str(st.session_state.get("perfil", "")).lower().strip() not in {"admin", "usuario", "operador"}:
    st.error("Acesso não autorizado.")
    st.stop()
# Quando o Hub cadastrar uma permissão explícita para o módulo 7, respeitá-la.
# Não exigir a permissão antes da configuração no Hub para não bloquear a implantação inicial.

ID_PRESSAO = str(st.secrets.get("modulo_1_spreadsheet_id", "15iN3YEGyxk3l1ZKaHJJp-BvTfVHqpd7gL1GX3RbAKUU")).strip()
ID_FAROL = str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip()
if not ID_FAROL or ID_FAROL == ID_PRESSAO:
    st.error("Configure farol_producao_spreadsheet_id; as fontes devem ser independentes.")
    st.stop()

@st.cache_resource(show_spinner=False)
def cliente_google():
    conf = st.secrets["gcp_json"]
    if isinstance(conf, str):
        conf = json.loads(conf)
    else:
        conf = dict(conf)
    credentials = Credentials.from_service_account_info(
        conf, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return gspread.authorize(credentials)

@st.cache_data(ttl=120, show_spinner=False)
def ler_base(spreadsheet_id, aba):
    """Uma consulta de valores por fonte, cacheada entre sessões para aliviar quota 429."""
    valores = cliente_google().open_by_key(spreadsheet_id).worksheet(aba).get_all_values()
    if not valores:
        return pd.DataFrame()
    cabe = [str(c).strip() for c in valores[0]]
    return pd.DataFrame([linha[:len(cabe)] + [""] * max(0, len(cabe)-len(linha))
                         for linha in valores[1:] if any(str(v).strip() for v in linha)], columns=cabe)

def normalizar(v):
    t = unicodedata.normalize("NFKD", str(v or "").upper().strip())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())

def unidade_da_cidade_farol(valor):
    """Mesma classificação do Módulo 3: THE, API e TIM não se misturam."""
    cidade = re.sub(r"[^A-Z0-9]", "", normalizar(valor))
    if cidade == "TERESINA":
        return "THE"
    if cidade in {"TIMON", "TIMONMA", "TIMONMARANHAO"}:
        return "TIM"
    return "API"

def flutuante(valor):
    texto = str("" if valor is None else valor).strip().replace(" ", "")
    if not texto or texto.lower() in {"nan", "none", "nat", "-"}:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".") if texto.rfind(",") > texto.rfind(".") else texto.replace(",", "")
    else:
        texto = texto.replace(",", ".")
    try:
        numero = float(texto)
        return numero if math.isfinite(numero) else None
    except ValueError:
        return None

def coordenada(v, tipo):
    num = flutuante(v)
    if num is None or num == 0 or not ( (-90 <= num <= 90) if tipo == "lat" else (-180 <= num <= 180)):
        return None
    return num

def haversine(lat1, lon1, lat2, lon2):
    la1, la2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(math.radians(lon2-lon1)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(a)))

def data_parse(v):
    """Datas do Farol e do M1: dia/mês/ano, ISO, hora opcional e serial Sheets.

    Zero, vazio ou valores não interpretáveis não são convertidos em data fictícia.
    """
    if v is None or (not isinstance(v, (list, tuple, dict)) and pd.isna(v)):
        return pd.NaT
    if isinstance(v, (pd.Timestamp, datetime, date)):
        try:
            return pd.Timestamp(v)
        except (TypeError, ValueError):
            return pd.NaT
    texto = str(v).strip()
    if not texto or texto.lower() in {"nan", "nat", "none", "-"}:
        return pd.NaT
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%d/%m/%y %H:%M", "%d/%m/%y", "%d-%m-%Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d"):
        try:
            return pd.Timestamp(datetime.strptime(texto, fmt))
        except ValueError:
            pass
    # Datas seriadas podem ter vindo de exportações não formatadas.
    try:
        n = float(texto.replace(",", "."))
        if 20000 <= n <= 90000:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(n, unit="D")
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        return pd.to_datetime(texto, dayfirst=True, errors="coerce")
    except (ValueError, TypeError, OverflowError):
        return pd.NaT


def preparar_os(df):
    nomes = ["Nº da O.S", "Matrícula", "Cidade", "Bairro", "Latitude", "Longitude", "Dt. Emissão"]
    faltantes = [c for c in nomes if c not in df.columns]
    if faltantes:
        raise ValueError("Aba PONTOS — colunas ausentes: " + ", ".join(faltantes))
    df = df.copy()
    df["_municipio"] = df["Cidade"].map(normalizar)
    df["_bairro"] = df["Bairro"].map(normalizar)
    df["_lat"] = df["Latitude"].map(lambda v: coordenada(v, "lat"))
    df["_lon"] = df["Longitude"].map(lambda v: coordenada(v, "lon"))
    df["_data"] = pd.to_datetime(df["Dt. Emissão"].map(data_parse),errors="coerce")
    return df

def preparar_pressao(df):
    colunas = ["ID", "Data", "Municipio", "Bairro", "Latitude", "Longitude", "Pressao_MCA", "Observacao", "Matricula"]
    faltantes = [c for c in colunas if c not in df.columns]
    if faltantes:
        raise ValueError("Aba baixa_pressao — colunas ausentes: " + ", ".join(faltantes))
    df = df.copy()
    # A coluna J é opcional em registros antigos; jamais confundir com SAI_ATIVIDADE.
    if RI_BP_COLUNA_PROTOCOLO not in df.columns:
        for alternativa in ("Cód. Protocolo Origem", "Cod. Protocolo Origem",
                            "Codigo_Protocolo", "Código do Protocolo"):
            if alternativa in df.columns:
                df[RI_BP_COLUNA_PROTOCOLO] = df[alternativa]
                break
        else:
            df[RI_BP_COLUNA_PROTOCOLO] = ""
    df[RI_BP_COLUNA_PROTOCOLO] = (
        df[RI_BP_COLUNA_PROTOCOLO].fillna("").astype(str).str.strip()
        .replace({"nan": "", "None": "", "nat": ""})
    )
    df["_municipio"] = df["Municipio"].map(normalizar)
    df["_bairro"] = df["Bairro"].map(normalizar)
    df["_lat"] = df["Latitude"].map(lambda v: coordenada(v, "lat"))
    df["_lon"] = df["Longitude"].map(lambda v: coordenada(v, "lon"))
    df["_data"] = pd.to_datetime(df["Data"].map(data_parse),errors="coerce")
    df["_pressao"] = df["Pressao_MCA"].map(lambda v: (p if (p := flutuante(v)) is not None and p >= 0 else None))
    df["_faixa"] = df["_pressao"].map(faixa_pressao)
    df["_id_medicao"] = [f"BP_{i}" for i in range(len(df))]
    return df

def faixa_pressao(p):
    if p is None or pd.isna(p): return "Sem dado válido"
    if p == 0: return "Sem pressão (0 mca)"
    if 0 < p <= 5: return "Baixa pressão (0–5 mca)"
    if 5 < p <= 10: return "Em atenção (5–10 mca)"
    if 10 < p <= 15: return "Faixa oculta no mapa M1 (10–15 mca)"
    if p > 15: return "Acima de 15 mca"
    return "Dado inválido"

def concentracoes_farol(df, raio_m, minimo):
    """Replica o agrupamento do Farol atual: grade + union-find por unidade.

    O.S. de municípios distintos podem compor o mesmo grupo na API, mas
    THE, API e TIM jamais formam uma concentração única.
    """
    df = df.dropna(subset=["_lat", "_lon"]).reset_index(drop=True).copy()
    if df.empty:
        return pd.DataFrame(), df
    df["_unidade"] = df["Cidade"].map(unidade_da_cidade_farol)
    unidades = df["_unidade"].tolist()
    lat_step = raio_m / 111320.0
    lon_step = raio_m / (111320.0 * max(0.2, math.cos(math.radians(float(df["_lat"].mean())))))
    buckets = {}
    for i, r in df.iterrows():
        celula = (int(math.floor(r["_lat"] / lat_step)), int(math.floor(r["_lon"] / lon_step)))
        buckets.setdefault(celula, []).append(i)
    parent = list(range(len(df)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for (x, y), indices in buckets.items():
        candidatos = [j for dx in (-1,0,1) for dy in (-1,0,1)
                      for j in buckets.get((x+dx,y+dy), [])]
        for i in indices:
            for j in candidatos:
                if j <= i or unidades[i] != unidades[j]:
                    continue
                if haversine(df.at[i,"_lat"],df.at[i,"_lon"],
                             df.at[j,"_lat"],df.at[j,"_lon"]) <= raio_m:
                    union(i,j)
    df["_grupo"] = [find(i) for i in range(len(df))]
    validos = df.groupby("_grupo").size()
    df = df[df["_grupo"].isin(validos[validos >= minimo].index)].copy()
    saida = []
    for grupo, bloco in df.groupby("_grupo"):
        cidades = sorted(set(bloco["_municipio"]) - {""})
        pares = sorted({(str(r["_municipio"]), str(r["_bairro"]))
                        for _,r in bloco.iterrows() if r["_municipio"] and r["_bairro"]})
        saida.append({"_grupo":grupo, "Latitude":float(bloco["_lat"].mean()),
                      "Longitude":float(bloco["_lon"].mean()), "O.S.":len(bloco),
                      "Matrículas":bloco["Matrícula"].replace("",pd.NA).dropna().nunique(),
                      "Município":", ".join(cidades),
                      "Bairros":", ".join(sorted(set(bloco["_bairro"]) - {""})),
                      "_unidade":str(bloco["_unidade"].iloc[0]),
                      "_pares_bairro":pares})
    cdf = pd.DataFrame(saida)
    if cdf.empty:
        return cdf,df
    cdf = cdf.sort_values(["O.S.","Matrículas"],ascending=False).reset_index(drop=True)
    contador={"THE":0,"API":0,"TIM":0}
    ids=[]
    for _,c in cdf.iterrows():
        tipo=c["_unidade"]
        contador[tipo]+=1
        ids.append(f"{tipo} C{contador[tipo]:03d}")
    cdf.insert(0,"Concentração",ids)
    return cdf,df


def relacionar(concs, os_cluster, pressao, raio, inicio, fim):
    """Cruza pontos no MESMO município: proximidade com qualquer O.S. do grupo.

    Critérios mutuamente exclusivos: geográfico, apenas bairro, sem evidência.
    Medições são eventos pontuais, não atestam toda a área ou causa das O.S.
    """
    resultados, geograficos, apenas_bairro = [], [], []
    pressao = (pressao[pressao["_data"].dt.date.between(inicio, fim)].copy()
              if not pressao.empty else pressao.copy())
    for _, c in concs.iterrows():
        grupo = os_cluster[os_cluster["_grupo"] == c["_grupo"]].copy()
        municipios = set(grupo["_municipio"]) - {""}
        pares_bairro = set(c.get("_pares_bairro", []))
        cand = pressao[pressao["_municipio"].isin(municipios)].copy()
        geo_rows, bairro_rows = [], []
        for _, med in cand.iterrows():
            tem_coords = pd.notna(med["_lat"]) and pd.notna(med["_lon"])
            os_mesma_cidade = grupo[grupo["_municipio"] == med["_municipio"]]
            pontos = list(zip(os_mesma_cidade["_lat"], os_mesma_cidade["_lon"]))
            distancia = min((haversine(float(lat),float(lon),float(med["_lat"]),float(med["_lon"]))
                             for lat,lon in pontos), default=float("inf")) if tem_coords else float("inf")
            reg = med.to_dict()
            reg["Concentração"] = c["Concentração"]
            reg["Distância até O.S. (m)"] = round(distancia) if math.isfinite(distancia) else None
            reg["Critério de associação"] = "Proximidade geográfica" if distancia <= raio else "Bairro coincidente (sem proximidade confirmada)"
            if distancia <= raio:
                geo_rows.append(reg)
            elif (med["_municipio"], med["_bairro"]) in pares_bairro:
                bairro_rows.append(reg)
        geos = pd.DataFrame(geo_rows)
        territorial = pd.DataFrame(bairro_rows)
        # A mesma medição não é contada duas vezes dentro de uma concentração.
        n_geo, n_bairro = len(geos), len(territorial)
        avaliados = geos if n_geo else territorial
        valores = pd.to_numeric(avaliados["_pressao"],errors="coerce").dropna() if not avaliados.empty else pd.Series(dtype=float)
        criticos = int((valores <= 5).sum())
        atencao = int(((valores > 5)&(valores <= 10)).sum())
        ocultos = int(((valores > 10)&(valores <= 15)).sum())
        zeros = int((valores == 0).sum())
        if n_geo:
            situacao = ("PRESSÃO CRÍTICA PRÓXIMA" if criticos else "PRESSÃO EM ATENÇÃO PRÓXIMA" if atencao else "MEDIÇÃO PRÓXIMA SEM PRESSÃO CRÍTICA")
            leitura = (f"{n_geo} medição(ões) a até {raio} m de alguma O.S. do grupo; "
                       + (f"{criticos} até 5 mca." if criticos else "nenhuma até 5 mca."))
        elif n_bairro:
            situacao = "INDÍCIO NO MESMO BAIRRO"
            leitura = (f"{n_bairro} medição(ões) no(s) mesmo(s) bairro(s), mas fora do raio de {raio} m "
                       "ou sem coordenadas válidas. Não equivalem a confirmação no local da O.S.")
        else:
            situacao = ""
            leitura = ""
        resultados.append({**c.to_dict(),
            "Medições próximas": n_geo,
            "Medições mesmo bairro": n_bairro,
            "Sem/baixa pressão": criticos,
            "Medições 0 mca": zeros,
            "Em atenção (5–10)": atencao,
            "Faixa 10–15 (oculta no M1)": ocultos,
            "Menor pressão (mca)": float(valores.min()) if len(valores) else None,
            "Classificação indicativa": situacao,
            "Análise operacional": leitura,
            "O.S. do grupo": ", ".join(sorted(set(grupo["Nº da O.S"].astype(str))))})
        geograficos.extend(geo_rows)
        apenas_bairro.extend(bairro_rows)
    return pd.DataFrame(resultados),pd.DataFrame(geograficos),pd.DataFrame(apenas_bairro)


def diagnostico_geral(resumo, pressao, associados, territorial, inicio, fim):
    """Produz achados rastreáveis sem inferir causalidade nem normalidade de pressão."""
    if resumo.empty:
        return ["Nenhuma concentração encontrada com os filtros atuais."],pd.DataFrame()
    geo = resumo[resumo["Medições próximas"] > 0]
    bairro = resumo[(resumo["Medições próximas"] == 0) & (resumo["Medições mesmo bairro"] > 0)]
    lacunas = resumo[(resumo["Medições próximas"] == 0) & (resumo["Medições mesmo bairro"] == 0)]
    achados = [f"{len(resumo)} concentração(ões) de O.S. identificada(s) no período."]
    if len(geo) or len(bairro):
        achados.append(f"{len(geo)} concentração(ões) com medição próxima de alguma O.S.; {len(bairro)} com medição no mesmo bairro, fora do raio confirmado.")
    if len(geo):
        crit = geo[geo["Sem/baixa pressão"]>0]
        if len(crit):
            achados.append(f"{len(crit)} concentração(ões) têm medição de 0 a 5 mca próxima de pelo menos uma O.S.: " + ", ".join(crit["Concentração"].astype(str)) + ". Priorizar verificação operacional.")
        else:
            achados.append("Há medições próximas a O.S., mas nenhuma delas registrou de 0 a 5 mca no período.")
    if len(bairro):
        achados.append("Correspondências apenas por bairro NÃO são prova de que a baixa pressão ocorreu no endereço da reclamação; confirmar localização e janela de medição.")
    # Diagnóstico de cobertura: bairros com medição crítica e nenhuma concentração no mesmo bairro.
    bp = pressao[pressao["_data"].dt.date.between(inicio,fim)].copy()
    bp = bp[bp["_pressao"].notna() & (bp["_pressao"]<=5)]
    municipios_bairros = set()
    for _,r in resumo.iterrows():
        for municipio,bairro in r.get("_pares_bairro", []):
            municipios_bairros.add((municipio,bairro))
    isolados = bp[bp.apply(lambda r:(r["_municipio"],r["_bairro"]) not in municipios_bairros,axis=1)].copy()
    if not isolados.empty:
        locais = sorted({f"{r['_bairro']} ({r['_municipio']})" for _,r in isolados.iterrows() if r['_bairro']})
        achados.append(f"Há {len(isolados)} medição(ões) de até 5 mca em bairro(s) sem concentração identificada pelos critérios atuais" + (": " + ", ".join(locais[:6]) if locais else "") + ".")
    return achados,isolados

def resumo_pressao_por_bairro(pressao):
    """Resumo de aferições, NÃO média de pressão de toda a rede/bairro.

    As 5 faixas são disjuntas. Zero é leitura real e entra no cálculo da média.
    """
    cols=["Município","Bairro","Medições","Matrículas","0 mca","Até 5 mca",
          "5–10 mca","10–15 mca",">15 mca","Mín. (mca)","Média (mca)",
          "Mediana (mca)","Máx. (mca)","% até 5 mca"]
    if pressao is None or pressao.empty:
        return pd.DataFrame(columns=cols)
    base=pressao[pressao["_pressao"].notna()].copy()
    if base.empty:
        return pd.DataFrame(columns=cols)
    linhas=[]
    for (mun,bairro),grupo in base.groupby(["_municipio","_bairro"],dropna=False):
        p=pd.to_numeric(grupo["_pressao"],errors="coerce").dropna()
        if p.empty:continue
        n_crit=int(p.le(5).sum())
        mat=grupo.get("Matricula",pd.Series(dtype=str)).astype(str).str.strip()
        linhas.append({"Município":str(mun or "Não informado"),
          "Bairro":str(bairro or "Não informado"),"Medições":int(len(p)),
          "Matrículas":int(mat[~mat.isin(["","nan","None"])].nunique()),
          "0 mca":int(p.eq(0).sum()),"Até 5 mca":n_crit,
          "5–10 mca":int(((p>5)&(p<=10)).sum()),
          "10–15 mca":int(((p>10)&(p<=15)).sum()),
          ">15 mca":int(p.gt(15).sum()),
          "Mín. (mca)":round(float(p.min()),2),
          "Média (mca)":round(float(p.mean()),2),
          "Mediana (mca)":round(float(p.median()),2),
          "Máx. (mca)":round(float(p.max()),2),
          "% até 5 mca":round(n_crit*100/len(p),1)})
    return (pd.DataFrame(linhas,columns=cols)
            .sort_values(["% até 5 mca","Até 5 mca","Medições","Bairro"],
                         ascending=[False,False,False,True]).reset_index(drop=True))


def associar_pressao_as_os(pressao, os_base):
    """Identifica POSSÍVEL O.S. a partir da matrícula + município + mesma data.

    Sem número da O.S. na base M1, não atribuir uma medição a uma O.S.
    por mera proximidade ou bairro. Não usar vínculos para contagem de pressão.
    """
    if pressao is None:return pd.DataFrame()
    resultado=pressao.copy()
    resultado["O.S. relacionada"]=""
    resultado["_qtd_os_mesma_matricula_dia"]=0
    if resultado.empty or os_base is None or os_base.empty:return resultado
    base=os_base.copy()
    base["_data_dia"]=pd.to_datetime(base["_data"],errors="coerce").dt.date
    base["_matricula_vinculo"]=base["Matrícula"].astype(str).str.strip()
    mapa={}
    for _,os_row in base.iterrows():
        chave=(os_row["_municipio"], os_row["_matricula_vinculo"],os_row["_data_dia"])
        if not chave[0] or not chave[1] or pd.isna(chave[2]):continue
        numero=protocolo_os_relatorio(os_row)
        if numero: mapa.setdefault(chave,set()).add(numero)
    for idx,r in resultado.iterrows():
        mat=str(r.get("Matricula","") or "").strip()
        dt=r.get("_data")
        if not mat or pd.isna(dt):continue
        chave=(r.get("_municipio",""),mat,pd.Timestamp(dt).date())
        os_rel=sorted(mapa.get(chave,set()))
        resultado.at[idx,"_qtd_os_mesma_matricula_dia"]=len(os_rel)
        resultado.at[idx,"O.S. relacionada"]="; ".join(os_rel)
    return resultado


def mapa_exclusivo_pressao(pressao):
    """Mapa independente do M1. Pontos de 10–15 mca ficam ocultos APENAS no mapa."""
    if pressao is None or pressao.empty:return None
    validas=pressao.dropna(subset=["_lat","_lon","_pressao"])
    visiveis=validas[validas.apply(pressao_visivel,axis=1)].copy()
    if visiveis.empty:return None
    lat=float(visiveis["_lat"].mean());lon=float(visiveis["_lon"].mean())
    mapa=folium.Map(location=[lat,lon],zoom_start=13,tiles="OpenStreetMap",
                    control_scale=True,zoom_control=False)
    posicoes=adicionar_pontos_pressao(mapa,visiveis)
    if len(posicoes)>1:
        mapa.fit_bounds(posicoes,padding=(45,45),max_zoom=14)
    from branca.element import Element
    legenda=('<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
      'background:#fff;color:#24364b;border:1px solid #cad4df;border-radius:5px;'
      'padding:8px 11px;font:12px Arial">'
      '<strong>Medições de pressão</strong><br>'
      '<span style="color:#f04452">●</span> 0 a 5 mca &nbsp; '
      '<span style="color:#f2c600">●</span> &gt;5 a 10 mca &nbsp; '
      '<span style="color:#8b27ef">●</span> &gt;15 mca</div>')
    mapa.get_root().html.add_child(Element(legenda))
    return mapa


def achados_baixa_pressao(pressao, bairros, os_vinculadas=None):
    """Achados descritivos auditáveis; pressão pontual não confirma causa nem pressão do bairro."""
    if pressao is None or pressao.empty:return []
    p=pd.to_numeric(pressao["_pressao"],errors="coerce").dropna()
    if p.empty:return []
    n=len(p); baixa=int(p.le(5).sum());zeros=int(p.eq(0).sum())
    achados=[f"Foram analisadas {n} medições válidas em {len(bairros)} bairro(s). "
             f"A pressão média das medições foi de {p.mean():.1f} mca "
             f"(mínima {p.min():g} e máxima {p.max():g} mca).",
             f"{baixa} aferição(ões) ({baixa*100/n:.1f}%) apresentaram até 5 mca, "
             f"incluindo {zeros} leitura(s) de 0 mca."]
    if not bairros.empty:
        relevantes=bairros[bairros["Até 5 mca"]>0].copy()
        if not relevantes.empty:
            ranking=relevantes.sort_values(["Até 5 mca","% até 5 mca"],ascending=False).head(4)
            locais=[f"{r['Bairro']} ({r['Município']}: {r['Até 5 mca']}/{r['Medições']} até 5 mca)" for _,r in ranking.iterrows()]
            achados.append("Bairros que concentram mais aferições críticas: "+"; ".join(locais)+".")
        if (bairros["Medições"]==1).any():
            achados.append("Médias de bairros com apenas uma aferição representam somente aquele ponto de medição.")
    if os_vinculadas is not None and not os_vinculadas.empty:
        qtd=int(os_vinculadas["_qtd_os_mesma_matricula_dia"].gt(0).sum())
        if qtd:achados.append(f"{qtd} medição(ões) possuem O.S. com a mesma matrícula, município e data; "
                              "a correspondência não comprova que a pressão foi aferida na abertura da O.S.")
    achados.append("Médias e incidências resumem apenas os pontos registrados, não a pressão de todo o bairro.")
    return achados


# Cores e faixas iguais nos dashboards do Streamlit e no PDF.
CORES_FAIXAS_PRESSAO = {
    "0 mca": "#e64e62",
    ">5 a 10 mca": "#e6bc34",
    ">0 a 5 mca": "#f3a53a",
    ">10 a 15 mca": "#cf71a9",
    ">15 mca": "#8554b8",
}
FUNDO_GRAFICO_PRESSAO = "#0e1117"


def contagem_faixas_dashboard(valores):
    """Cinco classes mutuamente exclusivas; 0 mca é medição válida."""
    p = pd.to_numeric(valores, errors="coerce").dropna()
    return {
        "0 mca": int(p.eq(0).sum()),
        ">5 a 10 mca": int(((p > 5) & (p <= 10)).sum()),
        ">0 a 5 mca": int(((p > 0) & (p <= 5)).sum()),
        ">10 a 15 mca": int(((p > 10) & (p <= 15)).sum()),
        ">15 mca": int(p.gt(15).sum()),
    }


def _chart_faixas_vetorial(pressao, fonte="Helvetica"):
    """Gráfico de rosca vetorial, com o visual escuro da referência."""
    from reportlab.graphics.shapes import Wedge

    largura, altura = 355, 267
    d = Drawing(largura, altura)
    bg = colors.HexColor(FUNDO_GRAFICO_PRESSAO)
    branco = colors.HexColor("#f7f8fb")
    d.add(Rect(0, 0, largura, altura, fillColor=bg, strokeColor=None))
    d.add(String(13, 241, "Distribuição por faixa de pressão",
                 fontName=fonte, fontSize=11.7, fillColor=branco))

    grupos = contagem_faixas_dashboard(pressao["_pressao"])
    total = sum(grupos.values())
    centro_x, centro_y, raio_externo, raio_interno = 177, 146, 78, 43
    # O ângulo inicial posiciona a primeira faixa do lado direito, como na imagem.
    angulo = -180 * grupos["0 mca"] / max(total, 1)
    for nome, qtd in grupos.items():
        if qtd <= 0 or not total:
            continue
        abertura = 360 * qtd / total
        inicio = angulo
        fim = angulo + min(abertura, 359.999)
        cor = colors.HexColor(CORES_FAIXAS_PRESSAO[nome])
        d.add(Wedge(centro_x, centro_y, raio_externo, inicio, fim,
                    fillColor=cor, strokeColor=None))
        if abertura >= 28:
            ang_medio = math.radians(inicio + abertura / 2)
            rr = (raio_externo + raio_interno) / 2
            texto = f"{100 * qtd / total:.0f}%"
            x = centro_x + rr * math.cos(ang_medio)
            y = centro_y + rr * math.sin(ang_medio) - 4
            largura_texto = pdfmetrics.stringWidth(texto, fonte, 10)
            cor_texto = (bg if nome == ">5 a 10 mca" else branco)
            d.add(String(x - largura_texto / 2, y, texto,
                         fontName=fonte, fontSize=10, fillColor=cor_texto))
        angulo += abertura
    d.add(Circle(centro_x, centro_y, raio_interno, fillColor=bg, strokeColor=None))

    # Duas linhas de legenda evitam fontes minúsculas no PDF A4 paisagem.
    legenda = list(CORES_FAIXAS_PRESSAO.items())
    for i, (nome, hex_cor) in enumerate(legenda):
        linha, coluna = divmod(i, 3)
        x, y = 13 + coluna * 116, 43 - linha * 20
        d.add(Rect(x, y - 1, 9, 9, fillColor=colors.HexColor(hex_cor), strokeColor=None))
        d.add(String(x + 15, y, nome, fontName=fonte,
                     fontSize=7.65, fillColor=branco))
    return d


def _chart_bairros_vetorial(bairros, fonte="Helvetica"):
    """Média por bairro: barras horizontais azuis, maior média no topo."""
    largura, altura = 355, 267
    d = Drawing(largura, altura)
    bg = colors.HexColor(FUNDO_GRAFICO_PRESSAO)
    branco = colors.HexColor("#f7f8fb")
    d.add(Rect(0, 0, largura, altura, fillColor=bg, strokeColor=None))
    d.add(String(12, 241, "Média das aferições por bairro",
                 fontName=fonte, fontSize=11.7, fillColor=branco))

    if bairros is None or bairros.empty:
        d.add(String(18, 132, "Sem aferições no período", fontName=fonte,
                     fontSize=10, fillColor=branco))
        return d

    # Mostra os dez bairros de menor média (prioridade operacional), com
    # barras visualmente ordenadas da maior para a menor, como na referência.
    amostra = (bairros.sort_values(["Média (mca)", "Medições"],
                                   ascending=[True, False]).head(10)
               .sort_values(["Média (mca)", "Medições"],
                            ascending=[False, False]).copy())
    maior = max(float(amostra["Média (mca)"].max()), 0)
    # Limite superior simples, sem distorcer a escala de pressão.
    escala = max(2, int(math.ceil(maior / 2.0) * 2))
    x0, xmax = 148, 343
    largura_grafico = xmax - x0
    y_topo, y_base = 214, 36
    qt = len(amostra)
    passo = min(34, (y_topo - y_base) / max(qt, 1))
    altura_barra = min(19, passo * 0.65)

    for tick in range(0, escala + 1, 2):
        x = x0 + largura_grafico * tick / escala
        d.add(Line(x, 30, x, 225, strokeColor=colors.HexColor("#243040"), strokeWidth=.6))
        texto = str(tick)
        d.add(String(x - pdfmetrics.stringWidth(texto, fonte, 7.4) / 2,
                     16, texto, fontName=fonte, fontSize=7.4,
                     fillColor=colors.HexColor("#e1e7f0")))
    d.add(String(238, 3, "Média (mca)", fontName=fonte, fontSize=8,
                 fillColor=branco))

    for i, (_, r) in enumerate(amostra.iterrows()):
        y = y_topo - i * passo
        nome = f"{r['Bairro']} / {r['Município']}".upper()
        original = nome
        tamanho = 7.0
        while len(nome) > 4 and pdfmetrics.stringWidth(nome, fonte, tamanho) > 137:
            nome = nome[:-1]
        if nome != original:
            nome = nome.rstrip() + "…"
        d.add(String(7, y - 3, nome, fontName=fonte,
                     fontSize=tamanho, fillColor=branco))
        media = max(0.0, float(r["Média (mca)"]))
        if media > 0:
            d.add(Rect(x0, y - altura_barra / 2, largura_grafico * media / escala,
                       altura_barra, fillColor=colors.HexColor("#327cb7"), strokeColor=None))
    return d


def diagnostico_pressao_independente(pressao, resumo_bairros, associados, territoriais):
    """Diagnóstico apoiado em todas as leituras, não apenas nas ligadas a O.S."""
    if pressao is None or pressao.empty:
        return []
    total = len(pressao)
    p = pd.to_numeric(pressao["_pressao"], errors="coerce").dropna()
    if p.empty:
        return []
    n_zero = int((p == 0).sum())
    n_baixa = int(((p > 0) & (p <= 5)).sum())
    n_atencao = int(((p > 5) & (p <= 10)).sum())
    n_oculta = int(((p > 10) & (p <= 15)).sum())
    n_alta = int((p > 15).sum())
    achados = [
        f"O Módulo 1 registrou {total} medição(ões) no período e unidade selecionados, "
        f"em {len(resumo_bairros)} bairro(s), independentemente das concentrações do Farol.",
        f"Distribuição: {n_zero} com 0 mca; {n_baixa} acima de 0 até 5 mca; "
        f"{n_atencao} acima de 5 até 10 mca; {n_oculta} acima de 10 até 15 mca "
        f"(faixa que não aparece no mapa); {n_alta} acima de 15 mca.",
    ]
    criticos=resumo_bairros[resumo_bairros["Até 5 mca"]>0]
    if not criticos.empty:
        nomes=[f"{r['Bairro']} ({r['Município']})" for _,r in criticos.head(5).iterrows()]
        achados.append("Prioridade de verificação da pressão (0–5 mca): "
                       + ", ".join(nomes) + ". A medição é pontual e não estabelece a causa.")
    relacionadas=set()
    for tabela in (associados, territoriais):
        if tabela is not None and not tabela.empty and "_id_medicao" in tabela:
            relacionadas.update(tabela["_id_medicao"].astype(str))
    q_sem_relacao = int((~pressao["_id_medicao"].astype(str).isin(relacionadas)).sum())
    if q_sem_relacao:
        achados.append(f"{q_sem_relacao} medição(ões) não estão vinculadas a nenhuma "
                       "concentração pela proximidade ou coincidência de bairro; continuam "
                       "representadas no panorama de pressão.")
    return achados


def mapa_integrado(concs, associados, raio):
    if concs.empty: return None
    mapa=folium.Map(location=[float(concs["Latitude"].mean()),float(concs["Longitude"].mean())],zoom_start=12,tiles="OpenStreetMap")
    for _,c in concs.iterrows():
        folium.Circle(location=[c["Latitude"],c["Longitude"]],radius=raio,color="#154c78",weight=2,fill=True,fill_opacity=.08).add_to(mapa)
        folium.Marker(location=[c["Latitude"],c["Longitude"]],tooltip=f"{c['Concentração']} — {c['O.S.']} O.S.",
            popup=folium.Popup(escape(str(c["Bairros"])),max_width=280),icon=folium.Icon(color="blue",icon="info-sign")).add_to(mapa)
    for _,r in associados.iterrows():
        # Espelha a regra visual do Módulo 1: pontos entre 10 e 15 mca não aparecem.
        if pd.notna(r["_pressao"]) and 10 < r["_pressao"] <= 15:
            continue
        cor=("#dc3545" if r["_pressao"] is not None and pd.notna(r["_pressao"]) and r["_pressao"]<=5 else
             "#ffbd22" if r["_pressao"] is not None and pd.notna(r["_pressao"]) and r["_pressao"]<=10 else
             "#989898")
        folium.CircleMarker(location=[r["_lat"],r["_lon"]],radius=5,color=cor,fill=True,fill_opacity=.9,
            tooltip=f"{r['Concentração']} · {r['_pressao']} mca · {r['Data']}").add_to(mapa)
    return mapa

def imagem_folium(mapa, largura=1100, altura=650):
    """Captura cartografia real como o Farol; não substitui o mapa por gráfico fictício."""
    from playwright.sync_api import sync_playwright
    html = mapa.get_root().render().replace('</head>',
        f'<style>html,body{{margin:0;width:{largura}px;height:{altura}px;overflow:hidden}}'
        f'.folium-map{{width:{largura}px !important;height:{altura}px !important}}</style></head>',1)
    with tempfile.TemporaryDirectory(prefix="relatorio_coi_") as diretorio:
        caminho = Path(diretorio)/"mapa.html"
        caminho.write_text(html,encoding="utf-8")
        with sync_playwright() as playwright:
            exe=(os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
                 or shutil.which("chromium-browser") or shutil.which("google-chrome"))
            opcoes={"headless":True,"args":["--no-sandbox","--disable-dev-shm-usage"]}
            if exe: opcoes["executable_path"]=exe
            browser=playwright.chromium.launch(**opcoes)
            try:
                pg=browser.new_page(viewport={"width":largura,"height":altura},device_scale_factor=1.2)
                pg.goto(caminho.as_uri(),wait_until="load",timeout=45000)
                pg.wait_for_function("typeof L !== 'undefined' && document.querySelector('.leaflet-map-pane') !== null",timeout=30000)
                pg.wait_for_timeout(2200)
                if pg.locator('.leaflet-tile-loaded').count()==0:
                    raise RuntimeError("O mapa-base não carregou. Verifique a rede do Chromium.")
                return pg.locator('.folium-map').first.screenshot(type="png")
            finally: browser.close()


def mapa_ficha(c, grupo, proximas, raio):
    centro=[float(c["Latitude"]),float(c["Longitude"])]
    mapa=folium.Map(location=centro,zoom_start=14,tiles="OpenStreetMap",control_scale=True)
    folium.Circle(location=centro,radius=raio,color="#164d85",fill=True,fill_opacity=.05).add_to(mapa)
    coords=[]
    for _,os_row in grupo.iterrows():
        lat,lon=os_row.get("_lat"),os_row.get("_lon")
        if pd.notna(lat) and pd.notna(lon):
            coords.append([float(lat),float(lon)])
            folium.CircleMarker([lat,lon],radius=5,color="#b51d35",fill=True,fill_opacity=.85,
                tooltip="O.S.: "+str(os_row.get("Nº da O.S",""))).add_to(mapa)
    for _,med in proximas.iterrows():
        press=med.get("_pressao")
        if pd.isna(press) or (10<float(press)<=15):continue
        lat,lon=med.get("_lat"),med.get("_lon")
        if pd.isna(lat) or pd.isna(lon):continue
        coords.append([float(lat),float(lon)])
        cor="#d21d47" if press<=5 else "#e5b700" if press<=10 else "#8024d6"
        folium.CircleMarker([lat,lon],radius=7,color=cor,weight=2,fill=True,fill_opacity=.85,
           tooltip=f"Pressão {press:g} mca · {med.get('Bairro','')}").add_to(mapa)
    if coords:
        if len(coords)>1: mapa.fit_bounds(coords,padding=(55,55))
    return mapa


# ============================================================
# CARTOGRAFIA SANEAR E MAPAS DO MÓDULO 1 — USADOS APENAS PELO PDF
# ============================================================
def ri_bp_encontrar_kml_sanear():
    """Localiza o KML no repositório (módulo na raiz ou em pages/)."""
    relativo = Path("geodados") / "areas_influencia" / "sanear.kml"
    base = Path(__file__).resolve().parent
    candidatos = (base / relativo, base.parent / relativo, Path.cwd() / relativo)
    for caminho in candidatos:
        if caminho.is_file():
            return caminho
    return candidatos[0]


@st.cache_data(show_spinner=False)
def ri_bp_carregar_poligonos_sanear(caminho: str, versao: int):
    """Lê polígonos KML locais em GeoJSON, sem dependências geoespaciais extras.

    O KML guarda os vértices em longitude/latitude. GeoJSON utiliza a mesma
    ordem, enquanto o Leaflet se encarrega da projeção no mapa.
    `versao` recebe o mtime_ns para invalidar o cache ao atualizar o arquivo.
    """
    _ = versao
    raiz = ET.parse(caminho).getroot()
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    placas = raiz.findall(".//k:Placemark", ns)
    if not placas:
        placas = raiz.findall(".//Placemark")

    def ler_anel(elemento):
        if elemento is None:
            return None
        txt = elemento.findtext(".//k:coordinates", default="", namespaces=ns)
        if not txt:
            txt = elemento.findtext(".//coordinates", default="")
        pontos = []
        for item in txt.split():
            valores = item.split(",")
            if len(valores) < 2:
                continue
            try:
                longitude = float(valores[0])
                latitude = float(valores[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(longitude) and math.isfinite(latitude):
                pontos.append([longitude, latitude])
        if len(pontos) < 3:
            return None
        if pontos[0] != pontos[-1]:
            pontos.append(pontos[0])
        return pontos if len(pontos) >= 4 else None

    feicoes = []
    for placa in placas:
        dados = {}
        for item in placa.findall(".//k:SimpleData", ns):
            dados[item.get("name", "")] = (item.text or "").strip()
        if not dados:
            for item in placa.findall(".//SimpleData"):
                dados[item.get("name", "")] = (item.text or "").strip()
        nome_placa = placa.findtext("k:name", default="", namespaces=ns) or ""
        atributos = {
            "Área": dados.get("NOME") or nome_placa or "Não identificada",
            "Bacia": dados.get("BACIA") or "Não informada",
            "Município": dados.get("MUNICIPIO") or "Não informado",
            "Empresa": dados.get("EMPRESA") or "Não informada",
        }
        poligonos = placa.findall(".//k:Polygon", ns)
        if not poligonos:
            poligonos = placa.findall(".//Polygon")
        for poligono in poligonos:
            exterior = poligono.find("k:outerBoundaryIs", ns)
            if exterior is None:
                exterior = poligono.find("outerBoundaryIs")
            anel_externo = ler_anel(exterior)
            if not anel_externo:
                continue
            aneis = [anel_externo]
            interiores = poligono.findall("k:innerBoundaryIs", ns)
            if not interiores:
                interiores = poligono.findall("innerBoundaryIs")
            for interior in interiores:
                anel = ler_anel(interior)
                if anel:
                    aneis.append(anel)
            feicoes.append({
                "type": "Feature",
                "properties": atributos.copy(),
                "geometry": {"type": "Polygon", "coordinates": aneis},
            })

    return {"type": "FeatureCollection", "features": feicoes}


def ri_bp_mapa(base):
    """Mapa de pontos, incluindo também as medições entre 10 e 15 MCA."""
    validos=base.dropna(subset=["Latitude","Longitude","_pressao"]).copy()
    if validos.empty:return None
    mapa=folium.Map(location=[float(validos["Latitude"].mean()),float(validos["Longitude"].mean())],
        zoom_start=12,tiles="OpenStreetMap",control_scale=True,zoom_control=False)
    posicoes=[]
    for _,r in validos.iterrows():
        lat=float(r["Latitude"]);lon=float(r["Longitude"])
        if not (-90<=lat<=90 and -180<=lon<=180):continue
        press=float(r["_pressao"])
        cor="#e64e62" if press==0 else "#f3a53a" if press<=5 else "#e6bc34" if press<=10 else "#cf71a9" if press<=15 else "#8554b8"
        pop=(f"<b>{escape(str(r['Bairro']))} / {escape(str(r['Municipio']))}</b><br>"
             f"Pressão: <b>{press:g} mca</b><br>"
             f"Data: {escape(str(r['Data']))}<br>"
             f"Matrícula: {escape(str(r.get('Matricula','')))}")
        folium.CircleMarker([lat,lon],radius=7,color="#ffffff",weight=1.3,
            fill=True,fill_color=cor,fill_opacity=.97,
            tooltip=f"{r['Bairro']}: {press:g} mca",popup=folium.Popup(pop,max_width=360)).add_to(mapa)
        posicoes.append((lat,lon))
    if not posicoes:return None
    if len(posicoes)>1:mapa.fit_bounds(posicoes,padding=(45,45),max_zoom=14)
    from branca.element import Element
    mapa.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
        'background:white;padding:8px 11px;border:1px solid #ccd5e0;border-radius:5px;'
        'font:12px Arial;color:#24364b"><b>Medições de pressão</b><br>'
        '<span style="color:#e64e62">●</span> 0 mca &nbsp; '
        '<span style="color:#f3a53a">●</span> &gt;0–5 &nbsp; '
        '<span style="color:#e6bc34">●</span> &gt;5–10 &nbsp; '
        '<span style="color:#cf71a9">●</span> &gt;10–15 &nbsp; '
        '<span style="color:#8554b8">●</span> &gt;15 mca</div>'))
    return mapa


def ri_bp_mapa_fallback(base, largura=1280, altura=650):
    """Plano geográfico esquemático com coordenadas reais caso Chromium/tiles falhem.

    Usa somente coordenadas dos pontos selecionados; jamais simula ruas/mapa-base.
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont
    pontos=base.dropna(subset=["Latitude","Longitude","_pressao"]).copy()
    imagem=PILImage.new("RGB",(largura,altura),"#f1f5fa")
    desenho=ImageDraw.Draw(imagem)
    try:
        font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",16)
        forte=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",19)
    except OSError:
        font=ImageFont.load_default(); forte=font
    desenho.text((30,19),"LOCALIZAÇÃO DAS MEDIÇÕES — MAPA-BASE INDISPONÍVEL",font=forte,fill="#193c65")
    desenho.text((30,50),"Representação por coordenadas geográficas; sem ruas ou limites cartográficos",font=font,fill="#60748b")
    x0,y0,x1,y1=55,105,largura-55,altura-100
    desenho.rectangle((x0,y0,x1,y1),outline="#a1b3c5",width=2,fill="#ffffff")
    if not pontos.empty:
        lats=pontos["Latitude"].astype(float).tolist()
        lons=pontos["Longitude"].astype(float).tolist()
        lat_c=(min(lats)+max(lats))/2
        # Pondera o eixo horizontal para aproximar escalas lineares em metros.
        corrigir=max(.2,math.cos(math.radians(lat_c)))
        xs=[v*corrigir for v in lons]
        minx,maxx=min(xs),max(xs);miny,maxy=min(lats),max(lats)
        meio_x=(minx+maxx)/2; meio_y=(miny+maxy)/2
        spanx=max(maxx-minx,.02);spany=max(maxy-miny,.02)
        span=max(spanx/(x1-x0),spany/(y1-y0))*1.18
        for idx,(_,r) in enumerate(pontos.iterrows()):
            px=(x0+x1)/2+((float(r["Longitude"])*corrigir-meio_x)/span)
            py=(y0+y1)/2-((float(r["Latitude"])-meio_y)/span)
            press=float(r["_pressao"])
            cor="#e64e62" if press==0 else "#f3a53a" if press<=5 else "#e6bc34" if press<=10 else "#cf71a9" if press<=15 else "#8554b8"
            raio=9
            desenho.ellipse((px-raio,py-raio,px+raio,py+raio),fill=cor,outline="#ffffff",width=2)
    legenda=[("#e64e62","0 mca"),("#f3a53a",">0–5 mca"),
             ("#e6bc34",">5–10 mca"),("#cf71a9",">10–15 mca"),("#8554b8",">15 mca")]
    for j,(cor,rotulo) in enumerate(legenda):
        ex=60+j*230
        desenho.ellipse((ex,altura-63,ex+14,altura-49),fill=cor)
        desenho.text((ex+22,altura-65),rotulo,font=font,fill="#193c65")
    buffer_img=io.BytesIO(); imagem.save(buffer_img,format="PNG")
    return buffer_img.getvalue()



def ri_bp_ponto_no_anel(lon, lat, anel):
    """Ray casting lon/lat; bordas do anel são consideradas incluídas."""
    dentro = False
    for i in range(len(anel) - 1):
        ax, ay = anel[i][0], anel[i][1]
        bx, by = anel[i+1][0], anel[i+1][1]
        dx, dy = bx - ax, by - ay
        cruz = (lon - ax) * dy - (lat - ay) * dx
        if (abs(cruz) <= 1e-11 and
                min(ax,bx)-1e-11 <= lon <= max(ax,bx)+1e-11 and
                min(ay,by)-1e-11 <= lat <= max(ay,by)+1e-11):
            return True
        if (ay > lat) != (by > lat):
            intercepto = ax + (lat-ay) * dx / dy
            if lon < intercepto:
                dentro = not dentro
    return dentro


def ri_bp_ponto_na_geometria(lon, lat, geometria):
    """Polígonos com furos e multipolígonos; sem buffers ou inferências por bairro."""
    tipo = geometria.get("type", "")
    conjuntos = geometria.get("coordinates", [])
    if tipo == "Polygon":
        conjuntos = [conjuntos]
    elif tipo != "MultiPolygon":
        return False
    for aneis in conjuntos:
        if not aneis or not ri_bp_ponto_no_anel(lon, lat, aneis[0]):
            continue
        if not any(ri_bp_ponto_no_anel(lon, lat, furo) for furo in aneis[1:]):
            return True
    return False


def ri_bp_selecionar_sanear(base, geojson):
    """Retorna pontos no Sanear e índices das feições correspondentes.

    Um registro será listado uma única vez mesmo ao interceptar polígonos
    sobrepostos; todas as áreas correspondentes são desenhadas no mapa.
    """
    feicoes = geojson.get("features", [])
    limites = []
    for feat in feicoes:
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        if geom.get("type") == "Polygon":
            exteriores = [coords[0]] if coords else []
        elif geom.get("type") == "MultiPolygon":
            exteriores = [pol[0] for pol in coords if pol]
        else:
            exteriores = []
        pontos = [p for anel in exteriores for p in anel]
        if not pontos:
            limites.append(None)
            continue
        limites.append((min(p[0] for p in pontos), min(p[1] for p in pontos),
                        max(p[0] for p in pontos), max(p[1] for p in pontos)))
    selecionados = []
    feicoes_usadas = set()
    for pos_linha, (_, registro) in enumerate(base.iterrows()):
        try:
            lat = float(registro["Latitude"])
            lon = float(registro["Longitude"])
        except (ValueError, TypeError, KeyError):
            continue
        if not math.isfinite(lat) or not math.isfinite(lon):
            continue
        acertos = []
        for pos, (feat, lim) in enumerate(zip(feicoes, limites)):
            if lim is None or not (lim[0] <= lon <= lim[2] and lim[1] <= lat <= lim[3]):
                continue
            if ri_bp_ponto_na_geometria(lon, lat, feat.get("geometry", {})):
                acertos.append(pos)
        if acertos:
            selecionados.append(pos_linha)
            feicoes_usadas.update(acertos)
    # Seleção POSICIONAL evita multiplicar registros se o DataFrame tem
    # índices de origem repetidos após concatenações/transformações.
    return base.iloc[selecionados].copy(), sorted(feicoes_usadas)


def ri_bp_mapa_sanear(pontos, geojson, indices):
    """Mapa do anexo Sanear: feições reais + posições das medições associadas."""
    feicoes = [geojson["features"][i] for i in indices]
    coords = []
    for feat in feicoes:
        geom = feat["geometry"]
        rings = ([geom["coordinates"]] if geom["type"] == "Polygon"
                 else geom["coordinates"] if geom["type"] == "MultiPolygon" else [])
        for pol in rings:
            for lon, lat, *resto in pol[0]:
                coords.append((lat, lon))
    for _, r in pontos.iterrows():
        coords.append((float(r["Latitude"]), float(r["Longitude"])))
    if not coords:
        return None
    mapa = folium.Map(location=[sum(p[0] for p in coords)/len(coords),
                                 sum(p[1] for p in coords)/len(coords)],
                       tiles="OpenStreetMap", zoom_start=13,
                       control_scale=True, zoom_control=False)
    folium.GeoJson({"type": "FeatureCollection", "features": feicoes},
        name="Polígonos Sanear com medições",
        style_function=lambda _: {"color": "#087e73", "weight": 3,
                                  "fillColor": "#2DD4BF", "fillOpacity": 0.22},
        tooltip=folium.GeoJsonTooltip(fields=["Área", "Bacia"],
                    aliases=["Área Sanear:", "Bacia:"])).add_to(mapa)
    for _, r in pontos.iterrows():
        press = float(r["_pressao"])
        cor = ("#e64e62" if press == 0 else "#f3a53a" if press <= 5
               else "#e6bc34" if press <= 10 else "#cf71a9"
               if press <= 15 else "#8554b8")
        protocolo = str(r.get(RI_BP_COLUNA_PROTOCOLO, "") or "").strip() or "Não informado"
        msg = (f"<b>{escape(str(r.get('Bairro','')))}</b><br>"
               f"Matrícula: {escape(str(r.get('Matricula','')))}<br>"
               f"Cód. protocolo: {escape(protocolo)}<br>"
               f"Medição: {press:g} mca")
        folium.CircleMarker([float(r["Latitude"]), float(r["Longitude"])],
             radius=7, color="white", weight=1.6, fill=True,
             fill_color=cor, fill_opacity=1,
             popup=folium.Popup(msg, max_width=320),
             tooltip=f"{press:g} mca").add_to(mapa)
    if len(coords) > 1:
        mapa.fit_bounds([[min(p[0] for p in coords), min(p[1] for p in coords)],
                         [max(p[0] for p in coords), max(p[1] for p in coords)]],
                         padding=(38, 38), max_zoom=15)
    mapa.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
        'background:#fff;padding:8px 11px;border:1px solid #ccd5e0;'
        'border-radius:5px;font:12px Arial;color:#24364b">'
        '<b>Sanear — medições nas áreas</b><br>'
        '<span style="color:#087e73">▰</span> Polígono Sanear &nbsp; '
        '<span style="color:#e64e62">●</span> 0 mca &nbsp; '
        '<span style="color:#f3a53a">●</span> &gt;0–5 &nbsp; '
        '<span style="color:#e6bc34">●</span> &gt;5–10 &nbsp; '
        '<span style="color:#cf71a9">●</span> &gt;10–15 &nbsp; '
        '<span style="color:#8554b8">●</span> &gt;15 mca</div>'))
    return mapa


def ri_bp_fallback_sanear(pontos, geojson, indices, largura=1280, altura=650):
    """Alternativa sem mapa-base, com poligonais e pontos em coordenadas reais."""
    from PIL import Image as PILImage, ImageDraw, ImageFont
    imagem = PILImage.new("RGB", (largura, altura), "#eef3f8")
    pincel = ImageDraw.Draw(imagem)
    try:
        fonte = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        negrito = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 19)
    except OSError:
        fonte = ImageFont.load_default()
        negrito = fonte
    pincel.text((35, 18), "SANEAR — LOCALIZAÇÃO DAS MEDIÇÕES", font=negrito, fill="#193c65")
    pincel.text((35, 49), "Mapa geográfico esquemático, sem ruas; posições e limites reais", font=fonte, fill="#60748b")
    shapes = []
    poligonos = [geojson["features"][i]["geometry"] for i in indices]
    for geom in poligonos:
        coords = geom.get("coordinates", [])
        shapes.extend([coords] if geom.get("type") == "Polygon" else coords if geom.get("type") == "MultiPolygon" else [])
    vertices = [(lon, lat) for pol in shapes for anel in pol for lon, lat, *rest in anel]
    vertices.extend((float(r["Longitude"]),float(r["Latitude"])) for _, r in pontos.iterrows())
    if vertices:
        lat_c = sum(lat for _, lat in vertices)/len(vertices)
        escala_x = max(0.2, math.cos(math.radians(lat_c)))
        valores_x = [x*escala_x for x,y in vertices]
        valores_y = [y for x,y in vertices]
        minx,maxx,miny,maxy = min(valores_x),max(valores_x),min(valores_y),max(valores_y)
        mx,my = (minx+maxx)/2, (miny+maxy)/2
        x0,y0,x1,y1 = 60, 95, largura-60, altura-92
        extensao = max((maxx-minx)/(x1-x0), (maxy-miny)/(y1-y0), 1e-8)*1.15
        def pixel(lon,lat):
            return (round((x0+x1)/2+(lon*escala_x-mx)/extensao),
                    round((y0+y1)/2-(lat-my)/extensao))
        pincel.rectangle((x0,y0,x1,y1),outline="#c2d1df",fill="white",width=2)
        for pol in shapes:
            if not pol:
                continue
            pincel.polygon([pixel(*pt[:2]) for pt in pol[0]],
                          fill="#bceae5", outline="#087e73", width=3)
            for furo in pol[1:]:
                pincel.polygon([pixel(*pt[:2]) for pt in furo],
                              fill="#ffffff", outline="#087e73", width=2)
        for _, r in pontos.iterrows():
            x,y=pixel(float(r["Longitude"]),float(r["Latitude"]))
            press=float(r["_pressao"])
            cor=("#e64e62" if press==0 else "#f3a53a" if press<=5
                 else "#e6bc34" if press<=10 else "#cf71a9" if press<=15 else "#8554b8")
            pincel.ellipse((x-8,y-8,x+8,y+8),fill=cor,outline="white",width=3)
    pincel.text((42, altura-58), "Polígonos Sanear: verde-água  |  Marcadores: medições (cores por faixa de pressão)",
                 font=fonte,fill="#193c65")
    buf=io.BytesIO()
    imagem.save(buf,format="PNG")
    return buf.getvalue()


# ============================================================
# RELATÓRIO NO PADRÃO FAROL — CONTEÚDO ORIGINAL + PRESSÃO
# ============================================================
@st.cache_data(ttl=120, show_spinner=False)
def carregar_contexto_farol():
    """Respeita fontes do Farol: EVENTOS no Sheets; poços e loggers nos CSV do GitHub.

    Outras abas de acompanhamento são lidas em lote. O relatório é somente leitura.
    """
    def opcional(identificador, aba):
        try:
            return ler_base(identificador, aba)
        except gspread.exceptions.WorksheetNotFound:
            return pd.DataFrame()

    contexto = {"EVENTOS": opcional(ID_FAROL, "EVENTOS")}
    raiz = Path(__file__).resolve().parent.parent
    for aba, nome_arquivo in (("POCOS", "pocos.csv"), ("LOGGERS", "loggers.csv")):
        caminho = raiz / "dados" / nome_arquivo
        if caminho.is_file():
            try:
                contexto[aba] = pd.read_csv(caminho, dtype=str, keep_default_na=False,
                                            encoding="utf-8-sig", sep=None, engine="python")
            except Exception as erro:
                raise ValueError(f"Não foi possível ler dados/{nome_arquivo}: {erro}") from erro
        else:
            # Compatibilidade com instalações nas quais o cadastro ainda está no Sheets.
            contexto[aba] = opcional(ID_FAROL, aba)
    contexto.update({"vinculos_farol_producao": pd.DataFrame(),
                      "eventos_acomp": pd.DataFrame(), "movimentacoes": pd.DataFrame()})
    id_acomp = str(st.secrets.get("acompanhamento_operacional_spreadsheet_id", "")).strip()
    if id_acomp and id_acomp != ID_FAROL:
        for aba in ("vinculos_farol_producao", "eventos"):
            destino = "eventos_acomp" if aba == "eventos" else aba
            contexto[destino] = opcional(id_acomp, aba)
    return contexto


def pressao_visivel(m):
    v = m.get("_pressao")
    return pd.notna(v) and not (10 < float(v) <= 15)


def ri_bp_observacao_visivel(valor):
    """Apresentação: remove o marcador técnico, sem modificar o Sheets."""
    texto = str(valor if valor is not None else "").strip()
    if texto.lower() in ("", "nan", "nat", "none"):
        return ""
    texto = re.sub(r"\bSAI_ATIVIDADE\s*=\s*[A-Z0-9_-]+", "", texto, flags=re.I)
    return texto.strip(" \t\r\n|;,/-")


def adicionar_pontos_pressao(mapa, pressao, pontos_limiter=None):
    """Pontos medidos sobrepostos às concentrações, mesmo sem relação direta com O.S.

    Apenas 10 < pressão <= 15 fica oculto, conforme a regra do Módulo 1.
    """
    if pressao is None or pressao.empty:
        return []
    camada = folium.FeatureGroup(name="Medições de pressão (Módulo 1)", show=True)
    posicoes = []
    for _, med in pressao.iterrows():
        lat, lon = med.get("_lat"), med.get("_lon")
        if pd.isna(lat) or pd.isna(lon) or not pressao_visivel(med):
            continue
        lat, lon = float(lat), float(lon)
        valor = float(med["_pressao"])
        cor = "#f04452" if valor <= 5 else "#f2c600" if valor <= 10 else "#8b27ef"
        obs_visivel = ri_bp_observacao_visivel(med.get("Observacao", ""))
        popup = (f"<b>{escape(str(med.get('Bairro', '')))}</b><br>"
                 f"Pressão aferida: <b>{valor:g} mca</b><br>"
                 f"Data: {escape(str(med.get('Data','')))}<br>"
                 f"Matrícula: {escape(str(med.get('Matricula','')))}")
        if obs_visivel:
            popup += f"<br>Observação: {escape(obs_visivel)}"
        folium.CircleMarker([lat,lon],radius=7,color="#ffffff",weight=1.6,
            fill=True,fill_color=cor,fill_opacity=.98,
            tooltip=f"Pressão {valor:g} mca — {med.get('Bairro','')}",
            popup=folium.Popup(popup,max_width=350)).add_to(camada)
        posicoes.append([lat,lon])
    if posicoes:
        camada.add_to(mapa)
    return posicoes


def marcador_concentracao(c):
    """Alvo roxo do Farol, distinto dos pontos de aferição do Módulo 1."""
    nome=escape(str(c.get("Concentração", "")))
    icon=folium.DivIcon(icon_size=(26,26),icon_anchor=(13,13),html=(
        '<div style="width:24px;height:24px;border:4px solid #8a24bf;'
        'background:#ffffff;border-radius:50%;box-shadow:0 1px 5px #0006;'
        'box-sizing:border-box;display:flex;align-items:center;justify-content:center;">'
        '<div style="width:7px;height:7px;background:#8a24bf;border-radius:50%"></div>'
        '</div>'))
    return folium.Marker([float(c["Latitude"]),float(c["Longitude"])],
            icon=icon,tooltip=f"{nome} — {int(c['O.S.'])} O.S.")


def adicionar_legenda_integrada(mapa, tem_pressao):
    itens='<span style="color:#8729ad">●</span> Concentrações&nbsp;&nbsp; <span style="color:#1877d2">●</span> O.S.'
    if tem_pressao:
        itens+='&nbsp;&nbsp; <span style="color:#f04452">●</span> 0–5 mca&nbsp;&nbsp; <span style="color:#d0a700">●</span> 5–10 mca&nbsp;&nbsp; <span style="color:#8b27ef">●</span> &gt;15 mca'
    from branca.element import Element
    mapa.get_root().html.add_child(Element(
        '<div style="position:fixed;bottom:12px;left:12px;z-index:9999;'
        'background:rgba(255,255,255,.96);border:1px solid #bdc9d4;'
        'padding:6px 9px;border-radius:5px;font:11px Arial;color:#22354a">'
        +itens+'</div>'))


def adicionar_ativos_eventos_mapa(mapa, contexto, municipio=None, centros=None, raio=500, rotulos=False):
    """Usa os CSV de poços/loggers do Farol; limita os equipamentos aos centros."""
    for tipo, icone in (("POCOS", "tint"), ("LOGGERS", "signal")):
        base = contexto.get(tipo, pd.DataFrame())
        if base is None or base.empty:
            continue
        camada = folium.FeatureGroup(name="Ativos — " + ("Poços" if tipo=="POCOS" else "Loggers"), show=True)
        for _, item in base.iterrows():
            mun = normalizar(item.get("MUNICIPIO",item.get("Município","")))
            if municipio and mun and mun != normalizar(municipio):
                continue
            lat=coordenada(item.get("LATITUDE"),"lat")
            lon=coordenada(item.get("LONGITUDE"),"lon")
            if lat is None or lon is None:
                continue
            if centros and not any(haversine(lat,lon,y,x)<=raio for y,x in centros):
                continue
            nome=str(item.get("IDENTIFICACAO_ATIVO","") or item.get("ID_POCO","") or item.get("ID_LOGGER","") or "Ativo")
            nome_sec=str(item.get("NOME_POCO","") or item.get("ENDERECO", ""))
            folium.Marker([lat,lon],icon=folium.Icon(color="orange",icon=icone,prefix="fa"),
                tooltip=f"{nome} — {nome_sec}").add_to(camada)
            if rotulos:
                folium.Marker([lat,lon],icon=folium.DivIcon(icon_size=(155,20),icon_anchor=(-8,24),
                    html='<div style="display:inline-block;white-space:nowrap;'
                         'background:#ffffffed;border:1px solid #b8c4d0;'
                         'padding:2px 4px;border-radius:3px;font:bold 10px Arial;'
                         'color:#1b324a">'+escape(nome)+'</div>')).add_to(camada)
        if camada._children:
            camada.add_to(mapa)


def mapa_integrado_farol(concs, os_cluster, pressao, contexto, raio_cluster, pontos_todos=None):
    """Panorama do Farol com TODAS as O.S. do período, alvos, ativos e aferições.

    Inclusive as O.S. fora das concentrações, exatamente como no mapa do Módulo 3.
    """
    pontos_mapa=[]
    if concs is not None and not concs.empty:
        pontos_mapa.extend(concs[["Latitude","Longitude"]].astype(float).values.tolist())
    base_mapa = pontos_todos if pontos_todos is not None else os_cluster
    if base_mapa is not None and not base_mapa.empty:
        pontos_mapa.extend([[float(r["_lat"]),float(r["_lon"])]
            for _,r in base_mapa.dropna(subset=["_lat","_lon"]).iterrows()])
    if pressao is not None and not pressao.empty:
        pontos_mapa.extend([[float(r["_lat"]),float(r["_lon"])]
            for _,r in pressao.dropna(subset=["_lat","_lon"]).iterrows()
            if pressao_visivel(r)])
    if not pontos_mapa:
        return None
    centro_lat=sum(p[0] for p in pontos_mapa)/len(pontos_mapa)
    centro_lon=sum(p[1] for p in pontos_mapa)/len(pontos_mapa)
    mapa=folium.Map(location=[centro_lat,centro_lon], zoom_start=12,
                     tiles="OpenStreetMap",control_scale=True,zoom_control=False)
    posicoes=[]
    os_fg=folium.FeatureGroup(name="O.S. — Farol",show=True)
    if base_mapa is None:
        base_mapa = pd.DataFrame()
    for _,p in base_mapa.iterrows():
        if pd.isna(p.get("_lat")) or pd.isna(p.get("_lon")): continue
        lat,lon=float(p["_lat"]),float(p["_lon"])
        servico=str(p.get("Serviço Executado", ""))
        cor="#f28c28" if re.search(r"(?<!\d)146005(?!\d)",servico) else "#1677d2"
        folium.CircleMarker([lat,lon],radius=4.2,color=cor,weight=1,fill=True,
            fill_color=cor,fill_opacity=.9,
            tooltip=f"O.S. {p.get('Nº da O.S','')} — {p.get('Bairro','')}").add_to(os_fg)
        posicoes.append([lat,lon])
    os_fg.add_to(mapa)
    centros=[(float(c["Latitude"]),float(c["Longitude"])) for _,c in concs.iterrows()] if concs is not None and not concs.empty else []
    if centros:
        adicionar_ativos_eventos_mapa(mapa,contexto,centros=centros,raio=raio_cluster)
    # A inclusão de TODAS as aferições do período não depende de uma associação com O.S.
    pontos_bp=adicionar_pontos_pressao(mapa,pressao)
    for _,c in (concs if concs is not None else pd.DataFrame()).iterrows():
        y,x=float(c["Latitude"]),float(c["Longitude"])
        folium.Circle([y,x],radius=raio_cluster,color="#8522a5",weight=3,
                      fill=True,fill_color="#8522a5",fill_opacity=.05).add_to(mapa)
        marcador_concentracao(c).add_to(mapa)
        posicoes.append([y,x])
    # Todas as medições válidas selecionadas entram no enquadramento, inclusive
    # bairros sem O.S. concentradas (antes pontos >10 km eram cortados).
    enquadrar=posicoes+pontos_bp
    if len(enquadrar)>1:
        mapa.fit_bounds(enquadrar,padding=(35,35),max_zoom=14)
    adicionar_legenda_integrada(mapa,bool(pontos_bp))
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def mapa_ficha_farol(c, grupo, pressao, contexto, raio_cluster):
    centro=[float(c["Latitude"]),float(c["Longitude"])]
    mapa=folium.Map(location=centro,zoom_start=15,tiles="OpenStreetMap",control_scale=True,zoom_control=False)
    folium.Circle(centro,radius=raio_cluster,color="#8522a5",weight=3,
                  fill=True,fill_color="#8522a5",fill_opacity=.06).add_to(mapa)
    pontos_os=[]
    os_fg=folium.FeatureGroup(name="O.S. — Concentração",show=True)
    for _,p in grupo.iterrows():
        if pd.isna(p.get("_lat")) or pd.isna(p.get("_lon")):continue
        y,x=float(p["_lat"]),float(p["_lon"])
        cor="#f28c28" if re.search(r"(?<!\d)146005(?!\d)",str(p.get("Serviço Executado", ""))) else "#1677d2"
        folium.CircleMarker([y,x],radius=4.5,color=cor,weight=1,fill=True,
            fill_color=cor,fill_opacity=.92,tooltip=f"O.S. {p.get('Nº da O.S','')}").add_to(os_fg)
        pontos_os.append([y,x])
    os_fg.add_to(mapa)
    municipio=str(c.get("Município", "")).split(",")[0]
    adicionar_ativos_eventos_mapa(mapa,contexto,municipio=municipio,
                                   centros=[tuple(centro)],raio=raio_cluster,rotulos=True)
    codigo_set={normalizar(b) for b in str(c.get("Bairros", "")).split(",")}
    med_visivel=[]
    for _,p in pressao.iterrows():
        if p.get("_municipio")!=normalizar(municipio):continue
        if pd.isna(p.get("_lat")) or pd.isna(p.get("_lon")):continue
        if not pressao_visivel(p):continue
        y,x=float(p["_lat"]),float(p["_lon"])
        perto=any(haversine(y,x,p_os[0],p_os[1])<=max(raio_cluster*2,750) for p_os in pontos_os)
        if perto or (p.get("_bairro") in codigo_set and haversine(y,x,*centro)<=3000):
            med_visivel.append(p)
    pos_bp=adicionar_pontos_pressao(mapa,pd.DataFrame(med_visivel)) if med_visivel else []
    marcador_concentracao(c).add_to(mapa)
    extremos=pontos_os+pos_bp
    if extremos:
        extremos += [centro]
        if len(extremos)>1:
            mapa.fit_bounds(extremos,padding=(40,40),max_zoom=16)
    adicionar_legenda_integrada(mapa,bool(pos_bp))
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def ativos_da_concentracao(c,contexto,raio):
    achados=[]
    for tipo in ("POCOS","LOGGERS"):
        df=contexto.get(tipo,pd.DataFrame())
        if df.empty:continue
        for _,a in df.iterrows():
            lat=coordenada(a.get("LATITUDE",a.get("Latitude")),"lat")
            lon=coordenada(a.get("LONGITUDE",a.get("Longitude")),"lon")
            if lat is None or lon is None:continue
            cidade=normalizar(a.get("MUNICIPIO",a.get("Município","")))
            if cidade and cidade not in normalizar(c["Município"]):continue
            distancia=haversine(float(c["Latitude"]),float(c["Longitude"]),lat,lon)
            if distancia<=raio:
                ident=str(a.get("IDENTIFICACAO_ATIVO",a.get("ID_POCO",a.get("ID_LOGGER",""))))
                nome=str(a.get("NOME_POCO",a.get("ENDERECO","")))
                achados.append(["Poço" if tipo=="POCOS" else "Logger",ident,nome,f"{distancia:.0f} m"])
    return sorted(achados,key=lambda v:float(v[3].split()[0]))


def evento_tem_abrangencia_municipal(texto):
    return bool(re.search(r"(?:TODA A CIDADE|TODO O MUNICIPIO|MUNICIPIO INTEIRO|TODOS OS BAIRROS)", normalizar(texto)))


def nomes_bairros_evento(texto):
    bruto=normalizar(texto)
    bruto=re.sub(r"BAIRROS?\s*(?:IMPACTADOS?|AFETADOS?)?\s*:",";",bruto)
    bruto=re.sub(r"AREAS?\s+IMPACTADAS?\s*:",";",bruto)
    return {re.sub(r"\s*\(PARCIAL\)","",p).strip(" .:-")
            for p in re.split(r"[,;\n]+",bruto) if p.strip(" .:-")}


def evento_na_janela(evento, inicio, fim):
    """Verifica interseção temporal, sem declarar ativo evento encerrado sem término."""
    ini=data_parse(evento.get("Início", ""))
    term=data_parse(evento.get("Término Real", ""))
    if pd.isna(ini):return False
    finalizado=any(p in normalizar(evento.get("Status","")) for p in ("FINALIZ", "CONCLUID", "ENCERRAD"))
    if pd.isna(term) and finalizado:return False
    if pd.notna(term) and term.date()<inicio:return False
    if ini.date()>fim:return False
    return True


def eventos_da_concentracao(c,contexto,inicio=None,fim=None):
    """Mesmas chaves de município/bairro do Farol, e evento ativo no período."""
    eventos=contexto.get("EVENTOS",pd.DataFrame())
    if eventos is None or eventos.empty:return []
    pares=set(c.get("_pares_bairro", []))
    municipios={municipio for municipio,_ in pares}
    municipios.update(normalizar(v) for v in str(c.get("Município","")).split(",") if v.strip())
    encontrados=[]
    for _,e in eventos.iterrows():
        mun=normalizar(e.get("Cidade",""))
        if mun not in municipios:continue
        if inicio is not None and fim is not None and not evento_na_janela(e,inicio,fim):continue
        areas=e.get("Áreas Impactadas", "")
        municipal=evento_tem_abrangencia_municipal(areas)
        coincidentes=sorted({bairro for cidade,bairro in pares
                            if cidade==mun and bairro in nomes_bairros_evento(areas)})
        if not municipal and not coincidentes:continue
        item=e.to_dict()
        item["Bairros coincidentes"]="Todo o município" if municipal and not coincidentes else ", ".join(coincidentes)
        encontrados.append(item)
    return encontrados


def horario_operacional(valor):
    """Data sem hora não basta para vincular uma O.S. temporalmente ao evento."""
    texto=str(valor or "").strip()
    if not texto or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}",texto):
        return pd.NaT
    return data_parse(texto)


def classificar_os_eventos(grupo,eventos):
    """Classificação Farol: município, bairro e horário [início, fim + 3h]."""
    vinculadas=[]; nao_vinculadas=[]
    agora=pd.Timestamp.now().tz_localize(None)
    for _,osr in grupo.iterrows():
        data_os=horario_operacional(osr.get("Dt. Emissão", ""))
        cidade=normalizar(osr.get("Cidade", ""))
        bairro=normalizar(osr.get("Bairro", ""))
        protocolos=[]
        if pd.notna(data_os):
            for e in eventos:
                if cidade!=normalizar(e.get("Cidade", "")):continue
                b_evt=nomes_bairros_evento(e.get("Bairros coincidentes", ""))
                if ("TODO O MUNICIPIO" not in normalizar(e.get("Bairros coincidentes", ""))
                    and bairro not in b_evt):
                    continue
                ini=horario_operacional(e.get("Início", ""))
                fim=horario_operacional(e.get("Término Real", ""))
                if pd.isna(ini):continue
                finalizado=any(p in normalizar(e.get("Status","")) for p in ("FINALIZ","CONCLUID","ENCERRAD"))
                if pd.isna(fim):
                    if finalizado:continue
                    fim=agora
                if ini <= data_os <= fim+pd.Timedelta(hours=3):
                    protocolos.append(str(e.get("Protocolo","") or e.get("Serviço", "") or "Evento"))
        registro=osr.to_dict()
        registro["Eventos vinculados"]="; ".join(dict.fromkeys(protocolos))
        (vinculadas if protocolos else nao_vinculadas).append(registro)
    return vinculadas,nao_vinculadas


def protocolo_os_relatorio(reg):
    """Usa O.S. e ano quando há emissão válida, como no Módulo 3."""
    numero=str(reg.get("Nº da O.S","") or "").strip()
    if not numero or numero.lower()=="nan":return ""
    if re.search(r"/\d{4}-1$",numero):return numero
    dt=data_parse(reg.get("Dt. Emissão", ""))
    return f"{numero}/{dt.year}-1" if pd.notna(dt) else numero


def _movimentacoes_em_lote(contexto, evento_id):
    """Plano B de leitura, exclusivamente na planilha independente de acompanhamento."""
    base = contexto.get("movimentacoes", pd.DataFrame())
    if base is None or base.empty:
        return None
    colunas = {normalizar(c).replace(" ", "_"): c for c in base.columns}
    col_evento = colunas.get("EVENTO_ID")
    if col_evento is None:
        return None
    selecionadas = base.loc[base[col_evento].astype(str).str.strip() == str(evento_id).strip()]
    return [
        {campo: reg.get(origem, "") for campo, origem in (
            ("data_hora", colunas.get("DATA_HORA")),
            ("status_momento", colunas.get("STATUS_MOMENTO")),
            ("descricao", colunas.get("DESCRICAO")),
            ("id", colunas.get("ID")),
        ) if origem is not None}
        for _, reg in selecionadas.iterrows()
    ]


def _ordenar_movimentacoes(registros):
    """Ordena cronologicamente; não duplica o registro inicial na ficha PDF."""
    itens = []
    for pos, reg in enumerate(registros or []):
        if not isinstance(reg, dict):
            continue
        descricao = str(reg.get("descricao", "") or "").strip()
        if descricao.startswith("Registro inicial criado. Relato:"):
            continue
        dt = data_parse(reg.get("data_hora", ""))
        chave_data = dt if pd.notna(dt) else pd.Timestamp.max
        itens.append((chave_data, pos, reg))
    itens.sort(key=lambda x: (x[0], x[1]))
    return [reg for _, _, reg in itens]


def ficha_acompanhamento(c, grupo, contexto, incluir_movimentacoes=True):
    """Associa somente à ficha exata do Farol.

    A consulta de movimentações é opcional; fica desativada na geração do PDF.
    Nunca aproxima acompanhamentos só por município/bairro ou código C001.
    """
    vinc = contexto.get("vinculos_farol_producao", pd.DataFrame())
    fichas = contexto.get("eventos_acomp", pd.DataFrame())
    if vinc.empty or fichas.empty:
        return None, [], None
    try:
        from integracao_acompanhamento_producao import chave_concentracao
        numeros = sorted({str(x).strip() for x in grupo["Nº da O.S"] if str(x).strip()})
        # O Farol utiliza o primeiro município (em ordem alfabética) das O.S.
        # na chave de acompanhamento. O relatório integrado pode reunir municípios
        # API, por isso NÃO usamos o texto concatenado de c["Município"].
        municipios = sorted({str(x).strip() for x in grupo["Cidade"] if str(x).strip()},
                            key=normalizar)
        if not municipios:
            return None, [], None
        chave = chave_concentracao(municipios[0], float(c["Latitude"]),
                                  float(c["Longitude"]), numeros)
    except (KeyError, TypeError, ValueError, ImportError):
        return None, [], None
    colunas = {normalizar(z).replace(" ", "_"): z for z in vinc.columns}
    col_chave, col_evento = colunas.get("CHAVE"), colunas.get("EVENTO_ID")
    if not col_chave or not col_evento:
        return None, [], None
    achado = vinc[vinc[col_chave].astype(str).str.strip() == str(chave).strip()]
    if achado.empty:
        return None, [], None
    evento_id = str(achado.iloc[-1][col_evento]).strip()
    col_fichas = {normalizar(z).replace(" ", "_"): z for z in fichas.columns}
    col_id = col_fichas.get("ID")
    if not col_id:
        return None, [], None
    ficha = fichas[fichas[col_id].astype(str).str.strip() == evento_id]
    if ficha.empty:
        return None, [], None

    if not incluir_movimentacoes:
        return ficha.iloc[-1].to_dict(), [], None

    # Mesmo mecanismo de consulta do Módulo 3; leitura apenas, sem gravação.
    # Se o serviço estiver temporariamente indisponível, tentar a aba lida
    # em lote e deixar explícita qualquer impossibilidade de obter o histórico.
    try:
        from integracao_acompanhamento_producao import historico_evento
        movimentos = historico_evento(evento_id)
        if movimentos is None:
            raise ValueError("Consulta de histórico sem resposta")
        return ficha.iloc[-1].to_dict(), _ordenar_movimentacoes(movimentos), None
    except Exception:
        registros_lote = _movimentacoes_em_lote(contexto, evento_id)
        if registros_lote is not None:
            return ficha.iloc[-1].to_dict(), _ordenar_movimentacoes(registros_lote), None
        return ficha.iloc[-1].to_dict(), [], "Histórico de movimentações temporariamente indisponível."


def secao_pdf_movimentacoes(movimentacoes, erro_movimentacoes, largura, normal, pequeno,
                            secao, P):
    """Apresentação alinhada à ficha do Módulo 3: data/status + descrição."""
    azul = colors.HexColor("#193C65")
    fundo = colors.HexColor("#F1F5FA")
    saida = [P("HISTÓRICO DE MOVIMENTAÇÕES", secao)]
    if erro_movimentacoes:
        return saida + [P(erro_movimentacoes, normal)]
    if not movimentacoes:
        return saida + [P("Sem movimentações posteriores ao registro inicial.", normal)]
    for reg in movimentacoes:
        momento = str(reg.get("data_hora", "") or "").strip()
        status = str(reg.get("status_momento", "") or "").strip()
        cabecalho = "  |  ".join([v for v in (momento, status) if v]) or "Sem data ou status informado"
        descricao = reg.get("descricao", "") or "Sem descrição informada."
        bloco = Table([[P(cabecalho, pequeno), P(descricao, normal)]],
                       colWidths=[largura * .29, largura * .71], hAlign="LEFT")
        bloco.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), fundo),
            ("LINEBEFORE", (0, 0), (0, 0), 2.5, colors.HexColor("#3975AC")),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        saida.append(KeepTogether([bloco, Spacer(1, 3)]))
    return saida


def montar_secao_baixa_pressao(historia, pressao, inicio, fim, normal, pequeno,
                                titulo, secao, fonte, P, tabela, os_base=None):
    """Seção PDF idêntica em estrutura e critérios ao relatório atual do Módulo 1.

    Mantém Farol independente. A análise Sanear usa apenas medições válidas
    e filtradas por período/unidade/município, sem vincular O.S. por proximidade.
    """
    if pressao is None or pressao.empty:
        return
    base = pressao.loc[pressao["_pressao"].notna() & (pressao["_pressao"] >= 0)].copy()
    if base.empty:
        return
    # A conversão numérica é exclusiva do PDF e não modifica a base carregada.
    base["Latitude"] = pd.to_numeric(base["_lat"], errors="coerce")
    base["Longitude"] = pd.to_numeric(base["_lon"], errors="coerce")
    if RI_BP_COLUNA_PROTOCOLO not in base:
        base[RI_BP_COLUNA_PROTOCOLO] = ""
    bairros = resumo_pressao_por_bairro(base)
    if bairros.empty:
        return
    medidas = pd.to_numeric(base["_pressao"], errors="coerce").dropna()
    mun = sorted(set(base["_municipio"].dropna()) - {""})
    nota_periodo = (f"Período: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}  |  "
                    f"Município(s): {', '.join(mun) if mun else 'Não informados'}")

    # Mesmo estilo da tabela de Baixa Pressão do Módulo 1, sem alterar o Farol.
    pequeno_bp = ParagraphStyle(
        "ri_bp_pequeno_pdf", parent=pequeno, fontSize=7.5, leading=10)
    cabecalho_bp = ParagraphStyle(
        "ri_bp_cabecalho_pdf", parent=pequeno_bp,
        fontSize=7.5, leading=10, textColor=colors.HexColor("#193c65"))
    def tab_bp(cabecalhos, linhas, larguras):
        dados = [[P(x, cabecalho_bp) for x in cabecalhos]] + [
            [P(x, pequeno_bp) for x in linha] for linha in linhas]
        # Tabela contínua: o ReportLab repete a linha de cabeçalho somente
        # quando existe quebra REAL de página, nunca no meio da mesma página.
        t = Table(dados, colWidths=larguras, repeatRows=1, splitByRow=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e1eaf4")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f4f7fb")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    historia.extend([
        P("MONITORAMENTO DE BAIXA PRESSÃO — COI", titulo),
        P(nota_periodo, normal), Spacer(1, 10)])

    def cartao(rotulo, valor):
        estilo = ParagraphStyle(
            "ri_bp_kpi_" + re.sub(r"\W+", "_", rotulo), parent=normal,
            fontSize=16, leading=19, textColor=colors.HexColor("#193c65"))
        c = Table([[P(rotulo, pequeno_bp)], [P(valor, estilo)]],
                  colWidths=[6.25*cm])
        c.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5fa")),
            ("BOX", (0, 0), (-1, -1), .6, colors.HexColor("#dce5ef")),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return c

    criticos = int(medidas.le(5).sum())
    cards = Table([[
        cartao("MEDIÇÕES", str(len(base))),
        cartao("BAIRROS", str(len(bairros))),
        cartao("MÉDIA", f"{medidas.mean():.2f} mca"),
        cartao("ATÉ 5 MCA", f"{criticos} ({100*criticos/len(base):.0f}%)"),
    ]], colWidths=[6.55*cm]*4, hAlign="LEFT")
    cards.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    historia.extend([cards, Spacer(1, 9)])

    mapa = ri_bp_mapa(base)
    if mapa is not None:
        historia.append(P("MAPA DAS AFERIÇÕES", secao))
        try:
            imagem = imagem_folium(mapa, 1280, 650)
        except Exception:
            imagem = ri_bp_mapa_fallback(base)
            historia.append(P(
                "Mapa-base indisponível: pontos exibidos em plano geográfico "
                "esquemático, sem ruas.", pequeno_bp))
        historia.append(Image(io.BytesIO(imagem), width=26.1*cm, height=11.25*cm))
    else:
        historia.append(P(
            "Não há pontos com coordenadas válidas para os filtros selecionados.",
            normal))
    historia.append(PageBreak())

    historia.extend([
        P("DASHBOARD DE PRESSÃO", titulo), Spacer(1, 10)])
    graficos = Table([[
        _chart_faixas_vetorial(base, fonte),
        _chart_bairros_vetorial(bairros, fonte),
    ]], colWidths=[13.1*cm,13.1*cm], hAlign="LEFT")
    graficos.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    historia.append(graficos)
    historia.append(PageBreak())

    historia.append(P("DIAGNÓSTICO POR BAIRRO", titulo))
    crit = bairros[bairros["Até 5 mca"] > 0]
    if not crit.empty:
        lideres = crit.sort_values(["Até 5 mca", "Medições"],
                                     ascending=False).head(4)
        resumo = "Pontos de atenção: " + "; ".join(
            f"{r['Bairro']} / {r['Município']} "
            f"({int(r['Até 5 mca'])} de {int(r['Medições'])} medições até 5 mca)"
            for _, r in lideres.iterrows()) + "."
        historia.extend([P(resumo, normal), Spacer(1, 7)])

    linhas = []
    for _, r in bairros.iterrows():
        linhas.append([
            f"{r['Bairro']} / {r['Município']}", int(r["Medições"]),
            int(r["Matrículas"]), f"{r['Mín. (mca)']:g}",
            f"{r['Média (mca)']:g}", f"{r['Máx. (mca)']:g}",
            f"{r['Até 5 mca']}/{r['Medições']}", f"{r['% até 5 mca']:g}%"])
    # Uma única tabela por diagnóstico: cabeçalho no começo e nas páginas
    # seguintes, apenas se a tabela ultrapassar o espaço disponível.
    historia.append(tab_bp(
        ["Bairro / Município", "n", "Matrículas", "Mín.",
         "Média", "Máx.", "≤5 mca", "% ≤5"],
        linhas,
        [9.6*cm, 1.4*cm, 2.2*cm, 2*cm, 2*cm, 2*cm, 3*cm, 2.8*cm]))

    # Última seção, idêntica ao Módulo 1. Não depende da checkbox Sanear.
    historia.append(PageBreak())
    historia.extend([
        P("MEDIÇÕES EM ÁREAS DE INTERVENÇÃO SANEAR", titulo),
        Spacer(1, 6)])
    try:
        arquivo = ri_bp_encontrar_kml_sanear()
        if not arquivo.is_file():
            historia.append(P(
                "Camada Sanear indisponível no repositório: "
                "geodados/areas_influencia/sanear.kml. "
                "Nenhum cruzamento foi executado.", normal))
        else:
            geometrias = ri_bp_carregar_poligonos_sanear(
                str(arquivo), arquivo.stat().st_mtime_ns)
            if not geometrias.get("features"):
                historia.append(P(
                    "A camada Sanear não contém polígonos válidos. "
                    "Nenhum cruzamento foi executado.", normal))
            else:
                dentro, indices_sanear = ri_bp_selecionar_sanear(base, geometrias)
                historia.append(P(
                    f"Medições dentro ou sobre o limite dos polígonos: "
                    f"{len(dentro)} de {len(base)} registros filtrados. "
                    f"Áreas intersectadas: {len(indices_sanear)}.", normal))
                if dentro.empty:
                    historia.extend([Spacer(1, 9), P(
                        "Não foram encontradas medições nas áreas Sanear "
                        "para o período e os filtros selecionados.", pequeno_bp)])
                else:
                    historia.append(Spacer(1, 6))
                    mapa_sanear = ri_bp_mapa_sanear(dentro, geometrias, indices_sanear)
                    try:
                        imagem_sanear = imagem_folium(mapa_sanear, 1280, 650)
                    except Exception:
                        imagem_sanear = ri_bp_fallback_sanear(
                            dentro, geometrias, indices_sanear)
                        historia.append(P(
                            "Mapa-base indisponível: a imagem mantém as "
                            "geometrias Sanear e as coordenadas reais, sem ruas.",
                            pequeno_bp))
                    # Reserva espaço para identificar a relação de medições
                    # logo abaixo do mapa, sem perder a tabela na página.
                    historia.append(Image(io.BytesIO(imagem_sanear),
                                         width=26.1*cm, height=10.1*cm))
                    linhas_sanear = []
                    for _, r in dentro.sort_values(
                            ["_bairro", "_data", "Matricula"],
                            na_position="last").iterrows():
                        protocolo = str(r.get(RI_BP_COLUNA_PROTOCOLO, "") or "").strip()
                        if protocolo.lower() in ("nan", "none", "nat"):
                            protocolo = ""
                        linhas_sanear.append([
                            str(r.get("Matricula", "") or "") or "—",
                            protocolo or "Não informado",
                            str(r.get("Bairro", "") or "") or "—",
                            f"{float(r['_pressao']):g} mca"])
                    # Separação clara entre o mapa e a listagem operacional.
                    # Título fica unido ao início da tabela mesmo em quebras.
                    subtitulo_sanear = ParagraphStyle(
                        "ri_bp_subtitulo_lista_sanear", parent=secao,
                        spaceBefore=0, spaceAfter=6, keepWithNext=1)
                    historia.append(Spacer(1, 12))
                    historia.append(P(
                        "RELAÇÃO DE MEDIÇÕES NAS ÁREAS SANEAR",
                        subtitulo_sanear))
                    historia.append(tab_bp(
                        ["MATRÍCULA", "CÓD. PROTOCOLO", "BAIRRO", "MEDIÇÃO"],
                        linhas_sanear,
                        [5.5*cm, 6.1*cm, 10.2*cm, 4.4*cm]))
    except (ET.ParseError, OSError, ValueError, TypeError,
            KeyError, IndexError) as erro:
        historia.append(P(
            "Não foi possível realizar o cruzamento com a camada Sanear: "
            + str(erro), pequeno_bp))


def _dia_evidencias():
    """Dia civil de Teresina, independente do fuso do servidor Streamlit."""
    return datetime.now(FUSO_LOGGERS).date()


def carregar_graficos_do_dia_somente_leitura(spreadsheet_id, dia):
    """Lê imagens do Farol SEM escrever na aba e sem alterar sua limpeza diária.

    O Módulo 3 é responsável pelos uploads, substituições e limpeza. No
    Módulo 7, somente os registros da data requisitada são considerados.
    Importante: os dados Base64 podem ocupar várias colunas do Google Sheets.
    """
    try:
        linhas = cliente_google().open_by_key(spreadsheet_id).worksheet(ABA_GRAFICOS_DIA).get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        # Aba pode não existir até a primeira evidência enviada no Módulo 3.
        return {}
    hoje = dia.isoformat()
    indice = {}
    for linha in linhas[1:]:
        if len(linha) < 5 or linha[0] != hoje or not linha[1]:
            continue
        conteudo = "".join(linha[4:])
        if not conteudo:
            continue
        try:
            imagem = base64.b64decode(conteudo, validate=True)
        except (ValueError, base64.binascii.Error):
            # Um anexo ilegível não deve ser apresentado como evidência.
            continue
        # Mesmo critério do Módulo 3: em duplicatas, prevalece a última linha.
        indice[linha[1]] = {"logger": linha[2], "atualizado_em": linha[3], "imagem": imagem}
    return indice


def loggers_proximos_da_concentracao(c, contexto, raio_operacional):
    """Reconstrói os campos usados pela chave compartilhada do Módulo 3.

    A proximidade é medida a partir do centro da concentração, assim como no
    Farol. Não associa o gráfico pelo ID variável THE C001/API C002.
    """
    base = contexto.get("LOGGERS", pd.DataFrame())
    if base is None or base.empty:
        return pd.DataFrame(columns=["Tipo", "Identificação", "ID Logger", "Nome", "Município", "Distância"])
    lat_c, lon_c = float(c["Latitude"]), float(c["Longitude"])
    encontrados = []
    for _, item in base.iterrows():
        lat = coordenada(item.get("LATITUDE"), "lat")
        lon = coordenada(item.get("LONGITUDE"), "lon")
        if lat is None or lon is None:
            continue
        distancia = haversine(lat_c, lon_c, lat, lon)
        if distancia > raio_operacional:
            continue
        encontrados.append({
            "Tipo": "Logger",
            "Identificação": str(item.get("IDENTIFICACAO_ATIVO", "") or ""),
            "ID Logger": str(item.get("ID_LOGGER", "") or ""),
            "Nome": str(item.get("ENDERECO", "") or ""),
            "Município": str(item.get("MUNICIPIO", "") or ""),
            "Distância": f"{distancia:.0f} m" if distancia < 1000 else f"{distancia / 1000:.2f} km",
            "_distancia": distancia,
        })
    if not encontrados:
        return pd.DataFrame(columns=["Tipo", "Identificação", "ID Logger", "Nome", "Município", "Distância"])
    return pd.DataFrame(encontrados).sort_values("_distancia").drop(columns="_distancia")


def evidencias_loggers_relatorio_integrado(loggers, indice, fonte, largura=26.6*cm):
    """Cria seção SOMENTE para loggers do raio que possuem gráfico válido."""
    from PIL import Image as ImagemPIL
    evidencias = []
    for logger in filtrar_loggers(loggers):
        registro = (indice or {}).get(chave_logger(logger))
        if not registro or not registro.get("imagem"):
            continue
        foto = registro["imagem"]
        try:
            with ImagemPIL.open(io.BytesIO(foto)) as imagem_pil:
                largura_foto, altura_foto = imagem_pil.size
                imagem_pil.verify()
            if largura_foto <= 0 or altura_foto <= 0:
                continue
            larg = min(largura, 25.4*cm)
            alt = larg * altura_foto / largura_foto
            if alt > 9.8*cm:
                fator = 9.8*cm / alt
                larg *= fator
                alt *= fator
            figura = Image(io.BytesIO(foto), width=larg, height=alt)
            evidencias.append((logger, figura))
        except Exception:
            # Captura inválida não gera página nem aviso desnecessário no PDF.
            continue
    if not evidencias:
        return []

    corpo = ParagraphStyle("EvidenciaLoggersM7Corpo", fontName=fonte,
        fontSize=9, leading=13, textColor=colors.HexColor("#273449"), spaceAfter=5)
    titulo = ParagraphStyle("EvidenciaLoggersM7Titulo", parent=corpo,
        fontSize=13, leading=17, spaceAfter=10,
        textColor=colors.HexColor("#17365D"))
    secao = [PageBreak(), Paragraph("Evidências gráficas dos loggers próximos", titulo)]
    for numero, (logger, figura) in enumerate(evidencias, start=1):
        descricao = f"Logger {numero} — {rotulo_logger(logger)} | Distância: {logger.get('Distância', '')}"
        secao.append(KeepTogether([
            Paragraph(xml_escape(descricao), corpo), Spacer(1, .12*cm),
            figura, Spacer(1, .3*cm),
        ]))
    return secao



def kpis_panorama_farol(n_os, n_concentracoes, n_medicoes, n_com_medicao, raio, normal, pequeno):
    """Indicadores abaixo do panorama, no padrão visual das medições de baixa pressão.

    Os cálculos seguem os KPIs da página Streamlit, inclusive o critério de
    existência de ao menos uma medição próxima à O.S. (sem contagem dupla).
    """
    azul = colors.HexColor("#193C65")
    cinza = colors.HexColor("#F1F5FA")
    borda = colors.HexColor("#DCE5EF")
    destaque = ParagraphStyle(
        "PanoramaFarolKpiNumero", parent=normal, fontSize=17.3, leading=20.2,
        textColor=azul, spaceBefore=0, spaceAfter=0,
    )
    legenda = ParagraphStyle(
        "PanoramaFarolKpiLegenda", parent=pequeno, fontSize=6.7, leading=8.0,
        textColor=colors.HexColor("#42546A"), spaceBefore=0, spaceAfter=0,
    )
    rotulo = ParagraphStyle(
        "PanoramaFarolKpiRotulo", parent=pequeno, fontSize=7.1, leading=8.5,
        textColor=colors.HexColor("#25354B"), spaceBefore=0, spaceAfter=0,
    )

    def cartao(nome, valor, detalhe):
        bloco = Table([
            [Paragraph(xml_escape(nome), rotulo)],
            [Paragraph(xml_escape(str(valor)), destaque)],
        ], colWidths=[6.19*cm], hAlign="LEFT")
        bloco.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), cinza),
            ("BOX", (0, 0), (-1, -1), .6, borda),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return bloco

    painel = Table([[
        cartao("O.S. NO PERÍODO", n_os, "Registros da seleção"),
        cartao("CONCENTRAÇÕES", n_concentracoes, "Agrupamentos identificados"),
        cartao("MEDIÇÕES NO PERÍODO", n_medicoes, "Aferições de pressão"),
        cartao(f"COM MEDIÇÃO PRÓXIMA (≤{raio} m)", n_com_medicao, ""),
    ]], colWidths=[6.65*cm]*4, hAlign="LEFT")
    painel.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return painel


def pdf_relatorio(resumo, associados, territoriais, achados, inicio, fim, raio, n_os, n_bp,
                  concentracoes=None, grupos=None, raio_cluster=RAIO_CONCENTRACOES_M, contexto=None, pressao=None,
                  pontos_todos=None, indice_graficos=None, raio_operacional_loggers=RAIO_OPERACIONAL_EVIDENCIAS):
    """Relatório do Farol: panorama + fichas com mapa e O.S. ao lado.

    Mantém ativos, eventos, classificação temporal de O.S. e acompanhamento.
    Apresenta panorama de aferições e indicadores agregados por bairro no anexo M1.
    Não modifica nem importa a página do Módulo 3.
    """
    contexto = contexto or {}
    pressao = pressao if pressao is not None else pd.DataFrame()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
            leftMargin=1.2*cm, rightMargin=1.2*cm,
            topMargin=1.2*cm, bottomMargin=1.2*cm)
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.isfile(regular) and "FIntReg" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("FIntReg", regular))
        pdfmetrics.registerFont(TTFont("FIntBold", bold))
        pdfmetrics.registerFontFamily("FIntReg", normal="FIntReg", bold="FIntBold")
    fonte = "FIntReg" if "FIntReg" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    azul = colors.HexColor("#193c65")
    normal = ParagraphStyle("farol_integra_normal",fontName=fonte,fontSize=8.4,leading=11.5)
    pequeno = ParagraphStyle("farol_integra_pequeno",parent=normal,fontSize=7.1,leading=9.3)
    titulo = ParagraphStyle("farol_integra_titulo",parent=normal,fontSize=12.5,leading=16,spaceAfter=8,textColor=azul)
    secao = ParagraphStyle("farol_integra_secao",parent=normal,fontSize=9.7,leading=12,
                           spaceBefore=8,spaceAfter=5,textColor=azul)
    def P(v,sty=pequeno):
        return Paragraph(xml_escape(str(v if v is not None else "")).replace("\n","<br/>"),sty)
    def tabela(cab,linhas,larguras):
        dados=[[P(x) for x in cab]]+[[P(v) for v in linha] for linha in linhas]
        t=Table(dados,colWidths=larguras,repeatRows=1,hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dee8f2")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f4f7fb")]),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),3),
            ("RIGHTPADDING",(0,0),(-1,-1),3),
            ("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3)
        ]))
        return t
    def tabela_os(linhas,largura=11.85*cm):
        return tabela(["O.S. / Ano","Matrícula","Status"],linhas,
                      [largura*.37,largura*.27,largura*.36])

    historia=[P("Farol Operacional — Relatório de concentrações",titulo),
             P(f"Período: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"
               + (" | Unidade: " + str(concentracoes.iloc[0]["_unidade"]) if concentracoes is not None and not concentracoes.empty else ""),normal),
             Spacer(1,9)]
    # Primeira parte: Farol na estrutura original, SEM pontos de pressão.
    # Segunda parte: relatório M1 independente, com mapa e dashboards.
    pressao=associar_pressao_as_os(pressao,pontos_todos)

    def adicionar_secao_modulo1():
        montar_secao_baixa_pressao(
            historia,pressao,inicio,fim,normal,pequeno,titulo,secao,
            fonte,P,tabela,os_base=pontos_todos)


    if concentracoes is None or concentracoes.empty:
        historia.append(P("Nenhuma concentração identificada para a seleção.", normal))
        if not pressao.empty:
            historia.append(PageBreak())
            adicionar_secao_modulo1()
        doc.build(historia)
        return buffer.getvalue()

    # PANORAMA do Farol + todos os pontos cartográficos válidos de pressão.
    mapa=mapa_integrado_farol(concentracoes,grupos,pressao.iloc[:0],contexto,raio_cluster,
                             pontos_todos=pontos_todos)
    # Enquadramento panorâmico mais horizontal: preserva a proporção da captura
    # sem distorcer o mapa, deixando espaço para os 4 indicadores na mesma página.
    imagem=imagem_folium(mapa,1280,585)
    medicao_proxima=(int((resumo["Medições próximas"] > 0).sum())
                     if resumo is not None and not resumo.empty else 0)
    historia.append(KeepTogether([
        P("Panorama territorial das concentrações",titulo),
        Image(io.BytesIO(imagem),width=26*cm,height=(26*585/1280)*cm),
        Spacer(1,6),
        kpis_panorama_farol(n_os, len(concentracoes), n_bp,
                           medicao_proxima, raio, normal, pequeno),
    ]))
    historia.append(PageBreak())

    for indice,(_,c) in enumerate(resumo.iterrows()):
        codigo=str(c["Concentração"])
        grupo=grupos[grupos["_grupo"]==c["_grupo"]].copy()
        proximas=associados[associados["Concentração"]==codigo] if not associados.empty else pd.DataFrame()
        territoriais_c=territoriais[territoriais["Concentração"]==codigo] if not territoriais.empty else pd.DataFrame()
        historia.append(P(f"{codigo} — {c['Município']} — {c['Bairros']}",titulo))
        figura=imagem_folium(mapa_ficha_farol(c,grupo,pressao.iloc[:0],contexto,raio_cluster),760,515)
        img=Image(io.BytesIO(figura),width=13.4*cm,height=9.08*cm)
        eventos=eventos_da_concentracao(c,contexto,inicio,fim)
        vinculadas,nao_vinculadas=classificar_os_eventos(grupo,eventos)
        ev_descr="; ".join((f"{e.get('Protocolo','')} — {e.get('Serviço','')} / "
                           f"{e.get('Código do Ativo','')} [{e.get('Status','')}] "
                           f"{e.get('Início','')} até {e.get('Término Real','') or 'em andamento'}")
                          for e in eventos)
        base_info=[P(f"{len(grupo)} O.S. · {int(c['Matrículas'])} matrículas distintas",normal),
                   Spacer(1,5),Paragraph("<b>Eventos relacionados:</b> " +
                          xml_escape(ev_descr or "Nenhum evento relacionado"),pequeno),
                   Spacer(1,3)]
        def linhas_os(registros):
            return [[protocolo_os_relatorio(r),str(r.get("Matrícula","")),
                     str(r.get("Status OS",""))] for r in registros]
        categorias=[("Ordens vinculadas aos eventos",linhas_os(vinculadas)),
                    ("Ordens não vinculadas",linhas_os(nao_vinculadas))]
        def conteudo_painel(qt):
            blocos=list(base_info)
            for (nome,linhas),qtd in zip(categorias,qt):
                blocos.extend([Spacer(1,4),Paragraph("<b>" +
                    xml_escape(f"{nome} ({len(linhas)})") + "</b>",pequeno)])
                if linhas and qtd:
                    blocos.append(tabela_os(linhas[:qtd]))
            return blocos
        # Reserva exatamente o espaço do painel lateral usado pelo Farol.
        limite_h=9.08*cm-12
        largura_painel=12.8*cm-16
        def altura_bloco(elements):
            altura=0
            for element in elements:
                _,h=element.wrap(largura_painel,limite_h)
                altura+=h+element.getSpaceBefore()+element.getSpaceAfter()
            return altura
        contagens=[0,0]
        for k,(_,linhas) in enumerate(categorias):
            for _ in linhas:
                candidato=contagens.copy(); candidato[k]+=1
                if altura_bloco(conteudo_painel(candidato))<=limite_h:
                    contagens=candidato
                else:break
        ficha=Table([[img,conteudo_painel(contagens)]],
                    colWidths=[13.8*cm,12.8*cm],hAlign="LEFT")
        ficha.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BACKGROUND",(1,0),(1,0),colors.HexColor("#f4f7fb")),
            ("LEFTPADDING",(0,0),(0,0),0),
            ("LEFTPADDING",(1,0),(1,0),10),
            ("RIGHTPADDING",(1,0),(1,0),6),
            ("TOPPADDING",(0,0),(-1,-1),6),
            ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ]))
        historia.extend([ficha,Spacer(1,7)])
        for (nome,linhas),ja_exibidas in zip(categorias,contagens):
            restantes=linhas[ja_exibidas:]
            if restantes:
                # IMPORTANTE: não envolver todas as O.S. restantes em uma
                # Table de célula única. Uma célula com outra tabela dentro
                # não pode ser quebrada pelo ReportLab entre duas páginas;
                # listas longas geravam LayoutError (célula maior que a página).
                # A tabela de O.S. fica diretamente na história do PDF, para
                # permitir quebra por linha e repetição automática do cabeçalho.
                cabecalho_continuacao = Paragraph(
                    "<b>" + xml_escape(f"{nome} — continuação") + "</b>",
                    ParagraphStyle(
                        "farol_integra_continuacao_os",
                        parent=pequeno,
                        spaceBefore=9,
                        spaceAfter=5,
                        keepWithNext=1,
                    ),
                )
                historia.extend([
                    cabecalho_continuacao,
                    tabela_os(restantes, 25.6*cm),
                ])
        # Padrão do Farol: equipamentos são exibidos no mapa e os eventos no
        # painel de O.S. Não criar quadros extras que alterem o layout original.
        # Somente os dados da ficha são usados no PDF. Não consultar o histórico.
        acompanhamento, _, _ = ficha_acompanhamento(c, grupo, contexto, incluir_movimentacoes=False)
        if acompanhamento:
            historia.append(P("ACOMPANHAMENTO OPERACIONAL",secao))
            historia.append(tabela(["Status / Finalizado","Município / Regional","Reclamações / Unidade"],[[
                f"{acompanhamento.get('status','')} · {acompanhamento.get('finalizado','')}",
                f"{acompanhamento.get('municipio','')} · {acompanhamento.get('regional','')}",
                f"{acompanhamento.get('qtd_reclamacoes','')} · {acompanhamento.get('unidade','')}"
            ]],[8.8*cm,8.8*cm,8.4*cm]))
            historia.extend([P("Validações operacionais",secao),
                tabela(["ArcGIS","SIA","Loggers","Retorno Operação/Controle"],[[
                acompanhamento.get("arcgis",""),acompanhamento.get("sia",""),
                acompanhamento.get("loggers",""),acompanhamento.get("retorno_operacao","")]],
                [6.0*cm,6.0*cm,6.0*cm,8.0*cm]),
            ])
            if str(acompanhamento.get("descricao", "") or "").strip():
                historia.extend([P("Descrição da ocorrência", secao),
                                 P(acompanhamento["descricao"], normal)])
        # Evidências do dia atual, idênticas às usadas pelo Módulo 3: não
        # criar título, espaço reservado ou página para logger sem imagem.
        loggers_da_ficha = loggers_proximos_da_concentracao(
            c, contexto, raio_operacional_loggers
        )
        historia.extend(evidencias_loggers_relatorio_integrado(
            loggers_da_ficha, indice_graficos, fonte
        ))
        if indice<len(resumo)-1:
            historia.append(PageBreak())
    if not pressao.empty:
        historia.append(PageBreak())
        adicionar_secao_modulo1()
    doc.build(historia)
    return buffer.getvalue()


st.title("📊 Relatórios Integrados — Farol + Baixa Pressão")
st.caption("Integração analítica entre concentrações de O.S. e registros de pressão · fontes somente leitura")
if st.button("← Voltar ao Hub Central",key="retornar_hub_ri"):
    st.switch_page("app.py")

hoje=date.today()
with st.form("parametros"):
    unidade_sel=st.radio("Unidade do relatório",["THE — Teresina", "API — Piauí", "TIM — Timon"],horizontal=True)
    a,b,c=st.columns([1,1,1.3])
    with a: inicio=st.date_input("Data inicial",value=hoje-timedelta(days=7),format="DD/MM/YYYY")
    with b: fim=st.date_input("Data final",value=hoje,format="DD/MM/YYYY")
    with c: municipio_sel=st.text_input("Município (opcional)",placeholder="Todos os municípios")
    executar=st.form_submit_button("Gerar diagnóstico integrado",type="primary",use_container_width=True)
# Regras fixas do Farol, aplicadas tanto à análise quanto ao relatório.
raio_cluster=RAIO_CONCENTRACOES_M
raio_relacao=RAIO_MEDICOES_M
minimo=MINIMO_OS_CONCENTRACAO
if executar:
    # Descarta resultados antigos antes de uma nova consulta, evitando exibir diagnósticos desatualizados.
    st.session_state.pop("relatorio_coi_integrado", None)
    st.session_state.pop("pdf_relatorio_integrado_farol", None)
    st.session_state.pop("pdf_relatorio_integrado_dia", None)
    if inicio>fim:
        st.error("A data inicial não pode ser posterior à data final.")
        st.stop()
    try:
        with st.spinner("Consultando as bases e cruzando O.S. com medições..."):
            os_df=preparar_os(ler_base(ID_FAROL,"PONTOS"))
            contexto=carregar_contexto_farol()
            bp_todas=preparar_pressao(ler_base(ID_PRESSAO,"baixa_pressao"))
            contagem_origem={"total": len(bp_todas),
                "data_valida": int(bp_todas["_data"].notna().sum()),
                "pressao_valida": int(bp_todas["_pressao"].notna().sum())}
            os_df=os_df[os_df["_data"].dt.date.between(inicio,fim)].copy()
            bp=bp_todas[bp_todas["_data"].dt.date.between(inicio,fim) & bp_todas["_pressao"].notna()].copy()
            contagem_origem["periodo"] = len(bp)
            unidade=unidade_sel.split(" ",1)[0]
            os_df=os_df[os_df["Cidade"].map(unidade_da_cidade_farol)==unidade].copy()
            bp=bp[bp["Municipio"].map(unidade_da_cidade_farol)==unidade].copy()
            if municipio_sel.strip():
                mun=normalizar(municipio_sel)
                os_df=os_df[os_df["_municipio"]==mun]
                bp=bp[bp["_municipio"]==mun]
            contagem_origem["unidade_municipio"] = len(bp)
            bp=associar_pressao_as_os(bp,os_df)
            resumo_bp=resumo_pressao_por_bairro(bp)
            concs,grupos=concentracoes_farol(os_df,int(raio_cluster),int(minimo))
            if not concs.empty:
                resumo,associados,territoriais=relacionar(concs,grupos,bp,int(raio_relacao),inicio,fim)
                achados,pressao_sem_conc=diagnostico_geral(resumo,bp,associados,territoriais,inicio,fim)
            else:
                resumo,associados,territoriais=pd.DataFrame(),pd.DataFrame(),pd.DataFrame()
                achados=["Nenhuma concentração atende aos critérios selecionados."]
                pressao_sem_conc=bp.iloc[:0].copy()
            st.session_state["relatorio_coi_integrado"]={"versao_esquema":12,"resumo":resumo,"associados":associados,"territoriais":territoriais,
             "concentracoes":concs,"achados":achados,"pressao_sem_conc":pressao_sem_conc,
             "inicio":inicio,"fim":fim,"raio":int(raio_relacao),"n_os":len(os_df),"n_bp":len(bp),"grupos":grupos,"raio_cluster":int(raio_cluster),"contexto":contexto,"pressao_mapa":bp,"unidade":unidade,"os_base":os_df,"resumo_bp":resumo_bp,"contagem_origem":contagem_origem}
    except gspread.exceptions.APIError as ex:
        codigo=getattr(getattr(ex,"response",None),"status_code",None)
        st.error("Quota temporária do Google Sheets excedida (429)." if codigo==429 else f"Falha na consulta às fontes (HTTP {codigo or 'desconhecido'}).")
        st.stop()
    except Exception as ex:
        st.error(f"Falha ao preparar relatório: {ex}")
        st.stop()

r=st.session_state.get("relatorio_coi_integrado")
# Streamlit mantém session_state entre atualizações de código. Um resultado salvo
# pela versão anterior não tem as chaves "achados"/"associados" e não pode ser
# apresentado como se tivesse passado pela análise nova.
_chaves_necessarias = {
    "resumo", "associados", "territoriais", "concentracoes", "achados", "grupos", "raio_cluster",
    "pressao_sem_conc", "inicio", "fim", "raio", "n_os", "n_bp", "contexto", "pressao_mapa", "unidade", "os_base", "resumo_bp", "contagem_origem"
}
if r is not None and (
    not isinstance(r, dict)
    or r.get("versao_esquema") != 12
    or not _chaves_necessarias.issubset(r)
):
    st.session_state.pop("relatorio_coi_integrado", None)
    r = None
    st.info("O diagnóstico anterior é de uma versão antiga. Clique em **Gerar diagnóstico integrado** para atualizar a análise.")
if r:
    resumo=r["resumo"]
    tem_pressao = r["n_bp"] > 0
    if tem_pressao:
        a,b,c,d=st.columns(4)
        a.metric("O.S. no período",r["n_os"])
        b.metric("Concentrações",len(resumo))
        c.metric("Medições no período",r["n_bp"])
        d.metric("Concentrações com medição próxima",int((resumo["Medições próximas"]>0).sum()) if not resumo.empty else 0)
    else:
        a,b=st.columns(2)
        a.metric("O.S. no período",r["n_os"])
        b.metric("Concentrações",len(resumo))
    st.subheader(f"Análise operacional — {r['unidade']}")
    for achado in r["achados"]:
        st.write("• " + achado)
    if not resumo.empty or tem_pressao:
        st.subheader("Parte 1 — Farol Operacional")
        st.caption("Concentrações, O.S., eventos e ativos operacionais; o relatório de baixa pressão vem em seguida.")
        mapa=mapa_integrado_farol(r["concentracoes"],r["grupos"],r["pressao_mapa"].iloc[:0],r["contexto"],
                                  r["raio_cluster"],pontos_todos=r["os_base"])
        if mapa is not None:
            st_folium(mapa,height=500,width=None,returned_objects=[],key="mapa_integrado_relatorio")
        st.subheader("Concentrações operacionais")
        visiveis=["Concentração","Município","Bairros","O.S.","Matrículas"]
        if not resumo.empty and ((resumo["Medições próximas"]>0)|(resumo["Medições mesmo bairro"]>0)).any():
            visiveis += ["Medições próximas","Medições mesmo bairro","Menor pressão (mca)","Classificação indicativa"]
        if not resumo.empty:
            st.dataframe(resumo[visiveis],hide_index=True,use_container_width=True)
        else:
            st.caption("Não há concentração nesta seleção. Os pontos medidos do Módulo 1 continuam visíveis no mapa.")
        # Um PDF gerado no dia anterior nunca deve continuar disponível após
        # a expiração diária das imagens compartilhadas.
        if (st.session_state.get("pdf_relatorio_integrado_farol") and
            st.session_state.get("pdf_relatorio_integrado_dia") != _dia_evidencias().isoformat()):
            st.session_state.pop("pdf_relatorio_integrado_farol", None)
            st.session_state.pop("pdf_relatorio_integrado_dia", None)
        if st.button("📄 Preparar relatório cartográfico (PDF)", type="primary", key="gerar_pdf_ri_farol"):
            st.session_state.pop("pdf_relatorio_integrado_farol", None)
            st.session_state.pop("pdf_relatorio_integrado_dia", None)
            try:
                with st.spinner("Capturando mapas e montando as fichas operacionais..."):
                    dia_graficos = _dia_evidencias()
                    # Gráficos enviados hoje só acompanham relatórios cujo
                    # período inclui o dia atual; não atribuí-los a datas antigas.
                    indice_graficos = (
                        carregar_graficos_do_dia_somente_leitura(ID_FAROL, dia_graficos)
                        if r["inicio"] <= dia_graficos <= r["fim"] else {}
                    )
                    pdf = pdf_relatorio(
                        resumo,r["associados"],r["territoriais"],r["achados"],r["inicio"],r["fim"],
                        r["raio"],r["n_os"],r["n_bp"],r["concentracoes"],r["grupos"],r["raio_cluster"],
                        r["contexto"],r["pressao_mapa"],r["os_base"],
                        indice_graficos=indice_graficos,
                        raio_operacional_loggers=RAIO_OPERACIONAL_EVIDENCIAS)
                    st.session_state["pdf_relatorio_integrado_farol"] = pdf
                    st.session_state["pdf_relatorio_integrado_dia"] = dia_graficos.isoformat()
            except Exception as erro_pdf:
                st.error(f"Não foi possível preparar o PDF integrado: {erro_pdf}")
        if st.session_state.get("pdf_relatorio_integrado_farol"):
            st.download_button("⬇️ Baixar relatório no padrão Farol (PDF)",
                data=st.session_state["pdf_relatorio_integrado_farol"],
                file_name="relatorio_operacional_integrado_coi.pdf",mime="application/pdf")
        if not resumo.empty:
            st.download_button("📋 Baixar diagnóstico (CSV)",data=resumo[visiveis].to_csv(index=False,sep=";").encode("utf-8-sig"),
                file_name="diagnostico_integrado_coi.csv",mime="text/csv")
        if not r["associados"].empty:
            with st.expander("Medições associadas por proximidade geográfica"):
                campos=["Concentração","Data","Municipio","Bairro","Matricula","Pressao_MCA","_faixa","Distância até O.S. (m)","Observacao"]
                st.dataframe(r["associados"].reindex(columns=campos),hide_index=True,use_container_width=True)
        if not r["territoriais"].empty:
            with st.expander("Indícios apenas pelo mesmo bairro (sem proximidade confirmada)"):
                campos=["Concentração","Data","Municipio","Bairro","Matricula","Pressao_MCA","_faixa","Observacao"]
                st.dataframe(r["territoriais"].reindex(columns=campos),hide_index=True,use_container_width=True)
        if not r["pressao_sem_conc"].empty:
            with st.expander("Medições até 5 mca em bairros sem concentração"):
                st.dataframe(r["pressao_sem_conc"][["Data","Municipio","Bairro","Pressao_MCA","Observacao"]]
                    .assign(Observacao=lambda d: d["Observacao"].map(ri_bp_observacao_visivel)),
                    hide_index=True,use_container_width=True)
    if tem_pressao:
        st.divider()
        st.subheader("Parte 2 — Monitoramento de Baixa Pressão")
        st.caption("Dados do Módulo 1 analisados independentemente das concentrações de O.S. "
                   "A pressão média é amostral, calculada com as aferições disponíveis.")
        bp=r["pressao_mapa"]
        vals=pd.to_numeric(bp["_pressao"],errors="coerce").dropna()
        bairros=r["resumo_bp"]
        k1,k2,k3,k4,k5=st.columns(5)
        k1.metric("Medições",len(vals))
        k2.metric("Bairros aferidos",len(bairros))
        k3.metric("Pressão média",f"{vals.mean():.1f} mca")
        k4.metric("Menor pressão",f"{vals.min():g} mca")
        k5.metric("Até 5 mca",f"{vals.le(5).sum()} ({vals.le(5).mean()*100:.0f}%)")
        mapa_bp=mapa_exclusivo_pressao(bp)
        if mapa_bp is not None:
            st.subheader("Mapa das medições")
            st_folium(mapa_bp,height=465,width=None,returned_objects=[],key="mapa_pressao_independente")
            st.caption("Marcadores: vermelho (0–5 mca), amarelo (>5–10 mca), roxo (>15 mca). "
                       "Os registros entre 10 e 15 mca continuam nas análises, mas não são exibidos no mapa.")
        st.subheader("Dashboard executivo de pressão")
        # Apenas dois gráficos, no tema escuro do modelo aprovado.
        faixas=contagem_faixas_dashboard(vals)
        df_faixas=pd.DataFrame({"Faixa":list(faixas),"Medições":list(faixas.values())})
        df_bar=(bairros.sort_values(["Média (mca)","Medições"],
                                    ascending=[True,False]).head(12).copy())
        df_bar["Local"]=(df_bar["Bairro"]+" / "+df_bar["Município"]).str.upper()
        altura_dash=max(425,min(650,150+len(df_bar)*48))
        g1,g2=st.columns(2,gap="medium")
        with g1:
            fig=px.pie(df_faixas,values="Medições",names="Faixa",hole=.45,
                       title="Distribuição por faixa de pressão",
                       color="Faixa",color_discrete_map=CORES_FAIXAS_PRESSAO,
                       category_orders={"Faixa":list(CORES_FAIXAS_PRESSAO)})
            fig.update_traces(textinfo="percent",textposition="inside",
                              insidetextfont=dict(color="white",size=13),
                              sort=False,marker=dict(line=dict(width=0)),
                              hovertemplate="<b>%{label}</b><br>%{value} medição(ões) (%{percent})<extra></extra>")
            fig.update_layout(
                paper_bgcolor=FUNDO_GRAFICO_PRESSAO,
                plot_bgcolor=FUNDO_GRAFICO_PRESSAO,
                font=dict(color="#ffffff",size=11),
                title=dict(font=dict(size=19,color="white"),x=.01,xanchor="left"),
                height=altura_dash,
                margin=dict(l=14,r=14,t=75,b=78),
                legend=dict(orientation="h",x=0,y=-.10,xanchor="left",
                            font=dict(color="white",size=10),title_text=""),
                showlegend=True)
            st.plotly_chart(fig,use_container_width=True)
        with g2:
            fig=px.bar(df_bar,y="Local",x="Média (mca)",orientation="h",
                       title="Média das aferições por bairro",
                       hover_data={"Medições":True,"Mín. (mca)":True,
                                   "Máx. (mca)":True,"Local":False},
                       color_discrete_sequence=["#327cb7"])
            fig.update_traces(marker_line_width=0,
                              hovertemplate="<b>%{y}</b><br>Média: %{x:.2f} mca<extra></extra>")
            fig.update_yaxes(categoryorder="array",categoryarray=df_bar["Local"].tolist(),
                             title_text=None,showgrid=False,zeroline=False,
                             tickfont=dict(color="white",size=11),automargin=True)
            fig.update_xaxes(title_text="Média (mca)",showgrid=False,zeroline=False,
                             tickfont=dict(color="white",size=11),
                             title_font=dict(color="white",size=12),rangemode="tozero")
            fig.update_layout(
                paper_bgcolor=FUNDO_GRAFICO_PRESSAO,
                plot_bgcolor=FUNDO_GRAFICO_PRESSAO,
                font=dict(color="white",size=11),
                title=dict(font=dict(size=19,color="white"),x=.01,xanchor="left"),
                height=altura_dash,
                margin=dict(l=8,r=15,t=75,b=35),
                bargap=.24,showlegend=False)
            st.plotly_chart(fig,use_container_width=True)
        st.subheader("Diagnóstico por bairro")
        for item in achados_baixa_pressao(bp,bairros,bp):
            st.write("• "+item)
        st.dataframe(bairros,hide_index=True,use_container_width=True)

    else:
        origem=r["contagem_origem"]
        st.caption("Módulo 1: nenhuma medição válida para o período, unidade e município selecionados. "
                   f"Registros disponíveis na fonte: {origem['total']}; datas reconhecidas: "
                   f"{origem['data_valida']}; pressões numéricas: {origem['pressao_valida']}; "
                   f"válidas no período (antes de filtrar unidade): {origem['periodo']}.")
else:
    st.info("Selecione o período e clique em **Gerar diagnóstico integrado**. As bases são consultadas somente quando solicitado.")
