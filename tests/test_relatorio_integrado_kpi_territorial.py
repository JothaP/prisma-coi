"""Regressão dos KPIs territoriais do Relatório Integrado (THE/TIM/API).

Executa somente funções isoladas da AST sem iniciar a interface Streamlit.
"""
import ast
from pathlib import Path
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODULO7 = ROOT / "pages" / "7_Relatorios_Integrados.py"


def carregar_calculos():
    fonte = MODULO7.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    nomes = {"totais_territoriais_concentracoes", "rotulo_total_territorial"}
    nos = [
        no for no in arvore.body
        if isinstance(no, ast.FunctionDef) and no.name in nomes
    ]
    assert {no.name for no in nos} == nomes
    ambiente = {"pd": pd}
    exec(compile(ast.Module(body=nos, type_ignores=[]), str(MODULO7), "exec"), ambiente)
    return ambiente


def preparar_cenario(unidade, dentro, fora, **outros):
    """Cada entrada é (municipio, bairro, número de O.S.) no mesmo período."""
    linhas, grupo = [], []
    numero = 0
    for municipio, bairro, quantidade in dentro + fora:
        for _ in range(quantidade):
            numero += 1
            reg = {
                "Nº da O.S": str(numero),
                "_municipio": municipio,
                "_bairro": bairro,
                "_grupo": 10 if (municipio, bairro, quantidade) in dentro else -1,
            }
            linhas.append(reg)
    # Usa a posição, não os campos: áreas podem coincidir entre dentro e fora.
    total_dentro = sum(n for _,_,n in dentro)
    for i in range(total_dentro):
        grupo.append(linhas[i].copy())
        grupo[-1]["_grupo"] = 10
    return (
        pd.DataFrame([{"Concentração": f"{unidade} C001", "_grupo": 10,
                       "_unidade": unidade, "O.S.": len(grupo),
                       "Matrículas": 1}]),
        pd.DataFrame(grupo),
        pd.DataFrame(linhas),
    )


class TestTotalTerritorial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        funcoes = carregar_calculos()
        cls.calcular = staticmethod(funcoes["totais_territoriais_concentracoes"])
        cls.rotulo = staticmethod(funcoes["rotulo_total_territorial"])

    def test_the_cinco_na_concentracao_e_sete_fora_equivale_doze(self):
        conc, grupos, base = preparar_cenario(
            "THE",
            [("TERESINA","A",1), ("TERESINA","B",2), ("TERESINA","C",2)],
            [("TERESINA","B",3), ("TERESINA","C",4),
             ("TERESINA","D",9)],
        )
        resumo = self.calcular(conc, grupos, base)
        self.assertEqual(int(resumo.iloc[0]["Total territorial"]), 12)
        self.assertEqual(int(resumo.iloc[0]["O.S."]), 5)
        self.assertEqual(int(resumo.iloc[0]["Matrículas"]), 1)
        self.assertEqual(self.rotulo("THE"), "Total da Região")

    def test_timon_soma_bairros_sem_incluir_outros_bairros(self):
        conc, grupos, base = preparar_cenario(
            "TIM", [("TIMON","CENTRO",3)],
            [("TIMON","CENTRO",5), ("TIMON","SAO BENEDITO",4)],
        )
        self.assertEqual(int(self.calcular(conc, grupos, base).iloc[0]["Total territorial"]), 8)
        self.assertEqual(self.rotulo("TIM"), "Total da Região")

    def test_api_quatro_no_agrupamento_mais_cinco_no_municipio(self):
        conc, grupos, base = preparar_cenario(
            "API", [("PICOS","A",2), ("PICOS","B",2)],
            [("PICOS","C",5), ("FLORIANO","A",4)],
        )
        self.assertEqual(int(self.calcular(conc, grupos, base).iloc[0]["Total territorial"]), 9)
        self.assertEqual(self.rotulo("API"), "Total do Município")

    def test_api_multiplos_municipios_sem_duplicar_cidades(self):
        conc, grupos, base = preparar_cenario(
            "API", [("MUNICIPIO X","A",2), ("MUNICIPIO X","B",1),
                    ("MUNICIPIO Y","B",1)],
            [("MUNICIPIO X","C",5), ("MUNICIPIO Y","D",3),
             ("MUNICIPIO Z","B",7)],
        )
        self.assertEqual(int(self.calcular(conc, grupos, base).iloc[0]["Total territorial"]), 12)

    def test_the_ignora_bairro_homonimo_de_outro_municipio(self):
        conc, grupos, base = preparar_cenario(
            "THE", [("TERESINA","CENTRO",3)],
            [("TERESINA","CENTRO",2), ("OUTRO MUNICIPIO","CENTRO",6)],
        )
        self.assertEqual(int(self.calcular(conc, grupos, base).iloc[0]["Total territorial"]), 5)

    def test_os_sem_bairro_permanecem_mas_nao_ampliam_area_em_branco(self):
        conc, grupos, base = preparar_cenario(
            "TIM", [("TIMON","",2), ("TIMON","CENTRO",2)],
            [("TIMON","",5), ("TIMON","CENTRO",3)],
        )
        self.assertEqual(int(self.calcular(conc, grupos, base).iloc[0]["Total territorial"]), 7)

    def test_multiplas_concentracoes_mesmo_bairro_nao_duplicam_registros(self):
        conc, grupos, base = preparar_cenario(
            "THE", [("TERESINA","A",3)], [("TERESINA","A",4)],
        )
        conc2 = conc.copy()
        conc2["_grupo"] = 11
        conc2["Concentração"] = "THE C002"
        grupos2 = grupos.copy()
        grupos2["_grupo"] = 11
        resumo = self.calcular(
            pd.concat([conc,conc2],ignore_index=True),
            pd.concat([grupos,grupos2],ignore_index=True),
            base,
        )
        self.assertEqual(resumo["Total territorial"].tolist(), [7,7])

    def test_sem_concentracoes_nao_quebra_e_preserva_coluna(self):
        vazio = pd.DataFrame(columns=["Concentração", "_grupo", "_unidade"])
        resultado = self.calcular(vazio,pd.DataFrame(),pd.DataFrame())
        self.assertTrue(resultado.empty)
        self.assertIn("Total territorial",resultado.columns)

    def test_interface_pdf_e_csv_usam_mesmo_kpi(self):
        fonte = MODULO7.read_text(encoding="utf-8")
        self.assertIn('str(int(conc.get(\'Total territorial\', 0)))',fonte)
        self.assertIn('rotulo_total_territorial(conc.get(\'_unidade\', \'\'))',fonte)
        self.assertIn('resumo_exibicao = resumo.rename(columns={"Total territorial": rotulo_total})',fonte)
        self.assertIn('st.dataframe(resumo_exibicao[visiveis]',fonte)
        self.assertIn('data=resumo_exibicao[visiveis].to_csv(',fonte)
        self.assertNotIn("'Matrículas distintas'",fonte)
        self.assertIn("concs = totais_territoriais_concentracoes(concs, grupos, os_df)",fonte)
        self.assertIn('os_df = os_df[os_df["_data"].dt.date.between(inicio, fim)].copy()',fonte)
        self.assertIn('os_df = os_df[os_df["_unidade"] == unidade].copy()',fonte)
        self.assertIn('bp = bp[bp["_unidade"] == unidade].copy()',fonte)
        self.assertIn('"versao_esquema": 16',fonte)


if __name__ == "__main__":
    unittest.main()
