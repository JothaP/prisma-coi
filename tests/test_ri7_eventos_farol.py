"""Regressões Módulo 7: mesmos critérios por O.S. do Farol Operacional M3.

Executa somente funções puras da AST do Módulo 7, sem acessar Streamlit,
Google Sheets ou Supabase. Não modifica os arquivos originais.
"""
from __future__ import annotations

import ast
import copy
from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from farol_eventos_relatorio_integrado import (
    carregar_gpkg, eventos_do_grupo_farol, evento_pertinente_periodo,
    classificar_os_por_evento,
)

RAIZ = Path(__file__).resolve().parents[1]
FAROL = RAIZ / "pages" / "3_Farol_Operacional.py"
MODULO7 = RAIZ / "pages" / "7_Relatorios_Integrados.py"
AUXILIAR = RAIZ / "farol_eventos_relatorio_integrado.py"

COPIAS_EXATAS = (
    "normalizar_texto", "normalizar_cabecalho", "parse_float",
    "limpar_nome_bairro", "normalizar_localidade_busca",
    "endereco_contem_localidade", "nomes_reconhecidos_feicao",
    "indice_nomes_bairros", "resolver_nome_bairro",
    "evento_abrange_municipio", "extrair_bairros_evento",
    "normalizar_municipio", "carregar_kmz_bytes", "carregar_kml_bytes",
    "carregar_gpkg", "nome_municipio_arquivo",
    "preparar_poligonos_para_mapa", "carregar_geometrias_municipais",
    "descobrir_kmz", "ponto_em_poligono", "ponto_em_bairro",
    "indexar_geometrias_evento",
    "identificar_correspondencia_territorial_evento_os",
    "normalizar_coordenada", "classificar_status_evento",
    "interpretar_data_operacional", "classificar_os_por_evento",
)


def funcoes_modulo7():
    arvore = ast.parse(MODULO7.read_text(encoding="utf-8"))
    nomes = {
        "eventos_da_concentracao", "classificar_os_eventos",
        "ri7_resumo_eventos_relacionados",
    }
    selecionadas = [
        no for no in arvore.body
        if isinstance(no, ast.FunctionDef) and no.name in nomes
    ]
    assert {no.name for no in selecionadas} == nomes
    ambiente = {
        "pd": pd,
        "eventos_do_grupo_farol": eventos_do_grupo_farol,
        "classificar_os_por_evento": classificar_os_por_evento,
    }
    exec(compile(ast.Module(body=selecionadas, type_ignores=[]),
                 str(MODULO7), "exec"), ambiente)
    return ambiente


def os_exemplo(numero, latitude, longitude, bairro, data_os):
    return {
        "Nº da O.S": numero,
        "Matrícula": "1234567",
        "Cidade": "TERESINA",
        "Bairro": bairro,
        "Endereço": "Rua Exemplo, 10",
        "Latitude": latitude, "Longitude": longitude,
        "Dt. Emissão": data_os,
        "Status OS": "Pendente",
    }


def bairro_geografico():
    # Mesmo formato usado no Módulo 3; polígono grande sintético.
    return [{
        "municipio": "TERESINA", "nome": "PORTO DO CENTRO",
        "nome_normalizado": "PORTO DO CENTRO",
        "poligonos": [
            [(-5.10, -42.90), (-5.10, -42.70),
             (-4.90, -42.70), (-4.90, -42.90)]
        ],
    }]


def evento_exemplo(**dados):
    base = {
        "Cidade": "TERESINA", "Áreas Impactadas": "Porto do Centro",
        "Serviço": "Manutenção Elétrica",
        "Descrição do Serviço": "Manutenção Elétrica",
        "Código do Ativo": "PL-THE-PCO0356",
        "Protocolo": "E-123", "Status": "Finalizado",
        "Início": "10/11/2026 08:00h",
        "Término Real": "10/11/2026 15:00h",
    }
    base.update(dados)
    return base


class TestRelatorio7EventosFarol(unittest.TestCase):
    def setUp(self):
        self.funcoes = funcoes_modulo7()
        self.data = date(2026, 11, 10)

    def test_mesmas_implementacoes_puras_do_modulo3(self):
        origem = ast.parse(FAROL.read_text(encoding="utf-8"))
        copia = ast.parse(AUXILIAR.read_text(encoding="utf-8"))
        def indice(arvore):
            return {
                no.name: no for no in arvore.body
                if isinstance(no, ast.FunctionDef)
            }
        src, dst = indice(origem), indice(copia)
        for nome in COPIAS_EXATAS:
            with self.subTest(funcao=nome):
                self.assertIn(nome, src)
                self.assertIn(nome, dst)
                original = copy.deepcopy(src[nome])
                duplicada = copy.deepcopy(dst[nome])
                # Decoradores st.cache_data são exclusivos da página do Farol.
                # A lógica interna, inclusive atribuições, deve permanecer idêntica.
                original.decorator_list = []
                duplicada.decorator_list = []
                self.assertEqual(
                    ast.dump(original, include_attributes=False),
                    ast.dump(duplicada, include_attributes=False),
                    "Rotina divergente; atualizar compartilhamento com M3",
                )

    def test_evento_manutencao_eletrica_vincula_apenas_os_no_poligono_e_horario(self):
        grupo = pd.DataFrame([
            os_exemplo("1657243", -5.05, -42.80, "VALE QUEM TEM",
                       "10/11/2026 09:30"),
            os_exemplo("1657242", -5.15, -42.80, "PORTO DO CENTRO",
                       "10/11/2026 09:30"),
            os_exemplo("1657307", -5.05, -42.80, "VALE QUEM TEM",
                       "10/11/2026 22:30"),
        ])
        eventos = pd.DataFrame([evento_exemplo()])
        relacionados = eventos_do_grupo_farol(
            grupo, eventos, bairro_geografico(), self.data, self.data
        )
        self.assertEqual(len(relacionados), 1)
        self.assertEqual(
            relacionados.iloc[0]["_indices_os_correspondentes"], (0, 2)
        )
        self.assertEqual(
            relacionados.iloc[0]["_criterios_os_correspondentes"], {0: "Polígono", 2: "Polígono"}
        )
        vinculadas, nao = self.funcoes["classificar_os_eventos"](
            grupo, relacionados.to_dict("records")
        )
        self.assertEqual([r["Nº da O.S"] for r in vinculadas], ["1657243"])
        self.assertEqual(
            {r["Nº da O.S"] for r in nao}, {"1657242", "1657307"}
        )
        self.assertEqual(vinculadas[0]["Eventos vinculados"], "E-123")
        descricao = self.funcoes["ri7_resumo_eventos_relacionados"](
            relacionados.to_dict("records")
        )
        self.assertIn("Manutenção Elétrica / PL-THE-PCO0356", descricao)

    def test_relatorio7_utiliza_contexto_real_sem_falso_vinculo(self):
        grupo = pd.DataFrame([
            os_exemplo("1657243", -5.05, -42.80, "VALE QUEM TEM",
                       "10/11/2026 10:00")
        ])
        contexto = {"EVENTOS": pd.DataFrame([evento_exemplo()])}
        relacionados = self.funcoes["eventos_da_concentracao"](
            {"Concentração": "THE C001"}, contexto,
            self.data, self.data, grupo, bairro_geografico()
        )
        self.assertEqual(len(relacionados), 1)
        self.assertEqual(relacionados[0]["Código do Ativo"], "PL-THE-PCO0356")
        # Outro município não deve gerar vínculo, mesmo com a mesma localização.
        fora = grupo.copy()
        fora["Cidade"] = "TIMON"
        self.assertEqual(
            self.funcoes["eventos_da_concentracao"](
                {}, contexto, self.data, self.data, fora, bairro_geografico()
            ), []
        )

    def test_fallback_bairro_ou_endereco_quando_sem_poligono(self):
        grupo = pd.DataFrame([
            os_exemplo("1", -5.05, -42.80, "PORTO DO CENTRO",
                       "10/11/2026 10:00"),
            {**os_exemplo("2", -5.05, -42.80, "OUTRO BAIRRO",
                          "10/11/2026 10:00"),
             "Endereço": "Rua das Acácias, 12 - Porto do Centro"},
        ])
        relativos = eventos_do_grupo_farol(
            grupo, pd.DataFrame([evento_exemplo()]), [],
            self.data, self.data
        )
        self.assertEqual(
            relativos.iloc[0]["_criterios_os_correspondentes"],
            {0: "Bairro cadastrado", 1: "Endereço"}
        )

    def test_data_e_status_impedem_vinculo_indevido(self):
        self.assertFalse(evento_pertinente_periodo(
            evento_exemplo(**{"Início": "12/11/2026 08:00h"}),
            self.data, self.data
        ))
        self.assertFalse(evento_pertinente_periodo(
            evento_exemplo(**{"Status": "Finalizado", "Término Real": ""}),
            self.data, self.data
        ))
        self.assertTrue(evento_pertinente_periodo(
            evento_exemplo(), self.data, self.data
        ))
        # Horário não informado: M3 não confirma associação temporal.
        grupo = pd.DataFrame([
            os_exemplo("1", -5.05, -42.80, "PORTO DO CENTRO",
                       "10/11/2026")
        ])
        rel = eventos_do_grupo_farol(
            grupo, pd.DataFrame([evento_exemplo()]), [],
            self.data, self.data
        )
        vinc, nao = self.funcoes["classificar_os_eventos"](
            grupo, rel.to_dict("records")
        )
        self.assertEqual(vinc, [])
        self.assertEqual(len(nao), 1)

    def test_leitor_real_usa_geopackage_do_farol(self):
        gpkg = RAIZ / "geodados" / "municipios" / "NM_MUN_Teresina.gpkg"
        self.assertTrue(gpkg.is_file(), "Geografia usada em produção não encontrada")
        feicoes = carregar_gpkg(str(gpkg))
        self.assertGreater(len(feicoes), 0)
        self.assertTrue(any(f.get("poligonos") for f in feicoes))
        self.assertTrue(any(f.get("municipio") == "TERESINA" for f in feicoes))

    def test_pdf_passou_a_utilizar_vinculo_territorial_por_os(self):
        codigo = MODULO7.read_text(encoding="utf-8")
        self.assertIn("bairros_geograficos = (", codigo)
        self.assertIn("bairros_do_farol()", codigo)
        self.assertIn("eventos_do_grupo_farol(", codigo)
        self.assertIn("classificar_os_por_evento(grupo, relacionados)", codigo)
        self.assertIn("grupo, bairros_geograficos", codigo)
        self.assertIn("aplicar_vinculos_manuais(", codigo)
        self.assertIn("eventos_manuais_do_grupo(", codigo)
        self.assertNotIn("evento_tem_abrangencia_municipal(", codigo)
        self.assertNotIn("horario_operacional(", codigo)
        self.assertNotIn("from pages.3_Farol_Operacional", codigo)
        ast.parse(codigo)


if __name__ == "__main__":
    unittest.main()
