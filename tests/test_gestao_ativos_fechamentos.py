"""Regressões isoladas dos fechamentos de rede do Módulo 9.

Sem importar Streamlit nem acessar o Supabase nos testes.
"""
import ast
from datetime import date
import math
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
import uuid

from gestao_ativos_relatorio import (
    preparar_fechamentos_relatorio, gerar_pontos_atencao,
)


RAIZ = Path(__file__).resolve().parents[1]
BACKEND = RAIZ / "gestao_ativos_fechamentos.py"
PAGINA = RAIZ / "pages" / "10_Gestao_de_Ativos.py"


def backend_isolado(chamadas):
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    nomes = {
        "_autor", "_uuid", "_leitura", "_campos", "_coordenadas",
        "criar_fechamento", "editar_fechamento", "listar_fechamentos",
        "listar_fechamentos_relatorio", "obter_fechamento",
    }
    defs = [n for n in arvore.body if (
        isinstance(n, ast.FunctionDef) and n.name in nomes
    ) or (
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in
                ("STATUS_FECHAMENTO", "LIMITE_MARCADORES_FECHAMENTOS")
                for t in n.targets)
    )]
    assert len(defs) == len(nomes) + 2
    class ErroPRISMA(Exception):
        pass
    def tabela(nome, **kwargs):
        chamadas.append((nome, kwargs))
        return [{"id": "12345678-1234-1234-1234-123456789012",
                 "atualizado_em": "2026-11-17T12:00:00Z"}]
    contexto = {
        "date": date, "math": math, "re": re, "uuid": uuid,
        "st": SimpleNamespace(session_state={
            "prisma_usuario_id": "12345678-1234-1234-1234-123456789012",
        }),
        "pode_executar_acao": lambda *args: True,
        "tem_acesso_modulo": lambda *args: True,
        "_tabela": tabela, "ErroPRISMA": ErroPRISMA,
    }
    exec(compile(ast.Module(body=defs, type_ignores=[]), str(BACKEND),
                 "exec"), contexto)
    return contexto


class TestFechamentosRede(unittest.TestCase):
    def setUp(self):
        self.chamadas = []
        self.ctx = backend_isolado(self.chamadas)
        self.dados = {
            "lider": "Ana", "equipe": "Equipe 08",
            "matricula": "000112233", "os": "000900400885",
            "bairro": "Dirceu Arcoverde II", "municipio": "TERESINA",
            "data": date(2026, 11, 17), "status": "em_andamento",
        }

    def test_formulario_tres_linhas_na_ordem_solicitada(self):
        codigo = PAGINA.read_text(encoding="utf-8")
        inicio = codigo.index("def _painel_fechamento(")
        fim = codigo.index('st.title("Gestão de Ativos")', inicio)
        bloco = codigo[inicio:fim]
        pos = [bloco.index(f'"{campo}"') for campo in
               ("Líder", "Equipe", "Matrícula", "O.S.", "Bairro")]
        self.assertEqual(pos, sorted(pos))
        self.assertLess(bloco.index('st.date_input('),
                        bloco.index('status_reparo = st.selectbox('))
        self.assertIn("c1, c2 = st.columns(2)", bloco)
        self.assertIn("c1, c2, c3 = st.columns(3)", bloco)
        self.assertIn('st.date_input(\n                    "Data"', bloco)
        self.assertIn('"Status", codigos', bloco)

    def test_marcador_filtros_e_edicao_independentes_dos_pocos(self):
        pagina = PAGINA.read_text(encoding="utf-8")
        self.assertIn("🚧 FECHAMENTOS / REPAROS DE REDE", (
            RAIZ / "gestao_ativos_relatorio.py").read_text(encoding="utf-8"))
        self.assertIn('st.selectbox(\n        "Município • Fechamentos"', pagina)
        self.assertIn('st.text_input(\n        "Bairro • Fechamentos"', pagina)
        self.assertIn('st.selectbox(\n        "Status • Fechamentos"', pagina)
        self.assertIn('"ga_pesquisa_reparo_filtro"', pagina)
        self.assertIn('folium.DivIcon(', pagina)
        self.assertIn("PRISMA_FECHAMENTO=", pagina)
        self.assertIn("modo_fechamento", pagina)
        self.assertIn("if novo_click and achado_reparo:", pagina)
        self.assertIn('elif painel.get("tipo") == "fechamento"', pagina)
        self.assertIn("criar_fechamento(campos_reparo, lat, lon)", pagina)
        self.assertIn("editar_fechamento(", pagina)

    def test_campos_e_coordenadas_validos(self):
        valido = self.ctx["_campos"](self.dados)
        self.assertEqual(valido["os"], "000900400885")
        self.assertEqual(valido["matricula"], "000112233")
        self.assertEqual(valido["data"], "2026-11-17")
        self.assertEqual(self.ctx["_coordenadas"](-5.09, -42.8), (-5.09, -42.8))

    def test_bloqueia_invalidos_sem_gravar(self):
        for atualizacao in (
            {"status": "operando"}, {"bairro": ""},
            {"municipio": ""}, {"data": "invalid"},
        ):
            with self.subTest(atualizacao=atualizacao):
                with self.assertRaises(self.ctx["ErroPRISMA"]):
                    self.ctx["_campos"]({**self.dados, **atualizacao})
        for lat, lon in ((91, 0), (0, -181), ("nan", 2)):
            with self.assertRaises(self.ctx["ErroPRISMA"]):
                self.ctx["_coordenadas"](lat, lon)
        self.assertEqual(self.chamadas, [])

    def test_criar_e_editar_somente_tabela_de_fechamentos(self):
        criado = self.ctx["criar_fechamento"](self.dados, -5.09, -42.8)
        self.assertTrue(criado.get("id"))
        nome, options = self.chamadas[-1]
        self.assertEqual(nome, "prisma_fechamentos_rede")
        self.assertEqual(options["method"], "POST")
        self.assertEqual(options["payload"]["matricula"], "000112233")
        self.assertEqual(options["payload"]["status"], "em_andamento")
        self.assertEqual(options["payload"]["latitude"], -5.09)
        self.ctx["editar_fechamento"](
            criado["id"], self.dados, "2026-11-17T12:00:00Z")
        nome, options = self.chamadas[-1]
        self.assertEqual(nome, "prisma_fechamentos_rede")
        self.assertEqual(options["method"], "PATCH")
        self.assertEqual(options["params"]["atualizado_em"],
                         "eq.2026-11-17T12:00:00Z")
        self.assertNotIn("prisma_ativos", str(self.chamadas))
        with self.assertRaises(self.ctx["ErroPRISMA"]):
            self.ctx["editar_fechamento"](criado["id"], self.dados, None)

    def test_consulta_paginada_e_filtros_sem_gravacao(self):
        self.ctx["listar_fechamentos"](
            "TERESINA", "Dirceu", "900400885", "em_andamento")
        _, kwargs = self.chamadas[-1]
        filtros = kwargs["params"]
        self.assertEqual(filtros["status"], "eq.em_andamento")
        self.assertEqual(filtros["bairro"], "ilike.*Dirceu*")
        self.assertIn("os.ilike.*900400885*", filtros["or"])
        self.assertEqual(filtros["municipio"], "ilike.TERESINA")
        self.ctx["listar_fechamentos_relatorio"]("TERESINA")
        _, kwargs = self.chamadas[-1]
        self.assertEqual(kwargs["params"]["limit"], "1000")
        self.assertEqual(kwargs["params"]["municipio"], "ilike.TERESINA")
        self.assertTrue(all(
            x[1].get("method", "GET") == "GET" for x in self.chamadas
        ))

    def test_relatorio_separado_por_status_com_campos_da_ficha(self):
        itens = preparar_fechamentos_relatorio([
            {**self.dados, "data": "2026-11-17"},
            {**self.dados, "bairro": "Centro", "status": "pendente",
             "os": "000900001111"},
            {**self.dados, "status": "concluido", "bairro": "Sul"},
        ])
        self.assertEqual(len(itens), 3)
        self.assertTrue(itens[0]["Incluir"])
        self.assertFalse(itens[2]["Incluir"])
        texto = gerar_pontos_atencao(
            "Teresina", date(2026, 11, 17), [],
            fechamentos=itens,
        )
        self.assertIn("🚧 FECHAMENTOS / REPAROS DE REDE", texto)
        self.assertIn("🔴 Pendente:", texto)
        self.assertIn("🟡 Em andamento:", texto)
        self.assertIn("🚧 1. Bairro: Centro", texto)
        self.assertIn("🚧 1. Bairro: Dirceu Arcoverde II", texto)
        self.assertIn("Líder: Ana", texto)
        self.assertIn("Equipe: Equipe 08", texto)
        self.assertIn("Matrícula: 000112233", texto)
        self.assertIn("O.S.: 000900400885", texto)
        self.assertIn("Data: 17/11/2026", texto)
        self.assertNotIn("Bairro: Sul", texto)
        self.assertNotIn("OBSERVAÇÕES POÇOS:", texto)

    def test_sintaxe(self):
        ast.parse(PAGINA.read_text(encoding="utf-8"))
        ast.parse(BACKEND.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
