"""Regressões da seleção de histórico de logger após upload múltiplo."""
from __future__ import annotations

import io
from pathlib import Path
import unittest

import pandas as pd

from logger_lote_core import catalogar_historicos, analisar_selecionado
from logger_monitor_core import ErroBaseLogger

ROOT = Path(__file__).resolve().parents[1]


def referencia(id_logger, valor=12.0, ultimo=12.0):
    linhas = []
    for dia in range(1, 7):
        for minuto in (0, 5, 10, 15, 20):
            dt = pd.Timestamp(2026, 11, dia, 10, 0) + pd.Timedelta(minutes=minuto)
            linhas.append({
                "Galaxia ID": id_logger,
                "Data": dt,
                "Pressão (mca)": float(valor),
            })
    linhas.append({
        "Galaxia ID": id_logger,
        "Data": pd.Timestamp("2026-11-25 10:26"),
        "Pressão (mca)": float(ultimo),
    })
    return pd.DataFrame(linhas)


def xlsx(df):
    saida = io.BytesIO()
    df.to_excel(saida, index=False)
    return saida.getvalue()


class TesteSeletorLogger(unittest.TestCase):
    def test_um_upload_multiplos_arquivos_e_selecao_isolada(self):
        a = referencia("LOG-001", valor=16, ultimo=3)
        b = referencia("LOG-002", valor=9, ultimo=20)
        catalogo, origens, falhas = catalogar_historicos([
            ("primeiro.xlsx", xlsx(a)),
            ("segundo.xlsx", xlsx(b)),
        ])
        self.assertEqual(set(catalogo), {"LOG-001", "LOG-002"})
        self.assertEqual(falhas, [])
        self.assertEqual(origens["LOG-001"], ["primeiro.xlsx"])
        self.assertEqual(origens["LOG-002"], ["segundo.xlsx"])

        serie_a, coluna_a, resultado_a = analisar_selecionado(catalogo, "LOG-001")
        serie_b, coluna_b, resultado_b = analisar_selecionado(catalogo, "LOG-002")
        self.assertEqual(len(serie_a), len(a))
        self.assertEqual(len(serie_b), len(b))
        self.assertEqual(coluna_a, "Pressão (mca)")
        self.assertEqual(coluna_b, "Pressão (mca)")
        self.assertEqual(resultado_a["direcao"], "Abaixo do padrão")
        self.assertEqual(resultado_b["direcao"], "Acima do padrão")
        self.assertEqual(resultado_a["pressao_ultima"], 3)
        self.assertEqual(resultado_b["pressao_ultima"], 20)

    def test_arquivo_com_varios_galaxia_id_exibe_opcoes_separadas(self):
        a = referencia("FFDA78", 12, 12)
        b = referencia("FFDA79", 14, 14)
        catalogo, _, falhas = catalogar_historicos([
            ("juntos.xlsx", xlsx(pd.concat([a, b], ignore_index=True))),
        ])
        self.assertEqual(falhas, [])
        self.assertEqual(set(catalogo), {"FFDA78", "FFDA79"})
        selecao, _, diagnostico = analisar_selecionado(catalogo, "FFDA78")
        self.assertEqual(len(selecao), len(a))
        self.assertEqual(diagnostico["pressao_ultima"], 12)

    def test_historico_mesmo_logger_em_arquivos_diferentes(self):
        serie = referencia("MESMO-ID", 11, 15)
        catalogo, origens, _ = catalogar_historicos([
            ("parte_1.xlsx", xlsx(serie.iloc[:13])),
            ("parte_2.xlsx", xlsx(serie.iloc[13:])),
        ])
        self.assertEqual(list(catalogo), ["MESMO-ID"])
        self.assertEqual(len(origens["MESMO-ID"]), 2)
        dados, _, resultado = analisar_selecionado(catalogo, "MESMO-ID")
        self.assertEqual(len(dados), len(serie))
        self.assertEqual(resultado["quantidade"], len(serie))

    def test_arquivos_sem_id_ficam_separados_ate_com_nome_repetido(self):
        um = referencia("A", 10, 10).drop(columns=["Galaxia ID"])
        dois = referencia("B", 17, 17).drop(columns=["Galaxia ID"])
        catalogo, origens, _ = catalogar_historicos([
            ("historico.xlsx", xlsx(um)),
            ("historico.xlsx", xlsx(dois)),
        ])
        self.assertEqual(set(catalogo), {"Arquivo: historico", "Arquivo: historico (2)"})
        _, _, r1 = analisar_selecionado(catalogo, "Arquivo: historico")
        _, _, r2 = analisar_selecionado(catalogo, "Arquivo: historico (2)")
        self.assertEqual(r1["pressao_ultima"], 10)
        self.assertEqual(r2["pressao_ultima"], 17)

    def test_arquivo_invalido_nao_impede_selecao_do_valido(self):
        catalogo, origens, falhas = catalogar_historicos([
            ("bom.xlsx", xlsx(referencia("LOG-OK"))),
            ("invalido.xlsx", b"nao e planilha"),
        ])
        self.assertEqual(set(catalogo), {"LOG-OK"})
        self.assertEqual(len(falhas), 1)
        self.assertIn("invalido.xlsx", falhas[0])
        _, _, resultado = analisar_selecionado(catalogo, "LOG-OK")
        self.assertEqual(resultado["direcao"], "Dentro do padrão")

    def test_nao_analisa_ids_inexistentes(self):
        catalogo, _, _ = catalogar_historicos([
            ("ok.xlsx", xlsx(referencia("LOG-OK"))),
        ])
        with self.assertRaisesRegex(ErroBaseLogger, "Selecione"):
            analisar_selecionado(catalogo, "INEXISTENTE")

    def test_limite_especifico_so_aplicado_ao_selecionado(self):
        a = referencia("A", 7, 7)
        b = referencia("B", 12, 12)
        catalogo, _, _ = catalogar_historicos([
            ("a.xlsx", xlsx(a)),
            ("b.xlsx", xlsx(b)),
        ])
        _, _, r1 = analisar_selecionado(catalogo, "A", limiar_min_mca=10)
        _, _, r2 = analisar_selecionado(catalogo, "B")
        self.assertEqual(r1["prioridade"], "Atenção")
        self.assertEqual(r2["prioridade"], "Sem sinal histórico de queda")

    def test_tela_tem_um_upload_e_um_seletor_sem_painel_lote(self):
        fonte = (ROOT / "pages" / "4_4_Analise_de_Logger.py").read_text(
            encoding="utf-8"
        )
        tela = fonte[fonte.index("def _comportamento():"):fonte.index("with st.sidebar:")]
        self.assertEqual(tela.count("st.file_uploader("), 1)
        self.assertEqual(tela.count("st.selectbox("), 1)
        self.assertIn("accept_multiple_files=True", tela)
        self.assertIn("Selecione o logger para analisar", tela)
        self.assertIn("analisar_selecionado(", tela)
        self.assertNotIn('["Individual", "Em lote"]', tela)
        self.assertNotIn("Exportar diagnóstico consolidado", tela)
        self.assertIn('_mostrar_diagnostico(dados, diagnostico, escolhido)', tela)
        self.assertIn('key="btn_download_manual_analise_logger"', fonte)


if __name__ == "__main__":
    unittest.main()
