"""Testes isolados do responsável pela última importação no Farol M3.

Executa somente as funções selecionadas da AST; não acessa Google Sheets,
Supabase ou a aplicação Streamlit publicada.
"""
import ast
import copy
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
import re
import unittest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FONTE = ROOT / "pages" / "3_Farol_Operacional.py"
ARVORE = ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))
FUNCOES = {
    "interpretar_registros_atualizacao",
    "responsavel_importacao_farol",
    "carregar_datas_atualizacao",
    "registrar_atualizacao_base",
    "exibir_atualizacoes_abaixo_mapa",
}
selecionadas = []
for no in ARVORE.body:
    if isinstance(no, ast.FunctionDef) and no.name in FUNCOES:
        no = copy.deepcopy(no)
        no.decorator_list = []  # remove somente cache_data durante o teste
        selecionadas.append(no)
assert {n.name for n in selecionadas} == FUNCOES
CAB = ["BASE", "ATUALIZADO_EM", "ATUALIZADO_POR"]


class Aba:
    def __init__(self, linhas=None, cols=2):
        self.linhas = [list(l) for l in (linhas or [])]
        self.col_count = cols
        self.historico_escritas = []

    def get_all_values(self):
        return [list(l) for l in self.linhas]

    def resize(self, *, cols):
        if cols < self.col_count:
            raise AssertionError("A estrutura não pode perder colunas")
        self.col_count = cols

    def update(self, *, range_name, values, value_input_option):
        assert value_input_option == "RAW"
        inicio = range_name.split(":")[0]
        m = re.fullmatch(r"([A-Z]+)(\d+)", inicio)
        assert m, range_name
        coluna = 0
        for letra in m.group(1):
            coluna = coluna * 26 + ord(letra) - ord("A") + 1
        coluna -= 1
        linha = int(m.group(2)) - 1
        for j, valores in enumerate(values):
            indice = linha + j
            while len(self.linhas) <= indice:
                self.linhas.append([])
            for k, valor in enumerate(valores):
                pos = coluna + k
                while len(self.linhas[indice]) <= pos:
                    self.linhas[indice].append("")
                self.linhas[indice][pos] = valor
        self.historico_escritas.append((range_name, values))

    def append_row(self, valores, *, value_input_option):
        assert value_input_option == "RAW"
        self.linhas.append(list(valores))
        self.historico_escritas.append(("append_row", valores))


class AbaNaoEncontrada(Exception):
    pass


class Planilha:
    def __init__(self, aba=None):
        self.aba = aba

    def worksheet(self, nome):
        assert nome == "FAROL_ATUALIZACOES"
        if self.aba is None:
            raise AbaNaoEncontrada()
        return self.aba

    def add_worksheet(self, *, title, rows, cols):
        assert title == "FAROL_ATUALIZACOES"
        assert cols == 3 and rows >= 2
        self.aba = Aba(cols=cols)
        return self.aba


class TesteResponsavelAtualizacoes(unittest.TestCase):
    def setUp(self):
        self.renderizados = []
        self.cache_invalidado = []
        self.planilha = Planilha()
        self.session = {"usuario_logado": "Analista de Teste"}
        self.autenticado = True
        self.ambiente = {
            "datetime": datetime,
            "FUSO_FAROL": ZoneInfo("America/Fortaleza"),
            "CABECALHO_ATUALIZACOES": CAB,
            "NOME_ABA_ATUALIZACOES": "FAROL_ATUALIZACOES",
            "SPREADSHEET_ID": "planilha-ficticia",
            "html_escape": html_escape,
            "gspread": SimpleNamespace(WorksheetNotFound=AbaNaoEncontrada),
            "obter_planilha": lambda _id: self.planilha,
            "exigir_edicao_farol": lambda: None,
            "verificar_autenticacao": lambda: self.autenticado,
            "st": SimpleNamespace(
                session_state=self.session,
                markdown=lambda texto, **kwargs: self.renderizados.append(texto),
            ),
        }
        exec(compile(ast.Module(body=selecionadas, type_ignores=[]),
                     str(FONTE), "exec"), self.ambiente)
        self.ambiente["carregar_datas_atualizacao"].clear = (
            lambda: self.cache_invalidado.append(True)
        )

    def func(self, nome, *args):
        return self.ambiente[nome](*args)

    def test_legado_duas_colunas_preserva_horarios_sem_inventar_autor(self):
        self.planilha.aba = Aba([
            ["BASE", "ATUALIZADO_EM"],
            ["PONTOS", "12/11/2026 08:00:00"],
            ["EVENTOS", "12/11/2026 10:30:00"],
        ], cols=2)
        dados = self.func("carregar_datas_atualizacao")
        self.assertEqual(dados["PONTOS"]["atualizado_em"], "12/11/2026 08:00:00")
        self.assertEqual(dados["EVENTOS"]["atualizado_em"], "12/11/2026 10:30:00")
        self.assertEqual(dados["PONTOS"]["atualizado_por"], "Responsável não registrado")
        self.assertEqual(dados["EVENTOS"]["atualizado_por"], "Responsável não registrado")

    def test_importar_pontos_amplia_planilha_sem_apagar_historico_de_eventos(self):
        self.planilha.aba = Aba([
            ["BASE", "ATUALIZADO_EM"],
            ["PONTOS", "12/11/2026 08:00:00"],
            ["EVENTOS", "12/11/2026 10:30:00"],
        ], cols=2)
        self.func("registrar_atualizacao_base", "PONTOS")
        aba = self.planilha.aba
        self.assertEqual(aba.col_count, 3)
        self.assertEqual(aba.linhas[0], CAB)
        self.assertEqual(aba.linhas[1][0], "PONTOS")
        self.assertRegex(aba.linhas[1][1], r"^\d\d/\d\d/\d{4} \d\d:\d\d:\d\d$")
        self.assertEqual(aba.linhas[1][2], "Analista de Teste")
        self.assertEqual(aba.linhas[2][:2], ["EVENTOS", "12/11/2026 10:30:00"])
        self.assertEqual(self.func("carregar_datas_atualizacao")["EVENTOS"]["atualizado_por"],
                         "Responsável não registrado")
        self.assertEqual(len(self.cache_invalidado), 1)

    def test_importar_eventos_registra_outro_usuario_e_nao_mexe_em_pontos(self):
        self.planilha.aba = Aba([
            CAB[:],
            ["PONTOS", "13/11/2026 09:15:00", "Analista A"],
            ["EVENTOS", "12/11/2026 10:30:00", "Analista B"],
        ], cols=3)
        self.session["usuario_logado"] = "Analista C"
        self.func("registrar_atualizacao_base", "EVENTOS")
        dados = self.func("carregar_datas_atualizacao")
        self.assertEqual(dados["PONTOS"]["atualizado_por"], "Analista A")
        self.assertEqual(dados["EVENTOS"]["atualizado_por"], "Analista C")
        self.assertEqual(dados["PONTOS"]["atualizado_em"], "13/11/2026 09:15:00")

    def test_planilha_nova_e_importacoes_dos_dois_tipos(self):
        self.func("registrar_atualizacao_base", "PONTOS")
        self.func("registrar_atualizacao_base", "EVENTOS")
        aba = self.planilha.aba
        self.assertEqual(aba.linhas[0], CAB)
        self.assertEqual(len(aba.linhas), 3)
        self.assertEqual(aba.linhas[1][2], "Analista de Teste")
        self.assertEqual(aba.linhas[2][2], "Analista de Teste")

    def test_sem_identificacao_nao_grava_dados_incompletos(self):
        self.session.clear()
        with self.assertRaises(PermissionError):
            self.func("registrar_atualizacao_base", "PONTOS")
        self.assertIsNone(self.planilha.aba)
        self.session["usuario_logado"] = "Analista"
        self.autenticado = False
        with self.assertRaises(PermissionError):
            self.func("registrar_atualizacao_base", "EVENTOS")
        self.assertIsNone(self.planilha.aba)

    def test_cabecalho_terceira_coluna_inesperado_nao_e_sobrescrito(self):
        aba = Aba([["BASE", "ATUALIZADO_EM", "OUTRA_COLUNA"],
                   ["PONTOS", "12/11/2026", "conteudo"]], cols=3)
        self.planilha.aba = aba
        with self.assertRaises(ValueError):
            self.func("registrar_atualizacao_base", "PONTOS")
        self.assertEqual(aba.linhas[1][2], "conteudo")
        self.assertFalse(aba.historico_escritas)

    def test_exibe_nome_escapando_html_malicioso(self):
        self.planilha.aba = Aba([
            CAB[:],
            ["PONTOS", "13/11/2026 08:30:00", "<script>alert('x')</script>"],
            ["EVENTOS", "13/11/2026 09:30:00", "Analista B"],
        ], cols=3)
        self.func("exibir_atualizacoes_abaixo_mapa")
        self.assertEqual(len(self.renderizados), 1)
        resultado = self.renderizados[0]
        self.assertIn("O.S. — Atualizado em 13/11/2026 08:30:00", resultado)
        self.assertIn("Eventos — Atualizado em 13/11/2026 09:30:00", resultado)
        self.assertIn("Por: &lt;script&gt;", resultado)
        self.assertNotIn("<script>", resultado)
        self.assertIn("Por: Analista B", resultado)

    def test_limpeza_respeita_coluna_atualizado_por(self):
        fonte = FONTE.read_text(encoding="utf-8")
        self.assertIn("(NOME_ABA_ATUALIZACOES, CABECALHO_ATUALIZACOES)", fonte)
        self.assertIn('registrar_atualizacao_base("PONTOS")', fonte)
        self.assertIn('registrar_atualizacao_base("EVENTOS")', fonte)
        self.assertIn('st.session_state.get("usuario_logado")', fonte)


if __name__ == "__main__":
    unittest.main()
