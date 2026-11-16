"""Interface experimental do Farol V3; não modifica Módulo 3 nem Supabase.

Entradas de O.S. são temporárias e restritas ao dia atual (Fortaleza).
Fichas e movimentações ficam em memória da sessão, com exportação/restauração
manual em JSON. NÃO constituem o histórico operacional de produção.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from html import escape as escape_html
import json
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from farol_v3_nucleo_teste import (
    preparar_os_do_dia, concentracoes_do_dia, eventos_pertinentes,
    ativos_proximos, pressao_proxima, normalizar, RAIO_PADRAO, MINIMO_OS,
)

FUSO = ZoneInfo("America/Fortaleza")
MAX_ARQUIVO_BYTES = 40 * 1024 * 1024
STATUS = ["Em análise", "Em acompanhamento", "Em observação", "Concluída"]
RAIZ = Path(__file__).resolve().parent
CHAVE_OPERACAO = "farol_v3_teste_operacao"
CHAVE_FICHAS = "farol_v3_teste_fichas"
CHAVE_COPIAS = "farol_v3_teste_copias"


def hoje():
    return datetime.now(FUSO).date()


def hora():
    return datetime.now(FUSO).strftime("%d/%m/%Y %H:%M")


def ler_arquivo(upload) -> pd.DataFrame:
    if upload is None:
        raise ValueError("Selecione primeiro um arquivo.")
    dados = upload.getvalue()
    if len(dados) > MAX_ARQUIVO_BYTES:
        raise ValueError("Arquivo maior que 40 MB. Exporte apenas as reclamações do dia.")
    if not dados:
        raise ValueError("O arquivo está vazio.")
    nome = str(upload.name).lower()
    if nome.endswith(".xlsx") or nome.endswith(".xls"):
        return pd.read_excel(BytesIO(dados), dtype=str).fillna("")
    if nome.endswith(".csv"):
        ultima = None
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                retorno = pd.read_csv(
                    BytesIO(dados), dtype=str, encoding=encoding,
                    sep=None, engine="python", on_bad_lines="error",
                )
                if len(retorno.columns) > 1:
                    return retorno.fillna("")
            except (UnicodeError, pd.errors.ParserError, ValueError) as erro:
                ultima = erro
        raise ValueError("Não foi possível ler as colunas do CSV.") from ultima
    raise ValueError("Formato não suportado. Utilize CSV ou XLSX.")


def exemplo_do_dia(dia):
    """Exemplo inteiramente fictício; nunca grava no sistema operacional."""
    linhas = []
    for i in range(5):
        linhas.append({
            "Nº da O.S": f"EXEMPLO-A-{i + 1}", "Matrícula": f"DEMO-{i + 1}",
            "Cidade": "Teresina", "Bairro": "Bairro A (fictício)",
            "Latitude": -5.090 + i * 0.00065,
            "Longitude": -42.800 + i * 0.00065,
            "Status OS": "Aberta", "Dt. Emissão": f"{dia:%d/%m/%Y} 08:00",
            "Serviço Executado": "Informação de falta de água", "Endereço": "Exemplo",
        })
    for i in range(3):
        linhas.append({
            "Nº da O.S": f"EXEMPLO-B-{i + 1}", "Matrícula": f"DEMO-B-{i + 1}",
            "Cidade": "Teresina", "Bairro": "Bairro B (fictício)",
            "Latitude": -5.120 + i * 0.00038,
            "Longitude": -42.820 + i * 0.00045,
            "Status OS": "Executada", "Dt. Emissão": f"{dia:%d/%m/%Y} 09:00",
            "Serviço Executado": "Informação de falta de água", "Endereço": "Exemplo",
        })
    return pd.DataFrame(linhas)


def processar(base: pd.DataFrame, dia, demonstracao=False):
    inicio_filtro = time.perf_counter()
    os_dia, estat = preparar_os_do_dia(base, dia)
    duracao_filtro = time.perf_counter() - inicio_filtro
    if os_dia.empty:
        raise ValueError(
            f"Nenhuma O.S. da data {dia:%d/%m/%Y} foi encontrada no arquivo. "
            "O V3 não aceita dados de outros dias."
        )
    inicio_agrupamento = time.perf_counter()
    concentracoes, pontos = concentracoes_do_dia(os_dia, dia, RAIO_PADRAO, MINIMO_OS)
    estat["tempo_filtro_s"] = round(duracao_filtro, 3)
    estat["tempo_agrupamento_s"] = round(time.perf_counter() - inicio_agrupamento, 3)
    return {
        "dia": dia.isoformat(), "demonstracao": demonstracao,
        "concentracoes": concentracoes, "pontos": pontos, "estatisticas": estat,
        "processado_em": hora(),
    }


def _mapa(concentracoes, pontos):
    geograficos = pontos.dropna(subset=["lat", "lon"])
    if geograficos.empty:
        return None
    centro = [float(geograficos["lat"].mean()), float(geograficos["lon"].mean())]
    mapa = folium.Map(
        location=centro, zoom_start=12, tiles="OpenStreetMap",
        prefer_canvas=True, control_scale=True,
    )
    fg_os = folium.FeatureGroup(name="Reclamações do dia", show=True)
    for reg in geograficos.itertuples():
        folium.CircleMarker(
            [float(reg.lat), float(reg.lon)], radius=4,
            color="#2563eb", weight=1, fill=True, fill_opacity=.75,
            tooltip=escape_html(f"O.S. {reg.os} · {reg.bairro} · {reg.status or 'Sem status'}"),
        ).add_to(fg_os)
    fg_os.add_to(mapa)
    fg_conc = folium.FeatureGroup(name="Concentrações", show=True)
    for item in concentracoes.to_dict("records"):
        lat, lon = item["latitude"], item["longitude"]
        folium.Circle(
            [lat, lon], radius=RAIO_PADRAO, color="#7c3aed",
            weight=2, fill=True, fill_opacity=.04,
        ).add_to(fg_conc)
        folium.Marker(
            [lat, lon],
            tooltip=escape_html(f"{item['codigo']} — {item['qtd_os']} O.S."),
            popup=folium.Popup(
                f"<b>{escape_html(item['codigo'])}</b>"
                f"<p>{escape_html(item['municipio'])} — "
                f"{item['qtd_os']} O.S.</p>",
                max_width=240,
            ),
            icon=folium.Icon(color="purple", icon="info-sign"),
        ).add_to(fg_conc)
    fg_conc.add_to(mapa)
    if len(geograficos) > 1:
        mapa.fit_bounds([
            [float(geograficos["lat"].min()), float(geograficos["lon"].min())],
            [float(geograficos["lat"].max()), float(geograficos["lon"].max())],
        ], padding=(25, 25), max_zoom=14)
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def _visao_panorama(op):
    concentracoes, pontos = op["concentracoes"], op["pontos"]
    fichas = st.session_state[CHAVE_FICHAS]
    estat = op["estatisticas"]
    colunas = st.columns(4)
    colunas[0].metric("Reclamações do dia", estat["validos"])
    colunas[1].metric("Concentrações", len(concentracoes))
    colunas[2].metric("O.S. sem coordenadas", estat["sem_coordenadas"])
    colunas[3].metric(
        "Fichas abertas (teste)",
        sum(1 for f in fichas.values() if f.get("status") != "Concluída"),
    )
    if estat["ignorados_fora_da_data"]:
        st.caption(
            f"{estat['ignorados_fora_da_data']} registro(s) de outros dias "
            "foram excluídos antes de qualquer agrupamento ou cruzamento."
        )
    if estat["duplicados"]:
        st.caption(f"{estat['duplicados']} O.S. duplicada(s) desconsiderada(s).")
    st.caption(
        f"Tempo medido: filtro de data e validação {estat.get('tempo_filtro_s', 0):.3f} s; "
        f"agrupamento espacial {estat.get('tempo_agrupamento_s', 0):.3f} s."
    )
    st.markdown("#### Panorama operacional")
    if len(pontos) > 3500:
        st.warning(
            "Há muitos pontos para renderizar. A tabela permanece completa; "
            "o mapa está limitado aos primeiros 3.500 pontos georreferenciados."
        )
    amostra = pontos.iloc[:3500] if len(pontos) > 3500 else pontos
    mapa = _mapa(concentracoes, amostra)
    if mapa is not None:
        st_folium(
            mapa, height=520, width=None, returned_objects=[],
            key="farol_v3_teste_mapa",
        )
    else:
        st.info("Não há coordenadas válidas para o mapa.")

    if concentracoes.empty:
        st.info("Nenhum agrupamento atingiu três reclamações em até 750 metros.")
    else:
        tabela = concentracoes.assign(
            acompanhamento=lambda t: t["chave"].map(
                lambda chave: fichas.get(chave, {}).get("status", "Não iniciado")
            )
        ).rename(columns={
            "codigo": "Concentração", "municipio": "Município",
            "bairros": "Bairros", "qtd_os": "O.S.",
            "matriculas": "Matrículas", "acompanhamento": "Acompanhamento",
        })
        st.dataframe(
            tabela[["Concentração", "Município", "Bairros", "O.S.", "Matrículas", "Acompanhamento"]],
            use_container_width=True, hide_index=True,
        )
    st.caption(
        "A localização geográfica é indicativa. Não há desenho manual, "
        "mesclagem nem alteração automática dos acompanhamentos."
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cadastro_ativo_local(nome):
    caminho = RAIZ / "dados" / nome
    if not caminho.is_file():
        return pd.DataFrame()
    for separador in (",", ";"):
        try:
            resultado = pd.read_csv(caminho, sep=separador, dtype=str).fillna("")
            if len(resultado.columns) > 1:
                return resultado
        except (ValueError, UnicodeDecodeError, pd.errors.ParserError):
            continue
    return pd.DataFrame()


def _visao_investigacao(op):
    concentracoes, pontos = op["concentracoes"], op["pontos"]
    if concentracoes.empty:
        st.info("Nenhuma concentração identificada no dia.")
        return
    itens = concentracoes.to_dict("records")
    opcoes = {g["chave"]: g for g in itens}
    chave = st.selectbox(
        "Concentração a investigar", list(opcoes),
        format_func=lambda k: (
            f"{opcoes[k]['codigo']} — {opcoes[k]['municipio']} · "
            f"{opcoes[k]['bairros']} ({opcoes[k]['qtd_os']} O.S.)"
        ),
        key="farol_v3_teste_investigacao",
    )
    conc = opcoes[chave]
    os_grupo = pontos[pontos["_chave"] == chave]
    st.markdown(f"#### {conc['codigo']} — {conc['bairros']}")
    st.caption(
        "Cada concentração é independente. Eventos próximos ou no mesmo "
        "bairro são indícios, não comprovação de causa."
    )
    st.dataframe(
        os_grupo[["os", "matricula", "bairro", "status", "data", "servico", "endereco"]]
        .rename(columns={
            "os": "O.S.", "matricula": "Matrícula", "bairro": "Bairro",
            "status": "Situação", "data": "Abertura",
            "servico": "Serviço", "endereco": "Endereço",
        }),
        use_container_width=True, hide_index=True,
    )
    st.divider()
    st.markdown("#### Evidências sob demanda")
    st.caption(
        "Eventos, ativos e medições só são examinados ao solicitar a investigação. "
        "Arquivos opcionais permanecem apenas nesta sessão."
    )
    a, b = st.columns(2)
    with a:
        eventos_upload = st.file_uploader(
            "Eventos operacionais (CSV ou XLSX, opcional)",
            type=["csv", "xlsx"], key="farol_v3_teste_eventos",
        )
    with b:
        pressao_upload = st.file_uploader(
            "Medições de pressão (CSV ou XLSX, opcional)",
            type=["csv", "xlsx"], key="farol_v3_teste_pressoes",
        )
    if st.button("🔍 Carregar evidências desta concentração", key="farol_v3_teste_investigar"):
        try:
            dia = hoje()
            eventos = (
                eventos_pertinentes(ler_arquivo(eventos_upload), conc, dia)
                if eventos_upload else []
            )
            pressoes = (
                pressao_proxima(ler_arquivo(pressao_upload), conc, dia)
                if pressao_upload else []
            )
            poco = ativos_proximos(_cadastro_ativo_local("pocos.csv"), conc)
            logger = ativos_proximos(_cadastro_ativo_local("loggers.csv"), conc)
            st.session_state["farol_v3_teste_evidencias"] = {
                "chave": chave, "eventos": eventos, "pressoes": pressoes,
                "pocos": poco, "loggers": logger,
            }
        except Exception as erro:
            st.error(f"Falha ao consultar evidências: {erro}")
    dados = st.session_state.get("farol_v3_teste_evidencias", {})
    if dados.get("chave") == chave:
        col1, col2, col3 = st.columns(3)
        col1.metric("Eventos relacionados", len(dados["eventos"]))
        col2.metric("Ativos em 750 m", len(dados["pocos"]) + len(dados["loggers"]))
        col3.metric("Medições do dia em 750 m", len(dados["pressoes"]))
        with st.expander("Eventos relacionados — descrição e ativo", expanded=True):
            if dados["eventos"]:
                st.dataframe(pd.DataFrame(dados["eventos"]), use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhum evento identificado ou arquivo de eventos não fornecido.")
        with st.expander("Ativos e loggers próximos"):
            ativos = [
                dict(item, tipo=tipo)
                for tipo, itens in (("Poço / ativo", dados["pocos"]), ("Logger", dados["loggers"]))
                for item in itens
            ]
            if ativos:
                st.dataframe(pd.DataFrame(ativos), use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhum ativo encontrado dentro de 750 metros do centro.")
        with st.expander("Pressões medidas no dia"):
            if dados["pressoes"]:
                st.dataframe(pd.DataFrame(dados["pressoes"]), use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhuma medição informada ou encontrada para o dia.")
    if st.button("Abrir acompanhamento desta concentração", key="farol_v3_teste_abrir_ficha"):
        st.session_state["farol_v3_teste_alvo"] = chave
        st.session_state.pop("farol_v3_teste_ficha_selecionada", None)
        # A navegação é aplicada no próximo rerun: não editar chave do
        # widget radio depois que ele já foi instanciado nesta execução.
        st.session_state["farol_v3_teste_navegar"] = "Acompanhamentos"
        st.rerun()


def _backup_fichas():
    return json.dumps({
        "formato": "farol-v3-teste-1",
        "exportado_em": hora(),
        "fichas": st.session_state[CHAVE_FICHAS],
    }, ensure_ascii=False, indent=2).encode("utf-8")


def _fichas_backup():
    fichas = st.session_state[CHAVE_FICHAS]
    with st.expander("Exportar ou restaurar fichas experimentais", expanded=False):
        st.warning(
            "Neste protótipo, as fichas permanecem só nesta sessão. "
            "Exporte o JSON antes de encerrar o navegador. "
            "A produção não recebe estes dados."
        )
        st.download_button(
            "⬇️ Exportar fichas de teste (JSON)",
            data=_backup_fichas(), mime="application/json",
            file_name="farol_v3_fichas_teste.json",
            key="farol_v3_teste_download_backup",
        )
        entrada = st.file_uploader(
            "Restaurar backup do laboratório (JSON)",
            type=["json"], key="farol_v3_teste_import_backup",
        )
        confirmar = st.checkbox(
            "Confirmo que a importação substituirá minhas fichas atuais desta sessão.",
            key="farol_v3_teste_confirm_backup",
        )
        if st.button("Restaurar fichas", disabled=not (entrada and confirmar)):
            try:
                if len(entrada.getvalue()) > 2 * 1024 * 1024:
                    raise ValueError("Backup maior que 2 MB.")
                dados = json.loads(entrada.getvalue())
                if not isinstance(dados, dict) or dados.get("formato") != "farol-v3-teste-1":
                    raise ValueError("Backup de formato incompatível.")
                novos = dados.get("fichas")
                if not isinstance(novos, dict) or len(novos) > 1000:
                    raise ValueError("Coleção de fichas inválida.")
                for chave, ficha in novos.items():
                    if (not isinstance(chave, str) or len(chave) != 24
                            or not all(ch in "0123456789abcdef" for ch in chave)
                            or not isinstance(ficha, dict)
                            or ficha.get("chave") != chave
                            or ficha.get("status") not in STATUS
                            or not isinstance(ficha.get("movimentacoes", []), list)):
                        raise ValueError("Backup contém fichas inválidas.")
                st.session_state[CHAVE_FICHAS] = novos
                st.session_state[CHAVE_COPIAS] = {}
                st.success(f"{len(novos)} ficha(s) restaurada(s) na sessão de teste.")
                st.rerun()
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as erro:
                st.error(f"Não foi possível restaurar o backup: {erro}")
    return fichas


def _visao_acompanhamentos(op):
    fichas = st.session_state[CHAVE_FICHAS]
    concs = op["concentracoes"].to_dict("records")
    atuais = {item["chave"]: item for item in concs}
    st.markdown("#### Acompanhamentos operacionais — laboratório")
    st.caption(
        "Uma ficha por concentração. Fichas antigas permanecem disponíveis "
        "nesta sessão mesmo sem recalcular dados de dias anteriores."
    )
    abertas = [f for f in fichas.values() if f.get("status") != "Concluída"]
    a, b = st.columns(2)
    a.metric("Fichas em aberto (sessão)", len(abertas))
    b.metric("Fichas concluídas (sessão)", len(fichas) - len(abertas))
    if fichas:
        st.dataframe(
            pd.DataFrame([
                {"Concentração": f.get("codigo", ""), "Dia de origem": f.get("dia", ""),
                 "Município": f.get("municipio", ""), "Status": f.get("status", ""),
                 "Última atualização": f.get("atualizado_em", "")}
                for f in sorted(fichas.values(), key=lambda x: x.get("atualizado_em", ""), reverse=True)
            ]), use_container_width=True, hide_index=True,
        )

    opcoes = {**{
        chave: {
            "chave": chave, "codigo": ficha["codigo"],
            "municipio": ficha["municipio"], "bairros": ficha["bairros"],
            "qtd_os": ficha.get("qtd_os", 0), "dia": ficha.get("dia", ""),
        }
        for chave, ficha in fichas.items()
    }, **{
        chave: dict(item, dia=op["dia"]) for chave, item in atuais.items()
    }}
    if not opcoes:
        st.info("Nenhuma concentração nem ficha para consultar.")
        _fichas_backup()
        return
    alvo = st.session_state.pop("farol_v3_teste_alvo", None)
    if alvo not in opcoes:
        alvo = next(iter(opcoes))
    chaves = list(opcoes)
    chave = st.selectbox(
        "Selecionar concentração / ficha",
        chaves, index=chaves.index(alvo),
        format_func=lambda k: (
            f"{opcoes[k]['codigo']} — {opcoes[k]['municipio']} "
            f"({opcoes[k]['dia']})"
        ),
        key="farol_v3_teste_ficha_selecionada",
    )
    alvo = opcoes[chave]
    atual = fichas.get(chave)
    st.markdown(f"##### {alvo['codigo']} — {alvo['bairros']}")

    copia_key = f"farol_v3_teste_copiar_{chave}"
    fontes = {k: v for k, v in fichas.items() if k != chave}
    if fontes:
        with st.expander("📋 Copiar dados de outra concentração"):
            ids = list(fontes)
            origem = st.selectbox(
                "Ficha de origem", ids,
                format_func=lambda k: (
                    f"{fontes[k]['codigo']} — {fontes[k]['municipio']} "
                    f"({fontes[k]['dia']})"
                ), key=f"farol_v3_teste_origem_{chave}",
            )
            if st.button("Copiar campos operacionais", key=f"farol_v3_teste_copiar_btn_{chave}"):
                fonte = fontes[origem]
                st.session_state[CHAVE_COPIAS][chave] = {
                    "descricao": fonte.get("descricao", ""),
                    "causa_provavel": fonte.get("causa_provavel", ""),
                    "causa_confirmada": fonte.get("causa_confirmada", ""),
                    "arcgis": fonte.get("arcgis", False),
                    "sia": fonte.get("sia", False),
                    "loggers": fonte.get("loggers", False),
                    "retorno": fonte.get("retorno", False),
                    "origem": origem,
                }
                st.rerun()
    dados_copia = st.session_state[CHAVE_COPIAS].get(chave, {})
    dados = {**(atual or {}), **dados_copia}
    if dados_copia:
        st.info(
            f"Campos copiados da ficha {fontes.get(dados_copia['origem'], {}).get('codigo', '')}. "
            "Revise antes de salvar. O status e o histórico da origem não são copiados."
        )

    versao_form = dados_copia.get("origem", "original")
    with st.form(f"farol_v3_teste_form_{chave}_{versao_form}"):
        if atual:
            status_atual = atual.get("status", STATUS[0])
        else:
            status_atual = "Em análise"
        status = st.selectbox(
            "Situação operacional", STATUS,
            index=STATUS.index(status_atual) if status_atual in STATUS else 0,
        )
        descricao = st.text_area(
            "Diagnóstico / relato", value=str(dados.get("descricao", "")), height=110,
        )
        col1, col2 = st.columns(2)
        causa_provavel = col1.text_input(
            "Causa provável (hipótese)", value=str(dados.get("causa_provavel", "")),
        )
        causa_confirmada = col2.text_input(
            "Causa confirmada (quando houver evidência)",
            value=str(dados.get("causa_confirmada", "")),
        )
        c1, c2, c3, c4 = st.columns(4)
        arcgis = c1.checkbox("ArcGIS validado", value=bool(dados.get("arcgis", False)))
        sia = c2.checkbox("SIA validado", value=bool(dados.get("sia", False)))
        loggers = c3.checkbox("Loggers validados", value=bool(dados.get("loggers", False)))
        retorno = c4.checkbox("Retorno operação", value=bool(dados.get("retorno", False)))
        movimento = st.text_area(
            "Nova movimentação (opcional)", value="", height=75,
        )
        confirmado = st.checkbox(
            "Confirmo a conclusão desta concentração específica."
        ) if status == "Concluída" and status_atual != "Concluída" else True
        salvar = st.form_submit_button(
            "💾 Salvar ficha individual (somente teste)",
            type="primary", use_container_width=True,
        )
    if salvar:
        if not confirmado:
            st.warning("Confirme a conclusão antes de salvar.")
        else:
            hora_salvamento = hora()
            movimentos = list((atual or {}).get("movimentacoes", []))
            if movimento.strip():
                movimentos.append({
                    "em": hora_salvamento, "status": status,
                    "descricao": movimento.strip(),
                })
            if status == "Concluída" and status_atual != "Concluída":
                movimentos.append({
                    "em": hora_salvamento, "status": status,
                    "descricao": "Conclusão confirmada pelo analista.",
                })
            fichas[chave] = {
                "chave": chave, "dia": alvo["dia"],
                "codigo": alvo["codigo"], "municipio": alvo["municipio"],
                "bairros": alvo["bairros"], "qtd_os": alvo["qtd_os"],
                "status": status, "descricao": descricao,
                "causa_provavel": causa_provavel,
                "causa_confirmada": causa_confirmada,
                "arcgis": arcgis, "sia": sia, "loggers": loggers,
                "retorno": retorno, "movimentacoes": movimentos,
                "atualizado_em": hora_salvamento,
            }
            st.session_state[CHAVE_COPIAS].pop(chave, None)
            st.toast("Ficha de teste salva somente nesta sessão.")
            st.rerun()
    if atual and atual.get("movimentacoes"):
        with st.expander("Histórico de movimentações desta ficha", expanded=False):
            for registro in atual["movimentacoes"]:
                st.write(
                    f"**{registro.get('em', '')} · {registro.get('status', '')}** — "
                    f"{registro.get('descricao', '')}"
                )
    _fichas_backup()


def renderizar():
    dia = hoje()
    st.subheader("🔬 Farol Operacional V3 — Operação do dia")
    st.caption(
        f"Data operacional: **{dia:%d/%m/%Y}** (America/Fortaleza). "
        "Somente teste; Módulo 3 e bancos de produção não são alterados."
    )
    if CHAVE_FICHAS not in st.session_state:
        st.session_state[CHAVE_FICHAS] = {}
    if CHAVE_COPIAS not in st.session_state:
        st.session_state[CHAVE_COPIAS] = {}
    op = st.session_state.get(CHAVE_OPERACAO)
    if op and op["dia"] != dia.isoformat():
        st.session_state.pop(CHAVE_OPERACAO, None)
        st.session_state.pop("farol_v3_teste_evidencias", None)
        op = None
        st.info("O dia operacional mudou. Carregue as reclamações de hoje.")
    with st.expander("1. Carregar reclamações do dia", expanded=op is None):
        st.caption(
            "Envie uma exportação CSV/XLSX com O.S., cidade, bairro, latitude, "
            "longitude e data de emissão. Os registros fora da data atual "
            "serão ignorados. Nenhuma planilha de produção é modificada."
        )
        upload = st.file_uploader(
            "Arquivo de reclamações", type=["csv", "xlsx"],
            key="farol_v3_teste_upload_os",
        )
        col1, col2 = st.columns(2)
        if col1.button("Processar arquivo de hoje", type="primary", disabled=upload is None):
            try:
                with st.spinner("Filtrando o dia e formando concentrações..."):
                    st.session_state[CHAVE_OPERACAO] = processar(ler_arquivo(upload), dia)
                    st.session_state.pop("farol_v3_teste_evidencias", None)
                st.rerun()
            except Exception as erro:
                st.error(f"Importação interrompida: {erro}")
        if col2.button("Carregar exemplo fictício (demonstração)"):
            st.session_state[CHAVE_OPERACAO] = processar(
                exemplo_do_dia(dia), dia, demonstracao=True,
            )
            st.session_state.pop("farol_v3_teste_evidencias", None)
            st.rerun()

    op = st.session_state.get(CHAVE_OPERACAO)
    if not op:
        st.info(
            "Não há reclamações do dia carregadas. Você ainda pode consultar "
            "as fichas experimentais existentes nesta sessão, sem reprocessar O.S."
        )
        if st.session_state[CHAVE_FICHAS]:
            _visao_acompanhamentos({
                "concentracoes": pd.DataFrame(), "dia": dia.isoformat(),
            })
        else:
            _fichas_backup()
        return
    if op["demonstracao"]:
        st.warning("DADOS FICTÍCIOS: os registros desta análise são apenas demonstrativos.")
    st.caption(
        f"Último processamento: {op['processado_em']}. "
        "A análise usa apenas a data corrente, não uma janela de oito dias."
    )
    navegar = st.session_state.pop("farol_v3_teste_navegar", None)
    if navegar in {"Panorama do dia", "Investigação", "Acompanhamentos"}:
        st.session_state["farol_v3_teste_visao"] = navegar
    visao = st.radio(
        "Área do Farol V3",
        ["Panorama do dia", "Investigação", "Acompanhamentos"],
        horizontal=True,
        key="farol_v3_teste_visao",
    )
    if visao == "Panorama do dia":
        _visao_panorama(op)
    elif visao == "Investigação":
        _visao_investigacao(op)
    else:
        _visao_acompanhamentos(op)
