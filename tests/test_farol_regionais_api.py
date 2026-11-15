"""Regressão da separação visual das concentrações API por regional."""
import unittest
from pathlib import Path

import pandas as pd

from farol_regionais_api import (
    ABA_MUNICIPIOS_REGIONAIS, PLANILHA_CADASTROS_COI,
    REGIONAL_NAO_CADASTRADA, MULTIPLAS_REGIONAIS,
    chave_municipio, mapa_regionais_de_linhas,
    regional_da_concentracao_api, ordenar_concentracoes_api_por_regional,
)

RAIZ = Path(__file__).resolve().parents[1]


def concentracoes_exemplo():
    return pd.DataFrame([
        {"ID_CONCENTRACAO": "API C001", "GRUPO": 11, "QTD_OS": 9},
        {"ID_CONCENTRACAO": "API C002", "GRUPO": 22, "QTD_OS": 8},
        {"ID_CONCENTRACAO": "API C003", "GRUPO": 33, "QTD_OS": 7},
        {"ID_CONCENTRACAO": "API C004", "GRUPO": 44, "QTD_OS": 6},
    ])


class TesteRegionaisAPI(unittest.TestCase):
    def test_cadastro_exatamente_do_modulo5(self):
        mod5 = (RAIZ / "pages" / "5_Cadastros_Consultas_COI.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(PLANILHA_CADASTROS_COI, mod5)
        self.assertIn('"municipios_regionais"', mod5)
        self.assertEqual(ABA_MUNICIPIOS_REGIONAIS, "municipios_regionais")

    def test_le_cabecalho_e_acentos_independente_de_ordem(self):
        linhas = [
            ["ZONA", "REGIONAL", "MUNICIPIO", "BASE"],
            ["123", "  Cerrado  ", "São João do Piauí", "Base Sul"],
            ["10", "Litoral", "Luís Correia", "Base Costa"],
        ]
        catalogo = mapa_regionais_de_linhas(linhas)
        self.assertEqual(catalogo[chave_municipio("SAO JOAO DO PIAUI")], "Cerrado")
        self.assertEqual(catalogo[chave_municipio("Luís-Correia")], "Litoral")

    def test_linha_sem_regional_nao_inventa_classificacao(self):
        catalogo = mapa_regionais_de_linhas([
            ["MUNICIPIO", "BASE", "REGIONAL", "ZONA"],
            ["Campo Maior", "Base", "", "X"],
            ["Picos", "Base", "Semiárido Sul", "X"],
        ])
        self.assertNotIn(chave_municipio("CAMPO MAIOR"), catalogo)
        self.assertEqual(catalogo[chave_municipio("Picos")], "Semiárido Sul")

    def test_conflito_de_cadastro_mesmo_municipio_nao_e_arbitrado(self):
        catalogo = mapa_regionais_de_linhas([
            ["MUNICIPIO", "REGIONAL"],
            ["Picos", "Cerrado"],
            ["PICOS", "Litoral"],
            ["picos", "Cerrado"],
        ])
        self.assertNotIn(chave_municipio("Picos"), catalogo)

    def test_sem_colunas_obrigatorias_gera_erro(self):
        with self.assertRaisesRegex(ValueError, "MUNICIPIO e REGIONAL"):
            mapa_regionais_de_linhas([["Cidade", "Base"], ["Picos", "Base 1"]])

    def test_concentracao_de_dois_municipios_na_mesma_regional(self):
        grupo = pd.DataFrame({"Cidade": ["Picos", "PICOS", "Itainópolis"]})
        mapa = {
            chave_municipio("Picos"): "Semiárido Sul",
            chave_municipio("Itainópolis"): "SEMIÁRIDO SUL",
        }
        self.assertEqual(regional_da_concentracao_api(grupo, mapa),
                         "SEMIÁRIDO SUL")

    def test_concentracao_que_cruza_regionais_distintas(self):
        grupo = pd.DataFrame({"Cidade": ["Picos", "Parnaíba"]})
        mapa = {
            chave_municipio("Picos"): "Semiárido Sul",
            chave_municipio("Parnaíba"): "Litoral",
        }
        self.assertEqual(regional_da_concentracao_api(grupo, mapa),
                         MULTIPLAS_REGIONAIS)

    def test_concentracao_sem_municipio_ou_sem_cadastro_nao_desaparece(self):
        self.assertEqual(regional_da_concentracao_api(
            pd.DataFrame({"Cidade": ["Sem Cadastro"]}), {}
        ), REGIONAL_NAO_CADASTRADA)
        self.assertEqual(regional_da_concentracao_api(
            pd.DataFrame({"Cidade": ["Picos", "Cidade não cadastrada"]}),
            {chave_municipio("Picos"): "Cerrado"},
        ), REGIONAL_NAO_CADASTRADA)

    def test_ordena_regionais_e_mantem_ordem_interna_e_codigos(self):
        original = concentracoes_exemplo()
        grupos = {
            11: pd.DataFrame({"Cidade": ["Parnaíba"]}),
            22: pd.DataFrame({"Cidade": ["Picos"]}),
            33: pd.DataFrame({"Cidade": ["Luís Correia"]}),
            44: pd.DataFrame({"Cidade": ["Picos"]}),
        }
        mapa = {
            chave_municipio("Parnaíba"): "Litoral",
            chave_municipio("Luís Correia"): "Litoral",
            chave_municipio("Picos"): "Cerrado",
        }
        grupos_ordenados = ordenar_concentracoes_api_por_regional(original, grupos, mapa)
        self.assertEqual(
            [(r, c["ID_CONCENTRACAO"]) for r, c in grupos_ordenados],
            [("Cerrado", "API C002"), ("Cerrado", "API C004"),
             ("Litoral", "API C001"), ("Litoral", "API C003")]
        )
        self.assertEqual(original["ID_CONCENTRACAO"].tolist(),
                         ["API C001", "API C002", "API C003", "API C004"])

    def test_municipios_sem_regional_vem_ao_final(self):
        c = concentracoes_exemplo().iloc[:2].copy()
        grupos = {
            11: pd.DataFrame({"Cidade": ["Picos"]}),
            22: pd.DataFrame({"Cidade": ["Cidade sem regional"]}),
        }
        order = ordenar_concentracoes_api_por_regional(
            c, grupos, {chave_municipio("Picos"): "Cerrado"}
        )
        self.assertEqual([regional for regional, _ in order],
                         ["Cerrado", REGIONAL_NAO_CADASTRADA])

    def test_aba_api_usa_apenas_titulos_sem_filtro_novo(self):
        source = (RAIZ / "pages" / "3_Farol_Operacional.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("carregar_regionais_api_modulo5()", source)
        self.assertIn("ordenar_concentracoes_api_por_regional(", source)
        self.assertIn('if abrangencia == "API" and regional_atual != regional_anterior:', source)
        self.assertIn('st.markdown(f"#### {html_escape(regional_atual).upper()}")', source)
        self.assertIn('for aba, abrangencia in ((aba_the, "THE"), (aba_api, "API"), (aba_tim, "TIM")):', source)


if __name__ == "__main__":
    unittest.main()
