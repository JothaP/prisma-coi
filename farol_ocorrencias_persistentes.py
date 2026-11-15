"""Farol M3 — identidade permanente do acompanhamento (ficha.id).

Resolução puramente em memória. Sem rede, Streamlit, agendamentos nem gravações.
O cálculo espacial das concentrações permanece inalterado.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import asin, cos, radians, sin, sqrt, isfinite
import unicodedata
import re

import pandas as pd

NAO_CLASSIFICADO = "Sem vínculo exato identificado"


def normalizar_municipio(valor):
    x = unicodedata.normalize("NFKD", str(valor or "").strip().upper())
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in x if not unicodedata.combining(c)))


def distancia_metros(lat1, lon1, lat2, lon2):
    try:
        a, b, c, d = map(float, (lat1, lon1, lat2, lon2))
        if not all(isfinite(x) for x in (a, b, c, d)):
            return None
        if not (-90 <= a <= 90 and -90 <= c <= 90 and -180 <= b <= 180 and -180 <= d <= 180):
            return None
        da, db = radians(c-a), radians(d-b)
        h = sin(da/2)**2 + cos(radians(a))*cos(radians(c))*sin(db/2)**2
        return 6371000 * 2 * asin(min(1, sqrt(max(0, h))))
    except (TypeError, ValueError, OverflowError):
        return None


def chaves_eventos_confirmados(os_grupo, eventos_relacionados, chave_evento,
                               converter_data, classificar_status, agora):
    """Somente eventos comprovados por O.S. + território + período.

    Uma concentração pode possuir vários eventos; nesse caso a associação
    automática a uma ficha de evento não é segura.
    """
    if os_grupo is None or os_grupo.empty or eventos_relacionados is None or eventos_relacionados.empty:
        return ()
    encontrados = set()
    for _, evento in eventos_relacionados.iterrows():
        inicio = converter_data(evento.get("Início", ""))
        fim = converter_data(evento.get("Término Real", ""))
        if pd.isna(inicio):
            continue
        if pd.isna(fim):
            if classificar_status(evento.get("Status", ""))[0] == "Finalizado":
                continue
            limite = agora
        else:
            if fim < inicio:
                continue
            limite = fim + pd.Timedelta(hours=3)
        ids = evento.get("_indices_os_correspondentes")
        if not isinstance(ids, (tuple, list, set)):
            # Nunca usar contagem apenas territorial como prova de vínculo.
            continue
        for idx in ids:
            if idx not in os_grupo.index:
                continue
            ordem = os_grupo.loc[idx]
            abertura = converter_data(ordem.get("Dt. Emissão", ""))
            if pd.notna(abertura) and inicio - pd.Timedelta(hours=1) <= abertura <= limite:
                encontrados.add(chave_evento(evento))
                break
    return tuple(sorted(encontrados))


def _os(valor):
    if isinstance(valor, (list, tuple, set)):
        return {str(x).strip() for x in valor if str(x).strip()}
    return {x.strip() for x in str(valor or "").split(";") if x.strip()}


def _encerrado(ficha):
    return str(ficha.get("finalizado", "")).casefold().strip() == "sim" or (
        str(ficha.get("status", "")).casefold().strip().startswith("conclu")
    )


def _ordenar_ficha(fid, fichas):
    item = fichas.get(str(fid), {})
    data = str(item.get("importado_em") or "")
    try:
        numero = int(fid)
    except (ValueError, TypeError):
        numero = 999999999
    return data, numero, str(fid)


def _sugestoes(ctx, index, vinculos, fichas):
    """Índice invertido de O.S.: consulta somente fichas com O.S. em comum."""
    atuais = _os(ctx.get("os", ()))
    cidade = normalizar_municipio(ctx.get("municipio"))
    relacionados = set()
    for numero in atuais:
        relacionados.update(index.get((cidade, numero), ()))
    por_ficha = {}
    for pos in relacionados:
        antigo = vinculos[pos]
        fid = str(antigo.get("evento_id", ""))
        if fid not in fichas or _encerrado(fichas[fid]):
            continue
        anteriores = _os(antigo.get("os_origem", ""))
        iguais = len(atuais & anteriores)
        if iguais < 2 or 2*iguais < len(anteriores) or 2*iguais < len(atuais):
            continue
        distancia = distancia_metros(
            ctx.get("latitude"), ctx.get("longitude"),
            antigo.get("latitude"), antigo.get("longitude"),
        )
        if distancia is None or distancia > 1500:
            continue
        opcao = {
            "chave_anterior": str(antigo["chave"]), "evento_id": fid,
            "municipio": str(antigo.get("municipio", "")),
            "criado_em": str(antigo.get("criado_em", "")),
            "os_em_comum": iguais, "os_anteriores": len(anteriores),
            "os_atuais": len(atuais), "distancia_m": round(distancia),
            "cobertura_anterior": iguais/len(anteriores),
            "cobertura_atual": iguais/len(atuais),
        }
        score = (iguais, min(opcao["cobertura_anterior"], opcao["cobertura_atual"]), -distancia)
        atual = por_ficha.get(fid)
        if atual is None or score > atual[0]:
            por_ficha[fid] = (score, opcao)
    return [x[1] for x in sorted(por_ficha.values(), key=lambda x: x[0], reverse=True)]


def resolver_ocorrencias(contextos, vinculos, fichas, ancoras):
    """Resolve uma ficha por concentração e prepara UMA operação em lote.

    Prioridades: chave exata, identidade de evento confirmada, continuidade
    segura por O.S. e proximidade. Nunca cria fichas novas nem faz consultas.
    Fichas de eventos distintos não são fundidas.
    """
    por_chave = {str(v.get("chave", "")): v for v in vinculos}
    por_evento = {
        str(a.get("evento_chave", "")): str(a.get("ficha_id", ""))
        for a in ancoras
        if str(a.get("ficha_id", "")) in fichas
    }
    eventos_por_ficha = defaultdict(set)
    for evt, fid in por_evento.items():
        eventos_por_ficha[fid].add(evt)
    indice = defaultdict(set)
    for pos, vinculo in enumerate(vinculos):
        cidade = normalizar_municipio(vinculo.get("municipio"))
        for numero in _os(vinculo.get("os_origem")):
            indice[(cidade, numero)].add(pos)

    fichas_exatas = {}
    for codigo, ctx in contextos.items():
        v = por_chave.get(ctx.get("chave"))
        if v and str(v.get("evento_id", "")) in fichas:
            fichas_exatas[codigo] = str(v["evento_id"])

    grupos = defaultdict(list)
    for codigo, ctx in contextos.items():
        evts = tuple(ctx.get("evento_chaves") or ())
        if len(evts) == 1 and not ctx.get("multiplos_municipios"):
            grupos[evts[0]].append(codigo)

    resultado, manual, operacoes = {}, {}, []
    processados = set()
    for chave_evento, codigos in sorted(grupos.items()):
        candidatos = {fichas_exatas[c] for c in codigos if c in fichas_exatas}
        ancora = por_evento.get(chave_evento)
        if ancora:
            candidatos.add(ancora)
        # Uma ficha vinculada a outro evento confirmado não pode ser incorporada.
        if any(eventos_por_ficha.get(fid, set()) - {chave_evento} for fid in candidatos):
            for codigo in codigos:
                if codigo in fichas_exatas:
                    resultado[codigo] = {"evento_id": fichas_exatas[codigo], "motivo": "exato"}
                    processados.add(codigo)
            continue

        antigo_por_codigo = {}
        if not candidatos:
            # Semente para cadastro legado: única ficha anterior compatível.
            todas = set()
            for codigo in codigos:
                op = _sugestoes(contextos[codigo], indice, vinculos, fichas)
                if len(op) == 1 and op[0]["distancia_m"] <= 750:
                    todas.add(op[0]["evento_id"])
                    antigo_por_codigo[codigo] = op[0]
            if len(todas) == 1:
                candidatos = todas

        if not candidatos:
            continue
        principal = ancora or min(candidatos, key=lambda x: _ordenar_ficha(x, fichas))
        if not all(normalizar_municipio(contextos[c]["municipio"])
                   == normalizar_municipio(contextos[codigos[0]]["municipio"]) for c in codigos):
            continue
        for codigo in codigos:
            ctx = contextos[codigo]
            fid_existente = fichas_exatas.get(codigo)
            antiga = antigo_por_codigo.get(codigo)
            if not fid_existente and not ancora and not any(
                c in fichas_exatas for c in codigos
            ) and not antiga:
                continue
            resultado[codigo] = {
                "evento_id": principal, "motivo": "evento",
                "evento_chave": chave_evento, "agrupados": len(codigos),
                "incorporacoes": sorted(candidatos-{principal}),
            }
            processados.add(codigo)
            # Evento já ancorado e vínculo atual correto: não gravar novamente.
            if fid_existente == principal and ancora == principal and len(candidatos) == 1:
                continue
            operacoes.append({
                "chave": ctx["chave"], "ficha_id": fid_existente or principal,
                "municipio": ctx["municipio"], "bairros": ctx.get("bairros", ""),
                "latitude": str(ctx["latitude"]), "longitude": str(ctx["longitude"]),
                "os": sorted(_os(ctx["os"])), "evento_chave": chave_evento,
                "chave_anterior": antiga["chave_anterior"] if antiga else "",
                "motivo": "evento",
            })

    # Em cada município, avaliar somente os vínculos que compartilham O.S.
    for codigo, ctx in contextos.items():
        if codigo in processados:
            continue
        if codigo in fichas_exatas:
            resultado[codigo] = {"evento_id": fichas_exatas[codigo], "motivo": "exato"}
            continue
        if len(ctx.get("evento_chaves") or ()) > 1 or ctx.get("multiplos_municipios"):
            continue
        opcoes = _sugestoes(ctx, indice, vinculos, fichas)
        if not opcoes:
            continue
        if len(opcoes) != 1 or opcoes[0]["distancia_m"] > 750:
            manual[codigo] = opcoes
            continue
        escolhido = opcoes[0]
        resultado[codigo] = {
            "evento_id": escolhido["evento_id"], "motivo": "continuidade",
        }
        operacoes.append({
            "chave": ctx["chave"], "ficha_id": escolhido["evento_id"],
            "municipio": ctx["municipio"], "bairros": ctx.get("bairros", ""),
            "latitude": str(ctx["latitude"]), "longitude": str(ctx["longitude"]),
            "os": sorted(_os(ctx["os"])), "chave_anterior": escolhido["chave_anterior"],
            "motivo": "continuidade",
        })

    # Nunca permitir a mesma ficha reatribuída por continuidade (sem evento)
    # a duas concentrações atuais distintas. Eventos podem ser multiárea.
    por_ficha_nova = defaultdict(list)
    for codigo, r in resultado.items():
        if r["motivo"] == "continuidade":
            por_ficha_nova[r["evento_id"]].append(codigo)
    for lista in por_ficha_nova.values():
        if len(lista) > 1:
            for codigo in lista:
                resultado.pop(codigo, None)
                manual[codigo] = _sugestoes(contextos[codigo], indice, vinculos, fichas)
            operacoes = [o for o in operacoes
                         if o.get("chave") not in {contextos[c]["chave"] for c in lista}]
    return resultado, manual, operacoes
