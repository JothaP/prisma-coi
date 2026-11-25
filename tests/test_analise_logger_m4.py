"""Regressões sem credenciais para os dois modos da Análise de Logger M4."""
from __future__ import annotations

import io
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from logger_monitor_core import (
    ErroBaseLogger, comparar_bases, consolidar_arquivos,
    ler_arquivo, numeros, preparar_base,
)
from logger_regionais import MAPA_REGIONAIS, dados_regional
from logger_comportamento_core import (
    diagnosticar, extrair_historico, padrao_horario, variacao_recente,
)

ROOT = Path(__file__).resolve().parents[1]


def base_exemplo(id_="A", pressao=12, data="25/11/2026 08:00", cidade="Teresina"):
    return {
        "Galaxia ID": id_,
        "Valor da última medição": pressao,
        "Data da última medição": data,
        "Município": cidade,
        "Status": "Conectado",
        "Estado": "Instalado",
        "Nome": "Logger de teste",
        "Endereço": "Rua A",
    }


class TesteMonitorVariacao(unittest.TestCase):
    def test_regionais_iguais_fonte_original(self):
        self.assertEqual(len(MAPA_REGIONAIS), 243)
        self.assertEqual(dados_regional("Teresina"), ("MEIO NORTE", "TERESINA", "110"))
        self.assertEqual(dados_regional("Anísio de Abreu"), ("SEMIÁRIDO SUL", "SRN", "7"))
        self.assertEqual(dados_regional("São Raimundo Nonato"), ("SEMIÁRIDO SUL", "SRN", "106"))
        self.assertEqual(dados_regional("local não mapeado"), ("Não mapeada", "-", ""))

    def test_normalizacao_pressao_decimais_zeros_e_ausentes(self):
        resultado = numeros(pd.Series(["12,5", "1.234,56", "0", "", "nan", None]))
        self.assertEqual(resultado.iloc[0], 12.5)
        self.assertEqual(resultado.iloc[1], 1234.56)
        self.assertEqual(resultado.iloc[2], 0.0)
        self.assertTrue(resultado.iloc[3:].isna().all())

    def test_comparacao_estrita_e_sem_falso_estavel(self):
        data1 = "25/11/2026 07:00"
        data2 = "25/11/2026 08:00"
        dados0 = pd.DataFrame([
            base_exemplo("A", 10, data1),
            base_exemplo("B", 20, data1),
            base_exemplo("C", 8, data1),
            base_exemplo("D", 0, data1),
            base_exemplo("E", 12, data1),
            base_exemplo("F", 18, data1),
        ])
        dados1 = pd.DataFrame([
            base_exemplo("A", 5, data2),
            base_exemplo("B", 20, data2),
            base_exemplo("C", None, data2),
            base_exemplo("D", 0, data1),
            base_exemplo("E", 15, "25/11/2026 06:00"),
            base_exemplo("G", 4, data2),
        ])
        resultado = comparar_bases(
            preparar_base(dados0, "antes.xlsx"),
            preparar_base(dados1, "depois.xlsx"),
        ).set_index("Galaxia ID")
        self.assertEqual(resultado.loc["A", "Tendencia"], "Queda (-)")
        self.assertEqual(resultado.loc["A", "Delta_MCA"], -5)
        self.assertEqual(resultado.loc["B", "Tendencia"], "Estável")
        self.assertEqual(resultado.loc["B", "Delta_MCA"], 0)
        for id_ in ("C", "D", "E", "F", "G"):
            with self.subTest(id_=id_):
                self.assertEqual(resultado.loc[id_, "Tendencia"], "Não comparável")
                self.assertTrue(pd.isna(resultado.loc[id_, "Delta_MCA"]))
        self.assertIn("Pressão ausente", resultado.loc["C", "Motivo"])
        self.assertIn("mesmo horário", resultado.loc["D", "Motivo"])
        self.assertIn("horário anterior", resultado.loc["E", "Motivo"])
        self.assertIn("base posterior", resultado.loc["F", "Motivo"])
        self.assertIn("base anterior", resultado.loc["G", "Motivo"])

    def test_dupla_leitura_mesmo_id_escolhe_horario_mais_recente(self):
        original = pd.DataFrame([
            base_exemplo("A", 3, "25/11/2026 06:00"),
            base_exemplo("A", 8, "25/11/2026 08:00"),
            base_exemplo("A", 4, "25/11/2026 07:00"),
        ])
        resultado = preparar_base(original, "duplicados.xlsx")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["Pressao"], 8)

    def test_leitura_em_memoria_csv_e_xlsx(self):
        original = pd.DataFrame([base_exemplo("A", 7, "25/11/2026 08:00")])
        conteudo_csv = original.to_csv(index=False, sep=";").encode("utf-8-sig")
        bio = io.BytesIO()
        original.to_excel(bio, index=False)
        csv = consolidar_arquivos([("dados.csv", conteudo_csv)])
        xlsx = consolidar_arquivos([("dados.xlsx", bio.getvalue())])
        self.assertEqual(csv.iloc[0]["Pressao"], 7)
        self.assertEqual(xlsx.iloc[0]["Pressao"], 7)
        self.assertEqual(csv.iloc[0]["Galaxia ID"], "A")

    def test_nao_aceita_arquivo_sem_id(self):
        with self.assertRaises(ErroBaseLogger):
            preparar_base(pd.DataFrame({
                "Pressão":[2], "Data da última medição":["25/11/2026 08:00"]
            }), "sem_id.xlsx")


def historico_ficticio(ultima_pressao=14):
    # 5 dias anteriores, 5 medições em cada dia no entorno do horário 08:00.
    linhas = []
    for dia in range(1, 6):
        for minuto in (-10,-5,0,5,10):
            horario = pd.Timestamp(2026, 11, dia, 8, 0) + pd.Timedelta(minutes=minuto)
            linhas.append({"data":horario,"pressao":14.0})
    linhas.append({
        "data":pd.Timestamp("2026-11-06 08:00"),
        "pressao":float(ultima_pressao),
    })
    return pd.DataFrame(linhas).sort_values("data").reset_index(drop=True)


class TesteComportamento(unittest.TestCase):
    def test_dentro_fora_e_insuficiente(self):
        dentro = padrao_horario(historico_ficticio(14))
        fora = padrao_horario(historico_ficticio(0))
        self.assertEqual(dentro.classificacao, "Dentro do padrão")
        self.assertEqual(fora.classificacao, "Fora do padrão")
        self.assertEqual(dentro.medicoes_referencia, 25)
        self.assertEqual(dentro.dias_referencia, 5)
        curto = historico_ficticio().iloc[:12].copy()
        self.assertEqual(padrao_horario(curto).classificacao, "Dados insuficientes")

    def test_detecta_queda_persistente_90min(self):
        df = pd.DataFrame({
            "data":pd.to_datetime([
                "2026-11-25 07:30", "2026-11-25 07:40",
                "2026-11-25 07:45", "2026-11-25 07:50",
                "2026-11-25 08:00",
            ]),
            "pressao":[18,18,10,10,10],
        })
        texto = variacao_recente(df)
        self.assertIn("queda acentuada", texto)
        self.assertIn("8,0 mca", texto)

    def test_coluna_pressao_e_filtro_loggers_historicos(self):
        dados = []
        for idx in range(8):
            horario = pd.Timestamp("2026-11-25 07:30") + pd.Timedelta(minutes=idx*5)
            dados.append({
                "Galaxia ID": "LOGGER-A", "Data": horario,
                "ID": 9999+idx, "Pressão (mca)": 12+idx,
            })
            dados.append({
                "Galaxia ID": "LOGGER-B", "Data": horario,
                "ID": 8888+idx, "Pressão (mca)": 2+idx,
            })
        hist, coluna, selecionado = extrair_historico(pd.DataFrame(dados), "LOGGER-A")
        self.assertEqual(coluna, "Pressão (mca)")
        self.assertEqual(selecionado, "LOGGER-A")
        self.assertEqual(len(hist),8)
        self.assertEqual(hist["pressao"].max(),19)
        self.assertIn("padrão", diagnosticar(hist)["diagnostico"])

    def test_historico_multilogger_exige_selecao(self):
        original=pd.DataFrame([
            {"Galaxia ID":"A","Data":"25/11/2026 08:00","Pressao":3},
            {"Galaxia ID":"B","Data":"25/11/2026 08:10","Pressao":4},
        ])
        with self.assertRaisesRegex(ErroBaseLogger,"Selecione"):
            extrair_historico(original)

    def test_integracao_nova_pagina_e_menu_m4(self):
        entrada=(ROOT/"pages"/"4_Ferramentas_Operacionais.py").read_text(encoding="utf-8")
        tela=(ROOT/"pages"/"4_4_Analise_de_Logger.py").read_text(encoding="utf-8")
        self.assertIn('st.switch_page("pages/4_4_Analise_de_Logger.py")',entrada)
        self.assertIn('exigir_acesso_modulo("4")',tela)
        self.assertIn('st.tabs(["📈 Monitor de Variação", "📉 Análise de Comportamento"])',tela)
        self.assertIn('accept_multiple_files=True',tela)
        self.assertIn('comparar_bases',tela)
        self.assertIn('diagnosticar',tela)
        self.assertNotIn('google.colab',tela)
        self.assertNotIn('ipywidgets',tela)


if __name__ == "__main__":
    unittest.main()
