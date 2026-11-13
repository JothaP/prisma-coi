"""Testes isolados para a vinculação manual de O.S. a eventos no Farol.

Executa somente funções puras da fonte, sem rede, secrets ou Streamlit.
"""
import ast
from collections import defaultdict
from pathlib import Path
import re
import unittest
import unicodedata

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
FONTE = RAIZ / "farol_vinculos_os_manuais.py"
arvore = ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))
nomes = {"normalizar_os", "normalizar_municipio", "aplicar_vinculos_manuais",
         "eventos_manuais_do_grupo"}
funcoes = [x for x in arvore.body if isinstance(x, ast.FunctionDef) and x.name in nomes]
assert {f.name for f in funcoes} == nomes
ambiente = {
    "pd": pd, "re": re, "unicodedata": unicodedata,
    "defaultdict": defaultdict,
    "VALIDO_OS": re.compile(r"^[A-Z0-9./_-]{1,100}$"),
}
exec(compile(ast.Module(body=funcoes, type_ignores=[]), str(FONTE), "exec"), ambiente)


def linhas():
    vinculadas = pd.DataFrame([
        {"Nº da O.S": "200/2026-1", "Cidade": "Teresina",
         "Evento(s) vinculado(s)": "EV-AUTO", "Critério do vínculo": "Polígono"},
    ])
    sem = pd.DataFrame([
        {"Nº da O.S": "100/2026-1", "Cidade": "Teresina",
         "Evento(s) vinculado(s)": "", "Critério do vínculo": ""},
        {"Nº da O.S": "300/2026-1", "Cidade": "Timon",
         "Evento(s) vinculado(s)": "", "Critério do vínculo": ""},
    ])
    return vinculadas, sem


class VinculoManual(unittest.TestCase):
    def test_movimenta_os_sem_duplicar_e_mantem_automatico(self):
        vinculadas, nao = linhas()
        os_nova = {"numero_os": "100/2026-1", "municipio": "TERESINA",
                   "protocolo": "EV-MANUAL"}
        os_auto = {"numero_os": "200/2026-1", "municipio": "TERESINA",
                   "protocolo": "EV-AUTO"}
        novas, restantes = ambiente["aplicar_vinculos_manuais"](
            vinculadas, nao, [os_nova, os_auto])
        self.assertEqual(len(novas), 2)
        self.assertEqual(len(restantes), 1)
        self.assertEqual(restantes.iloc[0]["Nº da O.S"], "300/2026-1")
        mapa = dict(zip(novas["Nº da O.S"], novas["Evento(s) vinculado(s)"]))
        self.assertEqual(mapa["100/2026-1"], "EV-MANUAL")
        self.assertEqual(mapa["200/2026-1"], "EV-AUTO")
        criterios = dict(zip(novas["Nº da O.S"], novas["Critério do vínculo"]))
        self.assertIn("Manual (analista)", criterios["100/2026-1"])
        self.assertIn("Polígono", criterios["200/2026-1"])

    def test_nao_transfere_os_para_evento_de_outra_cidade(self):
        vinculadas, nao = linhas()
        nova = {"numero_os": "300/2026-1", "municipio": "TERESINA",
                "protocolo": "EV-ERRO"}
        v, n = ambiente["aplicar_vinculos_manuais"](vinculadas, nao, [nova])
        self.assertEqual(len(v), 1)
        self.assertEqual(len(n), 2)

    def test_mesma_logica_relatorio_integrado_lista(self):
        automaticas = [{"Nº da O.S": "200/2026-1", "Cidade": "Teresina",
                        "Eventos vinculados": "EV-AUTO"}]
        outras = [{"Nº da O.S": "100/2026-1", "Cidade": "Teresina",
                   "Eventos vinculados": ""},
                  {"Nº da O.S": "300/2026-1", "Cidade": "Timon",
                   "Eventos vinculados": ""}]
        v, n = ambiente["aplicar_vinculos_manuais"](
            automaticas, outras,
            [{"numero_os": "100/2026-1", "municipio": "TERESINA", "protocolo": "EV-X"}],
            coluna_evento="Eventos vinculados", coluna_criterio=None,
        )
        self.assertEqual(len(v), 2)
        self.assertEqual(len(n), 1)
        self.assertEqual(v[-1]["Eventos vinculados"], "EV-X")

    def test_resumo_manual_por_os_e_cidade(self):
        grupo = pd.DataFrame([
            {"Nº da O.S": "100/2026-1", "Cidade": "Teresina"},
            {"Nº da O.S": "300/2026-1", "Cidade": "Timon"},
        ])
        vinculos = [
            {"numero_os": "100/2026-1", "municipio": "TERESINA",
             "evento_chave": "a" * 32, "protocolo": "EV-MANUAL"},
            {"numero_os": "100/2026-1", "municipio": "TERESINA",
             "evento_chave": "a" * 32, "protocolo": "EV-MANUAL"},
            {"numero_os": "300/2026-1", "municipio": "TERESINA",
             "evento_chave": "b" * 32, "protocolo": "EV-ERRADO"},
        ]
        resumo = ambiente["eventos_manuais_do_grupo"](vinculos, grupo)
        self.assertEqual(len(resumo), 1)
        self.assertEqual(resumo[0]["Protocolo"], "EV-MANUAL")
        self.assertEqual(resumo[0]["Status"], "Vínculo manual")

    def test_habilitacao_e_auditoria(self):
        m3 = (RAIZ / "pages" / "3_Farol_Operacional.py").read_text(encoding="utf-8")
        m7 = (RAIZ / "pages" / "7_Relatorios_Integrados.py").read_text(encoding="utf-8")
        sql = (RAIZ / "sql" / "farol_vinculos_os_manuais_20261113.sql").read_text(encoding="utf-8")
        self.assertIn("st.data_editor(", m3)
        self.assertIn("vincular_os_manual(", m3)
        self.assertIn("desfazer_vinculo_os_manual(", m3)
        self.assertIn("aplicar_vinculos_manuais(", m3)
        self.assertIn("aplicar_vinculos_manuais(", m7)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("prisma_auditoria", sql)
        self.assertIn("TO service_role", sql)


if __name__ == "__main__":
    unittest.main()
