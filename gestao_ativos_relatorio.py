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
STATUS_FECHAMENTOS_RELATORIO = {
    "Pendente": "🔴",
    "Em andamento": "🟡",
    "Concluído": "🟢",
    "Cancelado": "⚫",
}
CODIGOS_FECHAMENTO = {
    "pendente": "Pendente",
    "em_andamento": "Em andamento",
    "concluido": "Concluído",
    "cancelado": "Cancelado",
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





def preparar_fechamentos_relatorio(fechamentos: list[dict]) -> list[dict]:
    """Conserva os status definidos nas fichas; permite revisão antes da geração."""
    registros = []
    for reg in fechamentos:
        status = CODIGOS_FECHAMENTO.get(_texto(reg.get("status")))
        if status is None:
            continue
        registros.append({
            "Incluir": status in ("Pendente", "Em andamento"),
            "Ordem": len(registros) + 1,
            "Status do fechamento": status,
            "Líder": _texto(reg.get("lider")),
            "Equipe": _texto(reg.get("equipe")),
            "Matrícula": _texto(reg.get("matricula")),
            "O.S.": _texto(reg.get("os")),
            "Bairro": _texto(reg.get("bairro")),
            "Data": _texto(reg.get("data"))[:10],
        })
    return registros


def _data_relatorio_fechamento(valor: str) -> str:
    valor = _texto(valor)
    try:
        return date.fromisoformat(valor[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return valor


def _formatar_fechamento(item: dict, numero: int) -> str:
    partes = []
    for chave, rotulo in (
        ("Bairro", "Bairro"), ("Líder", "Líder"), ("Equipe", "Equipe"),
        ("Matrícula", "Matrícula"), ("O.S.", "O.S."),
        ("Data", "Data"),
    ):
        valor = _texto(item.get(chave))
        if valor:
            if chave == "Data":
                valor = _data_relatorio_fechamento(valor)
            partes.append(f"{rotulo}: {valor}")
    return f"🚧 {numero}. " + (" | ".join(partes) if partes else "Reparo de rede")


def gerar_pontos_atencao(municipio: str, data_relatorio: date, itens: list[dict],
                         fechamentos: list[dict] | None = None) -> str:
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
    # A relação de fechamentos fica separada dos poços e agrupada pelo status
    # atribuído ao reparo, sem qualquer inferência a partir da existência de O.S.
    grupos_rede = {status: [] for status in STATUS_FECHAMENTOS_RELATORIO}
    for _, reparo in sorted(enumerate(fechamentos or []),
                            key=lambda par: _ordem_item(par[1], par[0])):
        if reparo.get("Incluir") is not True:
            continue
        status = _texto(reparo.get("Status do fechamento"))
        if status in grupos_rede:
            grupos_rede[status].append(reparo)
    if any(grupos_rede.values()):
        cabecalho.extend(["______________", "🚧 FECHAMENTOS / REPAROS DE REDE"])
        for status, lista in grupos_rede.items():
            if not lista:
                continue
            cabecalho.extend([
                "______________",
                f"{STATUS_FECHAMENTOS_RELATORIO[status]} {status}:",
                "_____________________",
            ])
            for numero, reparo in enumerate(lista, start=1):
                cabecalho.append(_formatar_fechamento(reparo, numero))
    return "\n".join(cabecalho).strip() + "\n"
