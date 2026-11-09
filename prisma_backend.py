"""PRISMA — autenticação por e-mail + base de perfis via Supabase REST.

Chaves de serviço utilizadas somente no servidor Streamlit; nunca no navegador.
O campo password e os tokens jamais são armazenados em tabelas operacionais.
"""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import quote
import re
import requests
import streamlit as st

MODULOS = {"1":"Baixa Pressão", "2":"Mapeamento de Pressão", "3":"Farol Operacional",
           "4":"Ferramentas Operacionais", "5":"Cadastros e Consultas",
           "6":"Ferramentas Adicionais", "7":"Relatórios Integrados",
           "8":"Consulta Operacional", "9":"Gestão de Ativos", "teste":"Ambiente de Teste",
           "gestao":"Gestão de Acessos"}
ACOES = ("visualizar", "criar", "importar", "editar", "excluir", "exportar", "relatorios")

class ErroPRISMA(RuntimeError): pass

def configuracao():
    """Aceita chaves modernas do Supabase e, transitoriamente, as legadas.

    Ambas são enviadas no cabeçalho apikey. Apenas JWTs legados são usados
    como Bearer; chaves sb_publishable_ e sb_secret_ não são JWTs.
    """
    def obter(*nomes):
        for nome in nomes:
            valor = str(st.secrets.get(nome, "")).strip()
            if valor:
                return valor
        return ""

    cfg = {
        "PRISMA_SUPABASE_URL": obter("PRISMA_SUPABASE_URL"),
        "PRISMA_SUPABASE_ANON_KEY": obter(
            "PRISMA_SUPABASE_PUBLISHABLE_KEY", "PRISMA_SUPABASE_ANON_KEY"),
        "PRISMA_SUPABASE_SERVICE_ROLE_KEY": obter(
            "PRISMA_SUPABASE_SECRET_KEY", "PRISMA_SUPABASE_SERVICE_ROLE_KEY"),
        "PRISMA_ADMIN_EMAIL": obter("PRISMA_ADMIN_EMAIL"),
        "PRISMA_ALLOWED_EMAIL_DOMAINS": obter("PRISMA_ALLOWED_EMAIL_DOMAINS"),
    }
    nomes_visiveis = {
        "PRISMA_SUPABASE_ANON_KEY": "PRISMA_SUPABASE_PUBLISHABLE_KEY",
        "PRISMA_SUPABASE_SERVICE_ROLE_KEY": "PRISMA_SUPABASE_SECRET_KEY",
    }
    faltam = [nomes_visiveis.get(campo, campo) for campo, valor in cfg.items() if not valor]
    if faltam:
        raise ErroPRISMA("Configuração do PRISMA incompleta: " + ", ".join(faltam))
    if not cfg["PRISMA_SUPABASE_URL"].startswith("https://"):
        raise ErroPRISMA("PRISMA_SUPABASE_URL deve utilizar HTTPS.")
    return cfg

def dominio_permitido(email):
    cfg=configuracao()
    email=str(email).strip().lower()
    permitidos={p.strip().lower().lstrip('@') for p in cfg["PRISMA_ALLOWED_EMAIL_DOMAINS"].split(",") if p.strip()}
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email)) and email.rsplit("@",1)[1] in permitidos

def _request(method,path,*, admin=False,bearer=None,payload=None,params=None):
    cfg=configuracao()
    key=cfg["PRISMA_SUPABASE_SERVICE_ROLE_KEY"] if admin else cfg["PRISMA_SUPABASE_ANON_KEY"]
    cab={"apikey": key, "Content-Type": "application/json"}
    if admin and bearer:
        raise ErroPRISMA("Chamada administrativa não aceita token do usuário")
    if bearer:
        cab["Authorization"] = "Bearer " + bearer
    elif not key.startswith(("sb_publishable_", "sb_secret_")):
        # Compatibilidade temporária: as chaves legadas são JWTs.
        cab["Authorization"] = "Bearer " + key
    try:
        response=requests.request(method,cfg["PRISMA_SUPABASE_URL"].rstrip("/")+path,
                                  headers=cab,json=payload,params=params,timeout=16)
    except requests.RequestException as exc:
        raise ErroPRISMA("Serviço de autenticação indisponível. Tente novamente.") from exc
    if response.status_code >= 400:
        # Não divulgar URLs, chaves ou detalhes de bancos. Erros de API de autenticação são genéricos.
        if response.status_code==429: msg="Muitas solicitações. Tente novamente mais tarde."
        elif response.status_code in (401,403): msg="Credenciais inválidas ou operação não autorizada."
        elif response.status_code==404: msg="Serviço ou tabelas do PRISMA não configurados."
        else:
            corpo=response.json() if "json" in response.headers.get("content-type","") else {}
            detalhe=str(corpo.get("msg") or corpo.get("message") or corpo.get("error_description") or "")
            msg=detalhe[:180] if (not admin and path.startswith('/auth/v1/')) else "Erro no serviço de usuários (código HTTP %s)."%response.status_code
        raise ErroPRISMA(msg)
    if not response.content:return None
    return response.json() if "json" in response.headers.get("content-type","") else None

def cadastrar(email,senha,nome):
    if not dominio_permitido(email): raise ErroPRISMA("Utilize um e-mail de domínio corporativo autorizado.")
    if len(senha)<12: raise ErroPRISMA("Use uma senha com no mínimo 12 caracteres.")
    if len(str(nome).strip())<3: raise ErroPRISMA("Informe seu nome completo.")
    return _request("POST","/auth/v1/signup",payload={"email":email.strip().lower(),"password":senha,"data":{"nome":nome.strip()}})

def confirmar(email,codigo,tipo="signup"):
    if not dominio_permitido(email): raise ErroPRISMA("E-mail corporativo inválido.")
    if tipo not in ("signup","recovery"):raise ErroPRISMA("Tipo de verificação inválido")
    return _request("POST","/auth/v1/verify",payload={"email":email.strip().lower(),"token":codigo.strip(),"type":tipo})

def entrar(email,senha):
    return _request("POST","/auth/v1/token",params={"grant_type":"password"},payload={"email":email.strip().lower(),"password":senha})

def renovar(refresh_token):
    return _request("POST","/auth/v1/token",params={"grant_type":"refresh_token"},payload={"refresh_token":refresh_token})

def usuario_autenticado(access_token):
    return _request("GET","/auth/v1/user",bearer=access_token)

def pedir_recuperacao(email):
    if not dominio_permitido(email):raise ErroPRISMA("Informe o e-mail corporativo cadastrado.")
    return _request("POST","/auth/v1/recover",payload={"email":email.strip().lower()})

def mudar_senha_token(access_token,nova_senha):
    if len(nova_senha)<12:raise ErroPRISMA("Use uma senha com no mínimo 12 caracteres.")
    return _request("PUT","/auth/v1/user",bearer=access_token,payload={"password":nova_senha})

def _tabela(tabela,*,method="GET",params=None,payload=None,prefer=None):
    cfg=configuracao()
    key=cfg["PRISMA_SUPABASE_SERVICE_ROLE_KEY"]
    headers={"apikey": key, "Content-Type": "application/json"}
    if not key.startswith(("sb_publishable_", "sb_secret_")):
        headers["Authorization"] = "Bearer " + key
    if prefer:headers["Prefer"]=prefer
    try:
        r=requests.request(method,cfg["PRISMA_SUPABASE_URL"].rstrip("/")+"/rest/v1/"+tabela,
                           headers=headers,params=params,json=payload,timeout=16)
    except requests.RequestException as exc:raise ErroPRISMA("Banco de permissões indisponível.") from exc
    if r.status_code>=400:raise ErroPRISMA("Falha na consulta de permissões (HTTP %s). Verifique a instalação SQL."%r.status_code)
    return r.json() if r.content and "json" in r.headers.get("content-type","") else []

def perfil_de_usuario(user_id):
    rows=_tabela("prisma_perfis",params={"id":"eq."+str(user_id),"select":"id,email,nome,perfil,status"})
    return rows[0] if rows else None

def bootstrap_admin(user):
    cfg=configuracao()
    email=str(user.get("email","")).lower()
    if not user.get("email_confirmed_at") or email != cfg["PRISMA_ADMIN_EMAIL"].lower():return
    p=perfil_de_usuario(user["id"])
    if not p or p.get("status")!="pendente":return
    # A promoção inicial só é possível se não existir administrador ativo.
    admin_atual=_tabela("prisma_perfis",params={"perfil":"eq.admin","status":"eq.ativo","select":"id","limit":"1"})
    if admin_atual:return
    _tabela("prisma_perfis",method="PATCH",params={"id":"eq."+user["id"]},
            payload={"perfil":"admin","status":"ativo"},prefer="return=minimal")
    auditar(user["id"], "bootstrap_admin", user["id"], "Primeira conta administrativa",
            modulo="gestao", tipo="ALTERACAO")

def permissoes_usuario(user_id):
    return _tabela("prisma_permissoes",params={"usuario_id":"eq."+str(user_id),"select":"modulo,acao,permitido"})

def permissoes_padrao(perfil):
    if perfil=="admin":return {(m,a):True for m in MODULOS for a in ACOES}
    permitidos={str(i) for i in range(1,9)} if perfil=="usuario" else {"1","3","8"}
    return {(m,a): (m in permitidos and (perfil!="operador" or a in ("visualizar","exportar","relatorios")))
            for m in MODULOS for a in ACOES}

def permissoes_efetivas(perfil,linhas):
    base=permissoes_padrao(perfil)
    for r in linhas:
        m,a=r.get("modulo"),r.get("acao")
        if (m,a) in base and m!="gestao":base[(m,a)]=bool(r.get("permitido"))
    # Gestão de Ativos: visualização universal para contas ativas.
    # A autenticação já bloqueia cadastros pendentes ou bloqueados.
    # Preserva todas as demais permissões individuais de criação/edição.
    base[("9","visualizar")]=True
    # Somente perfil admin pode acessar o gerenciamento de permissões.
    for a in ACOES:base[("gestao",a)]=(perfil=="admin")
    return base

def listar_perfis():
    return _tabela("prisma_perfis",params={"select":"id,email,nome,perfil,status,criado_em","order":"criado_em.desc","limit":"500"})

def auditar(ator, acao, alvo=None, detalhes="", *, modulo=None, tipo=None,
            resultado="SUCESSO", registro_id=None):
    """Grava um evento persistente de auditoria no Supabase (somente servidor).

    Preserva os quatro argumentos posicionais utilizados pela Gestão de Acessos.
    Não registrar senhas, tokens, conteúdo de planilhas ou dados sensíveis em detalhes.
    """
    if not ator or not str(acao).strip():
        raise ErroPRISMA("Evento de auditoria sem autor ou ação.")
    acao = str(acao).strip().lower()
    tipos_legados = {
        "bootstrap_admin": "ALTERACAO", "alterar_conta": "ALTERACAO",
        "alterar_permissoes": "ALTERACAO", "login": "LOGIN",
    }
    if tipo is None:
        tipo = tipos_legados.get(acao)
    if modulo is None and acao in ("bootstrap_admin", "alterar_conta", "alterar_permissoes"):
        modulo = "gestao"
    resultado = str(resultado).strip().upper()
    if resultado not in ("SUCESSO", "FALHA"):
        raise ErroPRISMA("Resultado de auditoria inválido.")
    if tipo is not None:
        tipo = str(tipo).strip().upper()
        if tipo not in ("LOGIN", "CRIACAO", "GRAVACAO", "ALTERACAO", "EXCLUSAO"):
            raise ErroPRISMA("Tipo de auditoria inválido.")
    payload = {
        "autor_id": str(ator), "acao": acao,
        "alvo_id": str(alvo) if alvo else None,
        "detalhes": str(detalhes or "")[:500],
        "modulo": str(modulo).strip() if modulo else None,
        "tipo": tipo, "resultado": resultado,
        "registro_id": str(registro_id)[:160] if registro_id is not None else (
            str(alvo) if alvo else None),
    }
    # A identificação nominal do autor é preenchida pelo banco via trigger,
    # para manter um retrato histórico mesmo se a conta for posteriormente excluída.
    _tabela("prisma_auditoria", method="POST", payload=payload,
            prefer="return=minimal")

def atualizar_usuario(ator,alvo,perfil,status):
    if perfil not in {"admin","usuario","operador"} or status not in {"pendente","ativo","bloqueado"}:
        raise ErroPRISMA("Perfil ou status inválido")
    atual=perfil_de_usuario(alvo)
    if not atual:raise ErroPRISMA("Usuário não encontrado")
    if str(ator)==str(alvo) and (perfil!="admin" or status!="ativo"):
        raise ErroPRISMA("Não é possível remover sua própria administração.")
    if atual["perfil"]=="admin" and atual["status"]=="ativo" and (perfil!="admin" or status!="ativo"):
        outros=_tabela("prisma_perfis",params={"perfil":"eq.admin","status":"eq.ativo","select":"id"})
        if len(outros)<2:raise ErroPRISMA("Não é possível bloquear o último administrador.")
    _tabela("prisma_perfis",method="PATCH",params={"id":"eq."+str(alvo)},
            payload={"perfil":perfil,"status":status},prefer="return=minimal")
    auditar(ator, "alterar_conta", alvo, f"Perfil={perfil}; Status={status}",
            modulo="gestao", tipo="ALTERACAO")

def salvar_permissoes(ator,alvo,matriz,*,origem_id=None):
    if str(ator)==str(alvo):raise ErroPRISMA("Permissões administrativas próprias não podem ser alteradas aqui.")
    perfil=perfil_de_usuario(alvo)
    if not perfil:raise ErroPRISMA("Usuário não encontrado")
    if perfil["perfil"]=="admin":raise ErroPRISMA("As permissões de administradores são gerenciadas pelo perfil.")
    itens=[{"usuario_id":str(alvo),"modulo":m,"acao":a,"permitido":bool(v)} for (m,a),v in matriz.items()
            if m in MODULOS and m!="gestao" and a in ACOES
            and (m,a)!=("9","visualizar")]  # Leitura do módulo 9 é universal.
    _tabela("prisma_permissoes",method="POST",params={"on_conflict":"usuario_id,modulo,acao"},
            payload=itens,prefer="resolution=merge-duplicates,return=minimal")
    detalhes=(f"Espelhamento das permissões de {origem_id}; {len(itens)} permissões atualizadas"
              if origem_id else f"{len(itens)} permissões atualizadas")
    auditar(ator, "alterar_permissoes", alvo, detalhes,
            modulo="gestao", tipo="ALTERACAO")


def espelhar_permissoes(ator,origem_id,destino_id):
    """Replica TODAS as permissões efetivas, não apenas as personalizações.

    Mantém perfil, situação e a exclusividade da Gestão de Acessos.
    Pode configurar uma conta pendente, mas nunca usa uma conta inativa como modelo.
    """
    administrador=perfil_de_usuario(ator)
    if not administrador or administrador.get("perfil")!="admin" or administrador.get("status")!="ativo":
        raise ErroPRISMA("Somente um administrador ativo pode espelhar permissões.")
    if str(origem_id)==str(destino_id):
        raise ErroPRISMA("Selecione contas diferentes para origem e destino.")
    origem=perfil_de_usuario(origem_id)
    destino=perfil_de_usuario(destino_id)
    if not origem or not destino:
        raise ErroPRISMA("Usuário de origem ou destino não encontrado.")
    if origem.get("status")!="ativo":
        raise ErroPRISMA("O usuário modelo precisa estar ativo.")
    if destino.get("perfil")=="admin":
        raise ErroPRISMA("Permissões de administradores são determinadas pelo perfil.")
    matriz=permissoes_efetivas(origem["perfil"],permissoes_usuario(origem_id))
    salvar_permissoes(ator,destino_id,matriz,origem_id=origem_id)

