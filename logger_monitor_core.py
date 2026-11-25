"""Núcleo do Monitor de Variação de loggers (sem dependências do Streamlit).

Múltiplas exportações acumulam leituras por Galaxia ID e timestamp real;
o panorama compara as duas últimas leituras distintas, preservando todas
as leituras para o gráfico de evolução no decorrer do dia. O legado
comparar_bases() continua disponível para comparações pontuais.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from logger_regionais import dados_regional, normalizar
from logger_xlsx_fallback import ler_xlsx_xml


class ErroBaseLogger(ValueError):
    """Arquivo ou estrutura incompatível com a análise de pressão."""


def _coluna(df, *alternativas):
    indices = {normalizar(c): c for c in df.columns}
    for nome in alternativas:
        col = indices.get(normalizar(nome))
        if col is not None:
            return col
    return None


def _valores(df, coluna, padrao=""):
    if coluna is None:
        return pd.Series(padrao, index=df.index, dtype="object")
    return df[coluna].fillna(padrao).astype(str).str.strip()


def numeros(serie):
    """Conversão decimal PT-BR sem converter valor vazio em pressão zero."""
    s = serie.astype(str).str.strip().str.replace(r"\s+", "", regex=True)
    # Um ponto isolado é separador DECIMAL (1.234 = 1,234 mca).
    # Havendo ponto e vírgula juntos, o ponto separa milhares (1.234,56).
    com_ponto_e_virgula = s.str.contains(".", regex=False) & s.str.contains(",", regex=False)
    s = s.where(~com_ponto_e_virgula, s.str.replace(".", "", regex=False))
    s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)


def datas_medicao(serie):
    """Aceita datas locais brasileiras e serial Excel, quando houver."""
    resultado = pd.to_datetime(serie, dayfirst=True, errors="coerce")
    numerico = pd.to_numeric(serie, errors="coerce")
    serial_excel = numerico.between(20000, 90000)
    if serial_excel.any():
        resultado.loc[serial_excel] = pd.to_datetime(
            numerico.loc[serial_excel], unit="D", origin="1899-12-30",
            errors="coerce",
        )
    return resultado


def ler_arquivo(nome, conteudo):
    """Lê XLSX, XLS, CSV em memória. XLSX malformado usa XML de contingência."""
    if len(conteudo) > 40 * 1024 * 1024:
        raise ErroBaseLogger("Arquivo acima de 40 MB; divida a base antes de enviar.")
    extensao = Path(nome).suffix.lower()
    if extensao in {".xlsx", ".xls"}:
        try:
            return pd.read_excel(io.BytesIO(conteudo))
        except Exception as erro_excel:
            if extensao == ".xlsx":
                try:
                    return ler_xlsx_xml(conteudo)
                except (ValueError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as erro_xml:
                    raise ErroBaseLogger(
                        f"Não foi possível ler {nome} pelo leitor de Excel nem "
                        "pela recuperação de dados XLSX. "
                        f"Erro original: {erro_excel}; recuperação: {erro_xml}"
                    ) from erro_xml
            raise ErroBaseLogger(
                f"Não foi possível ler {nome}: {erro_excel}"
            ) from erro_excel
    if extensao in {".csv", ".txt"}:
        for codificacao in ("utf-8-sig", "latin1"):
            try:
                texto = conteudo.decode(codificacao)
                return pd.read_csv(io.StringIO(texto), sep=None, engine="python")
            except (UnicodeError, pd.errors.ParserError):
                continue
        raise ErroBaseLogger(
            f"Não foi possível interpretar a codificação ou o separador de {nome}."
        )
    raise ErroBaseLogger("Formato não suportado. Utilize XLSX, XLS ou CSV.")

def classificar_ponto(endereco, nome):
    texto = normalizar(f"{nome} {endereco}")
    if "RESERVATORIO" in texto:
        return "Ativo"
    trecho = re.split(r"[/,]", str(endereco or ""))[-1].strip()
    return "Ativo" if re.search(r"[A-Za-z]{2,}\s*[-_]?\s*\d+", trecho) else "Residência"


def preparar_medicoes(df, origem):
    """Padroniza todas as leituras da exportação, sem perder a série temporal."""
    if df.empty:
        raise ErroBaseLogger(f"{origem}: planilha vazia.")
    col_id = _coluna(df, "Galaxia ID", "Galax.ia ID", "ID Galaxia", "ID Logger")
    col_pressao = _coluna(
        df, "Valor da última medição", "Pressão", "Pressao", "Pressão (mca)",
        "Valor da medição", "Valor medido",
    )
    col_data = _coluna(
        df, "Data da última medição", "Última medição",
        "Data da medição", "Data e hora", "Data/hora", "Timestamp",
    )
    if col_id is None or col_pressao is None or col_data is None:
        faltantes = [
            titulo for titulo, col in (
                ("Galaxia ID", col_id), ("pressão", col_pressao),
                ("data da última medição", col_data),
            ) if col is None
        ]
        raise ErroBaseLogger(f"{origem}: coluna(s) ausente(s): {', '.join(faltantes)}.")

    saida = pd.DataFrame(index=df.index)
    ids = _valores(df, col_id)
    saida["Galaxia ID"] = ids.str.replace(r"\.0$", "", regex=True)
    saida["Pressao"] = numeros(df[col_pressao])
    saida["MedicaoEm"] = datas_medicao(df[col_data])
    col_cidade = _coluna(df, "Município", "Municipio", "Cidade", "Localidade")
    saida["Cidade"] = _valores(df, col_cidade, "-")
    for campo, nomes in {
        "Nome": ("Nome", "Nome do logger"),
        "Endereco": ("Endereço", "Endereco"),
        "Status": ("Status", "Status comunicação", "Status de comunicação"),
        "Estado": ("Estado", "Estado instalação"),
    }.items():
        saida[campo] = _valores(df, _coluna(df, *nomes), "-")
    nao_instalado = saida["Estado"].map(
        lambda x: "NAO INSTALADO" in normalizar(x)
    )
    instalado = saida["Estado"].map(
        lambda x: "INSTALADO" in normalizar(x)
    )
    saida.loc[instalado, "Estado"] = "Instalado"
    saida.loc[nao_instalado, "Estado"] = "Não instalado"
    saida["Tipo_Ponto"] = [
        classificar_ponto(e, n) for e, n in zip(saida["Endereco"], saida["Nome"])
    ]

    col_lat = _coluna(df, "Latitude", "Lat", "Coord Y", "Coordenada Y")
    col_lon = _coluna(df, "Longitude", "Lon", "Long", "Lng", "Coord X", "Coordenada X")
    saida["Latitude"] = numeros(df[col_lat]) if col_lat else np.nan
    saida["Longitude"] = numeros(df[col_lon]) if col_lon else np.nan
    saida.loc[~saida["Latitude"].between(-90, 90), "Latitude"] = np.nan
    saida.loc[~saida["Longitude"].between(-180, 180), "Longitude"] = np.nan

    regioes = [dados_regional(cidade) for cidade in saida["Cidade"]]
    saida["Regional"] = [r[0] for r in regioes]
    saida["Base"] = [r[1] for r in regioes]
    saida["Zona"] = [r[2] for r in regioes]
    # Quando a cidade não consta na tabela do Colab, preservar cadastro
    # explícito da exportação (se fornecido).
    for destino, alias in {
        "Regional": ("Regional",), "Base": ("Base",), "Zona": ("Zona",),
    }.items():
        original = _valores(df, _coluna(df, *alias))
        ausente = saida[destino].isin(["Não mapeada", "-", ""])
        preenchido = original.ne("")
        saida.loc[ausente & preenchido, destino] = original.loc[ausente & preenchido]

    saida["Arquivo_Origem"] = str(origem)
    saida = saida[saida["Galaxia ID"].ne("") & ~saida["Galaxia ID"].str.lower().isin(["nan", "none"])]
    if saida.empty:
        raise ErroBaseLogger(f"{origem}: nenhum Galaxia ID válido.")
    # Cada exportação pode conter vários horários do mesmo equipamento.
    # A deduplicação por (ID, instante) é feita na acumulação, não aqui.
    return saida.reset_index(drop=True)


def preparar_base(df, origem):
    """Legado de base única: preserva a última leitura de cada Galaxia ID."""
    return (
        preparar_medicoes(df, origem)
        .sort_values("MedicaoEm", na_position="first", kind="stable")
        .drop_duplicates("Galaxia ID", keep="last")
        .reset_index(drop=True)
    )


def mesclar_historico(anterior, novo):
    """Anexa bases por sessão e elimina somente leituras repetidas ID+horário.

    Exportações posteriores do mesmo horário atualizam o registro do ponto.
    Não usa horário de upload para fabricar avanço de pressão.
    """
    frames = [df for df in (anterior, novo) if df is not None and not df.empty]
    if not frames:
        raise ErroBaseLogger("Nenhuma medição de logger para analisar.")
    dados = pd.concat(frames, ignore_index=True)
    dados = dados.drop_duplicates(
        ["Galaxia ID", "MedicaoEm"], keep="last"
    )
    return (
        dados.sort_values(["Galaxia ID", "MedicaoEm"], kind="stable", na_position="first")
        .reset_index(drop=True)
    )


def carregar_historico(arquivos):
    """Importa quantas exportações forem enviadas, usando as datas reais."""
    if not arquivos:
        raise ErroBaseLogger("Selecione uma ou mais bases.")
    dados = [preparar_medicoes(ler_arquivo(nome, conteudo), nome)
             for nome, conteudo in arquivos]
    return mesclar_historico(None, pd.concat(dados, ignore_index=True))


def historico_logger(dados, galaxia_id):
    """Série histórica válida e ordenada para o gráfico de um equipamento."""
    recorte = dados.loc[
        dados["Galaxia ID"].astype(str).eq(str(galaxia_id))
    ].dropna(subset=["MedicaoEm", "Pressao"])
    return (
        recorte.sort_values("MedicaoEm", kind="stable")
        .drop_duplicates("MedicaoEm", keep="last")
        .reset_index(drop=True)
    )


def resumir_historico(dados):
    """Variação entre as últimas duas leituras de timestamp distinto por ID.

    A leitura mais recente sem pressão é reportada como não comparável,
    não é substituída silenciosamente por um valor antigo.
    """
    if dados is None or dados.empty:
        raise ErroBaseLogger("Nenhuma base foi importada.")
    # Nunca considerar timestamps NaT como medições comparáveis.
    validas_data = dados.dropna(subset=["MedicaoEm"]).sort_values(
        ["Galaxia ID", "MedicaoEm"], kind="stable"
    )
    atual = validas_data.groupby("Galaxia ID", sort=False).tail(1).copy()
    anteriores = validas_data.groupby("Galaxia ID", sort=False).tail(2)
    anterior = anteriores.loc[
        anteriores.groupby("Galaxia ID").cumcount(ascending=False).eq(1)
    ].copy()
    ids_sem_data = dados.loc[
        ~dados["Galaxia ID"].isin(atual["Galaxia ID"])
    ].drop_duplicates("Galaxia ID", keep="last")
    if not ids_sem_data.empty:
        atual = pd.concat([atual, ids_sem_data], ignore_index=True)

    resultado = comparar_bases(anterior, atual)
    sem_anterior = resultado["_merge"].eq("right_only")
    resultado.loc[sem_anterior, "Motivo"] = "Sem duas medições em horários distintos"
    datas_invalidas = sem_anterior & resultado["MedicaoEm_Atual"].isna()
    resultado.loc[datas_invalidas, "Motivo"] = "Sem horário de medição válido"

    quantidade = validas_data.groupby("Galaxia ID").size().rename("Qtd_Medicoes")
    arquivos = dados.groupby("Galaxia ID")["Arquivo_Origem"].nunique().rename("Qtd_Arquivos")
    resultado = resultado.merge(quantidade, on="Galaxia ID", how="left")
    resultado = resultado.merge(arquivos, on="Galaxia ID", how="left")
    resultado["Qtd_Medicoes"] = resultado["Qtd_Medicoes"].fillna(0).astype(int)
    resultado["Qtd_Arquivos"] = resultado["Qtd_Arquivos"].fillna(0).astype(int)
    return resultado


def consolidar_arquivos(arquivos):
    """Recebe sequência de (nome, bytes), preservando a última leitura por ID."""
    if not arquivos:
        raise ErroBaseLogger("Envie pelo menos uma base.")
    conjuntos = []
    for nome, conteudo in arquivos:
        conjuntos.append(preparar_base(ler_arquivo(nome, conteudo), nome))
    unificados = pd.concat(conjuntos, ignore_index=True)
    return (
        unificados.sort_values("MedicaoEm", na_position="first", kind="stable")
        .drop_duplicates("Galaxia ID", keep="last")
        .reset_index(drop=True)
    )


def comparar_bases(anterior, atual):
    """Comparação estrita e auditável entre os dois momentos de exportação."""
    colunas = [c for c in anterior.columns if c != "Galaxia ID"]
    antes = anterior.rename(columns={c: c + "_Anterior" for c in colunas})
    colunas_atual = [c for c in atual.columns if c != "Galaxia ID"]
    depois = atual.rename(columns={c: c + "_Atual" for c in colunas_atual})
    resultado = antes.merge(depois, on="Galaxia ID", how="outer",
                            indicator=True, validate="one_to_one")
    metadados = ["Cidade", "Regional", "Base", "Zona", "Tipo_Ponto",
                 "Status", "Estado", "Nome", "Endereco", "Latitude", "Longitude"]
    for campo in metadados:
        resultado[campo] = resultado.get(campo + "_Atual").combine_first(
            resultado.get(campo + "_Anterior")
        )

    p0 = resultado["Pressao_Anterior"]
    p1 = resultado["Pressao_Atual"]
    t0 = resultado["MedicaoEm_Anterior"]
    t1 = resultado["MedicaoEm_Atual"]
    validos = resultado["_merge"].eq("both") & p0.notna() & p1.notna() & t0.notna() & t1.notna() & t1.gt(t0)
    resultado["Delta_MCA"] = (p1 - p0).where(validos)
    resultado["Intervalo_Min"] = ((t1 - t0).dt.total_seconds() / 60).where(validos)
    resultado["Tendencia"] = "Não comparável"
    resultado["Motivo"] = ""
    resultado.loc[resultado["_merge"].eq("left_only"), "Motivo"] = "Ausente na base posterior"
    resultado.loc[resultado["_merge"].eq("right_only"), "Motivo"] = "Ausente na base anterior"
    ambas = resultado["_merge"].eq("both")
    resultado.loc[ambas & (p0.isna() | p1.isna()), "Motivo"] = "Pressão ausente ou inválida"
    datas_ausentes = ambas & p0.notna() & p1.notna() & (t0.isna() | t1.isna())
    resultado.loc[datas_ausentes, "Motivo"] = "Data da medição ausente ou inválida"
    regrediu = ambas & p0.notna() & p1.notna() & t0.notna() & t1.notna() & t1.lt(t0)
    repetiu = ambas & p0.notna() & p1.notna() & t0.notna() & t1.notna() & t1.eq(t0)
    resultado.loc[regrediu, "Motivo"] = "Leitura posterior com horário anterior"
    resultado.loc[repetiu, "Motivo"] = "Sem nova medição (mesmo horário)"
    resultado.loc[validos & resultado["Delta_MCA"].gt(0), "Tendencia"] = "Aumento (+)"
    resultado.loc[validos & resultado["Delta_MCA"].lt(0), "Tendencia"] = "Queda (-)"
    resultado.loc[validos & resultado["Delta_MCA"].eq(0), "Tendencia"] = "Estável"
    resultado["Abs_Delta"] = resultado["Delta_MCA"].abs()
    return (
        resultado.sort_values("Abs_Delta", ascending=False, na_position="last",
                              kind="stable")
        .reset_index(drop=True)
    )
