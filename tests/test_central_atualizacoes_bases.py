"""Regressões da Central de Atualização de Bases — sem rede nem gravações."""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import types
from urllib.parse import quote
import unittest
from unittest.mock import Mock, patch

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT/"prisma_atualizacoes_backend.py"
PAGINA=ROOT/"pages"/"13_Atualizacao_de_Bases.py"
HUB=ROOT/"app.py"
SQL=ROOT/"sql"/"prisma_central_atualizacoes_bases.sql"


def funcoes(*nomes):
    arvore=ast.parse(BACKEND.read_text(encoding="utf-8"))
    defs=[item for item in arvore.body
          if isinstance(item,ast.FunctionDef) and item.name in nomes]
    if len(defs)!=len(nomes):
        raise AssertionError("Função ausente ou duplicada")
    erro=type("ErroAtualizacao",(RuntimeError,),{})
    ns={
        "csv":csv,"io":io,"json":json,"pd":pd,
        "PurePosixPath":PurePosixPath,
        "EXTENSOES_DADOS":set(".csv .xlsx .xls .gpkg .kmz .kml .tif .tiff .geojson .zip".split()),
        "BACKUP_STORAGE":"_backup_atualizacoes",
        "MAX_STORAGE":35*1024*1024,
        "MAX_GITHUB":35*1024*1024,
        "MAX_REGISTROS_TABELA":10000,
        "COLUNAS_POCOS":("ID_POCO","IDENTIFICACAO_ATIVO","NOME_POCO","MUNICIPIO","LATITUDE","LONGITUDE"),
        "ErroAtualizacao":erro,
        "quote":quote,
        "hashlib":hashlib,
    }
    exec(compile(ast.Module(body=defs,type_ignores=[]),str(BACKEND),"exec"),ns)
    return ns


def csv_pocos():
    return ("ID_POCO,IDENTIFICACAO_ATIVO,NOME_POCO,MUNICIPIO,LATITUDE,LONGITUDE\n"
            ",PL-THE-PCO1,THE-001,TERESINA,\"-5,12\",\"-42,75\"\n"
            ",PL-THE-PCO2,THE-002,TERESINA,\"-5,13\",\"-42,76\"\n"
           ).encode("utf-8")


class TestCentralAtualizacoes(unittest.TestCase):
    def setUp(self):
        self.ns=funcoes("caminho_permitido","_sha","_csv_ler",
                        "_validar_pontos","preparar_linhas_pocos","validar_arquivo")

    def test_somente_arquivos_operacionais_substituiveis(self):
        permitido=self.ns["caminho_permitido"]
        for caminho in ("dados/loggers.csv","dados/matriculas.xlsx",
                        "dados/topografia/teresina_dem.tif",
                        "geodados/municipios/TIMON.gpkg"):
            self.assertTrue(permitido(caminho),caminho)
        for caminho in ("app.py","pages/3_Farol_Operacional.py",
                        ".streamlit/secrets.toml","sql/schema.sql",
                        "dados/../../../secrets.csv","dados/payload.py",
                        "/dados/loggers.csv","dados\\loggers.csv"):
            self.assertFalse(permitido(caminho),caminho)
        self.assertFalse(permitido("_backup_atualizacoes/versao/dados/loggers.csv",storage=True))

    def test_csv_pocos_valido_com_virgulas_decimais(self):
        resumo=self.ns["validar_arquivo"]("dados/pocos.csv","pocos.csv",csv_pocos())
        self.assertEqual(resumo["registros"],2)
        self.assertIn("IDENTIFICACAO_ATIVO",resumo["colunas"])
        linhas=self.ns["preparar_linhas_pocos"](csv_pocos(),".csv")
        self.assertEqual(len(linhas),2)
        self.assertEqual(linhas[0]["LATITUDE"],"-5,12")

    def test_rejeita_identificadores_duplicados(self):
        data=csv_pocos().replace(b"PL-THE-PCO2",b"PL-THE-PCO1")
        with self.assertRaisesRegex(self.ns["ErroAtualizacao"],"duplicado"):
            self.ns["validar_arquivo"]("dados/pocos.csv","pocos.csv",data)

    def test_rejeita_coordenada_invalida_mesmo_apos_amostra(self):
        original=csv_pocos()
        final=original+b",PL-THE-PCO3,THE-003,TERESINA,999,-42\n"
        with self.assertRaisesRegex(self.ns["ErroAtualizacao"],"Coordenada"):
            self.ns["validar_arquivo"]("dados/pocos.csv","pocos.csv",final)

    def test_rejeita_extensao_trocada_ou_arquivo_vazio(self):
        val=self.ns["validar_arquivo"]
        with self.assertRaisesRegex(self.ns["ErroAtualizacao"],"extensão"):
            val("dados/pocos.csv","pocos.xlsx",csv_pocos())
        with self.assertRaisesRegex(self.ns["ErroAtualizacao"],"vazio"):
            val("dados/pocos.csv","pocos.csv",b"")

    def test_gpkg_e_tiff_tem_assinatura(self):
        val=self.ns["validar_arquivo"]
        self.assertEqual(val("geodados/municipios/TIMON.gpkg","TIMON.gpkg",
                             b"SQLite format 3\x00"+b"x"*100)["extensao"],".gpkg")
        with self.assertRaisesRegex(self.ns["ErroAtualizacao"],"GeoPackage"):
            val("geodados/municipios/TIMON.gpkg","TIMON.gpkg",b"data" )
        val("dados/topografia/a.tif","a.tif",b"II*\x00payload")
        with self.assertRaisesRegex(self.ns["ErroAtualizacao"],"TIFF"):
            val("dados/topografia/a.tif","a.tif",b"not a tif")

    def test_comparacao_loggers_identifica_entradas_saidas_e_alteracoes(self):
        ns=funcoes("comparar_csv_github","_comparar_registros_csv","_csv_ler")
        import base64
        import re
        colunas="ID_LOGGER,IDENTIFICACAO_ATIVO,ENDERECO,MUNICIPIO,LATITUDE,LONGITUDE\n"
        antigo=(colunas+
            ",AAA,A,TERESINA,-5,-42\n"
            ",BBB,B,TERESINA,-5,-42\n").encode()
        novo=(colunas+
            ",AAA,Alterado,TERESINA,-5,-42\n"
            ",CCC,C,TERESINA,-5,-42\n").encode()
        ns.update({"administrador_atual":lambda:"autor",
                   "github_config":lambda:("JothaP/prisma-coi","main","token"),
                   "re":re,"base64":base64,
                   "_github":lambda *a,**kw:{
                       "encoding":"base64","size":len(antigo),
                       "content":base64.b64encode(antigo).decode(),
                   }})
        res=ns["comparar_csv_github"]("dados/loggers.csv",novo,"a"*40)
        self.assertEqual(res["adicionados"],1)
        self.assertEqual(res["removidos"],1)
        self.assertEqual(res["modificados"],1)
        self.assertEqual(res["inalterados"],0)

    def test_base_antiga_com_duplicatas_nao_impede_comparacao(self):
        ns=funcoes("_comparar_registros_csv")
        colunas=("IDENTIFICACAO_ATIVO","MUNICIPIO","LATITUDE","LONGITUDE")
        antigos=[
            {"IDENTIFICACAO_ATIVO":"PL-A","MUNICIPIO":"THE","LATITUDE":"-5","LONGITUDE":"-42"},
            {"IDENTIFICACAO_ATIVO":"PL-A","MUNICIPIO":"TIM","LATITUDE":"-6","LONGITUDE":"-43"},
            {"IDENTIFICACAO_ATIVO":"PL-B","MUNICIPIO":"THE","LATITUDE":"-5","LONGITUDE":"-42"},
        ]
        novos=[
            {"IDENTIFICACAO_ATIVO":"PL-A","MUNICIPIO":"TIM","LATITUDE":"-6","LONGITUDE":"-43"},
            {"IDENTIFICACAO_ATIVO":"PL-B","MUNICIPIO":"THE","LATITUDE":"-5","LONGITUDE":"-42"},
        ]
        resultado=ns["_comparar_registros_csv"](antigos,novos,colunas)
        self.assertEqual(resultado["anteriores"],3)
        self.assertEqual(resultado["novos"],2)
        self.assertEqual(resultado["duplicados_antigos"],1)
        self.assertEqual(resultado["ocorrencias_excedentes_antigas"],1)
        self.assertEqual(resultado["amostra_duplicados_antigos"][0]["Ocorrências"],2)
        self.assertEqual(resultado["modificados"],1)
        self.assertEqual(resultado["removidos"],0)
        self.assertEqual(resultado["inalterados"],1)

    def test_base_antiga_com_duplicatas_iguais_e_sem_identificacao(self):
        ns=funcoes("_comparar_registros_csv")
        colunas=("IDENTIFICACAO_ATIVO","MUNICIPIO")
        antigo=[
            {"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"},
            {"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"},
            {"IDENTIFICACAO_ATIVO":"","MUNICIPIO":"THE"},
            {"IDENTIFICACAO_ATIVO":"B","MUNICIPIO":"THE"},
        ]
        novo=[
            {"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"},
            {"IDENTIFICACAO_ATIVO":"B","MUNICIPIO":"THE"},
        ]
        resultado=ns["_comparar_registros_csv"](antigo,novo,colunas)
        self.assertEqual(resultado["sem_identificacao_antigos"],1)
        self.assertEqual(resultado["duplicados_antigos"],1)
        self.assertEqual(resultado["modificados"],1)
        self.assertEqual(resultado["removidos"],0)

    def test_ordem_das_linhas_antigas_duplicadas_nao_altera_resultado(self):
        ns=funcoes("_comparar_registros_csv")
        colunas=("IDENTIFICACAO_ATIVO","MUNICIPIO")
        registros=[
            {"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"},
            {"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"TIM"},
        ]
        novo=[{"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"}]
        original=ns["_comparar_registros_csv"](registros,novo,colunas)
        invertida=ns["_comparar_registros_csv"](
            list(reversed(registros)),novo,colunas
        )
        self.assertEqual(original["duplicados_antigos"],1)
        self.assertEqual(original["modificados"],1)
        self.assertEqual(original["modificados"],invertida["modificados"])
        self.assertEqual(original["inalterados"],invertida["inalterados"])

    def test_duplicidade_nova_e_bloqueada_na_comparacao(self):
        ns=funcoes("_comparar_registros_csv")
        colunas=("IDENTIFICACAO_ATIVO","MUNICIPIO")
        antigo=[{"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"}]
        novo=[{"IDENTIFICACAO_ATIVO":"A","MUNICIPIO":"THE"},
              {"IDENTIFICACAO_ATIVO":"a","MUNICIPIO":"TIM"}]
        with self.assertRaisesRegex(ns["ErroAtualizacao"],"NOVA base"):
            ns["_comparar_registros_csv"](antigo,novo,colunas)

    def test_comparacao_das_bases_reais_com_duplicidades_existentes(self):
        ns=funcoes("_comparar_registros_csv","_csv_ler")
        for caminho in ("dados/loggers.csv","dados/pocos.csv"):
            bruto=(ROOT/caminho).read_bytes()
            colunas, registros=ns["_csv_ler"](bruto)
            anteriores=list(registros)
            distintos={}
            for reg in anteriores:
                chave=str(reg.get("IDENTIFICACAO_ATIVO") or "").strip().casefold()
                if chave:
                    distintos.setdefault(chave,reg)
            # Simula candidato válido construído em memória: não grava no GitHub.
            comparacao=ns["_comparar_registros_csv"](
                anteriores,list(distintos.values()),colunas
            )
            self.assertEqual(comparacao["anteriores"],len(anteriores))
            self.assertEqual(comparacao["identificadores_anteriores"],len(distintos))
            self.assertEqual(comparacao["novos"],len(distintos))
            self.assertEqual(comparacao["adicionados"],0)
            self.assertEqual(comparacao["removidos"],0)
            from collections import Counter
            frequencias=Counter(
                str(reg.get("IDENTIFICACAO_ATIVO") or "").strip().casefold()
                for reg in anteriores
                if str(reg.get("IDENTIFICACAO_ATIVO") or "").strip()
            )
            self.assertEqual(
                comparacao["duplicados_antigos"],
                sum(quantidade>1 for quantidade in frequencias.values()),
            )
            self.assertEqual(
                comparacao["ocorrencias_excedentes_antigas"],
                len(anteriores)-len(distintos)-comparacao["sem_identificacao_antigos"]
            )

    def test_upload_github_revalida_sha_e_audita(self):
        e=self.ns["ErroAtualizacao"]
        ns=funcoes("substituir_github")
        ns.update({
            "administrador_atual":lambda:"id-usuario",
            "validar_arquivo":lambda *a,**k:True,
            "github_config":lambda:("JothaP/prisma-coi","main","token"),
            "_auditar_base":Mock(),
            "ErroAtualizacao":e,
            "base64":__import__("base64"),
        })
        chamadas=[]
        def requisitar(metodo,path,**kwargs):
            chamadas.append((metodo,path,kwargs))
            if metodo=="GET":return {"type":"file","sha":"sha-correta"}
            return {"commit":{"sha":"1234567890abcdef"}}
        ns["_github"]=requisitar
        resultado=ns["substituir_github"]("dados/loggers.csv","loggers.csv",b"conteudo","sha-correta")
        self.assertEqual(resultado["commit"],"1234567890abcdef")
        self.assertEqual([x[0] for x in chamadas],["GET","PUT"])
        self.assertEqual(ns["_auditar_base"].call_count,1)
        chamadas.clear()
        with self.assertRaisesRegex(e,"alterado desde"):
            ns["substituir_github"]("dados/loggers.csv","loggers.csv",b"conteudo","sha-antiga")
        self.assertEqual([x[0] for x in chamadas],["GET"])

    def test_storage_backup_acontece_antes_de_substituir(self):
        ns=funcoes("substituir_storage")
        ns.update({
            "administrador_atual":lambda:"autor",
            "validar_arquivo":lambda *a,**k:True,
            "baixar_storage":lambda *a:b"versao-antiga",
            "MAX_STORAGE":35*1024*1024,
            "_sha":lambda b:hashlib.sha256(b).hexdigest(),
            "re":__import__("re"),
            "datetime":__import__("datetime").datetime,
            "timezone":__import__("datetime").timezone,
            "uuid":__import__("uuid"),
            "_auditar_base":Mock(),
        })
        chamadas=[]
        ns["_enviar_storage"]=lambda *args,**kwargs:chamadas.append((args,kwargs))
        resultado=ns["substituir_storage"]("historico-prisma","dados/a.csv",
                                           "a.csv",b"versao-nova")
        self.assertEqual(len(chamadas),2)
        self.assertIn("_backup_atualizacoes/",chamadas[0][0][1])
        self.assertEqual(chamadas[0][0][2],b"versao-antiga")
        self.assertNotIn("upsert",chamadas[0][1])
        self.assertEqual(chamadas[1][0][:2],("historico-prisma","dados/a.csv"))
        self.assertTrue(chamadas[1][1].get("upsert"))
        self.assertEqual(resultado["caminho"],"dados/a.csv")
        self.assertEqual(ns["_auditar_base"].call_count,1)

    def test_banco_somente_whitelist_e_rpc(self):
        ns=funcoes("substituir_tabela_pocos","restaurar_tabela_pocos","exportar_tabela")
        ns.update({"administrador_atual":lambda:"ator",
                   "TABELAS_CONSULTA":{"prisma_ativos_importacao":"Poços"},
                   "COLUNAS_POCOS":self.ns["COLUNAS_POCOS"],
                   "MAX_REGISTROS_TABELA":10000})
        chamadas=[]
        def t(*args,**kwargs):
            chamadas.append((args,kwargs))
            if args[0].startswith("rpc/"):
                return {"concluido":True,"backup_id":1}
            return [{"IDENTIFICACAO_ATIVO":"X"}]
        ns["_tabela"]=t
        linha={campo:"X" for campo in self.ns["COLUNAS_POCOS"]}
        self.assertTrue(ns["substituir_tabela_pocos"]([linha])["concluido"])
        self.assertEqual(chamadas[0][0],("rpc/prisma_atualizacoes_substituir_pocos_tx",))
        self.assertEqual(chamadas[0][1]["payload"]["p_autor"],"ator")
        ns["restaurar_tabela_pocos"](1)
        self.assertEqual(chamadas[1][0],("rpc/prisma_atualizacoes_restaurar_pocos_tx",))
        with self.assertRaises(ns["ErroAtualizacao"]):
            ns["exportar_tabela"]("prisma_perfis")

    def test_sql_transacional_e_protegido(self):
        sql=SQL.read_text(encoding="utf-8")
        self.assertIn("SECURITY INVOKER",sql)
        self.assertIn("perfil = 'admin' AND status = 'ativo'",sql)
        self.assertIn("ALTER TABLE public.prisma_atualizacoes_backups ENABLE ROW LEVEL SECURITY",sql)
        self.assertIn("INSERT INTO public.prisma_atualizacoes_backups",sql)
        self.assertIn("INSERT INTO public.prisma_auditoria",sql)
        self.assertIn("DELETE FROM public.prisma_ativos_importacao",sql)
        self.assertIn("FROM PUBLIC, anon, authenticated",sql)
        self.assertIn("TO service_role",sql)
        self.assertNotIn("DELETE FROM public.prisma_ativos;",sql)

    def test_hub_interface_administrativa_e_confirmações(self):
        fonte=HUB.read_text(encoding="utf-8")
        pagina=PAGINA.read_text(encoding="utf-8")
        self.assertIn('if perfil_interno == "admin":\n        if st.button(\n            "🗂️ Atualização',fonte)
        self.assertIn("pages/13_Atualizacao_de_Bases.py",fonte)
        self.assertIn("administrador_atual()",pagina)
        self.assertIn('frase.strip()!="SUBSTITUIR"',pagina)
        self.assertIn('palavra.strip()!="SUBSTITUIR POCOS"',pagina)
        self.assertIn('frase.strip()!="RESTAURAR POCOS"',pagina)
        self.assertIn("validar_arquivo(",pagina)
        self.assertNotIn("exec(",pagina)
        self.assertNotIn("eval(",pagina)


if __name__=="__main__":
    unittest.main()
