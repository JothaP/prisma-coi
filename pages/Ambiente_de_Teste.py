"""Laboratório PRISMA: exclusivamente Farol Operacional V3.

O ambiente usa dados do Módulo 3 apenas em leitura. Não importa a página de
produção nem executa limpeza, gravações ou rotinas históricas daquele módulo.
"""
import streamlit as st
from auth import exigir_acesso_modulo

st.set_page_config(
    page_title="Farol Operacional V3 — Ambiente de Teste",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="collapsed",
)
exigir_acesso_modulo("teste")

st.title("🧪 Ambiente de Teste — Farol Operacional V3")
st.caption(
    "Laboratório exclusivo do Farol V3. O Módulo 3 e seus dados de produção "
    "não serão alterados pelas simulações."
)

from farol_v3_teste import renderizar

renderizar()
