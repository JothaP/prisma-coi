"""PRISMA — Plataforma de Recursos Integrados para Suporte, Monitoramento e Análises | Hub Central.

Login individual PRISMA, gestão de acessos e interface corporativa.
Permissões e autenticação centralizadas em auth.py e prisma_backend.py.
"""

from html import escape
import streamlit as st

from prisma_backend import ErroPRISMA, entrar, cadastrar, confirmar, reenviar_confirmacao, pedir_recuperacao, mudar_senha_token, configuracao
from auth import (
    iniciar_sessao,
    verificar_autenticacao,
    fazer_logout,
    tem_acesso_modulo,
)
from prisma_manutencao import (
    aviso_login, verificar_manutencao, monitorar_manutencao, situacao, texto_fim,
    MODULOS as NOMES_MODULOS_MANUTENCAO, FUSO as FUSO_MANUTENCAO,
    _horario as _horario_manutencao,
)


# ============================================================
# CONFIGURAÇÃO E IDENTIDADE
# ============================================================
st.set_page_config(
    page_title="PRISMA | Inteligência Operacional Integrada",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def aplicar_estilo_sai(modo_escuro=False):
    """Tema autossuficiente; não exige imagens, fontes nem arquivos CSS."""
    cores = (
        """
        --sai-bg: #0f1830;
        --sai-surface: #162347;
        --sai-surface-2: #1c2d5b;
        --sai-border: #304a7c;
        --sai-text: #edf5ff;
        --sai-muted: #b7c6da;
        --sai-chip-bg: #1f5130;
        --sai-chip-text: #bff3c5;
        --sai-shadow: 0 12px 35px rgba(0, 0, 0, 0.20);
        """
        if modo_escuro
        else
        """
        --sai-bg: #f2f3f6;
        --sai-surface: #ffffff;
        --sai-surface-2: #f8fffb;
        --sai-border: #d8dfe7;
        --sai-text: #122033;
        --sai-muted: #5e6d7f;
        --sai-chip-bg: #e7f8ee;
        --sai-chip-text: #3a7d40;
        --sai-shadow: 0 13px 36px rgba(0, 39, 189, 0.08);
        """
    )
    st.markdown(
        """
        <style>
        :root { __THEME__ --sai-primary: #0027bd; --sai-primary-light: #2447d4;
                --sai-accent: #6ae0cb; --sai-accent-soft: #dff8f3; --sai-green:#6ce27b;
                --sai-green-dark:#3a7d40; --sai-pink:#ff8fe1; }
        [data-testid="stSidebarNav"] { display: none !important; }
        [data-testid="stHeader"] { background: transparent !important; }
        [data-testid="stAppViewContainer"], .stApp {
            background: var(--sai-bg) !important;
            color: var(--sai-text) !important;
        }
        .block-container {
            max-width: 1380px;
            padding: 2.2rem 2.2rem 3rem;
        }
        .stApp, .stApp button, .stApp input {
            font-family: "Segoe UI", Inter, -apple-system, BlinkMacSystemFont, sans-serif;
        }
        .sai-hero {
            position: relative;
            overflow: hidden;
            border-radius: 24px;
            padding: 30px 36px;
            background: linear-gradient(115deg, #0027bd 4%, #3a7d40 62%, #6ae0cb 108%);
            color: #ffffff;
            box-shadow: 0 16px 38px rgba(0, 39, 189, .18);
            margin: 0 0 20px;
        }
        .sai-hero:before {
            content:""; position:absolute; width:350px; height:350px; border-radius:50%;
            border: 1px solid rgba(255,255,255,.12); top:-220px; right:-40px;
            box-shadow: 0 0 0 65px rgba(255,255,255,.035), 0 0 0 130px rgba(255,255,255,.025);
            pointer-events:none;
        }
        .sai-brandline {display:flex; align-items:center; gap:18px; position:relative; z-index:1;}
        .sai-emblem {
            display:grid; place-items:center; flex:0 0 66px; width:66px; height:66px;
            border:1px solid rgba(255,255,255,.24); background:rgba(255,255,255,.13);
            border-radius:17px;
        }
        .sai-eyebrow {font-size:11px; letter-spacing:2.2px; font-weight:750; color:#dff8f3; margin:0 0 5px;}
        .sai-brand-title {font-size:43px; letter-spacing:-1.5px; font-weight:850; line-height:1.08; color:white; margin:0;}
        .sai-brand-title span {font-size:21px; letter-spacing:0; font-weight:450; opacity:.86; margin-left:12px;}
        .sai-brand-subtitle {font-size:14px; color:#d7eafa; margin-top:7px;}
        .sai-hero-bottom {display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:23px;
            border-top:1px solid rgba(255,255,255,.18); padding-top:15px; flex-wrap:wrap; position:relative;z-index:1;}
        .sai-hero-bottom span {font-size:12px; color:#e3f3fc;}
        .sai-hero-dot {display:inline-block; height:7px; width:7px; border-radius:50%; background:#6ce27b;
            margin-right:8px; vertical-align:middle; box-shadow:0 0 0 3px rgba(108,226,123,.16);}
        .sai-toolbar {display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:38px;}
        .sai-user-chip {display:inline-flex;align-items:center;gap:8px;background:var(--sai-surface);
            border:1px solid var(--sai-border);padding:8px 13px;border-radius:999px;color:var(--sai-text);
            font-size:12px;box-shadow:var(--sai-shadow);}
        .sai-user-dot {height:9px;width:9px;background:#6ae0cb;border-radius:50%;}
        .sai-section-eyebrow {color:var(--sai-muted);font-size:11px;letter-spacing:1.75px;
            font-weight:800;margin:16px 0 5px;}
        .sai-section-title {color:var(--sai-text);font-size:27px;font-weight:760;letter-spacing:-.7px;line-height:1.2;margin:0;}
        .sai-section-caption {color:var(--sai-muted);font-size:13px;margin:8px 0 23px;}
        /* Os contêineres com chave são usados apenas como cartões visuais. */
        [class*="st-key-sai_card_"] {
            background:var(--sai-surface) !important;
            border:1px solid var(--sai-border) !important;
            border-radius:18px !important;
            box-shadow:var(--sai-shadow);
            padding:18px 19px 15px !important;
            transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;
        }
        [class*="st-key-sai_card_"]:hover {
            transform:translateY(-2px);border-color:rgba(0,39,189,.32) !important;
            box-shadow:0 14px 34px rgba(0,39,189,.11);
        }
        .sai-card-top {display:flex;align-items:center;justify-content:space-between;gap:8px;}
        .sai-card-id {font-size:11px;letter-spacing:1.35px;color:var(--sai-muted);font-weight:800;}
        .sai-card-icon {display:grid;place-items:center;width:42px;height:42px;border-radius:13px;
            font-size:22px;background:var(--sai-surface-2);border:1px solid var(--sai-border);}
        .sai-card-heading {font-size:17px;font-weight:750;line-height:1.3;color:var(--sai-text);margin:14px 0 7px;}
        .sai-card-description {font-size:12px;line-height:1.48;color:var(--sai-muted);height:51px;max-height:51px;overflow:hidden;margin:0 0 13px;}
        .sai-card-status {display:inline-flex;align-items:center;gap:6px;font-size:10px;letter-spacing:.15px;
            font-weight:760;color:var(--sai-chip-text);background:var(--sai-chip-bg);padding:5px 10px;
            border-radius:999px;margin:0 0 8px;}
        .sai-card-status.is-locked {background:var(--sai-surface-2);color:var(--sai-muted);
            border:1px solid var(--sai-border);}
        [class*="st-key-sai_card_"] .stButton > button {
            width:100%;border-radius:10px;font-weight:700;min-height:40px;
            background:#0027bd;color:#fff;border:1px solid #0027bd;
            transition:background .15s ease,box-shadow .15s ease;
        }
        [class*="st-key-sai_card_"] .stButton > button p {color:inherit !important;}
        [class*="st-key-sai_card_"] .stButton > button:hover:enabled {
            background:#3a7d40;border-color:#3a7d40;color:white;
        }
        [class*="st-key-sai_card_"] .stButton > button:disabled {
            opacity:1 !important;cursor:not-allowed;background:var(--sai-surface-2) !important;
            color:var(--sai-muted) !important;border:1px solid var(--sai-border) !important;
        }
        [class*="st-key-sai_card_"] .stButton > button:disabled p {color:var(--sai-muted) !important;}
        .sai-footer {border-top:1px solid var(--sai-border);margin-top:27px;padding-top:18px;
            color:var(--sai-muted);font-size:12px;}
        /* Tela de autenticação */
        [class*="st-key-sai_login_card"] {
            border:1px solid var(--sai-border) !important;background:var(--sai-surface) !important;
            box-shadow:var(--sai-shadow);border-radius:20px !important;padding:18px 27px 21px !important;
        }
        .sai-login-title {font-size:22px;font-weight:750;color:var(--sai-text);margin:5px 0 6px;}
        .sai-login-caption {font-size:13px;line-height:1.5;color:var(--sai-muted);margin-bottom:18px;}
        [class*="st-key-sai_login_card"] .stFormSubmitButton button {
            background:#0027bd !important;color:white !important;border-color:#0027bd !important;
            border-radius:10px;min-height:44px;font-weight:750;
        }
        [class*="st-key-sai_login_card"] .stFormSubmitButton button:hover {
            background:#3a7d40 !important;border-color:#3a7d40 !important;color:white !important;
        }
        [class*="st-key-sai_login_card"] .stFormSubmitButton button p {color:white !important;}
        .sai-login-small {text-align:center;color:var(--sai-muted);font-size:12px;margin-top:12px;}
        .sai-logout .stButton button {border:1px solid var(--sai-border);}
        @media(max-width:760px) {
            .block-container {padding:1.5rem 1rem 2rem;}
            .sai-hero {padding:23px 20px;border-radius:18px;}
            .sai-brandline {align-items:flex-start;gap:12px;}
            .sai-emblem {width:52px;height:52px;flex-basis:52px;border-radius:13px;}
            .sai-brand-title {font-size:34px;}
            .sai-brand-title span {display:block;font-size:15px;margin:6px 0 0;}
            .sai-eyebrow {font-size:9px;letter-spacing:1.4px;}
        }
        </style>
        """.replace("__THEME__", cores),
        unsafe_allow_html=True,
    )


# Logotipo construído no próprio Hub (sem dependências ou arquivos externos).
SIMBOLO_SAI = """
<svg xmlns="http://www.w3.org/2000/svg" width="43" height="43" viewBox="0 0 64 64" fill="none" aria-hidden="true">
  <path d="M32 7C23 20 16 28 16 39a16 16 0 0 0 32 0C48 28 41 20 32 7Z" fill="#6AE0CB" opacity=".96"/>
  <path d="M18 40c6-6 11-6 17-1s10 6 14 3" stroke="#0027BD" stroke-width="4" stroke-linecap="round"/>
  <path d="M23 48c5-3 10-3 15 0" stroke="#0027BD" stroke-width="3" stroke-linecap="round" opacity=".9"/>
</svg>
"""


def exibir_cabecalho_sai(*, login=False):
    sobrelinha = "ACESSO À PLATAFORMA" if login else "INTELIGÊNCIA E CONTROLE OPERACIONAL"
    subtitulo = (
        "Ambiente de acesso seguro aos módulos operacionais."
        if login else "Plataforma de Recursos Integrados para Suporte, Monitoramento e Análises"
    )
    st.markdown(
        f"""
        <div class="sai-hero">
          <div class="sai-brandline">
            <div class="sai-emblem">{SIMBOLO_SAI}</div>
            <div>
              <div class="sai-eyebrow">{sobrelinha}</div>
              <div class="sai-brand-title">PRISMA</div>
              <div class="sai-brand-subtitle">{subtitulo}</div>
            </div>
          </div>
          <div class="sai-hero-bottom">
            <span><i class="sai-hero-dot"></i> COI — Centro de Operações Integradas</span>
            <span>Dados &nbsp;·&nbsp; Análise &nbsp;·&nbsp; Decisão</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# PRISMA — LOGIN INDIVIDUAL | MODELO 1 (LAYOUT DIVIDIDO)
# Painel institucional à esquerda e formulários à direita.
# O layout não modifica as regras de autenticação do backend.
# ============================================================
def aplicar_layout_login_prisma():
    st.markdown("""
    <style>
    [data-testid="stAppViewContainer"], .stApp {
        background: #f5f7fb !important;
    }
    .block-container {
        max-width: 1360px !important;
        padding: 2rem 1.4rem 3rem !important;
    }
    /* Três partes visuais fazem um único cartão, sem a faixa superior pesada. */
    [class*="st-key-prisma_login_layout"] [data-testid="stHorizontalBlock"] {
        gap: 0 !important;
        align-items: stretch !important;
    }
    [class*="st-key-prisma_login_layout"] [data-testid="column"] {
        min-width: 0;
    }
    [class*="st-key-prisma_login_left"] {
        position: relative;
        isolation: isolate;
        overflow: hidden;
        min-height: 615px;
        height: 100%;
        border-radius: 26px 0 0 26px;
        padding: 4.2rem 3.6rem !important;
        background: linear-gradient(138deg, #0027bd 0%, #1662b6 38%, #36b8b6 75%, #6ce27b 112%);
        box-shadow: 0 19px 48px rgba(23, 64, 116, .12);
    }
    [class*="st-key-prisma_login_left"]::before,
    [class*="st-key-prisma_login_left"]::after {
        content: "";
        position: absolute;
        pointer-events: none;
        border: 1px solid rgba(255,255,255,.15);
        border-radius: 45%;
        z-index: -1;
    }
    [class*="st-key-prisma_login_left"]::before {
        width: 700px; height: 700px; top: -500px; right: -270px;
        background: rgba(255,255,255,.05);
        transform: rotate(-25deg);
    }
    [class*="st-key-prisma_login_left"]::after {
        width: 830px; height: 540px; bottom: -385px; left: -250px;
        background: rgba(106,224,203,.20);
        transform: rotate(-23deg);
        box-shadow: 90px -35px 0 18px rgba(108,226,123,.08);
    }
    .prisma-brand-pane {
        display: flex;
        flex-direction: column;
        justify-content: center;
        min-height: 470px;
        position: relative;
    }
    .prisma-brand-head {display:flex;align-items:center;gap:22px;flex-wrap:wrap;}
    .prisma-brand-mark {
        width:84px;height:84px;border-radius:23px;
        background:rgba(255,255,255,.12);
        border:1px solid rgba(255,255,255,.3);
        box-shadow: inset 0 1px 14px rgba(255,255,255,.07);
        display:grid;place-items:center;
    }
    .prisma-brand-mark svg {width:55px;height:55px;}
    .prisma-brand-name {
        font:800 clamp(2.8rem, 4vw, 4.2rem)/1.1 'Segoe UI', sans-serif;
        letter-spacing:-.045em;color:#fff;
    }
    .prisma-brand-description {
        color:#f2fbff;font-size:clamp(1rem,1.4vw,1.3rem);
        line-height:1.65;max-width:430px;margin:22px 0 0 108px;
    }
    .prisma-brand-rule {
        width:52px;height:6px;border-radius:6px;background:#6ce27b;
        margin:31px 0 17px 108px;
    }
    .prisma-brand-office {
        color:#f5ffff;font-size:14px;font-weight:650;
        margin-left:108px;line-height:1.6;
    }
    [class*="st-key-prisma_login_right"] {
        min-height:615px;
        height:100%;
        padding:3.15rem 3.2rem 2.3rem !important;
        background:#fff;
        border:1px solid #e7edf5;
        border-left:0;
        border-radius:0 26px 26px 0;
        box-shadow:0 19px 48px rgba(23,64,116,.10);
    }
    .prisma-access-label {
        color:#234c70;
        text-transform:uppercase;
        letter-spacing:.21em;
        font-size:11px;
        font-weight:800;
        margin:7px 0 17px;
    }
    .prisma-login-heading {
        color:#122033;font-size:25px;font-weight:750;
        letter-spacing:-.025em;line-height:1.25;margin:0 0 8px;
    }
    .prisma-login-hint {font-size:13px;color:#66788c;line-height:1.6;margin-bottom:21px;}
    [class*="st-key-prisma_login_right"] [data-testid="stForm"] {
        border:0 !important; padding:0 !important; background:transparent;
    }
    /* A cor herdada pelo Streamlit estava deixando os rótulos brancos
       sobre o painel branco. Forçamos contraste apenas no login PRISMA. */
    [class*="st-key-prisma_login_right"] [data-testid="stWidgetLabel"],
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] label {
        color:#24354e !important;
        -webkit-text-fill-color:#24354e !important;
        opacity:1 !important;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stWidgetLabel"] p,
    [class*="st-key-prisma_login_right"] [data-testid="stWidgetLabel"] span,
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] label p,
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] label span {
        color:#24354e !important;
        -webkit-text-fill-color:#24354e !important;
        opacity:1 !important;
        font-size:0.91rem !important;
        font-weight:650 !important;
        line-height:1.45 !important;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] {
        margin-bottom:0.14rem;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] input::placeholder {
        color:#68778a !important;
        -webkit-text-fill-color:#68778a !important;
        opacity:1 !important;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] input {
        min-height:44px;
        border-radius:11px;
        background:#fff;
        color:#17283e;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stTextInput"] input:focus {
        border-color:#0027bd;
        box-shadow:0 0 0 1px #0027bd;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stFormSubmitButton"] button {
        width:100%;border-radius:11px;
        min-height:47px;font-weight:750;
        background:#0027bd;border-color:#0027bd;color:white;
        margin-top:11px;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stFormSubmitButton"] button:hover:enabled {
        background:#3a7d40;border-color:#3a7d40;color:#fff;
    }
    [class*="st-key-prisma_login_right"] [data-testid="stFormSubmitButton"] button:disabled {
        background:#7b93d8;border-color:#7b93d8;color:white;
    }
    [class*="st-key-prisma_login_right"] .stButton button[kind="secondary"] {
        background:transparent;border:0;box-shadow:none;
        color:#0027bd;font-weight:650;padding:.2rem;
        min-height:36px;
    }
    [class*="st-key-prisma_login_right"] .stButton button[kind="secondary"]:hover {
        background:#edf7f6;color:#3a7d40;
    }
    .prisma-login-status {
        border-radius:13px;
        padding:15px 16px;
        background:#f0f7fc;
        border:1px solid #e0edf6;
        color:#365474;
        font-size:12px;
        line-height:1.6;
        margin-top:22px;
    }
    .prisma-login-status strong {color:#234c70;font-weight:750;}
    .prisma-login-mini-footer {
        text-align:center;font-size:11px;color:#8a9bab;
        margin-top:17px;
    }
    @media(max-width:950px) {
        [class*="st-key-prisma_login_left"] {min-height:300px;padding:2.5rem 2rem !important;}
        .prisma-brand-pane {min-height:210px;}
        .prisma-brand-description,.prisma-brand-rule,.prisma-brand-office {margin-left:0;}
        [class*="st-key-prisma_login_right"] {padding:2.4rem 2rem !important;}
    }
    @media(max-width:700px) {
        .block-container {padding:1rem .8rem 2rem !important;}
        [class*="st-key-prisma_login_layout"] [data-testid="stHorizontalBlock"] {
            flex-direction:column !important;
        }
        [class*="st-key-prisma_login_left"] {
            min-height:235px;border-radius:22px 22px 0 0;padding:1.8rem 1.7rem !important;
        }
        .prisma-brand-pane {min-height:168px;}
        .prisma-brand-mark {width:58px;height:58px;border-radius:15px;}
        .prisma-brand-mark svg {width:37px;height:37px;}
        .prisma-brand-name {font-size:2.25rem;}
        .prisma-brand-description {font-size:12px;line-height:1.4;margin-top:12px;}
        .prisma-brand-rule {margin:13px 0 8px;width:40px;height:4px;}
        .prisma-brand-office {font-size:11px;}
        [class*="st-key-prisma_login_right"] {
            min-height:auto;border:1px solid #e7edf5;border-top:0;
            border-radius:0 0 22px 22px;padding:1.7rem 1.5rem !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)


def definir_etapa_login(etapa: str):
    """Navegação simples entre login, cadastro, confirmação e recuperação."""
    st.session_state["prisma_etapa_login"] = etapa


if not verificar_autenticacao():
    aplicar_estilo_sai()
    aplicar_layout_login_prisma()
    aviso_login()
    if "prisma_etapa_login" not in st.session_state:
        st.session_state["prisma_etapa_login"] = "Entrar"
    # O formulário somente pode enviar dados quando as credenciais do Supabase
    # estiverem configuradas. Exibir a causa (apenas nomes das variáveis) evita
    # que o botão desabilitado pareça ser um erro dos campos preenchidos.
    mensagem_configuracao = None
    try:
        configuracao()
        login_configurado = True
    except ErroPRISMA as erro:
        login_configurado = False
        mensagem_configuracao = str(erro)

    with st.container(key="prisma_login_layout"):
        painel_esquerdo, painel_direito = st.columns([1.1, 1], gap="small")
        with painel_esquerdo:
            with st.container(key="prisma_login_left"):
                st.markdown(f"""
                <div class="prisma-brand-pane">
                  <div class="prisma-brand-head">
                    <div class="prisma-brand-mark">{SIMBOLO_SAI}</div>
                    <div class="prisma-brand-name">PRISMA</div>
                  </div>
                  <div class="prisma-brand-description">
                    Plataforma de Recursos Integrados para<br>
                    Suporte, Monitoramento e Análises
                  </div>
                  <div class="prisma-brand-rule"></div>
                  <div class="prisma-brand-office">COI — Centro de Operações Integradas</div>
                </div>
                """, unsafe_allow_html=True)

        with painel_direito:
            with st.container(key="prisma_login_right"):
                st.markdown('<div class="prisma-access-label">ACESSO À PLATAFORMA</div>',
                            unsafe_allow_html=True)
                etapa = st.session_state["prisma_etapa_login"]
                if not login_configurado:
                    st.error(
                        "**Conexão com o Supabase ainda não configurada.** "
                        "O botão de envio permanece bloqueado até corrigir o Secrets. "
                        f"**Detalhe:** {escape(mensagem_configuracao or 'Verifique as configurações do aplicativo.')} "
                        "Acesse Streamlit → Settings → Secrets, corrija as variáveis e salve."
                    )
                if etapa == "Entrar":
                    st.markdown('<div class="prisma-login-heading">Bem-vindo ao PRISMA</div>'
                                '<div class="prisma-login-hint">Entre com suas credenciais corporativas para continuar.</div>',
                                unsafe_allow_html=True)
                    with st.form("prisma_entrar"):
                        email = st.text_input("E-mail corporativo", key="login_email")
                        senha = st.text_input("Senha", type="password", key="login_senha")
                        enviar = st.form_submit_button("Entrar", use_container_width=True,
                                                      type="primary", disabled=not login_configurado)
                    if enviar:
                        try:
                            tokens = entrar(email, senha)
                            if iniciar_sessao(tokens):
                                st.rerun()
                            st.warning("Seu e-mail está confirmado, mas o acesso ainda não foi aprovado ou está bloqueado.")
                        except ErroPRISMA as erro:
                            st.error(str(erro))
                    col_criar, col_recuperar = st.columns(2)
                    with col_criar:
                        st.button("Criar uma conta", key="prisma_link_criar", use_container_width=True,
                                  on_click=definir_etapa_login, args=("Criar conta",))
                    with col_recuperar:
                        st.button("Esqueci minha senha", key="prisma_link_recuperar", use_container_width=True,
                                  on_click=definir_etapa_login, args=("Recuperar senha",))
                    st.button("Confirmar e-mail / Reenviar código", key="prisma_link_confirmar",
                              use_container_width=True, on_click=definir_etapa_login,
                              args=("Confirmar e-mail",))

                elif etapa == "Criar conta":
                    st.markdown('<div class="prisma-login-heading">Criar conta</div>'
                                '<div class="prisma-login-hint">Use seu e-mail corporativo para solicitar acesso.</div>',
                                unsafe_allow_html=True)
                    with st.form("prisma_criar"):
                        nome = st.text_input("Nome completo")
                        email = st.text_input("E-mail corporativo")
                        senha = st.text_input("Criar senha (mínimo de 12 caracteres)", type="password")
                        repetida = st.text_input("Confirmar senha", type="password")
                        enviar = st.form_submit_button("Enviar código de confirmação",
                                                      use_container_width=True, disabled=not login_configurado)
                    if enviar:
                        if senha != repetida:
                            st.error("As senhas não coincidem.")
                        else:
                            try:
                                cadastrar(email, senha, nome)
                                st.success("Confira o e-mail e utilize 'Confirmar e-mail' para finalizar.")
                            except ErroPRISMA as erro:
                                st.error(str(erro))
                    voltar, confirmar_col = st.columns(2)
                    with voltar:
                        st.button("Voltar ao login", key="prisma_voltar_cadastro",
                                  use_container_width=True,on_click=definir_etapa_login,args=("Entrar",))
                    with confirmar_col:
                        st.button("Confirmar e-mail", key="prisma_ir_confirmacao",
                                  use_container_width=True,on_click=definir_etapa_login,args=("Confirmar e-mail",))

                elif etapa == "Confirmar e-mail":
                    st.markdown('<div class="prisma-login-heading">Confirmar e-mail</div>'
                                '<div class="prisma-login-hint">Digite o código recebido para concluir o cadastro.</div>',
                                unsafe_allow_html=True)
                    with st.form("prisma_confirmar"):
                        email = st.text_input("E-mail cadastrado")
                        codigo = st.text_input("Código recebido", max_chars=12)
                        enviar = st.form_submit_button("Confirmar cadastro", use_container_width=True,
                                                      disabled=not login_configurado)
                    if enviar:
                        try:
                            confirmar(email, codigo)
                            st.success("E-mail confirmado. Aguarde a aprovação do administrador.")
                        except ErroPRISMA as erro:
                            st.error(str(erro))
                    st.caption("Não recebeu o código? Confira a caixa de spam ou solicite um novo envio.")
                    with st.form("prisma_reenviar_confirmacao"):
                        email_reenvio = st.text_input("E-mail cadastrado para reenvio",
                                                       key="prisma_reenvio_email")
                        reenviar = st.form_submit_button(
                            "Reenviar código de confirmação",
                            use_container_width=True, disabled=not login_configurado)
                    if reenviar:
                        try:
                            reenviar_confirmacao(email_reenvio)
                            st.success(
                                "Se houver um cadastro aguardando confirmação, um novo código "
                                "será enviado. Confira também o spam e o lixo eletrônico."
                            )
                        except ErroPRISMA as erro:
                            st.error(str(erro))
                    st.button("Voltar ao login", key="prisma_voltar_confirmacao",
                              on_click=definir_etapa_login,args=("Entrar",))

                else:  # Recuperar senha
                    st.markdown('<div class="prisma-login-heading">Recuperar acesso</div>'
                                '<div class="prisma-login-hint">Solicite um código para redefinir sua senha.</div>',
                                unsafe_allow_html=True)
                    with st.form("prisma_recuperar"):
                        email = st.text_input("E-mail cadastrado")
                        enviar = st.form_submit_button("Enviar código de recuperação",
                                                      use_container_width=True, disabled=not login_configurado)
                    if enviar:
                        try:
                            pedir_recuperacao(email)
                            st.success("Se a conta existir, enviaremos as instruções ao e-mail informado.")
                        except ErroPRISMA as erro:
                            st.error(str(erro))
                    with st.form("prisma_redefinir"):
                        email_codigo = st.text_input("E-mail para confirmação da recuperação")
                        codigo = st.text_input("Código de recuperação", max_chars=12)
                        senha = st.text_input("Nova senha", type="password")
                        enviar = st.form_submit_button("Definir nova senha", use_container_width=True,
                                                      disabled=not login_configurado)
                    if enviar:
                        try:
                            t = confirmar(email_codigo, codigo, tipo="recovery")
                            mudar_senha_token(t["access_token"], senha)
                            st.success("Senha alterada. Faça login com a nova senha.")
                        except (ErroPRISMA, KeyError) as erro:
                            st.error(str(erro))
                    st.button("Voltar ao login", key="prisma_voltar_recuperacao",
                              on_click=definir_etapa_login,args=("Entrar",))

                st.markdown('<div class="prisma-login-mini-footer">Acesso restrito a usuários autorizados.</div>',
                            unsafe_allow_html=True)
    st.stop()


# ============================================================
# HUB CENTRAL — SOMENTE APRESENTAÇÃO RENOVADA
# ============================================================
# Bloqueio global precede cards e ações. Para sessões já abertas, o pulso
# verifica a janela mesmo sem clique ou recarga manual.
verificar_manutencao(None)
monitorar_manutencao(None)
if "modo_escuro_hub" not in st.session_state:
    st.session_state.modo_escuro_hub = False

modo_escuro_hub = st.session_state.modo_escuro_hub
aplicar_estilo_sai(modo_escuro_hub)
exibir_cabecalho_sai()

perfil_interno = str(st.session_state.get("perfil", "")).strip().lower()
perfil_consulta = perfil_interno == "operador"
perfil_atual = "CONSULTA" if perfil_consulta else perfil_interno.upper()
usuario_atual = str(st.session_state.get("usuario_logado", ""))

barra_usuario, barra_tema = st.columns([4, 1.35], vertical_alignment="center")
with barra_usuario:
    st.markdown(
        '<div class="sai-toolbar">'
        f'<span class="sai-user-chip"><span class="sai-user-dot"></span> '
        f'Perfil conectado: <b>{escape(perfil_atual)}</b></span>'
        f'<span class="sai-user-chip">Usuário: <b>{escape(usuario_atual)}</b></span>'
        '</div>',
        unsafe_allow_html=True,
    )
with barra_tema:
    st.session_state.modo_escuro_hub = st.toggle(
        "🌙 Modo escuro", value=modo_escuro_hub, key="toggle_modo_escuro_hub"
    )
    if st.session_state.modo_escuro_hub != modo_escuro_hub:
        st.rerun()

st.markdown(
    '<div class="sai-section-eyebrow">PLATAFORMA OPERACIONAL</div>'
    '<div class="sai-section-title">Explore os módulos do PRISMA</div>'
    '<div class="sai-section-caption">Selecione a ferramenta para continuar. '
    'Os acessos respeitam as permissões do seu perfil.</div>',
    unsafe_allow_html=True,
)

# Autorização original: todas as permissões continuam sendo lidas de auth.py.
acessos = {str(numero): tem_acesso_modulo(str(numero)) for numero in range(1, 10)}
acesso_teste = tem_acesso_modulo("teste")
# Os cartões dos módulos em manutenção são desabilitados para usuários
# comuns, sem retirar as permissões que já possuem no cadastro.
bloqueios = {}
avisos_programados = {}
if perfil_interno != "admin":
    for modulo_codigo in [str(n) for n in range(1, 10)] + ["teste"]:
        bloqueios[modulo_codigo], avisos_programados[modulo_codigo] = situacao(modulo_codigo)
    avisos_exibidos = []
    for modulo_codigo, aviso in avisos_programados.items():
        if aviso and aviso.get("escopo") == "modulo":
            titulo = NOMES_MODULOS_MANUTENCAO.get(modulo_codigo, modulo_codigo)
            inicio_local = _horario_manutencao(aviso["inicio"]).astimezone(
                FUSO_MANUTENCAO
            ).strftime("%d/%m/%Y às %H:%M")
            avisos_exibidos.append(
                f"{titulo}: início {inicio_local}; retorno: {texto_fim(aviso)}"
            )
    if avisos_exibidos:
        st.info("🛠️ Manutenções próximas — " + " | ".join(avisos_exibidos))

# A posição e os destinos são os mesmos do Hub anterior.
MODULOS = [
    {
        "numero": "1", "titulo": "Baixa Pressão", "icone": "💧",
        "descricao": "Medições e indicadores de pressão para análise operacional.",
        "botao": "Acessar Baixa Pressão", "chave": "acessar_baixa_pressao",
        "pagina": "pages/1_Baixa_Pressao.py", "autorizado": acessos["1"],
        "visivel": True,
    },
    {
        "numero": "2", "titulo": "Mapeamento de Pressão", "icone": "🗺️",
        "descricao": "Cadastro e visualização geográfica das medições de pressão.",
        "botao": "Acessar Mapeamento", "chave": "acessar_mapeamento_pressao",
        "pagina": "pages/2_Mapeamento_Pressao.py", "autorizado": acessos["2"],
        "visivel": True,
    },
    {
        "numero": "3", "titulo": "Farol Operacional", "icone": "🚰",
        "descricao": "Concentrações de O.S., eventos e evidências operacionais.",
        "botao": "Acessar Farol Operacional", "chave": "acessar_farol_operacional",
        "pagina": "pages/3_Farol_Operacional.py", "autorizado": acessos["3"],
        "visivel": True,
    },
    {
        "numero": "4", "titulo": "Ferramentas Operacionais", "icone": "🛠️",
        "descricao": "Gerador de lotes, painéis e cards operacionais.",
        "botao": "Acessar Ferramentas", "chave": "acessar_ferramentas_operacionais",
        "pagina": "pages/4_Ferramentas_Operacionais.py", "autorizado": acessos["4"],
        "visivel": True,
    },
    {
        "numero": "5", "titulo": "Cadastros e Consultas COI", "icone": "📋",
        "descricao": "Referências de bairros, regionais e cadastros operacionais.",
        "botao": "Acessar Cadastros e Consultas", "chave": "acessar_cadastros_consultas",
        "pagina": "pages/5_Cadastros_Consultas_COI.py", "autorizado": acessos["5"],
        "visivel": True,
    },
    {
        "numero": "6", "titulo": "Ferramentas Adicionais", "icone": "🔧",
        "descricao": "Ferramentas para Excel, documentos e cálculos de apoio.",
        "botao": "Acessar Ferramentas Adicionais", "chave": "acessar_ferramentas_adicionais",
        "pagina": "pages/6_Ferramentas_Adicionais.py", "autorizado": acessos["6"],
        "visivel": True,
    },
    {
        "numero": "7", "titulo": "Relatórios Integrados", "icone": "📊",
        "descricao": "Relatórios de concentrações e monitoramento de pressão.",
        "botao": "Acessar Relatórios Integrados", "chave": "acessar_relatorios_integrados",
        "pagina": "pages/7_Relatorios_Integrados.py", "autorizado": acessos["7"],
        "visivel": True,
    },
    {
        # Módulo 8 segue acessível a TODOS os usuários autenticados.
        "numero": "8", "titulo": "Consulta Operacional", "icone": "🔎",
        "descricao": "Consulta geográfica de matrículas, ativos e loggers.",
        "botao": "Acessar Consulta Operacional", "chave": "acessar_consulta_operacional",
        "pagina": "pages/8_Consulta_Operacional.py", "autorizado": acessos["8"],
        "visivel": True,
    },
    {
        "numero": "9", "titulo": "Gestão de Ativos", "icone": "🏭",
        "descricao": "Cadastro e histórico de ativos georreferenciados no Supabase.",
        "botao": "Acessar Gestão de Ativos", "chave": "acessar_gestao_ativos",
        "pagina": "pages/10_Gestao_de_Ativos.py", "autorizado": acessos["9"],
        "visivel": True,
    },
    {
        "numero": "teste", "titulo": "Ambiente de Teste", "icone": "🧪",
        "descricao": "Validação de funcionalidades antes da publicação.",
        "botao": "Acessar Ambiente de Teste", "chave": "acessar_ambiente_teste",
        "pagina": "pages/Ambiente_de_Teste.py", "autorizado": acesso_teste,
        "visivel": True,
    },
]


def exibir_cartao(modulo):
    """Exibe informações e botão nativos, com a mesma permissão de antes."""
    autorizado = bool(modulo["autorizado"])
    ambiente = modulo["numero"] == "teste"
    indice = str(modulo["numero"])
    bloqueado_manutencao = bloqueios.get(indice)
    liberado = autorizado and not bloqueado_manutencao
    rotulo = "AMBIENTE DE TESTE" if ambiente else f"MÓDULO {indice.zfill(2)}"
    status = (
        "Em manutenção" if bloqueado_manutencao
        else ("Acesso restrito" if autorizado else "Sem permissão") if ambiente
        else ("Disponível" if autorizado else "Acesso bloqueado")
    )
    status_classe = "" if liberado else " is-locked"
    with st.container(border=True, key=f"sai_card_{indice}"):
        st.markdown(
            '<div class="sai-card-top">'
            f'<div class="sai-card-id">{escape(rotulo)}</div>'
            f'<div class="sai-card-icon">{escape(modulo["icone"])}</div>'
            '</div>'
            f'<div class="sai-card-heading">{escape(modulo["titulo"])}</div>'
            f'<div class="sai-card-description">{escape(modulo["descricao"])}</div>'
            f'<div class="sai-card-status{status_classe}">'
            f'{"●" if autorizado else "○"} {escape(status)}</div>',
            unsafe_allow_html=True,
        )
        if st.button(
            modulo["botao"],
            type="primary" if autorizado else "secondary",
            use_container_width=True,
            key=modulo["chave"],
            disabled=not liberado,
        ):
            st.switch_page(modulo["pagina"])
        if bloqueado_manutencao:
            st.caption("Restabelecimento: " + texto_fim(bloqueado_manutencao))


# Módulos 8 e 9 encerram a terceira linha; Ambiente de Teste inicia a quarta.
for indice_linha in range((len(MODULOS) + 2) // 3):
    colunas = st.columns(3, gap="large")
    for indice_coluna in range(3):
        posicao = indice_linha * 3 + indice_coluna
        if posicao >= len(MODULOS):
            continue
        modulo = MODULOS[posicao]
        if modulo["visivel"]:
            with colunas[indice_coluna]:
                exibir_cartao(modulo)

if tem_acesso_modulo("gestao") or perfil_interno == "admin":
    st.divider()
    st.subheader("🔐 Administração")
    if tem_acesso_modulo("gestao"):
        if st.button("Gestão de Acessos — Usuários e permissões", type="primary", use_container_width=True):
            st.switch_page("pages/9_Gestao_de_Acessos.py")
    if perfil_interno == "admin":
        if st.button(
            "🛠️ Controle de Manutenção — Geral e por Módulo",
            key="abrir_controle_manutencao_prisma",
            use_container_width=True,
        ):
            st.switch_page("pages/11_Controle_de_Manutencao.py")

st.markdown(
    '<div class="sai-footer">PRISMA — Plataforma de Recursos Integrados para Suporte, Monitoramento e Análises'
    ' &nbsp; · &nbsp; COI — Centro de Operações Integradas</div>',
    unsafe_allow_html=True,
)
if st.button("Encerrar Sessão / Sair", key="encerrar_sessao"):
    fazer_logout()
    st.success("Sessão encerrada com sucesso!")
    st.rerun()
