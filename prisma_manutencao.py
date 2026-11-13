"""PRISMA — controle administrativo de janelas de manutenção.

O bloqueio é aplicado em auth.exigir_acesso_modulo, antes das operações dos
módulos. Usa o banco como fonte única; não depende de deploy nem de cron.
A conta admin autenticada conserva acesso para administrar/validar as páginas.
As rotinas de importação GitHub Actions não consultam este bloqueio.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from prisma_backend import _request, ErroPRISMA

FUSO = ZoneInfo("America/Fortaleza")
AVISO_ANTECEDENCIA = timedelta(minutes=30)
ATUALIZACAO_SEGUNDOS = 15
CAMPOS = "id,escopo,modulo,inicio,fim,motivo,mensagem,criado_em,criado_por,cancelado_em"
MODULOS = {
    "1": "Baixa Pressão", "2": "Mapeamento de Pressão",
    "3": "Farol Operacional", "4": "Ferramentas Operacionais",
    "5": "Cadastros e Consultas", "6": "Ferramentas Adicionais",
    "7": "Relatórios Integrados", "8": "Consulta Operacional",
    "9": "Gestão de Ativos", "teste": "Ambiente de Teste",
}


def _horario(valor):
    if not valor:
        return None
    horario = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if horario.tzinfo is None:
        raise ErroPRISMA("Período de manutenção sem fuso horário.")
    return horario.astimezone(timezone.utc)


def _visiveis_para_modulo(registros, modulo):
    modulo = str(modulo or "")
    return [
        r for r in registros if r.get("escopo") == "global" or
        (r.get("escopo") == "modulo" and str(r.get("modulo")) == modulo)
    ]


@st.cache_data(ttl=8, max_entries=2, show_spinner=False)
def consultar_manutencoes():
    """Retorna futuras e ativas; SQL/relógio do servidor decidem a janela."""
    agora = datetime.now(timezone.utc).isoformat()
    itens = _request(
        "GET", "/rest/v1/prisma_manutencoes", admin=True,
        params={
            "select": CAMPOS,
            "cancelado_em": "is.null",
            "or": f"(fim.is.null,fim.gt.{agora})",
            "order": "inicio.asc", "limit": "500",
        },
    )
    if not isinstance(itens, list):
        raise ErroPRISMA("Resposta de manutenção inválida.")
    return itens


def situacao(modulo=None, *, agora=None):
    """Bloqueio atual e próximo aviso para o módulo (None = hub/global)."""
    agora = agora or datetime.now(timezone.utc)
    registros = _visiveis_para_modulo(consultar_manutencoes(), modulo)
    vigentes = []
    futuras = []
    for r in registros:
        inicio, fim = _horario(r.get("inicio")), _horario(r.get("fim"))
        if not inicio:
            continue
        if inicio <= agora and (fim is None or agora < fim):
            vigentes.append(r)
        elif agora < inicio <= agora + AVISO_ANTECEDENCIA:
            futuras.append(r)
    # Global tem prioridade caso haja manutenção global e específica.
    vigentes.sort(key=lambda r: (r.get("escopo") != "global", r.get("inicio", "")))
    futuras.sort(key=lambda r: r.get("inicio", ""))
    return (vigentes[0] if vigentes else None,
            futuras[0] if futuras else None)


def texto_fim(registro):
    fim = _horario(registro.get("fim"))
    return (fim.astimezone(FUSO).strftime("%d/%m/%Y às %H:%M")
            if fim else "sem horário previsto — liberação pelo administrador")


def mensagem_publica(registro):
    texto = str(registro.get("mensagem") or "").strip()
    return texto or "A plataforma está temporariamente indisponível para manutenção técnica."


def exibir_tela_manutencao(registro, modulo=None):
    """Renderiza bloqueio, sem expor informações administrativas."""
    st.title("🛠️ PRISMA — Manutenção programada")
    st.warning(mensagem_publica(registro))
    if registro.get("escopo") == "modulo":
        st.info(f"Módulo temporariamente indisponível: {MODULOS.get(str(registro.get('modulo')), 'Operacional')}.")
    else:
        st.info("Os módulos operacionais estão temporariamente indisponíveis.")
    st.markdown(f"**Previsão de restabelecimento:** {texto_fim(registro)} (horário de Teresina).")
    st.caption("Os registros permanecem preservados. Contate a administração em caso de necessidade.")
    monitorar_liberacao(modulo)
    if modulo is not None and st.button("🏠 Voltar ao Hub Central", key="prisma_voltar_manutencao"):
        st.switch_page("app.py")
    st.stop()


def verificar_manutencao(modulo=None):
    """Impede leitura e escrita em módulos suspensos; admin pode testar."""
    if str(st.session_state.get("perfil") or "").strip().lower() == "admin":
        return
    try:
        bloqueio, aviso = situacao(modulo)
    except ErroPRISMA:
        st.error("Não foi possível verificar a disponibilidade da plataforma. "
                 "Para proteger a operação, o acesso foi temporariamente suspenso.")
        st.stop()
    if bloqueio:
        exibir_tela_manutencao(bloqueio, modulo=modulo)
    if aviso:
        area = "toda a plataforma" if aviso["escopo"] == "global" else MODULOS.get(str(aviso["modulo"]), "este módulo")
        st.info(
            f"🛠️ Manutenção programada para {area} a partir de "
            f"{_horario(aviso['inicio']).astimezone(FUSO):%d/%m/%Y às %H:%M}. "
            f"Previsão de retorno: {texto_fim(aviso)}."
        )


@st.fragment(run_every=f"{ATUALIZACAO_SEGUNDOS}s")
def monitorar_liberacao(modulo=None):
    """Permite restabelecimento automático da página bloqueada após o prazo."""
    try:
        ativo, _ = situacao(modulo)
    except ErroPRISMA:
        return
    if not ativo:
        st.rerun(scope="app")


@st.fragment(run_every=f"{ATUALIZACAO_SEGUNDOS}s")
def monitorar_manutencao(modulo=None):
    """Enquanto uma aba permanece aberta, detecta bloqueios e restaurações."""
    if str(st.session_state.get("perfil") or "").strip().lower() == "admin":
        return
    try:
        ativo, aviso = situacao(modulo)
    except ErroPRISMA:
        # Força passagem pelo guard principal, que apresenta o erro seguro.
        st.rerun(scope="app")
        return
    if ativo:
        st.rerun(scope="app")
    if aviso:
        # Atualiza uma única vez quando a janela entrar nos 30 minutos
        # anteriores ao início, sem fazer reruns infinitos.
        chave = "prisma_aviso_manutencao_" + str(modulo)
        identificador = str(aviso.get("id"))
        if st.session_state.get(chave) != identificador:
            st.session_state[chave] = identificador
            st.rerun(scope="app")


def _autor_administrador():
    from auth import verificar_autenticacao  # evita dependência circular
    if not verificar_autenticacao(forcar=True):
        raise ErroPRISMA("Sessão expirada. Entre novamente.")
    if st.session_state.get("perfil") != "admin":
        raise ErroPRISMA("Somente administradores podem definir manutenções.")
    autor = str(st.session_state.get("prisma_usuario_id") or "").strip()
    if not autor:
        raise ErroPRISMA("Administrador não identificado.")
    return autor


def agendar(escopo, modulo, inicio, fim, motivo, mensagem=""):
    autor = _autor_administrador()
    if not isinstance(inicio, datetime) or inicio.tzinfo is None:
        raise ValueError("Informe início com fuso horário.")
    if fim is not None and (not isinstance(fim, datetime) or fim.tzinfo is None
                             or fim <= inicio):
        raise ValueError("O término deve ocorrer após o início.")
    retorno = _request(
        "POST", "/rest/v1/rpc/prisma_agendar_manutencao_tx",
        admin=True,
        payload={
            "p_escopo": escopo,
            "p_modulo": modulo if escopo == "modulo" else None,
            "p_inicio": inicio.isoformat(),
            "p_fim": fim.isoformat() if fim is not None else None,
            "p_motivo": motivo,
            "p_mensagem": mensagem,
            "p_autor": autor,
        },
    )
    if not isinstance(retorno, int):
        raise ErroPRISMA("O Supabase não confirmou o agendamento.")
    consultar_manutencoes.clear()
    return retorno


def cancelar(identificador):
    autor = _autor_administrador()
    retorno = _request(
        "POST", "/rest/v1/rpc/prisma_cancelar_manutencao_tx",
        admin=True, payload={"p_id": int(identificador), "p_autor": autor},
    )
    if retorno is not True:
        raise ErroPRISMA("O Supabase não confirmou a liberação.")
    consultar_manutencoes.clear()


def historico():
    _autor_administrador()
    registros = _request(
        "GET", "/rest/v1/prisma_manutencoes",
        admin=True,
        params={"select": CAMPOS, "order": "id.desc", "limit": "200"},
    )
    if not isinstance(registros, list):
        raise ErroPRISMA("Não foi possível carregar o histórico das manutenções.")
    return registros


def status_historico(registro, agora=None):
    agora = agora or datetime.now(timezone.utc)
    if registro.get("cancelado_em"):
        return "Encerrada antecipadamente / cancelada"
    inicio, fim = _horario(registro["inicio"]), _horario(registro.get("fim"))
    if agora < inicio:
        return "Agendada"
    if fim is None or agora < fim:
        return "Em andamento"
    return "Concluída"


def aviso_login():
    """Informa a indisponibilidade sem impedir que um admin faça login."""
    try:
        ativa, proxima = situacao(None)
    except ErroPRISMA:
        return
    if ativa:
        st.warning(
            "🛠️ PRISMA em manutenção: " + mensagem_publica(ativa)
            + " Retorno: " + texto_fim(ativa) + ". "
              "O acesso técnico de administradores permanece disponível."
        )
    elif proxima:
        st.info(
            "Manutenção programada para toda a plataforma: "
            + _horario(proxima["inicio"]).astimezone(FUSO).strftime("%d/%m/%Y às %H:%M")
        )
