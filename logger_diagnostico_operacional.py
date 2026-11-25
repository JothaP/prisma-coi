"""Interpretação operacional de históricos de pressão (mca).

Não confundir: atipicidade estatística, tendência e possível comprometimento
do abastecimento são dimensões independentes. Não confirma FA ou anormalidade
hidráulica sem avaliação de campo/rede. Limiares locais são opcionais.
"""
from __future__ import annotations

import math
import pandas as pd
import numpy as np

JANELA_RECENTE_MIN = 90
JANELA_RECUPERACAO_H = 12
LIMIAR_ZERO_MCA = 0.5
DURACAO_MIN = 15


def _fmt(valor, decimais=1):
    return f"{float(valor):.{decimais}f}".replace(".", ",")


def _historico_do_horario(dados, ultimo, tolerancia_min=30):
    anteriores = dados[dados["data"].dt.date < ultimo["data"].date()].copy()
    minutos_ref = anteriores["data"].dt.hour * 60 + anteriores["data"].dt.minute
    minuto_atual = ultimo["data"].hour * 60 + ultimo["data"].minute
    dist = (minutos_ref - minuto_atual).abs()
    dist = np.minimum(dist, 1440 - dist)
    return anteriores.loc[dist <= tolerancia_min, "pressao"]


def _janela_contigua(dados, predicado, inicio_janela=None):
    """Trecho contínuo até a última leitura, sem extrapolar lacunas da série.

    Tempo entre leituras limitado a 30 minutos; no máximo 90 minutos antes da
    última observação. Duas leituras não bastam para inferir persistência.
    """
    ultimo = dados.iloc[-1]
    janela = dados[
        dados["data"] >= ultimo["data"] - pd.Timedelta(minutes=JANELA_RECENTE_MIN)
    ]
    if inicio_janela is not None:
        janela = janela[janela["data"] >= inicio_janela]
    if janela.empty or not bool(predicado(float(ultimo["pressao"]))):
        return 0, 0.0
    serie = janela.sort_values("data")
    i = len(serie) - 1
    inicio = serie.iloc[i]["data"]
    n = 1
    while i > 0:
        anterior, atual = serie.iloc[i - 1], serie.iloc[i]
        gap = (atual["data"] - anterior["data"]).total_seconds() / 60
        if gap <= 0 or gap > 30 or not predicado(float(anterior["pressao"])):
            break
        n += 1
        inicio = anterior["data"]
        i -= 1
    minutos = (ultimo["data"] - inicio).total_seconds() / 60
    return n, float(minutos)


def _ultima_janela(dados):
    ultimo = dados.iloc[-1]
    return dados.loc[
        dados["data"] >= ultimo["data"] - pd.Timedelta(minutes=JANELA_RECENTE_MIN)
    ].sort_values("data")


def _houve_recuperacao(dados, limite_ref, ultima_pressao):
    """Sinal observado de recuperação, não evidência conclusiva de normalização.

    Exige ao menos duas leituras recentes no patamar recuperado e uma leitura
    próxima de zero nas 12 horas anteriores; não deduz causa da queda.
    """
    if ultima_pressao < 5:
        return False, None
    ultima = dados.iloc[-1]["data"]
    janela = dados[
        dados["data"] >= ultima - pd.Timedelta(hours=JANELA_RECUPERACAO_H)
    ]
    if len(janela) < 4:
        return False, None
    zeros = janela[janela["pressao"] <= LIMIAR_ZERO_MCA]
    if zeros.empty:
        return False, None
    depois = janela[janela["data"] > zeros.iloc[-1]["data"]]
    if len(depois) < 2 or not (depois["pressao"].tail(2) >= 5).all():
        return False, None
    if limite_ref is not None and ultima_pressao < limite_ref:
        return False, None  # Ainda abaixo da referência: recuperação parcial
    return True, zeros.iloc[-1]["data"]


def avaliar_operacao(dados, padrao, limiar_min_mca=None):
    """Retorna interpretação com direção, persistência, recuperação e prioridade.

    'padrao': estrutura já calculada pelo algoritmo histórico do PRISMA
    (classificação, limites, dias e n de medições).
    """
    dados = dados.sort_values("data").reset_index(drop=True)
    ultima = dados.iloc[-1]
    pressao = float(ultima["pressao"])
    valido = padrao.classificacao != "Dados insuficientes"
    limite_inferior = padrao.intervalo_inferior if valido else None
    limite_superior = padrao.intervalo_superior if valido else None

    ref = _historico_do_horario(dados, ultima)
    mediana = float(ref.median()) if valido and not ref.empty else None
    q05 = float(ref.quantile(.05)) if valido and not ref.empty else None
    q95 = float(ref.quantile(.95)) if valido and not ref.empty else None
    if not valido:
        direcao = "Referência insuficiente"
    elif pressao < limite_inferior:
        direcao = "Abaixo do padrão"
    elif pressao > limite_superior:
        direcao = "Acima do padrão"
    elif padrao.classificacao == "Fora do padrão":
        direcao = "Atípico dentro da faixa"
    else:
        direcao = "Dentro do padrão"
    desvio = pressao - mediana if mediana is not None else None

    recente = _ultima_janela(dados)
    n_recentes = len(recente)
    duracao_zero = _janela_contigua(
        dados, lambda v: v <= LIMIAR_ZERO_MCA,
    )
    duracao_abaixo = (
        _janela_contigua(dados, lambda v: v < limite_inferior)
        if direcao == "Abaixo do padrão" else (0, 0.0)
    )
    padrao_persistente = (
        duracao_abaixo[0] >= 3 and duracao_abaixo[1] >= DURACAO_MIN
    )
    zero_persistente = (
        duracao_zero[0] >= 3 and duracao_zero[1] >= DURACAO_MIN
    )
    recuperou, momento_zero = _houve_recuperacao(
        dados, limite_inferior, pressao
    )
    condicao_local = None
    if limiar_min_mca is not None:
        valor_limite = float(limiar_min_mca)
        if not math.isfinite(valor_limite) or valor_limite < 0:
            raise ValueError("O limite operacional local deve ser não negativo e finito.")
        condicao_local = pressao < valor_limite

    if zero_persistente:
        prioridade = "Alta prioridade"
        justificativa = (
            f"Foram observadas pelo menos {duracao_zero[0]} leituras "
            f"próximas de zero durante {_fmt(duracao_zero[1],0)} min, "
            "sem lacunas superiores a 30 min."
        )
    elif pressao <= LIMIAR_ZERO_MCA:
        prioridade = "Atenção"
        justificativa = (
            "A última leitura está próxima de zero; é necessário confirmar "
            "a persistência e descartar falha de equipamento."
        )
    elif condicao_local is True:
        prioridade = "Atenção"
        justificativa = (
            f"A última pressão está abaixo do limite operacional informado "
            f"({_fmt(limiar_min_mca)} mca)."
        )
    elif direcao == "Abaixo do padrão":
        prioridade = "Atenção"
        justificativa = (
            f"Pressão abaixo da referência do horário"
            + (
                f" há pelo menos {_fmt(duracao_abaixo[1],0)} min "
                "em observações contínuas."
                if padrao_persistente else
                "; persistência ainda não confirmada."
            )
        )
    elif direcao == "Acima do padrão":
        prioridade = "Monitorar"
        justificativa = (
            "Pressão acima do comportamento habitual. Não representa, por si só, "
            "falta de água; avaliar possíveis condições de sobrepressão."
        )
    elif direcao == "Atípico dentro da faixa":
        prioridade = "Monitorar"
        justificativa = (
            "Leitura pouco frequente embora situada dentro da faixa histórica; "
            "não constitui, isoladamente, indício de falha de abastecimento."
        )
    elif not valido:
        prioridade = "Referência insuficiente"
        justificativa = (
            "Não há histórico suficiente para avaliar o padrão desse horário. "
            "A pressão atual deve ser interpretada com os critérios locais."
        )
    else:
        prioridade = "Sem sinal histórico de queda"
        justificativa = (
            "A última leitura está dentro do padrão do horário, "
            "mas essa condição não comprova a adequação hidráulica do ponto."
        )

    if n_recentes < 2:
        tendencia_texto = (
            "Há menos de duas medições nos 90 minutos anteriores "
            "à última leitura; não é possível concluir sobre variação recente."
        )
        tendencia = "Dados recentes insuficientes"
    else:
        vals = recente["pressao"]
        dif = float(vals.iloc[-1] - vals.iloc[0])
        amplitude = float(vals.max() - vals.min())
        if amplitude >= 3 and abs(dif) >= 3:
            tendencia = "Elevação recente" if dif > 0 else "Queda recente"
            tendencia_texto = (
                f"Entre a primeira e a última leitura dos últimos 90 min, "
                f"houve {'elevação' if dif > 0 else 'queda'} líquida de "
                f"{_fmt(abs(dif))} mca."
            )
        elif amplitude >= 3:
            tendencia = "Oscilação recente"
            tendencia_texto = (
                f"Nos últimos 90 min, ocorreram oscilações de até "
                f"{_fmt(amplitude)} mca, com pouca mudança líquida."
            )
        else:
            tendencia = "Sem variação relevante"
            tendencia_texto = (
                "Não houve variação relevante entre as leituras disponíveis "
                "dos últimos 90 min. Isso não elimina eventual desvio persistente."
            )
        if (recente["data"].diff().dt.total_seconds().div(60) > 30).any():
            tendencia_texto += (
                " Há lacunas superiores a 30 min entre medições; "
                "a continuidade do comportamento não pode ser assegurada."
            )

    # A leitura pode ter retornado à faixa histórica após uma queda
    # expressiva: isso requer acompanhamento, mesmo quando o valor FINAL
    # já é habitual. Não rotular como "sem sinal histórico de queda".
    if prioridade == "Sem sinal histórico de queda":
        if recuperou:
            prioridade = "Monitorar"
            justificativa = (
                "Há registros de pressão próxima de zero e recuperação "
                "observada. Recomenda-se conferir o histórico e a situação "
                "da rede antes de considerar a condição normalizada."
            )
        elif tendencia in ("Queda recente", "Oscilação recente"):
            prioridade = "Monitorar"
            justificativa = (
                "Foram registradas alterações recentes na pressão mesmo "
                "com a última leitura dentro da faixa habitual."
            )

    if recuperou:
        status = "Recuperação observada"
        recuperacao_texto = (
            "Há evidência de recuperação da pressão após leituras próximas "
            f"de zero registradas em {momento_zero:%d/%m às %Hh%M}. "
            "Isso não confirma a causa nem a normalização do abastecimento."
        )
    elif pressao >= 5 and (
        dados.loc[
            dados["data"] >= ultima["data"] - pd.Timedelta(hours=JANELA_RECUPERACAO_H),
            "pressao"
        ] <= LIMIAR_ZERO_MCA).any():
        status = "Recuperação parcial ou inconclusiva"
        recuperacao_texto = (
            "Houve leitura próxima de zero nas últimas 12 horas e pressão "
            "posteriormente superior; os dados não são suficientes para "
            "confirmar recuperação sustentada dentro da referência."
        )
    elif zero_persistente:
        status = "Patamar próximo de zero persistente"
        recuperacao_texto = "Não há recuperação confirmada na janela analisada."
    elif padrao_persistente:
        status = "Pressão abaixo da referência persistente"
        recuperacao_texto = (
            "A ausência de nova queda brusca não descaracteriza "
            "a permanência em um patamar reduzido."
        )
    else:
        status = tendencia
        recuperacao_texto = ""

    ref_texto = (
        f"Referência para esse horário: mediana {_fmt(mediana)} mca"
        f"; intervalo habitual ampliado de {_fmt(limite_inferior)} a "
        f"{_fmt(limite_superior)} mca"
        f" ({padrao.medicoes_referencia} medições em "
        f"{padrao.dias_referencia} dias anteriores)."
        if valido else
        f"Histórico insuficiente para confirmar o padrão: "
        f"{padrao.medicoes_referencia} medições "
        f"em {padrao.dias_referencia} dias anteriores próximos desse horário "
        "(mínimo necessário: 25 medições e 5 dias)."
    )
    diagnostico = (
        f"Na última leitura ({ultima['data']:%d/%m/%Y às %Hh%M}), "
        f"a pressão foi de {_fmt(pressao)} mca. {ref_texto} "
        f"Classificação: {direcao.lower()}. {tendencia_texto} "
        + (recuperacao_texto + " " if recuperacao_texto else "")
        + "Prioridade investigativa: " + prioridade.lower() + ". "
        + justificativa
    )
    return {
        "direcao": direcao,
        "referencia_horario_mediana": mediana,
        "referencia_horario_q05": q05,
        "referencia_horario_q95": q95,
        "desvio_mca": desvio,
        "tendencia": tendencia,
        "status_recente": status,
        "recuperacao": bool(recuperou),
        "persistencia_abaixo_min": (
            duracao_abaixo[1] if padrao_persistente else None
        ),
        "persistencia_zero_min": (
            duracao_zero[1] if zero_persistente else None
        ),
        "prioridade": prioridade,
        "justificativa_prioridade": justificativa,
        "limiar_operacional_mca": limiar_min_mca,
        "diagnostico_operacional": diagnostico,
        "observacoes_recentes": n_recentes,
    }
