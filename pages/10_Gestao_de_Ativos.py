"""Módulo 9 — Gestão de Ativos.

Cadastro independente no Supabase. Não altera nem importa os CSVs do Farol.
Mapa baseado no padrão Folium/Leaflet do Módulo 1.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import folium
from folium.plugins import Fullscreen
import streamlit as st
from streamlit_folium import st_folium

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from auth import exigir_acesso_modulo, pode_executar_acao, registrar_atividade
from gestao_ativos_backend import (
    MAX_MARCADORES, criar_ativo, editar_ativo, historico_ativo,
    listar_ativos, municipios, obter_ativo, validar_historico,
)
from prisma_backend import ErroPRISMA

st.set_page_config(
    page_title="Gestão de Ativos | PRISMA", page_icon="🚰",
    layout="wide", initial_sidebar_state="collapsed",
)
exigir_acesso_modulo("9")

STATUS = {
    "": "Não informado",
    "operando": "Operando",
    "parado": "Parado",
    "em_manutencao": "Em manutenção",
    "inativo": "Inativo",
}
ROTULOS = {
    "planta": "Planta", "nome": "Nome", "municipio": "Município",
    "matricula": "Matrícula", "status": "Status",
    "ultima_os_operacao": "Última O.S. Operação",
    "data_ultima_os_operacao": "Data da O.S. Operação",
    "ultima_os_eletromecanica": "Última O.S. Eletromecânica",
    "data_ultima_os_eletromecanica": "Data da O.S. Eletromecânica",
    "observacao": "Observação", "latitude": "Latitude",
    "longitude": "Longitude",
}
FUSO = ZoneInfo("America/Fortaleza")


@st.cache_data(ttl=600, show_spinner=False)
def _municipios():
    return municipios()


@st.cache_data(ttl=35, show_spinner=False)
def _listar(municipio, pesquisa, status):
    return listar_ativos(municipio, pesquisa, status)


def _data(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _texto(valor):
    return str(valor if valor is not None else "")


def _campos_formulario(registro, prefixo, *, novo=False, coords=None, municipio_padrao=""):
    """As linhas do formulário seguem a disposição aprovada para os poços."""
    with st.form(prefixo):
        col1, col2 = st.columns(2)
        with col1:
            planta = st.text_input("Planta", value=_texto(registro.get("planta")), max_chars=100)
        with col2:
            nome = st.text_input("Nome", value=_texto(registro.get("nome")))
        col1, col2 = st.columns(2)
        with col1:
            matricula = st.text_input("Matrícula", value=_texto(registro.get("matricula")))
        with col2:
            atual = _texto(registro.get("status"))
            selecao = st.selectbox("Status", list(STATUS), index=list(STATUS).index(atual)
                                   if atual in STATUS else 0, format_func=lambda k: STATUS[k])
        col1, col2 = st.columns(2)
        with col1:
            os_operacao = st.text_input("Última O.S. Operação",
                                       value=_texto(registro.get("ultima_os_operacao")))
        with col2:
            data_operacao = st.date_input("Data — Operação",
                                         value=_data(registro.get("data_ultima_os_operacao")),
                                         format="DD/MM/YYYY")
        col1, col2 = st.columns(2)
        with col1:
            os_eletro = st.text_input("Última O.S. Eletromecânica",
                                     value=_texto(registro.get("ultima_os_eletromecanica")))
        with col2:
            data_eletro = st.date_input("Data — Eletromecânica",
                                       value=_data(registro.get("data_ultima_os_eletromecanica")),
                                       format="DD/MM/YYYY")
        municipio = st.text_input(
            "Município", value=_texto(registro.get("municipio") or municipio_padrao))
        latitude = longitude = None
        if novo:
            lat, lon = coords or (-5.09, -42.80)
            col1, col2 = st.columns(2)
            with col1:
                latitude = st.number_input("Latitude", value=float(lat), format="%.7f",
                                           min_value=-90.0, max_value=90.0)
            with col2:
                longitude = st.number_input("Longitude", value=float(lon), format="%.7f",
                                            min_value=-180.0, max_value=180.0)
        observacao = st.text_area("Observação", value=_texto(registro.get("observacao")),
                                 height=100)
        permitido = pode_executar_acao("9", "criar" if novo else "editar")
        enviar = st.form_submit_button("Cadastrar ativo" if novo else "Salvar alterações",
                                       type="primary", use_container_width=True,
                                       disabled=not permitido or not HISTORICO_DISPONIVEL)
    campos = {
        "planta": planta, "nome": nome, "matricula": matricula,
        "status": selecao or None, "ultima_os_operacao": os_operacao,
        "data_ultima_os_operacao": data_operacao.isoformat() if data_operacao else None,
        "ultima_os_eletromecanica": os_eletro,
        "data_ultima_os_eletromecanica": data_eletro.isoformat() if data_eletro else None,
        "municipio": municipio, "observacao": observacao,
    }
    return enviar, campos, latitude, longitude


def _limpar_cache():
    _listar.clear()
    _municipios.clear()


def _auditar(acao, registro):
    try:
        registrar_atividade(
            "9", acao, registro_id=registro.get("id"),
            detalhes=f"Cadastro de ativo: {str(registro.get('planta') or '')[:100]}",
        )
    except ErroPRISMA:
        # Histórico específico já foi persistido pelo trigger do banco.
        st.session_state["ga_aviso_auditoria"] = (
            "A alteração foi salva e registrada no histórico do ativo, "
            "mas houve falha no registro de auditoria geral."
        )


def _historico(registro):
    try:
        eventos = historico_ativo(registro["id"])
    except ErroPRISMA as exc:
        st.warning(f"Não foi possível consultar o histórico: {exc}")
        return
    if not eventos:
        st.info("Nenhuma edição registrada desde a ativação do histórico.")
        return

    # A ficha agora fica em um expander: não aninhar expanders no histórico.
    titulos = []
    for evento in eventos:
        data = str(evento.get("registrado_em") or "")
        try:
            data = datetime.fromisoformat(data.replace("Z", "+00:00")).astimezone(
                FUSO).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass
        autor = evento.get("usuario_nome") or evento.get("usuario_email") or "Usuário"
        operacao = {
            "criacao": "Criação", "edicao": "Edição", "exclusao": "Exclusão",
        }.get(evento.get("operacao"), "Alteração")
        titulos.append(f"{data} · {operacao} · {autor}")

    indice = st.selectbox(
        "Selecione a movimentação", range(len(eventos)),
        format_func=lambda i: titulos[i],
        key="ga_historico_evento_" + str(registro["id"]),
    )
    evento = eventos[indice]
    with st.container(border=True):
        st.markdown(f"**{titulos[indice]}**")
        st.caption(evento.get("usuario_email") or "")
        diferencas = evento.get("campos_alterados") or {}
        if diferencas:
            for campo, mudanca in diferencas.items():
                if campo in ("atualizado_em", "atualizado_por"):
                    continue
                anterior = (mudanca or {}).get("antes")
                novo = (mudanca or {}).get("depois")
                st.write(f"**{ROTULOS.get(campo, campo)}:** "
                         f"{_texto(anterior) or '—'} → {_texto(novo) or '—'}")
        else:
            st.write("Registro de criação ou alteração sem campos detalhados.")


def _fechar_painel():
    st.session_state.pop("ga_painel", None)


def _painel_ativo(ativo_id):
    try:
        registro = obter_ativo(ativo_id)
    except ErroPRISMA as exc:
        st.error(str(exc))
        return
    if not registro:
        st.warning("Ativo não encontrado. Selecione outro marcador no mapa.")
        st.session_state.pop("ga_painel", None)
        return

    planta = _texto(registro.get("planta")) or "Sem identificação"
    nome = _texto(registro.get("nome"))
    titulo = f"Ficha do ativo — {planta}" + (f" · {nome}" if nome else "")
    with st.expander(titulo, expanded=True):
        st.caption(
            f"{_texto(registro.get('municipio'))} · "
            f"{float(registro['latitude']):.6f}, {float(registro['longitude']):.6f}"
        )
        st.button("Fechar ficha", key="ga_fechar_ficha", on_click=_fechar_painel)
        aba_dados, aba_historico = st.tabs(["Cadastro", "Histórico de alterações"])
        with aba_dados:
            if not HISTORICO_DISPONIVEL:
                st.warning("Execute primeiro o SQL 03 (histórico) para liberar a edição.")
            enviar, campos, _, _ = _campos_formulario(
                registro, "ga_form_edicao_" + str(registro["id"]))
            if enviar:
                if not HISTORICO_DISPONIVEL:
                    st.error("Histórico de alterações não instalado.")
                    return
                campos_atuais = {k: registro.get(k) for k in campos}
                normalizar = lambda v: v if v not in ("", None) else None
                if all(normalizar(campos[k]) == normalizar(campos_atuais[k]) for k in campos):
                    st.info("Nenhuma informação foi alterada.")
                else:
                    try:
                        salvo = editar_ativo(registro["id"], campos, registro.get("atualizado_em"))
                        _auditar("editar", salvo)
                        _limpar_cache()
                        st.session_state["ga_mensagem"] = "Alterações salvas com histórico."
                        st.rerun()
                    except ErroPRISMA as exc:
                        st.error(str(exc))
        with aba_historico:
            _historico(registro)


def _painel_novo(coordenadas=None, municipio_padrao=""):
    with st.expander("Cadastrar novo poço", expanded=True):
        st.caption("O poço será cadastrado apenas no Supabase, sem alterar o Farol.")
        st.button("Fechar cadastro", key="ga_fechar_cadastro", on_click=_fechar_painel)
        if not HISTORICO_DISPONIVEL:
            st.warning("Execute primeiro o SQL 03 (histórico) para liberar cadastros.")
        enviar, campos, lat, lon = _campos_formulario(
            {}, "ga_form_novo", novo=True, coords=coordenadas,
            municipio_padrao=municipio_padrao)
        if enviar:
            if not HISTORICO_DISPONIVEL:
                st.error("Histórico de alterações não instalado.")
                return
            try:
                salvo = criar_ativo(campos, lat, lon)
                _auditar("criar", salvo)
                _limpar_cache()
                st.session_state["ga_painel"] = {"tipo": "ficha", "id": salvo["id"]}
                st.session_state["ga_mensagem"] = "Ativo cadastrado com histórico."
                st.rerun()
            except (ErroPRISMA, ValueError) as exc:
                st.error(str(exc))


st.title("Gestão de Ativos")
st.caption("PRISMA · Cadastro operacional georreferenciado no Supabase")
col_voltar, col_novo = st.columns([4, 1])
with col_voltar:
    if st.button("← Menu principal", key="ga_voltar"):
        st.switch_page("app.py")

try:
    HISTORICO_DISPONIVEL = validar_historico()
    cidades = _municipios()
except ErroPRISMA as exc:
    st.error(f"Não foi possível acessar o cadastro de ativos: {exc}")
    st.stop()

with col_novo:
    if st.button("＋ Novo poço", use_container_width=True,
                 disabled=not pode_executar_acao("9", "criar") or not HISTORICO_DISPONIVEL):
        st.session_state["ga_painel"] = {
            "tipo": "novo", "coordenadas": None, "municipio_padrao": "TERESINA",
        }

if not HISTORICO_DISPONIVEL:
    st.warning("Histórico do Supabase não localizado. Consulta liberada; criação e edição bloqueadas.")

if st.session_state.pop("ga_mensagem", None):
    st.success("Operação concluída e registrada no histórico de alterações.")
if aviso := st.session_state.pop("ga_aviso_auditoria", None):
    st.warning(aviso)

f1, f2, f3, f4 = st.columns([2.1, 2.2, 1.6, 1.6])
with f1:
    opcoes_cidade = ["Todos"] + cidades
    cidade_inicial = opcoes_cidade.index("TERESINA") if "TERESINA" in cidades else 0
    municipio = st.selectbox("Município", opcoes_cidade, index=cidade_inicial)
with f2:
    pesquisa = st.text_input("Pesquisar planta ou nome", placeholder="Ex.: PL-THE-PCO0004")
with f3:
    status = st.selectbox("Status", ["Todos"] + list(STATUS)[1:],
                          format_func=lambda s: s if s == "Todos" else STATUS[s])
with f4:
    mapa_base = st.selectbox("Mapa-base", ["Padrão", "Satélite", "Terreno"])

pode_criar = pode_executar_acao("9", "criar") and HISTORICO_DISPONIVEL
col_rotulos, col_cadastro, _ = st.columns([1.25, 1.7, 3])
with col_rotulos:
    mostrar_rotulos = st.toggle(
        "Exibir rótulos", value=False, key="ga_exibir_rotulos",
        help="Desligado por padrão para reduzir a quantidade de elementos renderizados no mapa.",
    )
with col_cadastro:
    modo_cadastro = st.toggle(
        "Cadastrar clicando no mapa", value=False,
        disabled=not pode_criar, key="ga_modo_cadastro",
    )
if modo_cadastro:
    st.info("Clique em um local livre do mapa para abrir o formulário de um novo poço.")
else:
    st.caption("Passe o mouse para informações rápidas. Clique no marcador para abrir a ficha abaixo do mapa.")

try:
    registros = _listar(municipio, pesquisa, status)
except ErroPRISMA as exc:
    st.error(f"Erro ao buscar ativos: {exc}")
    st.stop()

limitado = len(registros) > MAX_MARCADORES
ativos = registros[:MAX_MARCADORES]
st.caption(
    f"{len(ativos)} ativos exibidos" +
    (f" · Limite de {MAX_MARCADORES}: refine os filtros" if limitado else "")
)

coordenadas = [
    (float(r["latitude"]), float(r["longitude"])) for r in ativos
    if r.get("latitude") is not None and r.get("longitude") is not None
]
centro = [-5.09, -42.80]
if coordenadas:
    centro = [
        sum(lat for lat, _ in coordenadas) / len(coordenadas),
        sum(lon for _, lon in coordenadas) / len(coordenadas),
    ]
mapa = folium.Map(location=centro, zoom_start=11, tiles=None,
                  control_scale=True, prefer_canvas=True)
if mapa_base == "Satélite":
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri — World Imagery", name="Satélite",
    ).add_to(mapa)
elif mapa_base == "Terreno":
    folium.TileLayer("OpenTopoMap", name="Terreno").add_to(mapa)
else:
    folium.TileLayer("OpenStreetMap", name="Padrão").add_to(mapa)

Fullscreen(position="topright", title="Expandir mapa para tela cheia",
           title_cancel="Sair da tela cheia", force_separate_button=True).add_to(mapa)

for registro in ativos:
    if registro.get("latitude") is None or registro.get("longitude") is None:
        continue
    lat, lon = float(registro["latitude"]), float(registro["longitude"])
    planta = _texto(registro.get("planta"))
    nome = _texto(registro.get("nome"))
    status_legivel = STATUS.get(_texto(registro.get("status")), "Não informado")
    tooltip = (
        f"<span style='display:none'>PRISMA_ID={registro['id']}</span>"
        f"<b>Planta:</b> {escape(planta)}"
        + (f"<br><b>Nome:</b> {escape(nome)}" if nome else "")
        + f"<br><b>Status:</b> {escape(status_legivel)}"
        + (f"<br><b>Matrícula:</b> {escape(_texto(registro.get('matricula')))}"
           if registro.get("matricula") else "")
        + (f"<br><b>O.S. Operação:</b> {escape(_texto(registro.get('ultima_os_operacao')))}"
           if registro.get("ultima_os_operacao") else "")
    )
    folium.Marker(
        [lat, lon], tooltip=folium.Tooltip(tooltip, sticky=True),
        icon=folium.Icon(color="blue", icon="tint", prefix="fa"),
        z_index_offset=300,
    ).add_to(mapa)
    # Nenhum marcador/rótulo extra é gerado quando a opção está desligada.
    # Isso reduz o tamanho do HTML e o processamento no navegador.
    if mostrar_rotulos:
        rotulo = escape(planta) + (f" · {escape(nome)}" if nome else "")
        folium.Marker(
            [lat, lon], interactive=False, keyboard=False,
            icon=folium.DivIcon(
                html=("<div style='pointer-events:none;white-space:nowrap;"
                      "font:600 11px Segoe UI,Arial,sans-serif;color:#153257;"
                      "background:rgba(255,255,255,.93);padding:3px 5px;"
                      "border:1px solid #cfdae6;border-radius:4px;"
                      "box-shadow:0 1px 3px #0002;width:max-content;'>"
                      + rotulo + "</div>"),
                icon_size=(0, 0), icon_anchor=(-13, 10),
            ),
        ).add_to(mapa)

if len(coordenadas) > 1:
    mapa.fit_bounds(coordenadas, padding=(30, 30), max_zoom=14)

resultado = st_folium(
    mapa, width="100%", height=600, key="ga_mapa_interativo",
    returned_objects=["last_clicked", "last_object_clicked_count",
                      "last_object_clicked_tooltip"],
)
if resultado:
    clicks = int(resultado.get("last_object_clicked_count") or 0)
    tooltip = str(resultado.get("last_object_clicked_tooltip") or "")
    evento_marcador = (clicks, tooltip)
    achado = re.search(r"PRISMA_ID=([0-9a-fA-F-]{36})", tooltip)
    selecionou_ativo = bool(
        clicks > 0 and achado
        and evento_marcador != st.session_state.get("ga_evento_marcador_processado")
    )
    if selecionou_ativo:
        st.session_state["ga_evento_marcador_processado"] = evento_marcador
        st.session_state["ga_painel"] = {"tipo": "ficha", "id": achado.group(1)}
    elif modo_cadastro and (ponto := resultado.get("last_clicked")):
        lat, lon = ponto.get("lat"), ponto.get("lng")
        if lat is not None and lon is not None:
            chave = (round(float(lat), 7), round(float(lon), 7))
            if chave != st.session_state.get("ga_terreno_processado"):
                st.session_state["ga_terreno_processado"] = chave
                st.session_state["ga_painel"] = {
                    "tipo": "novo", "coordenadas": (lat, lon),
                    "municipio_padrao": municipio if municipio != "Todos" else "",
                }

if not modo_cadastro:
    st.session_state.pop("ga_terreno_processado", None)

# O painel fica abaixo do mapa; navegação, formulários e histórico permanecem
# integrados, sem abrir caixas de diálogo e sem enviar todas as fichas ao navegador.
painel = st.session_state.get("ga_painel") or {}
if painel.get("tipo") == "ficha" and painel.get("id"):
    _painel_ativo(painel["id"])
elif painel.get("tipo") == "novo":
    _painel_novo(
        coordenadas=painel.get("coordenadas"),
        municipio_padrao=painel.get("municipio_padrao", ""),
    )
else:
    st.caption("Selecione um marcador de poço para consultar ou editar sua ficha abaixo do mapa.")
