"""Regressões da matrícula mais próxima dos pontos P1, P2... no Módulo 8."""
import ast
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd


FONTE = Path(__file__).resolve().parents[1] / "pages" / "8_Consulta_Operacional.py"
CODIGO = FONTE.read_text(encoding="utf-8")
ARVORE = ast.parse(CODIGO, filename=str(FONTE))
NOMES = {"_coordenada", "_conexao_leitura", "_m8_matriculas_mais_proximas"}
FUNCOES = [
    no for no in ARVORE.body
    if isinstance(no, ast.FunctionDef) and no.name in NOMES
]
assert {no.name for no in FUNCOES} == NOMES
for funcao in FUNCOES:
    funcao.decorator_list = []  # Não inicializar Streamlit no teste isolado.
AMBIENTE = {"pd": pd, "Path": Path, "math": math, "sqlite3": sqlite3}
exec(compile(ast.Module(body=FUNCOES, type_ignores=[]), str(FONTE), "exec"), AMBIENTE)


class TestarMatriculaMaisProxima(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "matriculas.sqlite"
        with sqlite3.connect(self.db) as conexao:
            conexao.execute(
                "CREATE TABLE matriculas ("
                "NUM_LIGACAO TEXT, COD_LATITUDE TEXT, COD_LONGITUDE TEXT)"
            )
            conexao.executemany(
                "INSERT INTO matriculas VALUES (?, ?, ?)",
                [
                    ("000123", "-5.14474000", "-42.78857000"),
                    ("000124", "-5,14474000", "-42,78858000"),
                    ("000900", "-5.30000000", "-42.88000000"),
                    ("009999", "não informado", "-42.8"),
                    ("009998", "-5.11", "coordenada inválida"),
                    ("", "-5.14474", "-42.78857"),
                ],
            )
        stat = self.db.stat()
        self.assinatura = (str(self.db), stat.st_size, stat.st_mtime_ns)
        self.buscar = AMBIENTE["_m8_matriculas_mais_proximas"]

    def test_encontra_matricula_mais_proxima_para_cada_ponto(self):
        pontos = (
            (-5.14474000, -42.78857000),
            (-5.30000000, -42.88000000),
            (-5.14474000, -42.78857900),
        )
        resultados = self.buscar(self.assinatura, pontos)
        self.assertEqual([item[0] for item in resultados],
                         ["000123", "000900", "000124"])
        self.assertEqual(resultados[0][1], 0.0)
        self.assertLess(resultados[2][1], 1)

    def test_nao_limita_busca_a_raio_de_equipamentos(self):
        # Nenhuma matrícula está a menos de 750 m deste ponto.
        matricula, distancia = self.buscar(
            self.assinatura, ((-5.5, -43.0),)
        )[0]
        self.assertTrue(matricula)
        self.assertGreater(distancia, 750)

    def test_preserva_zeros_esquerda_e_ordem_entrada(self):
        resultados = self.buscar(
            self.assinatura,
            ((-5.3, -42.88), (-5.14474, -42.78857), (-5.3, -42.88)),
        )
        self.assertEqual([r[0] for r in resultados],
                         ["000900", "000123", "000900"])

    def test_descarta_coordenadas_invalidas_e_banco_vazio(self):
        self.assertEqual(self.buscar(self.assinatura, ()), ())
        with sqlite3.connect(self.db) as conexao:
            conexao.execute("DELETE FROM matriculas")
        stat = self.db.stat()
        nova_assinatura = (str(self.db), stat.st_size, stat.st_mtime_ns)
        self.assertEqual(
            self.buscar(nova_assinatura, ((-5.1, -42.8),)),
            (("", None),),
        )

    def test_tabela_exibe_coluna_solicitada(self):
        self.assertIn('"Matrícula mais próxima": (', CODIGO)
        self.assertIn('matriculas_proximas[indice - 1][0]', CODIGO)
        self.assertIn('Localizando a matrícula mais próxima de cada ponto', CODIGO)


if __name__ == "__main__":
    unittest.main()
