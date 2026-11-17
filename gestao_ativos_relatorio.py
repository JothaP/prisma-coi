"""Relatório textual de Pontos de Atenção do Módulo 9.

Somente formatação em memória. Não classifica status automaticamente,
não grava fichas e não cria novos registros operacionais.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re


SECOES = ("ÁGUA", "OBSERVAÇÕES POÇOS")
SITUACOES = ("Pendente", "Em andamento")
SIMBOLOS = {"Pendente": "🔴", "Em andamento": "🟡"}


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
        return ""
    texto = str(valor).strip()
    if texto.lower() in ("nan", "none", "<na>"):
        return ""
    return re.sub(r"\s+", " ", texto)


def _ordens_ativo(registro: dict) -> str:
    """Preserva zeros à esquerda e ambas as O.S. quando diferentes."""
    ordens = []
    for campo in ("ultima_os_operacao", "ultima_os_eletromecanica"):
        valor = _texto(registro.get(campo))
        if valor and valor not in ordens:
            ordens.append(valor)
    return " / ".join(ordens)


def preparar_itens_relatorio(ativos: list[dict], incluir_operando=False) -> list[dict]:
    """Seleciona fichas preenchidas; NÃO infere situação ou seção pelo status."""
    fichas = []
    for ativo in ativos:
        if ativo.get("status") == "operando" and not incluir_operando:
            continue
        if not any(_texto(ativo.get(campo)) for campo in (
            "status", "observacao", "ultima_os_operacao",
            "ultima_os_eletromecanica", "protocolo_equatorial"
        )):
            continue
        nome = _texto(ativo.get("nome")) or _texto(ativo.get("planta"))
        # Mantém os zeros dos identificadores (THE-041 -> THE 041).
        if re.match(r"^THE[- ]", nome, re.IGNORECASE):
            nome = re.sub(r"^THE[- ]+", "THE ", nome, flags=re.IGNORECASE)
        fichas.append({
            "Incluir": True,
            "Ordem": len(fichas) + 1,
            "Seção": "OBSERVAÇÕES POÇOS",
            "Situação": "Pendente",
            "Poço / ativo": nome,
            "Observação": _texto(ativo.get("observacao")),
            "O.S.": _ordens_ativo(ativo),
            "Nota": "",
            "Planta de origem": _texto(ativo.get("planta")),
        })
    return fichas


def _ordem_item(item: dict, indice: int) -> tuple[int, int]:
    try:
        ordem = float(item.get("Ordem"))
        if math.isfinite(ordem) and ordem >= 0:
            return int(ordem), indice
    except (TypeError, ValueError):
        pass
    return 999999 + indice, indice


def gerar_pontos_atencao(municipio: str, data_relatorio: date, itens: list[dict]) -> str:
    """Produz texto pronto para copiar. Situação e seção são editadas pelo usuário."""
    if not isinstance(data_relatorio, (date, datetime)):
        raise ValueError("Data do relatório inválida.")
    titulo = f"📍 PONTOS DE ATENÇÃO – {_texto(municipio).upper()} | {data_relatorio:%d/%m/%Y}"
    cabecalho = [titulo, "🟡 Em andamento | 🔴 Pendente", "______________", "💧 ÁGUA", ""]
    lista = sorted(enumerate(itens), key=lambda par: _ordem_item(par[1], par[0]))
    por_secao = {nome: [] for nome in SECOES}
    for _, item in lista:
        if item.get("Incluir") is not True:
            continue
        secao = _texto(item.get("Seção"))
        situacao = _texto(item.get("Situação"))
        nome = _texto(item.get("Poço / ativo"))
        if secao not in por_secao or situacao not in SIMBOLOS or not nome:
            continue
        partes = [SIMBOLOS[situacao] + " " + nome]
        observacao = _texto(item.get("Observação"))
        ordem_servico = _texto(item.get("O.S."))
        nota = _texto(item.get("Nota"))
        if observacao:
            partes.append(observacao)
        if ordem_servico:
            partes.append("Ordem: " + ordem_servico)
        if nota:
            partes.append("Nota: " + nota)
        por_secao[secao].append(partes)

    for partes in por_secao["ÁGUA"]:
        cabecalho.append(partes[0] + (" - " + " | ".join(partes[1:]) if len(partes) > 1 else ""))
    cabecalho.extend(["______________", "⚠️ OBSERVAÇÕES POÇOS:"])
    for numero, partes in enumerate(por_secao["OBSERVAÇÕES POÇOS"], start=1):
        texto = f"{partes[0].split(' ', 1)[0]} {numero}. {partes[0].split(' ', 1)[1]}"
        cabecalho.append(texto + (" - " + " | ".join(partes[1:]) if len(partes) > 1 else ""))
    return "\n".join(cabecalho).strip() + "\n"
