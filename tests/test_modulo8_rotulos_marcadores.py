"""Teste isolado do mapa Folium do Módulo 8: marcadores independentes dos rótulos."""
import ast
from html import escape as html_escape
import math
from pathlib import Path
import unittest

import folium
from folium.plugins import Draw, Fullscreen
from branca.element import Element
import pandas as pd

source = Path(__file__).resolve().parents[1] / "pages" / "8_Consulta_Operacional.py"
tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
names = {
    "_popup_cliente", "_popup_ativo", "_m8_formatar_cota",
    "_m8_rótulo_html", "_m8_adicionar_rotulo", "_m8_mapa_unificado",
}
found = [
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name in names
]
assert {node.name for node in found} == names
namespace = {
    "folium": folium, "Draw": Draw, "Fullscreen": Fullscreen,
    "Element": Element, "pd": pd, "html_escape": html_escape, "math": math,
    "LIMITE_EQUIPAMENTOS_MAPA": 350,
}
exec(compile(ast.Module(body=found, type_ignores=[]), str(source), "exec"), namespace)


def todos_grupos(mapa):
    return [v for v in mapa._children.values() if isinstance(v, folium.FeatureGroup)]


def marcadores(grupo):
    return [v for v in grupo._children.values() if type(v) is folium.Marker]


class TestarRotulosModulo8(unittest.TestCase):
    def setUp(self):
        self.clientes = pd.DataFrame([{
            "_lat": -5.08, "_lon": -42.80,
            "NUM_LIGACAO": "12001066",
            "END_LIGACAO": "Rua São João",
            "_influencia": "Área operacional",
        }])
        self.ativos = [{
            "_lat": -5.09, "_lon": -42.80,
            "_tipo": "Logger", "IDENTIFICACAO_ATIVO": "FFDA78",
            "_nome": "Logger de teste",
        }]
        self.cotas = {(-5.08, -42.80): 123.8, (-5.09, -42.80): 110.3}

    def mapa(self, exibir):
        return namespace["_m8_mapa_unificado"](
            self.clientes, self.ativos, 750, [], [], None, None,
            self.cotas, exibir_rotulos=exibir
        )

    def test_com_rotulos_desligados_matricula_continua_em_marcador(self):
        mapa = self.mapa(False)
        grupos = {v.layer_name: v for v in todos_grupos(mapa)}
        self.assertIn("Matrículas consultadas", grupos)
        self.assertIn("Logger(s)", grupos)
        self.assertNotIn("Rótulos fixos", grupos)
        pins = marcadores(grupos["Matrículas consultadas"])
        self.assertEqual(len(pins), 1)
        self.assertTrue(any(isinstance(x, folium.Tooltip) for x in pins[0]._children.values()))
        rendered = mapa.get_root().render()
        self.assertIn("Matrícula: 12001066", rendered)
        self.assertIn("Elevação: 124 m", rendered)
        self.assertIn("FFDA78", rendered)

    def test_rotulos_ligados_mantem_os_mesmos_marcadores(self):
        on, off = self.mapa(True), self.mapa(False)
        groups_on = {v.layer_name: v for v in todos_grupos(on)}
        groups_off = {v.layer_name: v for v in todos_grupos(off)}
        self.assertEqual(len(marcadores(groups_on["Matrículas consultadas"])),
                         len(marcadores(groups_off["Matrículas consultadas"])))
        self.assertEqual(len(marcadores(groups_on["Logger(s)"])),
                         len(marcadores(groups_off["Logger(s)"])))
        self.assertIn("Rótulos fixos", groups_on)
        self.assertGreater(len(marcadores(groups_on["Rótulos fixos"])), 0)
        self.assertNotIn("Rótulos fixos", groups_off)
        self.assertIn("Matrícula: 12001066", on.get_root().render())

    def test_rotulos_nao_substituem_clique_ou_tooltip(self):
        for flag in (True, False):
            grupos = {v.layer_name: v for v in todos_grupos(self.mapa(flag))}
            pin = marcadores(grupos["Matrículas consultadas"])[0]
            self.assertTrue(any(isinstance(child, folium.Popup)
                                for child in pin._children.values()))
            self.assertTrue(any(isinstance(child, folium.Tooltip)
                                for child in pin._children.values()))

    def test_toggle_recria_o_mapa_sem_alterar_a_consulta(self):
        raw = source.read_text(encoding="utf-8")
        self.assertIn('f"_rotulos_{int(rotulos)}"', raw)
        self.assertIn('key=chave_mapa', raw)


if __name__ == "__main__":
    unittest.main()
