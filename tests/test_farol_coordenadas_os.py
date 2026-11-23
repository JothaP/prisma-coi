"""Regressões de correção de coordenadas de O.S. sem rede ou gravação real."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from farol_coordenadas_os import (  # noqa: E402
    CABECALHO, NOME_ABA, ErroCoordenadasOS, numero_coordenada,
    aplicar_correcoes, interpretar_historico, registrar_correcao,
)


class AbaFake:
    def __init__(self, linhas=None):
        self.linhas = [list(linha) for linha in (linhas or [])]
    def get_all_values(self):
        return [list(linha) for linha in self.linhas]
    def update(self, *, range_name, values, value_input_option):
        assert range_name == "A1:I1"
        assert value_input_option == "RAW"
        self.linhas = [list(linha) for linha in values]
    def append_row(self, row, *, value_input_option):
        assert value_input_option == "RAW"
        self.linhas.append(list(row))


class NaoEncontrada(Exception):
    pass


class PlanilhaFake:
    def __init__(self, aba=None):
        self.aba = aba
        self.criacoes = 0
    def worksheet(self, nome):
        if self.aba is None:
            raise NaoEncontrada(nome)
        assert nome == NOME_ABA
        return self.aba
    def add_worksheet(self, *, title, rows, cols):
        assert title == NOME_ABA
        assert rows == 1000 and cols == len(CABECALHO)
        self.criacoes += 1
        self.aba = AbaFake()
        return self.aba


def salvar(planilha, chave, *, acao="CORRIGIR",
           lat="-5,134879", lon="-42,784007", revisao=None):
    return registrar_correcao(
        planilha, chave_os=chave, acao=acao, latitude=lat, longitude=lon,
        latitude_original="-5,134000", longitude_original="-42,784000",
        autor_id="uuid-operador", autor="Operador de teste",
        momento="2026-11-23T16:00:00-03:00",
        revisao_esperada=revisao, worksheet_not_found=NaoEncontrada,
    )


def chave_ponto_fake(row):
    numero = str(row.get("Nº da O.S", "")).strip().upper()
    return "OS:" + numero if numero else "HASH:IGNORADO"


class TesteCoordenadasOS(unittest.TestCase):
    def test_cria_aba_na_primeira_correcao_e_mantem_log_append_only(self):
        p = PlanilhaFake()
        res = salvar(p, "OS:900400419")
        self.assertEqual(res["acao"], "CORRIGIR")
        self.assertEqual(p.criacoes, 1)
        self.assertEqual(p.aba.linhas[0], CABECALHO)
        self.assertEqual(len(p.aba.linhas), 2)
        registro = interpretar_historico(p.aba.get_all_values())["OS:900400419"]
        self.assertEqual(registro["lat"], -5.134879)
        self.assertEqual(registro["lon"], -42.784007)
        self.assertEqual(registro["AUTOR_ID"], "uuid-operador")
        self.assertEqual(registro["revisao"], 2)

    def test_correcao_nao_modifica_pontos_originais_e_resiste_a_reimportacao(self):
        df = pd.DataFrame([
            {"Nº da O.S":"900400419","Latitude":-5.0,"Longitude":-42.0},
            {"Nº da O.S":"900400420","Latitude":float("nan"),"Longitude":float("nan")},
        ])
        correcoes = {
            "OS:900400419":{"ACAO":"CORRIGIR","lat":-5.134879,"lon":-42.784007},
            "OS:900400420":{"ACAO":"CORRIGIR","lat":-5.2,"lon":-42.8},
        }
        resultado = aplicar_correcoes(df, correcoes, chave_ponto_fake)
        self.assertAlmostEqual(resultado.iloc[0]["Latitude"],-5.134879)
        self.assertAlmostEqual(resultado.iloc[1]["Longitude"],-42.8)
        self.assertEqual(df.iloc[0]["Latitude"],-5.0)
        self.assertTrue(pd.isna(df.iloc[1]["Latitude"]))
        reimportado = df.copy()
        reimportado.loc[0,"Latitude"] = -5.7
        nova_versao = aplicar_correcoes(reimportado, correcoes, chave_ponto_fake)
        self.assertAlmostEqual(nova_versao.iloc[0]["Latitude"],-5.134879)
        self.assertAlmostEqual(reimportado.iloc[0]["Latitude"],-5.7)

    def test_restauracao_adiciona_evento_e_volta_ao_valor_importado(self):
        p = PlanilhaFake()
        salvar(p,"OS:900400419")
        salvar(p,"OS:900400419",acao="RESTAURAR",revisao=2)
        self.assertEqual(len(p.aba.linhas),3)
        atual = interpretar_historico(p.aba.get_all_values())
        self.assertEqual(atual["OS:900400419"]["ACAO"],"RESTAURAR")
        self.assertEqual(atual["OS:900400419"]["revisao"],3)
        df = pd.DataFrame([
            {"Nº da O.S":"900400419","Latitude":-5.0,"Longitude":-42.0}
        ])
        resultado = aplicar_correcoes(df,atual,chave_ponto_fake)
        self.assertEqual(resultado.iloc[0]["Latitude"],-5.0)

    def test_rejeita_gravacao_concorrente_antes_de_append(self):
        p=PlanilhaFake()
        salvar(p,"OS:900400419")
        with self.assertRaisesRegex(ErroCoordenadasOS,"Outra correção"):
            salvar(p,"OS:900400419",revisao=None)
        self.assertEqual(len(p.aba.linhas),2)

    def test_outros_pontos_nao_sao_alterados(self):
        df=pd.DataFrame([
            {"Nº da O.S":"1","Latitude":-5.0,"Longitude":-42.0},
            {"Nº da O.S":"2","Latitude":-6.0,"Longitude":-43.0}
        ])
        correcoes={"OS:1":{"ACAO":"CORRIGIR","lat":-5.1,"lon":-42.1}}
        alterado=aplicar_correcoes(df,correcoes,chave_ponto_fake)
        self.assertEqual(alterado.loc[1,"Latitude"],-6.0)
        self.assertEqual(alterado.loc[1,"Longitude"],-43.0)

    def test_validacao_de_coordenadas_e_permissoes(self):
        self.assertEqual(numero_coordenada("-5,134879","lat"),-5.134879)
        for valor in ("", "nan", "inf", "0", "-95", "abc"):
            with self.subTest(valor=valor):
                with self.assertRaises(ErroCoordenadasOS):
                    numero_coordenada(valor,"lat")
        p=PlanilhaFake()
        with self.assertRaises(PermissionError):
            registrar_correcao(
                p,chave_os="OS:1",acao="CORRIGIR",latitude=-5,longitude=-42,
                latitude_original=-5,longitude_original=-42,autor_id="",
                autor="",momento="",revisao_esperada=None,
                worksheet_not_found=NaoEncontrada,
            )
        self.assertEqual(p.criacoes,0)

    def test_cabecalho_inesperado_bloqueia_sem_alterar_a_aba(self):
        p=PlanilhaFake(AbaFake([["ID","COORDENADA"],["1","2"]]))
        with self.assertRaisesRegex(ErroCoordenadasOS,"Cabeçalho inesperado"):
            salvar(p,"OS:1")
        self.assertEqual(len(p.aba.linhas),2)

    def test_correcao_invalida_nao_apaga_historico(self):
        p=PlanilhaFake()
        salvar(p,"OS:1")
        with self.assertRaises(ErroCoordenadasOS):
            salvar(p,"OS:1",lat="999",lon="-42",revisao=2)
        self.assertEqual(len(p.aba.linhas),2)

    def test_integracao_no_modulo_3_antes_do_cluster_e_sem_mudar_importacao(self):
        texto=(ROOT/"pages"/"3_Farol_Operacional.py").read_text(encoding="utf-8")
        ast.parse(texto)
        self.assertIn('df_pontos_importados = df_pontos.copy()',texto)
        self.assertIn('df_pontos = aplicar_correcoes(',texto)
        self.assertLess(texto.index("df_pontos = aplicar_correcoes("),
                        texto.index("concentracoes, pontos_cluster = criar_concentracoes("))
        self.assertIn('modal_corrigir_localizacao_os(',texto)
        self.assertIn('returned_objects=["last_clicked"]',texto)
        self.assertIn("✏️ Corrigir coordenadas pelo número da O.S.",texto)
        self.assertIn("✏️ Corrigir localização desta O.S.",texto)
        self.assertIn("carregar_correcoes_coordenadas_os.clear()",texto)
        self.assertIn('st.session_state.get("prisma_usuario_id", "")',texto)
        self.assertIn("if PODE_EDITAR_FAROL:",texto)
        self.assertIn("def upsert_pontos(df_novo):",texto)
        self.assertNotIn("registrar_correcao(",texto[
            texto.index("def upsert_pontos(df_novo):"):
            texto.index("def carregar_areas_editadas():")
        ])


if __name__=="__main__":
    unittest.main()
