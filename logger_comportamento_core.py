"""Análise de Comportamento de logger baseada no algoritmo original do Colab.

Mantém janela de comparação horária ±30 min, exigência 5 dias e 25
medições, quantis 5%-95%, regra de proximidade e avaliação dos últimos
90 min. O diagnóstico refere-se à última leitura DO ARQUIVO, não à
situação atual da rede.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np
import pandas as pd

from logger_monitor_core import ErroBaseLogger, datas_medicao, numeros


JANELA_HISTORICA_MIN = 30
JANELA_MOVIMENTO_MIN = 90
MUDANCA_MINIMA = 3.0


def normalizar_coluna(valor):
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def coluna_id(df):
    for col in df.columns:
        if normalizar_coluna(col) in {"galaxiaid", "idgalaxia", "idlogger"}:
            return col
    return None


def ids_disponiveis(df):
    campo = coluna_id(df)
    if campo is None:
        return []
    return sorted({
        str(valor).strip().removesuffix(".0")
        for valor in df[campo].dropna() if str(valor).strip()
    })


def extrair_historico(df, galaxia_id=None):
    """Extrai apenas um logger e uma coluna real de pressão, sem inventar pontos."""
    if df.empty:
        raise ErroBaseLogger("O arquivo do histórico está vazio.")
    base = df.copy()
    campo_id = coluna_id(base)
    if campo_id is not None:
        opcoes = ids_disponiveis(base)
        if galaxia_id is None:
            if len(opcoes) > 1:
                raise ErroBaseLogger(
                    "O arquivo contém vários Galaxia ID. Selecione apenas um logger."
                )
            galaxia_id = opcoes[0] if opcoes else None
        if galaxia_id is not None:
            mascara = base[campo_id].astype(str).str.strip().str.replace(
                r"\.0$", "", regex=True
            ) == str(galaxia_id)
            base = base.loc[mascara].copy()
    if base.empty:
        raise ErroBaseLogger("Não há medições para o Galaxia ID selecionado.")

    nomes = {normalizar_coluna(c): c for c in base.columns}
    col_data = next((
        c for c in base.columns
        if any(k in normalizar_coluna(c) for k in (
            "timestamp", "datetime", "datahora", "datehour",
        ))
    ), None)
    col_hora = next((
        c for c in base.columns
        if normalizar_coluna(c) in ("hora", "hour", "time", "horario")
    ), None)
    if col_data is None:
        col_data = next((
            c for c in base.columns if normalizar_coluna(c) in (
                "data", "date", "dt", "datamedicao",
                "datadaultimamedicao", "ultimamedicao",
            )
        ), None)
    if col_data is None:
        raise ErroBaseLogger("Não foi identificada uma coluna de data/hora.")

    if col_hora is not None and col_hora != col_data:
        data_raw = base[col_data].astype(str).str.strip() + " " + base[col_hora].astype(str).str.strip()
    else:
        data_raw = base[col_data]
    datas = datas_medicao(data_raw)
    if int(datas.notna().sum()) < max(5, int(len(base) * 0.5)):
        raise ErroBaseLogger("Não foi possível identificar datas e horas suficientes nas medições.")

    excluidas = {col_data, col_hora, campo_id}
    possiveis = []
    for col in base.columns:
        if col in excluidas:
            continue
        valores = numeros(base[col])
        taxa = valores.notna().mean()
        nome = normalizar_coluna(col)
        prioridade = 2 if any(k in nome for k in ("press", "mca", "bar")) else (
            1 if "valor" in nome else 0
        )
        if taxa > 0.5:
            possiveis.append((prioridade, taxa, col, valores))
    if not possiveis:
        raise ErroBaseLogger("Não foi possível identificar a coluna de pressão.")
    _, _, coluna_pressao, pressoes = max(
        possiveis, key=lambda x: (x[0], x[1])
    )

    dados = pd.DataFrame({"data": datas, "pressao": pressoes}).dropna()
    dados = (
        dados.sort_values("data")
        .drop_duplicates("data", keep="last").reset_index(drop=True)
    )
    if len(dados) < 3:
        raise ErroBaseLogger("A planilha precisa conter pelo menos três medições válidas.")
    return dados, str(coluna_pressao), galaxia_id


def _pt(valor, decimais=1):
    return f"{valor:,.{decimais}f}".replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass(frozen=True)
class PadraoHorario:
    classificacao: str
    descricao: str
    dias_referencia: int
    medicoes_referencia: int
    intervalo_inferior: float | None
    intervalo_superior: float | None


def padrao_horario(dados):
    """Compara a última leitura com outros DIAS no mesmo horário ±30 min."""
    ultimo = dados.iloc[-1]
    historico = dados[dados.data.dt.date < ultimo.data.date()]
    horario = ultimo.data.hour * 60 + ultimo.data.minute
    minutos = historico.data.dt.hour * 60 + historico.data.dt.minute
    distancia = np.minimum((minutos - horario).abs(), 1440 - (minutos - horario).abs())
    referencia = historico.loc[distancia <= JANELA_HISTORICA_MIN, "pressao"]
    dias = historico.loc[distancia <= JANELA_HISTORICA_MIN, "data"].dt.date.nunique()
    if dias < 5 or len(referencia) < 25:
        return PadraoHorario(
            "Dados insuficientes",
            "não há dias suficientes para confirmar o padrão histórico desse horário",
            int(dias), len(referencia), None, None,
        )
    q05, q95 = referencia.quantile([0.05, 0.95])
    tolerancia = max(0.8, 0.07 * (q95 - q05))
    proporcao_proxima = (referencia.sub(ultimo.pressao).abs() <= tolerancia).mean()
    dentro = (q05 - tolerancia <= ultimo.pressao <= q95 + tolerancia)
    if dentro and proporcao_proxima >= 0.10:
        return PadraoHorario(
            "Dentro do padrão",
            "está dentro do padrão histórico habitual para esse horário",
            int(dias), len(referencia),
            float(q05-tolerancia), float(q95+tolerancia),
        )
    return PadraoHorario(
        "Fora do padrão",
        "está fora do padrão histórico habitual para esse horário",
        int(dias), len(referencia),
        float(q05-tolerancia), float(q95+tolerancia),
    )


def variacao_recente(dados, unidade="mca"):
    """Mantém as regras de salto persistente e a janela de 90 min da fonte."""
    ultimo = dados.iloc[-1]
    tempo = dados.data.diff().dt.total_seconds().div(60)
    passo = tempo[(tempo > 0) & (tempo <= 30)].median()
    if pd.isna(passo):
        passo = 5
    max_intervalo = max(15, 2.5 * passo)
    deltas = dados.pressao.diff()
    rel = deltas.abs() / dados.pressao.shift().abs().clip(lower=1.0)
    candidatos = dados.index[
        (dados.data >= ultimo.data - pd.Timedelta(minutes=JANELA_MOVIMENTO_MIN))
        & (tempo > 0) & (tempo <= max_intervalo)
        & (deltas.abs() >= MUDANCA_MINIMA)
        & (rel >= 0.10)
    ]
    for i in reversed(candidatos.to_list()):
        antes, depois = dados.iloc[i - 1], dados.iloc[i]
        amplitude = abs(depois.pressao - antes.pressao)
        if abs(ultimo.pressao - depois.pressao) > max(2.0, 0.15 * amplitude):
            continue
        direcao = "queda" if depois.pressao < antes.pressao else "elevação"
        percentual = amplitude / abs(antes.pressao) * 100 if abs(antes.pressao) >= 1 else None
        intensidade = (
            "acentuada" if amplitude >= 10 or
            (amplitude >= 5 and percentual is not None and percentual >= 30)
            else "leve"
        )
        percentual_txt = f" ({_pt(percentual)}%)" if percentual is not None else ""
        return (
            f"Houve uma {direcao} {intensidade} de {_pt(amplitude)} {unidade}"
            f"{percentual_txt}, de {_pt(antes.pressao)} às "
            f"{antes.data:%Hh%M} para {_pt(depois.pressao)} às "
            f"{depois.data:%Hh%M}."
        )

    recorte = dados[dados.data >= ultimo.data - pd.Timedelta(minutes=60)]
    if len(recorte) >= 3 and (
        recorte.data.max() - recorte.data.min()
    ).total_seconds() >= 20 * 60:
        inicio = recorte.iloc[0]
        delta = ultimo.pressao - inicio.pressao
        amplitude = abs(delta)
        if amplitude >= max(0.8, 0.06 * max(abs(inicio.pressao), 1)):
            tipo = "queda" if delta < 0 else "elevação"
            perc = amplitude / abs(inicio.pressao) * 100 if abs(inicio.pressao) >= 1 else None
            grau = (
                "acentuada" if amplitude >= 10 or
                (amplitude >= 5 and perc is not None and perc >= 30)
                else "leve"
            )
            perc_txt = f" ({_pt(perc)}%)" if perc is not None else ""
            return (
                f"Na última hora, houve {tipo} {grau} de "
                f"{_pt(amplitude)} {unidade}{perc_txt}."
            )
    return "Não houve queda ou elevação significativa no período recente."


def diagnosticar(dados, unidade="mca"):
    """Diagnóstico textual e métricas baseadas somente na série importada."""
    if dados.empty or len(dados) < 3:
        raise ErroBaseLogger("São necessárias três medições válidas para analisar.")
    ultimo = dados.iloc[-1]
    padrao = padrao_horario(dados)
    texto = (
        f"Na última medição ({ultimo.data:%d/%m/%Y às %Hh%M}), "
        f"a pressão foi de {_pt(ultimo.pressao)} {unidade} e "
        f"{padrao.descricao}. {variacao_recente(dados, unidade)}"
    )
    return {
        "classificacao": padrao.classificacao,
        "diagnostico": texto,
        "data_ultima": ultimo.data,
        "pressao_ultima": float(ultimo.pressao),
        "dias_referencia": padrao.dias_referencia,
        "medicoes_referencia": padrao.medicoes_referencia,
        "referencia_inferior": padrao.intervalo_inferior,
        "referencia_superior": padrao.intervalo_superior,
        "minimo": float(dados.pressao.min()),
        "maximo": float(dados.pressao.max()),
        "media": float(dados.pressao.mean()),
        "quantidade": len(dados),
    }
