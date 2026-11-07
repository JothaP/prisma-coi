"""Testes sem credenciais para o parser de histórico do Módulo 2."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from arquivar_historico_pressao_modulo2 import (  # noqa: E402
    _data, _medicao_valida, selecionar_competencia,
)

CABECALHO = ["ID", "Data", "Município", "Pontos", "Latitude", "Longitude", "MCA", "Observação"]


class TesteArquivoModulo2(unittest.TestCase):
    def test_mistura_layout_com_id_e_legado(self):
        origem = [CABECALHO,
                  ["P-001", "09/10/2026", "TERESINA", "Ponto A", "-5.01", "-42.80", "0", "ok"],
                  ["10/10/2026", "TERESINA", "Ponto B", "-5,02", "-42,81", "1,2", "legado"],
                  ["P-002", "10/09/2026", "TERESINA", "Ponto C", "-5.03", "-42.82", "4", "outro mês"]]
        colunas, linhas, meta = selecionar_competencia(origem, "2026-10")
        self.assertEqual(colunas, CABECALHO)
        self.assertEqual(len(linhas), 2)
        self.assertEqual(meta["sem_id"], 1)
        self.assertEqual(meta["com_id"], 1)
        self.assertEqual(linhas[0][0], "P-001")
        self.assertEqual(linhas[0][6], "0")
        self.assertEqual(linhas[1][0], "")
        self.assertEqual(linhas[1][6], "1,2")

    def test_cabecalho_legado(self):
        _, linhas, meta = selecionar_competencia([
            CABECALHO[1:],
            ["01/10/2026", "TERESINA", "P1", "-5", "-42", "0", ""],
        ], "2026-10")
        self.assertEqual(meta["cabecalho"], "sem_id")
        self.assertEqual(linhas[0][0], "")

    def test_data_iso_e_serial_excel(self):
        self.assertEqual(_data("2026-10-31T12:30:40").isoformat(), "2026-10-31")
        self.assertIsNotNone(_data("46327"))

    def test_pressao_zero_valida_e_negativa_invalida(self):
        campos = ["01/10/2026", "TERESINA", "P1", "-5", "-42", "0", ""]
        self.assertTrue(_medicao_valida(campos))
        campos[5] = "-1"
        self.assertFalse(_medicao_valida(campos))

    def test_linha_invalida_bloqueia_exportacao(self):
        with self.assertRaisesRegex(ValueError, "não interpretáveis"):
            selecionar_competencia([
                CABECALHO,
                ["P-001", "09/10/2026", "TERESINA", "Ponto A", "-5", "-42", "0", ""],
                ["linha", "sem", "geometria"],
            ], "2026-10")

    def test_rejeita_cabecalho_inesperado(self):
        with self.assertRaisesRegex(ValueError, "Cabeçalho Módulo 2 desconhecido"):
            selecionar_competencia([["Data", "Bairro"], ["01/10/2026", "A"]], "2026-10")


if __name__ == "__main__":
    unittest.main()
