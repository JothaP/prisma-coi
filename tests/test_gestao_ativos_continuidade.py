"""Continuidade diária sem históricos duplicados ou alteração da data inicial.

Testes isolados da construção de filtros PostgREST e formatação de relatório.
Não consultam nem modificam os registros reais do Supabase.
"""
import ast
from datetime import date
from pathlib import Path
import unittest

from gestao_ativos_relatorio import (
    preparar_fechamentos_relatorio, gerar_pontos_atencao,
)

RAIZ = Path(__file__).resolve().parents[1]
BACKEND = RAIZ / "gestao_ativos_fechamentos.py"
PAGINA = RAIZ / "pages" / "10_Gestao_de_Ativos.py"


def filtro_isolado():
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    nos = [
        n for n in arvore.body
        if (
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "STATUS_FECHAMENTO"
                    for t in n.targets)
        ) or (isinstance(n, ast.FunctionDef)
              and n.name == "_filtros_continuidade")
    ]
    assert len(nos) == 2
    class ErroPRISMA(Exception):
        pass
    contexto = {"date": date, "ErroPRISMA": ErroPRISMA}
    exec(compile(ast.Module(body=nos, type_ignores=[]),
                 str(BACKEND), "exec"), contexto)
    return contexto["_filtros_continuidade"], ErroPRISMA


class TestContinuidadeFechamentos(unittest.TestCase):
    def setUp(self):
        self.filtrar, self.ErroPRISMA = filtro_isolado()
        self.dia1 = date(2026, 11, 17)
        self.dia2 = date(2026, 11, 18)

    def test_sem_status_data_de_hoje_consulta_abertos_de_dias_anteriores(self):
        filtros = self.filtrar(data_referencia=self.dia2)
        self.assertEqual(filtros, {
            "status": "in.(em_andamento,pendente)",
            "data": "lte.2026-11-18",
        })
        # A consulta por data inclui iniciados 17/11 e 18/11, nunca os futuros.
        self.assertLessEqual(self.dia1.isoformat(), self.dia2.isoformat())
        self.assertGreater("2026-11-19", self.dia2.isoformat())

    def test_status_abertos_selecionados_mantem_continuidade(self):
        filtros = self.filtrar(("pendente",), self.dia2)
        self.assertEqual(filtros, {
            "status": "eq.pendente", "data": "lte.2026-11-18",
        })
        dois = self.filtrar(("pendente", "em_andamento"), self.dia2)
        self.assertEqual(dois["status"], "in.(em_andamento,pendente)")
        self.assertEqual(dois["data"], "lte.2026-11-18")

    def test_encerrados_somente_consulta_historica_ou_selecao_expressa(self):
        dia = self.filtrar((), self.dia2)
        self.assertNotIn("encerrado", str(dia))
        self.assertNotIn("cancelado", str(dia))
        encerrados = self.filtrar(("encerrado",), self.dia2)
        self.assertEqual(encerrados, {
            "status": "in.(concluido,encerrado)", "data": "eq.2026-11-18",
        })
        cancelados = self.filtrar(("cancelado",), self.dia2)
        self.assertEqual(cancelados, {
            "status": "eq.cancelado", "data": "eq.2026-11-18",
        })
        historico = self.filtrar(data_referencia=None)
        self.assertEqual(historico, {})
        self.assertEqual(
            self.filtrar(("encerrado",), None)["status"],
            "in.(concluido,encerrado)"
        )

    def test_mistura_abertos_e_encerrados_sem_duplicar_registro(self):
        filtros = self.filtrar(
            ("pendente", "encerrado", "pendente"), self.dia2
        )
        self.assertEqual(set(filtros), {"and"})
        self.assertIn("status.eq.pendente,data.lte.2026-11-18", filtros["and"])
        self.assertIn("status.in.(concluido,encerrado),data.eq.2026-11-18",
                      filtros["and"])
        self.assertEqual(filtros["and"].count("status.eq.pendente"), 1)

    def test_relatorio_inclui_abertos_anteriores_e_arquivados_apenas_da_data(self):
        filtros = self.filtrar(
            data_referencia=self.dia2, incluir_arquivados_do_dia=True
        )
        self.assertEqual(set(filtros), {"and"})
        self.assertIn(
            "status.in.(em_andamento,pendente),data.lte.2026-11-18",
            filtros["and"],
        )
        self.assertIn(
            "status.in.(cancelado,concluido,encerrado),data.eq.2026-11-18",
            filtros["and"],
        )
        fichas = preparar_fechamentos_relatorio([
            {"status": "em_andamento", "data": "2026-11-17",
             "bairro": "Dirceu II", "os": "000900400885"},
            {"status": "pendente", "data": "2026-11-18",
             "bairro": "Centro", "os": "001234567"},
            {"status": "encerrado", "data": "2026-11-18",
             "bairro": "Zona Sul", "os": "900999999"},
        ], data_referencia=self.dia2)
        self.assertEqual(len(fichas), 3)
        self.assertEqual([f["Continuidade"] for f in fichas], ["Sim", "", ""])
        self.assertTrue(fichas[0]["Incluir"])
        self.assertFalse(fichas[2]["Incluir"])
        relatorio = gerar_pontos_atencao(
            "Teresina", self.dia2, [], fechamentos=fichas
        )
        self.assertIn("Dirceu II", relatorio)
        self.assertIn("Data: 17/11/2026", relatorio)
        self.assertIn("Em continuidade de dias anteriores", relatorio)
        self.assertNotIn("Zona Sul", relatorio)
        self.assertEqual(relatorio.count("Dirceu II"), 1)

    def test_somente_filtro_sem_gravar_ou_criar_novo_registro(self):
        backend = BACKEND.read_text(encoding="utf-8")
        funcao = backend.split("def _filtros_continuidade(", 1)[1].split(
            "\ndef listar_fechamentos(", 1
        )[0]
        self.assertNotIn("method=\"POST\"", funcao)
        self.assertNotIn("method=\"PATCH\"", funcao)
        self.assertNotIn("DELETE", funcao)
        pagina = PAGINA.read_text(encoding="utf-8")
        self.assertIn("reparos_em_continuidade = (", pagina)
        self.assertIn("data_referencia=data_relatorio", pagina)
        self.assertIn("data_referencia=data_relatorio\n", pagina)
        self.assertIn("data_referencia=data_relatorio\n", pagina)
        self.assertIn("Em continuidade de dias anteriores", pagina)

    def test_dados_invalidos_bloqueados(self):
        with self.assertRaises(self.ErroPRISMA):
            self.filtrar(("status_inexistente",), self.dia2)
        with self.assertRaises(self.ErroPRISMA):
            self.filtrar((), "data-invalida")

    def test_sintaxe(self):
        ast.parse(BACKEND.read_text(encoding="utf-8"))
        ast.parse(PAGINA.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
