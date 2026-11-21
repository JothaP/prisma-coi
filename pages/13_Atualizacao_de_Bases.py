"""PRISMA — Central de Atualização de Bases.

Navegação por arquivos GitHub, tabelas operacionais e Supabase Storage.
Toda gravação requer confirmação explícita e autorização administrativa.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

import pandas as pd
import streamlit as st

from auth import verificar_autenticacao
from prisma_atualizacoes_backend import (
    ErroAtualizacao, REPO_PADRAO, TABELAS_CONSULTA, COLUNAS_POCOS,
    administrador_atual, arquivos_github, github_config, validar_arquivo,
    substituir_github, buckets_storage, objetos_storage, substituir_storage,
    backups_storage, restaurar_storage, preparar_linhas_pocos,
    exportar_tabela, substituir_tabela_pocos, listar_backups_tabela,
    restaurar_tabela_pocos,
)

st.set_page_config(
    page_title="PRISMA | Central de Atualização de Bases",
    page_icon="🗂️", layout="wide", initial_sidebar_state="expanded",
)

if not verificar_autenticacao():
    st.error("Sessão não iniciada. Entre pelo Hub Central.")
    st.stop()
try:
    ADMIN_ID = administrador_atual()
except (PermissionError, ErroAtualizacao, Exception) as erro:
    st.error("Acesso exclusivo de administradores ativos.")
    st.stop()

st.title("🗂️ Central de Atualização de Bases")
st.caption(
    "Gerenciamento administrativo de arquivos e bases operacionais. "
    "Alterações exigem confirmação e são registradas em auditoria."
)
if st.button("← Voltar ao Hub Central"):
    st.switch_page("app.py")

mensagem = st.session_state.pop("cab_sucesso", None)
if mensagem:
    st.success(mensagem)

origem = st.radio(
    "Fonte de dados",
    ["GitHub · arquivos do repositório",
     "Supabase · tabelas",
     "Supabase · Storage"],
    horizontal=True,
    key="cab_origem",
)

def detalhes_validacao(resumo):
    col1, col2, col3 = st.columns(3)
    col1.metric("Tamanho do upload", f"{resumo['bytes'] / 1024 / 1024:.2f} MB")
    col2.metric("Tipo", resumo["extensao"].upper().lstrip("."))
    col3.metric("Registros", resumo.get("registros", "—"))
    st.caption("SHA-256: " + resumo["sha256"])
    if resumo.get("colunas"):
        st.caption("Colunas: " + ", ".join(map(str, resumo["colunas"])))
    if resumo.get("abas"):
        st.caption("Abas: " + ", ".join(resumo["abas"]))
    if resumo.get("amostra"):
        with st.expander("Visualizar cinco primeiros registros do novo arquivo"):
            st.dataframe(pd.DataFrame(resumo["amostra"]), hide_index=True,
                         use_container_width=True)


if origem.startswith("GitHub"):
    st.subheader("Repositório GitHub")
    repo, branch, token = github_config()
    st.caption(f"Repositório: `{repo}` · Branch: `{branch}`")
    if not token:
        st.warning(
            "A gravação pelo PRISMA depende da configuração da chave "
            "`PRISMA_GITHUB_TOKEN` no Streamlit Secrets. Para repositórios "
            "privados, a listagem também exige essa chave."
        )
    try:
        arquivos = arquivos_github()
    except (ErroAtualizacao, PermissionError) as exc:
        st.error(str(exc))
        st.stop()
    atualizaveis = [item for item in arquivos if item["editavel"]]
    x, y = st.columns(2)
    x.metric("Arquivos encontrados", len(arquivos))
    y.metric("Arquivos operacionais substituíveis", len(atualizaveis))
    mostrar_todos = st.checkbox(
        "Mostrar também arquivos de código e configuração (somente leitura)",
        value=False, key="cab_github_mostrar_todos",
    )
    itens = arquivos if mostrar_todos else atualizaveis
    termo = st.text_input(
        "Pesquisar por nome ou caminho",
        placeholder="Ex.: loggers, matriculas, NM_MUN_Teresina",
        key="cab_filtro_git",
    ).strip().casefold()
    if termo:
        itens = [x for x in itens if termo in x["caminho"].casefold()]
    if not itens:
        st.info("Nenhum arquivo corresponde à seleção.")
        st.stop()
    caminhos = {f"{item['caminho']} ({(item['tamanho'] or 0)/1024:.0f} KB)"
                + ("" if item["editavel"] else " · protegido"): item
                for item in itens}
    nome = st.selectbox("Arquivo do repositório", list(caminhos),
                        key="cab_arquivo_github")
    item = caminhos[nome]
    caminho = item["caminho"]
    st.caption(f"Caminho original: `{caminho}`")
    st.caption(f"SHA do arquivo atual (Git): `{item['sha']}`")
    st.link_button("Ver no GitHub",
        f"https://github.com/{repo}/blob/{quote(branch,safe='/')}/{quote(caminho,safe='/')}")
    if not item["editavel"]:
        st.info(
            "Este arquivo aparece no catálogo, mas está protegido contra "
            "substituição pela central. Códigos e configurações exigem "
            "o processo de desenvolvimento e revisão no GitHub."
        )
        st.stop()
    if not token:
        st.info("Configure o token com permissão Contents: read/write para habilitar substituições.")
        st.stop()
    st.divider()
    st.subheader("Substituir versão no GitHub")
    upload = st.file_uploader(
        "Novo arquivo — será gravado no caminho selecionado",
        type=[PurePosixPath(caminho).suffix.lower().lstrip(".")],
        key="cab_git_upload_"+item["sha"][:12],
    )
    if upload:
        try:
            arquivo = upload.getvalue()
            resumo = validar_arquivo(caminho, upload.name, arquivo)
            detalhes_validacao(resumo)
            st.info(
                "O GitHub preserva a versão anterior no histórico de commits. "
                "A nova versão depende do deploy da aplicação para ser lida "
                "pelos módulos que acessam arquivos do repositório."
            )
            confirma = st.checkbox(
                "Revisei os dados e autorizo substituir o arquivo selecionado.",
                key="cab_git_check_"+item["sha"][:10],
            )
            frase = st.text_input(
                "Digite SUBSTITUIR para confirmar",
                key="cab_git_frase_"+item["sha"][:10],
            )
            if st.button("Publicar nova versão no GitHub",type="primary",
                         disabled=not confirma or frase.strip()!="SUBSTITUIR",
                         key="cab_git_gravar"):
                resultado = substituir_github(caminho, upload.name, arquivo,item["sha"])
                st.session_state["cab_sucesso"] = (
                    f"Arquivo atualizado em commit {resultado['commit'][:12]}. "
                    f"Histórico disponível: {resultado['url']}"
                )
                st.rerun()
        except (ErroAtualizacao,PermissionError) as exc:
            st.error(str(exc))


elif origem.startswith("Supabase · tabelas"):
    st.subheader("Tabelas operacionais do Supabase")
    st.caption(
        "Tabelas críticas de usuários, permissões e auditoria não podem ser "
        "modificadas nesta central. A substituição integral é habilitada "
        "somente para a área de importação legada de poços."
    )
    tabela = st.selectbox(
        "Tabela", list(TABELAS_CONSULTA),
        format_func=lambda x: f"{x} — {TABELAS_CONSULTA[x]}",
        key="cab_tabela",
    )
    if st.button("Consultar registros da tabela",key="cab_tab_consultar"):
        try:
            dados = exportar_tabela(tabela)
            st.session_state["cab_tabela_dados"] = {"tabela":tabela,"dados":dados}
        except (ErroAtualizacao,PermissionError) as exc:
            st.error(str(exc))
    estado = st.session_state.get("cab_tabela_dados")
    if estado and estado.get("tabela")==tabela:
        dados = estado["dados"]
        st.caption(f"{len(dados):,} registros consultados. Limite de exportação: 10.000.")
        df = pd.DataFrame(dados)
        if not df.empty:
            st.dataframe(df.head(100),hide_index=True,use_container_width=True)
            st.download_button(
                "⬇️ Exportar registros em CSV",
                data=df.to_csv(index=False,sep=";").encode("utf-8-sig"),
                file_name=f"{tabela}.csv",
                mime="text/csv",
                key="cab_tab_exportar",
            )
    if tabela != "prisma_ativos_importacao":
        st.info(
            "Nesta versão, esta tabela permite consulta e exportação. "
            "A edição ou substituição direta de dados relacionais em produção "
            "exige uma rotina específica para preservar vínculos e históricos."
        )
    else:
        st.divider()
        st.subheader("Substituir área de importação de poços")
        st.warning(
            "A operação substitui somente `prisma_ativos_importacao`. "
            "Ela **não** modifica automaticamente o cadastro ativo "
            "`prisma_ativos` nem o arquivo `dados/pocos.csv` no GitHub."
        )
        planilha = st.file_uploader(
            "Enviar cadastro completo de poços (CSV ou XLSX)",
            type=["csv","xlsx"], key="cab_tab_upload",
        )
        if planilha:
            try:
                payload = planilha.getvalue()
                if len(payload)>35*1024*1024:
                    raise ErroAtualizacao("Limite de upload: 35 MB.")
                linhas = preparar_linhas_pocos(
                    payload,PurePosixPath(planilha.name).suffix,
                )
                st.metric("Registros na nova base",len(linhas))
                st.dataframe(pd.DataFrame(linhas[:10]),hide_index=True,
                             use_container_width=True)
                st.caption("A operação cria um snapshot da versão anterior e executa "
                           "a substituição numa única transação.")
                conferir = st.checkbox(
                    "Confirmo que desejo substituir integralmente a base de importação de poços.",
                    key="cab_tab_check",
                )
                palavra = st.text_input(
                    "Digite SUBSTITUIR POCOS para confirmar",
                    key="cab_tab_confirmar",
                )
                if st.button(
                    "Substituir base de importação",type="primary",
                    disabled=not conferir or palavra.strip()!="SUBSTITUIR POCOS",
                    key="cab_tab_gravar",
                ):
                    novo = substituir_tabela_pocos(linhas)
                    st.session_state.pop("cab_tabela_dados",None)
                    st.session_state["cab_sucesso"] = (
                        f"Base de importação atualizada: {novo['antes']} → "
                        f"{novo['depois']} registros. Backup #{novo['backup_id']}."
                    )
                    st.rerun()
            except (ErroAtualizacao,PermissionError,ValueError) as exc:
                st.error(str(exc))
        with st.expander("Histórico e restauração de versões da tabela"):
            try:
                backups = listar_backups_tabela()
                if backups:
                    st.dataframe(pd.DataFrame(backups),hide_index=True,
                                 use_container_width=True)
                    escolhidos={f"#{v['id']} · {v['total_registros']} registros · "
                               f"{v['criado_em']}":v["id"] for v in backups}
                    escolhido=st.selectbox("Snapshot para restaurar",
                                           list(escolhidos),key="cab_tab_backup_sel")
                    confirma=st.checkbox(
                        "Confirmo a restauração deste snapshot da base de importação.",
                        key="cab_tab_restaura_check",
                    )
                    frase=st.text_input("Digite RESTAURAR POCOS para confirmar",
                                       key="cab_tab_restaurar_frase")
                    if st.button(
                        "Restaurar snapshot",type="secondary",
                        disabled=not confirma or frase.strip()!="RESTAURAR POCOS",
                        key="cab_tab_restaurar",
                    ):
                        res=restaurar_tabela_pocos(escolhidos[escolhido])
                        st.session_state.pop("cab_tabela_dados",None)
                        st.session_state["cab_sucesso"]=(
                            f"Backup #{res['restaurado_de']} restaurado "
                            f"({res['total']} registros). Foi criado backup de segurança "
                            f"#{res['backup_anterior']}."
                        )
                        st.rerun()
                else:
                    st.info("Nenhum snapshot criado por esta central até o momento.")
            except (ErroAtualizacao,PermissionError) as exc:
                st.error(str(exc))


else:
    st.subheader("Supabase Storage")
    st.caption(
        "Substituição de arquivos existentes em buckets, com backup privado "
        "da versão anterior. Arquivos em pastas de backup são protegidos."
    )
    try:
        buckets=buckets_storage()
        if not buckets:
            st.info("Nenhum bucket disponível.")
            st.stop()
        mapa={x["id"]:x for x in buckets}
        bucket=st.selectbox("Bucket",list(mapa),key="cab_storage_bucket")
        if mapa[bucket]["public"]:
            st.warning("Este bucket é público: o novo arquivo ficará sujeito à visibilidade atual.")
        objetos=objetos_storage(bucket)
    except (ErroAtualizacao,PermissionError) as exc:
        st.error(str(exc))
        st.stop()
    selecionaveis=[x for x in objetos if x["editavel"]]
    x,y=st.columns(2)
    x.metric("Objetos encontrados",len(objetos))
    y.metric("Substituíveis",len(selecionaveis))
    termo=st.text_input("Filtrar objetos pelo caminho",key="cab_storage_busca")
    itens=[item for item in objetos if termo.casefold() in item["caminho"].casefold()]
    if not itens:
        st.info("Nenhum objeto nesta seleção.")
        st.stop()
    item_map={x["caminho"]+(" · protegido" if not x["editavel"] else ""):x
              for x in itens}
    selecionado=st.selectbox("Objeto do Storage",list(item_map),key="cab_storage_objeto")
    atual=item_map[selecionado]
    caminho=atual["caminho"]
    st.caption(f"Objeto: `{bucket}/{caminho}`")
    if not atual["editavel"]:
        st.info("Este tipo de arquivo está protegido contra substituição.")
        st.stop()
    st.divider()
    st.subheader("Substituir objeto")
    upload=st.file_uploader(
        "Selecionar nova versão do objeto",
        type=[PurePosixPath(caminho).suffix.lower().lstrip(".")],
        key="cab_storage_upload_"+str(abs(hash((bucket,caminho)))),
    )
    if upload:
        try:
            arquivo=upload.getvalue()
            resumo=validar_arquivo(caminho,upload.name,arquivo,storage=True)
            detalhes_validacao(resumo)
            st.warning(
                "A versão atual será copiada para uma pasta de backup privada "
                "antes da substituição. Até a propagação do cache, algumas "
                "consultas podem visualizar a versão anterior."
            )
            confirmado=st.checkbox(
                "Autorizo o backup e a substituição do objeto.",
                key="cab_storage_confirmar",
            )
            frase=st.text_input("Digite SUBSTITUIR para confirmar",
                                key="cab_storage_frase")
            if st.button(
                "Substituir arquivo no Storage",type="primary",
                disabled=not confirmado or frase.strip()!="SUBSTITUIR",
                key="cab_storage_gravar",
            ):
                resultado=substituir_storage(bucket,caminho,upload.name,arquivo)
                st.session_state["cab_sucesso"]=(
                    f"Objeto {bucket}/{caminho} atualizado. "
                    f"Backup criado em {resultado['backup']}."
                )
                st.rerun()
        except (ErroAtualizacao,PermissionError) as exc:
            st.error(str(exc))
    with st.expander("Restaurar versão do Storage"):
        try:
            backups=backups_storage(bucket,caminho)
            if not backups:
                st.info("Nenhuma cópia de segurança encontrada para este objeto.")
            else:
                caminho_backup=st.selectbox("Versão anterior",backups,
                                            key="cab_storage_backup_sel")
                confirmado=st.checkbox(
                    "Confirmo que quero restaurar esta versão.",
                    key="cab_storage_restaurar_check",
                )
                frase=st.text_input("Digite RESTAURAR para confirmar",
                                    key="cab_storage_restaurar_frase")
                if st.button(
                    "Restaurar arquivo",disabled=not confirmado
                    or frase.strip()!="RESTAURAR",
                    key="cab_storage_restaurar",
                ):
                    backup_atual=restaurar_storage(bucket,caminho,caminho_backup)
                    st.session_state["cab_sucesso"]=(
                        f"Arquivo restaurado. A versão que estava ativa foi "
                        f"preservada em {backup_atual}."
                    )
                    st.rerun()
        except (ErroAtualizacao,PermissionError) as exc:
            st.error(str(exc))
