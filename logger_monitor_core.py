"""Núcleo do Monitor de Variação de loggers (sem dependências do Streamlit).

Duas bases representam dois momentos de coleta. Compara somente leituras
válidas e cronologicamente posteriores do mesmo Galaxia ID, evitando marcar
ausência de dados ou timestamps repetidos como pressão estável.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from logger_regionais import dados_regional, normalizar


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
    s = s.str.replace(r"(?<=\d)\.(?=\d{3}(?:,|$))", "", regex=True)
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
    """XLSX, XLS, CSV; somente memória, sem armazenamento de bases."""
    if len(conteudo) > 40 * 1024 * 1024:
        raise ErroBaseLogger("Arquivo acima de 40 MB; divida a base antes de enviar.")
    extensao = Path(nome).suffix.lower()
    try:
        if extensao in {".xlsx", ".xls"}:
            return pd.read_excel(io.BytesIO(conteudo))
        if extensao in {".csv", ".txt"}:
            for codificacao in ("utf-8-sig", "latin1"):
                try:
                    texto = conteudo.decode(codificacao)
                    return pd.read_csv(io.StringIO(texto), sep=None, engine="python")
                except (UnicodeError, pd.errors.ParserError):
                    continue
            raise ErroBaseLogger("Não foi possível interpretar a codificação ou separador do CSV.")
    except (ValueError, KeyError, OSError, EOFError, zipfile.BadZipFile) as exc:
        raise ErroBaseLogger(f"Não foi possível ler {nome}: {exc}") from exc
    raise ErroBaseLogger("Formato não suportado. Utilize XLSX, XLS ou CSV.")


def classificar_ponto(endereco, nome):
    texto = normalizar(f"{nome} {endereco}")
    if "RESERVATORIO" in texto:
        return "Ativo"
    trecho = re.split(r"[/,]", str(endereco or ""))[-1].strip()
    return "Ativo" if re.search(r"[A-Za-z]{2,}\s*[-_]?\s*\d+", trecho) else "Residência"


def preparar_base(df, origem):
    """Padroniza uma exportação, mantendo uma última leitura por Galaxia ID."""
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
    # Mesmo logger duplicado: leitura com data maior prevalece.
    # Se não houver data válida, ela fica não comparável, nunca estável.
    return (
        saida.sort_values("MedicaoEm", na_position="first", kind="stable")
        .drop_duplicates("Galaxia ID", keep="last")
        .reset_index(drop=True)
    )


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
