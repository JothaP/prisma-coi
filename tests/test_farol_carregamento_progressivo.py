"""Testes do carregamento progressivo de concentrações (Farol M3).

O resumo rápido deve concordar com a análise detalhada original, sem
executá-la para cada concentração durante a montagem dos cartões.
"""
import unittest
from pathlib import Path

import pandas as pd

from farol_ocorrencias_persistentes import (
    resumir_eventos_concentracoes, os_por_evento_confirmado,
)
from test_farol_territorial_hibrido import (
    ambiente, geometrias, os_exemplo,
)

RAIZ=Path(__file__).resolve().parents[1]
FONTE=(RAIZ/"pages"/"3_Farol_Operacional.py")
AGORA=pd.Timestamp("2026-11-15 16:30:00")


def resumo(pontos, eventos, poligonos=None):
    return resumir_eventos_concentracoes(
        pontos, eventos, poligonos,
        normalizar_cidade=ambiente["normalizar_municipio"],
        conferir_data=lambda evento: True,
        abrange_municipio=ambiente["evento_abrange_municipio"],
        extrair_areas=ambiente["extrair_bairros_evento"],
        nomes_poligono=ambiente["nomes_reconhecidos_feicao"],
        localizar_os=ambiente["identificar_correspondencia_territorial_evento_os"],
        converter_data=ambiente["interpretar_data_operacional"],
        classificar_status=ambiente["classificar_status_evento"],
        chave_evento=lambda evento: str(evento.get("Protocolo","")),
        agora=AGORA,
    )


def ordens():
    return pd.DataFrame([
        os_exemplo(**{"Nº da O.S":"1001", "Bairro":"BAIRRO OFICIAL",
                      "_grupo":1,"Dt. Emissão":"11/11/2026 09:00"}),
        os_exemplo(**{"Nº da O.S":"1002", "Bairro":"DOM AVELAR",
                      "_grupo":1, "Latitude":-5.08,
                      "Dt. Emissão":"11/11/2026 10:00"}),
        os_exemplo(**{"Nº da O.S":"1003", "Bairro":"BAIRRO OFICIAL",
                      "_grupo":2, "Latitude":-5.05,
                      "Dt. Emissão":"11/11/2026 09:20"}),
        os_exemplo(**{"Nº da O.S":"2001","Cidade":"Picos","Bairro":"Centro",
                      "_grupo":3,"Latitude":-7.08, "Longitude":-41.46,
                      "Dt. Emissão":"11/11/2026 09:30"}),
        os_exemplo(**{"Nº da O.S":"2002","Cidade":"Picos","Bairro":"Outro",
                      "_grupo":3,"Latitude":-7.08, "Longitude":-41.46,
                      "Dt. Emissão":"11/11/2026 09:30"}),
    ])


def eventos():
    return pd.DataFrame([
        {"Cidade":"Teresina","Áreas Impactadas":"BAIRRO OFICIAL",
         "Início":"11/11/2026 08:00","Término Real":"11/11/2026 12:00",
         "Status":"Finalizado","Protocolo":"EV-01"},
        {"Cidade":"Teresina","Áreas Impactadas":"Dom Avelar",
         "Início":"11/11/2026 08:00","Término Real":"",
         "Status":"Em andamento","Protocolo":"EV-02"},
        {"Cidade":"Picos","Áreas Impactadas":"Centro",
         "Início":"11/11/2026 08:00","Término Real":"",
         "Status":"Em andamento","Protocolo":"EV-03"},
        {"Cidade":"Picos","Áreas Impactadas":"Todo o município",
         "Início":"11/11/2026 08:00","Término Real":"11/11/2026 12:00",
         "Status":"Finalizado","Protocolo":"EV-04"},
        {"Cidade":"Picos","Áreas Impactadas":"Centro",
         "Início":"11/11/2026 08:00","Término Real":"",
         "Status":"Finalizado","Protocolo":"EV-05"},
    ])


class TesteCarregamentoProgressivo(unittest.TestCase):
    def test_resumo_igual_analise_detalhada_para_cada_grupo(self):
        pontos,ev,poligonos=ordens(),eventos(),geometrias()
        visao=resumo(pontos,ev,poligonos)
        for grupo in (1,2,3):
            conc={"GRUPO":grupo,"ID_CONCENTRACAO":f"TESTE C00{grupo}"}
            os_grupo,_,relacionados=ambiente["dados_relatorio_concentracao"](
                conc,pontos,pd.DataFrame(),ev,poligonos,
            )
            basico=visao.get(grupo,{})
            self.assertEqual(
                basico.get("eventos_relacionados",0),len(relacionados),
                f"contagem territorial divergiu para grupo {grupo}",
            )
            previstos=os_por_evento_confirmado(
                os_grupo,relacionados,lambda item:str(item.get("Protocolo","")),
                ambiente["interpretar_data_operacional"],
                ambiente["classificar_status_evento"],AGORA,
            )
            self.assertEqual(
                basico.get("evento_os",{}),previstos,
                f"associações por O.S. divergiram para grupo {grupo}",
            )

    def test_sem_poligonos_e_abrangencia_municipal(self):
        pontos=ordens()
        resultado=resumo(pontos,eventos(),None)
        self.assertEqual(resultado[3]["eventos_relacionados"],3)
        self.assertEqual(resultado[3]["evento_os"]["EV-03"],("2001",))
        self.assertEqual(resultado[3]["evento_os"]["EV-04"],("2001","2002"))
        self.assertNotIn("EV-05",resultado[3]["evento_os"])

    def test_areas_sem_os_nao_geram_evento_no_grupo(self):
        os_grupo=pd.DataFrame([
            os_exemplo(**{"Bairro":"BAIRRO INEXISTENTE","_grupo":9})
        ])
        ev=pd.DataFrame([{
            "Cidade":"Teresina","Áreas Impactadas":"DOM AVELAR",
            "Início":"11/11/2026 08:00","Término Real":"",
            "Status":"Em andamento","Protocolo":"EVENTO-A",
        }])
        self.assertEqual(resumo(os_grupo,ev,[]),{})

    def test_base_vazia_nao_monta_ficha(self):
        self.assertEqual(resumo(pd.DataFrame(),eventos()),{})
        self.assertEqual(resumo(ordens(),pd.DataFrame()),{})

    def test_fluxo_nao_constroi_relatorio_de_cada_cartao(self):
        fonte=FONTE.read_text(encoding="utf-8")
        trecho=fonte.split(
            "# Um resumo compartilhado, por evento e município,",1
        )[1].split("# A ficha.id é permanente.",1)[0]
        self.assertIn("resumir_eventos_concentracoes(",trecho)
        self.assertNotIn("dados_relatorio_concentracao(",trecho)
        inicio=fonte.index("for aba, abrangencia in ((aba_the")
        detalhe=fonte.index("if detalhe_ativo:",inicio)
        chamada=fonte.index("= dados_relatorio_concentracao(",detalhe)
        self.assertLess(inicio,detalhe)
        self.assertLess(detalhe,chamada)
        self.assertIn("dados_preparados=(os_eventos, ativos_eventos, eventos_do_grupo)",
                      fonte[chamada:])

    def test_sincronizacao_depois_da_montagem_dos_cartoes(self):
        fonte=FONTE.read_text(encoding="utf-8")
        idx_cartoes=fonte.index("for aba, abrangencia in ((aba_the")
        idx_sincronia=fonte.index(
            "retorno_ocorrencias = sincronizar_ocorrencias_lote(")
        self.assertGreater(idx_sincronia,idx_cartoes)
        self.assertNotIn("gravacoes_nesta_interacao",fonte)


if __name__=="__main__":
    unittest.main()
