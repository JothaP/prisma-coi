"""Valida agrupamento cartográfico no Módulo 1 sem acessar dados reais."""
from pathlib import Path
import re
import unittest

import folium
from folium.plugins import MarkerCluster

from bp_agrupamento_marcadores import (
    CORES_FAIXAS, FUNCAO_ICONE_CLUSTER, RAIO_CLUSTER_PX,
    ZOOM_ROTULOS_INDIVIDUAIS, opcoes_cluster, prioridade_pressao_mca,
)

ROOT = Path(__file__).resolve().parents[1]
MAPA = ROOT / "pages" / "1_Baixa_Pressao.py"


class TesteClustersBaixaPressao(unittest.TestCase):
    def test_limites_de_cinco_faixas_sem_media(self):
        casos = (
            (0, 5), (0.001, 4), (5, 4), (5.001, 3),
            (9.999, 3), (10, 2), (15, 2), (15.001, 1),
        )
        for pressao, esperado in casos:
            with self.subTest(pressao=pressao):
                self.assertEqual(prioridade_pressao_mca(pressao), esperado)

    def test_valores_invalidos_bloqueados(self):
        for pressao in (-1, float("nan"), float("inf"), "abc"):
            with self.subTest(pressao=pressao):
                with self.assertRaises(ValueError):
                    prioridade_pressao_mca(pressao)

    def test_paleta_iguais_as_do_mapa_original(self):
        mapa = MAPA.read_text(encoding="utf-8")
        nomes = {
            5: "COR_SEM_PRESSAO",
            4: "COR_BAIXA_PRESSAO",
            3: "COR_EM_ATENCAO",
            2: "COR_NORMAL",
            1: "COR_ALTA_PRESSAO",
        }
        for rank, nome in nomes.items():
            match = re.search(r"^" + nome + r'\\s*=\\s*"(#[0-9A-Fa-f]{6})"',
                              mapa, flags=re.MULTILINE)
            self.assertIsNotNone(match, nome)
            self.assertEqual(CORES_FAIXAS[rank], match.group(1))

    def test_cluster_preserva_prioridade_e_clica_para_aproximar(self):
        mapa = folium.Map(location=[-5.1, -42.8], zoom_start=10)
        grupo = MarkerCluster(
            icon_create_function=FUNCAO_ICONE_CLUSTER, **opcoes_cluster()
        ).add_to(mapa)
        folium.Marker(
            [-5.1, -42.8], bp_pressure_rank=5
        ).add_to(grupo)
        folium.Marker(
            [-5.1001, -42.8001], bp_pressure_rank=2
        ).add_to(grupo)
        html = mapa.get_root().render()
        self.assertIn('"bpPressureRank": 5', html)
        self.assertIn('"bpPressureRank": 2', html)
        self.assertIn("filhos[i].options.bpPressureRank", html)
        self.assertIn('"zoomToBoundsOnClick": true', html)
        self.assertIn('"spiderfyOnMaxZoom": true', html)
        self.assertIn('"maxClusterRadius": ' + str(RAIO_CLUSTER_PX), html)
        self.assertIn("piorFaixa = Math.max", html)
        self.assertIn("semPressao", html)
        self.assertIn('className: "bp-cluster-icon"', html)

    def test_modo_bairro_e_filtros_nao_mudam(self):
        texto = MAPA.read_text(encoding="utf-8")
        self.assertIn('"Por bairro"', texto)
        self.assertIn('"Medições individuais"', texto)
        self.assertIn("df_mapa = bp_filtrar_por_faixas(df_filtrado, faixas_mapa)", texto)
        self.assertIn("if modo_visualizacao == \"Medições individuais\":", texto)
        self.assertIn("bp_agrupador_individual = MarkerCluster(", texto)
        self.assertIn(").add_to(bp_agrupador_individual)", texto)
        self.assertIn("key=\"mapa_principal_pressao\"", texto)
        self.assertIn("bp_pressure_rank=prioridade_pressao_mca(pressao)", texto)
        self.assertIn("if modo_visualizacao == \"Medições individuais\" and mostrar_rotulos:",texto)

    def test_cartoes_somente_com_zoom_proximo_e_cluster_permanente(self):
        texto = MAPA.read_text(encoding="utf-8")
        self.assertEqual(ZOOM_ROTULOS_INDIVIDUAIS, 15)
        self.assertIn('zoom >= 15', texto)
        self.assertIn('if (mapa.getZoom() < 15) return;', texto)
        self.assertIn(".leaflet-container:not(.bp-zoom-detalhe-individual)", texto)
        self.assertIn("showCoverageOnHover", opcoes_cluster())
        self.assertEqual(opcoes_cluster()["showCoverageOnHover"], False)
        self.assertTrue(opcoes_cluster()["chunkedLoading"])
        self.assertTrue(opcoes_cluster()["removeOutsideVisibleBounds"])


if __name__ == "__main__":
    unittest.main()
