"""PRISMA — Gestão de Acessos. Somente administradores confirmados e ativos."""
import streamlit as st
from auth import exigir_acesso_modulo, verificar_autenticacao
from prisma_backend import (ACOES, MODULOS, ErroPRISMA, listar_perfis, atualizar_usuario,
    perfil_de_usuario, permissoes_usuario, permissoes_efetivas, salvar_permissoes)

st.set_page_config(page_title="PRISMA | Gestão de Acessos",page_icon="🔐",layout="wide")
exigir_acesso_modulo("gestao")
if st.session_state.get("perfil")!="admin":
    st.error("Acesso exclusivo de administradores")
    st.stop()

st.title("🔐 PRISMA — Gestão de Acessos")
st.caption("Aprovação de cadastros, bloqueio de contas e permissões individuais por módulo e ação.")
if st.button("← Voltar ao Hub"):
    st.switch_page("app.py")

try:
    usuarios=listar_perfis()
except ErroPRISMA as erro:
    st.error(str(erro));st.stop()

ativos=sum(u["status"]=="ativo" for u in usuarios)
pendentes=sum(u["status"]=="pendente" for u in usuarios)
bloqueados=sum(u["status"]=="bloqueado" for u in usuarios)
a,b,c=st.columns(3)
a.metric("Contas ativas",ativos)
b.metric("Aguardando aprovação",pendentes)
c.metric("Bloqueadas",bloqueados)

if not usuarios:
    st.info("Ainda não existem cadastros. Confirme o primeiro e-mail administrativo.")
    st.stop()

filtro=st.selectbox("Filtrar contas",["Todos","Pendentes","Ativos","Bloqueados"],index=1 if pendentes else 0)
filtrados=[u for u in usuarios if filtro=="Todos" or u["status"]=={"Pendentes":"pendente","Ativos":"ativo","Bloqueados":"bloqueado"}.get(filtro)]
st.dataframe([{"Nome":u["nome"],"E-mail":u["email"],"Perfil":u["perfil"],"Situação":u["status"]} for u in filtrados],
             use_container_width=True,hide_index=True)

st.subheader("Configurar usuário")
nomes={f"{u['nome']} — {u['email']} ({u['status']})":u for u in usuarios}
selecionado=st.selectbox("Selecione a conta",list(nomes.keys()))
u=nomes[selecionado]
ator=st.session_state["prisma_usuario_id"]

with st.form("alterar_usuario"):
    col1,col2=st.columns(2)
    with col1: perfil=st.selectbox("Perfil base",["operador","usuario","admin"],
                                       index=["operador","usuario","admin"].index(u["perfil"]))
    with col2: status=st.selectbox("Situação",["pendente","ativo","bloqueado"],
                                      index=["pendente","ativo","bloqueado"].index(u["status"]))
    salvar=st.form_submit_button("Salvar situação e perfil",type="primary")
if salvar:
    try:
        atualizar_usuario(ator,u["id"],perfil,status)
        st.success("Perfil atualizado.")
        st.rerun()
    except ErroPRISMA as erro:st.error(str(erro))

st.subheader("Permissões por ferramenta")
st.caption("As opções abaixo substituem as permissões padrão do perfil. O módulo só permite outras ações quando Visualizar está liberado.")
if u["perfil"]=="admin":
    st.info("O perfil Administrador tem acesso integral. Para personalizar, utilize o perfil Usuário ou Consulta.")
else:
    try:
        matriz=permissoes_efetivas(u["perfil"],permissoes_usuario(u["id"]))
    except ErroPRISMA as erro:st.error(str(erro));st.stop()
    with st.form("alterar_permissoes"):
        alterar={}
        for mod,nome in MODULOS.items():
            if mod=="gestao":continue
            st.markdown(f"**{nome}**")
            colunas=st.columns(len(ACOES))
            for j,acao in enumerate(ACOES):
                with colunas[j]:
                    chave=f"perm_{u['id']}_{mod}_{acao}"
                    alterar[(mod,acao)]=st.checkbox(acao.title(),value=matriz.get((mod,acao),False),key=chave)
            st.divider()
        salvar=st.form_submit_button("Salvar permissões individuais",type="primary",use_container_width=True)
    if salvar:
        try:
            salvar_permissoes(ator,u["id"],alterar)
            st.success("Permissões atualizadas. A alteração será aplicada na próxima revalidação da sessão do usuário.")
            st.rerun()
        except ErroPRISMA as erro:st.error(str(erro))
