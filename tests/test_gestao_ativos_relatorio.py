"""Testes de regressão do relatório textual do Módulo 9.

A geração nunca altera status ou fichas no Supabase.
"""
from datetime import date
from pathlib import Path
import ast
import unittest

from gestao_ativos_relatorio import (
    preparar_itens_relatorio, gerar_pontos_atencao,
)


RAIZ = Path(__file__).resolve().parents[1]
PAGINA = RAIZ / "pages" / "10_Gestao_de_Ativos.py"
BACKEND = RAIZ / "gestao_ativos_backend.py"


class TestRelatorioPontosAtencao(unittest.TestCase):
    def setUp(self):
        self.ativos = [
            {
                "planta": "PL-THE-PCR0047", "nome": "THE-038",
                "municipio": "TERESINA", "status": "em_manutencao",
                "observacao": "Perda de vazão e sem pressão no barrilete",
                "ultima_os_operacao": None,
                "ultima_os_eletromecanica": "900400419",
            },
            {
                "planta": "PL-THE-PCO0039", "nome": "THE-032",
                "municipio": "TERESINA", "status": "em_observacao",
                "observacao": "Perda de vazão nas medições",
                "ultima_os_operacao": None,
                "ultima_os_eletromecanica": "000900400885",
            },
            {
                "planta": "PL-THE-ETR0126", "nome": "(THE 208) Booster",
                "municipio": "TERESINA", "status": "operando",
                "observacao": "Inspeção com pitometria",
                "ultima_os_eletromecanica": "900392871",
            },
            {
                "planta": "PL-THE-PCO0049", "nome": "THE-999",
                "municipio": "TERESINA", "status": None,
                "observacao": None,
                "ultima_os_eletromecanica": None,
            },
        ]

    def test_seleciona_fichas_e_exclui_operando_por_padrao(self):
        itens = preparar_itens_relatorio(self.ativos)
        self.assertEqual(len(itens), 2)
        self.assertEqual([x["Poço / ativo"] for x in itens],
                         ["THE 038", "THE 032"])
        self.assertTrue(all(x["Incluir"] for x in itens))
        self.assertEqual(itens[1]["O.S."], "000900400885")
        self.assertEqual(itens[1]["Situação"], "Pendente")
        self.assertEqual(itens[1]["Seção"], "OBSERVAÇÕES POÇOS")

    def test_incluir_operando_exige_marcação_manual(self):
        itens = preparar_itens_relatorio(self.ativos, incluir_operando=True)
        self.assertEqual(len(itens), 3)
        self.assertFalse(itens[2]["Incluir"])
        self.assertEqual(itens[2]["Poço / ativo"], "(THE 208) Booster")

    def test_status_nao_determina_situacao_nem_secao(self):
        itens = preparar_itens_relatorio(self.ativos)
        # Em manutenção e Em Observação têm o mesmo valor inicial para revisão.
        self.assertEqual(itens[0]["Situação"], itens[1]["Situação"])
        itens[0]["Seção"] = "ÁGUA"
        itens[0]["Situação"] = "Em andamento"
        itens[1]["Nota"] = "100515579"
        texto = gerar_pontos_atencao("Teresina", date(2026, 11, 17), itens)
        self.assertIn("📍 PONTOS DE ATENÇÃO – TERESINA | 17/11/2026", texto)
        self.assertIn("🟡 Em andamento | 🔴 Pendente", texto)
        self.assertIn("💧 ÁGUA", texto)
        self.assertIn("🟡 THE 038 - Perda de vazão e sem pressão no barrilete", texto)
        self.assertIn("Ordem: 900400419", texto)
        self.assertIn("⚠️ OBSERVAÇÕES POÇOS:", texto)
        self.assertIn("🔴 1. THE 032 - Perda de vazão nas medições", texto)
        self.assertIn("Ordem: 000900400885", texto)
        self.assertIn("Nota: 100515579", texto)
        self.assertNotIn("THE 208", texto)

    def test_pode_adicionar_linha_manual_e_ordenar(self):
        itens = preparar_itens_relatorio(self.ativos)
        itens[0]["Ordem"] = 3
        itens[1]["Ordem"] = 1
        itens.append({
            "Incluir": True, "Ordem": 2,
            "Seção": "OBSERVAÇÕES POÇOS", "Situação": "Pendente",
            "Poço / ativo": "THE 207",
            "Observação": "Inspeção conjunta com pitometria",
            "O.S.": "900392870", "Nota": "100522822",
        })
        texto = gerar_pontos_atencao("TERESINA", date(2026, 11, 17), itens)
        self.assertLess(texto.index("🔴 1. THE 032"), texto.index("🔴 2. THE 207"))
        self.assertLess(texto.index("🔴 2. THE 207"), texto.index("🔴 3. THE 038"))

    def test_sem_duplicar_ordem_e_sem_misturar_operando(self):
        item = self.ativos[0].copy()
        item["ultima_os_operacao"] = "900400419"
        self.assertEqual(preparar_itens_relatorio([item])[0]["O.S."], "900400419")
        item["ultima_os_operacao"] = "900123456"
        self.assertEqual(
            preparar_itens_relatorio([item])[0]["O.S."],
            "900123456 / 900400419",
        )
        itens = preparar_itens_relatorio(self.ativos, incluir_operando=True)
        texto = gerar_pontos_atencao("TERESINA", date(2026, 11, 17), itens)
        self.assertNotIn("THE 208", texto)

    def test_ui_e_backend_apenas_leitura(self):
        pagina = PAGINA.read_text(encoding="utf-8")
        backend = BACKEND.read_text(encoding="utf-8")
        self.assertIn('with st.expander("📋 Gerar relatório — Pontos de Atenção"', pagina)
        self.assertIn('with st.form("ga_relatorio_editor_form"):', pagina)
        self.assertIn('num_rows="dynamic"', pagina)
        self.assertIn('gerar_pontos_atencao(', pagina)
        self.assertIn('st.download_button(', pagina)
        self.assertIn('def listar_fichas_para_relatorio(municipio:', backend)
        funcao = backend.split('def listar_fichas_para_relatorio(', 1)[1].split(
            '\ndef obter_ativo(', 1)[0]
        self.assertIn('_exigir_leitura()', funcao)
        self.assertIn('"municipio": "ilike." + municipio', funcao)
        self.assertIn('"limit": str(limite)', funcao)
        self.assertNotIn('method="POST"', funcao)
        self.assertNotIn('method="PATCH"', funcao)
        self.assertNotIn('method="DELETE"', funcao)

    def test_sintaxe_arquivos(self):
        ast.parse(PAGINA.read_text(encoding="utf-8"))
        ast.parse(BACKEND.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
