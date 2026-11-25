"""Testes de evolução operacional e processamento multi-logger."""
from __future__ import annotations

import io
from pathlib import Path
import unittest

import pandas as pd

from logger_comportamento_core import diagnosticar, extrair_historico
from logger_lote_core import analisar_lote
from logger_monitor_core import ErroBaseLogger

ROOT = Path(__file__).resolve().parents[1]


def serie_base(valor=12.0, ultimo=12.0, tipo="constante"):
    """6 dias x 5 pontos no mesmo horário, suficientes para referência."""
    pontos = []
    for dia in range(1, 7):
        for deslocamento in (0, 5, 10, 15, 20):
            dt = pd.Timestamp(2026, 11, dia, 10) + pd.Timedelta(minutes=deslocamento)
            if tipo == "bimodal":
                v = 10.0 if dia <= 3 else 20.0
            else:
                v = valor
            pontos.append({"data": dt, "pressao": float(v)})
    # A última avaliação é feita em 25/11 às 10h26.
    pontos.append({
        "data": pd.Timestamp("2026-11-25 10:26"),
        "pressao": float(ultimo),
    })
    return pd.DataFrame(pontos).sort_values("data").reset_index(drop=True)


def xlsx(dados):
    buf = io.BytesIO()
    dados.to_excel(buf, index=False)
    return buf.getvalue()


class TesteInterpretacaoOperacional(unittest.TestCase):
    def test_acima_do_padrao_nao_vira_alerta_de_falta_agua(self):
        dados = serie_base(valor=12, ultimo=20.7)
        resultado = diagnosticar(dados)
        self.assertEqual(resultado["classificacao"], "Fora do padrão")
        self.assertEqual(resultado["direcao"], "Acima do padrão")
        self.assertEqual(resultado["prioridade"], "Monitorar")
        self.assertEqual(resultado["referencia_horario_mediana"], 12)
        self.assertAlmostEqual(resultado["desvio_mca"], 8.7)
        self.assertIn("não representa, por si só", resultado["diagnostico"].lower())
        self.assertNotIn("falta de água confirmada", resultado["diagnostico"].lower())

    def test_abaixo_do_padrao_continuo_sem_queda_brusca_recente(self):
        dados = serie_base(valor=17, ultimo=4)
        for minuto in (0, 10, 20, 30, 40, 50):
            dados.loc[len(dados)] = {
                "data": pd.Timestamp("2026-11-25 09:30") + pd.Timedelta(minutes=minuto),
                "pressao": 4.0,
            }
        dados = dados.sort_values("data").reset_index(drop=True)
        resp = diagnosticar(dados)
        self.assertEqual(resp["direcao"], "Abaixo do padrão")
        self.assertEqual(resp["prioridade"], "Atenção")
        self.assertGreaterEqual(resp["persistencia_abaixo_min"], 15)
        self.assertEqual(resp["status_recente"], "Pressão abaixo da referência persistente")
        self.assertIn("permanência em um patamar reduzido", resp["diagnostico"])

    def test_zero_persistente_prioridade_alta_sem_concluir_causa(self):
        dados = serie_base(valor=16, ultimo=0.0)
        for minuto in (0, 10, 20):
            dados.loc[len(dados)] = {
                "data": pd.Timestamp("2026-11-25 10:00") + pd.Timedelta(minutes=minuto),
                "pressao": 0.0,
            }
        resp = diagnosticar(dados.sort_values("data").reset_index(drop=True))
        self.assertEqual(resp["prioridade"], "Alta prioridade")
        self.assertGreaterEqual(resp["persistencia_zero_min"], 15)
        self.assertIn("sem lacunas superiores", resp["diagnostico"].lower())

    def test_recuperacao_observada_apos_valores_proximos_zero(self):
        dados = serie_base(valor=12, ultimo=12)
        dados.loc[len(dados)] = {
            "data": pd.Timestamp("2026-11-25 08:00"), "pressao": 0
        }
        dados.loc[len(dados)] = {
            "data": pd.Timestamp("2026-11-25 09:30"), "pressao": 10
        }
        dados.loc[len(dados)] = {
            "data": pd.Timestamp("2026-11-25 10:10"), "pressao": 12
        }
        resp = diagnosticar(dados.sort_values("data").reset_index(drop=True))
        self.assertEqual(resp["direcao"], "Dentro do padrão")
        self.assertTrue(resp["recuperacao"])
        self.assertEqual(resp["status_recente"], "Recuperação observada")
        self.assertIn("não confirma a causa", resp["diagnostico"].lower())

    def test_atipico_dentro_limites_de_referencia(self):
        dados = serie_base(tipo="bimodal", ultimo=15)
        resp = diagnosticar(dados)
        self.assertEqual(resp["classificacao"], "Fora do padrão")
        self.assertEqual(resp["direcao"], "Atípico dentro da faixa")
        self.assertEqual(resp["prioridade"], "Monitorar")

    def test_sem_historico_nao_inventa_padrao(self):
        dados = pd.DataFrame({
            "data": pd.to_datetime([
                "2026-11-25 08:00", "2026-11-25 09:00", "2026-11-25 10:00",
            ]),
            "pressao": [9., 12., 15.],
        })
        resp = diagnosticar(dados)
        self.assertEqual(resp["classificacao"], "Dados insuficientes")
        self.assertEqual(resp["direcao"], "Referência insuficiente")
        self.assertIsNone(resp["referencia_horario_mediana"])
        self.assertEqual(resp["prioridade"], "Referência insuficiente")
        self.assertIn("padrão", resp["diagnostico"])

    def test_limite_local_so_ativo_quando_informado(self):
        dados = serie_base(valor=7, ultimo=7)
        resp = diagnosticar(dados)
        self.assertEqual(resp["prioridade"], "Sem sinal histórico de queda")
        local = diagnosticar(dados, limiar_min_mca=10)
        self.assertEqual(local["prioridade"], "Atenção")
        self.assertEqual(local["limiar_operacional_mca"], 10)
        with self.assertRaises(ValueError):
            diagnosticar(dados, limiar_min_mca=-5)

    def test_leituras_sem_cobertura_nao_confirmam_estabilidade(self):
        dados = serie_base(valor=12, ultimo=12)
        resp = diagnosticar(dados)
        self.assertEqual(resp["tendencia"], "Dados recentes insuficientes")
        self.assertIn("não é possível concluir", resp["diagnostico"])


class TesteLote(unittest.TestCase):
    def test_multiplos_ids_em_um_arquivo_nao_misturam_historicos(self):
        a = serie_base(valor=16, ultimo=3).rename(
            columns={"data": "Data", "pressao": "Pressão (mca)"}
        )
        a["Galaxia ID"] = "A"
        b = serie_base(valor=10, ultimo=20).rename(
            columns={"data": "Data", "pressao": "Pressão (mca)"}
        )
        b["Galaxia ID"] = "B"
        panorama, detalhes = analisar_lote([(
            "historicos.xlsx", xlsx(pd.concat([a,b],ignore_index=True))
        )])
        self.assertEqual(set(detalhes), {"A", "B"})
        resumo = panorama.set_index("Galaxia ID")
        self.assertEqual(resumo.loc["A", "Direção"], "Abaixo do padrão")
        self.assertEqual(resumo.loc["B", "Direção"], "Acima do padrão")
        self.assertGreater(resumo.loc["A", "Última pressão (mca)"], 0)
        self.assertEqual(len(detalhes["A"][0]), len(a))
        self.assertEqual(len(detalhes["B"][0]), len(b))

    def test_junta_historicos_do_mesmo_id_em_dois_arquivos(self):
        serie = serie_base(valor=13, ultimo=14).rename(
            columns={"data": "Data", "pressao": "Pressão"}
        )
        serie["Galaxia ID"] = "X123"
        panorama, detalhes = analisar_lote([
            ("hist_parte1.xlsx", xlsx(serie.iloc[:18])),
            ("hist_parte2.xlsx", xlsx(serie.iloc[18:])),
        ])
        self.assertEqual(set(detalhes), {"X123"})
        self.assertEqual(len(detalhes["X123"][0]), len(serie))
        self.assertEqual(panorama.iloc[0]["Arquivos"], 2)

    def test_erro_em_um_arquivo_nao_impede_outro(self):
        historico = serie_base(valor=11, ultimo=9).rename(
            columns={"data": "Data", "pressao": "Pressão"}
        )
        historico["Galaxia ID"] = "BOM"
        resultados, detalhes = analisar_lote([
            ("historico.xlsx", xlsx(historico)),
            ("quebrado.xlsx", b"isso nao e um xlsx"),
        ])
        self.assertIn("BOM", detalhes)
        self.assertIn("Erro de importação", resultados["Classificação"].values)

    def test_arquivo_sem_id_e_identificado_sem_inventar_galaxia_id(self):
        dados = serie_base(valor=12, ultimo=12).rename(
            columns={"data": "Data", "pressao": "Pressão"}
        )
        resultados, detalhes = analisar_lote([("poco-teste.xlsx", xlsx(dados))])
        self.assertEqual(set(detalhes), {"Arquivo: poco-teste"})
        self.assertEqual(len(resultados), 1)

    def test_interface_tem_modalidades_e_nao_remova_download_do_manual(self):
        fonte = (ROOT / "pages" / "4_4_Analise_de_Logger.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"Individual", "Em lote"', fonte)
        self.assertIn('accept_multiple_files=True', fonte)
        self.assertIn("analisar_lote", fonte)
        self.assertIn("referencia_horario_mediana", fonte)
        self.assertIn("m4_logger_download_lote", fonte)
        self.assertIn('key="btn_download_manual_analise_logger"', fonte)
        self.assertIn('exigir_acesso_modulo("4")', fonte)


if __name__ == "__main__":
    unittest.main()
