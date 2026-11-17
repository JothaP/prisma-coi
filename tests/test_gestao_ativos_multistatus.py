"""Testa filtros de múltiplos status no Módulo Gestão de Campo.

Sem conexão ao Supabase ou importação do Streamlit: inspeciona apenas os
parâmetros de consulta enviados ao backend por funções isoladas.
"""
import ast
from datetime import date
from pathlib import Path
import re
import unittest

RAIZ = Path(__file__).resolve().parents[1]
PAGINA = RAIZ / "pages" / "10_Gestao_de_Ativos.py"
BACKEND_POCOS = RAIZ / "gestao_ativos_backend.py"
BACKEND_REDE = RAIZ / "gestao_ativos_fechamentos.py"


def montar_backend(caminho, nome_funcao, nomes_constantes):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    partes = [
        item for item in arvore.body
        if ((isinstance(item, ast.Assign) and any(
            isinstance(alvo, ast.Name) and alvo.id in nomes_constantes
            for alvo in item.targets))
            or (isinstance(item, ast.FunctionDef) and item.name == nome_funcao))
    ]
    assert len(partes) == len(nomes_constantes) + 1
    class ErroPRISMA(Exception):
        pass
    chamadas = []
    def tabela(nome, **kwargs):
        chamadas.append((nome, kwargs))
        return []
    escopo = {
        "ErroPRISMA": ErroPRISMA, "_exigir_leitura": lambda: None,
        "_leitura": lambda: None, "_tabela": tabela, "re": re, "date": date,
    }
    exec(compile(ast.Module(body=partes, type_ignores=[]),
                 str(caminho), "exec"), escopo)
    return escopo, chamadas


class TestFiltrosMultisselecao(unittest.TestCase):
    def setUp(self):
        self.pocos, self.chamadas_pocos = montar_backend(
            BACKEND_POCOS, "listar_ativos", ("STATUS", "MAX_MARCADORES")
        )
        self.rede, self.chamadas_rede = montar_backend(
            BACKEND_REDE, "listar_fechamentos",
            ("STATUS_FECHAMENTO", "LIMITE_MARCADORES_FECHAMENTOS")
        )

    def test_pocos_filtra_duas_ou_mais_situacoes(self):
        self.pocos["listar_ativos"](
            "TERESINA", "", ("em_observacao", "em_manutencao")
        )
        nome, argumentos = self.chamadas_pocos[-1]
        self.assertEqual(nome, "prisma_ativos")
        self.assertEqual(
            argumentos["params"]["status"],
            "in.(em_manutencao,em_observacao)"
        )
        self.assertEqual(argumentos["params"]["municipio"], "eq.TERESINA")

    def test_pocos_sem_selecao_mostra_todos_inclusive_sem_status(self):
        self.pocos["listar_ativos"]("TERESINA", "", ())
        self.assertNotIn("status", self.chamadas_pocos[-1][1]["params"])
        self.pocos["listar_ativos"]("TERESINA", "", None)
        self.assertNotIn("status", self.chamadas_pocos[-1][1]["params"])

    def test_pocos_um_status_e_validacao(self):
        self.pocos["listar_ativos"]("TERESINA", "", ("parado",))
        self.assertEqual(
            self.chamadas_pocos[-1][1]["params"]["status"], "eq.parado")
        self.pocos["listar_ativos"]("TERESINA", "", "operando")
        self.assertEqual(
            self.chamadas_pocos[-1][1]["params"]["status"], "eq.operando")
        quantidade = len(self.chamadas_pocos)
        with self.assertRaises(self.pocos["ErroPRISMA"]):
            self.pocos["listar_ativos"]("TERESINA", "", ["operando", "invalido"])
        self.assertEqual(len(self.chamadas_pocos), quantidade)

    def test_fechamentos_varios_status_e_mesma_data(self):
        self.rede["listar_fechamentos"](
            "TERESINA", "Centro", "", ("pendente", "em_andamento"),
            date(2026, 11, 17)
        )
        nome, argumentos = self.chamadas_rede[-1]
        self.assertEqual(nome, "prisma_fechamentos_rede")
        self.assertEqual(
            argumentos["params"]["status"], "in.(em_andamento,pendente)"
        )
        self.assertEqual(argumentos["params"]["bairro"], "ilike.*Centro*")
        self.assertEqual(argumentos["params"]["data"], "eq.2026-11-17")

    def test_fechamentos_encerrado_inclui_legado(self):
        self.rede["listar_fechamentos"](
            "TERESINA", "", "", ("encerrado",)
        )
        self.assertEqual(
            self.chamadas_rede[-1][1]["params"]["status"],
            "in.(concluido,encerrado)"
        )

    def test_fechamentos_nenhuma_selecao_e_validacao(self):
        self.rede["listar_fechamentos"]("TERESINA", "", "", ())
        self.assertNotIn("status", self.chamadas_rede[-1][1]["params"])
        self.rede["listar_fechamentos"]("TERESINA", "", "", "pendente")
        self.assertEqual(
            self.chamadas_rede[-1][1]["params"]["status"], "eq.pendente"
        )
        chamadas = len(self.chamadas_rede)
        with self.assertRaises(self.rede["ErroPRISMA"]):
            self.rede["listar_fechamentos"]("TERESINA", "", "", ("pendente", "invalido"))
        self.assertEqual(len(self.chamadas_rede), chamadas)

    def test_interface_permite_multisselecao_independente(self):
        codigo = PAGINA.read_text(encoding="utf-8")
        self.assertIn('st.multiselect(\n        "Status • Poços"', codigo)
        self.assertIn('st.multiselect(\n        "Status • Fechamentos"', codigo)
        self.assertIn('registros = _listar(municipio, pesquisa, tuple(status))', codigo)
        self.assertIn('pesquisa_reparo_filtro, tuple(status_reparo_filtro),', codigo)
        self.assertIn('placeholder="Todos os status"', codigo)
        self.assertNotIn('status = st.selectbox("Status",', codigo)

    def test_sintaxe(self):
        ast.parse(PAGINA.read_text(encoding="utf-8"))
        ast.parse(BACKEND_POCOS.read_text(encoding="utf-8"))
        ast.parse(BACKEND_REDE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
