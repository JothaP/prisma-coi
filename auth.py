"""PRISMA — autorização centralizada, sem senhas compartilhadas nem cookies HMAC.

Sessões vivem somente no estado do servidor Streamlit. Ao atualizar o navegador
pode ser necessário entrar novamente. Um acesso revogado deixa de funcionar
na próxima consulta de perfil (máx. 30 segundos).
"""
from datetime import datetime, timedelta, timezone
import streamlit as st
from prisma_backend import (ErroPRISMA, MODULOS, permissoes_efetivas, permissoes_usuario,
                           perfil_de_usuario, bootstrap_admin, renovar, usuario_autenticado)

_REVALIDAR_SEGUNDOS=25

def _limpar():
    for k in ("prisma_tokens","prisma_ultima_validacao","prisma_usuario_id","prisma_permissoes"):
        st.session_state.pop(k,None)
    for k in ("autenticado","perfil","modulos","usuario_logado"):
        st.session_state.pop(k,None)

def iniciar_sessao(tokens):
    _limpar()
    if not isinstance(tokens,dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
        return False
    st.session_state["prisma_tokens"]=tokens
    return verificar_autenticacao(forcar=True)

def verificar_autenticacao(forcar=False):
    tokens=st.session_state.get("prisma_tokens")
    if not isinstance(tokens,dict):return False
    agora=datetime.now(timezone.utc)
    ult=st.session_state.get("prisma_ultima_validacao")
    if not forcar and st.session_state.get("autenticado") and isinstance(ult,datetime) and (agora-ult).total_seconds()<_REVALIDAR_SEGUNDOS:
        return True
    try:
        valido_ate=float(tokens.get("prisma_expira_em",0))
        if not valido_ate:
            valido_ate=agora.timestamp()+float(tokens.get("expires_in",300))
            tokens["prisma_expira_em"]=valido_ate
        if agora.timestamp()>valido_ate-75:
            novo=renovar(tokens["refresh_token"])
            if not novo or not novo.get("access_token"):raise ErroPRISMA("Sessão expirada")
            novo["prisma_expira_em"]=agora.timestamp()+float(novo.get("expires_in",300))
            st.session_state["prisma_tokens"]=tokens=novo
        user=usuario_autenticado(tokens["access_token"])
        if not user or not user.get("id") or not user.get("email_confirmed_at"):
            raise ErroPRISMA("E-mail não confirmado")
        bootstrap_admin(user)
        perfil=perfil_de_usuario(user["id"])
        if not perfil or perfil.get("status")!="ativo":raise ErroPRISMA("Acesso pendente ou bloqueado")
        if str(perfil.get("email","")).lower()!=str(user.get("email","")).lower():
            raise ErroPRISMA("Conta divergente")
        permissoes=permissoes_efetivas(perfil["perfil"],permissoes_usuario(user["id"]))
        st.session_state.update({"autenticado":True,"usuario_logado":perfil.get("nome") or user["email"],
            "prisma_usuario_id":user["id"],"perfil":perfil["perfil"],
            "modulos":[m for m in MODULOS if permissoes.get((m,"visualizar"),False)],
            "prisma_permissoes":permissoes,"prisma_ultima_validacao":agora})
        return True
    except (ErroPRISMA,KeyError,TypeError,ValueError):
        _limpar()
        return False

def tem_acesso_modulo(modulo):
    return bool(verificar_autenticacao() and st.session_state.get("prisma_permissoes",{}).get((str(modulo),"visualizar"),False))

def pode_executar_acao(modulo,acao):
    if acao not in ("visualizar","criar","importar","editar","excluir","exportar","relatorios"):return False
    return bool(verificar_autenticacao() and st.session_state.get("prisma_permissoes",{}).get((str(modulo),str(acao)),False)
                and st.session_state.get("prisma_permissoes",{}).get((str(modulo),"visualizar"),False))

def exigir_acesso_modulo(modulo):
    if not verificar_autenticacao():
        st.error("Sessão não iniciada ou expirada. Entre pelo PRISMA.")
        if st.button("Ir para login",key="prisma_ir_login_"+str(modulo)):
            st.switch_page("app.py")
        st.stop()
    if not tem_acesso_modulo(modulo):
        st.error("Você não possui autorização para acessar este módulo.")
        st.stop()

def exigir_acao(modulo,acao):
    if not pode_executar_acao(modulo,acao):
        st.error("Seu usuário não possui permissão para executar esta ação.")
        st.stop()

def obter_modulos_perfil(perfil):
    # Compatibilidade com chamadas legadas: preferir as permissões da sessão.
    if perfil!=st.session_state.get("perfil"):return []
    return list(st.session_state.get("modulos",[]))

def obter_modulos_usuario():
    return list(st.session_state.get("modulos",[])) if verificar_autenticacao() else []

def fazer_logout():
    _limpar()
