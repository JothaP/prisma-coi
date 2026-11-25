"""Regressões do Monitor incremental e do leitor tolerante de exportações Galax.ia."""
import io
import unittest
from unittest.mock import patch

import pandas as pd

from logger_monitor_core import (
    carregar_historico, historico_logger, ler_arquivo, mesclar_historico,
    preparar_medicoes, resumir_historico,
)


def registro(identificador="A", pressao=10, horario="25/11/2026 08:00"):
    return {
        "Galaxia ID": identificador,
        "Valor da última medição": pressao,
        "Data da última medição": horario,
        "Município": "Teresina",
        "Status": "Conectado",
        "Estado": "Instalado",
        "Nome": "Logger simulado",
        "Endereço": "Rua A",
    }


def xlsx(registros):
    memoria = io.BytesIO()
    pd.DataFrame(registros).to_excel(memoria, index=False)
    return memoria.getvalue()


class TesteMonitorIncremental(unittest.TestCase):
    def test_xlsx_rejeitado_por_infinity_tem_recuperacao_xml(self):
        conteudo = xlsx([
            registro(pressao="7,5", horario="25/11/2026 07:00"),
            registro(pressao="5,0", horario="25/11/2026 08:00"),
        ])
        with patch(
            "logger_monitor_core.pd.read_excel",
            side_effect=ValueError("invalid literal for int() with base 10: 'Infinity'"),
        ):
            dataframe = ler_arquivo("Loggers (1).xlsx", conteudo)
        self.assertIn("Galaxia ID", dataframe.columns)
        self.assertEqual(len(dataframe), 2)
        convertido = preparar_medicoes(dataframe, "Loggers (1).xlsx")
        self.assertEqual(convertido["Pressao"].tolist(), [7.5, 5.0])

    def test_cinco_coletas_no_mesmo_upload_e_importacoes_sequenciais(self):
        bases = [
            (f"Loggers {hora:02d}.xlsx", xlsx([
                registro("LOGGER-01", pressao, f"25/11/2026 {hora:02d}:00")
            ]))
            for hora, pressao in [(6, 18), (7, 15), (8, 7), (9, 3), (10, 6)]
        ]
        lote1 = carregar_historico(bases[:2])
        lote2 = carregar_historico(bases[2:])
        serie = mesclar_historico(lote1, lote2)
        self.assertEqual(len(serie), 5)
        curva = historico_logger(serie, "LOGGER-01")
        self.assertEqual(curva["Pressao"].tolist(), [18, 15, 7, 3, 6])
        resultados = resumir_historico(serie).set_index("Galaxia ID")
        self.assertEqual(resultados.loc["LOGGER-01", "Delta_MCA"], 3)
        self.assertEqual(resultados.loc["LOGGER-01", "Tendencia"], "Aumento (+)")
        self.assertEqual(resultados.loc["LOGGER-01", "Qtd_Medicoes"], 5)
        self.assertEqual(resultados.loc["LOGGER-01", "MedicaoEm_Anterior"].hour, 9)
        self.assertEqual(resultados.loc["LOGGER-01", "MedicaoEm_Atual"].hour, 10)

    def test_um_horario_repetido_nao_gera_falso_estavel(self):
        base = preparar_medicoes(pd.DataFrame([
            registro("A", 12, "25/11/2026 08:00")
        ]), "primeira.xlsx")
        unico = resumir_historico(base).iloc[0]
        self.assertEqual(unico["Tendencia"], "Não comparável")
        self.assertIn("Sem duas medições", unico["Motivo"])
        duplicado = mesclar_historico(base, base)
        self.assertEqual(len(duplicado), 1)
        self.assertEqual(resumir_historico(duplicado).iloc[0]["Tendencia"], "Não comparável")

    def test_invalido_na_ultima_leitura_nao_usa_pressao_antiga(self):
        dados = preparar_medicoes(pd.DataFrame([
            registro("A", 12, "25/11/2026 07:00"),
            registro("A", 8, "25/11/2026 08:00"),
            registro("A", "Infinity", "25/11/2026 09:00"),
        ]), "loggers.xlsx")
        resumo = resumir_historico(mesclar_historico(None, dados)).iloc[0]
        self.assertEqual(resumo["Tendencia"], "Não comparável")
        self.assertTrue(pd.isna(resumo["Delta_MCA"]))
        self.assertIn("Pressão ausente", resumo["Motivo"])
        self.assertEqual(historico_logger(dados, "A")["Pressao"].tolist(), [12.0, 8.0])

    def test_ids_com_um_horario_ou_sem_data_continuam_visiveis(self):
        dados = preparar_medicoes(pd.DataFrame([
            registro("A", 12, "25/11/2026 08:00"),
            registro("A", 9, "25/11/2026 09:00"),
            registro("B", 5, "25/11/2026 08:30"),
            registro("C", 7, "invalida"),
        ]), "loggers.xlsx")
        resumo = resumir_historico(mesclar_historico(None, dados)).set_index("Galaxia ID")
        self.assertEqual(resumo.loc["A", "Delta_MCA"], -3)
        self.assertEqual(resumo.loc["B", "Tendencia"], "Não comparável")
        self.assertEqual(resumo.loc["C", "Tendencia"], "Não comparável")
        self.assertIn("Sem horário", resumo.loc["C", "Motivo"])

    def test_numero_de_registros_preserva_horas_de_varios_loggers(self):
        a = pd.DataFrame([
            registro("A", 15, "25/11/2026 08:00"),
            registro("A", 10, "25/11/2026 09:00"),
            registro("B", 8, "25/11/2026 08:00"),
            registro("B", 8, "25/11/2026 09:00"),
        ])
        dados = mesclar_historico(None, preparar_medicoes(a, "arquivo.xlsx"))
        resumo = resumir_historico(dados).set_index("Galaxia ID")
        self.assertEqual(resumo.loc["A", "Tendencia"], "Queda (-)")
        self.assertEqual(resumo.loc["B", "Tendencia"], "Estável")
        self.assertEqual(len(historico_logger(dados, "B")), 2)


if __name__ == "__main__":
    unittest.main()
