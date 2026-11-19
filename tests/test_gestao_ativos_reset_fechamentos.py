"""Exclusão administrativa independente dos fechamentos (sem tocar no banco).

A função do backend é carregada isoladamente com stubs. Testa autenticação,
autorização, confirmação e separação dos dados. Nunca executa o DELETE real.
"""
import ast
from pathlib import Path
import sys
import types
import unittest


RAIZ = Path(__file__).resolve().parents[1]
BACKEND = RAIZ / "gestao_ativos_fechamentos.py"
PAGINA = RAIZ / "pages" / "10_Gestao_de_Ativos.py"
MIGRACAO = RAIZ / "sql" / "prisma_fechamentos_reset_admin.sql"


def backend_isolado():
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    nos = [
        n for n in arvore.body if (
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name)
                    and t.id == "CONFIRMACAO_EXCLUSAO_FECHAMENTOS"
                    for t in n.targets)
        ) or (
            isinstance(n, ast.FunctionDef)
            and n.name == "excluir_todos_fechamentos"
        )
    ]
    assert len(nos) == 2
    chamadas = []
    estado = types.SimpleNamespace(
        session_state={"perfil": "admin"}
    )
    permissao = {"permitido": True}
    resposta = {"valor": {"fechamentos_excluidos": 6}}
    class ErroPRISMA(Exception):
        pass
    def tabela(nome, **kwargs):
        chamadas.append((nome, kwargs))
        return resposta["valor"]
    env = {
        "__builtins__": __builtins__, "st": estado,
        "ErroPRISMA": ErroPRISMA,
        "pode_executar_acao": lambda modulo, acao:
            permissao["permitido"] and modulo == "9" and acao == "excluir",
        "_autor": lambda acao: "11111111-1111-1111-1111-111111111111",
        "_tabela": tabela,
    }
    exec(compile(ast.Module(body=nos, type_ignores=[]),
                 str(BACKEND), "exec"), env)
    return env, chamadas, estado, permissao, resposta


class TesteExclusaoIndependenteFechamentos(unittest.TestCase):
    def setUp(self):
        self.auth_anterior = sys.modules.get("auth")
        self.auth = types.ModuleType("auth")
        self.auth.verificar_autenticacao = lambda forcar: forcar is True
        sys.modules["auth"] = self.auth

    def tearDown(self):
        if self.auth_anterior is None:
            sys.modules.pop("auth", None)
        else:
            sys.modules["auth"] = self.auth_anterior

    def test_admin_com_dupla_confirmacao_aciona_apenas_rpc_de_fechamentos(self):
        env, chamadas, _, _, _ = backend_isolado()
        frase = env["CONFIRMACAO_EXCLUSAO_FECHAMENTOS"]
        resposta = env["excluir_todos_fechamentos"](frase)
        self.assertEqual(resposta, {"fechamentos_excluidos": 6})
        self.assertEqual(len(chamadas), 1)
        tabela, kwargs = chamadas[0]
        self.assertEqual(tabela, "rpc/prisma_fechamentos_resetar_registros_tx")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["payload"], {
            "p_autor": "11111111-1111-1111-1111-111111111111",
            "p_confirmacao": "EXCLUIR TODOS OS FECHAMENTOS",
        })
        self.assertNotIn("prisma_ativos_resetar", tabela)

    def test_bloqueio_sem_frase_exata_sem_chamar_banco(self):
        env, chamadas, _, _, _ = backend_isolado()
        for frase in ("", "EXCLUIR FECHAMENTOS", "excluir todos os fechamentos",
                      "RESETAR FICHAS E HISTORICO", None):
            with self.subTest(frase=frase):
                with self.assertRaisesRegex(
                    env["ErroPRISMA"], "frase de confirmação"
                ):
                    env["excluir_todos_fechamentos"](frase)
        self.assertEqual(chamadas, [])

    def test_usuario_operador_ou_sem_permissao_nao_pode_excluir(self):
        env, chamadas, estado, permissoes, _ = backend_isolado()
        for perfil in ("usuario", "operador", "", None):
            estado.session_state["perfil"] = perfil
            with self.subTest(perfil=perfil), self.assertRaises(
                env["ErroPRISMA"]
            ):
                env["excluir_todos_fechamentos"](
                    env["CONFIRMACAO_EXCLUSAO_FECHAMENTOS"]
                )
        estado.session_state["perfil"] = "admin"
        permissoes["permitido"] = False
        with self.assertRaisesRegex(env["ErroPRISMA"], "permissão"):
            env["excluir_todos_fechamentos"](
                env["CONFIRMACAO_EXCLUSAO_FECHAMENTOS"]
            )
        self.assertEqual(chamadas, [])

    def test_sessao_expirada_impede_exclusao(self):
        env, chamadas, _, _, _ = backend_isolado()
        self.auth.verificar_autenticacao = lambda forcar: False
        with self.assertRaisesRegex(env["ErroPRISMA"], "expirada"):
            env["excluir_todos_fechamentos"](
                env["CONFIRMACAO_EXCLUSAO_FECHAMENTOS"]
            )
        self.assertEqual(chamadas, [])

    def test_resposta_invalida_nao_declara_sucesso(self):
        env, _, _, _, resposta = backend_isolado()
        frase = env["CONFIRMACAO_EXCLUSAO_FECHAMENTOS"]
        for valor in ({}, [], {"fechamentos_excluidos": -1},
                      {"fechamentos_excluidos": True},
                      {"fechamentos_excluidos": "6"}):
            with self.subTest(valor=valor):
                resposta["valor"] = valor
                with self.assertRaisesRegex(env["ErroPRISMA"], "não confirmou"):
                    env["excluir_todos_fechamentos"](frase)

    def test_interface_administrativa_apresenta_duas_operacoes_separadas(self):
        pagina = PAGINA.read_text(encoding="utf-8")
        self.assertIn('st.subheader("🚰 Fichas dos ativos")', pagina)
        self.assertIn('st.subheader("🚧 Registros de fechamentos / reparos de rede")',
                      pagina)
        self.assertIn('with st.form("ga_confirmacao_reset_geral"):', pagina)
        self.assertIn('with st.form("ga_confirmacao_exclusao_fechamentos"):', pagina)
        self.assertIn('frase_reset != CONFIRMACAO_RESET_ATIVOS', pagina)
        self.assertIn('frase_fechamentos != CONFIRMACAO_EXCLUSAO_FECHAMENTOS',
                      pagina)
        self.assertIn('resumo_reset = resetar_fichas_e_historico(frase_reset)',
                      pagina)
        self.assertIn('resultado_fechamentos = excluir_todos_fechamentos(',
                      pagina)
        self.assertIn('disabled=not pode_executar_acao("9", "excluir")', pagina)
        self.assertIn('st.session_state.get("perfil") != "admin"', pagina)
        self.assertIn('st.session_state.pop("ga_relatorio_fichas", None)', pagina)

    def test_sql_restrito_aos_fechamentos_com_auditoria_em_transacao(self):
        sql = MIGRACAO.read_text(encoding="utf-8")
        self.assertIn("SECURITY DEFINER", sql)
        self.assertIn("SET search_path = ''", sql)
        self.assertIn("WHERE id = p_autor AND perfil = 'admin' AND status = 'ativo'",
                      sql)
        self.assertIn("LOCK TABLE public.prisma_fechamentos_rede", sql)
        self.assertIn("DELETE FROM public.prisma_fechamentos_rede;", sql)
        self.assertNotIn("DELETE FROM public.prisma_ativos", sql)
        self.assertNotIn("UPDATE public.prisma_ativos", sql)
        self.assertIn("INSERT INTO public.prisma_auditoria", sql)
        self.assertIn("REVOKE ALL ON FUNCTION", sql)
        self.assertIn("FROM PUBLIC, anon, authenticated", sql)
        self.assertIn("TO service_role", sql)

    def test_sintaxe(self):
        ast.parse(BACKEND.read_text(encoding="utf-8"))
        ast.parse(PAGINA.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
