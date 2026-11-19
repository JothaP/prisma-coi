"""Regressões das regras por perfil, exceções individuais e identificação 3B.

Executa por AST sem conectar ao Supabase nem escrever dados de usuários.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "prisma_backend.py"
AUTH = ROOT / "auth.py"
HUB = ROOT / "app.py"
ACESSOS = ROOT / "pages" / "9_Gestao_de_Acessos.py"
FAROL = ROOT / "pages" / "12_Farol_Historico.py"
M3 = ROOT / "pages" / "3_Farol_Operacional.py"
SQL = ROOT / "sql" / "prisma_permissoes_perfil.sql"

MODULOS = {
    **{str(i): f"Modulo {i}" for i in range(1, 10)},
    "historico": "Módulo 3B — Farol Histórico",
    "teste": "Ambiente de Teste",
    "gestao": "Gestão de Acessos",
}
ACOES = ("visualizar", "criar", "importar", "editar", "excluir", "exportar", "relatorios")


def carregar_funcoes(*nomes, extra=None):
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    encontrados = [
        n for n in arvore.body if isinstance(n, ast.FunctionDef) and n.name in nomes
    ]
    assert {n.name for n in encontrados} == set(nomes)
    ambiente = {"MODULOS": MODULOS, "ACOES": ACOES, **(extra or {})}
    exec(compile(ast.Module(body=encontrados, type_ignores=[]), str(BACKEND), "exec"), ambiente)
    return ambiente


class TesteAcessosPorPerfil(unittest.TestCase):
    def setUp(self):
        env = carregar_funcoes("permissoes_padrao", "permissoes_efetivas")
        self.padrao = env["permissoes_padrao"]
        self.efetivas = env["permissoes_efetivas"]

    def test_sem_configuracao_preserva_regras_antigas(self):
        for perfil in ("usuario", "operador"):
            self.assertEqual(self.padrao(perfil, []), self.padrao(perfil))
        self.assertTrue(self.efetivas("usuario", [])[("1", "editar")])
        self.assertFalse(self.efetivas("operador", [])[("3", "editar")])
        self.assertTrue(self.efetivas("operador", [])[("3", "visualizar")])

    def test_alteracao_por_tipo_afeta_apenas_o_perfil_selecionado(self):
        regras = [
            {"perfil": "usuario", "modulo": "3", "acao": "visualizar", "permitido": False},
            {"perfil": "operador", "modulo": "historico", "acao": "visualizar", "permitido": True},
        ]
        self.assertFalse(self.efetivas("usuario", [], regras)[("3", "visualizar")])
        self.assertTrue(self.efetivas("operador", [], regras)[("3", "visualizar")])
        self.assertTrue(self.efetivas("operador", [], regras)[("historico", "visualizar")])
        self.assertFalse(self.efetivas("usuario", [], regras)[("historico", "visualizar")])

    def test_excecoes_individuais_prevalecem_e_restauracao_retorna_ao_perfil(self):
        regras = [{"perfil": "operador", "modulo": "7", "acao": "visualizar", "permitido": True}]
        personalizada = [{"modulo": "7", "acao": "visualizar", "permitido": False}]
        self.assertFalse(self.efetivas("operador", personalizada, regras)[("7", "visualizar")])
        self.assertTrue(self.efetivas("operador", [], regras)[("7", "visualizar")])

    def test_admin_e_gestao_nao_podem_ser_bloqueados(self):
        regras = [
            {"perfil": "usuario", "modulo": "gestao", "acao": "visualizar", "permitido": True},
            {"perfil": "admin", "modulo": "3", "acao": "visualizar", "permitido": False},
            {"perfil": "operador", "modulo": "9", "acao": "visualizar", "permitido": False},
        ]
        individuais = [{"modulo": "gestao", "acao": "editar", "permitido": True}]
        self.assertFalse(self.efetivas("usuario", individuais, regras)[("gestao", "visualizar")])
        self.assertFalse(self.efetivas("usuario", individuais, regras)[("gestao", "editar")])
        self.assertTrue(self.efetivas("operador", [], regras)[("9", "visualizar")])
        admin = self.efetivas("admin", [{"modulo": "3", "acao": "visualizar", "permitido": False}], regras)
        self.assertTrue(all(admin.values()))

    def test_configuracao_recusa_perfil_admin_e_matriz_incompleta(self):
        chamadas = []
        def guardar(tabela, **kwargs):
            chamadas.append((tabela, kwargs))
        class ErroPRISMA(Exception):
            pass
        ambiente = carregar_funcoes(
            "_exigir_admin_permissoes", "salvar_permissoes_por_perfil",
            extra={
                "perfil_de_usuario": lambda uid: {"perfil": "admin", "status": "ativo"},
                "_tabela": guardar,
                "ErroPRISMA": ErroPRISMA,
                "auditar": lambda *a, **kw: None,
            },
        )
        salvar = ambiente["salvar_permissoes_por_perfil"]
        with self.assertRaises(ErroPRISMA):
            salvar("admin-id", "admin", {})
        with self.assertRaises(ErroPRISMA):
            salvar("admin-id", "usuario", {("1", "visualizar"): True})
        self.assertEqual(chamadas, [])

    def test_salvar_grava_apenas_permissoes_padrao_e_audita(self):
        chamadas, auditoria = [], []
        class ErroPRISMA(Exception):
            pass
        ambiente = carregar_funcoes(
            "_exigir_admin_permissoes", "salvar_permissoes_por_perfil",
            extra={
                "perfil_de_usuario": lambda uid: {"perfil": "admin", "status": "ativo"},
                "_tabela": lambda tabela, **kw: chamadas.append((tabela, kw)),
                "ErroPRISMA": ErroPRISMA,
                "auditar": lambda *args, **kw: auditoria.append((args, kw)),
            },
        )
        matriz = {
            (m, a): False for m in MODULOS if m != "gestao"
            for a in ACOES if (m, a) != ("9", "visualizar")
        }
        matriz[("historico", "visualizar")] = True
        ambiente["salvar_permissoes_por_perfil"]("id-admin", "operador", matriz)
        self.assertEqual(len(chamadas), 1)
        tabela, kw = chamadas[0]
        self.assertEqual(tabela, "prisma_permissoes_perfil")
        self.assertEqual(kw["method"], "POST")
        self.assertEqual(len(kw["payload"]), len(matriz))
        self.assertTrue(all(x["perfil"] == "operador" for x in kw["payload"]))
        self.assertFalse(any(x["modulo"] == "gestao" for x in kw["payload"]))
        self.assertEqual(len(auditoria), 1)

    def test_restauracao_exige_admin_e_nao_apaga_padroes(self):
        chamadas = []
        class ErroPRISMA(Exception):
            pass
        usuario = {"perfil": "usuario", "status": "ativo"}
        def perfil(uid):
            return {"perfil": "admin", "status": "ativo"} if uid == "admin-id" else usuario
        env = carregar_funcoes(
            "_exigir_admin_permissoes", "restaurar_permissoes_do_perfil",
            extra={
                "perfil_de_usuario": perfil,
                "_tabela": lambda tabela, **kw: chamadas.append((tabela, kw)),
                "ErroPRISMA": ErroPRISMA,
                "auditar": lambda *args, **kw: None,
            },
        )
        restaurar = env["restaurar_permissoes_do_perfil"]
        with self.assertRaises(ErroPRISMA):
            restaurar("nao-admin", "usuario-id")
        self.assertEqual(chamadas, [])
        restaurar("admin-id", "usuario-id")
        self.assertEqual(chamadas[0][0], "prisma_permissoes")
        self.assertEqual(chamadas[0][1]["method"], "DELETE")
        self.assertNotIn("prisma_permissoes_perfil", str(chamadas))

    def test_tela_abas_autenticacao_e_migracao(self):
        ui = ACESSOS.read_text(encoding="utf-8")
        auth = AUTH.read_text(encoding="utf-8")
        sql = SQL.read_text(encoding="utf-8")
        self.assertIn('"👤 Acessos individuais", "👥 Acessos por tipo de usuário"', ui)
        self.assertIn("restaurar_permissoes_do_perfil(ator, u[", ui)
        self.assertIn("permissoes_por_perfil()", auth)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("REVOKE ALL", sql)
        self.assertIn("FROM PUBLIC, anon, authenticated", sql)

    def test_somente_farol_historico_recebe_3b(self):
        hub = HUB.read_text(encoding="utf-8")
        page = FAROL.read_text(encoding="utf-8")
        m3 = M3.read_text(encoding="utf-8")
        self.assertIn('"numero": "historico", "titulo": "Farol Histórico"', hub)
        self.assertIn('"MÓDULO 3B" if indice == "historico"', hub)
        self.assertIn('st.title("🕰️ Módulo 3B — Farol Histórico")', page)
        self.assertIn('page_title="Farol Operacional — Módulo 3"', m3)
        for i, nome in enumerate((
            "Baixa Pressão", "Mapeamento de Pressão", "Farol Operacional",
            "Ferramentas Operacionais", "Cadastros e Consultas COI",
            "Ferramentas Adicionais", "Relatórios Integrados",
            "Consulta Operacional", "Gestão de Campo",
        ), start=1):
            self.assertIn(f'"numero": "{i}", "titulo": "{nome}"', hub)


if __name__ == "__main__":
    unittest.main()
