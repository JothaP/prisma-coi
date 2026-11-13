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
# Os controles de data/hora permanecem montados, com keys permanentes.
# Recalcular 'value=agora' a cada rerun ou desmontar os widgets quando
# muda a modalidade podia substituir silenciosamente os horários escolhidos.
st.session_state.setdefault("prisma_man_data_inicio", agora.date())
st.session_state.setdefault(
    "prisma_man_hora_inicio", agora.time().replace(second=0, microsecond=0)
)
st.session_state.setdefault(
    "prisma_man_data_fim", (agora + timedelta(hours=1)).date()
)
st.session_state.setdefault(
    "prisma_man_hora_fim", (agora + timedelta(hours=1)).time().replace(
        second=0, microsecond=0
    )
)

abrangencia = st.radio(
    "Modalidade", ["Toda a plataforma", "Apenas um módulo"],
    horizontal=True, key="prisma_man_modalidade",
)
modulo = st.selectbox(
    "Módulo a suspender", list(MODULOS),
    format_func=lambda codigo: f"{codigo.upper()} — {MODULOS[codigo]}",
    key="prisma_man_modulo",
    disabled=abrangencia != "Apenas um módulo",
)
modo_inicio = st.radio(
    "Início", ["Imediato", "Agendar data e horário"],
    horizontal=True, key="prisma_man_inicio_tipo",
)
ci, ch = st.columns(2)
with ci:
    data_inicio = st.date_input(
        "Data de início", format="DD/MM/YYYY",
        key="prisma_man_data_inicio",
        disabled=modo_inicio == "Imediato",
    )
with ch:
    hora_inicio = st.time_input(
        "Horário de início (Teresina)", step=60,
        key="prisma_man_hora_inicio",
        disabled=modo_inicio == "Imediato",
    )
modo_fim = st.radio(
    "Término", ["Data e horário definidos", "Até encerramento manual"],
    horizontal=True, key="prisma_man_fim_tipo",
)
ci, ch = st.columns(2)
with ci:
    data_fim = st.date_input(
        "Data de término", format="DD/MM/YYYY",
        key="prisma_man_data_fim",
        disabled=modo_fim == "Até encerramento manual",
    )
with ch:
    hora_fim = st.time_input(
        "Horário de término (Teresina)", step=60,
        key="prisma_man_hora_fim",
        disabled=modo_fim == "Até encerramento manual",
    )

# Prévia calculada pelos MESMOS valores que serão enviados na gravação.
inicio_selecionado = (
    datetime.combine(data_inicio, hora_inicio, tzinfo=FUSO)
    if modo_inicio == "Agendar data e horário" else None
)
fim_selecionado = (
    datetime.combine(data_fim, hora_fim, tzinfo=FUSO)
    if modo_fim == "Data e horário definidos" else None
)
inicio_texto = (
    inicio_selecionado.strftime("%d/%m/%Y às %H:%M")
    if inicio_selecionado else "imediatamente após confirmar"
)
fim_texto = (
    fim_selecionado.strftime("%d/%m/%Y às %H:%M")
    if fim_selecionado else "até liberação manual"
)
area_texto = "Toda a plataforma" if abrangencia == "Toda a plataforma" else MODULOS[modulo]
st.markdown("**Confira o período antes de salvar**")
st.info(
    f"**Abrangência:** {area_texto}  |  **Início:** {inicio_texto}  |  "
    f"**Término:** {fim_texto}  |  **Fuso:** Teresina (UTC−3)"
)
# Mudou a seleção? Exige nova confirmação explícita para o novo período.
assinatura_previa = (
    abrangencia, modulo if abrangencia == "Apenas um módulo" else None,
    inicio_selecionado.isoformat() if inicio_selecionado else "imediato",
    fim_selecionado.isoformat() if fim_selecionado else "manual",
)
if st.session_state.get("prisma_man_ultima_previa") != assinatura_previa:
    st.session_state["prisma_man_confirmar"] = False
    st.session_state["prisma_man_ultima_previa"] = assinatura_previa

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
        "Conferi as datas, horários e abrangência exibidos acima e confirmo "
        "a suspensão. Administradores continuarão podendo acessar.",
        key="prisma_man_confirmar",
    )
    enviar = st.form_submit_button(
        "Criar manutenção", type="primary", use_container_width=True,
    )

if enviar:
    try:
        inicio = (
            datetime.now(FUSO)
            if modo_inicio == "Imediato" else inicio_selecionado
        )
        fim = fim_selecionado
        if not confirmar:
            st.warning("Confirme a suspensão de acesso antes de criar a manutenção.")
        elif len(motivo.strip()) < 5:
            st.warning("Informe um motivo interno com pelo menos 5 caracteres.")
        elif fim is not None and fim <= inicio:
            st.warning("O término deve ser posterior ao início da manutenção.")
        else:
            confirmado_banco = agendar(
                "global" if abrangencia == "Toda a plataforma" else "modulo",
                modulo, inicio, fim, motivo, mensagem,
            )
            inicio_confirmado = _horario(
                confirmado_banco["inicio"]
            ).astimezone(FUSO).strftime("%d/%m/%Y às %H:%M")
            fim_confirmado = (
                _horario(confirmado_banco["fim"]).astimezone(FUSO).strftime(
                    "%d/%m/%Y às %H:%M"
                ) if confirmado_banco["fim"] else "liberação manual"
            )
            st.success(
                f"Manutenção #{confirmado_banco['id']} confirmada no Supabase: "
                f"{inicio_confirmado} até {fim_confirmado} "
                f"(horário de Teresina)."
            )
            # Na próxima interação exige uma nova confirmação, sem alterar
            # o valor do checkbox após ele já ter sido renderizado.
            st.session_state["prisma_man_ultima_previa"] = None
    except (ErroPRISMA, ValueError) as erro:
        st.error(f"Problema ao registrar ou conferir a manutenção: {erro}")

st.divider()
st.subheader("Manutenções registradas")
st.caption(
    "Os avisos aparecem desde o agendamento no Hub e na tela de login, "
    "com destaque nos últimos 30 minutos. Durante a manutenção, "
    "o acesso comum é suspenso na abrangência selecionada."
)
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
