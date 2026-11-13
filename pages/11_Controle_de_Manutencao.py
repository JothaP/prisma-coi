"""PRISMA — administração de manutenções globais e por módulo."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from auth import exigir_acesso_modulo
from prisma_backend import ErroPRISMA
from prisma_manutencao import MODULOS, agendar, cancelar, historico, status_historico, _horario

st.set_page_config(page_title="PRISMA | Controle de Manutenção", page_icon="🛠️", layout="wide")
exigir_acesso_modulo("gestao")
if st.session_state.get("perfil") != "admin":
    st.error("Acesso exclusivo para administradores.")
    st.stop()

FUSO = ZoneInfo("America/Fortaleza")
agora = datetime.now(FUSO)
st.title("🛠️ Controle de Manutenção — PRISMA")
st.caption(
    "Agende indisponibilidades globais ou por módulo sem interromper os processos automáticos "
    "de importação. Administradores autorizados continuam podendo acessar e testar os módulos."
)
if st.button("← Voltar ao Hub Central", key="prisma_man_voltar"):
    st.switch_page("app.py")

st.subheader("Criar período de manutenção")
# Controles de modalidade e datas fora do formulário: em Streamlit,
# seleções dentro de form não re-renderizam campos condicionais.
abrangencia = st.radio(
    "Modalidade", ["Toda a plataforma", "Apenas um módulo"],
    horizontal=True, key="prisma_man_modalidade",
)
modulo = None
if abrangencia == "Apenas um módulo":
    modulo = st.selectbox(
        "Módulo a suspender", list(MODULOS),
        format_func=lambda codigo: f"{codigo.upper()} — {MODULOS[codigo]}",
    )

modo_inicio = st.radio(
    "Início", ["Imediato", "Agendar data e horário"],
    horizontal=True, key="prisma_man_inicio_tipo",
)
data_inicio = hora_inicio = None
if modo_inicio == "Agendar data e horário":
    ci, ch = st.columns(2)
    with ci:
        data_inicio = st.date_input("Data de início", value=agora.date())
    with ch:
        hora_inicio = st.time_input(
            "Horário de início (Teresina)", value=agora.time().replace(second=0, microsecond=0),
            step=60,
        )
modo_fim = st.radio(
    "Término", ["Data e horário definidos", "Até encerramento manual"],
    horizontal=True, key="prisma_man_fim_tipo",
)
data_fim = hora_fim = None
if modo_fim == "Data e horário definidos":
    ci, ch = st.columns(2)
    with ci:
        data_fim = st.date_input(
            "Data de término", value=(agora + timedelta(hours=1)).date(),
        )
    with ch:
        hora_fim = st.time_input(
            "Horário de término (Teresina)",
            value=(agora + timedelta(hours=1)).time().replace(second=0, microsecond=0),
            step=60,
        )
with st.form("prisma_form_manutencao", clear_on_submit=False):
    motivo = st.text_input(
        "Motivo interno da manutenção", max_chars=500,
        placeholder="Ex.: atualização de módulos e verificação de integridade",
    )
    mensagem = st.text_area(
        "Mensagem apresentada aos usuários (opcional)", max_chars=600,
        placeholder="Ex.: estamos atualizando o sistema; retornaremos em breve.",
        height=90,
    )
    confirmar = st.checkbox(
        "Confirmo a suspensão de acesso dos usuários durante o período. "
        "Administradores poderão continuar acessando.",
    )
    enviar = st.form_submit_button(
        "Criar manutenção", type="primary", use_container_width=True,
    )

if enviar:
    try:
        inicio = (datetime.now(FUSO) if modo_inicio == "Imediato"
                  else datetime.combine(data_inicio, hora_inicio, tzinfo=FUSO))
        fim = (datetime.combine(data_fim, hora_fim, tzinfo=FUSO)
               if modo_fim == "Data e horário definidos" else None)
        if not confirmar:
            st.warning("Confirme a suspensão de acesso antes de criar a manutenção.")
        elif len(motivo.strip()) < 5:
            st.warning("Informe um motivo interno com pelo menos 5 caracteres.")
        elif fim is not None and fim <= inicio:
            st.warning("O término deve ser posterior ao início da manutenção.")
        else:
            protocolo = agendar(
                "global" if abrangencia == "Toda a plataforma" else "modulo",
                modulo, inicio, fim, motivo, mensagem,
            )
            st.success(f"Manutenção #{protocolo} registrada. Os horários serão aplicados automaticamente.")
    except (ErroPRISMA, ValueError) as erro:
        st.error(f"Não foi possível salvar a manutenção: {erro}")

st.divider()
st.subheader("Manutenções registradas")
st.caption("O aviso aos usuários começa 30 minutos antes do início. "
           "Durante a manutenção, usuários comuns ficam bloqueados no Hub e nos módulos selecionados.")
if st.button("Atualizar situação", key="prisma_man_atualizar"):
    st.rerun()
try:
    registros = historico()
except ErroPRISMA as erro:
    st.error(f"Não foi possível obter o histórico: {erro}")
    st.stop()

if not registros:
    st.info("Nenhuma manutenção programada. Todos os módulos permanecem disponíveis.")
else:
    dados = []
    for r in registros:
        ini = _horario(r.get("inicio"))
        fim = _horario(r.get("fim"))
        dados.append({
            "ID": r["id"],
            "Modalidade": "Global" if r["escopo"] == "global" else
                         f"Módulo {r['modulo']} — {MODULOS.get(str(r['modulo']), '')}",
            "Início (Teresina)": ini.astimezone(FUSO).strftime("%d/%m/%Y %H:%M"),
            "Término (Teresina)": fim.astimezone(FUSO).strftime("%d/%m/%Y %H:%M") if fim else "Manual",
            "Situação": status_historico(r),
            "Motivo": r["motivo"],
        })
    st.dataframe(pd.DataFrame(dados), width="stretch", hide_index=True)
    editaveis = [
        r for r in registros if status_historico(r) in ("Em andamento", "Agendada")
    ]
    if editaveis:
        st.subheader("Encerrar ou cancelar manutenção")
        with st.form("prisma_form_encerrar_manutencao"):
            indice = st.selectbox(
                "Manutenção a encerrar",
                list(range(len(editaveis))),
                format_func=lambda i: (
                    f"#{editaveis[i]['id']} — "
                    f"{'Global' if editaveis[i]['escopo']=='global' else MODULOS.get(str(editaveis[i]['modulo']), 'Módulo')} "
                    f"({status_historico(editaveis[i])})"
                ),
            )
            confirmar_cancelamento = st.checkbox(
                "Confirmo o cancelamento ou encerramento antecipado desta manutenção.",
            )
            executar = st.form_submit_button(
                "Encerrar manutenção selecionada", type="secondary",
                use_container_width=True,
            )
        if executar:
            if not confirmar_cancelamento:
                st.warning("Confirme o encerramento antes de prosseguir.")
            else:
                try:
                    cancelar(editaveis[indice]["id"])
                    st.success("Manutenção encerrada/cancelada. O acesso será restabelecido automaticamente.")
                    st.rerun()
                except (ErroPRISMA, ValueError) as erro:
                    st.error(f"Não foi possível encerrar a manutenção: {erro}")
    else:
        st.caption("Não há manutenções abertas ou agendadas para cancelar.")

st.divider()
st.caption(
    "Os horários usam America/Fortaleza (Teresina, UTC−3). "
    "O bloqueio é reavaliado durante a navegação e nas sessões abertas. "
    "Rotinas agendadas no GitHub Actions permanecem independentes."
)
