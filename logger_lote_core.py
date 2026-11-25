"""Análise em lote de históricos de loggers por Galaxia ID, sem persistência.

Cada equipamento é isolado; arquivos diferentes do mesmo Galaxia ID são
concatenados, e medições de IDs diferentes nunca compartilham referência.
Erros individuais não impedem o processamento dos demais loggers.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

from logger_monitor_core import ErroBaseLogger, ler_arquivo
from logger_comportamento_core import (
    coluna_id, diagnosticar, extrair_historico, ids_disponiveis,
)

MAX_LOGGERS_LOTE = 150
MAX_ARQUIVOS = 100


def _id_normalizado(valor):
    return str(valor).strip().removesuffix(".0")


def analisar_lote(arquivos, limiar_min_mca=None):
    """Retorna (tabela de panorama, {id: (serie, diagnostico)}).

    arquivos: sequência de (nome, bytes). Não guarda arquivos em Supabase.
    """
    if not arquivos:
        raise ErroBaseLogger("Envie pelo menos um histórico.")
    if len(arquivos) > MAX_ARQUIVOS:
        raise ErroBaseLogger(
            f"Limite de {MAX_ARQUIVOS} arquivos por processamento. "
            "Divida o lote para evitar sobrecarga."
        )
    grupos = defaultdict(list)
    origens = defaultdict(set)
    falhas = []
    for nome, conteudo in arquivos:
        try:
            base = ler_arquivo(nome, conteudo)
            campo = coluna_id(base)
            if campo is None:
                # Preserva apenas UM arquivo independente por nome (sem ID não
                # é seguro atribuir um equipamento a outro por heurística).
                identificador = "Arquivo: " + Path(nome).stem
                grupos[identificador].append(base)
                origens[identificador].add(nome)
            else:
                ids = ids_disponiveis(base)
                if not ids:
                    raise ErroBaseLogger("Não há Galaxia ID válido nesse arquivo.")
                for identificador in ids:
                    mascara = (
                        base[campo].astype(str).str.strip()
                        .str.replace(r"\.0$", "", regex=True)
                        .eq(identificador)
                    )
                    grupos[identificador].append(base.loc[mascara].copy())
                    origens[identificador].add(nome)
        except (ErroBaseLogger, ValueError, KeyError, OSError) as e:
            falhas.append({
                "Galaxia ID": "Arquivo: " + nome,
                "Classificação": "Erro de importação",
                "Prioridade": "Não avaliado",
                "Detalhe": str(e),
            })

    if len(grupos) > MAX_LOGGERS_LOTE:
        raise ErroBaseLogger(
            f"O lote contém {len(grupos)} loggers. "
            f"Limite desta versão: {MAX_LOGGERS_LOTE}. "
            "Divida os arquivos ou selecione menos equipamentos."
        )
    resultados = []
    detalhes = {}
    for logger_id, partes in sorted(grupos.items(), key=lambda t: t[0]):
        try:
            base_unificada = pd.concat(partes, ignore_index=True, sort=False)
            # Cada grupo já contém somente um ID; sem ID rotulamos como arquivo.
            serie, coluna, _ = extrair_historico(
                base_unificada,
                None if logger_id.startswith("Arquivo: ") else logger_id,
            )
            diagnostico = diagnosticar(
                serie, limiar_min_mca=limiar_min_mca,
            )
            detalhes[logger_id] = (serie, diagnostico)
            resultados.append({
                "Galaxia ID": logger_id,
                "Classificação": diagnostico["classificacao"],
                "Direção": diagnostico["direcao"],
                "Prioridade": diagnostico["prioridade"],
                "Situação recente": diagnostico["status_recente"],
                "Última pressão (mca)": diagnostico["pressao_ultima"],
                "Mediana do horário (mca)": diagnostico["referencia_horario_mediana"],
                "Desvio da mediana (mca)": diagnostico["desvio_mca"],
                "Persistência abaixo (min)": diagnostico["persistencia_abaixo_min"],
                "Persistência 0 mca (min)": diagnostico["persistencia_zero_min"],
                "Última leitura": diagnostico["data_ultima"],
                "Dias de referência": diagnostico["dias_referencia"],
                "Medições referência": diagnostico["medicoes_referencia"],
                "Medições históricas": diagnostico["quantidade"],
                "Arquivos": len(origens[logger_id]),
                "Coluna utilizada": coluna,
                "Detalhe": diagnostico["justificativa_prioridade"],
            })
        except (ErroBaseLogger, ValueError, KeyError, TypeError, IndexError) as e:
            resultados.append({
                "Galaxia ID": logger_id,
                "Classificação": "Não avaliável",
                "Direção": "Indeterminada",
                "Prioridade": "Não avaliado",
                "Situação recente": "Dados indisponíveis",
                "Arquivos": len(origens[logger_id]),
                "Detalhe": str(e),
            })

    resultados.extend(falhas)
    if not resultados:
        raise ErroBaseLogger("Nenhum histórico foi identificado nos arquivos.")

    df = pd.DataFrame(resultados)
    colunas = [
        "Galaxia ID", "Classificação", "Direção", "Prioridade",
        "Situação recente", "Última pressão (mca)",
        "Mediana do horário (mca)", "Desvio da mediana (mca)",
        "Persistência abaixo (min)", "Persistência 0 mca (min)",
        "Última leitura", "Dias de referência", "Medições referência",
        "Medições históricas", "Arquivos", "Coluna utilizada", "Detalhe",
    ]
    df = df.reindex(columns=colunas)
    ordem = {
        "Alta prioridade": 0,
        "Atenção": 1,
        "Monitorar": 2,
        "Sem sinal histórico de queda": 3,
        "Referência insuficiente": 4,
        "Não avaliado": 5,
    }
    df["_ordem"] = df["Prioridade"].map(ordem).fillna(5)
    df = df.sort_values(
        ["_ordem", "Galaxia ID"], kind="stable"
    ).drop(columns=["_ordem"]).reset_index(drop=True)
    return df, detalhes


def catalogar_historicos(arquivos):
    """Importa vários históricos para consulta por seletor, SEM analisá-los todos.

    Retorna (catalogo, origens, falhas). Cada chave corresponde a um único
    Galaxia ID ou, quando ausente, a um arquivo identificado explicitamente
    como tal. A interpretação é executada apenas depois que o operador escolhe
    um equipamento, na interface Streamlit.
    """
    if not arquivos:
        raise ErroBaseLogger("Envie pelo menos um histórico.")
    if len(arquivos) > MAX_ARQUIVOS:
        raise ErroBaseLogger(
            f"Limite de {MAX_ARQUIVOS} arquivos por importação."
        )
    partes = defaultdict(list)
    origens = defaultdict(set)
    falhas = []
    arquivos_sem_id = 0
    for nome, conteudo in arquivos:
        try:
            base = ler_arquivo(nome, conteudo)
            if base.empty:
                raise ErroBaseLogger("A planilha está vazia.")
            campo = coluna_id(base)
            if campo is None:
                # Sem Galaxia ID não há como garantir identidade entre arquivos.
                # Cada histórico fica isolado, inclusive com nomes repetidos.
                arquivos_sem_id += 1
                titulo = "Arquivo: " + Path(nome).stem
                identificador = titulo
                n = 2
                while identificador in partes:
                    identificador = f"{titulo} ({n})"
                    n += 1
                partes[identificador].append(base)
                origens[identificador].add(nome)
            else:
                ids = ids_disponiveis(base)
                if not ids:
                    raise ErroBaseLogger(
                        "A coluna Galaxia ID está presente, mas não contém IDs válidos."
                    )
                for identificador in ids:
                    mascara = (
                        base[campo].astype(str).str.strip()
                        .str.replace(r"\.0$", "", regex=True)
                        .eq(identificador)
                    )
                    fatia = base.loc[mascara].copy()
                    if not fatia.empty:
                        partes[identificador].append(fatia)
                        origens[identificador].add(nome)
        except (ErroBaseLogger, ValueError, KeyError, OSError) as exc:
            falhas.append(f"{nome}: {exc}")
    if not partes:
        raise ErroBaseLogger(
            "Nenhum histórico válido foi identificado. "
            + ("Erros: " + " | ".join(falhas[:3]) if falhas else "")
        )
    if len(partes) > MAX_LOGGERS_LOTE:
        raise ErroBaseLogger(
            f"Foram identificados {len(partes)} históricos. "
            f"Limite: {MAX_LOGGERS_LOTE} equipamentos por importação."
        )
    catalogo = {
        identificador: pd.concat(blocos, ignore_index=True, sort=False)
        for identificador, blocos in sorted(partes.items())
    }
    return catalogo, {
        identificador: sorted(arquivos_fonte)
        for identificador, arquivos_fonte in origens.items()
    }, falhas


def analisar_selecionado(catalogo, identificador, limiar_min_mca=None):
    """Executa o diagnóstico SOMENTE para o logger selecionado."""
    if identificador not in catalogo:
        raise ErroBaseLogger(
            "Selecione um logger disponível nos históricos importados."
        )
    fonte = catalogo[identificador]
    # Um arquivo sem ID fica rotulado por seu nome, sem presumir ID.
    id_real = None if identificador.startswith("Arquivo: ") else identificador
    serie, coluna, _ = extrair_historico(fonte, id_real)
    resultado = diagnosticar(serie, limiar_min_mca=limiar_min_mca)
    return serie, coluna, resultado
