"""Fechamentos/reparos de rede do Módulo 9 — base separada dos poços.

Apenas o servidor Streamlit acessa a tabela via prisma_backend._tabela.
Não altera ativos, fichas ou a classificação dos status dos poços.
"""
from __future__ import annotations

from datetime import date
import math
import re
import uuid

import streamlit as st
from auth import pode_executar_acao, tem_acesso_modulo
from prisma_backend import _tabela, ErroPRISMA


STATUS_FECHAMENTO = {
    "pendente": ("Pendente", "#EF4444", "🔴"),
    "em_andamento": ("Em andamento", "#FACC15", "🟡"),
    "encerrado": ("Encerrado", "#16A34A", "🟢"),
    "cancelado": ("Cancelado", "#111111", "⚫"),
}
# Status anterior mantido apenas para leitura de registros antigos.
STATUS_LEGADO = {"concluido": STATUS_FECHAMENTO["encerrado"]}
LIMITE_MARCADORES_FECHAMENTOS = 450
CONFIRMACAO_EXCLUSAO_FECHAMENTOS = "EXCLUIR TODOS OS FECHAMENTOS"


def _leitura():
    if not tem_acesso_modulo("9"):
        raise ErroPRISMA("Acesso aos fechamentos não autorizado.")


def _autor(acao):
    if not pode_executar_acao("9", acao):
        raise ErroPRISMA("Sem permissão para alterar fechamentos de rede.")
    valor = st.session_state.get("prisma_usuario_id")
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, TypeError) as exc:
        raise ErroPRISMA("Sessão sem identificação do usuário.") from exc


def _uuid(valor):
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, TypeError) as exc:
        raise ErroPRISMA("Identificador de fechamento inválido.") from exc


def _campos(dados: dict) -> dict:
    """Campos exclusivamente solicitados para a ficha + município do filtro."""
    resultado = {}
    limites = {
        "lider": 160, "equipe": 160, "matricula": 80, "os": 80,
        "bairro": 160, "municipio": 100, "os_abertura": 80,
    }
    for campo, limite in limites.items():
        valor = str(dados.get(campo) or "").strip()
        if len(valor) > limite:
            raise ErroPRISMA(f"{campo.title()}: limite de {limite} caracteres.")
        resultado[campo] = valor or None
    if not resultado["bairro"] or not resultado["municipio"]:
        raise ErroPRISMA("Informe bairro e selecione o município do fechamento.")
    status = str(dados.get("status") or "").strip()
    if status not in STATUS_FECHAMENTO:
        raise ErroPRISMA("Selecione um status válido para o fechamento.")
    resultado["status"] = status
    data = dados.get("data")
    try:
        data = date.fromisoformat(data.isoformat() if isinstance(data, date)
                                   else str(data)[:10])
    except (ValueError, TypeError) as exc:
        raise ErroPRISMA("Informe uma data válida para o fechamento.") from exc
    resultado["data"] = data.isoformat()
    data_abertura = dados.get("data_abertura")
    if data_abertura in ("", None):
        resultado["data_abertura"] = None
    else:
        try:
            data_abertura = date.fromisoformat(
                data_abertura.isoformat() if isinstance(data_abertura, date)
                else str(data_abertura)[:10]
            )
        except (ValueError, TypeError) as exc:
            raise ErroPRISMA("Informe uma data de abertura válida.") from exc
        resultado["data_abertura"] = data_abertura.isoformat()
    if status == "encerrado" and (
        not resultado["os_abertura"] or not resultado["data_abertura"]
    ):
        raise ErroPRISMA(
            "Para marcar o fechamento como Encerrado, preencha a "
            "O.S. de abertura e a data da abertura."
        )
    return resultado


def _coordenadas(latitude, longitude):
    """Aceita graus decimais com ponto ou vírgula, sem coordenadas fictícias."""
    try:
        lat = float(str(latitude).strip().replace(",", "."))
        lon = float(str(longitude).strip().replace(",", "."))
    except (ValueError, TypeError) as exc:
        raise ErroPRISMA(
            "Informe latitude e longitude em graus decimais para posicionar o 🚧."
        ) from exc
    if not (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErroPRISMA(
            "Coordenadas do fechamento inválidas: latitude de -90 a 90 e "
            "longitude de -180 a 180."
        )
    return lat, lon


def municipios_fechamentos():
    _leitura()
    nomes = set()
    offset = 0
    while True:
        lote = _tabela("prisma_fechamentos_rede", params={
            "select": "municipio", "order": "municipio.asc",
            "offset": str(offset), "limit": "1000",
        })
        nomes.update(str(reg.get("municipio") or "").strip() for reg in lote)
        if len(lote) < 1000:
            break
        offset += 1000
    return sorted((n for n in nomes if n), key=str.casefold)


def _filtros_continuidade(status=None, data_referencia=None,
                         incluir_arquivados_do_dia=False) -> dict:
    """Filtro operacional sem cópia diária nem alteração dos cadastros.

    Na data selecionada entram ocorrências ainda abertas, iniciadas até esse
    dia. Encerradas/canceladas aparecem somente quando o status é pedido
    explicitamente ou na revisão opcional do relatório (com data original).
    Sem data, a consulta é histórica: recupera todos os registros.
    O status é o estado ATUAL do cadastro, não um snapshot histórico.
    """
    selecionados = (
        [status] if isinstance(status, str) and status != "Todos"
        else list(status or []) if not isinstance(status, str) else []
    )
    if any(codigo not in STATUS_FECHAMENTO for codigo in selecionados):
        raise ErroPRISMA("Um dos status de fechamento selecionados é inválido.")

    abertos = {"pendente", "em_andamento"}
    arquivados = {"encerrado", "concluido", "cancelado"}
    codigos = set(selecionados)
    if "encerrado" in codigos:
        codigos.add("concluido")  # Leitura de encerramentos legados.

    def condicao(campo, valores):
        valores = sorted(valores)
        return ("eq." + valores[0]) if len(valores) == 1 else (
            "in.(" + ",".join(valores) + ")"
        )

    if data_referencia is None:
        return {"status": condicao("status", codigos)} if codigos else {}
    try:
        dia = date.fromisoformat(
            data_referencia.isoformat() if isinstance(data_referencia, date)
            else str(data_referencia)[:10]
        ).isoformat()
    except (ValueError, TypeError) as exc:
        raise ErroPRISMA("Selecione uma data válida para filtrar fechamentos.") from exc

    # Sem seleção, mapa diário exibe apenas abertos; relatório também mostra
    # os encerrados/cancelados iniciados no dia (desmarcados para inclusão).
    atuais = (codigos & abertos) if selecionados else set(abertos)
    encerrados = (codigos & arquivados) if selecionados else (
        set(arquivados) if incluir_arquivados_do_dia else set()
    )

    if atuais and encerrados:
        # 'and' em paralelo ao 'or' da pesquisa textual preserva ambos os
        # filtros do PostgREST e aplica as condições antes do limite do mapa.
        regra = (
            "or("
            f"and(status.{condicao('status', atuais)},data.lte.{dia}),"
            f"and(status.{condicao('status', encerrados)},data.eq.{dia})"
            ")"
        )
        return {"and": "(" + regra + ")"}
    if atuais:
        return {"status": condicao("status", atuais), "data": "lte." + dia}
    if encerrados:
        return {"status": condicao("status", encerrados), "data": "eq." + dia}
    return {"status": "eq.__nenhum_status_valido__"}


def listar_fechamentos(municipio=None, bairro="", pesquisa="", status=None,
                       data_referencia=None):
    _leitura()
    parametros = {
        "select": "*",
        "order": "data.desc,criado_em.desc",
        "limit": str(LIMITE_MARCADORES_FECHAMENTOS + 1),
    }
    if municipio and municipio != "Todos":
        parametros["municipio"] = "ilike." + str(municipio).strip()[:100]
    parametros.update(_filtros_continuidade(
        status=status, data_referencia=data_referencia
    ))
    bairro = re.sub(r"[^\w\s\-]", "", bairro or "", flags=re.UNICODE).strip()[:80]
    if bairro:
        parametros["bairro"] = "ilike.*" + bairro + "*"
    termo = re.sub(r"[^\w\s\-]", "", pesquisa or "", flags=re.UNICODE).strip()[:80]
    if termo:
        parametros["or"] = "(" + ",".join(
            f"{campo}.ilike.*{termo}*"
            for campo in ("lider", "equipe", "matricula", "os", "os_abertura", "bairro")
        ) + ")"
    return _tabela("prisma_fechamentos_rede", params=parametros)


def obter_fechamento(identificador):
    _leitura()
    retorno = _tabela("prisma_fechamentos_rede", params={
        "select": "*", "id": "eq." + _uuid(identificador), "limit": "1",
    })
    return retorno[0] if retorno else None


def criar_fechamento(dados, latitude, longitude):
    autor = _autor("criar")
    lat, lon = _coordenadas(latitude, longitude)
    payload = {
        **_campos(dados), "latitude": lat, "longitude": lon,
        "criado_por": autor, "atualizado_por": autor,
    }
    resultado = _tabela("prisma_fechamentos_rede", method="POST",
                        payload=payload, prefer="return=representation")
    if not resultado:
        raise ErroPRISMA("Não foi possível confirmar o cadastro do fechamento.")
    return resultado[0]


def editar_fechamento(identificador, dados, versao):
    autor = _autor("editar")
    if not versao:
        raise ErroPRISMA("Reabra a ficha para obter a versão atual do fechamento.")
    payload = {**_campos(dados), "atualizado_por": autor}
    resultado = _tabela("prisma_fechamentos_rede", method="PATCH", params={
        "id": "eq." + _uuid(identificador),
        "atualizado_em": "eq." + str(versao),
    }, payload=payload, prefer="return=representation")
    if not resultado:
        raise ErroPRISMA(
            "O fechamento foi atualizado por outro usuário. Reabra a ficha."
        )
    return resultado[0]


def listar_fechamentos_relatorio(municipio: str, data_referencia=None):
    """Seleciona abertos em continuidade e arquivados do dia para revisão."""
    _leitura()
    municipio = str(municipio or "").strip()
    if not municipio or len(municipio) > 100:
        raise ErroPRISMA("Selecione um município válido para o relatório.")
    filtros = {
        "municipio": "ilike." + municipio,
        **_filtros_continuidade(
            data_referencia=data_referencia, incluir_arquivados_do_dia=True
        ),
    }
    registros = []
    offset = 0
    while True:
        lote = _tabela("prisma_fechamentos_rede", params={
            "select": "*", **filtros,
            "order": "data.desc,criado_em.desc",
            "limit": "1000", "offset": str(offset),
        })
        registros.extend(lote)
        if len(lote) < 1000:
            return registros
        offset += 1000
        if offset >= 10000:
            raise ErroPRISMA("Há mais de 10.000 fechamentos no município.")



def excluir_todos_fechamentos(confirmacao: str) -> dict:
    """Exclui apenas registros de fechamentos, com auditoria atômica no banco.

    Nunca altera `prisma_ativos` nem `prisma_ativos_historico`. Exige
    autenticação revalidada, perfil admin, permissão excluir, frase exata;
    a RPC confere novamente o administrador ativo antes do DELETE.
    """
    from auth import verificar_autenticacao

    if not verificar_autenticacao(forcar=True):
        raise ErroPRISMA("Sessão inválida ou expirada. Entre novamente.")
    if st.session_state.get("perfil") != "admin":
        raise ErroPRISMA(
            "A exclusão dos fechamentos está disponível somente para administradores."
        )
    if not pode_executar_acao("9", "excluir"):
        raise ErroPRISMA("Sem permissão para excluir os fechamentos de rede.")
    if confirmacao != CONFIRMACAO_EXCLUSAO_FECHAMENTOS:
        raise ErroPRISMA("Digite a frase de confirmação dos fechamentos exatamente.")

    autor = _autor("excluir")
    resposta = _tabela(
        "rpc/prisma_fechamentos_resetar_registros_tx",
        method="POST",
        payload={"p_autor": autor, "p_confirmacao": confirmacao},
    )
    if (not isinstance(resposta, dict)
            or isinstance(resposta.get("fechamentos_excluidos"), bool)
            or not isinstance(resposta.get("fechamentos_excluidos"), int)
            or resposta["fechamentos_excluidos"] < 0):
        raise ErroPRISMA(
            "O servidor não confirmou a exclusão dos fechamentos. "
            "Consulte a auditoria antes de tentar novamente."
        )
    return resposta
