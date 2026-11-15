"""Testes sem acesso a credenciais, Streamlit ou bases operacionais."""
import ast
from io import BytesIO
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

from shapely.geometry import shape, mapping

ROOT = Path(__file__).resolve().parents[1]
FONTE = ROOT / "investigacao_areas_teste.py"


def carregar_funcoes(*nomes):
    arvore = ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))
    definos = [node for node in arvore.body
               if isinstance(node, ast.FunctionDef) and node.name in nomes]
    for node in definos:
        node.decorator_list = []
    ambiente = {
        "shape": shape, "mapping": mapping, "ET": ET,
        "BytesIO": BytesIO, "zipfile": zipfile,
    }
    codigo = compile(ast.Module(body=definos, type_ignores=[]), str(FONTE), "exec")
    exec(codigo, ambiente)
    return ambiente


class TestInvestigacaoAreas(unittest.TestCase):
    def setUp(self):
        fn = carregar_funcoes("_poligono_desenhado", "_kmz_poligonos")
        self.validar = fn["_poligono_desenhado"]
        self.gerar = fn["_kmz_poligonos"]

    def test_poligono_valido_e_kmz_roundtrip(self):
        geometria = {"type": "Polygon", "coordinates": [[
            [-42.80, -5.10], [-42.79, -5.10],
            [-42.79, -5.09], [-42.80, -5.09], [-42.80, -5.10],
        ]]}
        area = self.validar({"type": "Feature", "geometry": geometria})
        self.assertIsNotNone(area)
        kmz = self.gerar([area, area], "Investigação A", "THE-075")
        with zipfile.ZipFile(BytesIO(kmz)) as pacote:
            self.assertEqual(pacote.namelist(), ["doc.kml"])
            root = ET.fromstring(pacote.read("doc.kml"))
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        areas = root.findall(".//k:Placemark", ns)
        self.assertEqual(len(areas), 2)
        coords = areas[0].find(".//k:coordinates", ns).text
        self.assertTrue(coords.startswith("-42.800000000,-5.100000000,0"))

    def test_recusa_desenhos_invalidos(self):
        self.assertIsNone(self.validar({"geometry": {
            "type": "LineString", "coordinates": [[-42.8, -5.1], [-42.7, -5.1]]
        }}))
        self.assertIsNone(self.validar({"geometry": {
            "type": "Polygon", "coordinates": [[
                [-42.80, -5.10], [-42.79, -5.09],
                [-42.80, -5.09], [-42.79, -5.10], [-42.80, -5.10]
            ]]
        }}))


if __name__ == "__main__":
    unittest.main()
