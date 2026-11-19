"""PRISMA — Gestão de Acessos. Somente administradores confirmados e ativos."""
import streamlit as st
from auth import exigir_acesso_modulo, verificar_autenticacao
from prisma_backend import (ACOES, MODULOS, ErroPRISMA, listar_perfis, atualizar_usuario,
    perfil_de_usuario, permissoes_usuario, permissoes_efetivas, salvar_permissoes,
    espelhar_permissoes, consultar_confirmacao_email_admin, confirmar_email_manualmente,
    permissoes_por_perfil, permissoes_padrao, salvar_permissoes_por_perfil,
    restaurar_permissoes_do_perfil)

st.set_page_config(page_title="PRISMA | Gestão de Acessos",page_icon="🔐",layout="wide")
exigir_acesso_modulo("gestao")
if st.session_state.get("perfil")!="admin":
    st.error("Acesso exclusivo de administradores")
    st.stop()

st.title("🔐 PRISMA — Gestão de Acessos")
st.caption("Aprovação de contas e permissões individuais ou padrão por tipo de usuário.")
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


try:
    regras_perfil = permissoes_por_perfil()
except ErroPRISMA as erro:
    st.error(f"Não foi possível consultar as permissões por tipo: {erro}")
    st.stop()

aba_individuais, aba_tipos = st.tabs([
    "👤 Acessos individuais", "👥 Acessos por tipo de usuário",
])
with aba_individuais:
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
    
    st.subheader("Confirmação de e-mail no Supabase")
    st.caption(
        "Esta ação é independente da aprovação de acesso ao PRISMA e não altera "
        "perfil, situação ou permissões da conta."
    )
    atualizar_email = st.button(
        "Atualizar situação do e-mail", key="ga_atualizar_email_" + str(u["id"])
    )
    chave_estado = "ga_confirmacao_email_estado"
    estado_salvo = st.session_state.get(chave_estado)
    if (atualizar_email or not isinstance(estado_salvo, dict)
            or estado_salvo.get("id") != u["id"]):
        try:
            with st.spinner("Verificando confirmação no Supabase Auth..."):
                estado = consultar_confirmacao_email_admin(ator, u["id"])
            estado_salvo = {"id": u["id"], "dados": estado, "erro": None}
        except ErroPRISMA as erro:
            estado_salvo = {"id": u["id"], "dados": None, "erro": str(erro)}
        st.session_state[chave_estado] = estado_salvo
    
    if st.session_state.pop("ga_email_confirmacao_sucesso", None) == u["id"]:
        st.success("E-mail confirmado no Supabase. O acesso ainda depende da aprovação da conta.")
    
    if estado_salvo.get("erro"):
        st.error("Não foi possível consultar a confirmação: " + estado_salvo["erro"])
    else:
        estado_email = estado_salvo["dados"]
        if estado_email["confirmado"]:
            st.success("E-mail confirmado no Supabase.")
        else:
            st.warning("Este e-mail ainda não foi confirmado no Supabase.")
            st.caption(
                "Antes de confirmar manualmente, valide a identidade do usuário e a "
                "titularidade do endereço por um canal corporativo independente."
            )
            with st.form("ga_confirmar_email_" + str(u["id"])):
                email_para_confirmar = st.text_input(
                    "Digite exatamente o e-mail cadastrado para confirmar a operação",
                    placeholder=str(u["email"]),
                )
                identidade_validada = st.checkbox(
                    "Confirmei por outro canal confiável que o e-mail pertence a este usuário.",
                )
                executar_confirmacao = st.form_submit_button(
                    "Confirmar e-mail manualmente", type="primary", use_container_width=True
                )
            if executar_confirmacao:
                try:
                    confirmado_agora = confirmar_email_manualmente(
                        ator, u["id"], email_para_confirmar,
                        identidade_verificada=identidade_validada,
                    )
                    st.session_state.pop(chave_estado, None)
                    if confirmado_agora:
                        st.session_state["ga_email_confirmacao_sucesso"] = u["id"]
                    st.rerun()
                except ErroPRISMA as erro:
                    st.error(str(erro))
    
    st.subheader("Espelhar permissões de outro usuário")
    st.caption(
        "Selecione um usuário modelo para substituir, de uma vez, as permissões individuais "
        "da conta escolhida. O perfil e a situação do cadastro não serão alterados."
    )
    if u["perfil"]=="admin":
        st.info("Administradores possuem permissões definidas pelo perfil e não podem receber espelhamento.")
    else:
        modelos = {
            outro["id"]: outro for outro in usuarios
            if outro["id"] != u["id"] and outro["status"] == "ativo"
        }
        if not modelos:
            st.info("Não há outro usuário ativo disponível para usar como modelo.")
        else:
            with st.form("espelhar_permissoes_" + str(u["id"])):
                origem_id = st.selectbox(
                    "Copiar permissões de",
                    options=list(modelos),
                    format_func=lambda identificador: (
                        f"{modelos[identificador]['nome']} — "
                        f"{modelos[identificador]['email']} "
                        f"({modelos[identificador]['perfil']})"
                    ),
                )
                st.caption(
                    "Copia inclusive as permissões padrão e as personalizações do modelo. "
                    "Não concede o perfil de administrador nem acesso à Gestão de Acessos. "
                    "A visualização do Módulo 9 permanece liberada a todas as contas ativas."
                )
                confirmar_espelho = st.checkbox(
                    f"Confirmo a substituição de todas as permissões individuais de {u['nome'] or u['email']}.",
                    key="confirmar_espelho_" + str(u["id"]),
                )
                executar_espelho = st.form_submit_button(
                    "Aplicar permissões do usuário modelo",
                    type="primary", use_container_width=True,
                )
            if executar_espelho:
                if not confirmar_espelho:
                    st.warning("Confirme a substituição das permissões antes de aplicar.")
                else:
                    try:
                        espelhar_permissoes(ator, origem_id, u["id"])
                        # As caixas da edição manual já existem em session_state;
                        # apagá-las garante que exibam as permissões recém-copiadas.
                        prefixo = f"perm_{u['id']}_"
                        for chave in list(st.session_state):
                            if chave.startswith(prefixo):
                                st.session_state.pop(chave, None)
                        st.session_state.pop("confirmar_espelho_" + str(u["id"]), None)
                        st.session_state["msg_espelhamento_sucesso"] = (
                            f"Permissões de {modelos[origem_id]['nome'] or modelos[origem_id]['email']} "
                            f"aplicadas a {u['nome'] or u['email']}."
                        )
                        st.rerun()
                    except ErroPRISMA as erro:
                        st.error(str(erro))
    
    mensagem_espelho = st.session_state.pop("msg_espelhamento_sucesso", None)
    if mensagem_espelho:
        st.success(mensagem_espelho)
    
    st.subheader("Permissões por ferramenta")
    st.caption("As opções abaixo são exceções individuais que prevalecem sobre o perfil. A visualização do Módulo 9 — Gestão de Campo permanece universal.")
    if u["perfil"]=="admin":
        st.info("O perfil Administrador tem acesso integral. Para personalizar, utilize o perfil Usuário ou Consulta.")
    else:
        try:
            matriz=permissoes_efetivas(u["perfil"],permissoes_usuario(u["id"]),regras_perfil)
        except ErroPRISMA as erro:st.error(str(erro));st.stop()
        with st.form("alterar_permissoes"):
            alterar={}
            for mod,nome in MODULOS.items():
                if mod=="gestao":continue
                st.markdown(f"**{nome}**" if mod != "historico" else "**Módulo 3B — Farol Histórico**")
                colunas=st.columns(len(ACOES))
                for j,acao in enumerate(ACOES):
                    with colunas[j]:
                        chave=f"perm_{u['id']}_{mod}_{acao}"
                        leitura_universal = (mod, acao) == ("9", "visualizar")
                        alterar[(mod,acao)]=st.checkbox(
                            acao.title(), value=True if leitura_universal else matriz.get((mod,acao),False),
                            key=chave, disabled=leitura_universal,
                            help="Liberado para todas as contas ativas." if leitura_universal else None,
                        )
                st.divider()
            salvar=st.form_submit_button("Salvar permissões individuais",type="primary",use_container_width=True)
        if salvar:
            try:
                salvar_permissoes(ator,u["id"],alterar)
                st.success("Permissões atualizadas. A alteração será aplicada na próxima revalidação da sessão do usuário.")
                st.rerun()
            except ErroPRISMA as erro:st.error(str(erro))
    
        st.divider()
        personalizacoes = permissoes_usuario(u["id"])
        st.subheader("Restaurar permissões do perfil")
        st.caption(
            "Remove todas as exceções individuais deste usuário, incluindo permissões "
            "copiadas por espelhamento. As regras do tipo de usuário passam a valer. "
            "Não altera perfil, conta nem os padrões globais."
        )
        st.write(f"**Exceções cadastradas:** {len(personalizacoes)}")
        with st.form("ga_restaurar_heranca_" + str(u["id"])):
            confirmar_restauracao = st.checkbox(
                f"Confirmo que desejo substituir as permissões específicas de "
                f"{u['nome'] or u['email']} pelos padrões de {u['perfil']}."
            )
            restaurar = st.form_submit_button(
                "Restaurar permissões do perfil",
                disabled=not personalizacoes,
                use_container_width=True,
            )
        if restaurar:
            if not confirmar_restauracao:
                st.warning("Confirme a restauração antes de continuar.")
            else:
                try:
                    restaurar_permissoes_do_perfil(ator, u["id"])
                    for chave in list(st.session_state):
                        if chave.startswith(f"perm_{u['id']}_"):
                            st.session_state.pop(chave, None)
                    st.session_state["ga_restauracao_sucesso"] = u["id"]
                    st.rerun()
                except ErroPRISMA as erro:
                    st.error(str(erro))
        if st.session_state.pop("ga_restauracao_sucesso", None) == u["id"]:
            st.success("Exceções individuais removidas. O perfil agora define as permissões.")

with aba_tipos:
    st.subheader("Permissões padrão por tipo de usuário")
    st.caption(
        "As regras deste perfil são aplicadas às contas atuais e futuras na "
        "próxima revalidação da sessão. Permissões individuais personalizadas "
        "têm prioridade e não serão apagadas."
    )
    perfil_padrao = st.selectbox(
        "Tipo de usuário", options=("usuario", "operador"),
        format_func=lambda perfil: {"usuario": "Usuário", "operador": "Operador / Consulta"}[perfil],
        key="ga_perfil_padrao_selecionado",
    )
    st.info(
        "Administrador permanece com acesso integral e não pode ser restringido "
        "por esta configuração. O acesso à Gestão de Acessos é exclusivo de administradores. "
        "A visualização da Gestão de Campo continua universal para contas ativas."
    )
    if st.button("Recarregar padrões do banco", key="ga_recarregar_tipos"):
        for chave in list(st.session_state):
            if chave.startswith("ga_tipo_"):
                st.session_state.pop(chave, None)
        st.rerun()
    matriz_padrao = permissoes_padrao(perfil_padrao, regras_perfil)
    with st.form("ga_form_perfil_" + perfil_padrao):
        alteracoes_perfil = {}
        for modulo, nome in MODULOS.items():
            if modulo == "gestao":
                continue
            rotulo_modulo = (
                "Módulo 3B — Farol Histórico" if modulo == "historico" else nome
            )
            st.markdown(f"**{rotulo_modulo}**")
            colunas = st.columns(len(ACOES))
            for indice, acao in enumerate(ACOES):
                universal = (modulo, acao) == ("9", "visualizar")
                with colunas[indice]:
                    selecionado = st.checkbox(
                        acao.title(),
                        value=True if universal else bool(matriz_padrao.get((modulo, acao), False)),
                        key=f"ga_tipo_{perfil_padrao}_{modulo}_{acao}",
                        disabled=universal,
                        help="Visualização universal para contas ativas." if universal else None,
                    )
                    if not universal:
                        alteracoes_perfil[(modulo, acao)] = selecionado
            st.divider()
        st.caption(
            "Liberar ações sem liberar Visualizar não possibilita executar essas "
            "ações; o acesso ao módulo também é obrigatório."
        )
        confirmar_tipo = st.checkbox(
            f"Confirmo a alteração das permissões padrão do perfil {perfil_padrao}.",
            key="ga_tipo_confirmar_" + perfil_padrao,
        )
        salvar_tipo = st.form_submit_button(
            "Salvar permissões do tipo de usuário",
            type="primary", use_container_width=True,
        )
    if st.session_state.pop("ga_perfil_salvo_mensagem", None) == perfil_padrao:
        st.success(
            f"Permissões padrão do perfil {perfil_padrao} salvas. "
            "As regras passam a valer na revalidação da sessão."
        )
    if salvar_tipo:
        if not confirmar_tipo:
            st.warning("Confirme a alteração dos acessos padrão antes de salvar.")
        else:
            try:
                salvar_permissoes_por_perfil(ator, perfil_padrao, alteracoes_perfil)
                st.session_state["ga_perfil_salvo_mensagem"] = perfil_padrao
                # Atualiza a prévia individual, que usa widgets persistentes.
                for chave in list(st.session_state):
                    if chave.startswith("perm_"):
                        st.session_state.pop(chave, None)
                st.rerun()
            except ErroPRISMA as erro:
                st.error(f"Não foi possível salvar os padrões do perfil: {erro}")
