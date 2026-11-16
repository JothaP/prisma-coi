"""Regressões do Farol V3 em teste: um dia, um recipiente, sem escrita em produção."""
from datetime import date, timedelta
from pathlib import Path
import unittest

import pandas as pd

from farol_v3_nucleo_teste import (
    preparar_os_do_dia, concentracoes_do_dia, eventos_pertinentes,
    ativos_proximos, pressao_proxima, distancia_m,
)

DIA = date(2026, 11, 16)
ROOT = Path(__file__).resolve().parents[1]


def _linha(os, bairro, lat, lon, dia="16/11/2026 08:00", municipio="Teresina"):
    return {
        "Nº da O.S": os, "Matrícula": str(os),
        "Cidade": municipio, "Bairro": bairro,
        "Latitude": lat, "Longitude": lon,
        "Dt. Emissão": dia, "Status OS": "Aberta",
    }


def dados_teste():
    a = [
        _linha(f"FA-{i}", "BAIRRO A", f"{-5.09+i*.0003}", f"{-42.80+i*.0003}")
        for i in range(4)
    ]
    b = [
        _linha(f"FB-{i}", "BAIRRO B", f"{-5.13+i*.0002}", f"{-42.83+i*.0002}")
        for i in range(3)
    ]
    return pd.DataFrame(
        a + b + [
            _linha("OUTRA-DATA", "BAIRRO A", -5.0902, -42.8002, "15/11/2026 09:00"),
            _linha("SEM-GPS", "BAIRRO B", "", "", "16/11/2026 11:00"),
            dict(a[0]),
        ]
    )


class TesteNucleoFarolV3(unittest.TestCase):
    def test_descartar_outras_datas_antes_de_agrupar(self):
        pontos, info = preparar_os_do_dia(dados_teste(), DIA)
        self.assertEqual(info["ignorados_fora_da_data"], 1)
        self.assertEqual(info["duplicados"], 1)
        self.assertEqual(info["sem_coordenadas"], 1)
        self.assertEqual(info["validos"], 8)
        self.assertEqual(len(pontos), 8)
        self.assertNotIn("OUTRA-DATA", pontos["os"].tolist())

    def test_duas_concentracoes_independentes(self):
        pontos, _ = preparar_os_do_dia(dados_teste(), DIA)
        concs, resultado = concentracoes_do_dia(pontos, DIA)
        self.assertEqual(len(concs), 2)
        self.assertEqual(sorted(concs["qtd_os"].tolist()), [3, 4])
        self.assertEqual(len(set(concs["chave"])), 2)
        self.assertEqual(len(resultado[resultado["_chave"] != ""]), 7)

    def test_mesmo_bairro_nao_implica_mesma_concentracao(self):
        a = [
            _linha(f"A-{i}", "MESMO BAIRRO", -5.09 + i*.0002, -42.80)
            for i in range(3)
        ]
        b = [
            _linha(f"B-{i}", "MESMO BAIRRO", -5.20 + i*.0002, -42.90)
            for i in range(3)
        ]
        pontos, _ = preparar_os_do_dia(pd.DataFrame(a + b), DIA)
        concs, _ = concentracoes_do_dia(pontos, DIA)
        self.assertEqual(len(concs), 2)
        self.assertEqual(concs.iloc[0]["bairros"], concs.iloc[1]["bairros"])
        self.assertNotEqual(concs.iloc[0]["chave"], concs.iloc[1]["chave"])

    def test_nao_mesclar_municipios(self):
        a = [
            _linha(f"THE-{i}", "Bairro A", -5.09, -42.80) for i in range(3)
        ]
        b = [
            _linha(f"ALT-{i}", "Bairro A", -5.09, -42.80, municipio="Altos")
            for i in range(3)
        ]
        pontos, _ = preparar_os_do_dia(pd.DataFrame(a + b), DIA)
        concs, _ = concentracoes_do_dia(pontos, DIA)
        self.assertEqual(len(concs), 2)

    def test_chave_estavel_na_mesma_data_e_dados(self):
        dados = dados_teste()
        a, _ = preparar_os_do_dia(dados, DIA)
        b, _ = preparar_os_do_dia(dados.sample(frac=1, random_state=42), DIA)
        c1, _ = concentracoes_do_dia(a, DIA)
        c2, _ = concentracoes_do_dia(b, DIA)
        self.assertEqual(set(c1["chave"]), set(c2["chave"]))
        outro_dia, _ = concentracoes_do_dia(a, DIA + timedelta(days=1))
        self.assertNotEqual(set(c1["chave"]), set(outro_dia["chave"]))

    def test_eventos_somente_pertinentes_ao_dia_e_bairro(self):
        pontos, _ = preparar_os_do_dia(dados_teste(), DIA)
        concs, _ = concentracoes_do_dia(pontos, DIA)
        conc = next(c for c in concs.to_dict("records") if c["bairros"] == "BAIRRO A")
        eventos = pd.DataFrame([
            {"Cidade": "Teresina", "Áreas Impactadas": "BAIRRO A",
             "Início": "15/11/2026 20:00", "Término Real": "",
             "Status": "Em andamento", "Descrição do Serviço": "Reparo de adutora",
             "Código do Ativo": "THE-01"},
            {"Cidade": "Teresina", "Áreas Impactadas": "BAIRRO A",
             "Início": "13/11/2026 08:00", "Término Real": "14/11/2026 11:00",
             "Status": "Concluído", "Descrição do Serviço": "Manutenção encerrada"},
            {"Cidade": "Teresina", "Áreas Impactadas": "BAIRRO C",
             "Início": "16/11/2026 08:00", "Término Real": "",
             "Status": "Em andamento", "Descrição do Serviço": "Bairro diferente"},
        ])
        encontrados = eventos_pertinentes(eventos, conc, DIA)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0]["ativo"], "THE-01")

    def test_ativos_e_pressao_sob_demanda(self):
        pontos, _ = preparar_os_do_dia(dados_teste(), DIA)
        concs, _ = concentracoes_do_dia(pontos, DIA)
        conc = next(c for c in concs.to_dict("records") if c["bairros"] == "BAIRRO A")
        ativos = pd.DataFrame([
            {"MUNICIPIO": "Teresina", "LATITUDE": -5.09, "LONGITUDE": -42.80,
             "IDENTIFICACAO_ATIVO": "POCO-01"},
            {"MUNICIPIO": "Teresina", "LATITUDE": -5.20, "LONGITUDE": -42.90,
             "IDENTIFICACAO_ATIVO": "POCO-DISTANTE"},
        ])
        self.assertEqual(len(ativos_proximos(ativos, conc)), 1)
        pressao = pd.DataFrame([
            {"Data": "16/11/2026 09:00", "Municipio": "Teresina",
             "Latitude": -5.09, "Longitude": -42.80, "Pressao_MCA": "2,5"},
            {"Data": "15/11/2026 09:00", "Municipio": "Teresina",
             "Latitude": -5.09, "Longitude": -42.80, "Pressao_MCA": "2,5"},
        ])
        encontrados = pressao_proxima(pressao, conc, DIA)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0]["pressao_mca"], 2.5)
        self.assertLess(distancia_m(-5.09, -42.80, -5.0901, -42.8001), 20)

    def test_ambiente_de_teste_isolado_do_modulo_3(self):
        pagina = (ROOT / "pages" / "Ambiente_de_Teste.py").read_text("utf-8")
        ui = (ROOT / "farol_v3_teste.py").read_text("utf-8")
        self.assertIn('if modo_teste == "Farol Operacional V3 — Diário":', pagina)
        self.assertIn('from farol_v3_teste import renderizar', pagina)
        self.assertIn('    st.stop()', pagina)
        self.assertNotIn("farol_acomp_supabase_backend", ui)
        self.assertNotIn("integracao_acompanhamento_producao", ui)
        self.assertNotIn("supabase.create", ui)
        self.assertIn("st.session_state[CHAVE_FICHAS]", ui)
        self.assertIn("Copiar campos operacionais", ui)
        self.assertIn("Exportar fichas de teste", ui)


if __name__ == "__main__":
    unittest.main()
