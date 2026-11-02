import hashlib
import re
from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st

from ..estado import (
    base_carregada,
    obter_base,
    limpar_resultado,
)

from ..exportacao import dataframe_para_excel

from ..zonas import (
    normalizar_texto,
    obter_zona,
)

from .componentes import (
    selecionar_modo_api_the,
)


# ============================================================
# CONFIGURAÇÕES DO FILTRO DE HORÁRIO
# ============================================================

# A O.S. pode ser aberta até 1 hora antes do início informado.
MARGEM_HORA_INICIAL = timedelta(hours=1)

# A O.S. pode ser aberta até 3 horas depois do fim informado.
MARGEM_HORA_FINAL = timedelta(hours=3)


# ============================================================
# LOCALIZAÇÃO DE COLUNAS
# ============================================================

def localizar_coluna(df, tipo):

    if df is None or df.empty:
        return None

    mapa = {
        "protocolo": "COD. PROTOCOLO ORIGEM",
        "matricula": "MATRICULA",
        "cidade": "CIDADE",
        "bairro": "BAIRRO",
        "data": "INÍCIO DO SLA",
    }

    alvo = mapa.get(tipo)

    if not alvo:
        return None

    colunas = list(df.columns)

    normalizadas = {
        normalizar_texto(coluna): coluna
        for coluna in colunas
    }

    alvo_normalizado = normalizar_texto(alvo)

    if alvo_normalizado in normalizadas:
        return normalizadas[alvo_normalizado]

    if tipo == "protocolo":

        for coluna in colunas:

            nome = normalizar_texto(coluna)

            if (
                "PROTOCOLO" in nome
                and "ORIGEM" in nome
            ):
                return coluna

    elif tipo == "matricula":

        for coluna in colunas:

            nome = normalizar_texto(coluna)

            if "MATRICULA" in nome:
                return coluna

    elif tipo == "cidade":

        for coluna in colunas:

            nome = normalizar_texto(coluna)

            if "CIDADE" in nome:
                return coluna

    elif tipo == "bairro":

        for coluna in colunas:

            nome = normalizar_texto(coluna)

            if "BAIRRO" in nome:
                return coluna

    elif tipo == "data":

        for coluna in colunas:

            nome = normalizar_texto(coluna)

            if (
                "INICIO" in nome
                and "SLA" in nome
            ):
                return coluna

    return None


# ============================================================
# VALORES ÚNICOS
# ============================================================

def obter_valores_unicos(df, coluna):

    if (
        df is None
        or coluna is None
        or coluna not in df.columns
    ):
        return []

    valores = []
    vistos = set()

    for valor in df[coluna].dropna():

        texto = str(valor).strip()

        if not texto:
            continue

        chave = normalizar_texto(texto)

        if chave in {
            "",
            "NAN",
            "NONE",
            "NULL",
        }:
            continue

        if chave not in vistos:

            vistos.add(chave)
            valores.append(texto)

    valores.sort(
        key=normalizar_texto
    )

    return valores


# ============================================================
# PROTOCOLO
# ============================================================

def parse_protocolo(proto):

    if pd.isna(proto):
        return "", "", None

    texto = str(proto).strip()

    resultado = re.search(
        r"(\d+)\s*/\s*(\d{4})",
        texto,
    )

    if not resultado:
        return "", "", None

    numero = resultado.group(1)
    ano = resultado.group(2)

    try:
        numero_int = int(numero)
    except Exception:
        numero_int = None

    return numero, ano, numero_int


# ============================================================
# HORA
# ============================================================

def converter_hora(valor):

    texto = str(valor).strip()

    if not re.fullmatch(
        r"\d{1,2}:\d{2}",
        texto,
    ):
        raise ValueError(
            f"Horário inválido: '{valor}'. Use HH:MM."
        )

    hora, minuto = texto.split(":")

    hora = int(hora)
    minuto = int(minuto)

    if (
        hora < 0
        or hora > 23
        or minuto < 0
        or minuto > 59
    ):
        raise ValueError(
            f"Horário inválido: '{valor}'. Use HH:MM."
        )

    return time(
        hour=hora,
        minute=minuto,
    )


# ============================================================
# DATA ROBUSTA
# ============================================================

def converter_datas_robusto(serie):

    resultado = pd.Series(
        pd.NaT,
        index=serie.index,
        dtype="datetime64[ns]",
    )

    for indice, valor in serie.items():

        if pd.isna(valor):
            continue

        if isinstance(
            valor,
            pd.Timestamp,
        ):
            resultado.loc[indice] = valor
            continue

        if isinstance(
            valor,
            datetime,
        ):
            resultado.loc[indice] = pd.Timestamp(
                valor
            )
            continue

        if isinstance(
            valor,
            (int, float),
        ) and not isinstance(valor, bool):

            try:

                numero = float(valor)

                if 1 <= numero <= 100000:

                    resultado.loc[indice] = (
                        pd.Timestamp(
                            "1899-12-30"
                        )
                        + pd.to_timedelta(
                            numero,
                            unit="D",
                        )
                    )

                    continue

            except Exception:
                pass

        texto = str(valor).strip()

        if not texto:
            continue

        formatos = [
            "%d/%m/%Y",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ]

        convertido = None

        for formato in formatos:

            try:

                convertido = pd.to_datetime(
                    texto,
                    format=formato,
                )

                break

            except Exception:
                continue

        if convertido is None:

            try:

                convertido = pd.to_datetime(
                    texto,
                    dayfirst=True,
                    errors="coerce",
                )

            except Exception:

                convertido = pd.NaT

        resultado.loc[indice] = convertido

    return resultado


# ============================================================
# CONVERSÃO DE MINUTOS
# ============================================================

def hora_para_minutos(valor_hora):

    if valor_hora is None:
        return None

    return (
        valor_hora.hour * 60
        + valor_hora.minute
    )


# ============================================================
# FILTRO DE HORÁRIO COM MARGEM OPERACIONAL
# ============================================================

def aplicar_filtro_horario(
    resultado,
    datas,
    hora_inicial=None,
    hora_final=None,
):

    if (
        hora_inicial is None
        and hora_final is None
    ):
        return resultado

    if datas.empty:
        return resultado

    horas = (
        datas.dt.hour * 60
        + datas.dt.minute
    )

    horas = horas.astype("Float64")

    # --------------------------------------------------------
    # Somente horário inicial
    # --------------------------------------------------------

    if (
        hora_inicial is not None
        and hora_final is None
    ):

        inicio_minutos = hora_para_minutos(
            hora_inicial
        )

        inicio_efetivo = (
            inicio_minutos - 60
        ) % 1440

        mascara = (
            horas.notna()
            & (
                horas >= inicio_efetivo
            )
        )

        return resultado.loc[
            mascara
        ]

    # --------------------------------------------------------
    # Somente horário final
    # --------------------------------------------------------

    if (
        hora_inicial is None
        and hora_final is not None
    ):

        fim_minutos = hora_para_minutos(
            hora_final
        )

        fim_efetivo = (
            fim_minutos + 180
        )

        if fim_efetivo >= 1440:

            mascara = horas.notna()

        else:

            mascara = (
                horas.notna()
                & (
                    horas <= fim_efetivo
                )
            )

        return resultado.loc[
            mascara
        ]

    # --------------------------------------------------------
    # Horário inicial + final
    # --------------------------------------------------------

    inicio_minutos = hora_para_minutos(
        hora_inicial
    )

    fim_minutos = hora_para_minutos(
        hora_final
    )

    duracao_original = (
        fim_minutos - inicio_minutos
    ) % 1440

    duracao_efetiva = (
        duracao_original + 240
    )

    inicio_efetivo = (
        inicio_minutos - 60
    ) % 1440

    fim_efetivo = (
        fim_minutos + 180
    ) % 1440

    if duracao_efetiva >= 1440:

        mascara = horas.notna()

        return resultado.loc[
            mascara
        ]

    if inicio_efetivo <= fim_efetivo:

        mascara = (
            horas.notna()
            & (
                horas >= inicio_efetivo
            )
            & (
                horas <= fim_efetivo
            )
        )

    else:

        mascara = (
            horas.notna()
            & (
                (horas >= inicio_efetivo)
                | (
                    horas <= fim_efetivo
                )
            )
        )

    return resultado.loc[
        mascara
    ]


# ============================================================
# FILTROS
# ============================================================

def aplicar_filtros(
    df,
    coluna_cidade=None,
    cidades=None,
    coluna_bairro=None,
    bairros=None,
    coluna_data=None,
    anos=None,
    meses=None,
    dias=None,
    hora_inicial=None,
    hora_final=None,
):

    if df is None or df.empty:

        return pd.DataFrame(
            columns=(
                df.columns
                if df is not None
                else []
            )
        )

    resultado = df.copy()

    # ========================================================
    # CIDADE
    # ========================================================

    if coluna_cidade and cidades:

        cidades_normalizadas = {
            normalizar_texto(valor)
            for valor in cidades
        }

        resultado = resultado[
            resultado[coluna_cidade]
            .map(normalizar_texto)
            .isin(cidades_normalizadas)
        ]

    # ========================================================
    # BAIRRO
    # ========================================================

    if coluna_bairro and bairros:

        bairros_normalizados = {
            normalizar_texto(valor)
            for valor in bairros
        }

        resultado = resultado[
            resultado[coluna_bairro]
            .map(normalizar_texto)
            .isin(bairros_normalizados)
        ]

    # ========================================================
    # DATA / HORÁRIO
    # ========================================================

    if coluna_data:

        precisa_data = (
            bool(anos)
            or bool(meses)
            or bool(dias)
            or hora_inicial is not None
            or hora_final is not None
        )

        if precisa_data:

            datas = converter_datas_robusto(
                resultado[coluna_data]
            )

            if anos:

                mascara = datas.dt.year.isin(
                    anos
                )

                resultado = resultado.loc[
                    mascara
                ]

                datas = datas.loc[
                    resultado.index
                ]

            if meses:

                mascara = datas.dt.month.isin(
                    meses
                )

                resultado = resultado.loc[
                    mascara
                ]

                datas = datas.loc[
                    resultado.index
                ]

            if dias:

                mascara = datas.dt.day.isin(
                    dias
                )

                resultado = resultado.loc[
                    mascara
                ]

                datas = datas.loc[
                    resultado.index
                ]

            if (
                hora_inicial is not None
                or hora_final is not None
            ):

                resultado = aplicar_filtro_horario(
                    resultado,
                    datas,
                    hora_inicial,
                    hora_final,
                )

    return resultado.reset_index(
        drop=True
    )


# ============================================================
# FUNÇÕES DO ARQUIVO DE EVENTOS
# ============================================================

def localizar_coluna_eventos(df, nome):

    if df is None or df.empty:
        return None

    normalizadas = {
        normalizar_texto(coluna): coluna
        for coluna in df.columns
    }

    alvo = normalizar_texto(nome)

    return normalizadas.get(alvo)


def converter_quantidade_os(valor):

    if pd.isna(valor):
        return 0

    try:

        texto = str(valor).strip()

        texto = texto.replace(
            ".",
            "",
        ).replace(
            ",",
            ".",
        )

        return int(float(texto))

    except Exception:

        return 0


def extrair_dias_evento(valor):

    if pd.isna(valor):
        return []

    texto = str(valor).strip()

    if not texto:
        return []

    # --------------------------------------------------------
    # Exemplo: 26
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{1,2}",
        texto,
    ):

        return [
            int(texto)
        ]

    # --------------------------------------------------------
    # Exemplo: 26 a 27
    # --------------------------------------------------------

    resultado = re.fullmatch(
        r"(\d{1,2})\s*a\s*(\d{1,2})",
        texto,
        flags=re.IGNORECASE,
    )

    if resultado:

        inicio = int(
            resultado.group(1)
        )

        fim = int(
            resultado.group(2)
        )

        if inicio <= fim:

            return list(
                range(
                    inicio,
                    fim + 1,
                )
            )

        return [
            inicio,
            fim,
        ]

    # --------------------------------------------------------
    # Exemplo: 30/09 a 01/10
    # --------------------------------------------------------

    datas = re.findall(
        r"\d{1,2}/\d{1,2}",
        texto,
    )

    if datas:

        dias = []

        for data in datas:

            partes = data.split("/")

            try:

                dias.append(
                    int(partes[0])
                )

            except Exception:
                continue

        return dias

    return []


def obter_hora_evento(valor):

    if pd.isna(valor):
        return None

    texto = str(valor).strip()

    if not texto:
        return None

    texto = texto.replace(
        " ",
        "",
    )

    resultado = re.fullmatch(
        r"(\d{1,2}):(\d{2})",
        texto,
    )

    if not resultado:
        return None

    try:

        hora = int(
            resultado.group(1)
        )

        minuto = int(
            resultado.group(2)
        )

        return time(
            hour=hora,
            minute=minuto,
        )

    except Exception:

        return None


def aplicar_filtros_arquivo_eventos(
    df_base,
    df_eventos,
    coluna_cidade,
    coluna_bairro,
    coluna_data,
):

    if (
        df_base is None
        or df_base.empty
        or df_eventos is None
        or df_eventos.empty
    ):

        return (
            pd.DataFrame(
                columns=(
                    df_base.columns
                    if df_base is not None
                    else []
                )
            ),
            pd.DataFrame(),
        )

    coluna_evento_cidade = (
        localizar_coluna_eventos(
            df_eventos,
            "Cidade",
        )
    )

    coluna_evento_bairro = (
        localizar_coluna_eventos(
            df_eventos,
            "Bairro",
        )
    )

    coluna_evento_ano = (
        localizar_coluna_eventos(
            df_eventos,
            "Ano",
        )
    )

    coluna_evento_mes = (
        localizar_coluna_eventos(
            df_eventos,
            "Mês",
        )
        or localizar_coluna_eventos(
            df_eventos,
            "Mes",
        )
    )

    coluna_evento_dia = (
        localizar_coluna_eventos(
            df_eventos,
            "Dia",
        )
    )

    coluna_evento_hora_inicial = (
        localizar_coluna_eventos(
            df_eventos,
            "Hora Inicial",
        )
    )

    coluna_evento_hora_final = (
        localizar_coluna_eventos(
            df_eventos,
            "Hora Final",
        )
    )

    coluna_evento_quantidade = (
        localizar_coluna_eventos(
            df_eventos,
            "Quant. de O.S",
        )
    )

    colunas_obrigatorias = {
        "Cidade": coluna_evento_cidade,
        "Bairro": coluna_evento_bairro,
        "Ano": coluna_evento_ano,
        "Mês": coluna_evento_mes,
        "Dia": coluna_evento_dia,
        "Hora Inicial": coluna_evento_hora_inicial,
        "Hora Final": coluna_evento_hora_final,
        "Quant. de O.S": coluna_evento_quantidade,
    }

    faltantes = [
        nome
        for nome, coluna in colunas_obrigatorias.items()
        if coluna is None
    ]

    if faltantes:

        raise ValueError(
            "O arquivo de Eventos não possui as colunas "
            "necessárias: "
            + ", ".join(faltantes)
        )

    resultados = []
    comparacao = []

    for _, evento in df_eventos.iterrows():

        cidade = evento[
            coluna_evento_cidade
        ]

        bairro = evento[
            coluna_evento_bairro
        ]

        ano = evento[
            coluna_evento_ano
        ]

        mes = evento[
            coluna_evento_mes
        ]

        dia = evento[
            coluna_evento_dia
        ]

        hora_inicial = obter_hora_evento(
            evento[
                coluna_evento_hora_inicial
            ]
        )

        hora_final = obter_hora_evento(
            evento[
                coluna_evento_hora_final
            ]
        )

        quantidade_indicada = (
            converter_quantidade_os(
                evento[
                    coluna_evento_quantidade
                ]
            )
        )

        try:
            ano = int(float(ano))
        except Exception:
            ano = None

        try:
            mes = int(float(mes))
        except Exception:
            mes = None

        dias = extrair_dias_evento(
            dia
        )

        if not dias:
            dias = None

        filtro = aplicar_filtros(
            df=df_base,

            coluna_cidade=coluna_cidade,
            cidades=(
                [cidade]
                if cidade is not None
                else []
            ),

            coluna_bairro=coluna_bairro,
            bairros=(
                [bairro]
                if bairro is not None
                and str(bairro).strip()
                and normalizar_texto(bairro)
                != "TODA A CIDADE"
                else []
            ),

            coluna_data=coluna_data,

            anos=(
                [ano]
                if ano is not None
                else []
            ),

            meses=(
                [mes]
                if mes is not None
                else []
            ),

            dias=dias,

            hora_inicial=hora_inicial,
            hora_final=hora_final,
        )

        quantidade_encontrada = len(
            filtro
        )

        if not filtro.empty:

            resultados.append(
                filtro
            )

        diferenca = (
            quantidade_encontrada
            - quantidade_indicada
        )

        comparacao.append(
            {
                "Cidade": cidade,
                "Bairro": bairro,
                "Ano": ano,
                "Mês": mes,
                "Dia": dia,
                "Hora Inicial": (
                    evento[
                        coluna_evento_hora_inicial
                    ]
                ),
                "Hora Final": (
                    evento[
                        coluna_evento_hora_final
                    ]
                ),
                "O.S indicada": (
                    quantidade_indicada
                ),
                "O.S encontrada": (
                    quantidade_encontrada
                ),
                "Diferença": diferenca,
            }
        )

    if resultados:

        resultado_final = pd.concat(
            resultados,
            ignore_index=True,
        )

        coluna_matricula = localizar_coluna(
            resultado_final,
            "matricula",
        )

        coluna_protocolo = localizar_coluna(
            resultado_final,
            "protocolo",
        )

        if (
            coluna_matricula
            and coluna_protocolo
        ):

            resultado_final = (
                resultado_final.drop_duplicates(
                    subset=[
                        coluna_matricula,
                        coluna_protocolo,
                    ]
                )
            )

        resultado_final = (
            resultado_final.reset_index(
                drop=True
            )
        )

    else:

        resultado_final = pd.DataFrame(
            columns=df_base.columns
        )

    comparacao_df = pd.DataFrame(
        comparacao
    )

    return (
        resultado_final,
        comparacao_df,
    )


# ============================================================
# TEXTO COMPARATIVO
# ============================================================

def gerar_texto_comparacao_eventos(
    comparacao_df,
    quantidade_resultado,
):

    if (
        comparacao_df is None
        or comparacao_df.empty
    ):

        return (
            "Não foi possível realizar a comparação "
            "com o arquivo de Eventos."
        )

    quantidade_indicada_total = int(
        comparacao_df[
            "O.S indicada"
        ].sum()
    )

    quantidade_encontrada_total = (
        int(quantidade_resultado)
    )

    divergencias = comparacao_df[
        comparacao_df["Diferença"] != 0
    ]

    if divergencias.empty:

        return (
            f"✅ A quantidade de O.S está de acordo "
            f"com o arquivo de Eventos: "
            f"**{quantidade_encontrada_total} O.S.** "
            f"encontradas após o filtro, "
            f"igual às **{quantidade_indicada_total} O.S.** "
            f"indicadas no arquivo."
        )

    diferenca_total = (
        quantidade_encontrada_total
        - quantidade_indicada_total
    )

    texto = (
        f"⚠️ Foi identificada divergência na quantidade "
        f"de O.S. após o filtro. "
        f"O arquivo de Eventos indica "
        f"**{quantidade_indicada_total} O.S.**, "
        f"enquanto o filtro encontrou "
        f"**{quantidade_encontrada_total} O.S.** "
        f"("
        f"{'+' if diferenca_total > 0 else ''}"
        f"{diferenca_total} de diferença)."
    )

    texto += (
        "\n\n**Diferenças identificadas:**"
    )

    for _, linha in divergencias.iterrows():

        diferenca = int(
            linha["Diferença"]
        )

        sinal = (
            "+"
            if diferenca > 0
            else ""
        )

        texto += (
            f"\n- **{linha['Cidade']} / "
            f"{linha['Bairro']}** — "
            f"Dia {linha['Dia']}, "
            f"{linha['Hora Inicial']} até "
            f"{linha['Hora Final']}: "
            f"arquivo = **{int(linha['O.S indicada'])}**, "
            f"encontradas = **{int(linha['O.S encontrada'])}**, "
            f"diferença = **{sinal}{diferenca}**."
        )

    return texto


# ============================================================
# GERAÇÃO DO LOTE
# ============================================================

def gerar_lote_cancelamento(
    df_filtrado,
    modo,
    observacao="",
):

    if (
        df_filtrado is None
        or df_filtrado.empty
    ):

        return (
            pd.DataFrame(),
            pd.DataFrame(),
        )

    coluna_protocolo = localizar_coluna(
        df_filtrado,
        "protocolo",
    )

    coluna_matricula = localizar_coluna(
        df_filtrado,
        "matricula",
    )

    coluna_cidade = localizar_coluna(
        df_filtrado,
        "cidade",
    )

    if coluna_protocolo is None:

        raise ValueError(
            "A coluna 'COD. PROTOCOLO ORIGEM' "
            "não foi localizada."
        )

    if coluna_matricula is None:

        raise ValueError(
            "A coluna 'MATRICULA' "
            "não foi localizada."
        )

    if (
        modo == "API"
        and coluna_cidade is None
    ):

        raise ValueError(
            "A coluna 'CIDADE' "
            "não foi localizada."
        )

    registros = []
    logs = []

    observacao_lote = (
        str(observacao).strip()
        if observacao is not None
        else ""
    )

    for _, linha in df_filtrado.iterrows():

        matricula = linha.get(
            coluna_matricula,
            "",
        )

        protocolo_original = linha.get(
            coluna_protocolo,
            "",
        )

        cidade = (
            linha.get(
                coluna_cidade,
                "",
            )
            if coluna_cidade
            else ""
        )

        numero, ano, numero_int = (
            parse_protocolo(
                protocolo_original
            )
        )

        if numero_int is None:

            logs.append(
                {
                    "Tipo": modo,
                    "Matrícula": matricula,
                    "Protocolo": protocolo_original,
                    "Cidade": cidade,
                    "Motivo": (
                        "Protocolo inválido "
                        "ou não localizado."
                    ),
                }
            )

            continue

        if modo == "API":

            zona = obter_zona(cidade)

            if zona is None:

                logs.append(
                    {
                        "Tipo": modo,
                        "Matrícula": matricula,
                        "Protocolo": protocolo_original,
                        "Cidade": cidade,
                        "Motivo": (
                            "Cidade sem zona definida."
                        ),
                    }
                )

                continue

        else:

            zona = 1

        registros.append(
            {
                "Matricula": matricula,
                "Zona Ligacao": zona,
                "Numero Do Pedido": numero_int,
                "Ano Do Pedido": ano,
                "Tipo Encerramento": 6,
                "Observações": observacao_lote,
            }
        )

    lote = pd.DataFrame(
        registros,
        columns=[
            "Matricula",
            "Zona Ligacao",
            "Numero Do Pedido",
            "Ano Do Pedido",
            "Tipo Encerramento",
            "Observações",
        ],
    )

    log = pd.DataFrame(
        logs,
        columns=[
            "Tipo",
            "Matrícula",
            "Protocolo",
            "Cidade",
            "Motivo",
        ],
    )

    return lote, log


# ============================================================
# FILTROS VISUAIS DO ARQUIVO DE EVENTOS
# ============================================================

def obter_filtros_visuais_eventos(df_eventos):

    filtros = {
        "cidades": [],
        "bairros": [],
        "anos": [],
        "meses": [],
        "dias": [],
        "hora_inicial": "",
        "hora_final": "",
    }

    if df_eventos is None or df_eventos.empty:
        return filtros

    coluna_cidade = localizar_coluna_eventos(
        df_eventos,
        "Cidade",
    )

    coluna_bairro = localizar_coluna_eventos(
        df_eventos,
        "Bairro",
    )

    coluna_ano = localizar_coluna_eventos(
        df_eventos,
        "Ano",
    )

    coluna_mes = (
        localizar_coluna_eventos(
            df_eventos,
            "Mês",
        )
        or localizar_coluna_eventos(
            df_eventos,
            "Mes",
        )
    )

    coluna_dia = localizar_coluna_eventos(
        df_eventos,
        "Dia",
    )

    coluna_hora_inicial = localizar_coluna_eventos(
        df_eventos,
        "Hora Inicial",
    )

    coluna_hora_final = localizar_coluna_eventos(
        df_eventos,
        "Hora Final",
    )

    if coluna_cidade:

        filtros["cidades"] = (
            obter_valores_unicos(
                df_eventos,
                coluna_cidade,
            )
        )

    if coluna_bairro:

        bairros = obter_valores_unicos(
            df_eventos,
            coluna_bairro,
        )

        filtros["bairros"] = [
            bairro
            for bairro in bairros
            if normalizar_texto(bairro)
            != "TODA A CIDADE"
        ]

    if coluna_ano:

        anos = []

        for valor in df_eventos[
            coluna_ano
        ].dropna():

            try:

                ano = int(
                    float(valor)
                )

                if ano not in anos:
                    anos.append(ano)

            except Exception:
                continue

        filtros["anos"] = sorted(
            anos
        )

    if coluna_mes:

        meses = []

        for valor in df_eventos[
            coluna_mes
        ].dropna():

            try:

                mes = int(
                    float(valor)
                )

                if (
                    1 <= mes <= 12
                    and mes not in meses
                ):
                    meses.append(mes)

            except Exception:
                continue

        filtros["meses"] = sorted(
            meses
        )

    if coluna_dia:

        dias = set()

        for valor in df_eventos[
            coluna_dia
        ].dropna():

            for dia in extrair_dias_evento(
                valor
            ):

                if 1 <= dia <= 31:
                    dias.add(dia)

        filtros["dias"] = sorted(
            dias
        )

    # --------------------------------------------------------
    # Horários
    # --------------------------------------------------------

    pares_horarios = set()

    if (
        coluna_hora_inicial
        and coluna_hora_final
    ):

        for _, linha in df_eventos.iterrows():

            inicial = obter_hora_evento(
                linha[
                    coluna_hora_inicial
                ]
            )

            final = obter_hora_evento(
                linha[
                    coluna_hora_final
                ]
            )

            if (
                inicial is None
                and final is None
            ):
                continue

            pares_horarios.add(
                (
                    inicial.strftime(
                        "%H:%M"
                    )
                    if inicial is not None
                    else "",
                    final.strftime(
                        "%H:%M"
                    )
                    if final is not None
                    else "",
                )
            )

    if len(pares_horarios) == 1:

        hora_inicial, hora_final = (
            next(
                iter(pares_horarios)
            )
        )

        filtros[
            "hora_inicial"
        ] = hora_inicial

        filtros[
            "hora_final"
        ] = hora_final

    return filtros


# ============================================================
# IDENTIFICADOR DO UPLOAD
# ============================================================

def obter_identificador_upload(
    arquivo_eventos,
    modo,
):

    if arquivo_eventos is None:
        return None

    try:

        conteudo = (
            arquivo_eventos.getvalue()
        )

        assinatura = hashlib.md5(
            conteudo,
            usedforsecurity=False,
        ).hexdigest()

    except Exception:

        assinatura = ""

    return (
        f"{modo}|"
        f"{arquivo_eventos.name}|"
        f"{getattr(arquivo_eventos, 'size', 0)}|"
        f"{assinatura}"
    )


# ============================================================
# LIMPEZA DOS FILTROS
# ============================================================

def limpar_filtros_tela():
    """Restaura todos os filtros antes de os widgets serem desenhados.

    Esta função é chamada por on_click: callbacks do Streamlit executam
    ANTES da renderização seguinte. Alterar apenas o argumento default=
    de um widget já registrado não limpa seu valor em session_state.
    """

    # Multiseleções (Cidade, Bairro, Ano, Mês e Dia).
    for chave in (
        "filtragem_cidades",
        "filtragem_bairros",
        "filtragem_anos",
        "filtragem_meses",
        "filtragem_dias",
    ):
        st.session_state[chave] = []

    # Horários de abertura e observação do lote.
    for chave in (
        "filtragem_hora_inicial",
        "filtragem_hora_final",
        "filtragem_observacao_lote",
    ):
        st.session_state[chave] = ""

    # st.file_uploader não permite atribuir None diretamente ao seu estado.
    # Trocar a chave do componente cria um uploader vazio na próxima renderização,
    # sem apagar as bases carregadas no hub do Gerador de Lotes.
    versao_atual = int(st.session_state.get("filtragem_eventos_upload_versao", 0))
    st.session_state["filtragem_eventos_upload_versao"] = versao_atual + 1

    # A versão anterior usava a chave fixa. Removê-la impede que um upload
    # antigo reapareça durante a migração para o novo componente versionado.
    st.session_state.pop("upload_arquivo_eventos", None)
    st.session_state.pop(f"upload_arquivo_eventos_v{versao_atual}", None)

    # Desativa a lógica antiga de reset, que alterava apenas os defaults.
    st.session_state["filtragem_resetar"] = False

    # Remove cruzamentos, prévias, lotes e logs do filtro anterior.
    for chave in (
        "arquivo_eventos_processado",
        "df_eventos",
        "df_comparacao_eventos",
        "df_resultado",
        "df_resultado_lote",
        "df_log",
        "nome_arquivo_resultado",
    ):
        st.session_state.pop(chave, None)

    limpar_resultado()


# ============================================================
# RENDERIZAÇÃO
# ============================================================

def render_filtragem():

    st.title(
        "🔎 Filtragem / Cancelamento"
    )

    st.caption(
        "Filtre a base carregada no Hub e gere "
        "o lote de cancelamento."
    )

    if st.button(
        "⬅️ Voltar ao Hub do Gerador de Lotes",
        key="btn_voltar_hub_filtragem",
    ):

        st.session_state.ferramenta_atual = None

        limpar_resultado()

        st.rerun()

    st.divider()

    # ========================================================
    # SELEÇÃO API / THE
    # ========================================================

    modo, df_base = selecionar_modo_api_the(
        key="filtragem_modo_api_the",
        titulo="Base de operação",
        limpar_resultado_callback=limpar_resultado,
    )

    if modo is None or df_base is None:
        return

    # ========================================================
    # CONTROLE DE RESET DOS FILTROS
    # ========================================================

    resetar_filtros = st.session_state.get(
        "filtragem_resetar",
        False,
    )

    # ========================================================
    # UPLOAD DO ARQUIVO DE EVENTOS
    # ========================================================

    with st.sidebar:

        st.markdown(
            "### 📂 Arquivo de Eventos"
        )

        st.caption(
            "Envie o arquivo gerado pela Ferramenta de Eventos "
            "para aplicar automaticamente os filtros."
        )

        arquivo_eventos = st.file_uploader(
            "Enviar arquivo de Eventos",
            type=["xlsx"],
            key=f"upload_arquivo_eventos_v{st.session_state.get('filtragem_eventos_upload_versao', 0)}",
        )

    # ========================================================
    # LOCALIZAÇÃO DAS COLUNAS
    # ========================================================

    coluna_cidade = localizar_coluna(
        df_base,
        "cidade",
    )

    coluna_bairro = localizar_coluna(
        df_base,
        "bairro",
    )

    coluna_data = localizar_coluna(
        df_base,
        "data",
    )

    coluna_matricula = localizar_coluna(
        df_base,
        "matricula",
    )

    coluna_protocolo = localizar_coluna(
        df_base,
        "protocolo",
    )

    # ========================================================
    # PROCESSAMENTO DO ARQUIVO DE EVENTOS
    # ========================================================

    arquivo_eventos_ativo = (
        arquivo_eventos is not None
    )

    if arquivo_eventos_ativo:

        identificador_upload = (
            obter_identificador_upload(
                arquivo_eventos,
                modo,
            )
        )

        if (
            st.session_state.get(
                "arquivo_eventos_processado"
            )
            != identificador_upload
        ):

            try:

                df_eventos = pd.read_excel(
                    arquivo_eventos
                )

                (
                    df_resultado_eventos,
                    comparacao_eventos,
                ) = aplicar_filtros_arquivo_eventos(
                    df_base=df_base,
                    df_eventos=df_eventos,
                    coluna_cidade=coluna_cidade,
                    coluna_bairro=coluna_bairro,
                    coluna_data=coluna_data,
                )

                st.session_state.df_eventos = (
                    df_eventos
                )

                st.session_state.df_resultado = (
                    df_resultado_eventos
                )

                st.session_state.df_comparacao_eventos = (
                    comparacao_eventos
                )

                st.session_state.arquivo_eventos_processado = (
                    identificador_upload
                )

                # ------------------------------------------------
                # Preenche visualmente os filtros.
                # ------------------------------------------------

                filtros_eventos = (
                    obter_filtros_visuais_eventos(
                        df_eventos
                    )
                )

                st.session_state[
                    "filtragem_cidades"
                ] = filtros_eventos[
                    "cidades"
                ]

                st.session_state[
                    "filtragem_bairros"
                ] = filtros_eventos[
                    "bairros"
                ]

                st.session_state[
                    "filtragem_anos"
                ] = filtros_eventos[
                    "anos"
                ]

                st.session_state[
                    "filtragem_meses"
                ] = filtros_eventos[
                    "meses"
                ]

                st.session_state[
                    "filtragem_dias"
                ] = filtros_eventos[
                    "dias"
                ]

                st.session_state[
                    "filtragem_hora_inicial"
                ] = filtros_eventos[
                    "hora_inicial"
                ]

                st.session_state[
                    "filtragem_hora_final"
                ] = filtros_eventos[
                    "hora_final"
                ]

                st.session_state.df_resultado_lote = (
                    None
                )

                st.session_state.df_log = (
                    None
                )

                st.session_state.nome_arquivo_resultado = (
                    None
                )

            except Exception as erro:

                st.session_state.pop(
                    "df_eventos",
                    None,
                )

                st.session_state.pop(
                    "df_resultado",
                    None,
                )

                st.error(
                    "Erro ao processar o arquivo de Eventos: "
                    f"{erro}"
                )

    else:

        # Se não existe arquivo, não mantemos o estado
        # do processamento anterior.
        st.session_state.pop(
            "df_eventos",
            None,
        )

        st.session_state.pop(
            "df_comparacao_eventos",
            None,
        )

        st.session_state.pop(
            "arquivo_eventos_processado",
            None,
        )

    # ========================================================
    # INFORMAÇÕES
    # ========================================================

    col1, col2, col3, col4 = st.columns(4)

    with col1:

        st.metric(
            "Registros",
            f"{len(df_base):,}".replace(
                ",",
                ".",
            ),
        )

    with col2:

        st.metric(
            "Colunas",
            len(df_base.columns),
        )

    with col3:

        st.metric(
            "Matrícula",
            "OK"
            if coluna_matricula
            else "Não encontrada",
        )

    with col4:

        st.metric(
            "Protocolo",
            "OK"
            if coluna_protocolo
            else "Não encontrado",
        )

    st.divider()

    # ========================================================
    # FILTROS
    # ========================================================

    st.subheader(
        "🎯 Filtros"
    )

    col1, col2 = st.columns(2)

    with col1:

        cidades_disponiveis = (
            obter_valores_unicos(
                df_base,
                coluna_cidade,
            )
            if coluna_cidade
            else []
        )

        if coluna_cidade:

            if resetar_filtros:
                valor_cidades = []

            else:
                valor_cidades = st.session_state.get(
                    "filtragem_cidades",
                    [],
                )

            valor_cidades = [
                valor
                for valor in valor_cidades
                if valor in cidades_disponiveis
            ]

            cidades = st.multiselect(
                "Cidade",
                cidades_disponiveis,
                default=valor_cidades,
                key="filtragem_cidades",
            )

        else:

            cidades = []

            st.info(
                "Coluna CIDADE não encontrada."
            )

    with col2:

        bairros_disponiveis = (
            obter_valores_unicos(
                df_base,
                coluna_bairro,
            )
            if coluna_bairro
            else []
        )

        if coluna_bairro:

            if resetar_filtros:
                valor_bairros = []

            else:
                valor_bairros = st.session_state.get(
                    "filtragem_bairros",
                    [],
                )

            valor_bairros = [
                valor
                for valor in valor_bairros
                if valor in bairros_disponiveis
            ]

            bairros = st.multiselect(
                "Bairro",
                bairros_disponiveis,
                default=valor_bairros,
                key="filtragem_bairros",
            )

        else:

            bairros = []

            st.info(
                "Coluna BAIRRO não encontrada."
            )

    if arquivo_eventos_ativo:

        st.info(
            "📂 Os filtros acima foram preenchidos com os valores "
            "encontrados no arquivo de Eventos. A aplicação do arquivo "
            "continua respeitando cada linha individualmente, incluindo "
            "Cidade, Bairro, data e janela de horário."
        )

        if (
            not st.session_state.get(
                "filtragem_hora_inicial"
            )
            and not st.session_state.get(
                "filtragem_hora_final"
            )
        ):

            st.caption(
                "ℹ️ O arquivo possui mais de uma janela de horário. "
                "Por isso os campos de horário permanecem vazios; "
                "as janelas de cada evento serão aplicadas "
                "individualmente."
            )

    # ========================================================
    # DATA
    # ========================================================

    anos = []
    meses = []
    dias = []

    if coluna_data:

        datas = converter_datas_robusto(
            df_base[coluna_data]
        )

        anos_disponiveis = sorted(
            datas.dt.year.dropna()
            .astype(int)
            .unique()
            .tolist()
        )

        meses_disponiveis = sorted(
            datas.dt.month.dropna()
            .astype(int)
            .unique()
            .tolist()
        )

        dias_disponiveis = sorted(
            datas.dt.day.dropna()
            .astype(int)
            .unique()
            .tolist()
        )

        col3, col4, col5 = st.columns(3)

        with col3:

            if resetar_filtros:
                valor_anos = []

            else:
                valor_anos = st.session_state.get(
                    "filtragem_anos",
                    [],
                )

            valor_anos = [
                valor
                for valor in valor_anos
                if valor in anos_disponiveis
            ]

            anos = st.multiselect(
                "Ano",
                anos_disponiveis,
                default=valor_anos,
                key="filtragem_anos",
            )

        with col4:

            if resetar_filtros:
                valor_meses = []

            else:
                valor_meses = st.session_state.get(
                    "filtragem_meses",
                    [],
                )

            valor_meses = [
                valor
                for valor in valor_meses
                if valor in meses_disponiveis
            ]

            meses = st.multiselect(
                "Mês",
                meses_disponiveis,
                default=valor_meses,
                key="filtragem_meses",
            )

        with col5:

            if resetar_filtros:
                valor_dias = []

            else:
                valor_dias = st.session_state.get(
                    "filtragem_dias",
                    [],
                )

            valor_dias = [
                valor
                for valor in valor_dias
                if valor in dias_disponiveis
            ]

            dias = st.multiselect(
                "Dia",
                dias_disponiveis,
                default=valor_dias,
                key="filtragem_dias",
            )

    else:

        st.error(
            "A coluna 'INÍCIO DO SLA' "
            "não foi encontrada na base."
        )

    # ========================================================
    # HORÁRIO
    # ========================================================

    st.markdown(
        "#### 🕐 Horário de abertura da O.S."
    )

    st.caption(
        "A referência do horário de abertura é exclusivamente "
        "**INÍCIO DO SLA**. O filtro considera automaticamente "
        "**1 hora antes do início** e **3 horas após o fim**."
    )

    col6, col7 = st.columns(2)

    with col6:

        if resetar_filtros:

            valor_hora_inicial = ""

        else:

            valor_hora_inicial = (
                st.session_state.get(
                    "filtragem_hora_inicial",
                    "",
                )
            )

        hora_inicial_texto = st.text_input(
            "Hora Inicial",
            value=valor_hora_inicial,
            placeholder="HH:MM",
            key="filtragem_hora_inicial",
        )

    with col7:

        if resetar_filtros:

            valor_hora_final = ""

        else:

            valor_hora_final = (
                st.session_state.get(
                    "filtragem_hora_final",
                    "",
                )
            )

        hora_final_texto = st.text_input(
            "Hora Final",
            value=valor_hora_final,
            placeholder="HH:MM",
            key="filtragem_hora_final",
        )

    hora_inicial = None
    hora_final = None
    erro_horario = False

    if hora_inicial_texto.strip():

        try:

            hora_inicial = converter_hora(
                hora_inicial_texto
            )

        except ValueError as erro:

            st.error(
                str(erro)
            )

            erro_horario = True

    if hora_final_texto.strip():

        try:

            hora_final = converter_hora(
                hora_final_texto
            )

        except ValueError as erro:

            st.error(
                str(erro)
            )

            erro_horario = True

    # ========================================================
    # MOSTRA A JANELA EFETIVA
    # ========================================================

    if (
        hora_inicial is not None
        and hora_final is not None
    ):

        inicio_minutos = hora_para_minutos(
            hora_inicial
        )

        fim_minutos = hora_para_minutos(
            hora_final
        )

        inicio_efetivo = (
            inicio_minutos - 60
        ) % 1440

        fim_efetivo = (
            fim_minutos + 180
        ) % 1440

        st.info(
            f"⏱️ Janela efetiva do filtro: "
            f"**{inicio_efetivo // 60:02d}:"
            f"{inicio_efetivo % 60:02d}** "
            f"até "
            f"**{fim_efetivo // 60:02d}:"
            f"{fim_efetivo % 60:02d}** "
            f"(margem de -1h / +3h)."
        )

    elif hora_inicial is not None:

        inicio_minutos = hora_para_minutos(
            hora_inicial
        )

        inicio_efetivo = (
            inicio_minutos - 60
        ) % 1440

        st.info(
            f"⏱️ O.S. abertas a partir de "
            f"**{inicio_efetivo // 60:02d}:"
            f"{inicio_efetivo % 60:02d}** "
            f"(1h antes do horário inicial)."
        )

    elif hora_final is not None:

        fim_minutos = hora_para_minutos(
            hora_final
        )

        fim_efetivo = (
            fim_minutos + 180
        ) % 1440

        st.info(
            f"⏱️ O.S. abertas até "
            f"**{fim_efetivo // 60:02d}:"
            f"{fim_efetivo % 60:02d}** "
            f"(3h após o horário final)."
        )

    # ========================================================
    # OBSERVAÇÃO DO LOTE
    # ========================================================

    st.markdown(
        "#### 📝 Observação do lote"
    )

    st.caption(
        "A observação informada será adicionada ao campo "
        "**Observações** de todas as O.S. incluídas no lote."
    )

    if resetar_filtros:

        valor_observacao = ""

    else:

        valor_observacao = st.session_state.get(
            "filtragem_observacao_lote",
            "",
        )

    observacao_lote = st.text_input(
        "Observação",
        value=valor_observacao,
        placeholder=(
            "Digite a observação que será gravada no lote..."
        ),
        key="filtragem_observacao_lote",
    )

    # ========================================================
    # COMPARAÇÃO COM ARQUIVO DE EVENTOS
    # ========================================================

    comparacao_eventos = (
        st.session_state.get(
            "df_comparacao_eventos"
        )
    )

    if (
        arquivo_eventos_ativo
        and comparacao_eventos is not None
    ):

        st.divider()

        st.subheader(
            "📊 Comparação com o Arquivo de Eventos"
        )

        df_resultado_comparacao = (
            st.session_state.get(
                "df_resultado"
            )
        )

        if df_resultado_comparacao is None:

            df_resultado_comparacao = (
                pd.DataFrame()
            )

        texto_comparacao = (
            gerar_texto_comparacao_eventos(
                comparacao_eventos,
                len(
                    df_resultado_comparacao
                ),
            )
        )

        st.info(
            texto_comparacao
        )

    # ========================================================
    # BOTÕES
    # ========================================================

    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:

        aplicar = st.button(
            "🔎 Aplicar Filtros",
            type="primary",
            use_container_width=True,
            key="filtragem_aplicar",
            disabled=erro_horario,
        )

    with col_btn2:

        st.button(
            "🧹 Limpar Filtros",
            use_container_width=True,
            key="filtragem_limpar",
            on_click=limpar_filtros_tela,
        )

    # O callback executa antes da reconstrução da tela; o Streamlit faz
    # o rerun automaticamente e todos os controles exibem os valores vazios.

    # ========================================================
    # APLICAÇÃO DOS FILTROS
    # ========================================================

    if aplicar:

        try:

            # ------------------------------------------------
            # COM ARQUIVO DE EVENTOS
            # ------------------------------------------------

            if (
                arquivo_eventos_ativo
                and st.session_state.get(
                    "df_eventos"
                ) is not None
            ):

                df_eventos = (
                    st.session_state.get(
                        "df_eventos"
                    )
                )

                (
                    df_resultado_eventos,
                    comparacao_eventos_nova,
                ) = aplicar_filtros_arquivo_eventos(
                    df_base=df_base,
                    df_eventos=df_eventos,
                    coluna_cidade=coluna_cidade,
                    coluna_bairro=coluna_bairro,
                    coluna_data=coluna_data,
                )

                st.session_state.df_resultado = (
                    df_resultado_eventos
                )

                st.session_state.df_comparacao_eventos = (
                    comparacao_eventos_nova
                )

            # ------------------------------------------------
            # SEM ARQUIVO DE EVENTOS
            # ------------------------------------------------

            else:

                df_filtrado = aplicar_filtros(
                    df=df_base,

                    coluna_cidade=coluna_cidade,
                    cidades=cidades,

                    coluna_bairro=coluna_bairro,
                    bairros=bairros,

                    coluna_data=coluna_data,
                    anos=anos,
                    meses=meses,
                    dias=dias,

                    hora_inicial=hora_inicial,
                    hora_final=hora_final,
                )

                st.session_state.df_resultado = (
                    df_filtrado
                )

                st.session_state.df_comparacao_eventos = (
                    None
                )

            # ------------------------------------------------
            # Novo filtro invalida lote anterior
            # ------------------------------------------------

            st.session_state.df_resultado_lote = (
                None
            )

            st.session_state.df_log = (
                None
            )

            st.session_state.nome_arquivo_resultado = (
                None
            )

            st.rerun()

        except Exception as erro:

            st.error(
                f"Erro ao aplicar os filtros: {erro}"
            )

    # ========================================================
    # FINALIZA RESET
    # ========================================================

    if resetar_filtros:

        st.session_state[
            "filtragem_resetar"
        ] = False

    # ========================================================
    # RESULTADO
    # ========================================================

    df_resultado = st.session_state.get(
        "df_resultado"
    )

    if df_resultado is None:
        return

    st.divider()

    st.subheader(
        "📋 Resultado da Filtragem"
    )

    col1, col2 = st.columns(2)

    with col1:

        st.metric(
            "Registros encontrados",
            f"{len(df_resultado):,}".replace(
                ",",
                ".",
            ),
        )

    with col2:

        st.metric(
            "Colunas preservadas",
            len(df_resultado.columns),
        )

    if df_resultado.empty:

        st.warning(
            "Nenhum registro corresponde aos "
            "filtros selecionados."
        )

        return

    st.dataframe(
        df_resultado,
        use_container_width=True,
        height=420,
    )

    arquivo_previa = dataframe_para_excel(
        df_resultado,
        nome_aba="Filtragem",
    )

    if arquivo_previa:

        st.download_button(
            "⬇️ Baixar Prévia Filtrada",
            data=arquivo_previa,
            file_name=(
                f"Previa_Filtragem_{modo}.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
            key="download_previa_filtragem",
        )

    # ========================================================
    # GERAÇÃO DO LOTE
    # ========================================================

    st.divider()

    st.subheader(
        "📦 Geração do Lote de Cancelamento"
    )

    if st.button(
        "📦 Gerar Lote de Cancelamento",
        type="primary",
        use_container_width=True,
        key="filtragem_gerar_lote",
    ):

        try:

            lote, log = gerar_lote_cancelamento(
                df_resultado,
                modo,
                observacao=observacao_lote,
            )

            st.session_state.df_resultado_lote = (
                lote
            )

            st.session_state.df_log = (
                log
            )

            timestamp = datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )

            st.session_state.nome_arquivo_resultado = (
                f"Lote_Cancelamento_{modo}_{timestamp}.xlsx"
            )

            st.rerun()

        except Exception as erro:

            st.error(
                f"Erro ao gerar o lote: {erro}"
            )

    # ========================================================
    # LOTE
    # ========================================================

    lote = st.session_state.get(
        "df_resultado_lote"
    )

    log = st.session_state.get(
        "df_log"
    )

    if lote is None:
        return

    st.divider()

    st.subheader(
        "✅ Lote Gerado"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "Registros no lote",
            f"{len(lote):,}".replace(
                ",",
                ".",
            ),
        )

    with col2:

        st.metric(
            "Registros no LOG",
            f"{len(log) if log is not None else 0:,}".replace(
                ",",
                ".",
            ),
        )

    with col3:

        st.metric(
            "Modo",
            modo,
        )

    if not lote.empty:

        st.dataframe(
            lote,
            use_container_width=True,
            height=300,
        )

        arquivo_lote = dataframe_para_excel(
            lote,
            nome_aba="Lote",
        )

        if arquivo_lote:

            st.download_button(
                "⬇️ Baixar Lote de Cancelamento",
                data=arquivo_lote,
                file_name=st.session_state.get(
                    "nome_arquivo_resultado",
                    f"Lote_Cancelamento_{modo}.xlsx",
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                type="primary",
                use_container_width=True,
                key="download_lote_cancelamento",
            )

    else:

        st.warning(
            "Nenhum registro válido foi gerado para o lote."
        )

    # ========================================================
    # LOG
    # ========================================================

    st.subheader(
        "📝 LOG"
    )

    if log is None or log.empty:

        st.success(
            "Nenhuma inconsistência foi encontrada "
            "durante a geração."
        )

    else:

        st.warning(
            f"{len(log)} registro(s) não foram "
            "incluídos no lote."
        )

        st.dataframe(
            log,
            use_container_width=True,
            height=300,
        )

        arquivo_log = dataframe_para_excel(
            log,
            nome_aba="LOG",
        )

        if arquivo_log:

            timestamp = datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )

            st.download_button(
                "⬇️ Baixar LOG",
                data=arquivo_log,
                file_name=(
                    f"LOG_Cancelamento_"
                    f"{modo}_{timestamp}.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
                key="download_log_cancelamento",
            )
