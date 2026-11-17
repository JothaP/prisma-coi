"""Segurança do reset administrativo de fichas e histórico do Módulo 9.

Nunca conecta ao banco: usa cópia isolada da função e stubs de autenticação.
"""
import ast
from pathlib import Path
import sys
import types
import unittest


RAIZ = Path(__file__).resolve().parents[1]
BACKEND = RAIZ / "gestao_ativos_backend.py"
PAGE = RAIZ / "pages" / "10_Gestao_de_Ativos.py"


def carregar_funcao_stub():
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    nos = [
        node for node in arvore.body
        if (isinstance(node, ast.Assign) and any(
            isinstance(x, ast.Name) and x.id in ("MODULO", "CONFIRMACAO_RESET_ATIVOS")
            for x in node.targets
        )) or (isinstance(node, ast.FunctionDef) and
               node.name == "resetar_fichas_e_historico")
    ]
    if len(nos) != 3:
        raise AssertionError("Backend de reset não encontrado na forma esperada")
    chamadas = []
    fake_st = types.SimpleNamespace(session_state={"perfil": "admin"})
    contexto = {
        "__builtins__": __builtins__,
        "st": fake_st,
        "ErroPRISMA": ValueError,
        "pode_executar_acao": lambda modulo, acao: modulo == "9" and acao == "excluir",
        "_autor": lambda acao: "11111111-1111-1111-1111-111111111111",
        "_tabela": lambda path, **kwargs: (
            chamadas.append((path, kwargs)) or {
                "ativos_preservados": 3748, "fichas_limpas": 3,
                "historicos_excluidos": 4,
            }
        ),
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=nos, type_ignores=[])), str(BACKEND), "exec"), contexto)
    return contexto, chamadas, fake_st


class TestGestaoAtivosResetAdmin(unittest.TestCase):
    def setUp(self):
        self.auth_anterior = sys.modules.get("auth")
        self.mod_auth = types.ModuleType("auth")
        self.mod_auth.verificar_autenticacao = lambda forcar: forcar is True
        sys.modules["auth"] = self.mod_auth

    def tearDown(self):
        if self.auth_anterior is None:
            sys.modules.pop("auth", None)
        else:
            sys.modules["auth"] = self.auth_anterior

    def test_admin_confirma_e_envia_para_rpc_transacional(self):
        contexto, chamadas, _ = carregar_funcao_stub()
        frase = contexto["CONFIRMACAO_RESET_ATIVOS"]
        resposta = contexto["resetar_fichas_e_historico"](frase)
        self.assertEqual(resposta["fichas_limpas"], 3)
        self.assertEqual(len(chamadas), 1)
        path, kwargs = chamadas[0]
        self.assertEqual(path, "rpc/prisma_ativos_resetar_fichas_historico_tx")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["payload"]["p_confirmacao"], frase)
        self.assertEqual(kwargs["payload"]["p_autor"],
                         "11111111-1111-1111-1111-111111111111")

    def test_usuario_e_operador_nao_podem_apagar(self):
        for perfil in ("usuario", "operador", "", None):
            with self.subTest(perfil=perfil):
                contexto, chamadas, st = carregar_funcao_stub()
                st.session_state["perfil"] = perfil
                with self.assertRaisesRegex(ValueError, "somente para administradores"):
                    contexto["resetar_fichas_e_historico"](
                        contexto["CONFIRMACAO_RESET_ATIVOS"])
                self.assertEqual(chamadas, [])

    def test_sessao_expirada_bloqueia_mesmo_admin(self):
        contexto, chamadas, _ = carregar_funcao_stub()
        self.mod_auth.verificar_autenticacao = lambda forcar: False
        with self.assertRaisesRegex(ValueError, "expirada"):
            contexto["resetar_fichas_e_historico"](
                contexto["CONFIRMACAO_RESET_ATIVOS"])
        self.assertEqual(chamadas, [])

    def test_confirmacao_exata_obrigatoria(self):
        contexto, chamadas, _ = carregar_funcao_stub()
        for frase in ("", "resetar", "RESETAR FICHAS", "RESETAR FICHAS E HISTÓRICO"):
            with self.subTest(frase=frase):
                with self.assertRaisesRegex(ValueError, "frase de confirmação"):
                    contexto["resetar_fichas_e_historico"](frase)
        self.assertEqual(chamadas, [])

    def test_interface_apenas_para_admin_e_com_confirmacao_dupla(self):
        codigo = PAGE.read_text(encoding="utf-8")
        self.assertIn('if st.session_state.get("perfil") == "admin":', codigo)
        self.assertIn('@st.dialog("⚙️ Administração — Gestão de Campo"', codigo)
        self.assertIn('st.button("⚙️ Administração"', codigo)
        self.assertIn('def _dialogo_administracao():', codigo)
        self.assertIn('with st.form("ga_confirmacao_reset_geral"):', codigo)
        self.assertIn("aceite_reset = st.checkbox(", codigo)
        self.assertIn('frase_reset != CONFIRMACAO_RESET_ATIVOS', codigo)
        self.assertIn("resumo_reset = resetar_fichas_e_historico(frase_reset)", codigo)
        self.assertIn("st.session_state.pop(\"ga_painel\", None)", codigo)

    def test_escopo_preserva_identidade_e_dados_ativos(self):
        codigo = BACKEND.read_text(encoding="utf-8")
        # A aplicação não oferece operações DELETE de ativo nem limpeza de cadastro.
        func = codigo.split("def resetar_fichas_e_historico(", 1)[1]
        self.assertNotIn('"prisma_ativos", method="DELETE"', func)
        self.assertNotIn('payload={"planta":', func)
        self.assertNotIn('payload={"latitude":', func)
        self.assertNotIn('payload={"municipio":', func)

    def test_sintaxe(self):
        ast.parse(BACKEND.read_text(encoding="utf-8"))
        ast.parse(PAGE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
