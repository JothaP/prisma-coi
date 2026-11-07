"""Testes isolados da leitura híbrida: não dependem de Supabase ou Google."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# Substitui Streamlit apenas neste processo de teste: o helper não deve
# abrir servidores nem consultar serviços externos durante os testes.
if "streamlit" not in sys.modules:
    fake = types.ModuleType("streamlit")
    fake.cache_data = lambda **kwargs: lambda func: func
    fake.secrets = {}
    sys.modules["streamlit"] = fake

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import historico_pressao_supabase as historico  # noqa: E402


H1 = ["ID", "Data", "Municipio", "Bairro", "Latitude", "Longitude",
      "Pressao_MCA", "Observacao", "Matricula", "Cod_Protocolo", "__PowerAppsId__"]
H2 = historico.COLUNAS_M2[:]


class TesteHistoricoSupabase(unittest.TestCase):
    def test_data_valida_zero_nao_altera_competencia(self):
        self.assertEqual(historico._mes_data("01/10/2026"), "2026-10")
        self.assertEqual(historico._mes_data("2026-10-31"), "2026-10")
        self.assertIsNone(historico._mes_data("sem data"))

    @patch.object(historico, "ativo", return_value=True)
    @patch.object(historico, "_arquivos")
    def test_m1_sheets_prevalece_sem_duplicar_id(self, arquivos, _):
        live = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                "0", "atualizado", "0012", "", ""]
        antigo = ["A1", "01/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                  "1", "antigo", "0012", "", ""]
        adicional = ["A2", "02/10/2026", "TERESINA", "CENTRO", "-5", "-42",
                     "0", "", "0013", "", ""]
        arquivos.return_value = [("modulo_1_baixa_pressao/2026/2026-10.xlsx",
                                  H1, [antigo, adicional])]
        saida, marcas = historico.combinar_modulo1([H1, live])
        self.assertEqual(len(saida), 3)
        self.assertEqual(saida[1][7], "atualizado")
        self.assertEqual(saida[2][8], "0013")
        self.assertEqual(marcas, [False, True])

    @patch.object(historico, "ativo", return_value=False)
    def test_m1_desativado_passa_sheets_sem_mudar(self, _):
        valores = [H1, ["A1", "01/10/2026", "TERESINA"]]
        saida, marcas = historico.combinar_modulo1(valores)
        self.assertEqual(saida, valores)
        self.assertEqual(marcas, [False])

    @patch.object(historico, "ativo", return_value=True)
    @patch.object(historico, "_arquivos")
    def test_m2_preserva_legado_e_nao_edita_historico(self, arquivos, _):
        atual = {
            "Data": "01/10/2026", "Município": "TERESINA", "Pontos": "P1",
            "Latitude": "-5", "Longitude": "-42", "MCA": "0",
            "Observacao": "", "_historico_id": "MP-1",
            "_linha_real": 2, "_arquivo_historico": False,
        }
        repetido = ["MP-1", "01/10/2026", "TERESINA", "P1", "-5", "-42", "0", ""]
        novo = ["MP-2", "02/10/2026", "TERESINA", "P2", "-5", "-42", "1", ""]
        legado = ["", "03/10/2026", "TERESINA", "P3", "-5", "-42", "0", ""]
        arquivos.return_value = [("modulo_2_historico_pressoes/2026/2026-10.xlsx",
                                  H2, [repetido, novo, legado])]
        todos = historico.adicionar_modulo2([atual])
        self.assertEqual(len(todos), 3)
        self.assertEqual(todos[0]["_linha_real"], 2)
        self.assertEqual(todos[1]["_linha_real"], -1)
        self.assertEqual(todos[2]["MCA"], "0")
        self.assertTrue(all(x["_arquivo_historico"] for x in todos[1:]))

    @patch.object(historico, "ativo", return_value=True)
    @patch.object(historico, "_arquivos")
    def test_m2_legado_ja_no_sheets_nao_duplica(self, arquivos, _):
        atual = {
            "Data": "03/10/2026", "Município": "TERESINA", "Pontos": "P3",
            "Latitude": "-5", "Longitude": "-42", "MCA": "0",
            "Observacao": "", "_historico_id": "",
            "_linha_real": 9, "_arquivo_historico": False,
        }
        arquivos.return_value = [("arq", H2, [
            ["", "03/10/2026", "TERESINA", "P3", "-5", "-42", "0", ""]
        ])]
        todos = historico.adicionar_modulo2([atual])
        self.assertEqual(len(todos), 1)

    @patch.object(historico, "ativo", return_value=True)
    @patch.object(historico, "_arquivos")
    def test_m2_id_repetido_no_arquivo_bloqueia(self, arquivos, _):
        linha = ["MP-2", "02/10/2026", "TERESINA", "P2", "-5", "-42", "1", ""]
        arquivos.return_value = [("arq", H2, [linha, linha])]
        with self.assertRaisesRegex(historico.ErroHistorico, "ID duplicado"):
            historico.adicionar_modulo2([])


if __name__ == "__main__":
    unittest.main()
