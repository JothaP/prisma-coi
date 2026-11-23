"""Correções geográficas manuais de O.S. no Farol Operacional (M3).

Registro append-only em aba separada. A importação PONTOS é imutável:
o mapa utiliza a correção mais recente por chave OS:<protocolo>.
Nenhuma gravação é executada na importação deste módulo.
"""
from __future__ import annotations

import math

CABECALHO = [
    "CHAVE_OS", "ACAO", "LATITUDE", "LONGITUDE",
    "LAT_ORIGINAL", "LON_ORIGINAL", "ALTERADO_EM", "AUTOR_ID", "AUTOR",
]
NOME_ABA = "FAROL_COORDENADAS_OS"
ACOES = frozenset({"CORRIGIR", "RESTAURAR"})


class ErroCoordenadasOS(ValueError):
    """Falha fechada quando o cadastro/histórico não é confiável."""


def numero_coordenada(valor, tipo):
    """Aceita ponto ou vírgula decimal; rejeita NaN, infinito e ponto nulo."""
    try:
        numero = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ErroCoordenadasOS("Latitude ou longitude inválida.") from exc
    limite = 90 if tipo == "lat" else 180
    if not math.isfinite(numero) or numero == 0 or not -limite <= numero <= limite:
        raise ErroCoordenadasOS("Coordenada fora do intervalo geográfico permitido.")
    return round(numero, 7)


def _validar_cabecalho(linhas):
    if linhas and [str(c).strip() for c in linhas[0]] != CABECALHO:
        raise ErroCoordenadasOS(
            "Cabeçalho inesperado em FAROL_COORDENADAS_OS. "
            "Nenhuma correção será aplicada ou salva até a conferência da aba."
        )


def interpretar_historico(linhas):
    """Última ação válida prevalece, sem apagar eventos ou coordenadas originais."""
    _validar_cabecalho(linhas)
    atuais = {}
    for indice, valores in enumerate(linhas[1:], start=2):
        if not any(str(c).strip() for c in valores):
            continue
        if len(valores) != len(CABECALHO):
            raise ErroCoordenadasOS(
                f"Registro incompleto na linha {indice} da aba de coordenadas."
            )
        item = dict(zip(CABECALHO, [str(c).strip() for c in valores]))
        chave = item["CHAVE_OS"]
        acao = item["ACAO"]
        if not chave.startswith("OS:") or not chave[3:] or acao not in ACOES:
            raise ErroCoordenadasOS(
                f"Registro inválido na linha {indice} da aba de coordenadas."
            )
        if acao == "CORRIGIR":
            item["lat"] = numero_coordenada(item["LATITUDE"], "lat")
            item["lon"] = numero_coordenada(item["LONGITUDE"], "lon")
        item["revisao"] = indice
        atuais[chave] = item
    return atuais


def aplicar_correcoes(df_pontos, correcoes, chave_ponto):
    """Muda só as coordenadas EM MEMÓRIA, antes do clustering/mapa/PDF."""
    if df_pontos.empty or not correcoes:
        return df_pontos
    ativos = {
        chave: (item["lat"], item["lon"])
        for chave, item in correcoes.items() if item["ACAO"] == "CORRIGIR"
    }
    if not ativos:
        return df_pontos
    resultado = df_pontos.copy()
    chaves = resultado.apply(chave_ponto, axis=1)
    indices = chaves.isin(ativos)
    if not indices.any():
        return df_pontos
    resultado.loc[indices, "Latitude"] = chaves[indices].map(
        lambda chave: ativos[chave][0]
    ).astype(float)
    resultado.loc[indices, "Longitude"] = chaves[indices].map(
        lambda chave: ativos[chave][1]
    ).astype(float)
    return resultado


def registrar_correcao(
    planilha, *, chave_os, acao, latitude, longitude,
    latitude_original, longitude_original, autor_id, autor, momento,
    revisao_esperada, worksheet_not_found,
):
    """Insere evento de auditoria, sem regravar PONTOS ou sobrescrever histórico.

    Releitura imediatamente antes do append detecta edição concorrente já
    persistida. Não substitui transação: é uma salvaguarda de melhor esforço.
    """
    if not chave_os.startswith("OS:") or not chave_os[3:]:
        raise ErroCoordenadasOS("A correção exige Nº da O.S. preenchido.")
    if acao not in ACOES:
        raise ErroCoordenadasOS("Ação de correção desconhecida.")
    if not str(autor_id).strip():
        raise PermissionError("A sessão não possui identificador de usuário.")
    if acao == "CORRIGIR":
        lat = numero_coordenada(latitude, "lat")
        lon = numero_coordenada(longitude, "lon")
    else:
        lat, lon = "", ""

    try:
        aba = planilha.worksheet(NOME_ABA)
        linhas = aba.get_all_values()
        _validar_cabecalho(linhas)
    except worksheet_not_found:
        aba = planilha.add_worksheet(
            title=NOME_ABA, rows=1000, cols=len(CABECALHO)
        )
        aba.update(
            range_name="A1:I1", values=[CABECALHO], value_input_option="RAW"
        )
        linhas = [CABECALHO]
    estado = interpretar_historico(linhas)
    revisao_atual = (estado.get(chave_os) or {}).get("revisao")
    if revisao_atual != revisao_esperada:
        raise ErroCoordenadasOS(
            "Outra correção foi salva para esta O.S. Reabra a janela "
            "para conferir o local atual antes de continuar."
        )
    def original(valor):
        if valor is None:
            return ""
        try:
            return f"{float(valor):.7f}"
        except (TypeError, ValueError):
            return str(valor).strip()

    registro = [
        chave_os, acao,
        f"{lat:.7f}" if acao == "CORRIGIR" else "",
        f"{lon:.7f}" if acao == "CORRIGIR" else "",
        original(latitude_original), original(longitude_original),
        str(momento), str(autor_id).strip(), str(autor).strip(),
    ]
    aba.append_row(registro, value_input_option="RAW")
    return {"chave_os": chave_os, "acao": acao, "autor": str(autor)}
