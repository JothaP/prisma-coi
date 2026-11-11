"""Testes isolados: mesclagem de concentrações por mesmo evento no Ambiente de Teste."""
import ast
from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import json
import math
import re
import sqlite3
import unittest
import unicodedata
from zoneinfo import ZoneInfo

import folium
import pandas as pd
from pyproj import CRS, Transformer
from shapely import wkb
from shapely.geometry import Point, Polygon, mapping
from shapely.ops import transform, unary_union

RAIZ = Path(__file__).resolve().parents[1]
FONTE = RAIZ / "farol_mesclagem_teste.py"
AST = ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))
PUROS = {
    "normalizar", "nome_area", "nomes_areas_evento", "evento_municipal",
    "parse_horario", "chave_evento", "caminho_gpkg", "ler_gpkg_evento",
    "relacao_concentracoes_evento", "unir_grupos_os", "mesclagem_valida",
    "mapa_mesclagem",
}
funcoes = [n for n in AST.body
           if isinstance(n, ast.FunctionDef) and n.name in PUROS]
assert {n.name for n in funcoes} == PUROS
for n in funcoes:
    n.decorator_list = []
globals_teste = {
    "date": date, "datetime": datetime, "timedelta": timedelta,
    "Path": Path, "hashlib": hashlib, "json": json, "math": math,
    "re": re, "sqlite3": sqlite3, "unicodedata": unicodedata,
    "ZoneInfo": ZoneInfo, "folium": folium, "pd": pd, "CRS": CRS,
    "Transformer": Transformer, "wkb": wkb, "Point": Point,
    "mapping": mapping, "transform": transform, "unary_union": unary_union,
    "PASTA_GEO": RAIZ / "geodados" / "municipios",
    "FUSO": ZoneInfo("America/Fortaleza"), "RAIO_M": 750,
}
exec(compile(ast.Module(body=funcoes, type_ignores=[]), str(FONTE), "exec"), globals_teste)
def f(nome): return globals_teste[nome]


class MesclagemTestes(unittest.TestCase):
    def setUp(self):
        self.evento = {
            "Cidade": "Teresina", "Protocolo": "EVT-1",
            "Início": "11/11/2026 10:00", "Término Real": "11/11/2026 13:00",
            "Status": "Concluído", "Áreas Impactadas": "Bairro Alfa",
            "Serviço": "Manutenção elétrica", "Código do Ativo": "P-100",
        }
        self.concs = pd.DataFrame([
            {"_grupo": 0,"Concentração":"THE C001","O.S.":3,
             "Latitude":-5.100,"Longitude":-42.800,"Bairros":"Bairro errado"},
            {"_grupo": 1,"Concentração":"THE C002","O.S.":3,
             "Latitude":-5.130,"Longitude":-42.800,"Bairros":"Alfa"},
            {"_grupo": 2,"Concentração":"THE C003","O.S.":3,
             "Latitude":-5.180,"Longitude":-42.800,"Bairros":"Alfa"},
        ])
        linhas=[]
        for grupo, lat, hora, cidade, bairro in [
            (0,-5.100,"11/11/2026 09:30","Teresina","Desconhecido"),
            (1,-5.130,"11/11/2026 14:30","Teresina","Alfa"),
            (2,-5.180,"11/11/2026 09:30","Teresina","Alfa"),
        ]:
            for i in range(3):
                linhas.append({
                    "_grupo":grupo, "_lat":lat+0.0001*i, "_lon":-42.800,
                    "Nº da O.S":str(grupo*10+i), "Matrícula":str(100+i),
                    "Dt. Emissão":hora, "Cidade":cidade,"Bairro":bairro,
                })
        self.os = pd.DataFrame(linhas)
        self.geos = {"ALFA":[Polygon([
            (-42.81,-5.14),(-42.79,-5.14),(-42.79,-5.09),(-42.81,-5.09),
        ])]}

    def candidatas(self, evento=None, geos=None, os=None):
        return f("relacao_concentracoes_evento")(
            evento if evento is not None else self.evento,
            self.concs,os if os is not None else self.os,
            geos if geos is not None else self.geos,
            agora=datetime(2026,11,11,15),
        )

    def test_dois_grupos_em_areas_mesmo_evento(self):
        candidatos=self.candidatas()
        self.assertEqual([x["codigo"] for x in candidatos],
                         ["THE C001","THE C002"])
        self.assertEqual(candidatos[0]["vinculadas"],3)

    def test_geometria_prevalece_sobre_nome_bairro(self):
        self.assertEqual(len(self.candidatas()),2)
        # Grupo C003 tem nome Alfa, mas as coordenadas estão fora do polígono.
        self.assertNotIn("THE C003",[x["codigo"] for x in self.candidatas()])

    def test_bairro_sem_poligono_tem_fallback(self):
        candidatos=self.candidatas(geos={})
        self.assertEqual([x["codigo"] for x in candidatos],
                         ["THE C002","THE C003"])

    def test_limite_temporal_uma_hora_antes_tres_depois(self):
        dados=self.os.copy()
        dados.loc[dados["_grupo"]==0,"Dt. Emissão"]="11/11/2026 08:59"
        dados.loc[dados["_grupo"]==1,"Dt. Emissão"]="11/11/2026 16:01"
        self.assertEqual(self.candidatas(os=dados),[])
        dados.loc[dados["_grupo"]==0,"Dt. Emissão"]="11/11/2026 09:00"
        dados.loc[dados["_grupo"]==1,"Dt. Emissão"]="11/11/2026 16:00"
        self.assertEqual(len(self.candidatas(os=dados)),2)

    def test_evento_com_inicio_sem_hora_nao_cria_vinculo(self):
        self.assertEqual(self.candidatas(evento={**self.evento, "Início":"11/11/2026"}),[])

    def test_municipio_divergente_nao_vincula(self):
        os=self.os.copy()
        os["Cidade"]="Timon"
        self.assertEqual(self.candidatas(os=os),[])

    def test_concentracoes_selecionadas_validadas(self):
        candidatas=self.candidatas()
        self.assertTrue(f("mesclagem_valida")(candidatas,["THE C001","THE C002"]))
        self.assertFalse(f("mesclagem_valida")(candidatas,["THE C001"]))
        self.assertFalse(f("mesclagem_valida")(candidatas,["THE C001","THE C001"]))
        self.assertFalse(f("mesclagem_valida")(candidatas,["THE C001","THE C003"]))

    def test_reune_os_deduplicando_identidade(self):
        duplicada=pd.concat([self.os,self.os.iloc[[0]]],ignore_index=True)
        unidos=f("unir_grupos_os")(duplicada,[0,1])
        self.assertEqual(len(unidos),6)
        self.assertEqual(set(unidos["_grupo"]),{0,1})

    def test_uma_feicao_mesclada_sem_novo_raio_artificial(self):
        candidatas=self.candidatas()
        unidos=f("unir_grupos_os")(self.os,[0,1])
        mapa=f("mapa_mesclagem")(
            candidatas,["THE C001","THE C002"],
            unidos,self.geos,self.evento
        )
        self.assertIsInstance(mapa,folium.Map)
        geo=[x for x in mapa._children.values() if isinstance(x,folium.GeoJson)]
        self.assertEqual(len(geo),2)  # áreas do evento + união das coberturas

    def test_chave_evento_independente_de_status(self):
        base=f("chave_evento")(self.evento)
        alt={**self.evento, "Status":"Em andamento",
             "Áreas Impactadas":"Bairro Alfa, Novo bairro"}
        self.assertEqual(base,f("chave_evento")(alt))
        self.assertNotEqual(base,f("chave_evento")({**self.evento,"Início":"12/11/2026 10:00"}))

    def test_ler_gpkg_real_novas_subareas(self):
        arquivo=f("caminho_gpkg")("Teresina")
        self.assertIsNotNone(arquivo)
        st=arquivo.stat()
        nomes=f("ler_gpkg_evento")((str(arquivo),st.st_size,st.st_mtime_ns))
        self.assertIn("VILA INGLESA",nomes)
        self.assertIn("TORQUATO NETO",nomes)
        self.assertGreater(len(nomes),130)

    def test_ambiente_isolado_de_producao(self):
        codigo=(RAIZ/"pages"/"Ambiente_de_Teste.py").read_text(encoding="utf-8")
        helper=FONTE.read_text(encoding="utf-8")
        self.assertIn("renderizar_mesclagem_teste(",codigo)
        self.assertIn("st.stop()",codigo[codigo.index('modo_teste = st.radio('):])
        self.assertNotIn("farol_acomp_supabase_backend",helper)
        self.assertNotIn("integracao_acompanhamento_producao",helper)
        self.assertNotIn("append_row(",helper)
        self.assertNotIn("update_row(",helper)
        self.assertNotIn("supabase",helper.lower().split("def renderizar_mesclagem_teste")[0])


if __name__=="__main__":
    unittest.main()
