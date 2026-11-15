"""Regressão do lote de Eventos (Módulo 4.1).

Verifica cruzamento real por área, agrupamento de bairros, O.S. distintas,
datas/horários e layout de exportação sem iniciar Streamlit ou Sheets.
"""
import ast
from datetime import datetime
from pathlib import Path
import re
import unicodedata
import unittest

import pandas as pd


FONTE = (Path(__file__).resolve().parents[1]
         / "gerador_lotes" / "ferramentas" / "eventos.py")
ARVORE = ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))
FUNCOES = {
    "normalizar_texto", "encontrar_coluna", "converter_datetime_evento",
    "normalizar_matricula", "eh_todo_municipio",
    "separar_areas_impactadas", "normalizar_nome_area_com_qualificadores",
    "tokenizar_area", "areas_evento_correspondem", "preparar_eventos",
    "formatar_periodo_evento", "limpar_texto_observacao", "montar_observacao",
    "limpar_bairro_lote_eventos", "consolidar_linhas_lote_eventos",
    "cruzar_eventos_com_backlog",
}
CONSTANTES = {
    "REGRA_PADRAO", "REGRAS_JANELA", "PADROES_TODO_MUNICIPIO",
    "SINONIMOS_AREAS", "COLUNAS_LOTE",
}
ambiente = {
    "pd": pd, "re": re, "datetime": datetime, "unicodedata": unicodedata,
}
funcs = []
for no in ARVORE.body:
    if isinstance(no, ast.Assign):
        nomes = {x.id for x in no.targets if isinstance(x, ast.Name)}
        for nome in nomes & CONSTANTES:
            ambiente[nome] = ast.literal_eval(no.value)
    if isinstance(no, ast.FunctionDef) and no.name in FUNCOES:
        funcs.append(no)
assert {x.name for x in funcs} == FUNCOES
exec(compile(ast.Module(body=funcs, type_ignores=[]), str(FONTE), "exec"), ambiente)

COLUNAS_LOTE = ambiente["COLUNAS_LOTE"]


def evento(area, *, cidade="Teresina", inicio="13/11/2026 18:00",
           fim="14/11/2026 09:08", servico="Falta de Energia Elétrica",
           ativo="PL-THE-EAT0022"):
    return {
        "Cidade": cidade,
        "Áreas Impactadas": area,
        "Início": inicio,
        "Término Real": fim,
        "Serviço": servico,
        "Código do Ativo": ativo,
    }


def ordem(numero, bairro, *, cidade="Teresina", inicio_sla="13/11/2026 19:00",
          matricula=None):
    return {
        "CIDADE": cidade,
        "BAIRRO": bairro,
        "INÍCIO DO SLA": inicio_sla,
        "COD. PROTOCOLO ORIGEM": str(numero),
        "MATRICULA": str(matricula if matricula is not None else numero),
    }


def gerar(eventos, ordens):
    preparados, avisos, estatisticas = ambiente["preparar_eventos"](
        pd.DataFrame(eventos)
    )
    assert not avisos, avisos
    assert estatisticas["validos"] == len(eventos), estatisticas
    return ambiente["cruzar_eventos_com_backlog"](
        preparados, pd.DataFrame(ordens), "THE"
    )


class TesteLotesEventosConsolidados(unittest.TestCase):
    def test_amostra_quatro_bairros_uma_linha_seis_os(self):
        eventos = [
            evento("MORROS"),
            evento("NOVO URUGUAI"),
            evento("SATELITE PARCIAL"),
            evento("VALE QUEM TEM"),
        ]
        ordens = [
            ordem(1001, "Morros"), ordem(1002, "Morros"),
            ordem(1003, "Novo Uruguai"),
            ordem(1004, "Satélite"),
            ordem(1005, "Vale Quem Tem"), ordem(1006, "Vale Quem Tem"),
        ]
        lote, qtd_backlog, avisos, qtd_os, protocolos = gerar(eventos, ordens)
        self.assertEqual(len(lote), 1)
        self.assertEqual(lote.columns.tolist(), COLUNAS_LOTE)
        linha = lote.iloc[0]
        self.assertEqual(int(linha["Quant. de O.S"]), 6)
        self.assertEqual(linha["Cidade"], "TERESINA")
        self.assertEqual(
            linha["Bairro"], "MORROS, NOVO URUGUAI, SATELITE, VALE QUEM TEM"
        )
        self.assertNotIn("PARCIAL", linha["Bairro"])
        self.assertEqual(linha["Ano"], 2026)
        self.assertEqual(linha["Mês"], 11)
        self.assertEqual(linha["Dia"], "13 a 14")
        self.assertEqual(linha["Hora Inicial"], "18:00")
        self.assertEqual(linha["Hora Final"], "09:08")
        self.assertEqual(
            linha["Observação"], "Falta de Energia Elétrica/PL-THE-EAT0022"
        )
        self.assertEqual(qtd_os, 6)
        self.assertEqual(qtd_backlog, 6)
        self.assertEqual(len(protocolos), 6)
        self.assertTrue(all("Falta de Energia Elétrica/PL-THE-EAT0022" in p
                            for p in protocolos))

    def test_os_repetida_em_areas_equivalentes_conta_uma_vez(self):
        lote, _, _, qtd_os, protocolos = gerar(
            [evento("MORROS, MORROS (PARCIAL), MORROS PARCIALMENTE")],
            [ordem(2001, "Morros"), ordem(2002, "Morros")]
        )
        self.assertEqual(len(lote), 1)
        self.assertEqual(lote.iloc[0]["Bairro"], "MORROS")
        self.assertEqual(int(lote.iloc[0]["Quant. de O.S"]), 2)
        self.assertEqual(qtd_os, 2)
        self.assertEqual(len(protocolos), 2)

    def test_mesma_observacao_mas_horario_diferente_nao_agrupa(self):
        eventos = [
            evento("MORROS"),
            evento("NOVO URUGUAI", inicio="13/11/2026 18:01"),
        ]
        ordens = [ordem(3001, "Morros"), ordem(3002, "Novo Uruguai")]
        lote, *_ = gerar(eventos, ordens)
        self.assertEqual(len(lote), 2)
        self.assertEqual(lote["Hora Inicial"].tolist(), ["18:00", "18:01"])

    def test_mesma_observacao_diferente_data_nao_agrupa(self):
        eventos = [
            evento("MORROS", inicio="13/11/2026 18:00",
                   fim="14/11/2026 09:08"),
            evento("NOVO URUGUAI", inicio="14/11/2026 18:00",
                   fim="15/11/2026 09:08"),
        ]
        ordens = [
            ordem(3101, "Morros", inicio_sla="13/11/2026 19:00"),
            ordem(3102, "Novo Uruguai", inicio_sla="14/11/2026 19:00"),
        ]
        lote, *_ = gerar(eventos, ordens)
        self.assertEqual(len(lote), 2)

    def test_mesma_data_mas_observacao_diferente_nao_agrupa(self):
        eventos = [
            evento("MORROS"),
            evento("NOVO URUGUAI", ativo="PL-THE-OUTRO"),
        ]
        ordens = [ordem(3201, "Morros"), ordem(3202, "Novo Uruguai")]
        lote, *_ = gerar(eventos, ordens)
        self.assertEqual(len(lote), 2)
        self.assertEqual(lote["Observação"].nunique(), 2)

    def test_mesmo_evento_em_cidades_diferentes_nao_agrupa(self):
        eventos = [
            evento("MORROS", cidade="Teresina"),
            evento("MORROS", cidade="Timon"),
        ]
        ordens = [
            ordem(3301, "Morros", cidade="Teresina"),
            ordem(3302, "Morros", cidade="Timon"),
        ]
        lote, *_ = gerar(eventos, ordens)
        self.assertEqual(len(lote), 2)
        self.assertEqual(set(lote["Cidade"]), {"TERESINA", "TIMON"})

    def test_sem_os_na_area_nao_adiciona_bairro_ao_agrupamento(self):
        lote, _, _, qtd_os, _ = gerar(
            [evento("MORROS"), evento("SATELITE PARCIAL")],
            [ordem(3401, "Morros")]
        )
        self.assertEqual(len(lote), 1)
        self.assertEqual(lote.iloc[0]["Bairro"], "MORROS")
        self.assertEqual(qtd_os, 1)

    def test_preserva_ordem_dos_eventos_e_dos_bairros(self):
        eventos = [
            evento("VALE QUEM TEM"), evento("MORROS"),
            evento("NOVO URUGUAI"),
        ]
        ordens = [
            ordem(3501, "Vale Quem Tem"), ordem(3502, "Morros"),
            ordem(3503, "Novo Uruguai"),
        ]
        lote, *_ = gerar(eventos, ordens)
        self.assertEqual(lote.iloc[0]["Bairro"],
                         "VALE QUEM TEM, MORROS, NOVO URUGUAI")

    def test_qualificador_parcial_sem_nome_vazio(self):
        f = ambiente["limpar_bairro_lote_eventos"]
        self.assertEqual(f("SATÉLITE (PARCIAL)"), "SATELITE")
        self.assertEqual(f("VALE QUEM TEM PARCIALMENTE"), "VALE QUEM TEM")
        self.assertEqual(f("MORROS - PARCIAL"), "MORROS")
        self.assertEqual(f("MORROS"), "MORROS")

    def test_resultado_sem_eventos_compatibilidade(self):
        cols = COLUNAS_LOTE
        resultado = ambiente["consolidar_linhas_lote_eventos"]([])
        self.assertTrue(resultado.empty)
        self.assertEqual(resultado.columns.tolist(), cols)


if __name__ == "__main__":
    unittest.main()
