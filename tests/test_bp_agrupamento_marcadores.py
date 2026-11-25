"""Valida agrupamento cartográfico no Módulo 1 sem acessar dados reais."""
import ast
from pathlib import Path
import re
import unittest

import folium
from branca.element import MacroElement
from jinja2 import Template
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
            (-1, 4), (0, 5), (0.001, 4), (5, 4), (5.001, 3),
            (9.999, 3), (10, 2), (15, 2), (15.001, 1),
        )
        for pressao, esperado in casos:
            with self.subTest(pressao=pressao):
                self.assertEqual(prioridade_pressao_mca(pressao), esperado)

    def test_valores_invalidos_bloqueados(self):
        for pressao in (float("nan"), float("inf"), "abc"):
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
            match = re.search(r"^" + nome + r'\s*=\s*"(#[0-9A-Fa-f]{6})"',
                              mapa, flags=re.MULTILINE)
            self.assertIsNotNone(match, nome)
            self.assertEqual(CORES_FAIXAS[rank], match.group(1))

    def test_cluster_preserva_prioridade_e_clica_para_aproximar(self):
        mapa = folium.Map(location=[-5.1, -42.8], zoom_start=10)
        grupo = MarkerCluster(
            icon_create_function=FUNCAO_ICONE_CLUSTER, **opcoes_cluster()
        ).add_to(mapa)
        folium.Marker(
            [-5.1, -42.8], bp_pressure_rank=5, bp_pressure_mca=0.0
        ).add_to(grupo)
        folium.Marker(
            [-5.1001, -42.8001], bp_pressure_rank=2, bp_pressure_mca=12.0
        ).add_to(grupo)
        html = mapa.get_root().render()
        self.assertIn('"bpPressureRank": 5', html)
        self.assertIn('"bpPressureRank": 2', html)
        self.assertIn('"bpPressureMca": 0.0', html)
        self.assertIn('"bpPressureMca": 12.0', html)
        self.assertIn("somaPressao / medicoesValidas", html)
        self.assertIn('media.toFixed(1).replace(".", ",")', html)
        self.assertIn('mediaTexto + \'</span>\'', html)
        self.assertIn("var valor = Number(opcoes.bpPressureRank || 1)", html)
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
        self.assertIn(").add_to(bp_destino_marcadores)", texto)
        self.assertIn("bp_destino_marcadores = bp_agrupador_individual", texto)
        self.assertIn("key=\"mapa_principal_pressao\"", texto)
        self.assertIn("bp_pressure_rank=prioridade_pressao_mca(pressao)", texto)
        self.assertIn("bp_pressure_mca=pressao", texto)
        self.assertIn('O **número** do grupo é a média das medições em MCA;', texto)
        self.assertIn("bp-cluster-unidade", texto)
        self.assertIn("if modo_visualizacao == \"Medições individuais\" and mostrar_rotulos:",texto)

    def test_caixa_sem_agrupamento_ao_lado_de_exibir_rotulos(self):
        texto = MAPA.read_text(encoding="utf-8")
        inicio = texto.index("c_map1, c_map2, c_map_faixas")
        fim = texto.index("# Este segundo filtro é EXCLUSIVO do mapa", inicio)
        ui = texto[inicio:fim]
        self.assertIn("c_map3, c_map_agrupamento, c_map4 = st.columns(", ui)
        self.assertIn('with c_map3:', ui)
        self.assertIn('with c_map_agrupamento:', ui)
        self.assertIn('"Exibir rótulos"', ui)
        self.assertIn('"Sem agrupamento"', ui)
        self.assertIn('key="bp_sem_agrupamento"', ui)
        self.assertIn('value=False', ui)
        self.assertIn('disabled=modo_visualizacao != "Medições individuais"', ui)
        self.assertIn('and bool(st.session_state.bp_sem_agrupamento)', ui)

    def test_sem_agrupamento_exibe_marcadores_individuais_sem_cluster(self):
        texto = MAPA.read_text(encoding="utf-8")
        inicio = texto.index('if modo_visualizacao == "Medições individuais":', texto.index("# VISUALIZAÇÃO DOS MARCADORES"))
        fim = texto.index("# POR BAIRRO", inicio)
        trecho = texto[inicio:fim]
        self.assertIn('if sem_agrupamento:\n                bp_destino_marcadores = m', trecho)
        self.assertIn('else:\n                bp_agrupador_individual = MarkerCluster(', trecho)
        self.assertIn('bp_destino_marcadores = bp_agrupador_individual', trecho)
        self.assertIn(').add_to(bp_destino_marcadores)', trecho)
        self.assertIn("if mostrar_rotulos:", trecho)
        self.assertIn('class="bp-label-card bp-label-card-individual"', trecho)
        self.assertIn('popup=folium.Popup(', trecho)
        self.assertIn('bp_pressure_rank=prioridade_pressao_mca(pressao)', trecho)

    def test_css_rotulos_visiveis_em_todos_zooms_quando_sem_agrupamento(self):
        texto = MAPA.read_text(encoding="utf-8")
        self.assertIn(
            ".leaflet-container:not(.bp-sem-agrupamento):not(.bp-zoom-detalhe-individual)",
            texto
        )
        self.assertIn(
            ".leaflet-container.bp-zoom-regional:not(.bp-sem-agrupamento) .bp-label-card",
            texto
        )
        self.assertIn(
            'container.classList.toggle("bp-sem-agrupamento", {{this.sem_agrupamento}});',
            texto
        )
        self.assertIn(
            'm.add_child(EscalaMarcadoresRotulosPorZoom(sem_agrupamento=sem_agrupamento))',
            texto
        )
        self.assertIn(
            'key="mapa_principal_pressao" if not sem_agrupamento else "mapa_principal_pressao_sem_agrupamento"',
            texto
        )
        # Verifica a cadeia de estado que distingue o mapa com e sem clusters.
        self.assertIn('"true" if sem_agrupamento else "false"', texto)

    def test_javascript_real_distingue_modo_sem_agrupamento(self):
        fonte = MAPA.read_text(encoding="utf-8")
        arvore = ast.parse(fonte, filename=str(MAPA))
        classe = next(
            item for item in arvore.body
            if isinstance(item, ast.ClassDef)
            and item.name == "EscalaMarcadoresRotulosPorZoom"
        )
        contexto = {"MacroElement": MacroElement, "Template": Template}
        exec(compile(ast.Module(body=[classe], type_ignores=[]), str(MAPA), "exec"), contexto)
        criar = contexto["EscalaMarcadoresRotulosPorZoom"]
        for sem_agrupamento, valor_js in ((False, "false"), (True, "true")):
            with self.subTest(sem_agrupamento=sem_agrupamento):
                mapa = folium.Map(location=[-5.1, -42.8], zoom_start=13)
                mapa.add_child(criar(sem_agrupamento=sem_agrupamento))
                html = mapa.get_root().render()
                self.assertIn(
                    'container.classList.toggle("bp-sem-agrupamento", '
                    + valor_js + ");",
                    html,
                )

    def test_reposicionamento_restaurado_quando_zoom_afasta(self):
        texto = MAPA.read_text(encoding="utf-8")
        self.assertIn("if (mapa.getZoom() < 15) {", texto)
        self.assertIn('rotulo.style.removeProperty("left");', texto)
        self.assertIn('rotulo.style.removeProperty("top");', texto)
        self.assertIn('rotulo.style.removeProperty("visibility");', texto)

    def test_zoom_agregado_preservado_por_padrao(self):
        texto = MAPA.read_text(encoding="utf-8")
        self.assertEqual(ZOOM_ROTULOS_INDIVIDUAIS, 15)
        self.assertIn('zoom >= 15', texto)
        self.assertIn('if (mapa.getZoom() < 15) return;', texto)
        self.assertIn(
            ".leaflet-container:not(.bp-sem-agrupamento):not(.bp-zoom-detalhe-individual)",
            texto
        )
        self.assertIn("showCoverageOnHover", opcoes_cluster())
        self.assertEqual(opcoes_cluster()["showCoverageOnHover"], False)
        self.assertTrue(opcoes_cluster()["chunkedLoading"])
        self.assertTrue(opcoes_cluster()["removeOutsideVisibleBounds"])


if __name__ == "__main__":
    unittest.main()
