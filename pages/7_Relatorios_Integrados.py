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
from integracao_acompanhamento_producao import listar_vinculos, dados_acompanhamento
from farol_acomp_supabase_backend import fonte_supabase
from farol_mesclagem_operacional import listar_mesclas as listar_mesclas_farol, aplicar_mesclas as aplicar_mesclas_farol

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



def aplicar_mesclagens_do_modulo_3(concentracoes, grupos):
    """Reaproveita as mesclagens confirmadas no Farol sem alterar O.S. originais.

    Converte o esquema de exibição do Módulo 7 para o do Módulo 3, executa a
    mesma regra de identificação pelas O.S. e restaura as colunas do relatório.
    """
    if concentracoes.empty or grupos.empty or not fonte_supabase():
        return concentracoes, grupos
    mesclas = listar_mesclas_farol()
    if not mesclas:
        return concentracoes, grupos
    compat = concentracoes.rename(columns={
        "Concentração": "ID_CONCENTRACAO", "_grupo": "GRUPO",
        "Latitude": "LATITUDE", "Longitude": "LONGITUDE",
        "O.S.": "QTD_OS", "Matrículas": "QTD_MATRICULAS",
        "Bairros": "BAIRROS",
    })
    compat, grupos_mesclados = aplicar_mesclas_farol(compat, grupos, mesclas)
    if not any(str(g).startswith("MESCLA_") for g in compat["GRUPO"]):
        return concentracoes, grupos
    saida = []
    por_grupo = {
        grupo: dados for grupo, dados in grupos_mesclados.groupby("_grupo", sort=False)
    }
    for _, c in compat.iterrows():
        dados = por_grupo.get(c["GRUPO"])
        if dados is None or dados.empty:
            continue
        cidades = sorted(set(dados["_municipio"]) - {""})
        bairros = sorted(set(dados["_bairro"]) - {""})
        pares = sorted({
            (str(r["_municipio"]), str(r["_bairro"]))
            for _, r in dados.iterrows() if r["_municipio"] and r["_bairro"]
        })
        registro = {
            "Concentração": str(c["ID_CONCENTRACAO"]),
            "_grupo": c["GRUPO"],
            "Latitude": float(c["LATITUDE"]),
            "Longitude": float(c["LONGITUDE"]),
            "O.S.": int(c["QTD_OS"]),
            "Matrículas": int(c["QTD_MATRICULAS"]),
            "Município": ", ".join(cidades),
            "Bairros": ", ".join(bairros),
            "_unidade": str(dados["_unidade"].iloc[0]),
            "_pares_bairro": pares,
        }
        for campo in ("CHAVE_MESCLA", "AREA_MESCLADA", "EVENTO_MESCLA", "CODIGOS_ORIGEM"):
            valor = c.get(campo)
            if isinstance(valor, (str, dict, list)) and valor:
                registro[campo] = valor
        saida.append(registro)
    return pd.DataFrame(saida), grupos_mesclados


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
    """Mapa de aferições com coordenadas numéricas (Sheets aceita vírgula decimal).

    Nunca calcula a média das colunas brutas Latitude/Longitude: quando são
    strings, pandas pode concatená-las e falhar ao converter o resultado.
    Utiliza as coordenadas já normalizadas de preparar_pressao quando existem;
    a conversão local também protege chamadas de outras rotinas do PDF.
    """
    if base is None or base.empty:
        return None
    validos = base.copy()
    coluna_lat = "_lat" if "_lat" in validos.columns else "Latitude"
    coluna_lon = "_lon" if "_lon" in validos.columns else "Longitude"
    if coluna_lat not in validos.columns or coluna_lon not in validos.columns:
        return None
    validos["Latitude"] = validos[coluna_lat].map(lambda valor: coordenada(valor, "lat"))
    validos["Longitude"] = validos[coluna_lon].map(lambda valor: coordenada(valor, "lon"))
    validos = validos.dropna(subset=["Latitude", "Longitude", "_pressao"])
    if validos.empty:
        return None
    mapa = folium.Map(
        location=[float(validos["Latitude"].mean()), float(validos["Longitude"].mean())],
        zoom_start=12, tiles="OpenStreetMap", control_scale=True, zoom_control=False)
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
    if fonte_supabase():
        # Mesma fonte ativa do Módulo 3: fichas e vínculos atuais no Supabase.
        contexto["vinculos_farol_producao"] = pd.DataFrame(listar_vinculos())
        contexto["eventos_acomp"] = pd.DataFrame(dados_acompanhamento().values())
    elif id_acomp and id_acomp != ID_FAROL:
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
        chave_mescla = c.get("CHAVE_MESCLA")
        chave = (
            str(chave_mescla) if isinstance(chave_mescla, str) and chave_mescla
            else chave_concentracao(municipios[0], float(c["Latitude"]),
                                    float(c["Longitude"]), numeros)
        )
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


# ============================================================
# RELATORIO INTEGRADO — NOVO PADRAO VISUAL INSTITUCIONAL
# Mantem os calculos, as fontes e as permissoes existentes do modulo 7.
# O PDF utiliza desenho por pagina para evitar cortes e LayoutError de tabelas.
# ============================================================
from reportlab.pdfgen import canvas as _ri7_canvas
from reportlab.lib.utils import ImageReader as _ri7_ImageReader
from reportlab.graphics import renderPDF as _ri7_renderPDF
from reportlab.lib.colors import HexColor as _ri7_HexColor
from PIL import Image as _ri7_PILImage, ImageDraw as _ri7_ImageDraw

RI7_AZUL = _ri7_HexColor('#063E92')
RI7_AZUL_ESCURO = _ri7_HexColor('#002B70')
RI7_AZUL_MEDIO = _ri7_HexColor('#0073BE')
RI7_TURQUESA = _ri7_HexColor('#00C7D8')
RI7_CINZA = _ri7_HexColor('#E9F3FD')
RI7_TEXTO = _ri7_HexColor('#0C2148')
RI7_A4_LARGURA, RI7_A4_ALTURA = landscape(A4)

# Marca recortada exclusivamente do modelo visual enviado pelo usuario.
# Embutida no codigo para nao criar dependencias de imagens externas no GitHub.
RI7_LOGO_AEGEA = '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'


def ri7_registrar_fontes():
    raiz = '/usr/share/fonts/truetype/dejavu/'
    regular, negrito = raiz + 'DejaVuSans.ttf', raiz + 'DejaVuSans-Bold.ttf'
    if os.path.isfile(regular) and 'RI7Regular' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('RI7Regular', regular))
        pdfmetrics.registerFont(TTFont('RI7Bold', negrito))
    if 'RI7Regular' in pdfmetrics.getRegisteredFontNames():
        return 'RI7Regular', 'RI7Bold'
    return 'Helvetica', 'Helvetica-Bold'


def ri7_retangulo(pdf, x, y, largura, altura, fundo='#FFFFFF', borda=None, raio=7, espessura=.6):
    pdf.saveState()
    pdf.setFillColor(_ri7_HexColor(fundo) if isinstance(fundo, str) else fundo)
    if borda:
        pdf.setStrokeColor(_ri7_HexColor(borda) if isinstance(borda, str) else borda)
        pdf.setLineWidth(espessura)
    pdf.roundRect(x, y, largura, altura, raio, stroke=int(bool(borda)), fill=1)
    pdf.restoreState()


def ri7_gradiente(pdf, x, y, largura, altura, cor_inicio='#034A9B', cor_fim='#00C7D8', raio=7):
    pdf.saveState()
    caminho = pdf.beginPath()
    caminho.roundRect(x, y, largura, altura, raio)
    pdf.clipPath(caminho, stroke=0, fill=0)
    pdf.linearGradient(x, y, x + largura, y, [_ri7_HexColor(cor_inicio), _ri7_HexColor(cor_fim)])
    pdf.restoreState()


def ri7_escrever(pdf, texto, x, y, tamanho=10, cor=RI7_TEXTO,
                  negrito=False, largura=None, alinhar='left'):
    fonte = pdf._ri7_bold if negrito else pdf._ri7_regular
    txt = str(texto if texto is not None else '')
    pdf.saveState()
    pdf.setFont(fonte, tamanho)
    pdf.setFillColor(_ri7_HexColor(cor) if isinstance(cor, str) else cor)
    if largura is not None:
        limite = max(1, largura)
        if pdfmetrics.stringWidth(txt, fonte, tamanho) > limite:
            while txt and pdfmetrics.stringWidth(txt + '…', fonte, tamanho) > limite:
                txt = txt[:-1]
            txt = txt.rstrip() + '…'
    if alinhar == 'right':
        pdf.drawRightString(x, y, txt)
    elif alinhar == 'center':
        pdf.drawCentredString(x, y, txt)
    else:
        pdf.drawString(x, y, txt)
    pdf.restoreState()


def ri7_quebrar_texto(texto, largura, fonte, tamanho):
    palavras = str(texto if texto is not None else '').replace('\r', '').split()
    if not palavras:
        return []
    linhas, atual = [], ''
    for palavra in palavras:
        candidato = (atual + ' ' + palavra).strip()
        if atual and pdfmetrics.stringWidth(candidato, fonte, tamanho) > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = candidato
    if atual:
        linhas.append(atual)
    return linhas


def ri7_paragrafo(pdf, texto, x, y, largura, tamanho=9, entrelinha=12,
                  cor=RI7_TEXTO, negrito=False, max_linhas=None, bullet=False):
    fonte = pdf._ri7_bold if negrito else pdf._ri7_regular
    linhas = ri7_quebrar_texto(texto, largura - (9 if bullet else 0), fonte, tamanho)
    if max_linhas is not None and len(linhas) > max_linhas:
        linhas = linhas[:max_linhas]
        if linhas:
            linhas[-1] = linhas[-1].rstrip(' .') + '…'
    for indice, linha in enumerate(linhas):
        ri7_escrever(pdf, ('• ' if bullet and indice == 0 else '  ' if bullet else '') + linha,
                     x, y - indice * entrelinha, tamanho, cor, negrito, largura)
    return y - entrelinha * len(linhas)


def ri7_descricao_quebrar_linhas(texto, largura, fonte, tamanho):
    """Respeita as quebras manuais do acompanhamento, sem unir registros distintos.

    Cada linha original não vazia inicia um item. Linhas automáticas de
    continuação permanecem alinhadas ao texto, e linhas em branco são mantidas.
    Retorna (texto_da_linha, inicia_item), com None para espaçamentos manuais.
    """
    texto = str(texto if texto is not None else '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not texto:
        return []
    saida = []
    for trecho in texto.split('\n'):
        trecho = re.sub(r'^\s*(?:[•*]\s*|[-–]\s+)', '', trecho).strip()
        if not trecho:
            if saida and saida[-1] is not None:
                saida.append(None)
            continue
        linhas = ri7_quebrar_texto(trecho, max(30, largura - 12), fonte, tamanho)
        for indice, linha in enumerate(linhas):
            saida.append((linha, indice == 0))
    while saida and saida[-1] is None:
        saida.pop()
    return saida


def ri7_ajustar_descricao(texto, largura, fonte, altura_util=62):
    """Procura a maior fonte que mantenha a descrição inteira no cartão.

    Quando o conteúdo excede até mesmo a fonte mínima, sinaliza a necessidade
    de página complementar; nunca descarta definitivamente as linhas originais.
    """
    for tamanho in (7.5, 7.25, 7.0, 6.75, 6.5, 6.25, 6.0):
        linhas = ri7_descricao_quebrar_linhas(texto, largura, fonte, tamanho)
        entrelinha = max(8.0, tamanho * 1.38)
        if not linhas or (len(linhas) - 1) * entrelinha <= altura_util:
            return tamanho, entrelinha, linhas, False
    tamanho = 6.0
    linhas = ri7_descricao_quebrar_linhas(texto, largura, fonte, tamanho)
    return tamanho, max(8.0, tamanho * 1.38), linhas, True


def ri7_desenhar_descricao(pdf, texto, x=568, topo=139, largura=237, base=76):
    """Desenha linhas independentes e reduz a fonte, sem ultrapassar o quadro."""
    tamanho, entrelinha, linhas, excedeu = ri7_ajustar_descricao(
        texto, largura, pdf._ri7_regular, altura_util=topo - base)
    limite = int((topo - base) / entrelinha) + 1
    # Reserva a última linha para indicar a continuação, quando necessária.
    visiveis = linhas[:max(1, limite - 1)] if excedeu else linhas
    for indice, entrada in enumerate(visiveis):
        if entrada is None:
            continue
        linha, inicio = entrada
        y = topo - indice * entrelinha
        if inicio:
            ri7_escrever(pdf, '•', x, y, tamanho, largura=10)
        ri7_escrever(pdf, linha, x + 11, y, tamanho, largura=largura-12)
    if excedeu:
        ri7_escrever(pdf, 'Texto completo na página seguinte',
                     x, topo - (limite - 1) * entrelinha,
                     6.1, RI7_AZUL, negrito=True, largura=largura)
    return excedeu


def ri7_tarja(pdf, titulo, x, y, largura, altura=27, tamanho=12, icone=''):
    ri7_gradiente(pdf, x, y, largura, altura)
    ri7_escrever(pdf, (icone + '   ' if icone else '') + titulo, x+13, y+8,
                 tamanho=tamanho, cor='#FFFFFF', negrito=True, largura=largura-21)


class RI7CanvasNumerado(_ri7_canvas.Canvas):
    """Numera paginas ao final; nao altera a contagem nem repete o conteudo."""
    def __init__(self, *args, **kwargs):
        _ri7_canvas.Canvas.__init__(self, *args, **kwargs)
        self._ri7_paginas = []
        self._ri7_regular, self._ri7_bold = ri7_registrar_fontes()

    def showPage(self):
        self._ri7_paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._ri7_paginas)
        for estado in self._ri7_paginas:
            self.__dict__.update(estado)
            ri7_retangulo(self, RI7_A4_LARGURA-98, RI7_A4_ALTURA-30, 79, 18,
                           '#D9EAFB', None, raio=9)
            ri7_escrever(self, f'Página {self._pageNumber} de {total}',
                         RI7_A4_LARGURA-58.5, RI7_A4_ALTURA-24.1,
                         7.3, RI7_AZUL_ESCURO, negrito=True, alinhar='center')
            _ri7_canvas.Canvas.showPage(self)
        _ri7_canvas.Canvas.save(self)


def ri7_cabecalho(pdf, titulo, inicio, fim, unidade, rodape, tipo_campo='Unidade'):
    largura, altura = RI7_A4_LARGURA, RI7_A4_ALTURA
    pdf.setFillColor(colors.white)
    pdf.rect(0, 0, largura, altura, stroke=0, fill=1)
    # Ondas institucionais: duas faixas vetoriais independentes e suaves.
    pdf.saveState()
    onda = pdf.beginPath()
    onda.moveTo(largura-225, altura)
    onda.curveTo(largura-120, altura-5, largura-125, altura-59, largura, altura-60)
    onda.lineTo(largura, altura)
    onda.close()
    pdf.setFillColor(_ri7_HexColor('#00DDE4'))
    pdf.drawPath(onda, fill=1, stroke=0)
    onda2 = pdf.beginPath()
    onda2.moveTo(largura-97, altura)
    onda2.curveTo(largura-52, altura-8, largura-39, altura-44, largura, altura-46)
    onda2.lineTo(largura, altura)
    onda2.close()
    pdf.setFillColor(_ri7_HexColor('#0068C6'))
    pdf.drawPath(onda2, fill=1, stroke=0)
    pdf.restoreState()
    # Identidade visual — logotipo sem arquivo auxiliar.
    try:
        logo = _ri7_ImageReader(io.BytesIO(base64.b64decode(RI7_LOGO_AEGEA)))
        pdf.drawImage(logo, 26, altura-61, width=105, height=37, mask='auto')
    except Exception:
        ri7_escrever(pdf, 'aegea', 26, altura-51, 24, RI7_AZUL, negrito=True)
    pdf.setStrokeColor(RI7_AZUL)
    pdf.setLineWidth(1)
    pdf.line(150, altura-61, 150, altura-12)
    # Nunca imprime sobre a aba de numero de pagina.
    ri7_escrever(pdf, titulo, 164, altura-34, tamanho=16.3, cor=RI7_AZUL,
                 negrito=True, largura=545)
    ri7_escrever(pdf, f'Período: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}   |   {tipo_campo}: {unidade}',
                 165, altura-55, tamanho=9.5, cor=RI7_AZUL_ESCURO,
                 negrito=True, largura=565)
    # Duas curvas de rodape, semelhantes ao modelo enviado.
    pdf.saveState()
    c1 = pdf.beginPath()
    c1.moveTo(largura*.43, 0)
    c1.curveTo(largura*.66, 42, largura*.78, 38, largura, 12)
    c1.lineTo(largura, 0)
    c1.close()
    pdf.setFillColor(_ri7_HexColor('#86F4F2'))
    pdf.drawPath(c1, fill=1, stroke=0)
    c2 = pdf.beginPath()
    c2.moveTo(largura*.68, 0)
    c2.curveTo(largura*.84, 25, largura*.96, 27, largura, 37)
    c2.lineTo(largura, 0)
    c2.close()
    pdf.setFillColor(_ri7_HexColor('#00C6D7'))
    pdf.drawPath(c2, fill=1, stroke=0)
    pdf.restoreState()
    ri7_escrever(pdf, rodape, 26, 26, tamanho=9.4, cor=RI7_AZUL, negrito=True)


def ri7_painel_kpi(pdf, x, y, w, h, rotulo, numero, subtitulo='', tamanho=23):
    ri7_retangulo(pdf, x, y, w, h, '#EAF3FD', '#CBDFF2', raio=9)
    ri7_escrever(pdf, numero, x+w/2, y+(7 if h<65 else 15 if h<90 else h*.34), tamanho, RI7_AZUL,
                 negrito=True, largura=w-16, alinhar='center')
    texto = str(rotulo)
    linhas = ri7_quebrar_texto(texto, w-18, pdf._ri7_bold, 10)
    for k, linha in enumerate(linhas[:3]):
        ri7_escrever(pdf, linha, x+w/2, y+h-22-k*12, 10,
                     RI7_TEXTO, negrito=True, alinhar='center')
    if subtitulo:
        ri7_escrever(pdf, subtitulo, x+w/2, y+7, 6.7,
                     RI7_AZUL_ESCURO, alinhar='center', largura=w-15)


def ri7_quadro_imagem(pdf, imagem, x, y, w, h):
    ri7_retangulo(pdf, x-1, y-1, w+2, h+2, '#FFFFFF', '#BBD0E4', raio=7)
    if imagem:
        try:
            pdf.drawImage(_ri7_ImageReader(io.BytesIO(imagem)), x, y,
                          width=w, height=h, preserveAspectRatio=False, mask='auto')
            return
        except Exception:
            pass
    ri7_escrever(pdf, 'Mapa indisponível para esta seleção', x+w/2, y+h/2,
                 10, RI7_AZUL_ESCURO, alinhar='center')


def ri7_imagem_cartografica(mapa, largura, altura, registros=None):
    """Mapa real por Playwright. Em falha, desenho esquematico e identificado."""
    try:
        if mapa is not None:
            return imagem_folium(mapa, largura, altura)
    except Exception:
        pass
    imagem = _ri7_PILImage.new('RGB', (largura, altura), '#F2F6FA')
    desenho = _ri7_ImageDraw.Draw(imagem)
    desenho.rectangle((8, 8, largura-8, altura-8), outline='#B9CAD9', width=2)
    desenho.text((15, 17), 'MAPA-BASE INDISPONIVEL - COORDENADAS REAIS', fill='#174E88')
    coordenadas = []
    if isinstance(registros, pd.DataFrame) and not registros.empty:
        for _, linha in registros.iterrows():
            lat = flutuante(linha.get('_lat', linha.get('Latitude', None)))
            lon = flutuante(linha.get('_lon', linha.get('Longitude', None)))
            if lat is not None and lon is not None and -90<=lat<=90 and -180<=lon<=180:
                coordenadas.append((lat,lon))
    if coordenadas:
        lats = [p[0] for p in coordenadas]
        lons = [p[1] for p in coordenadas]
        lat0, lat1 = min(lats)-.006, max(lats)+.006
        lon0, lon1 = min(lons)-.006, max(lons)+.006
        for lat,lon in coordenadas:
            xx = 26+(largura-52)*(lon-lon0)/max(1e-8,lon1-lon0)
            yy = altura-24-(altura-64)*(lat-lat0)/max(1e-8,lat1-lat0)
            desenho.ellipse((xx-6,yy-6,xx+6,yy+6), fill='#1A77C7', outline='white',width=2)
    buff=io.BytesIO()
    imagem.save(buff,format='PNG')
    return buff.getvalue()


def ri7_contar_os_com_medicao_proxima(pontos, pressao, raio=750):
    """Contagem por O.S. unica, nao por concentracao; mesmo municipio e ate raio m."""
    if (pontos is None or pontos.empty or pressao is None or pressao.empty):
        return 0
    bp = pressao.dropna(subset=['_lat','_lon']).copy()
    if '_pressao' in bp.columns:
        bp = bp[bp['_pressao'].notna()]
    if bp.empty:
        return 0
    gps={}
    for municipio, sub in bp.groupby('_municipio',dropna=False):
        gps[municipio] = list(zip(sub['_lat'].astype(float),sub['_lon'].astype(float)))
    encontrados=set()
    for indice, (_, os_) in enumerate(pontos.iterrows()):
        lat,lon=os_.get('_lat'),os_.get('_lon')
        if pd.isna(lat) or pd.isna(lon):
            continue
        cidade=os_.get('_municipio','')
        if not any(haversine(float(lat),float(lon),lt,ln)<=raio for lt,ln in gps.get(cidade, [])):
            continue
        identificador=str(os_.get('Nº da O.S','') or '').strip() or f'SEM_NUMERO_{indice}'
        encontrados.add(identificador)
    return len(encontrados)


def ri7_descrever_unidade(concentracoes):
    if concentracoes is None or concentracoes.empty:
        return 'Operação integrada'
    nomes = set(concentracoes['_unidade'].astype(str)) if '_unidade' in concentracoes else set()
    mapa={'THE':'Águas de Teresina','API':'Águas do Piauí','TIM':'Timon'}
    return ', '.join(mapa.get(v,v) for v in sorted(nomes)) or 'Operação integrada'


def ri7_pagina_panorama(pdf, concentracoes, grupos, pressao, pontos_todos,
                        inicio, fim, contexto, raio_cluster, raio_proximidade):
    ri7_cabecalho(pdf,'Farol Operacional — Relatório de Concentrações',
                  inicio,fim,ri7_descrever_unidade(concentracoes),'Visão Geral')
    ri7_tarja(pdf, 'Panorama Territorial das Concentrações', 25, 459, 370, 30)
    ri7_tarja(pdf, 'Panorama do Período', 410, 459, 407, 30)
    mapa = None
    if concentracoes is not None and not concentracoes.empty:
        mapa = mapa_integrado_farol(concentracoes,grupos,pressao.iloc[:0],contexto,
                                    raio_cluster,pontos_todos=pontos_todos)
    imagem = ri7_imagem_cartografica(mapa, 960, 920, pontos_todos)
    ri7_quadro_imagem(pdf, imagem, 26, 99, 368, 353)
    # Quantidades sao derivadas das bases reais, sem vinculo presumido por bairro.
    qtd_os = len(pontos_todos) if pontos_todos is not None else 0
    qtd_conc = len(concentracoes) if concentracoes is not None else 0
    qtd_bp = int(pressao['_pressao'].notna().sum()) if not pressao.empty else 0
    qtd_bairros = int(pressao[['_municipio','_bairro']].drop_duplicates().shape[0]) if not pressao.empty else 0
    media = float(pressao['_pressao'].dropna().mean()) if qtd_bp else None
    qtd_prox = ri7_contar_os_com_medicao_proxima(pontos_todos, pressao, raio_proximidade)
    ri7_painel_kpi(pdf, 410, 365, 199, 83, 'Reclamações do período', str(qtd_os), tamanho=25)
    ri7_painel_kpi(pdf, 618, 365, 199, 83, 'Concentrações', str(qtd_conc), tamanho=25)
    ri7_painel_kpi(pdf, 410, 272, 199, 83, 'Aferições de pressão do período', str(qtd_bp), tamanho=25)
    ri7_painel_kpi(pdf, 618, 272, 199, 83,
                    f'Reclamações com aferição próxima (≤ {raio_proximidade} m)',
                    str(qtd_prox), tamanho=25)
    ri7_tarja(pdf, 'Monitoramento de Pressão', 410, 228, 407, 30)
    ri7_painel_kpi(pdf, 410, 99, 129, 118, 'Aferições do período', str(qtd_bp), tamanho=25)
    ri7_painel_kpi(pdf, 548, 99, 129, 118, 'Bairros distintos', str(qtd_bairros), tamanho=25)
    ri7_painel_kpi(pdf, 686, 99, 131, 118, 'Média de pressão do período',
                    f'{media:.2f} mca'.replace('.',',') if media is not None else '—', tamanho=18)



# ============================================================
# RELATORIO INSTITUCIONAL — STATUS DA O.S. COM PRESSAO AFERIDA
# Usa exclusivamente as leituras efetivamente cadastradas no Modulo 1.
# Nao altera os status originais no Farol nem o vinculo por proximidade.
# ============================================================
def ri7_chave_protocolo_os(valor):
    """Extrai o numero-base da O.S. sem confundir sufixo /AAAA-1."""
    texto = str(valor or "").strip()
    encontrado = re.search(r"(?<!\d)(\d{5,})(?:/\d{4}-\d+)?(?!\d)", texto)
    return encontrado.group(1) if encontrado else ""


def ri7_chave_matricula(valor):
    """Mantem a matricula como identificador, sem coagir para float."""
    texto = str(valor or "").strip()
    if texto.lower() in {"nan", "nat", "none", ""}:
        return ""
    # Eventuais exportacoes numericas podem adicionar um sufixo decimal .0.
    if re.fullmatch(r"\d+\.0+", texto):
        texto = texto.split(".", 1)[0]
    return texto


def ri7_preparar_indice_pressao_os(medicoes, os_periodo):
    """Cria indices leves, uma vez por PDF (nao consulta o Sheets)."""
    por_protocolo, por_matricula = {}, {}
    matriculas_com_os = {}
    if os_periodo is not None and not os_periodo.empty:
        for _, os_ in os_periodo.iterrows():
            cidade = normalizar(os_.get("Cidade", ""))
            mat = ri7_chave_matricula(os_.get("Matrícula", ""))
            numero = ri7_chave_protocolo_os(os_.get("Nº da O.S", ""))
            if cidade and mat and numero:
                matriculas_com_os.setdefault((cidade, mat), set()).add(numero)
    if medicoes is not None and not medicoes.empty:
        for _, med in medicoes.iterrows():
            valor = med.get("_pressao")
            if valor is None or pd.isna(valor):
                continue
            try:
                valor = float(valor)
            except (ValueError, TypeError):
                continue
            if not math.isfinite(valor) or valor < 0:
                continue
            cidade = normalizar(med.get("Municipio", ""))
            mat = ri7_chave_matricula(med.get("Matricula", ""))
            cod = ri7_chave_protocolo_os(med.get(RI_BP_COLUNA_PROTOCOLO, ""))
            data_med = data_parse(med.get("Data", ""))
            if pd.isna(data_med):
                data_med = med.get("_data", pd.NaT)
            dia = pd.Timestamp(data_med).date() if pd.notna(data_med) else None
            registro = (valor, dia, cod)
            if cidade and cod:
                por_protocolo.setdefault((cidade, cod), []).append(registro)
            if cidade and mat:
                por_matricula.setdefault((cidade, mat), []).append(registro)
    return por_protocolo, por_matricula, matriculas_com_os


def ri7_pressao_inequivoca(registros):
    """Nao escolhe arbitrariamente entre leituras diferentes da mesma O.S."""
    valores = {round(float(reg[0]), 4) for reg in registros}
    if len(valores) != 1:
        return None
    valor = next(iter(valores))
    return f"{valor:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " mca"


def ri7_status_os_institucional(os_, indice):
    """Cancelada tem prioridade; pressao somente para O.S. atendida em campo.

    Correspondencia: protocolo exato; ou matricula+municipio+mesma data;
    ou matricula unica no periodo, com afericao posterior a emissao.
    Sem vinculo seguro, preserva o status original.
    """
    original = str(os_.get("Status OS", "") or "").strip()
    status = normalizar(original)
    if "CANCEL" in status:
        return "Cancelada"
    if not any(t in status for t in ("VISIT", "EXECUT", "REALIZAD", "CONCLUID",
                                      "ATENDID", "ENCERRAD", "FINALIZ")):
        return original

    por_protocolo, por_matricula, matriculas_com_os = indice
    cidade = normalizar(os_.get("Cidade", ""))
    numero = ri7_chave_protocolo_os(os_.get("Nº da O.S", ""))
    mat = ri7_chave_matricula(os_.get("Matrícula", ""))
    data_os = data_parse(os_.get("Dt. Emissão", ""))
    dia_os = pd.Timestamp(data_os).date() if pd.notna(data_os) else None

    if cidade and numero:
        por_os = por_protocolo.get((cidade, numero), [])
        if por_os:
            return ri7_pressao_inequivoca(por_os) or original

    if not (cidade and mat and dia_os):
        return original
    # Um protocolo explicito pertencente a OUTRA O.S. impede a associacao
    # por matricula, mesmo que a data e a cidade coincidam.
    candidatos = [reg for reg in por_matricula.get((cidade, mat), [])
                  if not reg[2] or reg[2] == numero]
    if not candidatos:
        return original
    protocolos = matriculas_com_os.get((cidade, mat), set())
    if len(protocolos) != 1:
        # Matricula compartilhada por varias O.S. no periodo:
        # apenas o cruzamento direto de protocolo e seguro.
        return original

    # Mesmo dia, mesmo municipio e matricula: exige valor inequivoco.
    mesmo_dia = [reg for reg in candidatos if reg[1] == dia_os]
    if mesmo_dia:
        return ri7_pressao_inequivoca(mesmo_dia) or original

    # Visita posterior a emissao: aceitar apenas se ha UMA O.S. dessa matricula
    # na selecao e UMA medicao posterior (evita atribuicoes por coincidencia).
    posteriores = [reg for reg in candidatos if reg[1] is not None and reg[1] > dia_os]
    if len(posteriores) == 1:
        return ri7_pressao_inequivoca(posteriores) or original
    return original


def ri7_rotular_status_os_grupo(grupo, indice):
    """Acrescenta somente uma coluna de exibicao, sem alterar Status OS."""
    grupo = grupo.copy()
    if grupo.empty:
        grupo["Status no relatório"] = pd.Series(dtype=str)
    else:
        grupo["Status no relatório"] = grupo.apply(
            lambda os_: ri7_status_os_institucional(os_, indice), axis=1)
    return grupo


def ri7_pagina_concentracao(pdf, conc, grupo, eventos, vinculadas, nao_vinculadas,
                            acompanhamento, contexto, pressao, inicio, fim,
                            raio_cluster, imagem_mapa=None):
    unidade = ri7_descrever_unidade(pd.DataFrame([conc]))
    ri7_cabecalho(pdf,'Farol Operacional — Relatório de Concentrações',
                  inicio,fim,unidade,'Análise da Concentração')
    codigo = str(conc.get('Concentração',''))
    cabecalho = f"{codigo} — {conc.get('Município','')} — {conc.get('Bairros','')}"
    ri7_tarja(pdf, cabecalho, 25, 459, 792, 31, tamanho=12)
    ri7_quadro_imagem(pdf, imagem_mapa, 26, 197, 317, 252)
    ri7_painel_kpi(pdf, 357, 396, 130, 53, 'Reclamações', str(len(grupo)),tamanho=18)
    ri7_painel_kpi(pdf, 494, 396, 130, 53, 'Matrículas distintas',
                   str(int(conc.get('Matrículas',0))),tamanho=18)
    ri7_retangulo(pdf, 631, 396, 186, 53, '#EAF3FD', '#CBDFF2')
    ri7_escrever(pdf, 'Eventos relacionados:', 640, 428, 9.2, RI7_AZUL,negrito=True)
    nome_evento = 'Nenhum evento relacionado'
    if eventos:
        ativos = [str(e.get('Serviço','') or e.get('Protocolo','') or '') for e in eventos]
        nome_evento = f'{len(eventos)} — ' + '; '.join(ativos)
    ri7_paragrafo(pdf, nome_evento, 640, 415, 166, tamanho=7.2,
                  entrelinha=9, max_linhas=2)
    ri7_tarja(pdf,'Ordens de Serviço vinculadas à concentração',357,356,460,30,tamanho=10.8)
    ri7_retangulo(pdf, 357, 196, 460, 151, '#FFFFFF', '#C8DBEB')
    colunas=[(366, 'O.S. / Ano'),(540,'Matrícula'),(674,'Status / Pressão')]
    pdf.setFillColor(_ri7_HexColor('#D9EAFB'))
    pdf.roundRect(363,325,448,19,5,stroke=0,fill=1)
    for xx, txt in colunas:
        ri7_escrever(pdf,txt,xx,331,8.5,RI7_AZUL,negrito=True)
    registros = [(r, 'Evento') for r in vinculadas] + [(r, 'Demais') for r in nao_vinculadas]
    max_registros = 9
    for i, (r, tipo) in enumerate(registros[:max_registros]):
        yy=314-i*12.3
        if i%2:
            pdf.setFillColor(_ri7_HexColor('#EDF5FC'))
            pdf.rect(364, yy-3, 446, 13, stroke=0, fill=1)
        ri7_escrever(pdf, protocolo_os_relatorio(r), 367, yy, 7.6, largura=161)
        ri7_escrever(pdf, str(r.get('Matrícula','')), 541, yy,7.6,largura=124)
        ri7_escrever(pdf, str(r.get('Status no relatório',r.get('Status OS',''))), 675, yy,7.6,largura=130)
    if len(registros)>max_registros:
        ri7_escrever(pdf, f'+ {len(registros)-max_registros} O.S. na página de continuação',
                     368,201,7.3,RI7_AZUL,negrito=True)
    # Quadros inferiores, ancorados — nao se deslocam conforme o numero de O.S.
    ri7_retangulo(pdf, 25, 65, 251, 118, '#FFFFFF', '#BDD4E9')
    ri7_tarja(pdf,'Acompanhamento Operacional',25,154,251,29,tamanho=9.5)
    if acompanhamento:
        ri7_escrever(pdf,'Status:',34,134,8,RI7_AZUL,negrito=True)
        ri7_escrever(pdf,str(acompanhamento.get('status','') or 'Não informado'),100,134,7.7,largura=164)
        ri7_escrever(pdf,'Finalizado:',34,116,8,RI7_AZUL,negrito=True)
        ri7_escrever(pdf,str(acompanhamento.get('finalizado','') or 'Não informado'),100,116,7.7,largura=164)
        ri7_escrever(pdf,'Município:',34,98,8,RI7_AZUL,negrito=True)
        ri7_escrever(pdf,str(acompanhamento.get('municipio','') or conc.get('Município','')),100,98,7.7,largura=164)
        ri7_escrever(pdf,'Regional:',34,80,8,RI7_AZUL,negrito=True)
        ri7_escrever(pdf,str(acompanhamento.get('regional','') or 'Não informado'),100,80,7.7,largura=164)
    else:
        ri7_paragrafo(pdf,'Sem informações de acompanhamento registradas.',34,133,232,tamanho=8)
    ri7_retangulo(pdf, 286, 65, 259, 118, '#FFFFFF', '#BDD4E9')
    ri7_tarja(pdf,'Validações Operacionais',286,154,259,29,tamanho=10)
    verificacoes=[('ArcGIS','arcgis'),('SIA','sia'),('Loggers','loggers'),('Retorno','retorno_operacao')]
    for i,(nome,chave) in enumerate(verificacoes):
        xx=294+i*61
        ri7_retangulo(pdf,xx,105,58,41,'#EAF3FD',None,raio=4)
        ri7_escrever(pdf,nome,xx+29,130,7.1,RI7_AZUL,negrito=True,alinhar='center',largura=54)
        valor=str((acompanhamento or {}).get(chave,'') or '').strip()
        chave_ok=normalizar(valor) in ('SIM','TRUE','1','OK','VALIDADO')
        ri7_escrever(pdf,('✓ ' if chave_ok else '✗ ' if valor else '– ') + (valor or 'N/A'),
                     xx+29,111,7.0,'#167A46' if chave_ok else '#B92A41',
                     negrito=True,alinhar='center',largura=54)
    ri7_retangulo(pdf, 555, 65, 262, 118, '#FFFFFF', '#BDD4E9')
    ri7_tarja(pdf,'Descrição da Ocorrência',555,154,262,29,tamanho=10)
    descricao = str((acompanhamento or {}).get('descricao','') or '').strip()
    if not descricao:
        descricao = 'Nenhum direcionamento registrado para esta concentração.'
    descricao_continua = ri7_desenhar_descricao(pdf, descricao)
    return registros, descricao_continua


def ri7_pagina_os_continuacao(pdf, conc, registros, inicio, fim, n_pagina):
    ri7_cabecalho(pdf,'Farol Operacional — Relatório de Concentrações',inicio,fim,
                  ri7_descrever_unidade(pd.DataFrame([conc])),
                  'O.S. vinculadas — continuação')
    ri7_tarja(pdf,f"{conc.get('Concentração','')} — O.S. vinculadas (continuação {n_pagina})",
              25,460,792,30,tamanho=11)
    pdf.setFillColor(_ri7_HexColor('#DCEBFA'))
    pdf.roundRect(26,429,790,23,5,fill=1,stroke=0)
    for x,t in [(40,'O.S. / Ano'),(285,'Matrícula'),(465,'Status / Pressão'),(685,'Classificação')]:
        ri7_escrever(pdf,t,x,436,9.5,RI7_AZUL,negrito=True)
    for i,(r,grupo) in enumerate(registros):
        yy=410-i*16.1
        if i%2:
            pdf.setFillColor(_ri7_HexColor('#F0F6FC'))
            pdf.rect(27,yy-4,788,16.1,stroke=0,fill=1)
        ri7_escrever(pdf,protocolo_os_relatorio(r),40,yy,9,largura=226)
        ri7_escrever(pdf,str(r.get('Matrícula','')),285,yy,9,largura=167)
        ri7_escrever(pdf,str(r.get('Status no relatório',r.get('Status OS',''))),465,yy,9,largura=200)
        ri7_escrever(pdf,grupo,685,yy,8,RI7_AZUL,largura=128)


def ri7_pagina_evidencia(pdf, conc, graficos, inicio, fim):
    for numero,(logger, imagem) in enumerate(graficos, 1):
        ri7_cabecalho(pdf,'Farol Operacional — Relatório de Concentrações',
                      inicio,fim,ri7_descrever_unidade(pd.DataFrame([conc])),
                      'Evidências de loggers')
        ri7_tarja(pdf,f"Evidência de Logger — {conc.get('Concentração','')}",25,458,792,30)
        texto = f"Logger {numero}: {rotulo_logger(logger)}  |  Distância: {logger.get('Distância','')}"
        ri7_escrever(pdf,texto,36,428,10.5,RI7_AZUL,negrito=True,largura=762)
        try:
            with _ri7_PILImage.open(io.BytesIO(imagem)) as im:
                iw,ih=im.size
            fator=min(763/iw,350/ih)
            ww,hh=iw*fator,ih*fator
            ri7_quadro_imagem(pdf,imagem, (RI7_A4_LARGURA-ww)/2, 87+(350-hh)/2, ww, hh)
        except Exception:
            ri7_escrever(pdf,'Imagem de logger inválida; não exibida.',40,399,9)
        yield


def ri7_imagens_loggers(c, contexto, indice_graficos, raio_operacional):
    imagens=[]
    loggers=loggers_proximos_da_concentracao(c, contexto, raio_operacional)
    for logger in filtrar_loggers(loggers):
        registro=(indice_graficos or {}).get(chave_logger(logger))
        if not registro or not registro.get('imagem'):
            continue
        arquivo=registro['imagem']
        try:
            with _ri7_PILImage.open(io.BytesIO(arquivo)) as im:
                im.verify()
            imagens.append((logger,arquivo))
        except Exception:
            continue
    return imagens


def ri7_pagina_bp_mapa(pdf, pressao, inicio, fim):
    municipios=sorted(set(pressao['_municipio'].astype(str)) - {''})
    nome_mun=', '.join(municipios) or 'Não informados'
    ri7_cabecalho(pdf,'Monitoramento de Baixa Pressão — COI',inicio,fim,nome_mun,
                  'Monitoramento de Pressão',tipo_campo='Município(s)')
    dados=pressao[pressao['_pressao'].notna()].copy()
    bairros=resumo_pressao_por_bairro(dados)
    n=len(dados)
    valores=dados['_pressao']
    críticos=int(valores.le(5).sum())
    lista_kpi=[('Medições',str(n)),('Bairros',str(len(bairros))),
               ('Média',f"{valores.mean():.2f} mca".replace('.',',')),
               ('Até 5 mca',f'{críticos} ({críticos/n:.0%})')]
    w=(792-3*7)/4
    for i,(nome,numero) in enumerate(lista_kpi):
        ri7_painel_kpi(pdf,25+i*(w+7),408,w,83,nome,numero,
                       tamanho=20 if 'mca' in numero else 24)
    ri7_tarja(pdf,'Mapa das aferições',25,374,494,28)
    ri7_tarja(pdf,'Pontos de atenção',532,374,285,28)
    mapa=ri_bp_mapa(dados)
    foto=ri7_imagem_cartografica(mapa,1280,682,dados)
    ri7_quadro_imagem(pdf,foto,26,106,492,262)
    ri7_retangulo(pdf,532,106,285,262,'#FFFFFF','#BFD8EC')
    if bairros.empty or not int(bairros['Até 5 mca'].sum()):
        ri7_paragrafo(pdf,'Não foram registradas medições até 5 mca no período.',
                      544,347,261,9,13)
    else:
        lideres=bairros[bairros['Até 5 mca']>0].sort_values(
            ['Até 5 mca','Medições'],ascending=False).head(5)
        yy=347
        for _,r in lideres.iterrows():
            texto=(f"{r['Bairro']} / {r['Município']}: "
                   f"{int(r['Até 5 mca'])} de {int(r['Medições'])} medições "
                   f"até 5 mca ({r['% até 5 mca']:g}%).")
            yy=ri7_paragrafo(pdf,texto,544,yy,260,8.8,13,
                              max_linhas=3,bullet=True)-7
            if yy<130: break
    ri7_escrever(pdf,'As médias e incidências referem-se aos pontos aferidos.',
                 534,89,7.3,RI7_AZUL)
    return bairros


def ri7_pagina_bp_dashboard(pdf, pressao, bairros, inicio, fim):
    municipios=sorted(set(pressao['_municipio'].astype(str)) - {''})
    ri7_cabecalho(pdf,'Monitoramento de Baixa Pressão — COI',inicio,fim,
                  ', '.join(municipios) or 'Não informados','Dashboard de Pressão', tipo_campo='Município(s)')
    ri7_tarja(pdf,'Dashboard de Pressão',25,451,792,34,tamanho=14)
    ri7_retangulo(pdf,25,88,385,350,'#0E1117',None,raio=10)
    ri7_retangulo(pdf,422,88,395,350,'#0E1117',None,raio=10)
    # Reutiliza os mesmos graficos vetoriais e dados que alimentam a tela.
    grafico_faixas=_chart_faixas_vetorial(pressao,pdf._ri7_regular)
    grafico_bairros=_chart_bairros_vetorial(bairros,pdf._ri7_regular)
    for x,desenho in ((33,grafico_faixas),(431,grafico_bairros)):
        pdf.saveState()
        fator=min(375/desenho.width,331/desenho.height)
        pdf.translate(x,104)
        pdf.scale(fator,fator)
        _ri7_renderPDF.draw(desenho,pdf,0,0)
        pdf.restoreState()


def ri7_pagina_bp_tabela(pdf, bairros, inicio, fim, municipio, pagina=1, total=1,
                         linhas_por_pagina=22):
    ri7_cabecalho(pdf,'Monitoramento de Baixa Pressão — COI',inicio,fim,municipio,
                  'Diagnóstico por Bairro', tipo_campo='Município(s)')
    ri7_tarja(pdf,'Diagnóstico por Bairro' + (f' — continuação {pagina}' if pagina>1 else ''),
              25,455,792,34,tamanho=13)
    cabecalhos=[('Bairro / Município',35,240),('n',276,36),('Matrículas',314,85),
                ('Mín.',407,58),('Média',471,65),('Máx.',543,57),('≤5 mca',605,88),('% ≤5',702,93)]
    ri7_retangulo(pdf,25,420,792,27,'#D3E7F8',None,raio=3)
    for txt,x,w in cabecalhos:
        ri7_escrever(pdf,txt,x,429,8.8,RI7_AZUL_ESCURO,negrito=True,largura=w-5)
    linhas=bairros.iloc[(pagina-1)*linhas_por_pagina:pagina*linhas_por_pagina]
    for idx,(_,r) in enumerate(linhas.iterrows()):
        yy=406-idx*14.6
        if idx%2:
            pdf.setFillColor(_ri7_HexColor('#EFF5FB'))
            pdf.rect(26,yy-4,790,14.6,stroke=0,fill=1)
        valores=[f"{r['Bairro']} / {r['Município']}",str(int(r['Medições'])),
                 str(int(r['Matrículas'])),f"{r['Mín. (mca)']:g}",
                 f"{r['Média (mca)']:.2f}",f"{r['Máx. (mca)']:g}",
                 f"{int(r['Até 5 mca'])}/{int(r['Medições'])}",
                 f"{r['% até 5 mca']:g}%"]
        for (txt,x,w),valor in zip(cabecalhos,valores):
            ri7_escrever(pdf,valor,x,yy,8.3,RI7_TEXTO,largura=w-5,
                         negrito=(x==35 and r['Até 5 mca']>0))
    ri7_escrever(pdf,'Tabela de aferições registradas; não representa a pressão de toda a rede.',
                 26,67,7.3,RI7_AZUL_ESCURO)


def ri7_pagina_sanear(pdf, base, geojson, indices, dentro, inicio, fim, municipio):
    ri7_cabecalho(pdf,'Monitoramento de Baixa Pressão — COI',inicio,fim,municipio,
                  'Anexo — Áreas Sanear', tipo_campo='Município(s)')
    ri7_tarja(pdf,'Medições em Áreas de Intervenção Sanear',25,460,792,30,tamanho=12)
    ri7_escrever(pdf,f'{len(dentro)} medições no interior ou limite das áreas; '
                 f'{len(indices)} áreas intersectadas.',30,442,9,RI7_AZUL_ESCURO)
    if dentro.empty:
        ri7_paragrafo(pdf,'Nenhuma medição do período está dentro dos polígonos Sanear.',
                      33,408,700,10)
        return
    mapa=ri_bp_mapa_sanear(dentro,geojson,indices)
    foto=ri7_imagem_cartografica(mapa,1500,397,dentro)
    ri7_quadro_imagem(pdf,foto,26,211,790,209)
    ri7_tarja(pdf,'Relação de medições nas áreas Sanear',25,175,792,28,tamanho=10)
    ri7_retangulo(pdf,25,150,792,22,'#D9EAFB',None,raio=3)
    for x,t in [(35,'Matrícula'),(220,'Cód. Protocolo'),(440,'Bairro'),(685,'Medição')]:
        ri7_escrever(pdf,t,x,156,8.7,RI7_AZUL_ESCURO,negrito=True)
    for i,(_,r) in enumerate(dentro.head(4).iterrows()):
        yy=137-i*15.5
        if i%2:
            pdf.setFillColor(_ri7_HexColor('#EFF6FC'))
            pdf.rect(26,yy-3,789,15,stroke=0,fill=1)
        protocolo=str(r.get(RI_BP_COLUNA_PROTOCOLO,'') or '').strip() or 'Não informado'
        for x,t,w in ((35,r.get('Matricula',''),165),(220,protocolo,203),
                       (440,r.get('Bairro',''),223),(685,f"{float(r['_pressao']):g} mca",117)):
            ri7_escrever(pdf,str(t),x,yy,8.5,RI7_TEXTO,largura=w)
    return dentro.iloc[4:]


def ri7_pagina_sanear_continuacao(pdf, restante, inicio, fim, municipio, num):
    ri7_cabecalho(pdf,'Monitoramento de Baixa Pressão — COI',inicio,fim,municipio,
                  'Anexo — Sanear (continuação)', tipo_campo='Município(s)')
    ri7_tarja(pdf,f'Medições nas Áreas Sanear — continuação {num}',25,458,792,30)
    ri7_retangulo(pdf,25,425,792,25,'#D9EAFB',None,raio=3)
    for x,t in [(35,'Matrícula'),(220,'Cód. Protocolo'),(440,'Bairro'),(685,'Medição')]:
        ri7_escrever(pdf,t,x,432,9.2,RI7_AZUL_ESCURO,negrito=True)
    for i,(_,r) in enumerate(restante.iterrows()):
        yy=411-i*16.3
        if i%2:
            pdf.setFillColor(_ri7_HexColor('#EFF6FC'))
            pdf.rect(26,yy-4,789,16,stroke=0,fill=1)
        protocolo=str(r.get(RI_BP_COLUNA_PROTOCOLO,'') or '').strip() or 'Não informado'
        for x,t,w in ((35,r.get('Matricula',''),165),(220,protocolo,202),
                       (440,r.get('Bairro',''),223),(685,f"{float(r['_pressao']):g} mca",119)):
            ri7_escrever(pdf,str(t),x,yy,8.5,RI7_TEXTO,largura=w)



def ri7_pagina_eventos_continuacao(pdf, conc, eventos, inicio, fim, pagina):
    """Todos os detalhes operacionais dos eventos, sem truncar o registro-fonte."""
    ri7_cabecalho(pdf,'Farol Operacional — Relatório de Concentrações',
                  inicio,fim,ri7_descrever_unidade(pd.DataFrame([conc])),
                  'Eventos relacionados')
    ri7_tarja(pdf,f"{conc.get('Concentração','')} — Eventos relacionados "
                  f"({pagina})",25,458,792,31,tamanho=12)
    for k,evento in enumerate(eventos):
        # Um bloco por evento com 90 pt de altura. Cinco blocos cabem na pagina.
        topo = 441-k*75
        ri7_retangulo(pdf,26,topo-70,790,68,'#F4F8FC','#BFD4EA',raio=5)
        ri7_escrever(pdf,f"Evento {k+1+5*(pagina-1)} · "
                         f"{evento.get('Protocolo','') or 'Protocolo não informado'}",
                     37,topo-16,9.2,RI7_AZUL,negrito=True,largura=750)
        servico=str(evento.get('Serviço','') or '')
        ativo=str(evento.get('Código do Ativo','') or '')
        ri7_escrever(pdf,f"Serviço: {servico}  |  Ativo: {ativo or 'Não informado'}",
                     37,topo-32,8.4,largura=754)
        ri7_escrever(pdf,f"Status: {evento.get('Status','')}  |  "
                         f"Início: {evento.get('Início','')}  |  "
                         f"Término: {evento.get('Término Real','') or 'Em andamento'}",
                     37,topo-47,8.1,largura=754)
        ri7_escrever(pdf,f"Áreas: {evento.get('Bairros coincidentes','') or 'Não informado'}",
                     37,topo-61,8.1,largura=754)


def ri7_pagina_descricao_continuacao(pdf, conc, linhas, inicio, fim, numero):
    ri7_cabecalho(pdf,'Farol Operacional — Relatório de Concentrações',
                  inicio,fim,ri7_descrever_unidade(pd.DataFrame([conc])),
                  'Descrição da ocorrência — continuação')
    ri7_tarja(pdf,f"{conc.get('Concentração','')} — Descrição da ocorrência "
                  f"({numero})",25,458,792,31,tamanho=12)
    for indice, entrada in enumerate(linhas):
        if entrada is None:
            continue
        linha, inicio_item = entrada
        y = 428 - indice * 16
        if inicio_item:
            ri7_escrever(pdf, '•', 36, y, 10, largura=12)
        ri7_escrever(pdf, linha, 50, y, 10, largura=744)


def pdf_relatorio(resumo, associados, territoriais, achados, inicio, fim, raio, n_os, n_bp,
                  concentracoes=None, grupos=None, raio_cluster=RAIO_CONCENTRACOES_M, contexto=None, pressao=None,
                  pontos_todos=None, indice_graficos=None, raio_operacional_loggers=RAIO_OPERACIONAL_EVIDENCIAS,
                  incluir_sanear=True):
    """Novo relatorio institucional, com paginas dinamicas e sem celulas nao quebraveis.

    Secoes: panorama; ficha por concentracao + excedentes; imagens reais de loggers;
    baixa pressao (mapa, graficos, tabela); anexo Sanear opcional.
    O numero de O.S. proximas de medicao e contado por O.S., nao por concentracao.
    """
    contexto = contexto or {}
    pressao = pressao if pressao is not None else pd.DataFrame()
    pontos_todos = pontos_todos if pontos_todos is not None else pd.DataFrame()
    concentracoes = concentracoes if concentracoes is not None else pd.DataFrame()
    grupos = grupos if grupos is not None else pd.DataFrame()
    # Filtros de data/unidade ja sao aplicados pela interface do modulo.
    if not pressao.empty:
        pressao = associar_pressao_as_os(pressao, pontos_todos)
        pressao = pressao[pressao['_pressao'].notna() & (pressao['_pressao'] >= 0)].copy()
    buffer = io.BytesIO()
    pdf = RI7CanvasNumerado(buffer, pagesize=landscape(A4), pageCompression=1)
    pdf.setTitle('PRISMA COI — Relatório Integrado: Farol e Baixa Pressão')
    pdf.setAuthor('COI — Centro de Operações Integradas')
    numero_de_paginas = 0
    indice_pressao_os = ri7_preparar_indice_pressao_os(pressao, pontos_todos)

    def terminar_pagina():
        nonlocal numero_de_paginas
        pdf.showPage()
        numero_de_paginas += 1

    ri7_pagina_panorama(pdf,concentracoes,grupos,pressao,pontos_todos,
                        inicio,fim,contexto,raio_cluster,raio)
    terminar_pagina()

    if not concentracoes.empty and resumo is not None and not resumo.empty:
        for _, conc in resumo.iterrows():
            codigo = str(conc['Concentração'])
            grupo = grupos[grupos['_grupo']==conc['_grupo']].copy()
            grupo = ri7_rotular_status_os_grupo(grupo, indice_pressao_os)
            eventos = eventos_da_concentracao(conc,contexto,inicio,fim)
            vinculadas,nao = classificar_os_eventos(grupo,eventos)
            acompanhamento,_,_ = ficha_acompanhamento(
                conc,grupo,contexto,incluir_movimentacoes=False)
            mapa = mapa_ficha_farol(conc,grupo,pressao.iloc[:0],contexto,raio_cluster)
            imagem = ri7_imagem_cartografica(mapa,850,675,grupo)
            registros, descricao_continua = ri7_pagina_concentracao(
                pdf,conc,grupo,eventos,vinculadas,nao,acompanhamento,
                contexto,pressao,inicio,fim,raio_cluster,imagem)
            terminar_pagina()
            restantes = registros[9:]
            for i in range(0,len(restantes),19):
                ri7_pagina_os_continuacao(pdf,conc,restantes[i:i+19],
                                         inicio,fim,i//19+1)
                terminar_pagina()
            # Os detalhes dos eventos nao cabem no card executivo. Preservar
            # protocolo, ativo, servico, status, janela e bairros em paginas proprias.
            for i in range(0,len(eventos),5):
                ri7_pagina_eventos_continuacao(pdf,conc,eventos[i:i+5],inicio,fim,i//5+1)
                terminar_pagina()
            if descricao_continua:
                # O cartão usa a menor fonte permitida e sinaliza a continuação.
                # A página seguinte contém a descrição INTEGRAL, preservando
                # quebras de linha e itens, sem qualquer truncamento de conteúdo.
                descricao = str((acompanhamento or {}).get('descricao','') or '').strip()
                linhas_descricao = ri7_descricao_quebrar_linhas(
                    descricao, 758, pdf._ri7_regular, 10)
                for i in range(0, len(linhas_descricao), 20):
                    ri7_pagina_descricao_continuacao(pdf, conc,
                        linhas_descricao[i:i+20], inicio, fim, i//20+1)
                    terminar_pagina()
            for _ in ri7_pagina_evidencia(
                pdf,conc,ri7_imagens_loggers(conc,contexto,indice_graficos,
                                              raio_operacional_loggers),inicio,fim):
                terminar_pagina()

    if not pressao.empty:
        bairros=ri7_pagina_bp_mapa(pdf,pressao,inicio,fim)
        terminar_pagina()
        ri7_pagina_bp_dashboard(pdf,pressao,bairros,inicio,fim)
        terminar_pagina()
        municipios=', '.join(sorted(set(pressao['_municipio'].astype(str)) - {''})) or 'Não informados'
        total_tabelas=max(1,math.ceil(len(bairros)/22))
        for pagina in range(1,total_tabelas+1):
            ri7_pagina_bp_tabela(pdf,bairros,inicio,fim,municipios,pagina,total_tabelas)
            terminar_pagina()
        if incluir_sanear:
            base=pressao.copy()
            base['Latitude']=pd.to_numeric(base['_lat'], errors='coerce')
            base['Longitude']=pd.to_numeric(base['_lon'], errors='coerce')
            try:
                arquivo=ri_bp_encontrar_kml_sanear()
                if arquivo.is_file():
                    geojson=ri_bp_carregar_poligonos_sanear(str(arquivo),arquivo.stat().st_mtime_ns)
                    if geojson.get('features'):
                        dentro,indices=ri_bp_selecionar_sanear(base,geojson)
                        # Sem medições intersectando Sanear: não cria página nem
                        # qualquer menção à seção no relatório institucional.
                        if not dentro.empty:
                            resto = ri7_pagina_sanear(pdf,base,geojson,indices,dentro,inicio,fim,municipios)
                            terminar_pagina()
                            if resto is not None and not resto.empty:
                                for i in range(0,len(resto),19):
                                    ri7_pagina_sanear_continuacao(pdf,resto.iloc[i:i+19],
                                                                   inicio,fim,municipios,i//19+1)
                                    terminar_pagina()
            except (ET.ParseError,OSError,ValueError,TypeError,KeyError,IndexError) as erro:
                # Mantem PDF valido mesmo se a camada de terceiro for invalida.
                ri7_cabecalho(pdf,'Monitoramento de Baixa Pressão — COI',inicio,fim,
                              municipios,'Anexo Sanear', tipo_campo='Município(s)')
                ri7_tarja(pdf,'Anexo Sanear — indisponível',25,458,792,31)
                ri7_paragrafo(pdf,'Não foi possível processar a camada Sanear: ' + str(erro),
                              36,405,760,9)
                terminar_pagina()
    pdf.save()
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
def consultar_diagnostico_atualizado(inicio, fim, unidade, municipio_filtro):
    """Consulta fontes ativas, sem snapshots de cache, para tela ou exportação."""
    # O cache de 120s protege consultas comuns, mas uma geração explícita
    # não pode apresentar dados anteriores às últimas edições do Farol/Pressões.
    ler_base.clear()
    carregar_contexto_farol.clear()
    if fonte_supabase():
        listar_mesclas_farol.clear()

    os_df = preparar_os(ler_base(ID_FAROL, "PONTOS"))
    contexto = carregar_contexto_farol()
    bp_todas = preparar_pressao(ler_base(ID_PRESSAO, "baixa_pressao"))
    contagem_origem = {
        "total": len(bp_todas),
        "data_valida": int(bp_todas["_data"].notna().sum()),
        "pressao_valida": int(bp_todas["_pressao"].notna().sum()),
    }
    os_df = os_df[os_df["_data"].dt.date.between(inicio, fim)].copy()
    bp = bp_todas[bp_todas["_data"].dt.date.between(inicio, fim)
                  & bp_todas["_pressao"].notna()].copy()
    contagem_origem["periodo"] = len(bp)
    os_df = os_df[os_df["Cidade"].map(unidade_da_cidade_farol) == unidade].copy()
    bp = bp[bp["Municipio"].map(unidade_da_cidade_farol) == unidade].copy()
    if municipio_filtro.strip():
        mun = normalizar(municipio_filtro)
        os_df = os_df[os_df["_municipio"] == mun]
        bp = bp[bp["_municipio"] == mun]
    contagem_origem["unidade_municipio"] = len(bp)
    bp = associar_pressao_as_os(bp, os_df)
    resumo_bp = resumo_pressao_por_bairro(bp)
    concs, grupos = concentracoes_farol(os_df, int(RAIO_CONCENTRACOES_M),
                                         int(MINIMO_OS_CONCENTRACAO))
    if not concs.empty:
        # O Módulo 3 mantém suas mesclagens no Supabase; o Módulo 7 deve
        # aplicá-las ANTES dos cruzamentos e da emissão de PDFs.
        concs, grupos = aplicar_mesclagens_do_modulo_3(concs, grupos)
        resumo, associados, territoriais = relacionar(
            concs, grupos, bp, int(RAIO_MEDICOES_M), inicio, fim
        )
        achados, pressao_sem_conc = diagnostico_geral(
            resumo, bp, associados, territoriais, inicio, fim
        )
    else:
        resumo, associados, territoriais = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        achados = ["Nenhuma concentração atende aos critérios selecionados."]
        pressao_sem_conc = bp.iloc[:0].copy()

    return {
        "versao_esquema": 13,
        "consultado_em": datetime.now(FUSO_LOGGERS).strftime("%d/%m/%Y %H:%M:%S"),
        "municipio_filtro": municipio_filtro,
        "resumo": resumo, "associados": associados, "territoriais": territoriais,
        "concentracoes": concs, "achados": achados,
        "pressao_sem_conc": pressao_sem_conc,
        "inicio": inicio, "fim": fim, "raio": int(RAIO_MEDICOES_M),
        "n_os": len(os_df), "n_bp": len(bp), "grupos": grupos,
        "raio_cluster": int(RAIO_CONCENTRACOES_M), "contexto": contexto,
        "pressao_mapa": bp, "unidade": unidade, "os_base": os_df,
        "resumo_bp": resumo_bp, "contagem_origem": contagem_origem,
    }


if executar:
    st.session_state.pop("relatorio_coi_integrado", None)
    st.session_state.pop("pdf_relatorio_integrado_farol", None)
    st.session_state.pop("pdf_relatorio_integrado_dia", None)
    if inicio > fim:
        st.error("A data inicial não pode ser posterior à data final.")
        st.stop()
    try:
        with st.spinner("Consultando as fontes atualizadas e cruzando O.S. com medições..."):
            unidade = unidade_sel.split(" ", 1)[0]
            st.session_state["relatorio_coi_integrado"] = consultar_diagnostico_atualizado(
                inicio, fim, unidade, municipio_sel
            )
    except gspread.exceptions.APIError as ex:
        codigo = getattr(getattr(ex, "response", None), "status_code", None)
        st.error("Quota temporária do Google Sheets excedida (429)." if codigo == 429
                 else f"Falha na consulta às fontes (HTTP {codigo or 'desconhecido'}).")
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
    or r.get("versao_esquema") != 13
    or not _chaves_necessarias.issubset(r)
):
    st.session_state.pop("relatorio_coi_integrado", None)
    r = None
    st.info("O diagnóstico anterior é de uma versão antiga. Clique em **Gerar diagnóstico integrado** para atualizar a análise.")
if r:
    st.caption(
        f"Dados consultados em {r.get('consultado_em', 'horário não disponível')} "
        "(horário de Teresina). A geração do PDF consulta novamente as fontes."
    )
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
        incluir_sanear_pdf = st.checkbox(
            "Incluir anexo de medições nas áreas Sanear",
            value=True, key="ri_pdf_incluir_sanear",
            help="Mantém o cruzamento cartográfico e a relação de medições Sanear do relatório anterior."
        )
        if st.button("📄 Gerar relatório institucional (PDF)", type="primary", key="gerar_pdf_ri_farol"):
            st.session_state.pop("pdf_relatorio_integrado_farol", None)
            st.session_state.pop("pdf_relatorio_integrado_dia", None)
            try:
                with st.spinner("Atualizando as fontes e gerando o PDF integrado..."):
                    # Reconsulta os dados neste clique, inclusive mesclagens,
                    # fichas e medições. Nunca exporta a cópia antiga da sessão.
                    r = consultar_diagnostico_atualizado(
                        r["inicio"], r["fim"], r["unidade"], r.get("municipio_filtro", "")
                    )
                    st.session_state["relatorio_coi_integrado"] = r
                    dia_graficos = _dia_evidencias()
                    indice_graficos = (
                        carregar_graficos_do_dia_somente_leitura(ID_FAROL, dia_graficos)
                        if r["inicio"] <= dia_graficos <= r["fim"] else {}
                    )
                    pdf = pdf_relatorio(
                        r["resumo"], r["associados"], r["territoriais"], r["achados"],
                        r["inicio"], r["fim"], r["raio"], r["n_os"], r["n_bp"],
                        r["concentracoes"], r["grupos"], r["raio_cluster"],
                        r["contexto"], r["pressao_mapa"], r["os_base"],
                        indice_graficos=indice_graficos,
                        raio_operacional_loggers=RAIO_OPERACIONAL_EVIDENCIAS,
                        incluir_sanear=incluir_sanear_pdf,
                    )
                    st.session_state["pdf_relatorio_integrado_farol"] = pdf
                    st.session_state["pdf_relatorio_integrado_dia"] = dia_graficos.isoformat()
                # Atualiza também os indicadores na tela para refletirem
                # exatamente o recorte recém-exportado, sem gerar outro PDF.
                st.rerun()
            except gspread.exceptions.APIError as erro_pdf:
                codigo = getattr(getattr(erro_pdf, "response", None), "status_code", None)
                st.error("Google Sheets indisponível por limite de consultas (429)." if codigo == 429
                         else f"Falha ao consultar as fontes para o PDF (HTTP {codigo or 'desconhecido'}).")
            except Exception as erro_pdf:
                st.error(f"Não foi possível preparar o PDF integrado: {erro_pdf}")
        if st.session_state.get("pdf_relatorio_integrado_farol"):
            st.download_button("⬇️ Baixar relatório institucional integrado (PDF)",
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
