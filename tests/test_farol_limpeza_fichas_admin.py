"""Segurança da limpeza opcional de fichas de acompanhamento do Módulo 3.

Testes em memória: não consultam nem alteram bases Google/Supabase.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import sys

RAIZ = Path(__file__).resolve().parents[1]
M3 = RAIZ / "pages" / "3_Farol_Operacional.py"
BACKEND = RAIZ / "farol_limpeza_acompanhamentos.py"
MIGRACAO = RAIZ / "sql" / "farol_acomp_limpeza_admin_tx.sql"


def obter_funcoes(nomes, ambiente):
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    selecionadas = [
        no for no in arvore.body
        if isinstance(no, ast.FunctionDef) and no.name in nomes
    ]
    assert {no.name for no in selecionadas} == set(nomes)
    exec(compile(ast.Module(body=selecionadas, type_ignores=[]),
                 str(BACKEND), "exec"), ambiente)
    return ambiente


class FalhaAcompanhamentoSupabase(RuntimeError):
    pass


class TesteLimpezaAcompanhamentos(unittest.TestCase):
    def ambiente(self, *, perfil="admin", autenticado=True, permitido=True, autor="user-123"):
        chamadas = []
        caches = []
        st = SimpleNamespace(session_state={"perfil": perfil,
                                               "prisma_usuario_id": autor})
        ambiente = {
            "st": st,
            "verificar_autenticacao": lambda: autenticado,
            "pode_executar_acao": lambda modulo, acao:
                chamadas.append(("permissao", modulo, acao)) or permitido,
            "fonte_supabase": lambda: True,
            "_request": lambda *a, **kw:
                chamadas.append(("rpc", a, kw)) or {
                    "concluido": True, "fichas": 7,
                    "movimentacoes": 11, "vinculos": 6,
                },
            "limpar_cache_supabase": lambda: caches.append("supabase"),
            "FalhaAcompanhamentoSupabase": FalhaAcompanhamentoSupabase,
        }
        ambiente = obter_funcoes(["_exigir_administrador",
                                  "excluir_fichas_acompanhamento_admin"], ambiente)
        return ambiente, chamadas, caches

    def test_admin_ativo_e_permitido_exclui_por_rpc_e_limpa_cache(self):
        ambiente, chamadas, caches = self.ambiente()
        leitor = SimpleNamespace(clear=lambda: caches.append("leitor"))
        integracao = SimpleNamespace(
            invalidar_cache_acompanhamento=lambda: caches.append("integracao"))
        with patch.dict(sys.modules, {
            "acompanhamento_operacional_farol": SimpleNamespace(ler_registros=leitor),
            "integracao_acompanhamento_producao": integracao,
        }):
            resultado = ambiente["excluir_fichas_acompanhamento_admin"]()
        self.assertEqual(resultado["fonte"], "Supabase")
        self.assertEqual(resultado["fichas"], 7)
        rpc = next(x for x in chamadas if x[0] == "rpc")
        self.assertEqual(rpc[1], (
            "POST", "/rest/v1/rpc/farol_acomp_limpar_tudo_admin_tx"))
        self.assertEqual(rpc[2]["dados"], {"p_autor": "user-123"})
        self.assertEqual(caches, ["supabase", "leitor", "integracao"])

    def test_nega_perfis_comuns_ou_sem_acesso_de_excluir(self):
        for perfil, autenticado, permitido, autor in (
            ("usuario", True, True, "id"),
            ("operador", True, True, "id"),
            ("admin", False, True, "id"),
            ("admin", True, False, "id"),
            ("admin", True, True, ""),
        ):
            with self.subTest(perfil=perfil, autenticado=autenticado,
                              permitido=permitido, autor=autor):
                ambiente, chamadas, _ = self.ambiente(
                    perfil=perfil, autenticado=autenticado,
                    permitido=permitido, autor=autor)
                with self.assertRaises(PermissionError):
                    ambiente["excluir_fichas_acompanhamento_admin"]()
                self.assertFalse(any(x[0] == "rpc" for x in chamadas))

    def test_erro_do_supabase_nao_faz_fallback_para_google(self):
        ambiente, _, caches = self.ambiente()
        def falhar(*a, **kw):
            raise FalhaAcompanhamentoSupabase("temporariamente indisponível")
        ambiente["_request"] = falhar
        ambiente["_limpar_fichas_google"] = lambda: self.fail(
            "Fallback para Google nunca deve ocorrer após falha Supabase")
        with self.assertRaises(FalhaAcompanhamentoSupabase):
            ambiente["excluir_fichas_acompanhamento_admin"]()
        self.assertEqual(caches, [])

    def test_resposta_supabase_sem_confirmacao_bloqueia_sucesso(self):
        ambiente, _, caches = self.ambiente()
        ambiente["_request"] = lambda *a, **kw: {"concluido": False}
        with self.assertRaises(FalhaAcompanhamentoSupabase):
            ambiente["excluir_fichas_acompanhamento_admin"]()
        self.assertEqual(caches, [])

    def test_google_valida_todos_cabecalhos_antes_de_apagar(self):
        class PlanilhaFake:
            def __init__(self, titulo, cabecalho):
                self.titulo, self.cabecalho, self.exclusoes = titulo, cabecalho, 0
            def get_all_values(self):
                return [self.cabecalho, ["registro"]]
            def clear(self):
                self.exclusoes += 1
            def update(self, **kwargs):
                pass

        sheets = {
            "eventos": PlanilhaFake("eventos", ["cabecalho-incorreto"]),
            "movimentacoes": PlanilhaFake("movimentacoes",
                                          ["id","evento_id","data_hora",
                                           "descricao","status_momento"]),
        }
        class WorksheetNotFound(Exception):
            pass
        def worksheet(nome):
            if nome not in sheets:
                raise WorksheetNotFound()
            return sheets[nome]
        ambiente = {
            "gspread": SimpleNamespace(WorksheetNotFound=WorksheetNotFound),
        }
        obter_funcoes(["_limpar_fichas_google"], ambiente)
        acomp = SimpleNamespace(
            CAMPOS_EVENTOS=["id","titulo","qtd_reclamacoes"],
            CAMPOS_MOVIMENTACOES=["id","evento_id","data_hora",
                                  "descricao","status_momento"],
            ler_registros=SimpleNamespace(clear=lambda: None),
        )
        integracao = SimpleNamespace(
            CAMPOS_VINCULO=["chave","evento_id"],
            _planilha=lambda ident: SimpleNamespace(worksheet=worksheet),
            planilha_id=lambda: "fake-id",
            invalidar_cache_acompanhamento=lambda: None,
        )
        with patch.dict(sys.modules, {
            "acompanhamento_operacional_farol": acomp,
            "integracao_acompanhamento_producao": integracao,
        }):
            with self.assertRaisesRegex(ValueError, "Cabeçalho inesperado"):
                ambiente["_limpar_fichas_google"]()
        self.assertEqual(sheets["movimentacoes"].exclusoes, 0)
        self.assertEqual(sheets["eventos"].exclusoes, 0)

    def test_google_preserva_cabecalhos_e_nao_cria_abas_ausentes(self):
        class WorksheetNotFound(Exception):
            pass
        class Sheet:
            def __init__(self, valores):
                self.valores = valores
                self.apagamentos = 0
            def get_all_values(self):
                return self.valores
            def clear(self):
                self.apagamentos += 1
                self.valores = []
            def update(self, **kw):
                self.valores = kw["values"]
        aba = Sheet([["id","titulo"], ["1","anterior"], ["2","anterior"]])
        def worksheet(nome):
            if nome != "eventos":
                raise WorksheetNotFound()
            return aba
        ambiente = {"gspread": SimpleNamespace(WorksheetNotFound=WorksheetNotFound)}
        obter_funcoes(["_limpar_fichas_google"], ambiente)
        caches = []
        with patch.dict(sys.modules, {
            "acompanhamento_operacional_farol": SimpleNamespace(
                CAMPOS_EVENTOS=["id","titulo"],
                CAMPOS_MOVIMENTACOES=["id","evento_id"],
                ler_registros=SimpleNamespace(clear=lambda: caches.append("leitor")),
            ),
            "integracao_acompanhamento_producao": SimpleNamespace(
                CAMPOS_VINCULO=["chave","evento_id"],
                _planilha=lambda i: SimpleNamespace(worksheet=worksheet),
                planilha_id=lambda: "id",
                invalidar_cache_acompanhamento=lambda: caches.append("integracao"),
            ),
        }):
            res = ambiente["_limpar_fichas_google"]()
        self.assertEqual(res["fichas"], 2)
        self.assertEqual(aba.apagamentos, 1)
        self.assertEqual(aba.valores, [["id","titulo"]])
        self.assertEqual(caches, ["leitor","integracao"])

    def test_sql_usa_transacao_invoker_fk_auditoria_e_nao_executa_agora(self):
        sql = MIGRACAO.read_text(encoding="utf-8")
        self.assertIn("SECURITY INVOKER", sql)
        self.assertIn("perfil = 'admin' AND status = 'ativo'", sql)
        self.assertIn("FROM PUBLIC, anon, authenticated", sql)
        self.assertIn("TO service_role", sql)
        self.assertIn("INSERT INTO public.prisma_auditoria", sql)
        tabelas = (
            ("farol_acomp_eventos_persistentes", "evento_chave"),
            ("farol_acomp_incorporacoes", "ficha_incorporada"),
            ("farol_acomp_vinculos", "chave"),
            ("farol_acomp_movimentacoes", "id"),
            ("farol_acomp_fichas", "id"),
        )
        indices = []
        for nome, chave in tabelas:
            with self.subTest(tabela=nome):
                esperado = "DELETE FROM public." + nome + "\\n  WHERE " + chave + " IS NOT NULL;"
                self.assertIn(esperado.replace("\\n", "\n"), sql)
                self.assertNotIn("DELETE FROM public." + nome + ";", sql)
                indices.append(sql.index(esperado.replace("\\n", "\n")))
        self.assertEqual(indices, sorted(indices))
        self.assertIn("WHERE processado_em IS NULL", sql)
        self.assertNotIn("TRUNCATE TABLE", sql)
        self.assertNotIn("DROP TABLE", sql)

    def test_correcao_where_mantem_funcao_sem_executar_limpeza(self):
        original = MIGRACAO.read_text(encoding="utf-8")
        migracao_nova = (
            RAIZ / "sql" / "farol_acomp_limpeza_admin_where_20261123.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(original, migracao_nova)
        self.assertIn("CREATE OR REPLACE FUNCTION", migracao_nova)
        self.assertIn("SECURITY INVOKER", migracao_nova)
        self.assertIn("REVOKE ALL", migracao_nova)
        self.assertIn("GRANT EXECUTE", migracao_nova)
        self.assertIn("O projeto aplica a proteção SQL", migracao_nova)
        self.assertNotIn("SELECT public.farol_acomp_limpar_tudo_admin_tx(", migracao_nova)

    def test_interface_padrao_nao_exclui_fichas_e_exige_confirmacoes(self):
        fonte = M3.read_text(encoding="utf-8")
        self.assertIn("from farol_limpeza_acompanhamentos import", fonte)
        self.assertIn("Excluir também todas as fichas de acompanhamento", fonte)
        self.assertIn('value=False,\n        key="farol_limpeza_incluir_fichas"', fonte)
        self.assertIn('frase_fichas.strip() == "EXCLUIR FICHAS"', fonte)
        self.assertIn('confirmacao_fichas and frase_fichas.strip()', fonte)
        self.assertIn("PERFIL_FAROL == \"admin\"", fonte)
        self.assertIn('pode_executar_acao("3", "excluir")', fonte)
        self.assertIn("if incluir_fichas:\n                    fichas_removidas =", fonte)
        self.assertIn("abas = limpar_dados_operacionais_admin()", fonte)


if __name__ == "__main__":
    unittest.main()
