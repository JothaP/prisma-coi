"""Regressões estáticas e temporais do Farol Histórico (sem acesso ao banco)."""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HISTORICO = ROOT / "pages" / "12_Farol_Historico.py"
MODULO3 = ROOT / "pages" / "3_Farol_Operacional.py"
APP = ROOT / "app.py"
BACKEND = ROOT / "prisma_backend.py"
MANUTENCAO = ROOT / "prisma_manutencao.py"
SQL = ROOT / "sql" / "prisma_farol_historico_permissoes.sql"


class TesteFarolHistorico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codigo = HISTORICO.read_text(encoding="utf-8")
        cls.arvore = ast.parse(cls.codigo)
        cls.modulo3 = MODULO3.read_text(encoding="utf-8")

    def test_clone_do_motor_geografico_m3_sem_importar_pagina_de_producao(self):
        funcoes = {n.name for n in cls_nodes(self.arvore)}
        for nome in (
            "criar_concentracoes", "criar_mapa_panorama_v2",
            "pontos_proximos_concentracoes", "dados_relatorio_concentracao",
            "gerar_pdf_concentracao", "modal_configurar_relatorio",
            "preparar_pontos", "preparar_eventos",
        ):
            self.assertIn(nome, funcoes)
        self.assertIn("def criar_concentracoes(", self.modulo3)
        self.assertNotIn('from pages.3_Farol_Operacional import', self.codigo)

    def test_isolamento_de_dados_e_permissoes(self):
        self.assertIn('_exigir_acesso_historico("historico")', self.codigo)
        self.assertIn('PODE_EDITAR_FAROL = False', self.codigo)
        self.assertIn('SPREADSHEET_ID = ""', self.codigo)
        self.assertIn('obter_planilha = _producao_bloqueada', self.codigo)
        self.assertIn('obter_cliente_google = _producao_bloqueada', self.codigo)
        self.assertIn('listar_vinculos_os_manuais(*args, **kwargs)', self.codigo)
        self.assertIn('st.session_state["fh_pontos_base"]', self.codigo)
        self.assertIn('validar_janela=False', self.codigo)
        self.assertNotIn('st.title("Farol Operacional")', self.codigo)

    def test_mapa_historico_representa_eventos_concluidos(self):
        """O módulo diário suprime encerrados; o histórico deve mostrá-los."""
        funcs = {node.name: ast.get_source_segment(self.codigo, node)
                 for node in cls_nodes(self.arvore)}
        georreferenciar = funcs["eventos_georreferenciados"]
        camada = funcs["adicionar_camada_eventos"]
        self.assertNotIn('if categoria_status == "Finalizado":', georreferenciar)
        self.assertNotIn('== "Finalizado":\n            continue', camada)
        self.assertIn('classificar_status_evento(evento.get("Status", ""))', georreferenciar)
        self.assertIn("def secao_pdf_acompanhamento(*args, **kwargs)", self.codigo)

    def test_selecao_do_periodo_nao_depende_do_relogio_atual(self):
        funcoes = {n.name: ast.get_source_segment(self.codigo, n)
                   for n in cls_nodes(self.arvore)}
        trecho = funcoes["classificar_os_por_evento"]
        self.assertIn('st.session_state.get("fh_periodo_ativo")', trecho)
        self.assertIn("datetime.combine(limite_estudo[1]", trecho)

    def test_hub_permissoes_manutencao_e_sql(self):
        self.assertIn('"numero": "historico"', APP.read_text(encoding="utf-8"))
        self.assertIn('historico":"Módulo 3B — Farol Histórico"', BACKEND.read_text(encoding="utf-8"))
        self.assertIn('"historico": "Módulo 3B — Farol Histórico"', MANUTENCAO.read_text(encoding="utf-8"))
        self.assertIn("prisma_permissoes_modulo_check", SQL.read_text(encoding="utf-8"))
        self.assertIn("manutencao_escopo_modulo", SQL.read_text(encoding="utf-8"))

    def test_sobreposicao_temporal_e_rejeicao_de_evento_fora(self):
        funcao = next(n for n in self.arvore.body if isinstance(n, ast.FunctionDef)
                      and n.name == "evento_pertinente_data_analise")
        namespace = {
            "pd": pd,
            "st": SimpleNamespace(session_state={
                "fh_periodo_ativo": (date(2026, 2, 1), date(2026, 2, 28))
            }),
            "interpretar_data_operacional": lambda x:
                pd.to_datetime(x, dayfirst=True, errors="coerce"),
            "classificar_status_evento": lambda x:
                ("Finalizado", "") if x == "Finalizado" else ("Aberto", ""),
        }
        exec(compile(ast.Module(body=[funcao], type_ignores=[]),
                     str(HISTORICO), "exec"), namespace)
        aceita = namespace["evento_pertinente_data_analise"]
        self.assertTrue(aceita({
            "Início": "31/01/2026 22:00", "Término Real": "02/02/2026 14:00",
            "Status": "Finalizado",
        }))
        self.assertFalse(aceita({
            "Início": "20/01/2026 12:00", "Término Real": "21/01/2026 16:00",
            "Status": "Finalizado",
        }))
        self.assertFalse(aceita({
            "Início": "01/03/2026 15:00", "Término Real": "",
            "Status": "Aberto",
        }))
        self.assertTrue(aceita({
            "Início": "16/02/2026 12:00", "Término Real": "",
            "Status": "Finalizado",
        }))


def cls_nodes(tree):
    return [node for node in tree.body if isinstance(node, ast.FunctionDef)]


if __name__ == "__main__":
    unittest.main()
