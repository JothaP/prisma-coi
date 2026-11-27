"""Paridade territorial entre Módulo 3 e Módulo 7: THE, API e TIM.

Sem acessar Google, Supabase ou Streamlit: funções puras extraídas do módulo.
"""
from __future__ import annotations

import ast
import math
import unicodedata
import unittest
from pathlib import Path

import pandas as pd

from farol_regionais_api import (
    unidade_operacional_os, chave_municipio,
    regional_da_concentracao_api, mapa_regionais_de_linhas,
    REGIONAL_CHAPADINHA_SUL, REGIONAL_NAO_CADASTRADA,
    MULTIPLAS_REGIONAIS,
)


ROOT = Path(__file__).resolve().parents[1]
M7 = ROOT / "pages" / "7_Relatorios_Integrados.py"
M3 = ROOT / "pages" / "3_Farol_Operacional.py"


def carregar_funcoes_m7():
    nomes = {
        "unidade_da_cidade_farol", "atribuir_unidade_operacional_ri7",
        "classificar_regionais_concentracoes_ri7", "concentracoes_farol",
        "haversine", "totais_territoriais_concentracoes",
    }
    arvore = ast.parse(M7.read_text(encoding="utf-8"))
    funcoes = [
        n for n in arvore.body
        if isinstance(n, ast.FunctionDef) and n.name in nomes
    ]
    assert {n.name for n in funcoes} == nomes
    contexto = {
        "pd": pd, "math": math,
        "unidade_operacional_os": unidade_operacional_os,
        "regional_da_concentracao_api": regional_da_concentracao_api,
        "chave_municipio": chave_municipio,
        "REGIONAL_NAO_CADASTRADA": REGIONAL_NAO_CADASTRADA,
        "MULTIPLAS_REGIONAIS": MULTIPLAS_REGIONAIS,
    }
    exec(compile(ast.Module(body=funcoes, type_ignores=[]), str(M7), "exec"), contexto)
    return contexto


def base_os():
    linhas = []
    dados = [
        ("THE", "Teresina", "Centro", -5.10, -42.80),
        ("API", "Teresina", "Chapadinha Sul", -5.10, -42.80),
        ("TIM", "Timon", "Centro", -5.10, -42.80),
        ("API-OUTRA", "Picos", "Centro", -7.09, -41.47),
    ]
    for unidade, cidade, bairro, lat, lon in dados:
        for pos in range(3):
            linhas.append({
                "Nº da O.S": f"{unidade}-{pos}",
                "Cidade": cidade, "Bairro": bairro,
                "_municipio": chave_municipio(cidade),
                "_bairro": chave_municipio(bairro),
                "Matrícula": f"{unidade}{pos}",
                "_lat": lat + pos * .00005,
                "_lon": lon + pos * .00005,
            })
    return pd.DataFrame(linhas)


class TesteParidadeUnidadesM7(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = carregar_funcoes_m7()

    def test_mesma_funcao_pura_m3_e_m7(self):
        codigo = M7.read_text(encoding="utf-8")
        self.assertIn("from farol_regionais_api import (", codigo)
        self.assertIn("return unidade_operacional_os(valor, bairro)", codigo)
        self.assertIn('atribuir_unidade_operacional_ri7(df, "Cidade", "Bairro")', codigo)
        self.assertIn('atribuir_unidade_operacional_ri7(df, "Municipio", "Bairro")', codigo)
        self.assertIn('os_df = os_df[os_df["_unidade"] == unidade].copy()', codigo)
        self.assertIn('bp = bp[bp["_unidade"] == unidade].copy()', codigo)
        self.assertIn('or r.get("versao_esquema") != 16', codigo)
        codigo_m3 = M3.read_text(encoding="utf-8")
        self.assertIn("return unidade_operacional_os(valor, bairro)", codigo_m3)

    def test_excecao_terezina_chapadinha_povoados_sem_afetar_urbano(self):
        classificar = self.f["unidade_da_cidade_farol"]
        for alias in ("Chapadinha Sul", "CHAPADINHA-SUL", "Povoado Chapadinha Sul"):
            self.assertEqual(classificar("Teresina", alias), "API")
        self.assertEqual(classificar("Teresina", "Centro"), "THE")
        self.assertEqual(classificar("Timon", "Chapadinha Sul"), "TIM")
        self.assertEqual(classificar("Picos", "Centro"), "API")

    def test_divisao_m3_m7_por_os_sem_misturar_proximidade(self):
        os_base = base_os()
        c, grupo = self.f["concentracoes_farol"](os_base, 750, 3)
        self.assertEqual(len(c), 4)
        self.assertEqual(set(c["_unidade"]), {"THE", "API", "TIM"})
        self.assertEqual(set(c["Concentração"]), {"THE C001", "API C001", "API C002", "TIM C001"})
        self.assertEqual(grupo["_grupo"].nunique(), 4)
        for _, concentracao in c.iterrows():
            os_concentracao = grupo[grupo["_grupo"] == concentracao["_grupo"]]
            self.assertEqual(len(os_concentracao), 3)
            self.assertEqual(os_concentracao["_unidade"].nunique(), 1)
            self.assertEqual(os_concentracao["_unidade"].iloc[0], concentracao["_unidade"])
        chap = c[(c["_unidade"] == "API") & (c["Município"] == "TERESINA")]
        self.assertEqual(len(chap), 1)
        self.assertEqual(chap.iloc[0]["Bairros"], "CHAPADINHASUL")

    def test_pressao_e_os_separadas_por_cidade_e_localidade(self):
        tabela = pd.DataFrame([
            {"Cidade": "Teresina", "Bairro": "Centro"},
            {"Cidade": "Teresina", "Bairro": "Chapadinha Sul"},
            {"Cidade": "Timon", "Bairro": "Centro"},
            {"Cidade": "Picos", "Bairro": "Centro"},
        ])
        c = self.f["atribuir_unidade_operacional_ri7"](tabela, "Cidade", "Bairro")
        self.assertEqual(c["_unidade"].tolist(), ["THE", "API", "TIM", "API"])
        bp = tabela.rename(columns={"Cidade": "Municipio"})
        b = self.f["atribuir_unidade_operacional_ri7"](bp, "Municipio", "Bairro")
        self.assertEqual(b["_unidade"].tolist(), ["THE", "API", "TIM", "API"])
        self.assertEqual(c.loc[c["_unidade"] == "THE", "Bairro"].tolist(), ["Centro"])
        self.assertIn("Chapadinha Sul", b.loc[b["_unidade"] == "API", "Bairro"].tolist())

    def test_regional_meio_norte_sem_inferir_cerrado_para_chapadinha(self):
        c, os_grupo = self.f["concentracoes_farol"](base_os(), 750, 3)
        mapa = mapa_regionais_de_linhas([
            ["MUNICIPIO", "REGIONAL"],
            ["Picos", "Semiárido Sul"],
        ])
        c2 = self.f["classificar_regionais_concentracoes_ri7"](c, os_grupo, mapa)
        chap = c2[(c2["_unidade"] == "API") & (c2["Município"] == "TERESINA")]
        picos = c2[(c2["_unidade"] == "API") & (c2["Município"] == "PICOS")]
        self.assertEqual(chap.iloc[0]["Regional"], REGIONAL_CHAPADINHA_SUL)
        self.assertEqual(picos.iloc[0]["Regional"], "SEMIÁRIDO SUL")
        self.assertEqual(c2[c2["_unidade"] == "THE"]["Regional"].tolist(), [""])
        self.assertEqual(c2[c2["_unidade"] == "TIM"]["Regional"].tolist(), [""])
        self.assertEqual(set(c2["Concentração"]), set(c["Concentração"]))
        sem_cadastro = self.f["classificar_regionais_concentracoes_ri7"](c, os_grupo, {})
        self.assertEqual(
            sem_cadastro[(sem_cadastro["_unidade"] == "API")
                         & (sem_cadastro["Município"] == "TERESINA")].iloc[0]["Regional"],
            REGIONAL_CHAPADINHA_SUL,
        )

    def test_contagem_api_terezina_nao_inclui_the(self):
        base = self.f["atribuir_unidade_operacional_ri7"](base_os(), "Cidade", "Bairro")
        subset = base[base["_unidade"] == "API"].copy()
        concs, grupos = self.f["concentracoes_farol"](subset, 750, 3)
        totais = self.f["totais_territoriais_concentracoes"](concs, grupos, subset)
        chap = totais[totais["Município"] == "TERESINA"]
        self.assertEqual(len(chap), 1)
        self.assertEqual(int(chap.iloc[0]["Total territorial"]), 3)

    def test_pdf_csv_tabela_exibem_regional_api(self):
        fonte = M7.read_text(encoding="utf-8")
        self.assertIn('visiveis.insert(1, "Regional")', fonte)
        self.assertIn('conc.get("Regional", "")', fonte)
        self.assertIn('tabela_regionais = ler_base(', fonte)
        self.assertIn('"aviso_regionais": aviso_regionais', fonte)
        self.assertIn('data=resumo_exibicao[visiveis].to_csv(', fonte)


if __name__ == "__main__":
    unittest.main()
