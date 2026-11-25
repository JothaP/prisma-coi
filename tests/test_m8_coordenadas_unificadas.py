"""Regressões da pesquisa de coordenadas em campo único no Módulo 8."""
import ast
import math
from pathlib import Path
import re
import unittest


FONTE = Path(__file__).resolve().parents[1] / "pages" / "8_Consulta_Operacional.py"
CODIGO = FONTE.read_text(encoding="utf-8")
ARVORE = ast.parse(CODIGO, filename=str(FONTE))
NOMES = {"_m8_interpretar_coordenadas", "_m8_interpretar_par_coordenadas"}
FUNCOES = [
    no for no in ARVORE.body
    if isinstance(no, ast.FunctionDef) and no.name in NOMES
]
assert {no.name for no in FUNCOES} == NOMES
AMBIENTE = {"math": math, "re": re}
exec(compile(ast.Module(body=FUNCOES, type_ignores=[]), str(FONTE), "exec"), AMBIENTE)


class TestarCoordenadasUnificadas(unittest.TestCase):
    def setUp(self):
        self.interpretar = AMBIENTE["_m8_interpretar_par_coordenadas"]

    def test_formato_virgula_ou_espaco(self):
        for entrada in (
            "-5.14474000, -42.78857000",
            "-5.14474000 -42.78857000",
            "-5.14474000,-42.78857000",
            "  -5.14474000  -42.78857000  ",
            "-5.14474000 -42.78857000;",
        ):
            with self.subTest(entrada=entrada):
                self.assertEqual(self.interpretar(entrada), (-5.14474, -42.78857))

    def test_decimal_com_virgula_tambem_aceito(self):
        self.assertEqual(
            self.interpretar("-5,14474000 -42,78857000"),
            (-5.14474, -42.78857),
        )
        self.assertEqual(
            self.interpretar("-5,14474000, -42,78857000"),
            (-5.14474, -42.78857),
        )

    def test_rejeita_entrada_incompleta_ou_varios_pontos(self):
        for entrada in (
            "", "-5.14474000", "coordenadas",
            "-5.14474000, -42.78857000; -5.2 -42.8",
        ):
            with self.subTest(entrada=entrada):
                with self.assertRaisesRegex(ValueError, "mesmo campo"):
                    self.interpretar(entrada)

    def test_valida_limites_geograficos(self):
        for entrada, mensagem in (
            ("-91, -42", "Latitude fora"),
            ("-5, -181", "Longitude fora"),
        ):
            with self.subTest(entrada=entrada):
                with self.assertRaisesRegex(ValueError, mensagem):
                    self.interpretar(entrada)

    def test_interface_usa_apenas_um_campo(self):
        self.assertIn('key="m8_coordenadas_pesquisa"', CODIGO)
        self.assertNotIn('key="m8_latitude_pesquisa"', CODIGO)
        self.assertNotIn('key="m8_longitude_pesquisa"', CODIGO)
        self.assertIn("_m8_interpretar_par_coordenadas(\n                coordenadas_digitadas", CODIGO)


if __name__ == "__main__":
    unittest.main()
