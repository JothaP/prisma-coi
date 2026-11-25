"""Regressões da pesquisa por planta/logger e dos pontos de matrícula da área."""
import ast
from html import escape as html_escape
import math
from pathlib import Path
import re
import sqlite3
import unittest

import folium
from folium.plugins import Draw, Fullscreen
from branca.element import Element
import pandas as pd
from shapely.geometry import box

from m8_consulta_ampliada import (
    LIMITE_PONTOS_POLIGONO,
    buscar_equipamentos, interpretar_termos_equipamentos,
    pontos_matriculas_poligono,
)

RAIZ = Path(__file__).resolve().parents[1]
FONTE = RAIZ / "pages" / "8_Consulta_Operacional.py"
CODIGO = FONTE.read_text(encoding="utf-8")
ARVORE = ast.parse(CODIGO, filename=str(FONTE))
NOMES = {
    "_popup_cliente", "_popup_ativo", "_m8_formatar_cota",
    "_m8_rótulo_html", "_m8_adicionar_rotulo", "_m8_mapa_unificado",
    "_m8_normalizar_pontos",
}
ACHADAS = [
    no for no in ARVORE.body
    if isinstance(no, ast.FunctionDef) and no.name in NOMES
]
assert {no.name for no in ACHADAS} == NOMES
AMBIENTE = {
    "folium": folium, "Draw": Draw, "Fullscreen": Fullscreen,
    "Element": Element, "pd": pd, "html_escape": html_escape,
    "math": math, "re": re, "LIMITE_EQUIPAMENTOS_MAPA": 350,
}
exec(compile(ast.Module(body=ACHADAS, type_ignores=[]), str(FONTE), "exec"), AMBIENTE)


def _coords(valor, tipo):
    try:
        x = float(str(valor).replace(",", "."))
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def _bases():
    poc = pd.DataFrame([
        {
            "ID_POCO": "", "IDENTIFICACAO_ATIVO": "PL-THE-PCO0356",
            "NOME_POCO": "THE-148", "MUNICIPIO": "TERESINA",
            "_lat": -5.09, "_lon": -42.80, "_tipo": "Poço / Ativo",
            "_nome": "THE-148",
        },
        {
            "ID_POCO": "", "IDENTIFICACAO_ATIVO": "PL-THE-PCO0357",
            "NOME_POCO": "THE-149", "MUNICIPIO": "TERESINA",
            "_lat": -5.10, "_lon": -42.81, "_tipo": "Poço / Ativo",
            "_nome": "THE-149",
        },
    ])
    log = pd.DataFrame([
        {
            "ID_LOGGER": "", "IDENTIFICACAO_ATIVO": "FFDA78",
            "ENDERECO": "Rua A", "MUNICIPIO": "TERESINA",
            "_lat": -5.08, "_lon": -42.82, "_tipo": "Logger",
            "_nome": "Rua A",
        }
    ])
    return {"Poço / Ativo": poc, "Logger": log}


class TestarPesquisaEquipamentosModulo8(unittest.TestCase):
    def test_codigo_planta_exato_hifen_ou_espaco(self):
        for termo in ("PL-THE-PCO0356", "pl the pco0356", "PLTHEPCO0356"):
            with self.subTest(termo=termo):
                encontrados, faltantes, excedentes = buscar_equipamentos(
                    _bases(), "Poço / Ativo", [termo]
                )
                self.assertEqual(len(encontrados), 1)
                self.assertEqual(encontrados[0]["IDENTIFICACAO_ATIVO"], "PL-THE-PCO0356")
                self.assertEqual(faltantes, [])
                self.assertEqual(excedentes, 0)

    def test_nome_da_planta_e_inicio_do_codigo(self):
        for termo in ("THE-148", "PL-THE-PCO035"):
            encontrados, _, _ = buscar_equipamentos(
                _bases(), "Poço / Ativo", [termo]
            )
            self.assertGreaterEqual(len(encontrados), 1)
            self.assertTrue(all(x["_tipo"] == "Poço / Ativo" for x in encontrados))

    def test_logger_exato_e_sem_misturar_com_ativos(self):
        encontrados, faltantes, _ = buscar_equipamentos(
            _bases(), "Logger", ["ffda78", "PL-THE-PCO0356"]
        )
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0]["IDENTIFICACAO_ATIVO"], "FFDA78")
        self.assertEqual(faltantes, ["PL-THE-PCO0356"])

    def test_lista_multipla_separadores_e_limite_de_consulta(self):
        termos = interpretar_termos_equipamentos(
            "FFDA78,\nFFDA79; ffda78"
        )
        self.assertEqual(termos, ["FFDA78", "FFDA79"])
        with self.assertRaisesRegex(ValueError, "20"):
            interpretar_termos_equipamentos(
                "\n".join(f"FFDA{i:02d}" for i in range(21))
            )
        encontrados, faltantes, excesso = buscar_equipamentos(
            _bases(), "Poço / Ativo", ["PL-THE-PCO035"], limite=1
        )
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(excesso, 1)
        self.assertFalse(faltantes)

    def test_tipo_invalido_falha_sem_buscar_todas_as_bases(self):
        with self.assertRaises(ValueError):
            buscar_equipamentos(_bases(), "Tudo", ["FFDA78"])


class TestarPontosMatriculasPoligono(unittest.TestCase):
    def setUp(self):
        self.tabela = pd.DataFrame([
            {
                "NUM_LIGACAO": "001234", "BAIRRO": "Dirceu II",
                "END_LIGACAO": "Rua A",
                "COD_LATITUDE": "-5,09000", "COD_LONGITUDE": "-42,8000",
            },
            {
                "NUM_LIGACAO": "001235", "BAIRRO": "Dirceu II",
                "END_LIGACAO": "Rua B",
                "COD_LATITUDE": "-5.091", "COD_LONGITUDE": "-42.801",
            },
            {
                "NUM_LIGACAO": "001234", "BAIRRO": "Dirceu II",
                "END_LIGACAO": "Rua A",
                "COD_LATITUDE": "-5,09000", "COD_LONGITUDE": "-42,8000",
            },
            {
                "NUM_LIGACAO": "009999", "BAIRRO": "Dirceu II",
                "END_LIGACAO": "Rua C",
                "COD_LATITUDE": "sem coordenada", "COD_LONGITUDE": "-42.800",
            },
        ])

    def test_pontos_area_dedup_validacao_e_zeros_esquerda(self):
        pontos, total = pontos_matriculas_poligono(self.tabela, _coords)
        self.assertEqual(total, 2)
        self.assertEqual(pontos["NUM_LIGACAO"].tolist(), ["001234", "001235"])
        self.assertAlmostEqual(pontos.iloc[0]["_lat"], -5.09)

    def test_limite_nao_apaga_cadastro_original(self):
        pontos, total = pontos_matriculas_poligono(self.tabela, _coords, limite=1)
        self.assertEqual(len(pontos), 1)
        self.assertEqual(total, 2)
        self.assertEqual(len(self.tabela), 4)
        self.assertEqual(LIMITE_PONTOS_POLIGONO, 2500)

    def test_mapa_exibe_pontos_da_area_com_tooltips_matricula(self):
        clientes = pd.DataFrame(
            columns=["NUM_LIGACAO", "END_LIGACAO", "_lat", "_lon", "_influencia"]
        )
        pontos, _ = pontos_matriculas_poligono(self.tabela, _coords)
        mapa = AMBIENTE["_m8_mapa_unificado"](
            clientes, [], 750, [], [], box(-42.81, -5.10, -42.79, -5.08),
            None, {}, exibir_rotulos=False, matriculas_area=pontos,
        )
        html = mapa.get_root().render()
        camadas = [
            c for c in mapa._children.values()
            if isinstance(c, folium.plugins.MarkerCluster)
        ]
        self.assertEqual(len(camadas), 1)
        self.assertEqual(camadas[0].layer_name, "Matrículas dentro do polígono")
        self.assertIn("001234", html)
        self.assertIn("001235", html)
        self.assertIn("markerClusterGroup", html)
        self.assertNotIn("Matrículas consultadas", html)
        markers = [
            c for c in camadas[0]._children.values()
            if isinstance(c, folium.Marker)
        ]
        self.assertEqual(len(markers), 2)
        self.assertTrue(all(
            any(isinstance(child, folium.Tooltip)
                for child in marker._children.values())
            for marker in markers
        ))

    def test_busca_logger_gera_marcador_e_foco_sem_matricula(self):
        clientes = pd.DataFrame(
            columns=["NUM_LIGACAO", "END_LIGACAO", "_lat", "_lon", "_influencia"]
        )
        ativos, _, _ = buscar_equipamentos(_bases(), "Logger", ["FFDA78"])
        mapa = AMBIENTE["_m8_mapa_unificado"](
            clientes, ativos, 750, [], [], None, None, {},
            exibir_rotulos=False, foco_equipamentos=ativos,
        )
        self.assertEqual(mapa.location, [-5.08, -42.82])
        self.assertIn("FFDA78", mapa.get_root().render())
        self.assertNotIn("Matrícula: FFDA78", mapa.get_root().render())

    def test_busca_ativo_gera_marcador_e_foco_sem_matricula(self):
        clientes = pd.DataFrame(
            columns=["NUM_LIGACAO", "END_LIGACAO", "_lat", "_lon", "_influencia"]
        )
        ativos, _, _ = buscar_equipamentos(
            _bases(), "Poço / Ativo", ["PL-THE-PCO0356"]
        )
        mapa = AMBIENTE["_m8_mapa_unificado"](
            clientes, ativos, 750, [], [], None, None, {},
            exibir_rotulos=False, foco_equipamentos=ativos,
        )
        self.assertEqual(mapa.location, [-5.09, -42.80])
        self.assertIn("PL-THE-PCO0356", mapa.get_root().render())

    def test_texto_interface_integrado_e_consulta_sql_preservada(self):
        self.assertIn('["Matrícula", "Planta de ativo", "Logger"]', CODIGO)
        self.assertIn('m8_consulta_equipamento', CODIGO)
        self.assertIn("buscar_equipamentos(", CODIGO)
        self.assertIn("pontos_matriculas_poligono(", CODIGO)
        self.assertIn("foco_equipamentos=ativos_pesquisados", CODIGO)
        self.assertIn("matriculas_area=pontos_area", CODIGO)
        self.assertIn("_consultar_cadastro_no_poligono", CODIGO)
        self.assertIn("FROM matriculas", CODIGO)
        self.assertIn("st.session_state.pop(\"m8_integrado_rede_id\", None)", CODIGO)
        self.assertIn("name=\"Rede de água — recorte\"", CODIGO)


if __name__ == "__main__":
    unittest.main()
