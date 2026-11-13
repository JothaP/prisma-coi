"""Regressão da leitura e renderização de GeoPackages no Farol e Consulta.

Não importa os módulos Streamlit em execução nem acessa planilhas/bancos.
Usa o GPKG de Teresina versionado no repositório e um GPKG sintético
com furos e atributos vazios.
"""
import ast
from html import escape as html_escape
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

import folium
from folium.plugins import Fullscreen, Draw
from branca.element import Element
import pandas as pd
from pyproj import CRS, Transformer
from shapely import wkb
from shapely.geometry import Polygon
from shapely.ops import transform as transformar_geometria


RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO = RAIZ / "geodados" / "municipios" / "NM_MUN_Teresina.gpkg"
FONTE3 = RAIZ / "pages" / "3_Farol_Operacional.py"
FONTE8 = RAIZ / "pages" / "8_Consulta_Operacional.py"


def carregar_funcoes(caminho, nomes, globais):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    selecionadas = [
        node for node in arvore.body
        if isinstance(node, ast.FunctionDef) and node.name in nomes
    ]
    assert {node.name for node in selecionadas} == nomes, f"Funções não encontradas em {caminho}"
    for node in selecionadas:
        node.decorator_list = []
    exec(compile(ast.Module(body=selecionadas, type_ignores=[]), str(caminho), "exec"), globais)
    return globais


g3 = carregar_funcoes(FONTE3, {
    "normalizar_texto", "normalizar_cabecalho", "limpar_nome_bairro",
    "normalizar_municipio", "nome_municipio_arquivo", "carregar_gpkg",
    "preparar_poligonos_para_mapa", "adicionar_limites_bairros",
    "ponto_em_poligono", "ponto_em_bairro", "identificar_bairro_por_ponto",
    "indice_nomes_bairros", "nomes_reconhecidos_feicao",
}, {
    "sqlite3": sqlite3, "math": math, "re": re,
    "unicodedata": unicodedata, "wkb": wkb,
    "CRS": CRS, "Transformer": Transformer,
    "transformar_geometria": transformar_geometria, "folium": folium,
    "os": __import__("os"),
})

g8 = carregar_funcoes(FONTE8, {
    "_normalizar_texto", "_normalizar_cabecalho", "_assinatura",
    "_arquivos_bairros_municipais", "_assinaturas_bairros_municipais",
    "_bairros_geograficos",
    "_m8_mapa_unificado",
}, {
    "sqlite3": sqlite3, "math": math, "re": re, "unicodedata": unicodedata,
    "Path": Path, "RAIZ": RAIZ,
    "PASTA_BAIRROS": RAIZ / "geodados" / "municipios",
    "folium": folium, "wkb": wkb, "CRS": CRS, "Transformer": Transformer,
    "pd": pd, "ET": ET, "zipfile": zipfile, "Element": Element,
    "Draw": Draw, "Fullscreen": Fullscreen,
    "LIMITE_EQUIPAMENTOS_MAPA": 350, "html_escape": html_escape,
})


def gerar_gpkg_sintetico(caminho):
    """Duas geometrias: uma com furo, outra com NM_BAIRRO ausente."""
    con = sqlite3.connect(caminho)
    con.executescript("""
        CREATE TABLE gpkg_contents(
            table_name text, data_type text, srs_id integer);
        CREATE TABLE gpkg_geometry_columns(
            table_name text, column_name text, srs_id integer);
        CREATE TABLE gpkg_spatial_ref_sys(
            srs_id integer, organization text,
            organization_coordsys_id integer, definition text);
        CREATE TABLE regioes(
            geom blob, NM_BAIRRO text, NM_MUN text, name text);
        INSERT INTO gpkg_contents VALUES ('regioes','features',4326);
        INSERT INTO gpkg_geometry_columns VALUES ('regioes','geom',4326);
        INSERT INTO gpkg_spatial_ref_sys
            VALUES (4326,'EPSG',4326,'');
    """)
    polygon = Polygon(
        [(-42.81,-5.11),(-42.79,-5.11),(-42.79,-5.09),(-42.81,-5.09)],
        holes=[[(-42.805,-5.105),(-42.795,-5.105),
                (-42.795,-5.095),(-42.805,-5.095)]],
    )
    secondary = Polygon([
        (-42.84,-5.13),(-42.82,-5.13),(-42.82,-5.11),(-42.84,-5.11)
    ])
    for geo, nome, aux in [
        (polygon, "Região de Teste", ""),
        (secondary, None, "Novo elemento"),
    ]:
        # Cabeçalho padrão GPKG: "GP", versão 0, little endian, EPSG:4326.
        blob = b"GP\x00\x01" + (4326).to_bytes(4, "little") + geo.wkb
        con.execute(
            "INSERT INTO regioes(geom,NM_BAIRRO,NM_MUN,name) VALUES (?,?,?,?)",
            (blob, nome, "Teresina", aux),
        )
    con.commit()
    con.close()


class LeituraGpkgTeresina(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m3 = g3["carregar_gpkg"](str(ARQUIVO))
        cls.m8 = g8["_bairros_geograficos"](g8["_assinatura"](ARQUIVO))
        with sqlite3.connect(ARQUIVO) as conexao:
            cls.esperado = conexao.execute(
                'SELECT COUNT(*) FROM "NM_MUN_Teresina" WHERE geom IS NOT NULL'
            ).fetchone()[0]

    def test_todos_gpkg_reconhecidos_sem_incluir_backup(self):
        encontrados = g8["_arquivos_bairros_municipais"]()
        self.assertIn(ARQUIVO.resolve(), {p.resolve() for p in encontrados})
        self.assertGreater(len(encontrados), 1)
        self.assertTrue(all(p.suffix.lower() == ".gpkg" for p in encontrados))
        self.assertTrue(all(not p.name.lower().endswith(".bak") for p in encontrados))
        assinaturas = g8["_assinaturas_bairros_municipais"]()
        self.assertEqual(len(assinaturas), len(encontrados))

    def test_modulo_3_carrega_todas_as_geometrias(self):
        self.assertGreater(self.esperado, 123)  # há novas subáreas no arquivo atual
        self.assertEqual(sum(len(b["poligonos"]) for b in self.m3), self.esperado)
        self.assertTrue(all(b["municipio"] == "TERESINA" for b in self.m3))

    def test_modulo_8_carrega_todas_as_geometrias(self):
        self.assertEqual(len(self.m8), self.esperado)
        self.assertTrue(all(not b["geometria"].is_empty for b in self.m8))

    def test_subarea_da_exportacao_preservada(self):
        self.assertIn("Vila Inglesa", {b["nome"] for b in self.m3})
        self.assertIn("Vila Inglesa", {b["nome"] for b in self.m8})

    def test_subarea_do_campo_name_e_bairro_pai_identificados(self):
        with tempfile.TemporaryDirectory() as diretorio:
            arquivo = Path(diretorio) / "NM_MUN_Teresina.gpkg"
            gerar_gpkg_sintetico(arquivo)
            pol = Polygon([
                (-42.84, -5.13), (-42.82, -5.13),
                (-42.82, -5.11), (-42.84, -5.11),
            ])
            blob = b"GP\\x00\\x01" + (4326).to_bytes(4, "little") + pol.wkb
            with sqlite3.connect(arquivo) as conexao:
                conexao.execute(
                    "INSERT INTO regioes(geom,NM_BAIRRO,NM_MUN,name) VALUES (?,?,?,?)",
                    (blob, "Bairro Pai", "Teresina", "Subárea Nova"),
                )
                conexao.commit()
            registros = g3["carregar_gpkg"](str(arquivo))
        entrada = next(x for x in registros if x["nome"] == "Subárea Nova")
        self.assertEqual(entrada["tipo_area"], "Subárea")
        self.assertIn("BAIRRO PAI", g3["nomes_reconhecidos_feicao"](entrada))
        self.assertIn("SUBAREA NOVA", g3["nomes_reconhecidos_feicao"](entrada))

    def test_modulo_3_geojson_contem_todas_as_feicoes(self):
        regs = g3["preparar_poligonos_para_mapa"](self.m3)
        mapa = folium.Map(location=[-5.09, -42.8])
        g3["adicionar_limites_bairros"](mapa, regs)
        grupos = [
            child for child in mapa._children.values()
            if isinstance(child, folium.FeatureGroup) and child.layer_name == "Limites dos bairros"
        ]
        self.assertEqual(len(grupos), 1)
        geo = [x for x in grupos[0]._children.values() if isinstance(x, folium.GeoJson)]
        self.assertEqual(len(geo), 1)
        self.assertEqual(len(geo[0].data["features"]), self.esperado)

    def test_modulo_8_geojson_contem_todas_as_feicoes(self):
        # Sem matrículas pesquisadas, o mapa mostra todos os bairros cadastrados.
        clientes = pd.DataFrame(columns=["_lat", "_lon"])
        mapa = g8["_m8_mapa_unificado"](
            clientes, [], 750, self.m8, [], None, None, {}, False
        )
        grupos = [
            child for child in mapa._children.values()
            if isinstance(child, folium.FeatureGroup) and child.layer_name == "Limites dos bairros"
        ]
        self.assertEqual(len(grupos), 1)
        geo = [x for x in grupos[0]._children.values() if isinstance(x, folium.GeoJson)]
        self.assertEqual(len(geo), 1)
        self.assertEqual(len(geo[0].data["features"]), self.esperado)

    def test_modulo_8_sem_corte_de_90(self):
        codigo = FONTE8.read_text(encoding="utf-8")
        self.assertNotIn('bairros = [b for b in todas if not (', codigo)
        self.assertIn("bairros = todas", codigo)

    def test_furos_e_geometrias_sem_atributos(self):
        with tempfile.TemporaryDirectory() as diretorio:
            arquivo = Path(diretorio) / "NM_MUN_Teresina.gpkg"
            gerar_gpkg_sintetico(arquivo)
            m3 = g3["carregar_gpkg"](str(arquivo))
            m8 = g8["_bairros_geograficos"](g8["_assinatura"](arquivo))
        self.assertEqual(len(m3), 2)
        self.assertEqual(len(m8), 2)
        self.assertTrue(m3[1]["sem_nome"])
        self.assertTrue(m3[1]["nome"].startswith("Área sem nome"))
        self.assertTrue(m8[1]["nome"].startswith("Área sem nome"))
        self.assertEqual(len(m3[0]["aneis_por_poligono"][0]), 2)
        self.assertEqual(len(m8[0]["geometria_mapa"].interiors), 1)
        # Dentro do exterior, mas dentro do furo => fora da área.
        self.assertFalse(g3["ponto_em_bairro"](-5.10, -42.8, m3[0]))
        self.assertTrue(g3["ponto_em_bairro"](-5.109, -42.809, m3[0]))
        self.assertNotIn(
            "Área sem nome", g3["indice_nomes_bairros"](m3, "TERESINA")
        )


if __name__ == "__main__":
    unittest.main()
