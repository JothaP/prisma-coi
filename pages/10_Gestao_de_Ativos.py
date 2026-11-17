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
import pandas as pd
from folium.plugins import Fullscreen
from branca.element import Element, MacroElement
from jinja2 import Template
import streamlit as st
from streamlit_folium import st_folium

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from auth import exigir_acesso_modulo, pode_executar_acao, registrar_atividade
from gestao_ativos_backend import (
    CAMPOS_EDITAVEIS, CONFIRMACAO_RESET_ATIVOS, MAX_MARCADORES, criar_ativo,
    editar_ativo, historico_ativo, listar_ativos, listar_fichas_para_relatorio,
    municipios, obter_ativo, resetar_fichas_e_historico, validar_historico,
)
from gestao_ativos_relatorio import (
    SECOES as SECOES_RELATORIO, SITUACOES as SITUACOES_RELATORIO,
    STATUS_RELATORIO, STATUS_FECHAMENTOS_RELATORIO,
    gerar_pontos_atencao, preparar_itens_relatorio, preparar_fechamentos_relatorio,
)
from gestao_ativos_fechamentos import (
    STATUS_FECHAMENTO, LIMITE_MARCADORES_FECHAMENTOS,
    listar_fechamentos, listar_fechamentos_relatorio, municipios_fechamentos,
    criar_fechamento, editar_fechamento, obter_fechamento,
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
    "em_observacao": "Em Observação",
    "inativo": "Inativo",
    "validar": "Validar",
}
# Uma única paleta para os marcadores, o popup e a legenda.
CORES_STATUS = {
    "operando": "#2563EB",       # Azul
    "parado": "#EF4444",         # Vermelho
    "em_manutencao": "#FACC15",  # Amarelo
    "em_observacao": "#9333EA",  # Roxo
    "inativo": "#111111",        # Preto
    "validar": "#D1D5DB",        # Cinza claro
    "": "#94A3B8",               # Sem status: cinza claro
}
ROTULOS = {
    "planta": "Planta", "nome": "Nome", "municipio": "Município",
    "matricula": "Matrícula", "status": "Status",
    "ultima_os_operacao": "Última O.S. Operação",
    "data_ultima_os_operacao": "Data da O.S. Operação",
    "ultima_os_eletromecanica": "Última O.S. Eletromecânica",
    "data_ultima_os_eletromecanica": "Data da O.S. Eletromecânica",
    "protocolo_equatorial": "Protocolo Equatorial",
    "data_protocolo_equatorial": "Data — Protocolo",
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


@st.cache_data(ttl=35, show_spinner=False)
def _listar_reparos(municipio, bairro, pesquisa, status):
    return listar_fechamentos(municipio, bairro, pesquisa, status)


@st.cache_data(ttl=600, show_spinner=False)
def _municipios_reparos():
    return municipios_fechamentos()


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
        col1, col2 = st.columns(2)
        with col1:
            protocolo_equatorial = st.text_input(
                "Protocolo Equatorial",
                value=_texto(registro.get("protocolo_equatorial")))
        with col2:
            data_protocolo_equatorial = st.date_input(
                "Data — Protocolo",
                value=_data(registro.get("data_protocolo_equatorial")),
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
        "protocolo_equatorial": protocolo_equatorial,
        "data_protocolo_equatorial": (
            data_protocolo_equatorial.isoformat() if data_protocolo_equatorial else None
        ),
        "municipio": municipio, "observacao": observacao,
    }
    return enviar, campos, latitude, longitude


def _limpar_cache():
    _listar.clear()
    _municipios.clear()
    _listar_reparos.clear()
    _municipios_reparos.clear()
    # O st_folium preserva o mapa antigo entre reruns. Reconstrói os marcadores
    # somente após uma gravação confirmada, para refletir status e cores novos.
    st.session_state["ga_versao_mapa"] = st.session_state.get("ga_versao_mapa", 0) + 1


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


def _auditar_reparo(acao, registro):
    try:
        registrar_atividade(
            "9", acao, registro_id=registro.get("id"),
            detalhes=("Fechamento/reparo de rede: "
                      + str(registro.get("bairro") or "")[:100]),
        )
    except ErroPRISMA:
        st.session_state["ga_aviso_auditoria"] = (
            "O fechamento foi salvo, mas houve falha na auditoria geral. "
            "Informe a administração para verificar o registro."
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



def _icone_marcador_status(status):
    """Pin SVG colorido com gota branca, inclusive amarelo real."""
    cor = CORES_STATUS.get(_texto(status), CORES_STATUS[""])
    desenho = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="45" '
        'viewBox="0 0 34 45" role="img" aria-label="Ativo">'
        '<path d="M17 1C7.8 1 1.5 7.9 1.5 17.1C1.5 29.3 17 43 17 43'
        'S32.5 29.3 32.5 17.1C32.5 7.9 26.2 1 17 1Z" '
        f'fill="{cor}" stroke="#FFFFFF" stroke-width="1.6"/>'
        '<path d="M17 11C14.8 14.6 12.7 17.1 12.7 20.1'
        'a4.3 4.3 0 0 0 8.6 0C21.3 17.1 19.2 14.6 17 11Z" '
        'fill="#FFFFFF"/></svg>'
    )
    return folium.DivIcon(
        html=f'<div class="ga-pin-cor">{desenho}</div>',
        icon_size=(34, 45),
        icon_anchor=(17, 43),
        class_name="ga-icone-status",
    )


def _status_popup_html(registro, permitido):
    """Apresentação do formulário do Leaflet; gravação continua no Python."""
    planta = escape(_texto(registro.get("planta")) or "Ativo")
    nome = escape(_texto(registro.get("nome")))
    municipio = escape(_texto(registro.get("municipio")))
    atual = _texto(registro.get("status"))
    opcoes = []
    for codigo, cor in CORES_STATUS.items():
        if not codigo:
            continue
        selecionado = " checked" if atual == codigo else ""
        desabilitado = " disabled" if not permitido else ""
        opcoes.append(
            '<label class="ga-status-opcao">'
            f'<input type="radio" name="ga_status" value="{codigo}"{selecionado}{desabilitado}>'
            f'<span class="ga-status-cor" style="background:{cor}"></span>'
            f'<span>{escape(STATUS[codigo])}</span></label>'
        )
    botao = (
        '<button type="submit" class="ga-status-salvar">Atualizar status</button>'
        if permitido else
        '<div class="ga-status-sem-permissao">Somente leitura: edição não autorizada.</div>'
    )
    return (
        '<div class="ga-status-popup">'
        f'<div class="ga-status-titulo">{planta}</div>'
        f'<div class="ga-status-subtitulo">{nome or municipio}</div>'
        '<div class="ga-status-divisor"></div>'
        '<div class="ga-status-legenda">Status da atividade</div>'
        f'<form class="ga-status-form" data-ativo="{escape(str(registro["id"]))}" '
        f'data-atual="{escape(atual)}">'
        + "".join(opcoes)
        + f'<div class="ga-status-atual">Atual: {escape(STATUS.get(atual, "Não informado"))}</div>'
        + '<div class="ga-status-mensagem" role="status"></div>'
        + botao + '</form></div>'
    )


ESTILO_STATUS_NO_MAPA = """
<style>
.leaflet-marker-icon.ga-icone-status {
    background: transparent !important;
    border: 0 !important;
    overflow: visible !important;
}
.ga-pin-cor {width:34px;height:45px;filter:drop-shadow(0 2px 2px rgba(0,0,0,.28));}
.ga-pin-cor svg {display:block;overflow:visible;}
.ga-status-popup {min-width:255px;max-width:340px;color:#1d2c43;
    font-family:Segoe UI,Arial,sans-serif;font-size:13px;}
.ga-status-titulo {font-weight:800;font-size:17px;}
.ga-status-subtitulo {font-size:12px;color:#64748b;margin:3px 0;}
.ga-status-divisor {height:1px;background:#e2e8f0;margin:12px 0;}
.ga-status-legenda {font-weight:700;font-size:14px;margin-bottom:8px;}
.ga-status-opcao {display:flex;align-items:center;gap:11px;padding:9px;
    border-radius:8px;cursor:pointer;font-weight:600;}
.ga-status-opcao:has(input:checked) {background:#e8f0ff;color:#1453bb;}
.ga-status-opcao:has(input:disabled) {cursor:default;opacity:.75;}
.ga-status-opcao input {width:17px;height:17px;margin:0;accent-color:#1666e3;}
.ga-status-cor {width:17px;height:17px;border-radius:50%;flex-shrink:0;}
.ga-status-atual {color:#66778c;font-size:11px;margin:8px 2px;}
.ga-status-mensagem {font-size:11px;color:#b45309;}
.ga-status-salvar {background:#1765e9;color:white;border:0;border-radius:8px;
    padding:11px 12px;width:100%;font:700 13px Segoe UI,Arial,sans-serif;
    margin-top:8px;cursor:pointer;}
.ga-status-salvar:disabled {opacity:.65;cursor:progress;}
.ga-status-sem-permissao {font-size:11px;color:#66778c;margin-top:8px;}
.leaflet-popup-content:has(.ga-status-popup) {margin:12px 14px;}
</style>
"""


class PonteStatusFolium(MacroElement):
    """Entrega a seleção do popup ao componente Streamlit sem chamadas REST no navegador.

    O servidor continua responsável por validar permissão, versão e gravação.
    """
    def __init__(self):
        super().__init__()
        self._name = "PonteStatusFolium"
        self._template = Template(r"""
{% macro script(this, kwargs) %}
(function() {
    var mapa = {{this._parent.get_name()}};

    // O listener é instalado no container do mapa no carregamento,
    // antes de qualquer popup ser aberto. "popupopen" pode preceder a
    // disponibilidade completa do HTML interno; nesse caso o formulário
    // não recebia o listener e o primeiro clique não era processado.
    mapa.getContainer().addEventListener("submit", function(evento) {
        var form = evento.target;
        if (!form || !form.matches || !form.matches(".ga-status-form")) return;
        evento.preventDefault();
        evento.stopPropagation();

        var msg = form.querySelector(".ga-status-mensagem");
        var radio = form.querySelector('input[name="ga_status"]:checked');
        if (!radio) {
            if (msg) msg.textContent = "Selecione um status.";
            return;
        }
        if (radio.value === form.dataset.atual) {
            if (msg) msg.textContent = "O status selecionado já é o atual.";
            return;
        }
        if (["operando","parado","em_manutencao","em_observacao","inativo","validar"].indexOf(radio.value) < 0) {
            if (msg) msg.textContent = "Status inválido.";
            return;
        }
        if (form.dataset.enviando === "1") return;

        // Leaflet conserva a origem do popup atual, mesmo se o formulário
        // ainda não estava presente quando ocorreu "popupopen".
        var popup = mapa._popup;
        var marcador = (form.__prismaMarcador || (popup && popup._source));
        if (!marcador || !marcador.getLatLng) {
            if (msg) msg.textContent = "Não foi possível identificar o ativo. Reabra o marcador.";
            return;
        }
        var token = String(Date.now())+"_"+Math.random().toString(36).slice(2,10);
        var comando = "PRISMA_STATUS="+form.dataset.ativo+"|"+radio.value+"|"+token;
        var dados = window.__GLOBAL_DATA__;
        var ponte = window.Streamlit;
        try {
            if (dados && ponte && typeof ponte.setComponentValue === "function") {
                dados.last_object_clicked = marcador.getLatLng();
                dados.last_object_clicked_count = (Number(dados.last_object_clicked_count) || 0) + 1;
                dados.last_object_clicked_tooltip = comando;
                var retorno = {
                    last_clicked: dados.lat_lng_clicked || null,
                    last_object_clicked_count: dados.last_object_clicked_count,
                    last_object_clicked_tooltip: comando
                };
                // Mantém o estado sincronizado com o debounce interno do
                // streamlit-folium; não restaura a tooltip antes da captura.
                dados.previous_data = retorno;
                ponte.setComponentValue(retorno);
            } else if (marcador.getTooltip && marcador.getTooltip()) {
                marcador.setTooltipContent(comando);
                marcador.fire("click", {
                    latlng: marcador.getLatLng(), sourceTarget: marcador
                });
            } else {
                if (msg) msg.textContent = "Não foi possível comunicar com o mapa. Reabra o marcador.";
                return;
            }
        } catch (erro) {
            form.dataset.enviando = "0";
            if (msg) msg.textContent = "Falha no envio. Tente novamente.";
            return;
        }
        form.dataset.enviando = "1";
        var botao = form.querySelector(".ga-status-salvar");
        if (botao) botao.disabled = true;
        if (msg) msg.textContent = "Enviando atualização ao PRISMA...";
    }, true);

    // A abertura do popup cuida apenas da propagação do clique e,
    // quando possível, associa o marcador ao formulário.
    mapa.on("popupopen", function(e) {
        var el = e.popup && e.popup.getElement();
        if (!el) return;
        L.DomEvent.disableClickPropagation(el);
        L.DomEvent.disableScrollPropagation(el);
        var form = el.querySelector(".ga-status-form");
        if (form) form.__prismaMarcador = e.popup._source;
    });
})();
{% endmacro %}
""")


def _painel_ativo(ativo_id, registro=None):
    if registro is None:
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


def _painel_fechamento(registro=None, coordenadas=None, municipio_padrao="",
                       *, modal=False, cidades_disponiveis=()):
    """Ficha 🚧: três linhas e coordenadas editáveis no cadastro.

    No botão Novo Fechamento, o mesmo formulário abre em janela modal;
    pelo clique no mapa, continua disponível no painel inferior.
    """
    novo = registro is None
    registro = registro or {}
    bairro_atual = _texto(registro.get("bairro"))
    titulo = ("🚧 Cadastrar fechamento / reparo de rede" if novo
              else "🚧 Ficha do fechamento / reparo — " + bairro_atual)
    # No dialog, evitar expander aninhado e manter a ficha aberta.
    contexto = st.container() if modal else st.expander(titulo, expanded=True)
    with contexto:
        if novo:
            lat, lon = coordenadas or (None, None)
        else:
            lat, lon = registro.get("latitude"), registro.get("longitude")
        municipio_reparo = _texto(registro.get("municipio") or municipio_padrao)
        if modal:
            st.caption("Preencha a ficha e informe a posição do 🚧 no mapa.")
            if st.button("Cancelar", key="ga_cancelar_modal_fechamento"):
                st.rerun()
        else:
            if lat is not None and lon is not None:
                st.caption(
                    f"Município: {municipio_reparo} · "
                    f"Localização: {float(lat):.7f}, {float(lon):.7f}"
                )
            else:
                st.caption("Informe latitude e longitude para posicionar o 🚧.")
            st.button("Fechar ficha 🚧", key="ga_fechar_ficha_reparo",
                      on_click=_fechar_painel)
        with st.form("ga_form_fechamento_" + ("novo_modal" if modal else
                                             "novo" if novo else str(registro["id"]))):
            if novo and modal:
                opcoes_municipio = sorted({
                    str(nome).strip().upper() for nome in cidades_disponiveis
                    if str(nome).strip() and str(nome).strip().lower() != "todos"
                } | {"TERESINA"})
                municipio_reparo = st.selectbox(
                    "Município", opcoes_municipio,
                    index=(opcoes_municipio.index(municipio_reparo.upper())
                           if municipio_reparo.upper() in opcoes_municipio else
                           opcoes_municipio.index("TERESINA")),
                    key="ga_municipio_novo_fechamento",
                )
            # Primeira linha: Líder e Equipe.
            c1, c2 = st.columns(2)
            with c1:
                lider = st.text_input("Líder", value=_texto(registro.get("lider")),
                                      max_chars=160)
            with c2:
                equipe = st.text_input("Equipe", value=_texto(registro.get("equipe")),
                                       max_chars=160)
            # Segunda linha: Matrícula, O.S. e Bairro.
            c1, c2, c3 = st.columns(3)
            with c1:
                matricula_reparo = st.text_input(
                    "Matrícula", value=_texto(registro.get("matricula")), max_chars=80)
            with c2:
                os_reparo = st.text_input(
                    "O.S.", value=_texto(registro.get("os")), max_chars=80)
            with c3:
                bairro_reparo = st.text_input(
                    "Bairro", value=_texto(registro.get("bairro")), max_chars=160)
            # Terceira linha: Data e Status.
            c1, c2 = st.columns(2)
            with c1:
                data_reparo = st.date_input(
                    "Data", value=_data(registro.get("data")) or datetime.now(FUSO).date(),
                    format="DD/MM/YYYY",
                )
            with c2:
                codigos = list(STATUS_FECHAMENTO)
                atual = _texto(registro.get("status"))
                status_reparo = st.selectbox(
                    "Status", codigos,
                    index=codigos.index(atual) if atual in codigos else 0,
                    format_func=lambda codigo: STATUS_FECHAMENTO[codigo][0],
                )
            # Campos geográficos extras ao final, sem alterar as três linhas
            # da ficha operacional. Vazios até o usuário informar a posição;
            # nunca cadastrar inadvertidamente no centro do mapa.
            if novo:
                c1, c2 = st.columns(2)
                with c1:
                    latitude_reparo = st.text_input(
                        "Latitude", value=str(lat) if lat is not None else "",
                        placeholder="Ex.: -5,0890000", key=(
                            "ga_fechamento_lat_modal" if modal else
                            "ga_fechamento_lat_mapa"),
                    )
                with c2:
                    longitude_reparo = st.text_input(
                        "Longitude", value=str(lon) if lon is not None else "",
                        placeholder="Ex.: -42,8010000", key=(
                            "ga_fechamento_lon_modal" if modal else
                            "ga_fechamento_lon_mapa"),
                    )
            enviar = st.form_submit_button(
                "Cadastrar fechamento 🚧" if novo else "Salvar fechamento 🚧",
                type="primary", use_container_width=True,
                disabled=not pode_executar_acao("9", "criar" if novo else "editar"),
            )
        if enviar:
            campos_reparo = {
                "lider": lider, "equipe": equipe,
                "matricula": matricula_reparo, "os": os_reparo,
                "bairro": bairro_reparo, "data": data_reparo,
                "status": status_reparo, "municipio": municipio_reparo,
            }
            try:
                if novo:
                    salvo = criar_fechamento(
                        campos_reparo, latitude_reparo, longitude_reparo
                    )
                    acao = "criar"
                else:
                    salvo = editar_fechamento(
                        registro["id"], campos_reparo, registro.get("atualizado_em")
                    )
                    acao = "editar"
                _auditar_reparo(acao, salvo)
                _limpar_cache()
                st.session_state["ga_painel"] = {
                    "tipo": "fechamento", "id": salvo["id"],
                }
                st.session_state["ga_mensagem"] = (
                    "Fechamento / reparo de rede salvo com sucesso."
                )
                st.rerun()
            except ErroPRISMA as exc:
                st.error(f"Não foi possível salvar o fechamento: {exc}")


@st.dialog("🚧 Novo fechamento / reparo de rede", width="large")
def _dialogo_novo_fechamento(cidades_disponiveis, municipio_padrao="TERESINA"):
    """Janela de cadastro diretamente no botão, independente do clique no mapa."""
    _painel_fechamento(
        municipio_padrao=municipio_padrao,
        modal=True,
        cidades_disponiveis=cidades_disponiveis,
    )


st.title("Gestão de Ativos")
st.caption("PRISMA · Cadastro operacional georreferenciado no Supabase")
col_voltar, col_novo = st.columns([3, 2])
with col_voltar:
    if st.button("← Menu principal", key="ga_voltar"):
        st.switch_page("app.py")

try:
    HISTORICO_DISPONIVEL = validar_historico()
    cidades = _municipios()
    cidades_reparos = _municipios_reparos()
except ErroPRISMA as exc:
    st.error(f"Não foi possível acessar o cadastro de ativos: {exc}")
    st.stop()

with col_novo:
    b_poco, b_reparo = st.columns(2)
    with b_poco:
        if st.button("＋ Novo poço", use_container_width=True,
                     disabled=not pode_executar_acao("9", "criar") or not HISTORICO_DISPONIVEL):
            st.session_state["ga_painel"] = {
                "tipo": "novo", "coordenadas": None, "municipio_padrao": "TERESINA",
            }
    with b_reparo:
        if st.button("🚧 Novo fechamento", use_container_width=True,
                     disabled=not pode_executar_acao("9", "criar")):
            # Abre formulário completo sem depender de clique prévio no mapa.
            st.session_state["ga_modo_cadastro"] = False
            st.session_state["ga_modo_fechamento"] = False
            municipio_atual = st.session_state.get("ga_municipio_reparo")
            municipio_padrao_dialogo = (
                municipio_atual if municipio_atual and municipio_atual != "Todos"
                else "TERESINA"
            )
            _dialogo_novo_fechamento(
                [*cidades, *cidades_reparos],
                municipio_padrao=municipio_padrao_dialogo,
            )

if not HISTORICO_DISPONIVEL:
    st.warning("Histórico do Supabase não localizado. Consulta liberada; criação e edição bloqueadas.")

# Recurso administrativo do Módulo 9: não aparece para usuários ou operadores.
# A validação de admin e permissão também ocorre no backend e dentro do banco.
if st.session_state.get("perfil") == "admin":
    with st.expander("Administração — Resetar dados das fichas e histórico", expanded=False):
        st.warning(
            "Esta operação atua em TODOS os ativos do módulo. Remove o status, "
            "as últimas O.S. de Operação e Eletromecânica, suas datas, "
            "o Protocolo Equatorial e a data, as observações e o histórico "
            "das fichas. Preserva todos os ativos, suas plantas, nomes, "
            "matrículas, municípios, coordenadas e dados cadastrais de origem. "
            "O registro de auditoria administrativa do reset será preservado. "
            "A exclusão do histórico não pode ser desfeita pela interface."
        )
        with st.form("ga_confirmacao_reset_geral"):
            st.caption(
                "Confirme que deseja limpar todas as fichas e o histórico "
                "do módulo. Digite exatamente:"
            )
            st.code(CONFIRMACAO_RESET_ATIVOS)
            frase_reset = st.text_input(
                "Frase de confirmação",
                value="",
                key="ga_frase_reset_geral",
                placeholder=CONFIRMACAO_RESET_ATIVOS,
            )
            aceite_reset = st.checkbox(
                "Estou ciente de que os dados operacionais e o histórico "
                "serão apagados de todos os ativos."
            )
            enviar_reset = st.form_submit_button(
                "Resetar fichas e histórico",
                type="secondary",
                disabled=not HISTORICO_DISPONIVEL
                or not pode_executar_acao("9", "excluir"),
            )
        if enviar_reset:
            if not aceite_reset or frase_reset != CONFIRMACAO_RESET_ATIVOS:
                st.error("Marque a confirmação e digite a frase exatamente como apresentada.")
            else:
                try:
                    with st.spinner("Limpando as fichas e o histórico com auditoria..."):
                        resumo_reset = resetar_fichas_e_historico(frase_reset)
                    _limpar_cache()
                    st.session_state.pop("ga_painel", None)
                    st.session_state.pop("ga_ultimo_status_processado", None)
                    st.session_state.pop("ga_evento_marcador_processado", None)
                    st.session_state["ga_mensagem"] = (
                        f"Reset concluído: {resumo_reset['ativos_preservados']} ativos "
                        f"preservados, {resumo_reset['fichas_limpas']} fichas limpas e "
                        f"{resumo_reset['historicos_excluidos']} registros do histórico "
                        "apagados. Auditoria administrativa registrada."
                    )
                    st.rerun()
                except ErroPRISMA as exc:
                    st.error(f"Reset não realizado: {exc}")

# Consulta apenas leitura e revisão em memória. Nenhuma classificação
# automática dos status e nenhuma gravação em prisma_ativos.
with st.expander("📋 Gerar relatório — Pontos de Atenção", expanded=False):
    st.caption(
        "Relatório dividido pelo status registrado na ficha: cada grupo e "
        "marcador utilizam a cor correspondente no mapa. Situação (Pendente / "
        "Em andamento) permanece um campo independente e não altera o status "
        "cadastrado. As edições feitas aqui valem apenas para o relatório."
    )
    cidades_relatorio = sorted(
        {str(c).strip().upper() for c in [*cidades, *cidades_reparos]
         if str(c).strip()}
    )
    if not cidades_relatorio:
        st.info("Nenhum município cadastrado para gerar relatório.")
    else:
        col_cidade_relatorio, col_data_relatorio = st.columns(2)
        with col_cidade_relatorio:
            indice_cidade = (
                cidades_relatorio.index("TERESINA")
                if "TERESINA" in cidades_relatorio else 0
            )
            municipio_relatorio = st.selectbox(
                "Município do relatório", cidades_relatorio, index=indice_cidade,
                key="ga_relatorio_municipio",
            )
        with col_data_relatorio:
            data_relatorio = st.date_input(
                "Data de referência", value=datetime.now(FUSO).date(),
                format="DD/MM/YYYY", key="ga_relatorio_data",
            )
        incluir_operando = st.checkbox(
            "Mostrar também ativos com status Operando para seleção manual",
            value=False, key="ga_relatorio_mostrar_operando",
            help="Ativos Operando ficam desmarcados por padrão e não são "
                 "incluídos no relatório sem seleção expressa.",
        )
        if st.button(
            "Carregar fichas", key="ga_relatorio_carregar",
            help="Consulta as fichas do município; não modifica nenhum registro.",
        ):
            try:
                with st.spinner("Consultando poços e fechamentos no Supabase..."):
                    registros_relatorio = listar_fichas_para_relatorio(municipio_relatorio)
                    registros_reparos_relatorio = listar_fechamentos_relatorio(
                        municipio_relatorio
                    )
                linhas_relatorio = preparar_itens_relatorio(
                    registros_relatorio, incluir_operando=incluir_operando
                )
                linhas_reparos_relatorio = preparar_fechamentos_relatorio(
                    registros_reparos_relatorio
                )
                st.session_state["ga_relatorio_fichas"] = {
                    "municipio": municipio_relatorio,
                    "incluir_operando": incluir_operando,
                    "itens": linhas_relatorio,
                    "fechamentos": linhas_reparos_relatorio,
                }
                st.session_state["ga_relatorio_versao"] = (
                    st.session_state.get("ga_relatorio_versao", 0) + 1
                )
                st.session_state.pop("ga_relatorio_texto", None)
                if not linhas_relatorio and not linhas_reparos_relatorio:
                    st.info("Nenhuma ficha de poço ou fechamento disponível para esse município.")
            except ErroPRISMA as exc:
                st.error(f"Não foi possível consultar as fichas: {exc}")

        fichas_revisao = st.session_state.get("ga_relatorio_fichas")
        if fichas_revisao and (
            fichas_revisao["municipio"] != municipio_relatorio
            or fichas_revisao["incluir_operando"] != incluir_operando
        ):
            st.warning(
                "Os filtros foram alterados. Clique em Carregar fichas "
                "novamente para montar a lista correspondente."
            )
        elif fichas_revisao and (
            fichas_revisao["itens"] or fichas_revisao.get("fechamentos")
        ):
            st.caption(
                f"{len(fichas_revisao['itens'])} ficha(s) de poços e "
                f"{len(fichas_revisao.get('fechamentos', []))} fechamento(s) disponíveis. "
                "Revise os status, descrições e itens selecionados. "
                "Os ajustes no editor não alteram as fichas do Supabase."
            )
            with st.form("ga_relatorio_editor_form"):
                st.markdown("**💧 Poços — agrupados por status**")
                dados_editor = st.data_editor(
                    pd.DataFrame(fichas_revisao["itens"], columns=[
                        "Incluir", "Ordem", "Status no relatório", "Seção", "Situação",
                        "Poço / ativo", "Observação", "O.S.", "Nota", "Planta de origem"
                    ]),
                    key=f"ga_relatorio_editor_{st.session_state.get('ga_relatorio_versao', 0)}",
                    use_container_width=True, hide_index=True,
                    num_rows="dynamic",
                    disabled=["Planta de origem"],
                    column_config={
                        "Incluir": st.column_config.CheckboxColumn("Incluir"),
                        "Ordem": st.column_config.NumberColumn(
                            "Posição", min_value=0, step=1, format="%d"),
                        "Status no relatório": st.column_config.SelectboxColumn(
                            "Grupo por status",
                            options=list(STATUS_RELATORIO), required=True,
                            help="Preenchido com o status da ficha. Ajustes manuais "
                                 "aqui não alteram o cadastro nem o mapa."
                        ),
                        "Seção": st.column_config.SelectboxColumn(
                            "Seção", options=list(SECOES_RELATORIO), required=True),
                        "Situação": st.column_config.SelectboxColumn(
                            "Situação do relatório", options=list(SITUACOES_RELATORIO),
                            required=True),
                        "Poço / ativo": st.column_config.TextColumn(
                            "Poço / ativo", required=True),
                        "Observação": st.column_config.TextColumn("Descrição da ocorrência"),
                        "O.S.": st.column_config.TextColumn("Ordem de serviço"),
                        "Nota": st.column_config.TextColumn("Nota (opcional)"),
                        "Planta de origem": st.column_config.TextColumn("Planta (referência)"),
                    },
                )
                st.markdown("**🚧 Fechamentos / reparos de rede — por status**")
                dados_reparos_editor = st.data_editor(
                    pd.DataFrame(fichas_revisao.get("fechamentos", []), columns=[
                        "Incluir", "Ordem", "Status do fechamento",
                        "Líder", "Equipe", "Matrícula", "O.S.", "Bairro", "Data",
                    ]),
                    key=f"ga_relatorio_reparos_{st.session_state.get('ga_relatorio_versao', 0)}",
                    use_container_width=True, hide_index=True,
                    num_rows="dynamic",
                    column_config={
                        "Incluir": st.column_config.CheckboxColumn("Incluir"),
                        "Ordem": st.column_config.NumberColumn(
                            "Posição", min_value=0, step=1, format="%d"),
                        "Status do fechamento": st.column_config.SelectboxColumn(
                            "Status 🚧", options=list(STATUS_FECHAMENTOS_RELATORIO),
                            required=True,
                            help="Agrupamento no relatório; não altera status cadastrado.",
                        ),
                        "Líder": st.column_config.TextColumn("Líder"),
                        "Equipe": st.column_config.TextColumn("Equipe"),
                        "Matrícula": st.column_config.TextColumn("Matrícula"),
                        "O.S.": st.column_config.TextColumn("O.S."),
                        "Bairro": st.column_config.TextColumn("Bairro"),
                        "Data": st.column_config.TextColumn("Data (AAAA-MM-DD)"),
                    },
                )
                montar_relatorio = st.form_submit_button(
                    "Gerar texto do relatório", type="primary",
                    use_container_width=True,
                )
            if montar_relatorio:
                registros_editados = dados_editor.to_dict("records")
                reparos_editados = dados_reparos_editor.to_dict("records")
                selecionados_pocos = any(
                    item.get("Incluir") is True
                    and str(item.get("Poço / ativo") or "").strip()
                    for item in registros_editados
                )
                selecionados_reparos = any(
                    item.get("Incluir") is True
                    and str(item.get("Bairro") or "").strip()
                    for item in reparos_editados
                )
                if not selecionados_pocos and not selecionados_reparos:
                    st.warning("Selecione pelo menos um poço ou fechamento identificado.")
                else:
                    st.session_state["ga_relatorio_texto"] = gerar_pontos_atencao(
                        municipio_relatorio, data_relatorio, registros_editados,
                        fechamentos=reparos_editados,
                    )

        if (fichas_revisao
                and fichas_revisao["municipio"] == municipio_relatorio
                and fichas_revisao["incluir_operando"] == incluir_operando
                and st.session_state.get("ga_relatorio_texto")):
            st.markdown("**Texto gerado — pronto para copiar e compartilhar**")
            texto_gerado = st.text_area(
                "Relatório final (editável antes de copiar)",
                key="ga_relatorio_texto", height=420,
            )
            nome_arquivo_relatorio = (
                "pontos_atencao_" + re.sub(r"[^a-z0-9]+", "_",
                    municipio_relatorio.lower()).strip("_")
                + f"_{data_relatorio:%Y%m%d}.txt"
            )
            st.download_button(
                "Baixar relatório (.txt)",
                data=texto_gerado.encode("utf-8-sig"),
                file_name=nome_arquivo_relatorio, mime="text/plain",
                key="ga_relatorio_baixar",
            )

if mensagem := st.session_state.pop("ga_mensagem", None):
    st.success(mensagem)
if erro_status := st.session_state.pop("ga_erro_status", None):
    st.error(erro_status)
if aviso := st.session_state.pop("ga_aviso_auditoria", None):
    st.warning(aviso)

st.markdown("**Filtros — Poços**")
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

st.markdown("**Filtros — 🚧 Fechamentos / reparos de rede**")
cidades_filtragem_reparo = sorted({
    str(c).strip().upper() for c in [*cidades, *cidades_reparos] if str(c).strip()
})
opcoes_reparo_cidade = ["Todos"] + cidades_filtragem_reparo
cidade_padrao_reparo = (
    opcoes_reparo_cidade.index("TERESINA")
    if "TERESINA" in opcoes_reparo_cidade else 0
)
fr1, fr2, fr3, fr4 = st.columns([2.1, 2.2, 1.6, 1.6])
with fr1:
    municipio_reparo_filtro = st.selectbox(
        "Município • Fechamentos", opcoes_reparo_cidade,
        index=cidade_padrao_reparo, key="ga_municipio_reparo",
    )
with fr2:
    bairro_reparo_filtro = st.text_input(
        "Bairro • Fechamentos", placeholder="Pesquisar bairro",
        key="ga_bairro_reparo_filtro",
    )
with fr3:
    codigos_reparo = ["Todos"] + list(STATUS_FECHAMENTO)
    status_reparo_filtro = st.selectbox(
        "Status • Fechamentos", codigos_reparo,
        format_func=lambda c: "Todos" if c == "Todos" else STATUS_FECHAMENTO[c][0],
        key="ga_status_reparo_filtro",
    )
with fr4:
    pesquisa_reparo_filtro = st.text_input(
        "Buscar fechamento", placeholder="O.S., matrícula, líder ou equipe",
        key="ga_pesquisa_reparo_filtro",
    )

pode_criar = pode_executar_acao("9", "criar") and HISTORICO_DISPONIVEL
col_rotulos, col_cadastro, col_reparo, col_camadas = st.columns([1.2, 1.65, 1.85, 1.6])
with col_rotulos:
    mostrar_rotulos = st.toggle(
        "Exibir rótulos", value=False, key="ga_exibir_rotulos",
        help="Desligado por padrão para reduzir a quantidade de elementos renderizados no mapa.",
    )
with col_cadastro:
    modo_cadastro = st.toggle(
        "Cadastrar poço clicando no mapa", value=False,
        disabled=not pode_criar or st.session_state.get("ga_modo_fechamento", False),
        key="ga_modo_cadastro",
    )
with col_reparo:
    modo_fechamento = st.toggle(
        "Cadastrar 🚧 clicando no mapa", value=False,
        disabled=not pode_executar_acao("9", "criar") or modo_cadastro
                 or municipio_reparo_filtro == "Todos",
        key="ga_modo_fechamento",
    )
with col_camadas:
    exibir_pocos = st.checkbox("Exibir poços no mapa", value=True, key="ga_mostrar_pocos")
    exibir_reparos = st.checkbox("Exibir 🚧 no mapa", value=True, key="ga_mostrar_reparos")
if modo_fechamento:
    st.info("Clique em terreno livre no mapa para posicionar o marcador 🚧 e abrir a ficha.")
elif modo_cadastro:
    st.info("Clique em um local livre do mapa para abrir o formulário de um novo poço.")
else:
    st.caption(
        "Clique no marcador do poço para alterar seu status ou no 🚧 "
        "para consultar a ficha do fechamento/reparo."
    )

# Mesma legenda usada no pin e no formulário de atualização rápida.
legenda_status = "".join(
    '<span style="display:inline-flex;align-items:center;gap:5px;margin-right:16px;'
    'white-space:nowrap">'
    f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;'
    f'background:{CORES_STATUS[codigo]};border:1px solid #94a3b8"></span>'
    f'{escape(nome)}</span>'
    for codigo, nome in STATUS.items() if codigo
)
st.markdown(
    '<div style="font-size:12px;margin:0 0 8px 0">' + legenda_status + '</div>',
    unsafe_allow_html=True,
)

try:
    registros = _listar(municipio, pesquisa, status) if exibir_pocos else []
    reparos_retornados = (
        _listar_reparos(
            municipio_reparo_filtro, bairro_reparo_filtro,
            pesquisa_reparo_filtro, status_reparo_filtro,
        )
        if exibir_reparos else []
    )
except ErroPRISMA as exc:
    st.error(f"Erro ao buscar ativos ou fechamentos: {exc}")
    st.stop()

limitado = len(registros) > MAX_MARCADORES
ativos = registros[:MAX_MARCADORES]
reparos_limitados = len(reparos_retornados) > LIMITE_MARCADORES_FECHAMENTOS
reparos_visiveis = reparos_retornados[:LIMITE_MARCADORES_FECHAMENTOS]
st.caption(
    f"{len(ativos)} poços · {len(reparos_visiveis)} fechamentos/reparos 🚧 no mapa"
    + (" · Refine os filtros de poços para exibir mais." if limitado else "")
    + (" · Refine os filtros de fechamentos para exibir mais."
       if reparos_limitados else "")
)

coordenadas = [
    (float(r["latitude"]), float(r["longitude"]))
    for r in [*ativos, *reparos_visiveis]
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
mapa.get_root().header.add_child(Element(ESTILO_STATUS_NO_MAPA))
mapa.add_child(PonteStatusFolium())

pode_editar_status = pode_executar_acao("9", "editar") and HISTORICO_DISPONIVEL

# Não variar o JavaScript/HTML do mapa em função do painel selecionado.
# streamlit-folium 0.27.4 calcula a identidade pelo hash do JS gerado,
# MESMO quando o componente possui "key". A opção "show=..." no popup
# recriava o iframe logo após clicar no marcador, fazendo o submit seguinte
# se perder quando o usuário agia antes do fim da montagem do mapa.
# A abertura do popup é controlada pelo próprio Leaflet, no clique.
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
        popup=folium.Popup(
            _status_popup_html(registro, pode_editar_status), max_width=390,
        ),
        icon=_icone_marcador_status(registro.get("status")),
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

# Marcadores 🚧 ficam em cadastro e filtros próprios, sem tocar nas fichas
# e nas cores dos poços. O aro do ícone segue o status do fechamento.
for reparo in reparos_visiveis:
    if reparo.get("latitude") is None or reparo.get("longitude") is None:
        continue
    lat, lon = float(reparo["latitude"]), float(reparo["longitude"])
    codigo_reparo = str(reparo.get("status") or "")
    rotulo_status, cor_reparo, _ = STATUS_FECHAMENTO.get(
        codigo_reparo, ("Não informado", "#94A3B8", "⚪")
    )
    bairro_reparo = escape(_texto(reparo.get("bairro")))
    tooltip_reparo = (
        f"<span style='display:none'>PRISMA_FECHAMENTO={reparo['id']}</span>"
        f"🚧 <b>Fechamento / reparo</b> · {bairro_reparo}"
        f"<br><b>Status:</b> {escape(rotulo_status)}"
    )
    detalhes_reparo = (
        ("Líder", "lider"), ("Equipe", "equipe"),
        ("Matrícula", "matricula"), ("O.S.", "os"),
        ("Bairro", "bairro"), ("Data", "data"),
    )
    popup_reparo = (
        "<div style='min-width:240px;font:13px Segoe UI,Arial,sans-serif'>"
        "<b>🚧 Fechamento / reparo de rede</b><hr>"
        + "".join(
            f"<div><b>{titulo}:</b> {escape(_texto(reparo.get(campo)) or '—')}</div>"
            for titulo, campo in detalhes_reparo
        )
        + f"<div><b>Status:</b> {escape(rotulo_status)}</div>"
        + "<small>Para editar, consulte a ficha abaixo do mapa.</small></div>"
    )
    folium.Marker(
        [lat, lon],
        tooltip=folium.Tooltip(tooltip_reparo, sticky=True),
        popup=folium.Popup(popup_reparo, max_width=390),
        icon=folium.DivIcon(
            html=(
                "<div style='width:39px;height:39px;display:flex;"
                "align-items:center;justify-content:center;font-size:23px;"
                "border-radius:50%;background:#fff;border:3px solid "
                + cor_reparo
                + ";box-shadow:0 2px 7px #0005'>🚧</div>"
            ),
            icon_size=(45, 45), icon_anchor=(22, 22),
            class_name="ga-icone-fechamento",
        ),
        z_index_offset=400,
    ).add_to(mapa)

if len(coordenadas) > 1:
    mapa.fit_bounds(coordenadas, padding=(30, 30), max_zoom=14)

resultado = st_folium(
    mapa, width="100%", height=600,
    key=f"ga_mapa_interativo_{st.session_state.get('ga_versao_mapa', 0)}",
    returned_objects=["last_clicked", "last_object_clicked_count",
                      "last_object_clicked_tooltip"],
)
if resultado:
    clicks = int(resultado.get("last_object_clicked_count") or 0)
    tooltip = str(resultado.get("last_object_clicked_tooltip") or "")
    evento_marcador = (clicks, tooltip)
    sinal_status = re.fullmatch(
        r"PRISMA_STATUS=([0-9a-fA-F-]{36})\|(operando|parado|em_manutencao|em_observacao|inativo|validar)"
        r"\|([0-9]{10,16}_[a-z0-9]{4,12})",
        tooltip,
    )
    if sinal_status:
        if tooltip != st.session_state.get("ga_ultimo_status_processado"):
            # Impede executar novamente o mesmo evento em reruns do Streamlit.
            st.session_state["ga_ultimo_status_processado"] = tooltip
            ativo_id, novo_status, _ = sinal_status.groups()
            visiveis = {str(item.get("id")) for item in ativos}
            try:
                if not pode_editar_status:
                    raise ErroPRISMA("Você não tem permissão para atualizar o status.")
                if ativo_id not in visiveis:
                    raise ErroPRISMA("O ativo não está no mapa atual. Atualize a consulta.")
                atual = obter_ativo(ativo_id)
                if not atual:
                    raise ErroPRISMA("O ativo não foi encontrado.")
                st.session_state["ga_painel"] = {"tipo": "ficha", "id": ativo_id}
                if (atual.get("status") or None) != novo_status:
                    campos = {campo: atual.get(campo) for campo in CAMPOS_EDITAVEIS}
                    campos["status"] = novo_status
                    salvo = editar_ativo(ativo_id, campos, atual.get("atualizado_em"))
                    _auditar("editar", salvo)
                    _limpar_cache()
                    st.session_state["ga_mensagem"] = (
                        "Status atualizado para " + STATUS[novo_status] +
                        ". Alteração registrada no histórico."
                    )
                else:
                    st.session_state["ga_mensagem"] = "Este status já estava atualizado."
            except ErroPRISMA as exc:
                st.session_state["ga_erro_status"] = str(exc)
            st.rerun()
    else:
        achado = re.search(r"PRISMA_ID=([0-9a-fA-F-]{36})", tooltip)
        achado_reparo = re.search(
            r"PRISMA_FECHAMENTO=([0-9a-fA-F-]{36})", tooltip
        )
        novo_click = (
            clicks > 0
            and evento_marcador != st.session_state.get("ga_evento_marcador_processado")
        )
        if novo_click and achado_reparo:
            st.session_state["ga_evento_marcador_processado"] = evento_marcador
            st.session_state["ga_painel"] = {
                "tipo": "fechamento", "id": achado_reparo.group(1),
            }
        elif novo_click and achado:
            st.session_state["ga_evento_marcador_processado"] = evento_marcador
            st.session_state["ga_painel"] = {"tipo": "ficha", "id": achado.group(1)}
        elif (modo_cadastro or modo_fechamento) and (ponto := resultado.get("last_clicked")):
            lat, lon = ponto.get("lat"), ponto.get("lng")
            if lat is not None and lon is not None:
                tipo_novo = "novo_fechamento" if modo_fechamento else "novo"
                chave = (tipo_novo, round(float(lat), 7), round(float(lon), 7))
                if chave != st.session_state.get("ga_terreno_processado"):
                    st.session_state["ga_terreno_processado"] = chave
                    st.session_state["ga_painel"] = {
                        "tipo": tipo_novo, "coordenadas": (lat, lon),
                        "municipio_padrao": (
                            municipio_reparo_filtro
                            if modo_fechamento else
                            municipio if municipio != "Todos" else ""
                        ),
                    }

if not modo_cadastro and not modo_fechamento:
    st.session_state.pop("ga_terreno_processado", None)

# O painel fica abaixo do mapa; navegação, formulários e histórico permanecem
# integrados, sem abrir caixas de diálogo e sem enviar todas as fichas ao navegador.
painel = st.session_state.get("ga_painel") or {}
if painel.get("tipo") == "ficha" and painel.get("id"):
    try:
        registro_selecionado = obter_ativo(painel["id"])
    except ErroPRISMA as exc:
        st.error(str(exc))
        registro_selecionado = None
    if registro_selecionado:
        _painel_ativo(painel["id"], registro=registro_selecionado)
    else:
        st.warning("O ativo selecionado não está disponível. Selecione outro marcador.")
elif painel.get("tipo") == "novo":
    _painel_novo(
        coordenadas=painel.get("coordenadas"),
        municipio_padrao=painel.get("municipio_padrao", ""),
    )
elif painel.get("tipo") == "fechamento" and painel.get("id"):
    try:
        fechamento_selecionado = obter_fechamento(painel["id"])
    except ErroPRISMA as exc:
        st.error(f"Não foi possível abrir o fechamento: {exc}")
        fechamento_selecionado = None
    if fechamento_selecionado:
        _painel_fechamento(fechamento_selecionado)
    else:
        st.warning("Fechamento não encontrado. Selecione outro marcador 🚧.")
elif painel.get("tipo") == "novo_fechamento":
    _painel_fechamento(
        coordenadas=painel.get("coordenadas"),
        municipio_padrao=painel.get("municipio_padrao", ""),
    )
else:
    st.caption("Selecione um marcador de poço ou 🚧 para consultar a ficha abaixo do mapa.")
