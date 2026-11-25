"""Regressoes isoladas do historico de medicoes nas areas Sanear (Modulo 1)."""
from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from bp_sanear_historico import (
    TODAS_AREAS, filtrar_historico_sanear, opcoes_areas_sanear,
)


def quadrado(oeste, sul, leste, norte):
    return [[
        [oeste, sul], [leste, sul], [leste, norte],
        [oeste, norte], [oeste, sul],
    ]]


def feature(nome, coords):
    return {
        "type": "Feature",
        "properties": {"Área": nome, "Bacia": "Teste", "Município": "Teresina"},
        "geometry": {"type": "Polygon", "coordinates": coords},
    }


GEO = {
    "type": "FeatureCollection",
    "features": [
        feature("A", quadrado(-42.80, -5.20, -42.70, -5.10) + quadrado(
            -42.77, -5.17, -42.75, -5.15
        )),
        feature("B", quadrado(-42.75, -5.20, -42.65, -5.10)),
        feature("A", quadrado(-42.60, -5.20, -42.55, -5.10)),
    ],
}


def base():
    dados = [
        ("a", "01/01/2025", -5.11, -42.79, 2.0),
        ("sobreposicao", "02/01/2025", -5.11, -42.74, 0.0),
        ("furo", "03/01/2025", -5.16, -42.76, 3.0),
        ("b", "04/01/2025", -5.11, -42.66, 6.0),
        ("borda", "05/01/2025", -5.11, -42.65, 8.0),
        ("a-separada", "06/01/2025", -5.11, -42.57, 11.0),
        ("antiga", "31/12/2024", -5.11, -42.79, 12.0),
        ("invalida", "05/01/2025", None, -42.79, 1.0),
        ("fora", "05/01/2025", -5.30, -42.70, 1.0),
        ("data-invalida", "sem data", -5.11, -42.79, 1.0),
    ]
    return pd.DataFrame([
        {
            "ID": identificador, "Data": data, "Latitude": lat,
            "Longitude": lon, "Pressao_MCA": pressao,
            "Municipio": "TERESINA", "Bairro": "TESTE",
            "Matricula": "0000001", "_arquivo_historico": True,
        }
        for identificador, data, lat, lon, pressao in dados
    ])


class TestarHistoricoSanear(unittest.TestCase):
    def test_opcoes_agrupam_partes_da_mesma_area(self):
        opcoes = opcoes_areas_sanear(GEO)
        self.assertEqual(opcoes[TODAS_AREAS], (0, 1, 2))
        area_a = next(k for k in opcoes if k.startswith("A |"))
        self.assertEqual(opcoes[area_a], (0, 2))

    def test_todas_as_areas_nao_duplicam_sobreposicoes(self):
        got = filtrar_historico_sanear(
            base(), GEO, (0, 1, 2), date(2025, 1, 1), date(2025, 1, 31),
        )
        self.assertEqual(
            set(got["ID"]), {"a", "sobreposicao", "b", "borda", "a-separada"}
        )
        self.assertEqual(len(got), 5)
        self.assertEqual(
            got.loc[got["ID"] == "sobreposicao", "Área Sanear"].iloc[0],
            "A; B",
        )
        self.assertEqual(got["ID"].iloc[0], "a-separada")
        self.assertTrue(got["_arquivo_historico"].all())

    def test_area_individual_e_periodo_inclusivo(self):
        got = filtrar_historico_sanear(
            base(), GEO, (0, 2), date(2025, 1, 2), date(2025, 1, 6),
        )
        self.assertEqual(set(got["ID"]), {"sobreposicao", "a-separada"})
        self.assertTrue((got["Área Sanear"] == "A").all())

    def test_sem_resultados_mantem_colunas_de_saida(self):
        got = filtrar_historico_sanear(
            base(), GEO, (0,), date(2026, 1, 1), date(2026, 12, 31),
        )
        self.assertTrue(got.empty)
        self.assertIn("Área Sanear", got.columns)
        self.assertIn("DataObjSanear", got.columns)

    def test_filtro_datas_e_geometria_invalidos(self):
        with self.assertRaisesRegex(ValueError, "data inicial"):
            filtrar_historico_sanear(
                base(), GEO, (0,), date(2026, 1, 1), date(2025, 1, 1),
            )
        got = filtrar_historico_sanear(
            base(), GEO, (91,), date(2025, 1, 1), date(2025, 1, 31),
        )
        self.assertTrue(got.empty)

    def test_interface_tem_consulta_e_exportacao_sem_os(self):
        codigo = (Path(__file__).resolve().parents[1] /
                  "pages" / "1_Baixa_Pressao.py").read_text(encoding="utf-8")
        self.assertIn('with st.expander("🛠️ Histórico de medições nas áreas Sanear"', codigo)
        self.assertIn("df, geo_sanear_hist,", codigo)
        self.assertIn('key="bp_sanear_hist_area"', codigo)
        self.assertIn('key="bp_sanear_hist_ini"', codigo)
        self.assertIn("resumo_mensal_sanear", codigo)
        self.assertIn("historico_medicoes_sanear.csv", codigo)
        self.assertIn("if not eh_operador:", codigo)


if __name__ == "__main__":
    unittest.main()
