"""Regressões da organização do Módulo 9 como Gestão de Campo.

A barra lateral concentra os acessos e a administração sem alterar a
funcionalidade dos poços, fechamentos e filtros de data.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "10_Gestao_de_Ativos.py"
APP = ROOT / "app.py"


class TestGestaoCampoSidebar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pagina = PAGE.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")

    def test_nome_renomeado_na_pagina_e_no_menu_principal(self):
        self.assertIn('page_title="Gestão de Campo | PRISMA"', self.pagina)
        self.assertIn('st.title("Gestão de Campo")', self.pagina)
        self.assertIn('"numero": "9", "titulo": "Gestão de Campo"', self.app)
        self.assertIn('"botao": "Acessar Gestão de Campo"', self.app)
        self.assertIn('"chave": "acessar_gestao_ativos"', self.app)
        self.assertIn('"pagina": "pages/10_Gestao_de_Ativos.py"', self.app)
        self.assertNotIn('"titulo": "Gestão de Ativos"', self.app)

    def test_quatro_acessos_somente_na_sidebar(self):
        inicio = self.pagina.index('with st.sidebar:\n    st.markdown("### Gestão de Campo")')
        fim = self.pagina.index('if not HISTORICO_DISPONIVEL:', inicio)
        sidebar = self.pagina[inicio:fim]
        for trecho in (
            'st.button("← Retornar ao Menu Principal"',
            'st.button("＋ Novo Poço"',
            'st.button("🚧 Novo Fechamento"',
            'st.button("⚙️ Administração"',
            'st.switch_page("app.py")',
            '_dialogo_novo_fechamento(',
            '_dialogo_administracao()',
        ):
            self.assertIn(trecho, sidebar)
        self.assertNotIn('with col_novo:', self.pagina)
        self.assertNotIn('with col_voltar:', self.pagina)
        self.assertEqual(self.pagina.count('st.button("🚧 Novo Fechamento"'), 1)
        self.assertEqual(self.pagina.count('st.button("＋ Novo Poço"'), 1)
        self.assertEqual(self.pagina.count('st.button("⚙️ Administração"'), 1)

    def test_administracao_em_modal_e_protegida(self):
        self.assertIn('@st.dialog("⚙️ Administração — Gestão de Campo"', self.pagina)
        inicio = self.pagina.index('def _dialogo_administracao():')
        fim = self.pagina.index('st.title("Gestão de Campo")', inicio)
        admin = self.pagina[inicio:fim]
        self.assertIn('if st.session_state.get("perfil") != "admin":', admin)
        self.assertIn('with st.form("ga_confirmacao_reset_geral"):', admin)
        self.assertIn('frase_reset != CONFIRMACAO_RESET_ATIVOS', admin)
        self.assertIn('resumo_reset = resetar_fichas_e_historico(frase_reset)', admin)
        self.assertIn('if st.session_state.get("perfil") == "admin":', self.pagina)

    def test_novos_cadastros_e_data_sidebar_preservados(self):
        self.assertIn('st.session_state["ga_painel"] = {', self.pagina)
        self.assertIn('"tipo": "novo", "coordenadas": None', self.pagina)
        self.assertIn('st.session_state.pop("ga_fechamento_lat_modal", None)', self.pagina)
        self.assertIn('st.session_state.pop("ga_fechamento_lon_modal", None)', self.pagina)
        self.assertIn('st.markdown("### 🚧 Fechamentos / reparos")', self.pagina)
        self.assertIn('"Data dos fechamentos"', self.pagina)
        self.assertIn('"Exibir todas as datas"', self.pagina)
        self.assertEqual(self.pagina.count("with st.sidebar:"), 2)

    def test_sintaxe(self):
        ast.parse(self.pagina)
        ast.parse(self.app)


if __name__ == "__main__":
    unittest.main()
