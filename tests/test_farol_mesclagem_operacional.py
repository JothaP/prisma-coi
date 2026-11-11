"""Regressão da mesclagem por evento no Módulo 3: geometrias e fichas."""
import ast
import hashlib
import json
import math
import re
import unittest
import unicodedata
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from shapely.geometry import Polygon, Point, mapping
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"farol_mesclagem_operacional.py"
ARVORE=ast.parse(SRC.read_text(encoding="utf-8"), filename=str(SRC))
FUNCOES={
    "normalizar","chave_evento","chave_acompanhamento_mesclado",
    "validar_poligono","extrair_desenho_folium","contagem_no_poligono",
    "aplicar_mesclas","eventos_e_concentracoes",
}
sel=[n for n in ARVORE.body if isinstance(n,ast.FunctionDef) and n.name in FUNCOES]
assert {n.name for n in sel}==FUNCOES
ns={
    "re":re,"unicodedata":unicodedata,"md5":hashlib.md5,
    "json":json,"math":math,"Point":Point,"shape":__import__("shapely.geometry",fromlist=["shape"]).shape,
    "mapping":mapping,"pd":pd,
}
exec(compile(ast.Module(body=sel,type_ignores=[]),str(SRC),"exec"),ns)

def func(nome):return ns[nome]

def amostra():
    c=pd.DataFrame([
        {"ID_CONCENTRACAO":"THE C001","GRUPO":0,"LATITUDE":-5.100,"LONGITUDE":-42.8,
         "QTD_OS":3,"QTD_MATRICULAS":3,"BAIRROS":"Itararé"},
        {"ID_CONCENTRACAO":"THE C002","GRUPO":1,"LATITUDE":-5.112,"LONGITUDE":-42.8,
         "QTD_OS":3,"QTD_MATRICULAS":3,"BAIRROS":"Novo Horizonte"},
        {"ID_CONCENTRACAO":"THE C003","GRUPO":2,"LATITUDE":-5.400,"LONGITUDE":-42.8,
         "QTD_OS":3,"QTD_MATRICULAS":3,"BAIRROS":"Centro"},
    ])
    ponto=[]
    for grupo,lat,cidade in ((0,-5.100,"Teresina"),(1,-5.112,"Teresina"),(2,-5.400,"Teresina")):
        for i in range(3):
            ponto.append({"_grupo":grupo,"Cidade":cidade,"Latitude":lat+0.0001*i,
                           "Longitude":-42.8,"Nº da O.S":str(10*grupo+i),
                           "Matrícula":str(100+grupo*3+i),"Bairro":"Teste"})
    return c,pd.DataFrame(ponto)

def poligono():
    return mapping(Polygon([(-42.82,-5.12),(-42.78,-5.12),
                            (-42.78,-5.08),(-42.82,-5.08)]))

def mescla(geo=None):
    return {
        "evento_chave":"a"*32,"municipio":"TERESINA",
        "codigo_mescla":"THE M000123",
        "chave_acompanhamento":"c"*32,
        "os_origem":["0","1","2","10","11","12"],
        "codigos_origem":["THE C001","THE C002"],
        "evento_rotulo":"EVT-101 — Teresina",
        "poligono":geo,
    }

class TestMerge(unittest.TestCase):
    def test_chave_estavel_por_evento(self):
        ev={"Cidade":"Teresina","Protocolo":"42","Início":"11/11/2026 10:00",
            "Serviço":"Reparo","Status":"Em andamento"}
        k=func("chave_evento")(ev)
        self.assertEqual(k,func("chave_evento")({**ev,"Status":"Finalizado"}))
        self.assertNotEqual(k,func("chave_evento")({**ev,"Protocolo":"43"}))
        self.assertEqual(len(func("chave_acompanhamento_mesclado")(k)),32)

    def test_valida_poligono_aceita_desenho(self):
        g=poligono()
        self.assertEqual(func("validar_poligono")(g)["type"],"Polygon")
        self.assertEqual(func("extrair_desenho_folium")(
            {"type":"Feature","geometry":g,"properties":{}}
        ),g)

    def test_bloqueia_geometria_nao_poligonal(self):
        with self.assertRaises(ValueError):
            func("validar_poligono")({"type":"Point","coordinates":[-42.8,-5.1]})

    def test_bloqueia_auto_interseccao(self):
        x={"type":"Polygon","coordinates":[[
            [-42.8,-5.1],[-42.81,-5.12],[-42.8,-5.12],[-42.81,-5.1],[-42.8,-5.1],
        ]]}
        with self.assertRaises(ValueError):func("validar_poligono")(x)

    def test_seleciona_os_por_coordenadas(self):
        c,p=amostra()
        dentro,centro=func("contagem_no_poligono")(poligono(),c.iloc[0],p[p["_grupo"]==0])
        self.assertEqual(dentro,3)
        self.assertTrue(centro)
        dentro,centro=func("contagem_no_poligono")(poligono(),c.iloc[2],p[p["_grupo"]==2])
        self.assertEqual(dentro,0)
        self.assertFalse(centro)

    def test_mesclagem_tem_uma_linha_e_preserva_os(self):
        c,p=amostra()
        cc,pp=func("aplicar_mesclas")(c,p,[mescla(poligono())])
        self.assertEqual(len(cc),2)
        self.assertEqual(len(pp),9)
        reg=cc[cc["ID_CONCENTRACAO"]=="THE M000123"].iloc[0]
        self.assertEqual(reg["QTD_OS"],6)
        self.assertEqual(reg["QTD_MATRICULAS"],6)
        self.assertEqual(reg["CHAVE_MESCLA"],"c"*32)
        self.assertIsInstance(reg["AREA_MESCLADA"],dict)
        self.assertEqual(len(pp[pp["_grupo"]==reg["GRUPO"]]),6)
        self.assertEqual(len(pp[pp["_grupo"]==2]),3)

    def test_mescla_nao_une_municipios_diferentes(self):
        c,p=amostra()
        p.loc[p["_grupo"]==1,"Cidade"]="Timon"
        cc,pp=func("aplicar_mesclas")(c,p,[mescla()])
        self.assertEqual(len(pp[pp["_grupo"].astype(str).str.startswith("MESCLA_")]),3)

    def test_os_sem_intersecao_nao_forcam_mescla(self):
        c,p=amostra()
        m=mescla();m["os_origem"]=["00000"]
        cc,pp=func("aplicar_mesclas")(c,p,[m])
        self.assertEqual(len(cc),3)
        self.assertTrue(pp.equals(p))

    def test_isolamento_e_auditoria(self):
        modulo=(ROOT/"pages"/"3_Farol_Operacional.py").read_text(encoding="utf-8")
        auxiliar=SRC.read_text(encoding="utf-8")
        sql=(ROOT/"sql"/"10_mesclagem_concentracoes_evento_farol.sql").read_text(encoding="utf-8")
        self.assertIn("Mesclar concentrações",modulo)
        self.assertIn("last_active_drawing",modulo)
        self.assertIn("renderizar_painel_mescla(",modulo)
        self.assertIn("CHAVE_MESCLA",modulo)
        self.assertIn("farol_salvar_mescla_tx",auxiliar)
        self.assertIn("farol_mesclas_operacionais ENABLE ROW LEVEL SECURITY",sql)
        self.assertIn("INSERT INTO public.prisma_auditoria",sql)
        self.assertIn("cardinality(v_fichas),0)>1",sql)
        self.assertIn("ON CONFLICT(chave) DO NOTHING",sql)

if __name__=="__main__":unittest.main()
