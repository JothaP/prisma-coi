"""Regressões da pesquisa de coordenadas em campo único no Módulo 8."""
import ast
import math
from pathlib import Path
import re
import unittest


FONTE = Path(__file__).resolve().parents[1] / "pages" / "8_Consulta_Operacional.py"
CODIGO = FONTE.read_text(encoding="utf-8")
ARVORE = ast.parse(CODIGO, filename=str(FONTE))
NOMES = {
    "_m8_interpretar_coordenadas", "_m8_interpretar_par_coordenadas",
    "_m8_interpretar_lista_coordenadas", "_m8_normalizar_pontos",
}
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

    def test_lista_preserva_ordem_para_rotulos(self):
        coordenadas = (
            "-5.14474000, -42.78857000\n"
            "-5.14485000 -42.78868000\n"
            "-5.14502000, -42.78891000\n"
            "-5.14518000 -42.78905000\n"
            "-5.14535000, -42.78923000"
        )
        pontos = AMBIENTE["_m8_interpretar_lista_coordenadas"](coordenadas)
        self.assertEqual(len(pontos), 5)
        self.assertEqual(pontos[0], (-5.14474, -42.78857))
        self.assertEqual(pontos[3], (-5.14518, -42.78905))
        self.assertEqual(pontos[-1], (-5.14535, -42.78923))

    def test_ignora_linhas_em_branco_sem_alterar_numeracao(self):
        pontos = AMBIENTE["_m8_interpretar_lista_coordenadas"](
            "\n-5.1, -42.1\n\n-5.2 -42.2\n"
        )
        self.assertEqual(pontos, ((-5.1, -42.1), (-5.2, -42.2)))

    def test_erro_identifica_linha_e_nao_substitui_parcialmente(self):
        for entrada, mensagem in (
            ("", "ao menos uma"),
            ("-5.1,-42.1\n-5.2", "Linha 2"),
            ("-5.1,-42.1\n999,-42.2", "Linha 2"),
            ("-5.1,-42.1\n-5.2,-42.2\ntexto", "Linha 3"),
        ):
            with self.subTest(entrada=entrada):
                with self.assertRaisesRegex(ValueError, mensagem):
                    AMBIENTE["_m8_interpretar_lista_coordenadas"](entrada)

    def test_limite_de_cem_pontos(self):
        entrada = "\n".join(["-5.1,-42.1"] * 100)
        self.assertEqual(
            len(AMBIENTE["_m8_interpretar_lista_coordenadas"](entrada)), 100
        )
        with self.assertRaisesRegex(ValueError, "100"):
            AMBIENTE["_m8_interpretar_lista_coordenadas"](entrada + "\n-5.2,-42.2")

    def test_sessoes_antigas_com_um_par_continuam_compativeis(self):
        normalizar = AMBIENTE["_m8_normalizar_pontos"]
        self.assertEqual(normalizar(None), ())
        self.assertEqual(normalizar((-5.1, -42.1)), ((-5.1, -42.1),))
        self.assertEqual(
            normalizar(((-5.1, -42.1), (-5.2, -42.2))),
            ((-5.1, -42.1), (-5.2, -42.2)),
        )

    def test_interface_usa_apenas_um_campo(self):
        self.assertIn('key="m8_coordenadas_pesquisa"', CODIGO)
        self.assertNotIn('key="m8_latitude_pesquisa"', CODIGO)
        self.assertNotIn('key="m8_longitude_pesquisa"', CODIGO)
        self.assertIn("st.text_area(", CODIGO)
        self.assertIn("_m8_interpretar_lista_coordenadas(\n                coordenadas_digitadas", CODIGO)


if __name__ == "__main__":
    unittest.main()
