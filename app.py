"""Página inicial provisória do PRISMA para criação do novo app no Streamlit.

Não conecta a bases nem solicita credenciais. Substituir pelo app definitivo
quando a nova autenticação e os controles de acesso estiverem implementados.
"""
import streamlit as st

st.set_page_config(
    page_title="PRISMA | COI",
    page_icon="🔷",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      #MainMenu, footer {visibility: hidden;}
      .block-container {padding-top: 5rem; max-width: 720px;}
      .prisma-header {text-align:center; padding: 2.5rem 1rem 1.5rem;}
      .prisma-mark {font-size: 3rem; font-weight: 800; letter-spacing: .12em;
          color: #1D5487; margin-bottom: .4rem;}
      .prisma-description {font-size: 1.08rem; line-height: 1.6;
          color: #526577; margin: 0 auto; max-width: 560px;}
      .prisma-line {height: 3px; width: 72px; margin: 1.5rem auto;
          border-radius: 4px; background: #2D92AD;}
      .prisma-caption {text-align:center; color:#677687; font-size: .92rem;}
    </style>
    <div class="prisma-header">
      <div class="prisma-mark">PRISMA</div>
      <div class="prisma-description">
        Plataforma de Recursos Integrados para Suporte,
        Monitoramento e Análises
      </div>
      <div class="prisma-line"></div>
      <div class="prisma-caption">INTELIGÊNCIA OPERACIONAL INTEGRADA · COI</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.info(
    "**Ambiente em implantação.** A nova autenticação e os módulos do PRISMA "
    "serão disponibilizados após a configuração e os testes de acesso."
)
