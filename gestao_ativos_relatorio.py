"""Relatório textual de Pontos de Atenção do Módulo 9.

Somente formatação em memória. Reutiliza o status registrado para
cor e agrupamento; não deduz status de O.S. e não modifica o banco.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re


SECOES = ("ÁGUA", "OBSERVAÇÕES POÇOS")
SITUACOES = ("Pendente", "Em andamento")

# A ordem prioriza os dois grupos pedidos para o relatório.
# Emojis reproduzem as mesmas cores dos marcadores no mapa de ativos.
STATUS_RELATORIO = {
    "Em Observação": "🟣",
    "Em manutenção": "🟡",
    "Parado": "🔴",
    "Inativo": "⚫",
    "Validar": "⚪",
    "Operando": "🔵",
    "Não informado": "⚪",
}
STATUS_CADASTRO = {
    "em_observacao": "Em Observação",
    "em_manutencao": "Em manutenção",
    "parado": "Parado",
    "inativo": "Inativo",
    "validar": "Validar",
    "operando": "Operando",
    "": "Não informado",
}


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
    """Lê o status da ficha; não deduz a classificação a partir de O.S./observações."""
    fichas = []
    for ativo in ativos:
        status_codigo = _texto(ativo.get("status"))
        if status_codigo == "operando" and not incluir_operando:
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
            "Incluir": status_codigo != "operando",
            "Ordem": len(fichas) + 1,
            "Status no relatório": STATUS_CADASTRO.get(
                status_codigo, "Não informado"
            ),
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


def _formatar_linha(item: dict, marcador: str, numero: int | None = None) -> str:
    nome = _texto(item.get("Poço / ativo"))
    prefixo = f"{marcador} {numero}. {nome}" if numero is not None else f"{marcador} {nome}"
    complementos = []
    observacao = _texto(item.get("Observação"))
    ordem = _texto(item.get("O.S."))
    nota = _texto(item.get("Nota"))
    situacao = _texto(item.get("Situação"))
    if observacao:
        complementos.append(observacao)
    if ordem:
        complementos.append("Ordem: " + ordem)
    if nota:
        complementos.append("Nota: " + nota)
    if situacao in SITUACOES:
        complementos.append("Situação: " + situacao)
    return prefixo + (" - " + " | ".join(complementos) if complementos else "")


def gerar_pontos_atencao(municipio: str, data_relatorio: date, itens: list[dict]) -> str:
    """Agrupa pelo status selecionado no relatório e usa sua cor como marcador."""
    if not isinstance(data_relatorio, (date, datetime)):
        raise ValueError("Data do relatório inválida.")
    titulo = f"📍 PONTOS DE ATENÇÃO – {_texto(municipio).upper()} | {data_relatorio:%d/%m/%Y}"
    cabecalho = [titulo, "💧 ÁGUA"]
    grupos: dict[str, dict[str, list[dict]]] = {
        status: {secao: [] for secao in SECOES} for status in STATUS_RELATORIO
    }
    lista = sorted(enumerate(itens), key=lambda par: _ordem_item(par[1], par[0]))
    for _, item in lista:
        if item.get("Incluir") is not True:
            continue
        nome = _texto(item.get("Poço / ativo"))
        status = _texto(item.get("Status no relatório"))
        secao = _texto(item.get("Seção"))
        if not nome or status not in grupos or secao not in SECOES:
            continue
        grupos[status][secao].append(item)

    for status, secoes in grupos.items():
        if not any(secoes.values()):
            continue
        simbolo = STATUS_RELATORIO[status]
        cabecalho.extend(["______________", f"{simbolo} {status}:", "_____________________"])
        if secoes["ÁGUA"]:
            for item in secoes["ÁGUA"]:
                cabecalho.append(_formatar_linha(item, simbolo))
        if secoes["OBSERVAÇÕES POÇOS"]:
            cabecalho.append("⚠️ OBSERVAÇÕES POÇOS:")
            for numero, item in enumerate(secoes["OBSERVAÇÕES POÇOS"], start=1):
                cabecalho.append(_formatar_linha(item, simbolo, numero))
    return "\n".join(cabecalho).strip() + "\n"
