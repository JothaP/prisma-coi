"""Testes de conciliação sem acesso ao Google/Supabase e sem exclusão."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from auditar_limpeza_pressoes import (  # noqa: E402
    M1_CAMPOS, M2_CAMPOS, FalhaAuditoria, conciliar,
)

M1 = list(M1_CAMPOS) + ["Cod_Protocolo", "__PowerAppsId__"]
M2 = list(M2_CAMPOS)


class AuditoriaTestes(unittest.TestCase):
    def test_m1_setembro_mantem_medicao_extra(self):
        primeira = ["A1", "01/09/2026", "TERESINA", "CENTRO", "-5", "-42",
                    "0", "", "0001", "", ""]
        segunda = ["A2", "02/09/2026", "TERESINA", "CENTRO", "-5.1", "-42",
                   "10", "", "0002", "", ""]
        extra = ["A3", "03/09/2026", "TERESINA", "CENTRO", "-5", "-42",
                 "3", "", "0003", "", ""]
        result = conciliar("1", "2026-09", M1, [primeira, segunda, extra],
                           M1, [primeira, segunda])
        self.assertEqual(result["planilha"], 3)
        self.assertEqual(result["supabase"], 2)
        self.assertEqual(result["registros_conciliados"], 2)
        self.assertEqual(result["somente_planilha"], 1)
        self.assertFalse(result["limpeza_executada"])

    def test_m1_nao_confunde_registro_mudado_com_arquivado(self):
        planilha = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                    "4", "", "0001", "", ""]
        arquivado = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                     "0", "", "0001", "", ""]
        result = conciliar("1", "2026-10", M1, [planilha], M1, [arquivado])
        self.assertEqual(result["registros_conciliados"], 0)
        self.assertEqual(result["somente_planilha"], 1)
        self.assertEqual(result["somente_supabase"], 1)

    def test_m1_numeros_com_ponto_virgula_e_pressao_zero(self):
        planilha = ["X", "01/10/2026", "TERESINA", "CENTRO", "-5,000000", "-42",
                    "0,00", "", "0001", "", ""]
        arquivado = ["X", "2026-10-01", "TERESINA", "CENTRO", "-5", "-42,0",
                     "0", "", "0001", "", ""]
        result = conciliar("1", "2026-10", M1, [planilha], M1, [arquivado])
        self.assertEqual(result["registros_conciliados"], 1)

    def test_m1_multiplos_registros_iguais_conta_corretamente(self):
        linha = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                 "0", "", "0001", "", ""]
        result = conciliar("1", "2026-10", M1, [linha, linha, linha],
                           M1, [linha, linha])
        self.assertEqual(result["registros_conciliados"], 2)
        self.assertEqual(result["somente_planilha"], 1)

    def test_m2_legado_e_com_id(self):
        linha_id = ["MP-001", "01/10/2026", "TERESINA", "P1",
                    "-5", "-42", "0", "ok"]
        legado = ["02/10/2026", "TERESINA", "P2",
                  "-5.1", "-42", "7", "legado"]
        arquivo_legado = ["", *legado]
        result = conciliar("2", "2026-10", M2, [linha_id, legado],
                           M2, [linha_id, arquivo_legado])
        self.assertEqual(result["registros_conciliados"], 2)
        self.assertEqual(result["somente_planilha"], 0)

    def test_m2_linha_invalida_interrompe(self):
        with self.assertRaises(FalhaAuditoria):
            conciliar("2", "2026-10", M2, [["linha", "invalida"]],
                     M2, [["MP-1", "01/10/2026", "TERESINA",
                           "P1", "-5", "-42", "0", ""]])

    def test_m1_data_invalida_interrompe(self):
        with self.assertRaises(FalhaAuditoria):
            conciliar("1", "2026-10", M1, [["A1", "sem data", "TERESINA",
                    "CENTRO", "-5", "-42", "0", "", "0001", "", ""]], M1, [])

    def test_m1_diferenca_colunas_auxiliares_nao_invalida_igualdade(self):
        linha = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                 "0", "", "0001", "", "id-outro"]
        arquivo = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                   "0", "", "0001", "", "id-arquivo"]
        result = conciliar("1", "2026-10", M1, [linha], M1, [arquivo])
        self.assertEqual(result["registros_conciliados"], 1)


if __name__ == "__main__":
    unittest.main()
