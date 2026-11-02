"""PRISMA — Plataforma de Recursos Integrados para Suporte, Monitoramento e Análises | Hub Central.

Atualização exclusivamente de apresentação do Hub Central e da tela de login.
A autenticação, as permissões de auth.py, os identificadores de sessão,
os caminhos das páginas e as posições dos nove cartões foram preservados.
"""

from html import escape
import streamlit as st

from prisma_backend import ErroPRISMA, entrar, cadastrar, confirmar, pedir_recuperacao, mudar_senha_token, configuracao
from auth import (
    iniciar_sessao,
    verificar_autenticacao,
    fazer_logout,
    tem_acesso_modulo,
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
        --sai-bg: #101b2a;
        --sai-surface: #19283b;
        --sai-surface-2: #1e3148;
        --sai-border: #30465e;
        --sai-text: #edf5ff;
        --sai-muted: #a9bed3;
        --sai-chip-bg: #244a50;
        --sai-chip-text: #a9f1df;
        --sai-shadow: 0 12px 35px rgba(0, 0, 0, 0.16);
        """
        if modo_escuro
        else
        """
        --sai-bg: #f2f6fb;
        --sai-surface: #ffffff;
        --sai-surface-2: #f7faff;
        --sai-border: #dfebf4;
        --sai-text: #20334d;
        --sai-muted: #61758b;
        --sai-chip-bg: #e6f7f2;
        --sai-chip-text: #187765;
        --sai-shadow: 0 13px 36px rgba(20, 59, 97, 0.055);
        """
    )
    st.markdown(
        """
        <style>
        :root { __THEME__ --sai-primary: #164574; --sai-primary-light: #206096;
                --sai-accent: #18b3b1; --sai-accent-soft: #a9e9e5; }
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
            background: linear-gradient(112deg, #102f55 2%, #164e77 60%, #107b85 110%);
            color: #ffffff;
            box-shadow: 0 16px 38px rgba(13, 56, 93, .16);
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
        .sai-eyebrow {font-size:11px; letter-spacing:2.2px; font-weight:750; color:#b6e8ee; margin:0 0 5px;}
        .sai-brand-title {font-size:43px; letter-spacing:-1.5px; font-weight:850; line-height:1.08; color:white; margin:0;}
        .sai-brand-title span {font-size:21px; letter-spacing:0; font-weight:450; opacity:.86; margin-left:12px;}
        .sai-brand-subtitle {font-size:14px; color:#d7eafa; margin-top:7px;}
        .sai-hero-bottom {display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:23px;
            border-top:1px solid rgba(255,255,255,.18); padding-top:15px; flex-wrap:wrap; position:relative;z-index:1;}
        .sai-hero-bottom span {font-size:12px; color:#e3f3fc;}
        .sai-hero-dot {display:inline-block; height:7px; width:7px; border-radius:50%; background:#57ded0;
            margin-right:8px; vertical-align:middle; box-shadow:0 0 0 3px rgba(87,222,208,.16);}
        .sai-toolbar {display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:38px;}
        .sai-user-chip {display:inline-flex;align-items:center;gap:8px;background:var(--sai-surface);
            border:1px solid var(--sai-border);padding:8px 13px;border-radius:999px;color:var(--sai-text);
            font-size:12px;box-shadow:var(--sai-shadow);}
        .sai-user-dot {height:9px;width:9px;background:#14a49a;border-radius:50%;}
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
            transform:translateY(-2px);border-color:rgba(24,154,175,.45) !important;
            box-shadow:0 14px 34px rgba(24,69,116,.12);
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
            background:#15517d;color:#fff;border:1px solid #15517d;
            transition:background .15s ease,box-shadow .15s ease;
        }
        [class*="st-key-sai_card_"] .stButton > button p {color:inherit !important;}
        [class*="st-key-sai_card_"] .stButton > button:hover:enabled {
            background:#0a7190;border-color:#0a7190;color:white;
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
            background:#15517d !important;color:white !important;border-color:#15517d !important;
            border-radius:10px;min-height:44px;font-weight:750;
        }
        [class*="st-key-sai_login_card"] .stFormSubmitButton button:hover {
            background:#0a7190 !important;border-color:#0a7190 !important;color:white !important;
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
  <path d="M32 7C23 20 16 28 16 39a16 16 0 0 0 32 0C48 28 41 20 32 7Z" fill="#B3F1F0" opacity=".96"/>
  <path d="M18 40c6-6 11-6 17-1s10 6 14 3" stroke="#0D6692" stroke-width="4" stroke-linecap="round"/>
  <path d="M23 48c5-3 10-3 15 0" stroke="#0D6692" stroke-width="3" stroke-linecap="round" opacity=".9"/>
</svg>
"""


def exibir_cabecalho_sai(*, login=False):
    sobrelinha = "ACESSO À PLATAFORMA" if login else "INTELIGÊNCIA E CONTROLE OPERACIONAL"
    subtitulo = (
        "Ambiente de acesso seguro aos módulos operacionais."
        if login else "Monitoramento, análises e ferramentas para apoio à decisão."
    )
    st.markdown(
        f"""
        <div class="sai-hero">
          <div class="sai-brandline">
            <div class="sai-emblem">{SIMBOLO_SAI}</div>
            <div>
              <div class="sai-eyebrow">{sobrelinha}</div>
              <div class="sai-brand-title">PRISMA <span>Suporte, Monitoramento e Análises</span></div>
              <div class="sai-brand-subtitle">{subtitulo}</div>
            </div>
          </div>
          <div class="sai-hero-bottom">
            <span><i class="sai-hero-dot"></i> COI — Controle Operacional Integrado</span>
            <span>Dados &nbsp;·&nbsp; Análise &nbsp;·&nbsp; Decisão</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# IDENTIDADE E AUTENTICAÇÃO INDIVIDUAL PRISMA
# ============================================================
if not verificar_autenticacao():
    aplicar_estilo_sai()
    exibir_cabecalho_sai(login=True)
    esq, centro, direita = st.columns([1, 1.35, 1], gap="large")
    with centro:
        with st.container(border=True,key="sai_login_card"):
            try:
                configuracao()
            except ErroPRISMA as erro:
                st.error(str(erro))
                st.info("Configure o Supabase e execute o script prisma_setup.sql antes de disponibilizar o cadastro.")
                st.stop()
            etapa=st.radio("Acesso",["Entrar","Criar conta","Confirmar e-mail","Recuperar senha"],
                           horizontal=True,key="prisma_etapa_login")
            if etapa=="Entrar":
                st.markdown('<div class="sai-login-title">Acesso individual</div>',unsafe_allow_html=True)
                with st.form("prisma_entrar"):
                    email=st.text_input("E-mail corporativo",key="login_email")
                    senha=st.text_input("Senha",type="password",key="login_senha")
                    enviar=st.form_submit_button("Entrar no PRISMA",use_container_width=True,type="primary")
                if enviar:
                    try:
                        tokens=entrar(email,senha)
                        if iniciar_sessao(tokens):st.rerun()
                        st.warning("E-mail confirmado, mas a conta ainda não foi aprovada ou está bloqueada.")
                    except ErroPRISMA as erro:st.error(str(erro))
            elif etapa=="Criar conta":
                st.markdown("**Primeiro acesso — solicitação de cadastro**")
                with st.form("prisma_criar"):
                    nome=st.text_input("Nome completo")
                    email=st.text_input("E-mail corporativo")
                    senha=st.text_input("Criar senha (mínimo de 12 caracteres)",type="password")
                    repetida=st.text_input("Confirmar senha",type="password")
                    enviar=st.form_submit_button("Enviar código de confirmação",use_container_width=True)
                if enviar:
                    if senha!=repetida:st.error("As senhas não coincidem.")
                    else:
                        try:
                            cadastrar(email,senha,nome)
                            st.success("Se o cadastro for elegível, a confirmação será enviada ao e-mail. Use 'Confirmar e-mail'.")
                        except ErroPRISMA as erro:st.error(str(erro))
            elif etapa=="Confirmar e-mail":
                st.info("Digite o código recebido no e-mail. Depois, aguarde a aprovação administrativa.")
                with st.form("prisma_confirmar"):
                    email=st.text_input("E-mail cadastrado")
                    codigo=st.text_input("Código recebido",max_chars=12)
                    enviar=st.form_submit_button("Confirmar cadastro",use_container_width=True)
                if enviar:
                    try:
                        confirmar(email,codigo)
                        st.success("E-mail confirmado! O acesso será liberado após aprovação do administrador.")
                    except ErroPRISMA as erro:st.error(str(erro))
            else:
                st.markdown("**Recuperar senha de acesso**")
                with st.form("prisma_recuperar"):
                    email=st.text_input("E-mail cadastrado")
                    enviar=st.form_submit_button("Enviar código de recuperação",use_container_width=True)
                if enviar:
                    try:
                        pedir_recuperacao(email)
                        st.success("Se a conta existir, enviaremos as instruções ao e-mail informado.")
                    except ErroPRISMA as erro:st.error(str(erro))
                with st.form("prisma_redefinir"):
                    email_codigo=st.text_input("E-mail para confirmação da recuperação")
                    codigo=st.text_input("Código de recuperação",max_chars=12)
                    senha=st.text_input("Nova senha",type="password")
                    enviar=st.form_submit_button("Definir nova senha",use_container_width=True)
                if enviar:
                    try:
                        t=confirmar(email_codigo,codigo,tipo="recovery")
                        mudar_senha_token(t["access_token"],senha)
                        st.success("Senha alterada. Faça login com a nova senha.")
                    except (ErroPRISMA,KeyError) as erro:st.error(str(erro))
    st.stop()

# ============================================================
# HUB CENTRAL — SOMENTE APRESENTAÇÃO RENOVADA
# ============================================================
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
    '<div class="sai-section-eyebrow">CENTRAL DE FERRAMENTAS</div>'
    '<div class="sai-section-title">Explore os módulos do sistema</div>'
    '<div class="sai-section-caption">Selecione a ferramenta para continuar. '
    'Os acessos respeitam as permissões do seu perfil.</div>',
    unsafe_allow_html=True,
)

# Autorização original: todas as permissões continuam sendo lidas de auth.py.
acessos = {str(numero): tem_acesso_modulo(str(numero)) for numero in range(1, 9)}
acesso_teste = tem_acesso_modulo("teste")

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
    rotulo = "AMBIENTE DE TESTE" if ambiente else f"MÓDULO {indice.zfill(2)}"
    status = ("Acesso restrito" if autorizado else "Sem permissão") if ambiente else (
        "Disponível" if autorizado else "Acesso bloqueado"
    )
    status_classe = "" if autorizado else " is-locked"
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
            disabled=not autorizado,
        ):
            st.switch_page(modulo["pagina"])


# Três linhas / três colunas — sem deslocar módulos ausentes.
for indice_linha in range(3):
    colunas = st.columns(3, gap="large")
    for indice_coluna in range(3):
        modulo = MODULOS[indice_linha * 3 + indice_coluna]
        if modulo["visivel"]:
            with colunas[indice_coluna]:
                exibir_cartao(modulo)

if tem_acesso_modulo("gestao"):
    st.divider()
    st.subheader("🔐 Administração")
    if st.button("Gestão de Acessos — Usuários e permissões",type="primary",use_container_width=True):
        st.switch_page("pages/9_Gestao_de_Acessos.py")

st.markdown(
    '<div class="sai-footer">PRISMA — Plataforma de Recursos Integrados para Suporte, Monitoramento e Análises'
    ' &nbsp; · &nbsp; COI — Controle Operacional Integrado</div>',
    unsafe_allow_html=True,
)
if st.button("Encerrar Sessão / Sair", key="encerrar_sessao"):
    fazer_logout()
    st.success("Sessão encerrada com sucesso!")
    st.rerun()
