"""Acompanhamento Operacional integrado ao Farol (Módulo 3).
Persistência: Google Sheets dedicado, separado dos dados do mapa.
"""
import json
import threading
import time
import random
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from zoneinfo import ZoneInfo
from io import BytesIO

import pandas as pd
import streamlit as st
from fpdf import FPDF
from integracao_acompanhamento_producao import (
    registrar_vinculo as registrar_vinculo_producao,
    invalidar_cache_acompanhamento,
)


STATUS = ["Pendente", "Em Andamento", "Concluído"]
UNIDADES = ["Águas do Piauí", "Águas de Teresina", "Águas de Timon"]
REGIONAIS = ["Semiárido Norte", "Semiárido Sul", "Cerrado", "Litoral", "Meio Norte", "Teresina", "Timon"]
SIM_NAO = ["Não", "Sim"]
SPREADSHEET_ID = str(st.secrets.get("acompanhamento_operacional_spreadsheet_id", "")).strip()
CAMPOS_EVENTOS = ["id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao", "unidade", "regional", "municipio", "data_identificacao", "descricao", "arcgis", "sia", "loggers", "retorno_operacao", "finalizado", "data_finalizacao", "causa_identificada"]
CAMPOS_MOVIMENTACOES = ["id", "evento_id", "data_hora", "descricao", "status_momento"]
# Protege gravações concorrentes dentro de um mesmo processo Streamlit.
_GRAVACAO_LOCK = threading.RLock()

@st.cache_resource(show_spinner=False)
def cliente_google():
    dados = st.secrets["gcp_json"]
    dados = json.loads(dados) if isinstance(dados, str) else dict(dados)
    credenciais = Credentials.from_service_account_info(dados, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    return gspread.authorize(credenciais)

@st.cache_resource(show_spinner=False)
def planilha_google():
    return cliente_google().open_by_key(SPREADSHEET_ID)

@st.cache_resource(show_spinner=False)
def aba_google(nome):
    planilha = planilha_google()
    colunas = CAMPOS_EVENTOS if nome == "eventos" else CAMPOS_MOVIMENTACOES
    try:
        aba = planilha.worksheet(nome)
    except gspread.WorksheetNotFound:
        aba = planilha.add_worksheet(title=nome, rows=1000, cols=len(colunas))
    primeira = aba.row_values(1)
    if not primeira:
        aba.update(range_name="A1", values=[colunas])
    elif nome == "eventos" and primeira == colunas[:-1]:
        # Evolução não destrutiva: amplia a grade ANTES de escrever na coluna Q.
        # Uma aba legada pode ter apenas 16 colunas (A:P); escrever em Q1
        # sem antes ampliar a grade causa APIError [400] no Google Sheets.
        # Não altera o total de linhas nem os registros/cabeçalhos existentes.
        if aba.col_count < len(colunas):
            aba.resize(cols=len(colunas))
        aba.update(range_name="Q1", values=[["causa_identificada"]], value_input_option="RAW")
    elif primeira != colunas:
        raise ValueError(f"A aba '{nome}' possui cabeçalhos diferentes dos esperados. Faça backup e confira a estrutura antes de continuar.")
    return aba

@st.cache_data(ttl=20, show_spinner=False)
def ler_registros(nome):
    """Evita leituras repetidas no mesmo rerun e faz retentativas apenas em falhas transitórias."""
    aba = aba_google(nome)
    colunas = CAMPOS_EVENTOS if nome == "eventos" else CAMPOS_MOVIMENTACOES
    for tentativa in range(4):
        try:
            linhas = aba.get_all_values()
            return [dict(zip(colunas, (linha + [""] * len(colunas))[:len(colunas)]))
                    for linha in linhas[1:] if linha and str(linha[0]).strip()]
        except gspread.exceptions.APIError as erro:
            codigo = getattr(getattr(erro, "response", None), "status_code", None)
            if codigo not in (429, 500, 502, 503, 504) or tentativa == 3:
                raise
            time.sleep(min(2 ** tentativa + random.uniform(0, 0.5), 8))


def novo_id(registros):
    return max((int(r["id"]) for r in registros if str(r.get("id", "")).isdigit()), default=0) + 1

def agora():
    # Horário operacional de Teresina, independente do fuso do servidor.
    return datetime.now(ZoneInfo("America/Fortaleza")).strftime("%d/%m/%Y %H:%M")


def status_para_interface(status):
    """Compatibilidade com acompanhamentos criados no formato anterior."""
    valor = str(status or "").strip()
    if valor.casefold().startswith("concluído"):
        return "Concluído"
    if valor == "Aguardando motivo":
        return "Pendente"
    return valor if valor in STATUS else "Em Andamento"


def causa_para_interface(evento):
    causa = str(evento.get("causa_identificada", "") or "").strip()
    if causa in SIM_NAO:
        return causa
    # Herda o significado dos antigos status para fichas já encerradas.
    antigo = str(evento.get("status", "")).casefold()
    return "Sim" if "causa identificada" in antigo and "não identificada" not in antigo else "Não"

def exigir_permissao_acompanhamento():
    """Somente perfis admin/usuario podem persistir mudanças, inclusive em ficha concluída."""
    perfil = str(st.session_state.get("perfil", "")).strip().lower()
    if perfil not in {"admin", "usuario"}:
        raise PermissionError("Seu perfil permite apenas consultar os acompanhamentos.")


def cadastrar(titulo, qtd, status, unidade, regional, municipio, data_ident, descricao,
              arcgis, sia, loggers, retorno, finalizado, data_fim, causa_identificada="Não"):
    exigir_permissao_acompanhamento()
    if status not in STATUS:
        raise ValueError("Status inválido para o novo acompanhamento.")
    if causa_identificada not in SIM_NAO:
        raise ValueError("Informe se a causa foi identificada.")
    if not titulo.strip() or not municipio.strip():
        raise ValueError("Título e município são obrigatórios.")
    momento = agora()
    if status == "Concluído" or finalizado == "Sim":
        status = "Concluído"
        finalizado = "Sim"
        data_fim = data_fim or momento
    else:
        finalizado, data_fim = "Não", ""
    with _GRAVACAO_LOCK:
        ev_aba = aba_google("eventos")
        mov_aba = aba_google("movimentacoes")
        eid = novo_id(ler_registros("eventos"))
        mid = novo_id(ler_registros("movimentacoes"))
        dados = [eid, titulo.strip(), int(qtd), status, momento, unidade, regional, municipio.strip(),
                 data_ident or momento, descricao, arcgis, sia, loggers, retorno, finalizado, data_fim,
                 causa_identificada]
        ev_aba.append_row(dados, value_input_option="RAW")
        mov_aba.append_row([mid, eid, momento, f"Registro inicial criado. Relato: {descricao}", status], value_input_option="RAW")
        ler_registros.clear()
        invalidar_cache_acompanhamento()
    return eid

def listar(status="Todos", municipio="", regional="Todas", unidade="Todas", finalizado="Todos"):
    df = pd.DataFrame(ler_registros("eventos"), columns=CAMPOS_EVENTOS)
    colunas = ["id", "titulo", "status", "municipio", "regional", "unidade", "qtd_reclamacoes", "ultima_atualizacao", "finalizado"]
    if df.empty:
        return pd.DataFrame(columns=colunas)
    for campo, valor, ignorar in [("status",status,"Todos"),("regional",regional,"Todas"),
                                   ("unidade",unidade,"Todas"),("finalizado",finalizado,"Todos")]:
        if valor != ignorar:
            df = df[df[campo] == valor]
    if municipio.strip():
        df = df[df["municipio"].str.contains(municipio.strip(), case=False, regex=False, na=False)]
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df["qtd_reclamacoes"] = pd.to_numeric(df["qtd_reclamacoes"], errors="coerce").fillna(0).astype(int)
    return df.sort_values("id", ascending=False)[colunas].reset_index(drop=True)

def obter_evento(eid):
    return next((r for r in ler_registros("eventos") if str(r["id"]) == str(eid)), None)

def obter_movimentacoes(eid):
    rows = [r for r in ler_registros("movimentacoes") if str(r["evento_id"]) == str(eid)]
    rows.sort(key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0)
    return [(r["data_hora"], r["descricao"], r["status_momento"]) for r in rows]

def esta_concluido(evento):
    """Identifica a situação da ficha; não determina as permissões de edição."""
    return (str(evento.get("status", "")).strip().casefold().startswith("concluído")
            or str(evento.get("finalizado", "")).strip().casefold() == "sim")


def editar_ficha(eid, titulo, qtd, unidade, regional, municipio, data_ident,
                 descricao, arcgis, sia, loggers, retorno):
    """Edita a ficha (inclusive concluída) mantendo o ID e as movimentações."""
    exigir_permissao_acompanhamento()
    if not str(titulo).strip() or not str(municipio).strip():
        raise ValueError("Título e município são obrigatórios.")
    with _GRAVACAO_LOCK:
        # Consulta atualizada para não sobrescrever uma ficha encerrada por outro usuário.
        aba = aba_google("eventos")
        linhas = aba.get_all_values()
        matches = [(i, linha) for i, linha in enumerate(linhas[1:], start=2)
                   if linha and str(linha[0]).strip() == str(eid)]
        if len(matches) != 1:
            raise ValueError("Acompanhamento não encontrado ou ID duplicado.")
        numero_linha, linha = matches[0]
        atual = dict(zip(CAMPOS_EVENTOS, (linha + [""] * len(CAMPOS_EVENTOS))[:len(CAMPOS_EVENTOS)]))
        momento = agora()
        # B:D = título, quantidade, status (mantido); E:N = atualização e campos editáveis.
        aba.batch_update([
            {"range": f"B{numero_linha}:C{numero_linha}",
             "values": [[str(titulo).strip(), int(qtd)]]},
            {"range": f"E{numero_linha}:N{numero_linha}",
             "values": [[momento, unidade, regional, str(municipio).strip(),
                         str(data_ident).strip() or atual.get("data_identificacao", ""),
                         descricao, arcgis, sia, loggers, retorno]]},
        ], value_input_option="RAW")
        # Auditoria: edição é uma movimentação, mas não apaga as anteriores.
        mov_aba = aba_google("movimentacoes")
        mid = novo_id(ler_registros("movimentacoes"))
        mov_aba.append_row([mid, eid, momento, "Dados da ficha atualizados (edição cadastral).",
                            atual.get("status", "")], value_input_option="RAW")
        ler_registros.clear()
        invalidar_cache_acompanhamento()


def atualizar(eid, status, observacao, finalizado, data_fim):
    exigir_permissao_acompanhamento()
    momento = agora()
    if status not in STATUS:
        raise ValueError("Status inválido.")
    if status == "Concluído" or finalizado == "Sim":
        status, finalizado = "Concluído", "Sim"
    else:
        finalizado, data_fim = "Não", ""
    texto = observacao.strip() or f"Status atualizado para: {status}"
    with _GRAVACAO_LOCK:
        aba = aba_google("eventos")
        ids_coluna = aba.col_values(1)
        linhas_exatas = [i for i, valor in enumerate(ids_coluna, start=1) if i > 1 and str(valor).strip() == str(eid)]
        if len(linhas_exatas) != 1:
            raise ValueError("Evento não encontrado ou ID duplicado.")
        numero_linha = linhas_exatas[0]
        linha_atual = aba.row_values(numero_linha)
        evento_atual = dict(zip(CAMPOS_EVENTOS, (linha_atual + [""] * len(CAMPOS_EVENTOS))[:len(CAMPOS_EVENTOS)]))
        if finalizado == "Sim":
            # Edições posteriores não mudam a data histórica do encerramento.
            data_fim = data_fim or evento_atual.get("data_finalizacao", "") or momento
        # Atualiza o intervalo D:E (status e data da última atualização) e O:P (finalização).
        aba.batch_update([
            {"range": f"D{numero_linha}:E{numero_linha}", "values": [[status, momento]]},
            {"range": f"O{numero_linha}:P{numero_linha}", "values": [[finalizado, data_fim or ""]]},
        ], value_input_option="RAW")
        mov_aba = aba_google("movimentacoes")
        mid = novo_id(ler_registros("movimentacoes"))
        mov_aba.append_row([mid, eid, momento, texto, status], value_input_option="RAW")
        ler_registros.clear()
        invalidar_cache_acompanhamento()

def salvar_popup(eid, titulo, qtd, status, causa, unidade, regional, municipio,
                 descricao, arcgis, sia, loggers, retorno, movimentacao="", concluir=False):
    """Edição autorizada, inclusive após conclusão, com registro de histórico."""
    exigir_permissao_acompanhamento()
    if not str(titulo).strip() or not str(municipio).strip():
        raise ValueError("Título e município são obrigatórios.")
    if status not in STATUS or causa not in SIM_NAO:
        raise ValueError("Status ou identificação da causa inválidos.")
    with _GRAVACAO_LOCK:
        aba = aba_google("eventos")
        # Releitura garante edição sobre o registro mais recente e preserva o histórico.
        indices = [n for n, id_item in enumerate(aba.col_values(1), start=1)
                   if n > 1 and str(id_item).strip() == str(eid)]
        if len(indices) != 1:
            raise ValueError("Acompanhamento não encontrado ou ID duplicado.")
        linha = indices[0]
        valores = aba.row_values(linha)
        anterior = dict(zip(CAMPOS_EVENTOS, (valores + [""] * len(CAMPOS_EVENTOS))[:len(CAMPOS_EVENTOS)]))
        momento = agora()
        ja_concluido = esta_concluido(anterior)
        # A ficha já concluída pode ser corrigida ou reaberta por admin/usuario.
        # Um novo encerramento de ficha aberta é confirmado pelo botão específico.
        if status == "Concluído" and not concluir and not ja_concluido:
            raise ValueError("Para concluir, utilize o botão 'Acompanhamento Concluído'.")
        status_final = "Concluído" if concluir else status
        finalizado = "Sim" if status_final == "Concluído" else "Não"
        if finalizado == "Sim":
            data_fim = (anterior.get("data_finalizacao", "") or momento) if ja_concluido else momento
        else:
            data_fim = ""
        linha_nova = [
            str(titulo).strip(), int(qtd), status_final, momento, unidade, regional,
            str(municipio).strip(), anterior.get("data_identificacao", "") or momento,
            str(descricao).strip(), arcgis, sia, loggers, retorno,
            finalizado, data_fim, causa,
        ]
        aba.update(range_name=f"B{linha}:Q{linha}", values=[linha_nova], value_input_option="RAW")
        descr = str(movimentacao or "").strip()
        if not descr:
            if concluir:
                descr = "Acompanhamento concluído."
            else:
                alteracoes = []
                rotulos = {"status": "status", "causa_identificada": "causa identificada",
                           "descricao": "relato", "arcgis": "ArcGIS", "sia": "SIA",
                           "loggers": "loggers", "retorno_operacao": "retorno operacional",
                           "titulo": "título", "qtd_reclamacoes": "reclamações",
                           "unidade": "unidade", "regional": "regional", "municipio": "município"}
                novo = dict(zip(CAMPOS_EVENTOS[1:], linha_nova))
                for campo, rotulo in rotulos.items():
                    antigo = anterior.get(campo, "")
                    atual = novo.get(campo, "")
                    if campo == "status":
                        antigo = status_para_interface(antigo)
                    elif campo == "causa_identificada":
                        antigo = causa_para_interface(anterior)
                    if str(antigo) != str(atual):
                        alteracoes.append(rotulo)
                descr = ("Ficha atualizada: " + ", ".join(alteracoes) + ".") if alteracoes else "Ficha revisada, sem alteração dos campos."
        if ja_concluido and status_final != "Concluído":
            descr = "Acompanhamento reaberto. " + descr
        elif concluir and not ja_concluido and "concluído" not in descr.casefold():
            descr += " | Acompanhamento concluído."
        aba_mov = aba_google("movimentacoes")
        mid = novo_id(ler_registros("movimentacoes"))
        aba_mov.append_row([mid, eid, momento, descr, status_final], value_input_option="RAW")
        ler_registros.clear()
        invalidar_cache_acompanhamento()


@st.dialog("📋 Acompanhamento Operacional", width="large")
def popup_acompanhamento_farol(contexto, pode_editar=True):
    """Abertura/edição imediata a partir do botão de uma concentração."""
    if not SPREADSHEET_ID or SPREADSHEET_ID == str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip():
        st.error("Configure uma planilha separada para o acompanhamento.")
        return
    try:
        aba_google("eventos")
        aba_google("movimentacoes")
        eid = str(contexto.get("evento_id", "") or "").strip()
        evento = obter_evento(eid) if eid else None
        if eid and evento is None:
            st.error("A ficha vinculada não foi encontrada na planilha de acompanhamento.")
            return
    except (gspread.exceptions.GSpreadException, ValueError, KeyError) as erro:
        st.error(f"Não foi possível consultar o acompanhamento: {erro}")
        return

    concluido = bool(evento and esta_concluido(evento))
    # A permissão tem uma única fonte: o perfil autenticado no Hub.
    # Não depender de pode_editar, que pode ter sido transmitido por um fluxo
    # legado de consulta e bloquear indevidamente administradores e usuários.
    perfil = str(st.session_state.get("perfil", "")).strip().lower()
    somente_leitura = perfil not in {"admin", "usuario"}
    if somente_leitura:
        st.warning(f"Modo consulta · perfil identificado: {perfil or 'não identificado'}. "
                   "A edição é exclusiva de admin e usuario.")
    else:
        st.caption(f"✏️ Edição habilitada para o perfil **{perfil}** · interface v3 (22/10/2026)")
    st.caption("Ficha vinculada à concentração do Farol · histórico preservado no Google Sheets")
    if concluido:
        st.success("Acompanhamento concluído.")
        if not somente_leitura:
            st.caption("Admin/usuário: todos os campos da ficha podem ser corrigidos. "
                       "Para reabrir, selecione Pendente ou Em Andamento e salve.")
    elif evento:
        st.info("Acompanhamento em andamento. É possível editar os campos e atualizar o status.")
    else:
        st.info("Ao salvar, o acompanhamento será iniciado com status 'Em Andamento'.")

    # Campos da ficha seguem a disposição aprovada pelo COI.
    with st.form(f"popup_acompanhamento_{eid or contexto.get('chave', 'novo')}"):
        topo_a, topo_b = st.columns([2.3, 1])
        titulo_padrao = (f"{contexto.get('codigo', '')} — {contexto.get('municipio', '')}"
                         f" — {contexto.get('bairros', '')}").strip(" —")
        titulo = topo_a.text_input("Título do Acompanhamento", value=str(evento.get("titulo", "")) if evento else titulo_padrao,
                                  disabled=somente_leitura)
        quantidade = topo_b.number_input("Nº de Reclamações", min_value=0, step=1,
                            value=int(float(evento.get("qtd_reclamacoes") or 0)) if evento else int(contexto.get("qtd", 0)),
                            disabled=somente_leitura)
        col_u, col_r, col_m = st.columns(3)
        unidade_atual = str(evento.get("unidade", "")) if evento else "Águas do Piauí"
        regional_atual = str(evento.get("regional", "")) if evento else "Cerrado"
        unidade = col_u.selectbox("Unidade", UNIDADES,
                     index=UNIDADES.index(unidade_atual) if unidade_atual in UNIDADES else 0,
                     disabled=somente_leitura)
        regional = col_r.selectbox("Regional", REGIONAIS,
                      index=REGIONAIS.index(regional_atual) if regional_atual in REGIONAIS else 0,
                      disabled=somente_leitura)
        municipio = col_m.text_input("Município", value=str(evento.get("municipio", "")) if evento else str(contexto.get("municipio", "")),
                                   disabled=somente_leitura)

        col_status, col_causa = st.columns(2)
        status_atual = status_para_interface(evento.get("status", "")) if evento else "Em Andamento"
        status = col_status.selectbox("Status", STATUS, index=STATUS.index(status_atual), disabled=somente_leitura,
                                      help="Ao mudar um concluído para Pendente/Em Andamento e salvar, ele será reaberto.")
        causa_atual = causa_para_interface(evento) if evento else "Não"
        causa = col_causa.radio("Causa identificada?", SIM_NAO, horizontal=True,
                                index=SIM_NAO.index(causa_atual), disabled=somente_leitura)
        descricao = st.text_area("Descrição / Relato Inicial", value=str(evento.get("descricao", "")) if evento else "",
                                 height=100, disabled=somente_leitura)
        st.markdown("**Validações de Sistemas**")
        cv1, cv2, cv3, cv4 = st.columns(4)
        arcgis = cv1.radio("Evento registrado no ArcGIS?", SIM_NAO, horizontal=True,
                          index=1 if evento and evento.get("arcgis") == "Sim" else 0, disabled=somente_leitura)
        sia = cv2.radio("Evento registrado no SIA?", SIM_NAO, horizontal=True,
                       index=1 if evento and evento.get("sia") == "Sim" else 0, disabled=somente_leitura)
        loggers = cv3.radio("Loggers validados?", SIM_NAO, horizontal=True,
                           index=1 if evento and evento.get("loggers") == "Sim" else 0, disabled=somente_leitura)
        retorno = cv4.radio("Retorno Operação/Controle?", SIM_NAO, horizontal=True,
                           index=1 if evento and evento.get("retorno_operacao") == "Sim" else 0,
                           disabled=somente_leitura)
        movimentacao = ""
        if evento and not somente_leitura:
            movimentacao = st.text_area("Nova movimentação (opcional)", placeholder="Descreva a ação realizada ou pendência...", height=70)
        if not somente_leitura:
            if concluido:
                salvar = st.form_submit_button("💾 Salvar alterações / Reabrir",
                                              type="primary", use_container_width=True)
                encerrar = confirmar_fim = False
            else:
                confirmar_fim = st.checkbox("Confirmo que este acompanhamento está encerrado.")
                salvar_col, concluir_col = st.columns(2)
                salvar = salvar_col.form_submit_button("💾 Salvar acompanhamento", type="primary", use_container_width=True)
                encerrar = concluir_col.form_submit_button("✅ Acompanhamento Concluído",
                                        use_container_width=True)
        else:
            salvar = encerrar = confirmar_fim = False

    if salvar or encerrar:
        try:
            if encerrar and not confirmar_fim:
                raise ValueError("Confirme o encerramento antes de concluir o acompanhamento.")
            if salvar and status == "Concluído" and not concluido:
                raise ValueError("Para concluir uma ficha em andamento, utilize 'Acompanhamento Concluído'.")
            if evento:
                salvar_popup(eid, titulo, quantidade, status, causa, unidade, regional,
                             municipio, descricao, arcgis, sia, loggers, retorno, movimentacao, concluir=encerrar)
            else:
                status_criacao = "Concluído" if encerrar else status
                novo = cadastrar(titulo, quantidade, status_criacao, unidade, regional,
                                 municipio, "", descricao, arcgis, sia, loggers, retorno,
                                 "Sim" if encerrar else "Não", "", causa_identificada=causa)
                # O vínculo nunca é criado antes da gravação da ficha.
                registrar_vinculo_producao(contexto, novo)
            st.rerun()
        except Exception as erro:
            st.error(f"Não foi possível salvar o acompanhamento: {erro}")
            if not evento:
                st.caption("Se o registro foi criado, confira a planilha antes de tentar novamente para evitar duplicação.")

    if evento:
        with st.expander("🕓 Histórico de movimentações", expanded=False):
            try:
                registros = obter_movimentacoes(eid)
                for data_mov, descricao_mov, status_mov in registros:
                    if str(descricao_mov).startswith("Registro inicial criado. Relato:"):
                        continue
                    st.markdown(f"**{data_mov} · {status_mov}**")
                    st.write(descricao_mov)
                if not registros:
                    st.caption("Ainda não existem movimentações.")
            except Exception as erro:
                st.warning(f"Histórico indisponível: {erro}")


def seguro(texto):
    substituicoes = {'•':'-', '—':'-', '–':'-', '“':'"', '”':'"', '‘':"'", '’':"'", '⚠️':'!', '📌':'', '⏱️':''}
    s = str(texto if texto is not None else "")
    for a, b in substituicoes.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")

class PDFExecutivo(FPDF):
    def header(self):
        self.set_fill_color(26,54,93)
        self.rect(0,0,210,22,"F")
        self.set_font("Helvetica","B",13)
        self.set_text_color(255,255,255)
        self.cell(0,8,"SISTEMA DE MONITORAMENTO OPERACIONAL - COI",align="C",ln=True)
        self.set_font("Helvetica",size=8)
        self.cell(0,4,"Relatorio Executivo de Acompanhamento de Evento",align="C",ln=True)
        self.ln(8)
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica","I",8)
        self.set_text_color(120,120,120)
        self.cell(0,10,seguro(f"Relatorio gerado em {agora()} - Pagina {self.page_no()}/{{nb}}"),align="C")

def gerar_pdf(eid):
    ev = obter_evento(eid)
    if not ev:
        raise ValueError("Evento não encontrado.")
    movs = obter_movimentacoes(eid)
    pdf = PDFExecutivo()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_fill_color(240,244,248)
    pdf.set_draw_color(200,210,220)
    pdf.rect(10,28,190,22,"DF")
    pdf.set_xy(15,31)
    pdf.set_font("Helvetica","B",11)
    pdf.set_text_color(26,54,93)
    pdf.cell(175,6,seguro(f"EVENTO #{eid}: {ev['titulo']}")[:92],ln=True)
    pdf.set_xy(15,40)
    pdf.set_font("Helvetica",size=9)
    pdf.cell(180,5,seguro(f"Status: {ev['status']} | Ultima atualizacao: {ev['ultima_atualizacao']}"))
    pdf.set_xy(10,55)
    def secao(titulo):
        pdf.set_font("Helvetica","B",11)
        pdf.set_text_color(26,54,93)
        pdf.cell(0,7,seguro(titulo),ln=True)
        pdf.line(10,pdf.get_y(),200,pdf.get_y())
        pdf.ln(4)
        pdf.set_text_color(0,0,0)
    def linha(rotulo, valor):
        pdf.set_font("Helvetica",size=9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin, 6, seguro(f"{rotulo}: {valor if valor not in (None,'') else 'N/A'}"), new_x="LMARGIN", new_y="NEXT")
    secao("1. RESUMO EXECUTIVO")
    for k, label in [("municipio","Municipio"),("regional","Regional"),("unidade","Unidade"),
                     ("qtd_reclamacoes","Qtd. reclamacoes")]:
        linha(label,ev[k])
    pdf.ln(4)
    secao("2. DADOS GERAIS")
    for k,label in [("data_identificacao","Data identificacao"),("status","Status atual"),
                    ("finalizado","Finalizado"),("data_finalizacao","Data finalizacao")]:
        linha(label,ev[k])
    pdf.ln(4)
    secao("3. VALIDACOES OPERACIONAIS")
    for k,label in [("arcgis","Evento no ArcGIS"),("sia","Evento no SIA"),
                    ("loggers","Loggers validados"),("retorno_operacao","Retorno Operacao/Controle")]:
        linha(label,ev[k])
    pdf.add_page()
    secao("4. DESCRICAO DETALHADA DA OCORRENCIA")
    linha("Relato",ev["descricao"] or "Nenhuma descricao informada")
    pdf.ln(6)
    secao("5. HISTORICO DE MOVIMENTACOES")
    for data, desc, status in movs:
        if pdf.get_y() > 257:
            pdf.add_page()
        pdf.set_font("Helvetica","B",9)
        pdf.set_text_color(26,54,93)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin, 5, seguro(f"{data} - Status: {status}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica",size=9)
        pdf.set_text_color(50,50,50)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin, 5, seguro(desc), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
    resultado = pdf.output(dest="S")
    return bytes(resultado) if not isinstance(resultado,str) else resultado.encode("latin-1")


def renderizar_acompanhamento_farol(pode_editar=True):
    """Ficha integrada ao Módulo 3, sem navegação para uma página separada."""
    if not SPREADSHEET_ID or SPREADSHEET_ID == str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip():
        st.error("Configure a planilha de acompanhamento separadamente da planilha do Farol.")
        return
    if not pode_editar:
        st.warning("Seu perfil possui acesso somente de leitura.")
        st.stop()
    try:
        aba_google("eventos")
        aba_google("movimentacoes")
    except Exception as exc:
        st.error(f"Não foi possível acessar a planilha do Módulo 7: {exc}")
        st.info("Confira o Secret gcp_json, o compartilhamento com a conta de serviço e o ID da planilha.")
        st.stop()

    contexto_farol = st.session_state.get("acomp_farol_contexto")
    if contexto_farol:
        st.info(f"Farol Operacional · {contexto_farol['codigo']} · {contexto_farol['municipio']} · {contexto_farol['bairros']}")
        if st.button("← Voltar à análise das concentrações"):
            st.session_state.pop("acomp_farol_contexto", None)
            st.session_state.pop("farol_exibir_acompanhamento", None)
            st.rerun()

    st.title("🌊 Acompanhamento Operacional — Farol")
    st.caption("Plataforma COI | Registro, acompanhamento de anomalias e relatórios executivos")
    with st.sidebar:
        st.markdown("### Navegação")
        if st.button("← Voltar ao Hub Central", use_container_width=True):
            st.session_state.pop("farol_exibir_acompanhamento", None)
            st.session_state.pop("acomp_farol_contexto", None)
            st.rerun()
        st.divider()
        st.caption("Armazenamento: Google Sheets exclusivo do Acompanhamento Operacional.")

    aba_novo, aba_consulta = st.tabs(["📝 Novo Registro Operacional", "🔍 Consulta, Histórico e PDF"])
    with aba_novo:
        with st.form("form_novo_evento", clear_on_submit=False):
            c1,c2,c3 = st.columns([2,1,2])
            titulo = c1.text_input("Título do Acompanhamento", value=(f"{contexto_farol['codigo']} — {contexto_farol['municipio']} — {contexto_farol['bairros']}" if contexto_farol and not contexto_farol.get("evento_id") else ""), placeholder="Ex.: Rompimento de rede Parnaíba")
            qtd = c2.number_input("Nº de Reclamações",min_value=0,value=int(contexto_farol["qtd"]) if contexto_farol and not contexto_farol.get("evento_id") else 0,step=1)
            status = c3.selectbox("Status",STATUS)
            c1,c2,c3 = st.columns(3)
            unidade = c1.selectbox("Unidade",UNIDADES)
            regional = c2.selectbox("Regional",REGIONAIS,index=2)
            municipio = c3.text_input("Município", value=contexto_farol["municipio"] if contexto_farol and not contexto_farol.get("evento_id") else "")
            c1,c2,c3 = st.columns(3)
            ident_auto = c1.checkbox("Identificação automática",value=True)
            data_ident = c1.text_input("Data de Identificação (DD/MM/AAAA HH:MM)",disabled=ident_auto)
            finalizado = c2.radio("Finalizado?",SIM_NAO,horizontal=True)
            data_fim = c3.text_input("Data de Finalização (DD/MM/AAAA HH:MM)")
            descricao = st.text_area("Descrição / Relato Inicial",height=110)
            st.markdown("#### Validações de Sistemas")
            c1,c2,c3,c4 = st.columns(4)
            arcgis = c1.radio("Evento registrado no ArcGIS?",SIM_NAO,horizontal=True)
            sia = c2.radio("Evento registrado no SIA?",SIM_NAO,horizontal=True)
            loggers = c3.radio("Loggers validados?",SIM_NAO,horizontal=True)
            retorno = c4.radio("Retorno Operação/Controle?",SIM_NAO,horizontal=True)
            salvar = st.form_submit_button("🚀 Cadastrar Ação Operacional",type="primary")
        if salvar:
            try:
                if contexto_farol and contexto_farol.get("evento_id"):
                    raise ValueError("Esta concentração já possui ficha. Utilize a aba Consulta, Histórico e PDF.")
                eid = cadastrar(titulo,qtd,status,unidade,regional,municipio,
                                "" if ident_auto else data_ident.strip(),descricao,arcgis,sia,loggers,retorno,
                                finalizado,data_fim.strip())
                if contexto_farol:
                    registrar_vinculo_producao(contexto_farol, eid)
                    st.session_state["acomp_farol_contexto"]["evento_id"] = str(eid)
                st.success(f"Evento #{eid} registrado com sucesso!")
            except (ValueError, gspread.exceptions.GSpreadException) as exc:
                st.error(str(exc))

    with aba_consulta:
        st.markdown("### Filtros de Pesquisa")
        with st.form("filtros_consulta"):
            c1,c2,c3,c4,c5 = st.columns(5)
            f_status = c1.selectbox("Status",["Todos"]+STATUS)
            f_uni = c2.selectbox("Unidade",["Todas"]+UNIDADES)
            f_reg = c3.selectbox("Regional",["Todas"]+REGIONAIS)
            f_fin = c4.selectbox("Finalizado",["Todos","Sim","Não"])
            f_mun = c5.text_input("Município")
            aplicar = st.form_submit_button("🔍 Aplicar Filtros")
        if aplicar or "filtros_acomp" not in st.session_state:
            st.session_state.filtros_acomp = (f_status,f_mun,f_reg,f_uni,f_fin)
        df = listar(*st.session_state.filtros_acomp)
        st.dataframe(df,hide_index=True,use_container_width=True)
        st.caption(f"{len(df)} evento(s) encontrado(s)")
        st.divider()
        st.markdown("### ⚙️ Gestão de Evento Selecionado")
        todos = listar()
        if todos.empty:
            st.info("Nenhum evento cadastrado ainda.")
        else:
            opcoes = {f"#{int(r.id)} - {r.titulo} ({r.municipio})":int(r.id) for r in todos.itertuples()}
            opcoes_lista = list(opcoes)
            id_contexto = str(contexto_farol.get("evento_id", "")) if contexto_farol else ""
            indice_contexto = next((i for i, rotulo in enumerate(opcoes_lista) if str(opcoes[rotulo]) == id_contexto), 0)
            escolhido = st.selectbox("Selecione o Evento para Gerenciar", opcoes_lista, index=indice_contexto, key="acomp_evento_selecionado")
            eid = opcoes[escolhido]
            ev = obter_evento(eid)
            if ev:
                st.info(f"📌 {ev['titulo']} | {ev['status']} | {ev['municipio']} | Reclamações: {ev['qtd_reclamacoes']} | Última atualização: {ev['ultima_atualizacao']}")
                c1,c2 = st.columns([1,2])
                with c1:
                    try:
                        pdf_bytes = gerar_pdf(eid)
                        st.download_button("📄 Baixar ficha de acompanhamento (sem mapa)",data=pdf_bytes,
                                           file_name=f"Relatorio_Executivo_COI_{eid}.pdf",mime="application/pdf",
                                           use_container_width=True)
                    except Exception as exc:
                        st.error(f"Falha ao gerar PDF: {exc}")
                with c2:
                    st.markdown("#### Histórico de Movimentações")
                    for dt, desc, stat in obter_movimentacoes(eid):
                        st.markdown(f"**⏱️ {dt} — {stat}**")
                        st.write(desc)
                if contexto_farol:
                    st.caption("Para baixar o PDF completo com o mapa da concentração, volte ao Farol e use ‘Preparar PDF com mapa da concentração’ na ficha correspondente.")
                if esta_concluido(ev):
                    st.info("Acompanhamento concluído: administrador e usuário podem corrigir ou reabrir a ficha.")
                if pode_editar:
                    with st.expander("✏️ Editar dados da ficha", expanded=True):
                        with st.form(f"editar_ficha_acomp_{eid}"):
                            a,b,c = st.columns([2,1,2])
                            titulo_edit = a.text_input("Título do Acompanhamento", value=ev["titulo"])
                            qtd_edit = b.number_input("Nº de Reclamações", min_value=0,
                                                      value=int(float(ev.get("qtd_reclamacoes") or 0)), step=1)
                            municipio_edit = c.text_input("Município", value=ev["municipio"])
                            a,b,c = st.columns(3)
                            unidade_edit = a.selectbox("Unidade", UNIDADES,
                                index=UNIDADES.index(ev["unidade"]) if ev["unidade"] in UNIDADES else 0)
                            regional_edit = b.selectbox("Regional", REGIONAIS,
                                index=REGIONAIS.index(ev["regional"]) if ev["regional"] in REGIONAIS else 0)
                            identificacao_edit = c.text_input("Data de Identificação", value=ev["data_identificacao"])
                            descricao_edit = st.text_area("Descrição / Relato Inicial", value=ev["descricao"], height=120)
                            st.markdown("**Validações dos sistemas**")
                            a,b,c,d = st.columns(4)
                            arcgis_edit = a.radio("ArcGIS", SIM_NAO, index=1 if ev["arcgis"] == "Sim" else 0,
                                                  horizontal=True, key=f"edit_arcgis_{eid}")
                            sia_edit = b.radio("SIA", SIM_NAO, index=1 if ev["sia"] == "Sim" else 0,
                                               horizontal=True, key=f"edit_sia_{eid}")
                            loggers_edit = c.radio("Loggers", SIM_NAO, index=1 if ev["loggers"] == "Sim" else 0,
                                                   horizontal=True, key=f"edit_loggers_{eid}")
                            retorno_edit = d.radio("Retorno Operação/Controle", SIM_NAO,
                                                    index=1 if ev["retorno_operacao"] == "Sim" else 0,
                                                    horizontal=True, key=f"edit_retorno_{eid}")
                            salvar_edicao = st.form_submit_button("💾 Salvar alterações da ficha", type="primary")
                        if salvar_edicao:
                            try:
                                editar_ficha(eid, titulo_edit, qtd_edit, unidade_edit, regional_edit,
                                            municipio_edit, identificacao_edit, descricao_edit,
                                            arcgis_edit, sia_edit, loggers_edit, retorno_edit)
                                st.success("Ficha atualizada com sucesso. Histórico preservado.")
                                st.rerun()
                            except (ValueError, gspread.exceptions.GSpreadException) as exc:
                                st.error(str(exc))
                if pode_editar:
                    st.markdown("#### Registrar Nova Movimentação / Atualização")
                    with st.form(f"atualizar_evento_{eid}"):
                        c1,c2,c3 = st.columns(3)
                        novo_status = c1.selectbox("Novo Status",STATUS,index=STATUS.index(ev["status"]) if ev["status"] in STATUS else 0)
                        fin_atual = c2.radio("Finalizado?",SIM_NAO,index=1 if ev["finalizado"]=="Sim" else 0,horizontal=True)
                        data_fim_atual = c3.text_input("Data Finalização",value=ev["data_finalizacao"] or "")
                        observacao = st.text_area("Descrição da Nova Atualização Operacional")
                        gravar = st.form_submit_button("➕ Salvar Movimentação",type="primary")
                    if gravar:
                        try:
                            atualizar(eid,novo_status,observacao,fin_atual,data_fim_atual.strip())
                            st.success(f"Evento #{eid} atualizado com sucesso!")
                            st.rerun()
                        except (ValueError, gspread.exceptions.GSpreadException) as exc:
                            st.error(str(exc))
