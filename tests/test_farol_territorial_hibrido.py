"""Regressões do vínculo híbrido do Farol (sem iniciar Streamlit/Google Sheets).

Apenas funções puras do Módulo 3 são carregadas a partir da AST. Nenhuma
credencial, banco ou planilha de produção é acessada durante os testes.
"""
import ast
from datetime import date, datetime
import math
from pathlib import Path
import re
import unittest
import unicodedata
from zoneinfo import ZoneInfo

import pandas as pd


FONTE = Path(__file__).resolve().parents[1] / "pages" / "3_Farol_Operacional.py"
ARVORE = ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))

FUNCOES = {
    "normalizar_texto", "normalizar_cabecalho", "limpar_valor", "parse_float",
    "normalizar_coordenada", "limpar_nome_bairro", "normalizar_localidade_busca",
    "nomes_reconhecidos_feicao",
    "endereco_contem_localidade", "normalizar_municipio", "indice_nomes_bairros",
    "resolver_nome_bairro", "evento_abrange_municipio", "extrair_bairros_evento",
    "ponto_em_poligono", "ponto_em_bairro", "indexar_geometrias_evento",
    "identificar_correspondencia_territorial_evento_os",
    "correspondencia_territorial_evento_os",
    "relacionar_eventos_pontos", "localizar_coluna", "mapear_colunas",
    "identificar_formato_pontos", "preparar_pontos", "chave_ponto",
    "consolidar_arquivos_importacao", "upsert_pontos",
    "dados_relatorio_concentracao", "interpretar_data_operacional",
    "classificar_status_evento", "classificar_os_por_evento",
    "diagnosticar_os_nao_vinculadas",
}
CONSTANTES = {"CABECALHO_PONTOS", "MAPA_COLUNAS_ATIVIDADES"}
ambiente = {
    "pd": pd, "re": re, "math": math, "unicodedata": unicodedata,
    "datetime": datetime, "date": date, "FUSO_FAROL": ZoneInfo("America/Fortaleza"),
    "NOME_ABA_PONTOS": "PONTOS",
    "evento_pertinente_data_analise": lambda evento: True,
}
for no in ARVORE.body:
    if isinstance(no, ast.Assign):
        nomes = {alvo.id for alvo in no.targets if isinstance(alvo, ast.Name)}
        if nomes & CONSTANTES:
            for nome in nomes & CONSTANTES:
                ambiente[nome] = ast.literal_eval(no.value)
subarvore = ast.Module(
    body=[no for no in ARVORE.body
          if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))
          and no.name in FUNCOES],
    type_ignores=[],
)
assert {n.name for n in subarvore.body} == FUNCOES, "Função não encontrada no Módulo 3"
exec(compile(subarvore, str(FONTE), "exec"), ambiente)


def os_exemplo(**alteracoes):
    base = {
        "Nº da O.S": "1001", "Matrícula": "2001", "Cidade": "Teresina",
        "Bairro": "BAIRRO OFICIAL", "Endereço": "Rua São Francisco, 125 - Dom Avelar",
        "Latitude": -5.05, "Longitude": -42.80,
        "Status OS": "Pendente", "Dt. Emissão": "11/11/2026 09:30",
        "Serviço Executado": "146005",
    }
    base.update(alteracoes)
    return base


def geometrias():
    # Polígono oficial contém o polígono menor: a sobreposição é intencional.
    oficial = [(-5.10, -42.90), (-5.10, -42.70),
               (-4.90, -42.70), (-4.90, -42.90)]
    subarea = [(-5.06, -42.82), (-5.06, -42.78),
               (-5.04, -42.78), (-5.04, -42.82)]
    return [
        {"nome": "BAIRRO OFICIAL", "nome_normalizado": "BAIRRO OFICIAL",
         "municipio": "TERESINA", "poligonos": [oficial]},
        {"nome": "Dom Avelar", "nome_normalizado": "DOM AVELAR",
         "municipio": "TERESINA", "poligonos": [subarea]},
    ]


class TesteVinculosTerritoriaisFarol(unittest.TestCase):
    def criterio(self, registro, areas, bairros=None, cidade="TERESINA"):
        areas = ambiente["extrair_bairros_evento"](areas, cidade, bairros)
        return ambiente["identificar_correspondencia_territorial_evento_os"](
            registro, cidade, areas, bairros
        )

    def test_subarea_sobreposta_ao_bairro_oficial(self):
        area, metodo = self.criterio(os_exemplo(), "Dom Avelar", geometrias())
        self.assertEqual((area, metodo), ("DOM AVELAR", "Polígono"))

    def test_subarea_com_rotulo_explicito_e_aliases_do_gpkg(self):
        # Simula feição do PRISMA-GEO com NM_BAIRRO + name distinto.
        exemplo = geometrias()[1].copy()
        exemplo["nome"] = "Vila Inglesa"
        exemplo["nome_normalizado"] = "VILA INGLESA"
        exemplo["nomes_alternativos"] = ["Bairro Oficial", "Villa Inglesa"]
        exemplo["tipo_area"] = "Subárea"
        base = [exemplo]
        catalogo = ambiente["indice_nomes_bairros"](base, "TERESINA")
        self.assertIn("VILA INGLESA", catalogo)
        self.assertIn("OFICIAL", catalogo)
        locais = ambiente["extrair_bairros_evento"](
            "Subáreas afetadas: Villa Inglesa", "TERESINA", base
        )
        self.assertEqual([x["bairro"] for x in locais], ["VILA INGLESA"])
        local, criterio = ambiente["identificar_correspondencia_territorial_evento_os"](
            os_exemplo(), "TERESINA", locais, base
        )
        self.assertEqual((local, criterio), ("VILA INGLESA", "Polígono"))

    def test_poligono_fora_impede_vinculo_por_endereco_ou_bairro(self):
        reg = os_exemplo(Latitude=-5.08, Longitude=-42.88,
                         Bairro="Dom Avelar", Endereço="Dom Avelar")
        self.assertEqual(self.criterio(reg, "Dom Avelar", geometrias()),
                         (None, None))

    def test_sem_poligono_utiliza_endereco_completo(self):
        reg = os_exemplo(Endereço="Rua São Francisco, 125 - Villa Inglesa")
        self.assertEqual(self.criterio(reg, "Vila Inglesa", geometrias()),
                         ("VILA INGLESA", "Endereço"))

    def test_sem_poligono_prioriza_bairro_exato(self):
        reg = os_exemplo(Bairro="Villa Inglesa", Endereço="Rua Aleatória, 123")
        self.assertEqual(self.criterio(reg, "Vila Inglesa", geometrias()),
                         ("VILA INGLESA", "Bairro cadastrado"))

    def test_endereco_nao_confunde_fragmento_com_localidade(self):
        busca = ambiente["endereco_contem_localidade"]
        self.assertTrue(busca("Rua São Francisco, 125 - Dom Avelar", "Dom Avelar"))
        self.assertTrue(busca("Avenida 3, Vila Inglesa", "Villa Inglesa"))
        self.assertFalse(busca("Rua Dom Avelar, 45", "Dom Avelar"))
        self.assertFalse(busca("Conjunto Dom Avelar II", "Dom Avelar"))
        self.assertFalse(busca("Residencial Villa Inglesa Nova", "Vila Inglesa"))
        self.assertFalse(busca("Residencial Dom Avelarzinho", "Dom Avelar"))

    def test_municipio_diferente_nunca_vincula(self):
        self.assertEqual(
            self.criterio(os_exemplo(Cidade="Timon"), "Dom Avelar", geometrias()),
            (None, None),
        )

    def test_evento_subarea_na_tabela_sem_dupla_contagem(self):
        eventos = pd.DataFrame([{
            "Cidade": "Teresina", "Áreas Impactadas": "Dom Avelar, Bairro Oficial",
            "Protocolo": "EVENTO 1", "Status": "Em andamento", "Data": "11/11/2026",
            "Serviço": "Manutenção",
        }])
        pontos = pd.DataFrame([os_exemplo()])
        rel = ambiente["relacionar_eventos_pontos"](eventos, pontos, geometrias())
        self.assertEqual(rel["O.S. encontradas"].sum(), 1)
        self.assertIn("Polígono: 1", rel["Critérios de associação"].iloc[0])

    def test_evento_sem_poligono_contabiliza_endereco(self):
        eventos = pd.DataFrame([{"Cidade": "Teresina",
                                 "Áreas Impactadas": "Villa Inglesa", "Status": "Ativo"}])
        pontos = pd.DataFrame([os_exemplo(
            Endereço="Rua São Francisco, 125 - Vila Inglesa")])
        rel = ambiente["relacionar_eventos_pontos"](eventos, pontos, geometrias())
        self.assertEqual(rel["O.S. encontradas"].sum(), 1)
        self.assertIn("Endereço: 1", rel["Critérios de associação"].iloc[0])

    def test_importacao_power_bi_preserva_endereco(self):
        fonte = pd.DataFrame([os_exemplo()])
        preparado = ambiente["preparar_pontos"](fonte)
        self.assertEqual(preparado["Endereço"].iloc[0],
                         "Rua São Francisco, 125 - Dom Avelar")

    def test_importacao_alternativa_endereco_opcional(self):
        linha = {col: "X" for col in ambiente["MAPA_COLUNAS_ATIVIDADES"].values()}
        linha.update({"Cód. Protocolo Origem": "1001",
                      "Latitude": "-5,05", "Longitude": "-42,80",
                      "Início do SLA": "11/11/2026 09:30"})
        sem = ambiente["preparar_pontos"](pd.DataFrame([linha]))
        self.assertEqual(sem["Endereço"].iloc[0], "")
        linha["Endereço"] = "Villa Inglesa"
        com = ambiente["preparar_pontos"](pd.DataFrame([linha]))
        self.assertEqual(com["Endereço"].iloc[0], "Villa Inglesa")

    def test_lote_misto_nao_apaga_endereco_anterior(self):
        class Upload:
            def __init__(self, name, df):
                self.name, self.df = name, df
        ant = Upload("primeiro.xlsx", pd.DataFrame([os_exemplo(
            Endereço="Dom Avelar")]))
        novo = Upload("segundo.xlsx", pd.DataFrame([os_exemplo(
            Endereço="")]))
        ambiente["ler_planilha_upload"] = lambda arq: arq.df
        combinado, _, _, repetidos = ambiente["consolidar_arquivos_importacao"](
            [ant, novo], "PONTOS", validar_janela=False
        )
        self.assertEqual(repetidos, 1)
        self.assertEqual(combinado["Endereço"].iloc[0], "Dom Avelar")

    def test_upsert_preserva_endereco_existente(self):
        ant = pd.DataFrame([os_exemplo(Endereço="Dom Avelar", Extra="Valor antigo")])
        novo = pd.DataFrame([os_exemplo(Endereço="", Extra="")])
        capturado = {}
        ambiente["exigir_edicao_farol"] = lambda: None
        ambiente["carregar_aba"] = lambda *args: ant.copy()
        ambiente["substituir_aba"] = lambda nome, cab, df: capturado.update(
            {"nome": nome, "cab": cab, "df": df.copy()}
        )
        ambiente["upsert_pontos"](novo)
        self.assertEqual(capturado["df"]["Endereço"].iloc[0], "Dom Avelar")
        self.assertEqual(capturado["df"]["Extra"].iloc[0], "Valor antigo")

    def test_todo_municipio_sem_poligonos_vincula_apenas_cidade_e_horario(self):
        pontos = pd.DataFrame([
            os_exemplo(**{"Nº da O.S": "2001", "Cidade": "São Raimundo Nonato",
                          "Bairro": "Centro", "Endereço": "", "_grupo": 1}),
            os_exemplo(**{"Nº da O.S": "2002", "Cidade": "São Raimundo Nonato",
                          "Bairro": "Outro bairro", "Endereço": "",
                          "Dt. Emissão": "11/11/2026 16:01", "_grupo": 1}),
            os_exemplo(**{"Nº da O.S": "2003", "Cidade": "Timon",
                          "Bairro": "Centro", "Endereço": "", "_grupo": 1}),
        ])
        eventos = pd.DataFrame([{
            "Cidade": "São Raimundo Nonato",
            "Áreas Impactadas": "Todo o município", "Início": "11/11/2026 08:00",
            "Término Real": "11/11/2026 12:00",
            "Status": "Em andamento", "Protocolo": "MUNICIPAL",
        }])
        conc = {"GRUPO": 1, "ID_CONCENTRACAO": "API 1"}
        prox = pd.DataFrame(columns=["Concentração"])
        grupo, _, relacionados = ambiente["dados_relatorio_concentracao"](
            conc, pontos, prox, eventos, bairros_geograficos=[]
        )
        vinculadas, nao = ambiente["classificar_os_por_evento"](grupo, relacionados)
        self.assertEqual(vinculadas["Nº da O.S"].tolist(), ["2001"])
        self.assertEqual(vinculadas["Critério do vínculo"].tolist(), ["Município"])
        self.assertEqual(set(nao["Nº da O.S"]), {"2002", "2003"})
        diagnostico = ambiente["diagnosticar_os_nao_vinculadas"](nao, eventos, [])
        motivos = dict(zip(diagnostico["Nº da O.S"], diagnostico["Motivo da não vinculação"]))
        self.assertEqual(motivos["2002"], "O.S. fora da janela temporal do evento")
        self.assertEqual(motivos["2003"], "Nenhum evento elegível no município e dia analisado")

    def test_sem_poligono_por_bairro_nao_liga_bairro_diferente(self):
        pontos = pd.DataFrame([
            os_exemplo(**{"Nº da O.S": "3001", "Cidade": "Picos",
                          "Bairro": "Centro", "Endereço": "", "_grupo": 2}),
            os_exemplo(**{"Nº da O.S": "3002", "Cidade": "Picos",
                          "Bairro": "Jardim das Flores", "Endereço": "", "_grupo": 2}),
        ])
        eventos = pd.DataFrame([{
            "Cidade": "Picos", "Áreas Impactadas": "Centro, Aldeia",
            "Início": "11/11/2026 08:00", "Término Real": "11/11/2026 12:00",
            "Status": "Ativo", "Protocolo": "PICOS 1",
        }])
        grupo, _, relacionados = ambiente["dados_relatorio_concentracao"](
            {"GRUPO": 2, "ID_CONCENTRACAO": "API 2"},
            pontos, pd.DataFrame(columns=["Concentração"]), eventos, []
        )
        vinculadas, nao = ambiente["classificar_os_por_evento"](grupo, relacionados)
        self.assertEqual(vinculadas["Nº da O.S"].tolist(), ["3001"])
        self.assertEqual(vinculadas["Critério do vínculo"].tolist(), ["Bairro cadastrado"])
        diag = ambiente["diagnosticar_os_nao_vinculadas"](nao, eventos, [])
        self.assertIn("Sem polígono da área", diag["Motivo da não vinculação"].iloc[0])

    def test_evento_sem_abrangencia_nao_vincula_municipio_sem_poligono(self):
        evento = pd.DataFrame([{
            "Cidade": "Picos", "Áreas Impactadas": "",
            "Início": "11/11/2026 08:00", "Término Real": "11/11/2026 12:00",
            "Status": "Ativo", "Protocolo": "SEM AREAS",
        }])
        pontos = pd.DataFrame([
            os_exemplo(**{"Nº da O.S": "4001", "Cidade": "Picos",
                          "Bairro": "Centro", "Endereço": "", "_grupo": 3}),
        ])
        grupo, _, relacionados = ambiente["dados_relatorio_concentracao"](
            {"GRUPO": 3, "ID_CONCENTRACAO": "API 3"},
            pontos, pd.DataFrame(columns=["Concentração"]), evento, []
        )
        self.assertTrue(relacionados.empty)
        vinculadas, nao = ambiente["classificar_os_por_evento"](grupo, relacionados)
        self.assertTrue(vinculadas.empty)
        diag = ambiente["diagnosticar_os_nao_vinculadas"](nao, evento, [])
        self.assertEqual(diag["Motivo da não vinculação"].iloc[0],
                         "Áreas Impactadas não informadas no evento")

    def test_editor_de_abrangencia_preserva_correcoes_na_importacao(self):
        fonte = FONTE.read_text(encoding="utf-8")
        self.assertIn('"Abrangência territorial do evento"', fonte)
        self.assertIn('areas_salvar = "Todo o município"', fonte)
        self.assertIn("registrar_area_editada(chave_evento(original), area_nova)", fonte)
        self.assertIn('evento["Áreas Impactadas"] = areas_protegidas[chave]', fonte)
        self.assertIn('"Motivo da não vinculação"', fonte)

    def test_relatorio_respeita_temporal_e_exibe_metodo(self):
        pontos = pd.DataFrame([os_exemplo(_grupo=1)])
        eventos = pd.DataFrame([{
            "Cidade": "Teresina", "Áreas Impactadas": "Villa Inglesa",
            "Início": "11/11/2026 09:00", "Término Real": "11/11/2026 11:00",
            "Status": "Em andamento", "Protocolo": "E001",
        }])
        pontos.at[0, "Endereço"] = "Rua São Francisco, 125 - Villa Inglesa"
        concentracao = {"GRUPO": 1, "ID_CONCENTRACAO": "THE 1"}
        prox = pd.DataFrame(columns=["Concentração"])
        os_grupo, _, relacionados = ambiente["dados_relatorio_concentracao"](
            concentracao, pontos, prox, eventos, geometrias()
        )
        vinculadas, _ = ambiente["classificar_os_por_evento"](os_grupo, relacionados)
        self.assertEqual(len(vinculadas), 1)
        self.assertEqual(vinculadas["Critério do vínculo"].iloc[0], "Endereço")
        self.assertEqual(vinculadas["Evento(s) vinculado(s)"].iloc[0], "E001")
        pontos.at[0, "Dt. Emissão"] = "11/11/2026 16:00"
        os_grupo, _, relacionados = ambiente["dados_relatorio_concentracao"](
            concentracao, pontos, prox, eventos, geometrias()
        )
        vinculadas, nao_vinculadas = ambiente["classificar_os_por_evento"](
            os_grupo, relacionados
        )
        self.assertEqual(len(vinculadas), 0)
        self.assertEqual(len(nao_vinculadas), 1)


if __name__ == "__main__":
    unittest.main()
