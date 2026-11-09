"""PRISMA — migração assistida, SOMENTE CÓPIA, do Acompanhamento Operacional.

Colocar provisoriamente em pages/99_Migracao_Acompanhamento.py, depois de
executar 01_criar_tabelas_acompanhamento.sql no SQL Editor do Supabase.

- Acesso somente para admin autenticado, usando Secrets do PRISMA no servidor.
- Nenhuma alteração é feita no Google Sheets.
- Não altera a fonte de leitura/gravação do Farol (Módulo 3).
- Prévia + backup ZIP + verificação de integridade antes de qualquer importação.
- Reexecução idempotente: mesmo ID/hash é ignorado; ID com hash diferente BLOQUEIA.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import requests
import streamlit as st
from google.oauth2.service_account import Credentials

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
from auth import verificar_autenticacao, registrar_atividade

st.set_page_config(page_title="Migração Acompanhamento | PRISMA", page_icon="🗃️", layout="wide")
if not verificar_autenticacao() or st.session_state.get("perfil") != "admin":
    st.error("Acesso exclusivo ao administrador autenticado do PRISMA.")
    st.stop()

COLUNAS_FICHAS = [
    "id", "titulo", "qtd_reclamacoes", "status", "ultima_atualizacao", "unidade",
    "regional", "municipio", "data_identificacao", "descricao", "arcgis", "sia",
    "loggers", "retorno_operacao", "finalizado", "data_finalizacao", "causa_identificada",
]
COLUNAS_VINCULOS = [
    "chave", "evento_id", "municipio", "bairros", "latitude", "longitude", "os_origem", "criado_em",
]
COLUNAS_MOV = ["id", "evento_id", "data_hora", "descricao", "status_momento"]
FONTES = {
    "farol_acomp_fichas": ("eventos", COLUNAS_FICHAS, "id"),
    "farol_acomp_vinculos": ("vinculos_farol_producao", COLUNAS_VINCULOS, "chave"),
    "farol_acomp_movimentacoes": ("movimentacoes", COLUNAS_MOV, "id"),
}
FUSO = ZoneInfo("America/Fortaleza")


def _chave_credencial():
    url = str(st.secrets.get("PRISMA_SUPABASE_URL", "")).strip().rstrip("/")
    chave = str(st.secrets.get("PRISMA_SUPABASE_SECRET_KEY", "") or
                st.secrets.get("PRISMA_SUPABASE_SERVICE_ROLE_KEY", "")).strip()
    if not url.startswith("https://") or not chave:
        raise ValueError("Supabase não configurado: URL e chave secreta de serviço do PRISMA.")
    return url, chave


def _solicitar(method, tabela, *, params=None, payload=None):
    url, chave = _chave_credencial()
    if tabela not in FONTES:
        raise ValueError("Tabela não autorizada na migração.")
    headers = {"apikey": chave, "Content-Type": "application/json"}
    # Chaves sb_secret_ não são JWT e só são enviadas como apikey.
    if not chave.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + chave
    if method == "POST":
        headers["Prefer"] = "return=minimal"
    try:
        resp = requests.request(method, f"{url}/rest/v1/{tabela}", headers=headers,
                                params=params, json=payload, timeout=40)
    except requests.RequestException as exc:
        raise RuntimeError("Não foi possível acessar o Supabase.") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase retornou HTTP {resp.status_code} para {tabela}. "
                           "Confira SQL, permissões e estrutura da tabela.")
    return resp.json() if method == "GET" else None


def _hash_linha(linha):
    serial = json.dumps(linha, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serial).hexdigest()


def _carregar_origem():
    planilha_id = str(st.secrets.get("acompanhamento_operacional_spreadsheet_id", "")).strip()
    farol_id = str(st.secrets.get("farol_producao_spreadsheet_id", "")).strip()
    if not planilha_id or planilha_id == farol_id:
        raise ValueError("Configure uma planilha exclusiva de acompanhamento diferente da do Farol.")
    configuracao = st.secrets.get("gcp_json")
    if isinstance(configuracao, str):
        configuracao = json.loads(configuracao)
    cred = Credentials.from_service_account_info(dict(configuracao), scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    planilha = gspread.authorize(cred).open_by_key(planilha_id)
    nomes = [dados[0] for dados in FONTES.values()]
    retorno = planilha.values_batch_get([f"'{nome}'!A:Z" for nome in nomes])
    faixas = retorno.get("valueRanges", [])
    if len(faixas) != len(FONTES):
        raise ValueError("O Google Sheets não retornou todas as três abas do acompanhamento.")
    tabelas = {}
    for (tabela, (_aba, colunas, pk)), faixa in zip(FONTES.items(), faixas):
        linhas = faixa.get("values", [])
        if not linhas:
            raise ValueError(f"A aba {_aba} não possui cabeçalho. Migração bloqueada.")
        header = [str(h).strip() for h in linhas[0]]
        # A ficha antiga podia ter 16 colunas, sem 'causa_identificada'.
        valido = header[:len(colunas)] == colunas
        legado = (tabela == "farol_acomp_fichas"
                  and header[:16] == colunas[:16]
                  and not any(h for h in header[16:]))
        if not (valido or legado) or any(header[len(colunas):]):
            raise ValueError(f"Cabeçalho incompatível na aba {_aba}; dados preservados sem importação.")
        itens, ids = [], set()
        for indice, celulas in enumerate(linhas[1:], start=2):
            if not any(str(v).strip() for v in celulas):
                continue
            # Não descartar colunas ou registros desconhecidos silenciosamente.
            if any(str(v).strip() for v in celulas[len(colunas):]):
                raise ValueError(f"Aba {_aba}, linha {indice}: valores além das colunas reconhecidas.")
            linha = {campo: (str(celulas[i]) if i < len(celulas) else "")
                     for i, campo in enumerate(colunas)}
            linha[pk] = linha[pk].strip()
            if not linha[pk] or linha[pk] in ids:
                raise ValueError(f"Aba {_aba}, linha {indice}: ID vazio ou duplicado.")
            ids.add(linha[pk])
            if "evento_id" in linha:
                linha["evento_id"] = linha["evento_id"].strip()
            itens.append(linha)
        tabelas[tabela] = itens
    fichas = {linha["id"] for linha in tabelas["farol_acomp_fichas"]}
    for tabela in ("farol_acomp_vinculos", "farol_acomp_movimentacoes"):
        orfaos = sorted({linha["evento_id"] for linha in tabelas[tabela]
                         if linha["evento_id"] not in fichas})
        if orfaos:
            raise ValueError(f"{tabela}: existem referências a fichas ausentes: "
                             + ", ".join(orfaos[:10]) + ". A importação foi bloqueada.")
    return tabelas


def _digest_snapshot(tabelas):
    # Ordena por chave para a verificação não depender da ordem das linhas na planilha.
    base = {tabela: sorted(registros, key=lambda r: r[FONTES[tabela][2]])
            for tabela, registros in tabelas.items()}
    return _hash_linha(base)


def _criar_backup(tabelas):
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, mode="w", compression=zipfile.ZIP_DEFLATED) as zipado:
        for tabela, registros in tabelas.items():
            buf = io.StringIO()
            escritor = csv.DictWriter(buf, fieldnames=FONTES[tabela][1], lineterminator="\n")
            escritor.writeheader()
            escritor.writerows(registros)
            zipado.writestr(tabela + ".csv", "\ufeff" + buf.getvalue())
        manifesto = {
            "origem": "Planilha exclusiva Acompanhamento Operacional (Google Sheets)",
            "gerado_em": datetime.now(FUSO).isoformat(),
            "digest": _digest_snapshot(tabelas),
            "contagens": {k: len(v) for k, v in tabelas.items()},
            "observacao": "Backup anterior à cópia. Nenhuma exclusão ou troca de fonte.",
        }
        zipado.writestr("manifesto.json", json.dumps(manifesto, ensure_ascii=False, indent=2))
    return saida.getvalue()


def _ids_destino(tabela):
    pk = FONTES[tabela][2]
    itens = {}
    limite, offset = 1000, 0
    while True:
        pagina = _solicitar("GET", tabela,
                            params={"select": f"{pk},origem_hash", "limit": limite, "offset": offset})
        if not isinstance(pagina, list):
            raise ValueError(f"Resposta inesperada para {tabela}.")
        for item in pagina:
            key = str(item[pk])
            if key in itens:
                raise ValueError(f"Supabase retornou ID duplicado em {tabela}.")
            itens[key] = str(item["origem_hash"])
        if len(pagina) < limite:
            return itens
        offset += limite


def _plano(tabelas):
    faltantes, existentes = {}, {}
    for tabela, registros in tabelas.items():
        banco = _ids_destino(tabela)
        novos = []
        for linha in registros:
            pk = FONTES[tabela][2]
            identificador = linha[pk]
            digest = _hash_linha(linha)
            anterior = banco.get(identificador)
            if anterior is not None and anterior != digest:
                raise ValueError(f"Conflito em {tabela}, ID={identificador}: o destino "
                                 "já possui conteúdo diferente. Nenhum dado foi sobrescrito.")
            if anterior is None:
                novos.append({**linha, "origem_hash": digest})
        faltantes[tabela] = novos
        existentes[tabela] = len(registros) - len(novos)
    return faltantes, existentes


def _importar(snapshot):
    # Confere que a planilha não mudou desde o backup aprovado na sessão.
    atual = _carregar_origem()
    if _digest_snapshot(atual) != snapshot["digest"]:
        raise ValueError("Os dados do Google Sheets mudaram desde a prévia. "
                         "Refaça a prévia e baixe um novo backup antes de importar.")
    faltantes, repetidos = _plano(snapshot["tabelas"])
    inseridos = {tabela: 0 for tabela in FONTES}
    # Cada POST é atômico; em falha entre lotes, repetir importa só os restantes.
    # A ordem respeita as chaves estrangeiras do banco.
    for tabela, registros in faltantes.items():
        for pos in range(0, len(registros), 200):
            lote = registros[pos:pos + 200]
            _solicitar("POST", tabela, payload=lote)
            inseridos[tabela] += len(lote)
    # Conferência por ID + hash de TODOS os registros, não só por quantidade.
    restantes, _ = _plano(snapshot["tabelas"])
    if any(restantes.values()):
        raise RuntimeError("A conferência final encontrou registros ainda não importados.")
    # Uma gravação concorrente no Sheets durante a cópia exige nova conferência.
    if _digest_snapshot(_carregar_origem()) != snapshot["digest"]:
        raise RuntimeError("A origem mudou durante a cópia. Mantenha o Google Sheets em produção, "
                           "refaça a prévia e reconcilie os registros antes da troca de fonte.")
    return inseridos, repetidos



def _conferir_paridade_ao_vivo():
    """Compara fontes atuais por PK/hash sem alterar Google Sheets nem Supabase."""
    origem = _carregar_origem()
    resumo = []
    detalhes = {}
    for tabela, registros in origem.items():
        pk = FONTES[tabela][2]
        hashes_origem = {str(linha[pk]): _hash_linha(linha) for linha in registros}
        hashes_destino = _ids_destino(tabela)
        faltantes = sorted(set(hashes_origem) - set(hashes_destino))
        excedentes = sorted(set(hashes_destino) - set(hashes_origem))
        divergentes = sorted(
            identificador for identificador in set(hashes_origem) & set(hashes_destino)
            if hashes_origem[identificador] != hashes_destino[identificador]
        )
        detalhes[tabela] = {
            "Sem cópia no Supabase": faltantes,
            "Alterados após a cópia": divergentes,
            "Somente no Supabase": excedentes,
        }
        resumo.append({
            "Tabela": tabela,
            "Google Sheets (atual)": len(hashes_origem),
            "Supabase": len(hashes_destino),
            "Sem cópia": len(faltantes),
            "Divergentes": len(divergentes),
            "Somente no Supabase": len(excedentes),
        })
    return resumo, detalhes


st.title("🗃️ Migração do Acompanhamento Operacional")
st.caption("Etapa 1 — cópia controlada Google Sheets → Supabase · sem mudar o Módulo 3")
st.warning("Execute primeiro o SQL de criação das três tabelas no projeto Supabase do PRISMA. "
           "Esta página não altera a fonte de produção nem apaga registros do Google Sheets.")

if st.button("1. Preparar prévia e backup", type="primary"):
    try:
        with st.spinner("Lendo e validando as três abas do Google Sheets..."):
            tabelas = _carregar_origem()
            snapshot = {"tabelas": tabelas, "digest": _digest_snapshot(tabelas)}
            snapshot["backup_zip"] = _criar_backup(tabelas)
            st.session_state["farol_acomp_snapshot"] = snapshot
        st.success("Fonte validada. Confira as quantidades e salve o backup antes da cópia.")
    except Exception as erro:
        st.session_state.pop("farol_acomp_snapshot", None)
        st.error(f"Prévia bloqueada: {erro}")

snapshot = st.session_state.get("farol_acomp_snapshot")
if snapshot:
    st.subheader("Resumo da fonte")
    st.dataframe([{"Conjunto": k, "Registros": len(v)} for k, v in snapshot["tabelas"].items()],
                 hide_index=True, use_container_width=True)
    st.code(snapshot["digest"], language="text")
    st.download_button("⬇️ Baixar backup completo (ZIP)", data=snapshot["backup_zip"],
                       file_name="backup_farol_acompanhamento.zip", mime="application/zip")
    st.divider()
    st.subheader("Conferência do destino")
    if st.button("2. Conferir estrutura e registros no Supabase"):
        try:
            faltantes, iguais = _plano(snapshot["tabelas"])
            st.dataframe([{"Tabela": k, "Novos": len(v), "Já idênticos": iguais[k]}
                          for k, v in faltantes.items()], hide_index=True, use_container_width=True)
            st.success("Consulta concluída. Registros divergentes são bloqueados automaticamente.")
        except Exception as erro:
            st.error(f"Não foi possível conferir o destino: {erro}")
    confirmado = st.checkbox("Baixei o backup e autorizo somente a CÓPIA dos registros "
                            "para as novas tabelas do Supabase.", key="farol_acomp_confirmar")
    if st.button("3. Importar e verificar cópia", disabled=not confirmado):
        try:
            with st.spinner("Copiando dados e verificando integridade dos registros..."):
                inseridos, repetidos = _importar(snapshot)
            st.success("Cópia concluída e conferida por ID e hash. "
                       "O Google Sheets continua como fonte de produção.")
            st.dataframe([{"Tabela": k, "Inseridos": inseridos[k], "Já existentes": repetidos[k]}
                          for k in FONTES], hide_index=True, use_container_width=True)
            try:
                if sum(inseridos.values()) > 0:
                    resumo = ", ".join(f"{k}: {v}" for k, v in inseridos.items())
                    registrar_atividade("3", "importar", detalhes="Migração inicial Supabase: " + resumo)
            except Exception:
                st.warning("A cópia foi verificada, mas a auditoria do PRISMA falhou. "
                           "Registre a ocorrência antes de liberar qualquer mudança de fonte.")
        except Exception as erro:
            st.error("Cópia não concluída: " + str(erro))
            st.info("Não repita manualmente os registros. Após resolver o problema, "
                    "execute novamente esta ferramenta; ela ignora registros idênticos.")


st.divider()
st.subheader("Etapa 2 — Conferência de paridade atual")
st.caption(
    "Compara os registros atuais do Google Sheets com os já copiados para o Supabase "
    "usando IDs e hashes. Somente leitura: nenhuma ficha será modificada."
)
if st.button("4. Verificar se a cópia continua atualizada", key="farol_acomp_paridade_ao_vivo"):
    try:
        with st.spinner("Comparando as três fontes de dados em tempo real..."):
            resumo, detalhes = _conferir_paridade_ao_vivo()
        st.dataframe(resumo, hide_index=True, use_container_width=True)
        if any(any(ids for ids in ocorrencias.values()) for ocorrencias in detalhes.values()):
            st.warning(
                "As fontes não estão mais idênticas. Não altere o Farol para Supabase "
                "antes de reconciliar os registros. A importação inicial não sobrescreve IDs divergentes."
            )
            for tabela, grupos in detalhes.items():
                if any(grupos.values()):
                    with st.expander(f"Divergências — {tabela}"):
                        for nome, ids in grupos.items():
                            if ids:
                                st.write(f"{nome} ({len(ids)}): " + ", ".join(ids[:20])
                                         + (" ..." if len(ids) > 20 else ""))
        else:
            st.success(
                "As três tabelas estão idênticas ao Google Sheets neste instante. "
                "A fonte de produção continua sendo o Google Sheets."
            )
    except Exception as erro:
        st.error(f"Não foi possível comparar as fontes: {erro}")


st.divider()
st.subheader("Etapa 3 — Validação da leitura individual pelo Supabase")
st.caption(
    "Teste somente leitura. Permite conferir uma ficha e seus registros vinculados "
    "sem modificar o Farol, suas tabelas ou a fonte de produção."
)
evento_teste = st.text_input(
    "ID do acompanhamento para conferir", placeholder="Ex.: 124",
    key="farol_supabase_id_teste",
)
if st.button("5. Testar leitura individual no Supabase", key="farol_teste_leitura_supabase"):
    try:
        if not evento_teste.strip():
            raise ValueError("Informe o ID de uma ficha existente no acompanhamento.")
        from farol_acomp_supabase_leitura import (
            ler_ficha, ler_movimentacoes, ler_vinculos
        )
        # A origem é relida diretamente do Google Sheets, não do backup em sessão.
        atual = _carregar_origem()
        ficha_origem = next(
            (r for r in atual["farol_acomp_fichas"] if r["id"] == evento_teste.strip()), None
        )
        if ficha_origem is None:
            raise ValueError("A ficha não foi encontrada no Google Sheets atual.")
        ficha_destino = ler_ficha(evento_teste)
        vinculos_destino = ler_vinculos(evento_teste)
        movimentos_destino = ler_movimentacoes(evento_teste)
        conjuntos = [
            ("Ficha", "farol_acomp_fichas", [ficha_destino] if ficha_destino else []),
            ("Vínculos", "farol_acomp_vinculos", vinculos_destino),
            ("Movimentações", "farol_acomp_movimentacoes", movimentos_destino),
        ]
        resumo_ficha = []
        for nome, tabela, encontrados in conjuntos:
            pk = FONTES[tabela][2]
            colunas = FONTES[tabela][1]
            origem_id = {
                str(r[pk]): r for r in atual[tabela]
                if tabela == "farol_acomp_fichas" and str(r["id"]) == evento_teste.strip()
                or tabela != "farol_acomp_fichas" and str(r.get("evento_id")) == evento_teste.strip()
            }
            destino_id = {str(r[pk]): r for r in encontrados}
            # Confere o conteúdo, não apenas o hash da importação.
            iguais = (
                origem_id.keys() == destino_id.keys()
                and all(
                    _hash_linha(origem_id[k]) ==
                    _hash_linha({col: str(destino_id[k].get(col, "")) for col in colunas})
                    for k in origem_id
                )
            )
            resumo_ficha.append({
                "Conjunto": nome,
                "Google Sheets": len(origem_id),
                "Supabase": len(destino_id),
                "Conteúdo equivalente": "Sim" if iguais else "Não",
            })
        st.dataframe(resumo_ficha, hide_index=True, use_container_width=True)
        if all(linha["Conteúdo equivalente"] == "Sim" for linha in resumo_ficha):
            st.success(
                "Leitura individual aprovada. Ficha, vínculos e movimentações "
                "correspondem aos dados atuais do Google Sheets."
            )
        else:
            st.warning(
                "Foi encontrada uma diferença no acompanhamento. "
                "Não altere a fonte de produção até reconciliar os registros."
            )
    except Exception as erro:
        st.error(f"Teste de leitura não concluído: {erro}")
