import math

import streamlit as st


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def _numero(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _formatar_numero(valor, casas=4):
    if valor is None:
        return ""

    if abs(valor) < 0.0000001:
        valor = 0

    texto = f"{valor:,.{casas}f}"

    # Formatação brasileira
    texto = texto.replace(",", "X")
    texto = texto.replace(".", ",")
    texto = texto.replace("X", ".")

    return texto


def _resultado(titulo, valor, unidade):
    st.success(
        f"**{titulo}: { _formatar_numero(valor) } {unidade}**"
    )


# ============================================================
# 6.2.1 — 💧 VAZÃO
# ============================================================

def render_vazao():
    st.markdown("### 💧 Vazão")

    st.caption(
        "Calcule vazão, volume ou tempo a partir de dois valores conhecidos."
    )

    operacao = st.radio(
        "O que deseja calcular?",
        [
            "Vazão",
            "Volume",
            "Tempo",
        ],
        horizontal=True,
        key="calculadora_vazao_operacao",
    )

    st.divider()

    if operacao == "Vazão":

        col1, col2 = st.columns(2)

        with col1:
            volume = st.number_input(
                "Volume",
                min_value=0.0,
                step=1.0,
                key="vazao_volume",
            )

        with col2:
            unidade_volume = st.selectbox(
                "Unidade do volume",
                [
                    "Litros (L)",
                    "m³",
                ],
                key="vazao_unidade_volume",
            )

        tempo = st.number_input(
            "Tempo",
            min_value=0.0001,
            step=1.0,
            key="vazao_tempo",
        )

        unidade_tempo = st.selectbox(
            "Unidade do tempo",
            [
                "segundos",
                "minutos",
                "horas",
                "dias",
            ],
            key="vazao_unidade_tempo",
        )

        unidade_saida = st.selectbox(
            "Unidade da vazão",
            [
                "L/s",
                "L/min",
                "L/h",
                "m³/h",
                "m³/dia",
            ],
            key="vazao_unidade_saida",
        )

        if st.button(
            "🧮 Calcular Vazão",
            type="primary",
            use_container_width=True,
            key="calcular_vazao",
        ):
            volume_litros = (
                volume
                if unidade_volume == "Litros (L)"
                else volume * 1000
            )

            tempo_segundos = tempo

            if unidade_tempo == "minutos":
                tempo_segundos *= 60
            elif unidade_tempo == "horas":
                tempo_segundos *= 3600
            elif unidade_tempo == "dias":
                tempo_segundos *= 86400

            vazao_l_s = (
                volume_litros / tempo_segundos
            )

            fatores = {
                "L/s": vazao_l_s,
                "L/min": vazao_l_s * 60,
                "L/h": vazao_l_s * 3600,
                "m³/h": vazao_l_s * 3.6,
                "m³/dia": vazao_l_s * 86.4,
            }

            _resultado(
                "Vazão",
                fatores[unidade_saida],
                unidade_saida,
            )

    elif operacao == "Volume":

        col1, col2 = st.columns(2)

        with col1:
            vazao = st.number_input(
                "Vazão",
                min_value=0.0,
                step=1.0,
                key="volume_vazao",
            )

        with col2:
            unidade_vazao = st.selectbox(
                "Unidade da vazão",
                [
                    "L/s",
                    "L/min",
                    "L/h",
                    "m³/h",
                    "m³/dia",
                ],
                key="volume_unidade_vazao",
            )

        tempo = st.number_input(
            "Tempo",
            min_value=0.0001,
            step=1.0,
            key="volume_tempo",
        )

        unidade_tempo = st.selectbox(
            "Unidade do tempo",
            [
                "segundos",
                "minutos",
                "horas",
                "dias",
            ],
            key="volume_unidade_tempo",
        )

        unidade_saida = st.selectbox(
            "Unidade do volume",
            [
                "Litros (L)",
                "m³",
            ],
            key="volume_unidade_saida",
        )

        if st.button(
            "🧮 Calcular Volume",
            type="primary",
            use_container_width=True,
            key="calcular_volume",
        ):
            fatores_vazao = {
                "L/s": 1,
                "L/min": 1 / 60,
                "L/h": 1 / 3600,
                "m³/h": 1000 / 3600,
                "m³/dia": 1000 / 86400,
            }

            fatores_tempo = {
                "segundos": 1,
                "minutos": 60,
                "horas": 3600,
                "dias": 86400,
            }

            vazao_l_s = (
                vazao
                * fatores_vazao[unidade_vazao]
            )

            tempo_segundos = (
                tempo
                * fatores_tempo[unidade_tempo]
            )

            volume_litros = (
                vazao_l_s * tempo_segundos
            )

            if unidade_saida == "m³":
                resultado = volume_litros / 1000
            else:
                resultado = volume_litros

            _resultado(
                "Volume",
                resultado,
                unidade_saida,
            )

    else:

        col1, col2 = st.columns(2)

        with col1:
            volume = st.number_input(
                "Volume",
                min_value=0.0,
                step=1.0,
                key="tempo_volume",
            )

            unidade_volume = st.selectbox(
                "Unidade do volume",
                [
                    "Litros (L)",
                    "m³",
                ],
                key="tempo_unidade_volume",
            )

        with col2:
            vazao = st.number_input(
                "Vazão",
                min_value=0.0001,
                step=1.0,
                key="tempo_vazao",
            )

            unidade_vazao = st.selectbox(
                "Unidade da vazão",
                [
                    "L/s",
                    "L/min",
                    "L/h",
                    "m³/h",
                    "m³/dia",
                ],
                key="tempo_unidade_vazao",
            )

        unidade_saida = st.selectbox(
            "Unidade do tempo",
            [
                "segundos",
                "minutos",
                "horas",
                "dias",
            ],
            key="tempo_unidade_saida",
        )

        if st.button(
            "🧮 Calcular Tempo",
            type="primary",
            use_container_width=True,
            key="calcular_tempo_vazao",
        ):
            volume_litros = (
                volume
                if unidade_volume == "Litros (L)"
                else volume * 1000
            )

            fatores_vazao = {
                "L/s": 1,
                "L/min": 1 / 60,
                "L/h": 1 / 3600,
                "m³/h": 1000 / 3600,
                "m³/dia": 1000 / 86400,
            }

            vazao_l_s = (
                vazao
                * fatores_vazao[unidade_vazao]
            )

            tempo_segundos = (
                volume_litros / vazao_l_s
            )

            fatores_saida = {
                "segundos": 1,
                "minutos": 60,
                "horas": 3600,
                "dias": 86400,
            }

            resultado = (
                tempo_segundos
                / fatores_saida[unidade_saida]
            )

            _resultado(
                "Tempo",
                resultado,
                unidade_saida,
            )


# ============================================================
# 6.2.2 — 📈 PRESSÃO
# ============================================================

def render_pressao():
    st.markdown("### 📈 Pressão")

    st.caption(
        "Converta valores de pressão entre as principais unidades."
    )

    valor = st.number_input(
        "Valor",
        min_value=0.0,
        step=1.0,
        key="pressao_valor",
    )

    unidade_entrada = st.selectbox(
        "Unidade de entrada",
        [
            "mca",
            "bar",
            "kPa",
            "psi",
            "atm",
        ],
        key="pressao_unidade_entrada",
    )

    unidade_saida = st.selectbox(
        "Unidade de saída",
        [
            "mca",
            "bar",
            "kPa",
            "psi",
            "atm",
        ],
        key="pressao_unidade_saida",
    )

    if st.button(
        "🧮 Converter Pressão",
        type="primary",
        use_container_width=True,
        key="calcular_pressao",
    ):
        # Conversão para mca
        fatores = {
            "mca": 1,
            "bar": 10.19716213,
            "kPa": 0.1019716213,
            "psi": 0.7032496149,
            "atm": 10.33227453,
        }

        valor_mca = valor * fatores[unidade_entrada]

        resultado = (
            valor_mca / fatores[unidade_saida]
        )

        _resultado(
            "Pressão",
            resultado,
            unidade_saida,
        )


# ============================================================
# 6.2.3 — 🚰 CONSUMO
# ============================================================

def render_consumo():
    st.markdown("### 🚰 Consumo")

    st.caption(
        "Calcule consumo médio a partir de volume e período."
    )

    volume = st.number_input(
        "Volume consumido",
        min_value=0.0,
        step=1.0,
        key="consumo_volume",
    )

    unidade_volume = st.selectbox(
        "Unidade do volume",
        [
            "Litros (L)",
            "m³",
        ],
        key="consumo_unidade_volume",
    )

    periodo = st.number_input(
        "Período",
        min_value=0.0001,
        step=1.0,
        key="consumo_periodo",
    )

    unidade_periodo = st.selectbox(
        "Unidade do período",
        [
            "dias",
            "meses",
            "horas",
        ],
        key="consumo_unidade_periodo",
    )

    unidade_saida = st.selectbox(
        "Unidade do consumo médio",
        [
            "L/dia",
            "m³/dia",
            "L/h",
            "m³/h",
        ],
        key="consumo_unidade_saida",
    )

    if st.button(
        "🧮 Calcular Consumo",
        type="primary",
        use_container_width=True,
        key="calcular_consumo",
    ):
        volume_litros = (
            volume
            if unidade_volume == "Litros (L)"
            else volume * 1000
        )

        fatores_periodo = {
            "dias": 1,
            "meses": 30,
            "horas": 1 / 24,
        }

        dias = (
            periodo
            * fatores_periodo[unidade_periodo]
        )

        consumo_l_dia = (
            volume_litros / dias
        )

        fatores_saida = {
            "L/dia": consumo_l_dia,
            "m³/dia": consumo_l_dia / 1000,
            "L/h": consumo_l_dia / 24,
            "m³/h": consumo_l_dia / 24000,
        }

        _resultado(
            "Consumo médio",
            fatores_saida[unidade_saida],
            unidade_saida,
        )


# ============================================================
# 6.2.4 — 📦 VOLUME
# ============================================================

def render_volume():
    st.markdown("### 📦 Volume")

    st.caption(
        "Calcule o volume de reservatórios retangulares ou cilíndricos."
    )

    tipo = st.radio(
        "Tipo de reservatório",
        [
            "Retangular",
            "Cilíndrico",
        ],
        horizontal=True,
        key="volume_tipo",
    )

    unidade = st.selectbox(
        "Unidade das dimensões",
        [
            "metros",
            "centímetros",
        ],
        key="volume_unidade_dimensoes",
    )

    fator = 1 if unidade == "metros" else 0.01

    if tipo == "Retangular":

        comprimento = st.number_input(
            "Comprimento",
            min_value=0.0,
            step=0.1,
            key="volume_comprimento",
        )

        largura = st.number_input(
            "Largura",
            min_value=0.0,
            step=0.1,
            key="volume_largura",
        )

        altura = st.number_input(
            "Altura",
            min_value=0.0,
            step=0.1,
            key="volume_altura",
        )

    else:

        diametro = st.number_input(
            "Diâmetro",
            min_value=0.0,
            step=0.1,
            key="volume_diametro",
        )

        altura = st.number_input(
            "Altura",
            min_value=0.0,
            step=0.1,
            key="volume_altura_cilindro",
        )

    unidade_saida = st.selectbox(
        "Unidade do resultado",
        [
            "m³",
            "Litros (L)",
        ],
        key="volume_unidade_saida",
    )

    if st.button(
        "🧮 Calcular Volume",
        type="primary",
        use_container_width=True,
        key="calcular_volume_reservatorio",
    ):
        if tipo == "Retangular":

            comprimento_m = comprimento * fator
            largura_m = largura * fator
            altura_m = altura * fator

            volume_m3 = (
                comprimento_m
                * largura_m
                * altura_m
            )

        else:

            diametro_m = diametro * fator
            altura_m = altura * fator

            raio_m = diametro_m / 2

            volume_m3 = (
                math.pi
                * raio_m ** 2
                * altura_m
            )

        if unidade_saida == "Litros (L)":
            resultado = volume_m3 * 1000
        else:
            resultado = volume_m3

        _resultado(
            "Volume",
            resultado,
            unidade_saida,
        )


# ============================================================
# 6.2.5 — ⏱️ TEMPO
# ============================================================

def render_tempo():
    st.markdown("### ⏱️ Tempo")

    st.caption(
        "Converta valores de tempo entre segundos, minutos, horas e dias."
    )

    valor = st.number_input(
        "Valor",
        min_value=0.0,
        step=1.0,
        key="tempo_conversao_valor",
    )

    unidade_entrada = st.selectbox(
        "Unidade de entrada",
        [
            "segundos",
            "minutos",
            "horas",
            "dias",
        ],
        key="tempo_conversao_entrada",
    )

    unidade_saida = st.selectbox(
        "Unidade de saída",
        [
            "segundos",
            "minutos",
            "horas",
            "dias",
        ],
        key="tempo_conversao_saida",
    )

    if st.button(
        "🧮 Converter Tempo",
        type="primary",
        use_container_width=True,
        key="calcular_tempo_conversao",
    ):
        fatores = {
            "segundos": 1,
            "minutos": 60,
            "horas": 3600,
            "dias": 86400,
        }

        segundos = (
            valor * fatores[unidade_entrada]
        )

        resultado = (
            segundos / fatores[unidade_saida]
        )

        _resultado(
            "Tempo",
            resultado,
            unidade_saida,
        )


# ============================================================
# 6.2.6 — 📅 SLA
# ============================================================

def render_sla():
    st.markdown("### 📅 SLA")

    st.caption(
        "Calcule uma data/hora de vencimento adicionando um prazo "
        "a uma data/hora inicial."
    )

    data_inicial = st.datetime_input(
        "Data e hora inicial",
        key="sla_data_inicial",
    )

    prazo = st.number_input(
        "Prazo",
        min_value=0.0,
        step=1.0,
        key="sla_prazo",
    )

    unidade_prazo = st.selectbox(
        "Unidade do prazo",
        [
            "horas",
            "dias",
            "minutos",
        ],
        key="sla_unidade_prazo",
    )

    considerar_dias_uteis = st.checkbox(
        "Considerar somente dias úteis",
        value=False,
        key="sla_dias_uteis",
    )

    if st.button(
        "🧮 Calcular SLA",
        type="primary",
        use_container_width=True,
        key="calcular_sla",
    ):
        if unidade_prazo == "minutos":
            minutos = prazo

        elif unidade_prazo == "horas":
            minutos = prazo * 60

        else:
            minutos = prazo * 24 * 60

        if not considerar_dias_uteis:

            from datetime import timedelta

            resultado = (
                data_inicial
                + timedelta(minutes=minutos)
            )

        else:

            from datetime import timedelta

            resultado = data_inicial
            minutos_restantes = minutos

            while minutos_restantes > 0:

                resultado += timedelta(minutes=1)

                if resultado.weekday() < 5:
                    minutos_restantes -= 1

        st.success(
            f"**Vencimento do SLA:** "
            f"{resultado.strftime('%d/%m/%Y %H:%M')}"
        )


# ============================================================
# 6.2.7 — 🔄 CONVERSOR DE UNIDADES
# ============================================================

def render_conversor_unidades():
    st.markdown("### 🔄 Conversor de Unidades")

    st.caption(
        "Converta unidades de comprimento, área, volume, massa e vazão."
    )

    categoria = st.selectbox(
        "Categoria",
        [
            "Comprimento",
            "Área",
            "Volume",
            "Massa",
            "Vazão",
        ],
        key="conversor_categoria",
    )

    unidades = {
        "Comprimento": {
            "m": 1,
            "cm": 0.01,
            "mm": 0.001,
            "km": 1000,
        },
        "Área": {
            "m²": 1,
            "cm²": 0.0001,
            "km²": 1_000_000,
        },
        "Volume": {
            "m³": 1,
            "L": 0.001,
            "mL": 0.000001,
            "cm³": 0.000001,
        },
        "Massa": {
            "kg": 1,
            "g": 0.001,
            "mg": 0.000001,
            "tonelada": 1000,
        },
        "Vazão": {
            "L/s": 1,
            "L/min": 1 / 60,
            "L/h": 1 / 3600,
            "m³/h": 1000 / 3600,
            "m³/dia": 1000 / 86400,
        },
    }

    opcoes = list(
        unidades[categoria].keys()
    )

    valor = st.number_input(
        "Valor",
        min_value=0.0,
        step=1.0,
        key="conversor_valor",
    )

    col1, col2 = st.columns(2)

    with col1:
        unidade_entrada = st.selectbox(
            "De",
            opcoes,
            key="conversor_unidade_entrada",
        )

    with col2:
        unidade_saida = st.selectbox(
            "Para",
            opcoes,
            key="conversor_unidade_saida",
        )

    if st.button(
        "🔄 Converter",
        type="primary",
        use_container_width=True,
        key="calcular_conversor_unidades",
    ):
        valor_base = (
            valor
            * unidades[categoria][unidade_entrada]
        )

        resultado = (
            valor_base
            / unidades[categoria][unidade_saida]
        )

        _resultado(
            "Resultado",
            resultado,
            unidade_saida,
        )
