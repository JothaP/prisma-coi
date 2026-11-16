"""Leitura segura das fontes de produção do Módulo 3 para simulação no Farol V3."""
from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from farol_v3_fontes_m3 import filtrar_registros_do_m3

DIA = date(2026, 11, 16)
ROOT = Path(__file__).resolve().parents[1]


class TestarFontesM3(unittest.TestCase):
    def test_pontos_filtrados_antes_de_construir_resultado(self):
        valores = [
            ["Nº da O.S", "Cidade", "Bairro", "Dt. Emissão", "Latitude", "Longitude"],
            ["OS-01", "Teresina", "Centro", "15/11/2026 23:59", "-5,09", "-42,80"],
            ["OS-02", "Teresina", "Centro", "16/11/2026 00:00", "-5,09", "-42,80"],
            ["OS-03", "Teresina", "Centro", "16/11/2026 23:59", "-5,09", "-42,80"],
            ["OS-04", "Teresina", "Centro", "17/11/2026 00:00", "-5,09", "-42,80"],
            ["OS-05", "Teresina", "Centro", "sem data", "-5,09", "-42,80"],
            [],
        ]
        base, contagem = filtrar_registros_do_m3(valores, DIA)
        self.assertEqual(contagem, {"lidos": 5, "do_dia": 2, "fora_do_dia": 3})
        self.assertEqual(base["Nº da O.S"].tolist(), ["OS-02", "OS-03"])
        self.assertEqual(base["Latitude"].tolist(), ["-5,09", "-5,09"])

    def test_arquivo_sem_registros_retorna_diagnostico(self):
        base, estat = filtrar_registros_do_m3([], DIA)
        self.assertTrue(base.empty)
        self.assertEqual(estat["do_dia"], 0)
        base, estat = filtrar_registros_do_m3(
            [["Nº da O.S", "Dt. Emissão"], ["AB", "15/11/2026"]], DIA
        )
        self.assertTrue(base.empty)
        self.assertEqual(estat["lidos"], 1)

    def test_recorte_ignora_linhas_incompletas_e_parses_falsos(self):
        valores = [
            ["Nº da O.S", "Dt. Emissão"],
            ["OS-12", "16/11/2026"],
            ["OS-13", "16/11/2026 10:31"],
            ["OS-14", "31/02/2026"],
        ]
        base, _ = filtrar_registros_do_m3(valores, DIA)
        self.assertEqual(len(base), 2)

    def test_ambiente_usa_somente_v3_e_autorizacao_teste(self):
        page = (ROOT / "pages" / "Ambiente_de_Teste.py").read_text("utf-8")
        self.assertIn('exigir_acesso_modulo("teste")', page)
        self.assertIn("from farol_v3_teste import renderizar", page)
        self.assertNotIn("Investigação de áreas de influência", page)
        self.assertNotIn("Mesclagem de concentrações por evento", page)
        self.assertNotIn("Relatórios integrados (original)", page)

    def test_m3_leitura_em_escopo_restrito_sem_escrita(self):
        fonte = (ROOT / "farol_v3_fontes_m3.py").read_text("utf-8")
        ui = (ROOT / "farol_v3_teste.py").read_text("utf-8")
        self.assertIn('tem_acesso_modulo("3")', fonte)
        self.assertIn("spreadsheets.readonly", fonte)
        self.assertIn('ABA_PONTOS = "PONTOS"', fonte)
        self.assertIn('ABA_EVENTOS = "EVENTOS"', fonte)
        self.assertIn("ler_pontos_m3_dia(dia)", ui)
        self.assertIn("ler_eventos_m3()", ui)
        self.assertNotIn("from pages.3_Farol_Operacional", fonte)
        for trecho in ("append_row(", ".update(", "delete_rows(", "batch_update(", "execute_sql("):
            self.assertNotIn(trecho, fonte)


if __name__ == "__main__":
    unittest.main()
