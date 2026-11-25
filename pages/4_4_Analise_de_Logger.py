"""Módulo 4.4 — Análise de Logger: Monitor de Variação e Análise de Comportamento.

Arquivos enviados são processados somente durante a sessão; nenhuma gravação
é feita no Supabase ou na planilha de origem dos loggers.
"""
from pathlib import Path
import sys
import urllib.parse

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="Análise de Logger — Ferramentas Operacionais",
    page_icon="📡", layout="wide", initial_sidebar_state="expanded",
)

RAIZ = str(Path(__file__).resolve().parent.parent)
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from auth import exigir_acesso_modulo, verificar_autenticacao
from logger_monitor_core import (
    ErroBaseLogger, ler_arquivo, preparar_medicoes, mesclar_historico,
    resumir_historico, historico_logger,
)
from logger_comportamento_core import (
    diagnosticar, extrair_historico, ids_disponiveis,
)
from logger_lote_core import catalogar_historicos, analisar_selecionado

exigir_acesso_modulo("4")
if not verificar_autenticacao():
    st.warning("Sessão não iniciada ou expirada.")
    if st.button("Ir para o login"):
        st.switch_page("app.py")
    st.stop()


def fnumero(valor, unidade="mca", com_sinal=False):
    if pd.isna(valor):
        return "—"
    numero = f"{float(valor):+.2f}" if com_sinal else f"{float(valor):.2f}"
    return numero.replace(".", ",") + " " + unidade


def _arquivos(uploaded):
    return tuple((f.name, f.getvalue()) for f in uploaded)


@st.cache_data(ttl=300, max_entries=6, show_spinner=False)
def _ler_upload(nome, conteudo):
    return ler_arquivo(nome, conteudo)


def _opcao(filtro, valores, chave):
    opcoes = ["Todos"] + sorted(
        {str(v) for v in valores if pd.notna(v) and str(v).strip() not in ("", "-", "nan")}
    )
    return st.selectbox(filtro, opcoes, key=chave)


def _mapa_url(registro):
    lat = registro.get("Latitude")
    lon = registro.get("Longitude")
    try:
        if pd.notna(lat) and pd.notna(lon) and -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180:
            return f"https://www.google.com/maps?q={float(lat):.7f},{float(lon):.7f}"
    except (TypeError, ValueError):
        pass
    partes = [
        str(registro.get(campo) or "").strip()
        for campo in ("Endereco", "Cidade", "Nome")
        if pd.notna(registro.get(campo))
    ]
    busca = ", ".join(x for x in partes if x and x != "-")
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(
        busca or str(registro.get("Galaxia ID", ""))
    )


def _exibir_detalhes_logger(dados, historico):
    st.divider()
    st.markdown("#### Consulta individual por Galaxia ID")
    ids = sorted(dados["Galaxia ID"].astype(str).unique().tolist())
    busca = st.text_input("Localizar Galaxia ID", key="logger_m4_busca_id").strip()
    if busca:
        correspondentes = [x for x in ids if busca.casefold() in x.casefold()]
    else:
        correspondentes = ids
    if not correspondentes:
        st.warning("Nenhum Galaxia ID corresponde à pesquisa.")
        return
    selecionado = st.selectbox(
        "Selecione um logger", correspondentes, key="logger_m4_id_selecionado",
    )
    row = dados.loc[dados["Galaxia ID"] == selecionado].iloc[0]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Leitura anterior", fnumero(row["Pressao_Anterior"]))
    col2.metric("Leitura posterior", fnumero(row["Pressao_Atual"]))
    col3.metric("Variação", fnumero(row["Delta_MCA"], com_sinal=True))
    col4.metric("Comparação", row["Tendencia"])

    if row["Tendencia"] == "Não comparável":
        st.warning("Comparação indisponível: " + str(row["Motivo"]))
    st.caption(
        f"Local: {row.get('Cidade') or '—'} · Regional: {row.get('Regional') or '—'} · "
        f"Base: {row.get('Base') or '—'} · Tipo de ponto estimado: "
        f"{row.get('Tipo_Ponto') or '—'}"
    )
    st.write({
        "Nome": row.get("Nome", "—"),
        "Endereço": row.get("Endereco", "—"),
        "Status": row.get("Status", "—"),
        "Estado da instalação": row.get("Estado", "—"),
        "Arquivo anterior": row.get("Arquivo_Origem_Anterior", "—"),
        "Arquivo posterior": row.get("Arquivo_Origem_Atual", "—"),
        "Data anterior": (
            str(row["MedicaoEm_Anterior"]) if pd.notna(row["MedicaoEm_Anterior"]) else "—"
        ),
        "Data posterior": (
            str(row["MedicaoEm_Atual"]) if pd.notna(row["MedicaoEm_Atual"]) else "—"
        ),
    })
    # Contexto da consulta original: comparar a pressão do logger à média
    # da última leitura dos OUTROS loggers do mesmo município.
    if pd.notna(row.get("Cidade")) and pd.notna(row.get("Pressao_Atual")):
        referencia_cidade = dados.loc[
            (dados["Cidade"] == row["Cidade"])
            & (dados["Galaxia ID"] != selecionado)
            & dados["Pressao_Atual"].notna()
        ]
        if not referencia_cidade.empty:
            media_cidade = referencia_cidade["Pressao_Atual"].mean()
            diferenca_cidade = row["Pressao_Atual"] - media_cidade
            st.info(
                f"Comparativo municipal: pressão do logger "
                f"{fnumero(diferenca_cidade, com_sinal=True)} em relação "
                f"à média de {len(referencia_cidade)} outro(s) logger(s) "
                f"de {row['Cidade']} ({fnumero(media_cidade)}). "
                "Comparação de últimas leituras disponíveis, "
                "que podem ter horários diferentes."
            )
    st.link_button("🗺️ Abrir localização no Google Maps", _mapa_url(row))
    st.markdown("#### Evolução das medições carregadas")
    serie = historico_logger(historico, selecionado)
    if serie.empty:
        st.warning("Este logger não possui medições com pressão e data válidas.")
        return
    st.caption(
        f"{len(serie)} leitura(s) distinta(s) com data e pressão válidas. "
        "A curva inclui todos os horários carregados nesta sessão; "
        "as duas últimas leituras são usadas na classificação da variação."
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Mínima", fnumero(serie["Pressao"].min()))
    m2.metric("Máxima", fnumero(serie["Pressao"].max()))
    m3.metric("Média", fnumero(serie["Pressao"].mean()))
    m4.metric(
        "Variação 1ª → última",
        fnumero(serie["Pressao"].iloc[-1] - serie["Pressao"].iloc[0], com_sinal=True)
        if len(serie) > 1 else "—"
    )
    grafico = px.line(
        serie,
        x="MedicaoEm", y="Pressao", markers=True,
        title=f"Variação de pressão — Logger {selecionado}",
        labels={"MedicaoEm": "Data/hora da medição", "Pressao": "Pressão (mca)"},
        hover_data={"Arquivo_Origem": True, "MedicaoEm": "|%d/%m/%Y %H:%M"},
    )
    grafico.add_hline(
        y=float(serie["Pressao"].mean()), line_dash="dash",
        annotation_text="Média das medições", annotation_position="top right",
    )
    if len(serie) <= 20:
        grafico.update_traces(
            text=[f"{v:.1f}".replace(".", ",") for v in serie["Pressao"]],
            textposition="top center", mode="lines+markers+text",
        )
    grafico.update_layout(hovermode="x unified", height=430)
    st.plotly_chart(grafico, use_container_width=True)
    with st.expander("Ver todos os horários carregados"):
        st.dataframe(
            serie[["MedicaoEm", "Pressao", "Arquivo_Origem"]].rename(
                columns={"MedicaoEm": "Horário real", "Pressao": "Pressão (mca)",
                         "Arquivo_Origem": "Arquivo de origem"}
            ), hide_index=True, use_container_width=True,
        )
    st.caption(
        "O gráfico representa as leituras presentes nas bases carregadas, "
        "não uma série contínua de todas as medições do equipamento. "
        "Para comparar com o padrão histórico diário, utilize Análise de Comportamento."
    )


def _monitor():
    st.markdown("### 📈 Monitor de Variação")
    st.caption(
        "Carregue bases coletadas em diferentes horários ao longo do dia. "
        "O monitor acumula as leituras nesta sessão, identifica a variação "
        "entre os dois últimos horários reais de cada logger e permite "
        "consultar o gráfico com todos os pontos importados."
    )

    if "m4_logger_monitor_historico" not in st.session_state:
        st.session_state["m4_logger_monitor_historico"] = pd.DataFrame()
    if "m4_logger_monitor_total_arquivos" not in st.session_state:
        st.session_state["m4_logger_monitor_total_arquivos"] = 0
    if "m4_logger_monitor_upload_versao" not in st.session_state:
        st.session_state["m4_logger_monitor_upload_versao"] = 0

    arquivos = st.file_uploader(
        "Importar bases de loggers (vários arquivos em um único campo)",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        key=f"m4_logger_monitor_upload_{st.session_state['m4_logger_monitor_upload_versao']}",
        help=(
            "Selecione várias exportações de horários diferentes ou acrescente "
            "novas bases conforme o dia avança, clicando em Adicionar bases."
        ),
    )
    col_add, col_clear = st.columns([3, 1])
    with col_add:
        adicionar = st.button(
            "➕ Adicionar bases ao monitoramento",
            type="primary", use_container_width=True,
            disabled=not bool(arquivos), key="m4_logger_adicionar_bases",
        )
    with col_clear:
        limpar = st.button(
            "🗑️ Limpar monitoramento",
            use_container_width=True,
            key="m4_logger_limpar_bases",
        )
    if limpar:
        st.session_state["m4_logger_monitor_historico"] = pd.DataFrame()
        st.session_state["m4_logger_monitor_total_arquivos"] = 0
        st.session_state["m4_logger_monitor_upload_versao"] += 1
        st.rerun()

    if adicionar:
        novas = []
        falhas = []
        with st.spinner("Importando as exportações e verificando horários..."):
            for arquivo in arquivos:
                try:
                    planilha = _ler_upload(arquivo.name, arquivo.getvalue())
                    novas.append(preparar_medicoes(planilha, arquivo.name))
                except (ErroBaseLogger, ValueError, KeyError, ImportError, OSError) as exc:
                    falhas.append(f"{arquivo.name}: {exc}")
            if novas:
                st.session_state["m4_logger_monitor_historico"] = mesclar_historico(
                    st.session_state["m4_logger_monitor_historico"],
                    pd.concat(novas, ignore_index=True),
                )
                st.session_state["m4_logger_monitor_total_arquivos"] += len(novas)
                st.session_state["m4_logger_monitor_upload_versao"] += 1
                st.session_state["m4_logger_monitor_ultima_importacao"] = (
                    f"{len(novas)} arquivo(s) incluído(s)."
                )
            if falhas:
                st.session_state["m4_logger_monitor_falhas"] = falhas
            else:
                st.session_state.pop("m4_logger_monitor_falhas", None)
        if novas:
            st.rerun()

    if st.session_state.get("m4_logger_monitor_ultima_importacao"):
        st.success(st.session_state.pop("m4_logger_monitor_ultima_importacao"))
    for falha in st.session_state.get("m4_logger_monitor_falhas", []):
        st.error(f"Falha na importação: {falha}")

    historico = st.session_state["m4_logger_monitor_historico"]
    if historico.empty:
        st.info(
            "Selecione uma ou várias bases e clique em **Adicionar bases**. "
            "Depois, faça novas importações no mesmo campo ao longo do dia "
            "para atualizar os resultados e os gráficos."
        )
        return

    try:
        comparacao = resumir_historico(historico)
    except (ErroBaseLogger, ValueError, KeyError, OSError) as erro:
        st.error(f"Não foi possível analisar as leituras importadas: {erro}")
        return

    if comparacao.empty:
        st.warning("Nenhum logger com Galaxia ID identificado.")
        return
    comparaveis = comparacao["Delta_MCA"].notna()
    resumo = comparacao["Tendencia"].value_counts()
    colunas = st.columns(6)
    for coluna, titulo, valor in zip(
        colunas,
        ("Loggers distintos", "Comparáveis", "Quedas", "Aumentos", "Estáveis", "Sem comparação"),
        (
            len(comparacao), int(comparaveis.sum()),
            int(resumo.get("Queda (-)", 0)),
            int(resumo.get("Aumento (+)", 0)),
            int(resumo.get("Estável", 0)),
            int(resumo.get("Não comparável", 0)),
        ),
    ):
        coluna.metric(titulo, valor)
    st.caption(
        f"Arquivos adicionados nesta sessão: "
        f"{st.session_state['m4_logger_monitor_total_arquivos']} · "
        f"{len(historico)} registros de logger/horário distintos. "
        "Variação = pressão da última leitura − pressão da penúltima, "
        "sempre usando horários de medição diferentes."

    )
    if not comparaveis.all():
        st.warning(
            f"{(~comparaveis).sum()} logger(s) ainda sem duas leituras "
            "comparáveis em horários diferentes ou com dados incompletos. "
            "Veja o motivo na tabela; ausência de dados não significa estabilidade."
        )

    c_reg, c_cid, c_est, c_inst, c_tipo, c_tend = st.columns([1.3,1.5,1,1.1,1.1,1.3])
    with c_reg:
        reg = _opcao("Regional", comparacao["Regional"], "m4_logger_regional")
    cidades_validas = comparacao if reg == "Todos" else comparacao[comparacao["Regional"] == reg]
    with c_cid:
        cidade = _opcao("Cidade", cidades_validas["Cidade"], "m4_logger_cidade")
    with c_est:
        status = _opcao("Status", comparacao["Status"], "m4_logger_status")
    with c_inst:
        estado = _opcao("Instalação", comparacao["Estado"], "m4_logger_estado")
    with c_tipo:
        tipo = _opcao("Tipo de ponto", comparacao["Tipo_Ponto"], "m4_logger_tipo")
    with c_tend:
        tendencia = _opcao("Tendência", comparacao["Tendencia"], "m4_logger_tendencia")
    filtrados = comparacao.copy()
    for campo, valor in (
        ("Regional", reg), ("Cidade", cidade), ("Status", status),
        ("Estado", estado), ("Tipo_Ponto", tipo), ("Tendencia", tendencia),
    ):
        if valor != "Todos":
            filtrados = filtrados.loc[filtrados[campo] == valor]

    col_top, _ = st.columns([1,4])
    with col_top:
        top = st.selectbox("Mostrar", [10, 20, 50, "Todos"], key="m4_logger_top")
    mostrar = filtrados if top == "Todos" else filtrados.head(int(top))
    st.caption(
        f"{len(filtrados)} logger(s) após os filtros; "
        f"exibindo {len(mostrar)}."
    )
    colunas_tabela = [
        "Galaxia ID", "Cidade", "Regional", "Base", "Tipo_Ponto",
        "Tendencia", "Pressao_Anterior", "Pressao_Atual", "Delta_MCA",
        "MedicaoEm_Anterior", "MedicaoEm_Atual", "Qtd_Medicoes",
        "Status", "Estado", "Motivo",
    ]
    tabela = mostrar[colunas_tabela].copy().rename(columns={
        "Tendencia": "Tendência",
        "Pressao_Anterior": "Pressão anterior (mca)",
        "Pressao_Atual": "Pressão atual (mca)",
        "Delta_MCA": "Variação (mca)",
        "MedicaoEm_Anterior": "Horário anterior",
        "MedicaoEm_Atual": "Horário atual",
        "Tipo_Ponto": "Tipo de ponto",
        "Qtd_Medicoes": "Horários com medição",
    })
    st.dataframe(
        tabela, hide_index=True, use_container_width=True,
        column_config={
            "Pressão anterior (mca)": st.column_config.NumberColumn(format="%.2f"),
            "Pressão atual (mca)": st.column_config.NumberColumn(format="%.2f"),
            "Variação (mca)": st.column_config.NumberColumn(format="%+.2f"),
        },
    )
    st.download_button(
        "⬇️ Exportar resultado filtrado (CSV)",
        data=filtrados.drop(columns=["_merge"], errors="ignore").to_csv(
            index=False, sep=";", decimal=","
        ).encode("utf-8-sig"),
        file_name="monitor_variacao_loggers.csv",
        mime="text/csv", key="m4_logger_download_monitor",
    )
    if comparacao["Delta_MCA"].notna().any():
        maiores = comparacao.dropna(subset=["Delta_MCA"]).head(20)
        fig = px.bar(
            maiores, x="Galaxia ID", y="Delta_MCA",
            color="Tendencia",
            title="20 maiores variações absolutas (mca)",
            labels={"Delta_MCA":"Variação (mca)","Galaxia ID":"Logger"},
        )
        st.plotly_chart(fig, use_container_width=True)
    _exibir_detalhes_logger(comparacao, historico)


@st.cache_data(ttl=600, max_entries=3, show_spinner=False)
def _catalogar_uploads_historicos(arquivos):
    # Somente leitura e agrupamento: o diagnóstico é calculado para o ID
    # selecionado depois, sem processar desnecessariamente todo o catálogo.
    return catalogar_historicos(arquivos)


def _campo_limite_local(sufixo):
    habilitado = st.checkbox(
        "Utilizar limite mínimo operacional específico (opcional)",
        value=False,
        key=f"m4_logger_limite_habilitado_{sufixo}",
        help=(
            "Somente habilite quando houver um limite mínimo aplicável ao(s) "
            "ponto(s) analisado(s). Não existe um limite universal definido "
            "por esta ferramenta. No lote, o mesmo valor será aplicado a todos."
        ),
    )
    if not habilitado:
        return None
    return st.number_input(
        "Limite mínimo informado pelo operador (mca)",
        min_value=0.0,
        max_value=150.0,
        value=10.0,
        step=0.5,
        key=f"m4_logger_limite_valor_{sufixo}",
        help="O valor é apenas ilustrativo até ser informado e confirmado pelo operador.",
    )


def _mostrar_diagnostico(dados, diagnostico, logger_id=None):
    """Exibe somente o diagnóstico do logger escolhido no seletor."""
    st.caption(
        f"Logger: {logger_id or 'identificador não disponível'} · "
        f"Última leitura do arquivo: "
        f"{diagnostico['data_ultima']:%d/%m/%Y %H:%M} · "
        f"{len(dados)} leituras válidas."
    )

    direcao = diagnostico["direcao"]
    prioridade = diagnostico["prioridade"]
    if prioridade == "Alta prioridade":
        st.error(
            "🔴 Prioridade alta para investigação — patamar próximo de zero "
            "persistente. Verificar a integridade do logger e a situação da rede."
        )
    elif direcao == "Abaixo do padrão":
        st.warning(
            "🟠 Pressão abaixo do padrão histórico do horário. "
            "Verificar duração e possíveis impactos no abastecimento."
        )
    elif direcao == "Acima do padrão":
        st.info(
            "🔵 Pressão acima do padrão histórico do horário. "
            "Isso não significa falta de água; avaliar eventuais condições de sobrepressão."
        )
    elif direcao == "Atípico dentro da faixa":
        st.info(
            "🔎 Leitura pouco frequente, embora dentro da faixa histórica "
            "do horário. A classificação isolada não confirma anormalidade."
        )
    elif direcao == "Dentro do padrão":
        st.success("✅ Pressão dentro do padrão estatístico daquele horário.")
        st.caption(
            "Padrão histórico não comprova pressão suficiente para o abastecimento. "
            "A avaliação hidráulica deve considerar as condições do ponto."
        )
    else:
        st.warning(
            "⚪ Referência histórica insuficiente para classificar o horário."
        )

    a, b, c, d = st.columns(4)
    a.metric("Última pressão", fnumero(diagnostico["pressao_ultima"]))
    b.metric(
        "Mediana neste horário",
        fnumero(diagnostico["referencia_horario_mediana"]),
        help="Mediana de leituras de dias anteriores na faixa horária ±30 min.",
    )
    c.metric(
        "Desvio da mediana",
        fnumero(diagnostico["desvio_mca"], com_sinal=True),
        help="Diferença entre a última pressão e a mediana do mesmo horário.",
    )
    d.metric("Prioridade investigativa", prioridade)

    e, f, g = st.columns(3)
    e.metric("Tendência recente", diagnostico["tendencia"])
    f.metric("Situação observada", diagnostico["status_recente"])
    duracao = diagnostico["persistencia_abaixo_min"]
    if duracao is None:
        duracao = diagnostico["persistencia_zero_min"]
    g.metric(
        "Persistência confirmada",
        f"{duracao:.0f} min" if duracao is not None else "Não demonstrada",
    )
    st.markdown("#### Diagnóstico operacional")
    st.write(diagnostico["diagnostico"])
    st.caption(
        f"Referência ±30 min: {diagnostico['medicoes_referencia']} medição(ões) "
        f"em {diagnostico['dias_referencia']} dia(s) anteriores; "
        "classificação requer ao menos 5 dias e 25 medições. "
        "Persistência limitada a intervalos de até 30 minutos entre leituras."
    )
    if diagnostico["referencia_inferior"] is not None:
        st.info(
            "Faixa histórica ampliada pela tolerância, específica do horário da "
            "última medição: "
            f"{fnumero(diagnostico['referencia_inferior'])} a "
            f"{fnumero(diagnostico['referencia_superior'])}. "
            "A classificação considera também a frequência de valores próximos."
        )

    grafico = px.line(
        dados, x="data", y="pressao", markers=False,
        title=f"Histórico de pressão{f' — {logger_id}' if logger_id else ''}",
        labels={"data": "Data/hora", "pressao": "Pressão (mca)"},
    )
    grafico.update_layout(hovermode="x unified", height=430)
    st.plotly_chart(grafico, use_container_width=True)
    st.caption(
        "A faixa histórica de referência é válida para o horário da última "
        "medição, não para toda a curva. Os dados não permitem confirmar "
        "sozinhos a causa de possíveis anormalidades."
    )
    with st.expander("Ver medições utilizadas"):
        st.dataframe(
            dados.rename(
                columns={"data": "Data/hora", "pressao": "Pressão (mca)"}
            ), hide_index=True, use_container_width=True,
        )
    texto = (
        f"Análise de Comportamento — {logger_id or 'logger não identificado'}\n"
        + f"Classificação histórica: {diagnostico['classificacao']}\n"
        + f"Direção: {direcao}\n"
        + f"Prioridade investigativa: {prioridade}\n"
        + diagnostico["diagnostico"] + "\n"
        + f"Referência: {diagnostico['dias_referencia']} dias, "
        + f"{diagnostico['medicoes_referencia']} medições\n"
    )
    st.download_button(
        "⬇️ Exportar diagnóstico individual (TXT)",
        data=texto.encode("utf-8"),
        file_name="analise_comportamento_logger.txt",
        mime="text/plain", key="m4_logger_download_analisador_selecionado",
    )


def _comportamento():
    st.markdown("### 📉 Análise de Comportamento")
    st.caption(
        "Importe vários históricos de uma única vez e utilize o seletor "
        "para consultar o diagnóstico de cada logger. A ferramenta analisa "
        "somente o equipamento selecionado, sem misturar suas medições com "
        "as dos demais."
    )
    arquivos = st.file_uploader(
        "Importar históricos de loggers (XLSX, XLS ou CSV)",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        key="m4_logger_historicos_seletor",
        help=(
            "Permite enviar vários arquivos ou um arquivo com diversos "
            "Galaxia ID. Depois, selecione o equipamento desejado."
        ),
    )
    if not arquivos:
        st.info(
            "Selecione um ou mais arquivos de históricos para "
            "habilitar o seletor de loggers."
        )
        return

    try:
        with st.spinner("Organizando históricos por Galaxia ID..."):
            catalogo, origens, falhas = _catalogar_uploads_historicos(
                _arquivos(arquivos)
            )
    except (ErroBaseLogger, ValueError, KeyError, OSError) as erro:
        st.error(f"Não foi possível importar os históricos: {erro}")
        return

    st.caption(
        f"{len(arquivos)} arquivo(s) enviado(s) · "
        f"{len(catalogo)} histórico(s) identificado(s). "
        "Os dados permanecem disponíveis enquanto os arquivos estiverem "
        "selecionados nesta sessão."
    )
    if falhas:
        st.warning(
            f"{len(falhas)} arquivo(s) não puderam ser interpretados. "
            "Os demais históricos permanecem disponíveis."
        )
        with st.expander("Ver erros de importação"):
            for falha in falhas:
                st.write(falha)

    ids = list(catalogo)
    escolhido = st.selectbox(
        "Selecione o logger para analisar (Galaxia ID)",
        ids,
        key="m4_logger_seletor_comportamento",
        help=(
            "O diagnóstico e o gráfico abaixo se referem somente ao ID "
            "escolhido. Digite parte do identificador para localizá-lo."
        ),
    )
    if escolhido is None:
        return
    if escolhido.startswith("Arquivo: "):
        st.info(
            "Este histórico não contém um Galaxia ID identificado. "
            "O nome do arquivo é usado apenas como referência de seleção; "
            "confirme a origem do equipamento."
        )
    fontes = origens.get(escolhido, [])
    st.caption(
        f"Histórico selecionado: **{escolhido}** · "
        f"arquivo(s) correspondente(s): {', '.join(fontes)}"
    )
    limite = _campo_limite_local("seletor")

    try:
        dados, nome_coluna, diagnostico = analisar_selecionado(
            catalogo, escolhido, limiar_min_mca=limite
        )
    except (ErroBaseLogger, ValueError, KeyError, TypeError, IndexError) as erro:
        st.error(f"Não foi possível analisar o logger {escolhido}: {erro}")
        return

    st.caption(
        f"Coluna de pressão utilizada: {nome_coluna}. "
        "Confirme que os valores originais estão em mca. "
        "A ferramenta não converte automaticamente valores de bar."
    )
    _mostrar_diagnostico(dados, diagnostico, escolhido)



with st.sidebar:
    st.markdown("### 📡 Análise de Logger")
    st.caption(
        f"Usuário: **{st.session_state.get('usuario_logado', '')}**"
    )
    st.caption(
        f"Perfil: **{str(st.session_state.get('perfil', '')).upper()}**"
    )
    if st.button("⬅️ Ferramentas Operacionais", use_container_width=True):
        st.switch_page("pages/4_Ferramentas_Operacionais.py")
    if st.button("🏠 Menu Principal", use_container_width=True):
        st.switch_page("app.py")
    st.divider()
    # O manual oficial fica versionado no repositório, sem links públicos
    # ou armazenamento fora do PRISMA.
    caminho_manual = (
        Path(__file__).resolve().parent.parent
        / "manuais"
        / "Manual_de_Uso_Analise_de_Logger_PRISMA_COI_Aegea.pdf"
    )
    if caminho_manual.is_file():
        try:
            bytes_manual = caminho_manual.read_bytes()
            if not bytes_manual.startswith(b"%PDF"):
                raise ValueError("O arquivo do manual não é um PDF válido.")
            st.download_button(
                "📘 Baixar manual de utilização (PDF)",
                data=bytes_manual,
                file_name="Manual_de_Uso_Analise_de_Logger_PRISMA_COI_Aegea.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="btn_download_manual_analise_logger",
            )
        except (OSError, ValueError):
            st.button(
                "📘 Manual indisponível",
                disabled=True,
                use_container_width=True,
                key="btn_manual_indisponivel",
            )
            st.caption("Falha ao carregar o manual. Verifique o arquivo em manuais/.")
    else:
        st.button(
            "📘 Baixar manual de utilização (PDF)",
            disabled=True,
            use_container_width=True,
            key="btn_manual_nao_publicado",
        )
        st.caption("Manual ainda não publicado em manuais/.")
    st.divider()
    st.caption(
        "Análises por upload, sem gravação de arquivos no PRISMA. "
        "Os resultados descrevem somente os dados enviados."
    )

st.title("📡 Análise de Logger")
st.caption("Módulo 4 — Ferramentas Operacionais")
st.divider()
aba_monitor, aba_analise = st.tabs(["📈 Monitor de Variação", "📉 Análise de Comportamento"])
with aba_monitor:
    _monitor()
with aba_analise:
    _comportamento()
