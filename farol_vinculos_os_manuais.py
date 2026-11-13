"""Vínculos manuais O.S. -> evento. Fonte compartilhada pelos módulos 3 e 7.

Não altera a associação automática; persiste exceções confirmadas pelo analista.
A gravação utiliza RPC transacional e service_role somente no backend.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

import pandas as pd
import streamlit as st

from farol_acomp_supabase_backend import _request, _autor, fonte_supabase
from farol_mesclagem_operacional import chave_evento

VALIDO_OS = re.compile(r"^[A-Z0-9./_-]{1,100}$")


def normalizar_os(numero):
    texto = str(numero if numero is not None else "").strip().upper()
    return texto if VALIDO_OS.fullmatch(texto) and texto not in {"NAN", "NONE"} else ""


def normalizar_municipio(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", texto)


@st.cache_data(ttl=15, max_entries=80, show_spinner=False)
def _consulta_lote(numeros: tuple[str, ...]) -> list[dict]:
    """Consulta apenas O.S. do recorte para não percorrer a tabela inteira."""
    if not numeros or not fonte_supabase():
        return []
    resultados = []
    for inicio in range(0, len(numeros), 120):
        lote = numeros[inicio:inicio + 120]
        offset = 0
        while True:
            itens = _request(
                "GET", "/rest/v1/farol_vinculos_os_manuais",
                params={
                    "select": "numero_os,evento_chave,municipio,protocolo,"
                              "servico,codigo_ativo,inicio,evento_rotulo,"
                              "justificativa,criado_em",
                    "numero_os": "in.(" + ",".join(lote) + ")",
                    "order": "criado_em.asc",
                    "limit": "1000",
                    "offset": str(offset),
                },
            )
            if not isinstance(itens, list):
                raise RuntimeError("O Supabase não retornou os vínculos manuais.")
            resultados.extend(itens)
            if len(itens) < 1000:
                break
            offset += len(itens)
    return resultados


def listar_vinculos_os_manuais(numeros):
    validos = tuple(sorted({
        numero for valor in numeros if (numero := normalizar_os(valor))
    }))
    return _consulta_lote(validos)


def limpar_cache_vinculos_manuais():
    _consulta_lote.clear()


def _rotulo_evento(evento):
    protocolo = str(evento.get("Protocolo", "") or "").strip()
    if protocolo and protocolo.lower() not in {"nan", "none", "-"}:
        return protocolo
    return (str(evento.get("Código do Ativo", "") or "").strip()
            or str(evento.get("Serviço", "") or "").strip()
            or "Evento sem protocolo")


def vincular_os_manual(evento, numeros_os, justificativa=""):
    """Associa ordens a um evento com auditoria, sem mudar dados de origem."""
    autor = _autor("editar")
    numeros = sorted({n for x in numeros_os if (n := normalizar_os(x))})
    if not 1 <= len(numeros) <= 200:
        raise ValueError("Selecione entre 1 e 200 ordens de serviço.")
    cidade = str(evento.get("Cidade", "") or "").strip()
    inicio = str(evento.get("Início", "") or "").strip()
    if not cidade or not inicio:
        raise ValueError("Selecione um evento com município e horário de início.")
    chave = chave_evento(evento)
    rotulo = _rotulo_evento(evento)
    resultado = _request(
        "POST", "/rest/v1/rpc/farol_adicionar_vinculos_os_manuais_tx",
        dados={
            "p_evento_chave": chave,
            "p_municipio": cidade,
            "p_protocolo": str(evento.get("Protocolo", "") or "").strip(),
            "p_servico": str(evento.get("Serviço", "") or "").strip(),
            "p_codigo_ativo": str(evento.get("Código do Ativo", "") or "").strip(),
            "p_inicio": inicio,
            "p_evento_rotulo": rotulo,
            "p_numeros_os": numeros,
            "p_justificativa": str(justificativa or "").strip()[:1200],
            "p_autor": autor,
        },
    )
    if not isinstance(resultado, int):
        raise RuntimeError("O Supabase não confirmou a vinculação manual.")
    limpar_cache_vinculos_manuais()
    return resultado


def desfazer_vinculo_os_manual(evento_chave, numeros_os):
    autor = _autor("editar")
    numeros = sorted({n for x in numeros_os if (n := normalizar_os(x))})
    if not 1 <= len(numeros) <= 200 or not re.fullmatch(r"[a-f0-9]{32}", str(evento_chave)):
        raise ValueError("Seleção inválida para desfazer o vínculo manual.")
    resultado = _request(
        "POST", "/rest/v1/rpc/farol_remover_vinculos_os_manuais_tx",
        dados={
            "p_evento_chave": evento_chave,
            "p_numeros_os": numeros,
            "p_autor": autor,
        },
    )
    if not isinstance(resultado, int):
        raise RuntimeError("O Supabase não confirmou a remoção.")
    limpar_cache_vinculos_manuais()
    return resultado



def eventos_manuais_do_grupo(registros, grupo_os):
    """Retorna metadados de eventos ligados manualmente a O.S. deste grupo.

    Usado apenas na exibição dos relatórios; NÃO alimenta o cálculo
    territorial/temporal da associação automática.
    """
    if isinstance(grupo_os, pd.DataFrame):
        linhas = grupo_os.to_dict("records")
    else:
        linhas = list(grupo_os)
    pares = {
        (normalizar_os(r.get("Nº da O.S")), normalizar_municipio(r.get("Cidade")))
        for r in linhas
    }
    encontrados = {}
    for r in registros:
        if (normalizar_os(r.get("numero_os")),
                normalizar_municipio(r.get("municipio"))) not in pares:
            continue
        chave = str(r.get("evento_chave") or "")
        if not chave or chave in encontrados:
            continue
        encontrados[chave] = {
            "Cidade": r.get("municipio", ""),
            "Protocolo": r.get("protocolo", ""),
            "Serviço": r.get("servico", ""),
            "Código do Ativo": r.get("codigo_ativo", ""),
            "Início": r.get("inicio", ""),
            "Término Real": "",
            "Status": "Vínculo manual",
            "Bairros coincidentes": "Associação manual do analista",
            "Áreas Impactadas": "",
        }
    return list(encontrados.values())

def aplicar_vinculos_manuais(vinculadas, nao_vinculadas, registros,
                            coluna_evento="Evento(s) vinculado(s)",
                            coluna_criterio="Critério do vínculo"):
    """Reclassifica apenas ordens explicitamente confirmadas.

    Preserva qualquer associação automática já existente; não duplica
    ordens nem rótulos e conserva tipo de saída (DataFrame ou list[dict]).
    """
    como_df = isinstance(vinculadas, pd.DataFrame)
    cols = (list(vinculadas.columns) if como_df else None)
    vinculadas_itens = (vinculadas.to_dict("records") if como_df else list(vinculadas))
    nao_itens = (nao_vinculadas.to_dict("records") if como_df else list(nao_vinculadas))
    por_os = defaultdict(list)
    for registro in registros:
        numero = normalizar_os(registro.get("numero_os"))
        if numero:
            por_os[numero].append(registro)

    def atualizar(linha):
        linha = dict(linha)
        manuais = [
            registro for registro in por_os.get(
                normalizar_os(linha.get("Nº da O.S")), []
            )
            if normalizar_municipio(registro.get("municipio")) ==
               normalizar_municipio(linha.get("Cidade"))
        ]
        if not manuais:
            return linha, False
        separador = "; " if coluna_evento == "Eventos vinculados" else ", "
        existentes = [x.strip() for x in str(linha.get(coluna_evento, "") or "").split(separador) if x.strip()]
        for m in manuais:
            nome = (str(m.get("protocolo") or "").strip()
                    or str(m.get("evento_rotulo") or "").strip()
                    or str(m.get("servico") or "").strip()
                    or "Evento manual")
            if nome not in existentes:
                existentes.append(nome)
        linha[coluna_evento] = separador.join(existentes)
        if coluna_criterio:
            anterior = str(linha.get(coluna_criterio, "") or "").strip()
            if "Manual (analista)" not in anterior:
                linha[coluna_criterio] = (anterior + ", " if anterior else "") + "Manual (analista)"
        return linha, True

    saida_vinculadas = [atualizar(l)[0] for l in vinculadas_itens]
    saida_nao = []
    for linha in nao_itens:
        nova, eh_manual = atualizar(linha)
        if eh_manual:
            saida_vinculadas.append(nova)
        else:
            saida_nao.append(nova)
    if como_df:
        return (pd.DataFrame(saida_vinculadas).reindex(columns=cols),
                pd.DataFrame(saida_nao).reindex(columns=cols))
    return saida_vinculadas, saida_nao
