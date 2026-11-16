"""Núcleo isolado do Farol V3 (laboratório): apenas reclamações do dia.

Nenhuma função grava em Supabase, planilhas ou arquivos no servidor.
O dia de abertura é parte da identidade e cada concentração é independente.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import math
import re
import unicodedata

import pandas as pd


COLUNAS_CONCENTRACAO = (
    "chave", "codigo", "municipio", "bairros", "latitude", "longitude",
    "qtd_os", "matriculas", "os_ids",
)
RAIO_PADRAO = 750
MINIMO_OS = 3


def normalizar(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor).strip().upper())
    return " ".join("".join(x for x in texto if not unicodedata.combining(x)).split())


def unidade(municipio: str) -> str:
    cidade = re.sub(r"[^A-Z0-9]", "", normalizar(municipio))
    if cidade == "TERESINA":
        return "THE"
    if cidade in ("TIMON", "TIMONMA", "TIMONMARANHAO"):
        return "TIM"
    return "API"


def data_valida(valor):
    """Recebe datas D/M/A, ISO, Excel e datetime. Nunca inventa uma data."""
    if valor is None or (not isinstance(valor, (tuple, dict, list)) and pd.isna(valor)):
        return pd.NaT
    if isinstance(valor, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(valor)
    texto = str(valor).strip()
    if not texto or texto.lower() in {"nan", "nat", "none", "-"}:
        return pd.NaT
    for formato in (
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    ):
        try:
            return pd.Timestamp(datetime.strptime(texto, formato))
        except ValueError:
            continue
    try:
        valor_n = float(texto.replace(",", "."))
        if 20000 <= valor_n <= 90000:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(valor_n, unit="D")
    except (ValueError, TypeError, OverflowError):
        pass
    return pd.NaT


def numero_coord(valor, *, latitude: bool):
    texto = str(valor or "").strip().replace(" ", "")
    if not texto or texto.casefold() in {"nan", "none", "null", "-"}:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".") if texto.rfind(",") > texto.rfind(".") else texto.replace(",", "")
    else:
        texto = texto.replace(",", ".")
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    maximo = 90 if latitude else 180
    return numero if math.isfinite(numero) and -maximo <= numero <= maximo else None


def distancia_m(lat1, lon1, lat2, lon2) -> float:
    a1, a2 = math.radians(float(lat1)), math.radians(float(lat2))
    delta_a = a2 - a1
    delta_o = math.radians(float(lon2) - float(lon1))
    h = math.sin(delta_a / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(delta_o / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def coluna(df, alternativas, *, obrigatoria=True):
    por_nome = {re.sub(r"[^A-Z0-9]", "", normalizar(nome)): nome for nome in df.columns}
    for nome in alternativas:
        chave = re.sub(r"[^A-Z0-9]", "", normalizar(nome))
        if chave in por_nome:
            return por_nome[chave]
    if obrigatoria:
        raise ValueError(f"Coluna obrigatória ausente: {alternativas[0]}")
    return None


def preparar_os_do_dia(base: pd.DataFrame, dia: date):
    """Filtra a data de emissão ANTES de geocodificar, agrupar e cruzar dados."""
    if base is None or base.empty:
        raise ValueError("O arquivo de reclamações está vazio.")
    nomes = {
        "os": ["Nº da O.S", "Nº O.S", "Numero OS", "Protocolo", "Cód. Protocolo Origem"],
        "municipio": ["Cidade", "Município", "Municipio"],
        "bairro": ["Bairro"],
        "lat": ["Latitude", "Lat"],
        "lon": ["Longitude", "Long", "Lon"],
        "data": ["Dt. Emissão", "Data Emissão", "Dt Emissao", "Data de Abertura", "Data"],
        "matricula": ["Matrícula", "Matricula"],
        "status": ["Status OS", "Situação", "Situacao"],
        "servico": ["Serviço Executado", "Servico Executado", "Serviço"],
        "endereco": ["Endereço", "Endereco", "Logradouro"],
    }
    col = {
        chave: coluna(base, nomes[chave], obrigatoria=chave in {"os", "municipio", "bairro", "lat", "lon", "data"})
        for chave in nomes
    }
    datas = base[col["data"]].map(data_valida)
    somente_dia = base.loc[datas.map(lambda x: pd.notna(x) and x.date() == dia)].copy()
    total_dia = len(somente_dia)
    dados = pd.DataFrame({
        "os": somente_dia[col["os"]].fillna("").astype(str).str.strip(),
        "municipio": somente_dia[col["municipio"]].fillna("").astype(str).str.strip(),
        "bairro": somente_dia[col["bairro"]].fillna("").astype(str).str.strip(),
        "lat": somente_dia[col["lat"]],
        "lon": somente_dia[col["lon"]],
        "data": datas.loc[somente_dia.index],
    }).reset_index(drop=True)
    for chave in ("matricula", "status", "servico", "endereco"):
        dados[chave] = (
            somente_dia[col[chave]].fillna("").astype(str).str.strip().to_numpy()
            if col[chave] else ""
        )
    dados["_municipio"] = dados["municipio"].map(normalizar)
    dados["_bairro"] = dados["bairro"].map(normalizar)
    dados = dados[dados["os"].ne("") & dados["_municipio"].ne("")].copy()
    antes = len(dados)
    dados = dados.drop_duplicates(subset=["_municipio", "os"], keep="last").reset_index(drop=True)
    dados["lat"] = dados["lat"].map(lambda x: numero_coord(x, latitude=True))
    dados["lon"] = dados["lon"].map(lambda x: numero_coord(x, latitude=False))
    dados["_chave"] = ""
    estatisticas = {
        "lidos": len(base),
        "do_dia": total_dia,
        "validos": len(dados),
        "duplicados": antes - len(dados),
        "sem_coordenadas": int((dados["lat"].isna() | dados["lon"].isna()).sum()),
        "ignorados_fora_da_data": len(base) - total_dia,
    }
    return dados, estatisticas


def concentracoes_do_dia(pontos: pd.DataFrame, dia: date, raio_m=RAIO_PADRAO, minimo_os=MINIMO_OS):
    """Grade espacial + união por proximidade, restrita a cada município."""
    resultado = pontos.copy()
    resultado["_chave"] = ""
    geograficos = resultado.dropna(subset=["lat", "lon"]).copy()
    if geograficos.empty:
        return pd.DataFrame(columns=COLUNAS_CONCENTRACAO), resultado
    if not 100 <= raio_m <= 5000 or not 2 <= minimo_os <= 100:
        raise ValueError("Parâmetros espaciais fora dos limites aceitos.")

    grupos = []
    for municipio, dados_mun in geograficos.groupby("_municipio", sort=True):
        indices = list(dados_mun.index)
        lat_media = float(dados_mun["lat"].mean())
        passo_lat = raio_m / 111320
        passo_lon = raio_m / (111320 * max(0.2, abs(math.cos(math.radians(lat_media)))))
        grade = {}
        for idx in indices:
            registro = geograficos.loc[idx]
            celula = (math.floor(registro["lat"] / passo_lat), math.floor(registro["lon"] / passo_lon))
            grade.setdefault(celula, []).append(idx)
        pais = {idx: idx for idx in indices}

        def raiz(i):
            while pais[i] != i:
                pais[i] = pais[pais[i]]
                i = pais[i]
            return i

        for (a, b), membros in grade.items():
            vizinhos = [k for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                        for k in grade.get((a + dx, b + dy), [])]
            for i in membros:
                for j in vizinhos:
                    if j <= i or raiz(i) == raiz(j):
                        continue
                    if distancia_m(geograficos.at[i, "lat"], geograficos.at[i, "lon"],
                                   geograficos.at[j, "lat"], geograficos.at[j, "lon"]) <= raio_m:
                        pais[raiz(j)] = raiz(i)
        componentes = {}
        for i in indices:
            componentes.setdefault(raiz(i), []).append(i)
        for membros in componentes.values():
            if len(membros) < minimo_os:
                continue
            bloco = geograficos.loc[membros]
            os_ids = sorted(set(bloco["os"].astype(str)))
            identidade = f"{dia.isoformat()}|{municipio}|{';'.join(os_ids)}"
            chave = hashlib.sha256(identidade.encode("utf-8")).hexdigest()[:24]
            grupos.append({
                "chave": chave, "municipio": municipio,
                "bairros": ", ".join(sorted(set(bloco["_bairro"]) - {""})),
                "latitude": float(bloco["lat"].mean()),
                "longitude": float(bloco["lon"].mean()),
                "qtd_os": len(bloco),
                "matriculas": int(bloco["matricula"].replace("", pd.NA).dropna().nunique()),
                "os_ids": os_ids,
                "_indices": membros,
            })
    grupos.sort(key=lambda g: (-g["qtd_os"], g["municipio"], g["chave"]))
    contador = {"THE": 0, "API": 0, "TIM": 0}
    for grupo in grupos:
        classe = unidade(grupo["municipio"])
        contador[classe] += 1
        grupo["codigo"] = f"{classe} C{contador[classe]:03d}"
        resultado.loc[grupo.pop("_indices"), "_chave"] = grupo["chave"]
    return pd.DataFrame(grupos, columns=COLUNAS_CONCENTRACAO), resultado


def eventos_pertinentes(base: pd.DataFrame, concentracao: dict, dia: date):
    """Relação indicativa por município, bairros e intervalo; não prova causalidade."""
    if base is None or base.empty:
        return []
    c_mun = coluna(base, ["Cidade", "Município"], obrigatoria=False)
    c_areas = coluna(base, ["Áreas Impactadas", "Bairros", "Area Impactada"], obrigatoria=False)
    c_inicio = coluna(base, ["Início", "Data de início", "Data"], obrigatoria=False)
    c_fim = coluna(base, ["Término Real", "Fim"], obrigatoria=False)
    c_status = coluna(base, ["Status"], obrigatoria=False)
    c_descricao = coluna(base, ["Descrição do Serviço", "Descricao do Servico", "Serviço"], obrigatoria=False)
    c_ativo = coluna(base, ["Código do Ativo", "Codigo do Ativo"], obrigatoria=False)
    if not (c_mun and c_areas and c_inicio):
        return []
    bairro_alvo = set(concentracao["bairros"].split(", "))
    inicio_dia = pd.Timestamp(dia)
    fim_dia = inicio_dia + pd.Timedelta(days=1)
    encontrados = []
    for registro in base.to_dict("records"):
        if normalizar(registro.get(c_mun)) != normalizar(concentracao["municipio"]):
            continue
        inicio = data_valida(registro.get(c_inicio))
        if pd.isna(inicio) or inicio >= fim_dia:
            continue
        fim = data_valida(registro.get(c_fim)) if c_fim else pd.NaT
        if pd.notna(fim) and fim < inicio_dia:
            continue
        status = normalizar(registro.get(c_status)) if c_status else ""
        if pd.isna(fim) and any(t in status for t in ("CONCLUID", "FINALIZ", "ENCERRAD")):
            continue
        areas = normalizar(registro.get(c_areas))
        municipal = any(s in areas for s in ("TODO O MUNICIPIO", "TODA A CIDADE", "ABRANGENCIA MUNICIPAL"))
        termos = set(s.strip() for s in re.split(r"[,;\n]+", areas) if s.strip())
        if not municipal and not bairro_alvo.intersection(termos):
            continue
        descricao = str(registro.get(c_descricao) or "").strip() if c_descricao else ""
        ativo = str(registro.get(c_ativo) or "").strip() if c_ativo else ""
        if ativo.casefold() in {"nan", "none", "não informado", "nao informado"}:
            ativo = ""
        encontrados.append({
            "descricao": descricao or "Evento sem descrição",
            "ativo": ativo, "inicio": str(registro.get(c_inicio) or ""),
            "status": str(registro.get(c_status) or "") if c_status else "",
            "criterio": "Município e janela" if municipal else "Bairro e janela",
        })
    return encontrados


def ativos_proximos(base: pd.DataFrame, concentracao: dict, raio_m=750):
    if base is None or base.empty:
        return []
    c_lat = coluna(base, ["LATITUDE", "Latitude"], obrigatoria=False)
    c_lon = coluna(base, ["LONGITUDE", "Longitude"], obrigatoria=False)
    c_cidade = coluna(base, ["MUNICIPIO", "Município", "Cidade"], obrigatoria=False)
    c_nome = coluna(base, ["IDENTIFICACAO_ATIVO", "ID_POCO", "ID_LOGGER", "NOME_POCO"], obrigatoria=False)
    if not c_lat or not c_lon:
        return []
    resultado = []
    for linha in base.to_dict("records"):
        if c_cidade and normalizar(linha.get(c_cidade)) != normalizar(concentracao["municipio"]):
            continue
        lat = numero_coord(linha.get(c_lat), latitude=True)
        lon = numero_coord(linha.get(c_lon), latitude=False)
        if lat is None or lon is None:
            continue
        distancia = distancia_m(concentracao["latitude"], concentracao["longitude"], lat, lon)
        if distancia <= raio_m:
            resultado.append({
                "ativo": str(linha.get(c_nome) or "Sem identificação") if c_nome else "Sem identificação",
                "distancia_m": round(distancia),
                "latitude": lat, "longitude": lon,
            })
    return sorted(resultado, key=lambda x: x["distancia_m"])


def pressao_proxima(base: pd.DataFrame, concentracao: dict, dia: date, raio_m=750):
    """Medições do próprio dia, carregadas somente na investigação."""
    if base is None or base.empty:
        return []
    c_data = coluna(base, ["Data", "Data Medição", "Data Medicao"], obrigatoria=False)
    c_cidade = coluna(base, ["Municipio", "Cidade"], obrigatoria=False)
    c_lat = coluna(base, ["Latitude"], obrigatoria=False)
    c_lon = coluna(base, ["Longitude"], obrigatoria=False)
    c_pressao = coluna(base, ["Pressao_MCA", "Pressão", "Pressao"], obrigatoria=False)
    if not all((c_data, c_lat, c_lon, c_pressao)):
        return []
    saida = []
    for linha in base.to_dict("records"):
        data = data_valida(linha.get(c_data))
        if pd.isna(data) or data.date() != dia:
            continue
        if c_cidade and normalizar(linha.get(c_cidade)) != normalizar(concentracao["municipio"]):
            continue
        lat = numero_coord(linha.get(c_lat), latitude=True)
        lon = numero_coord(linha.get(c_lon), latitude=False)
        try:
            valor = float(str(linha.get(c_pressao)).replace(",", "."))
        except (ValueError, TypeError):
            continue
        if lat is None or lon is None or not math.isfinite(valor) or valor < 0:
            continue
        distancia = distancia_m(concentracao["latitude"], concentracao["longitude"], lat, lon)
        if distancia <= raio_m:
            saida.append({"pressao_mca": valor, "distancia_m": round(distancia), "data": str(linha.get(c_data))})
    return sorted(saida, key=lambda x: x["distancia_m"])
