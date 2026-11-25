"""Testes offline de links Google Maps e integracao com Consulta Operacional."""
import unittest
from pathlib import Path

from m8_links_google_maps import extrair_link, extrair_lista_links, validar_url

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pages" / "8_Consulta_Operacional.py").read_text(encoding="utf-8")


class Resposta:
    def __init__(self, status, destino=None):
        self.status_code = status
        self.headers = {"Location": destino} if destino else {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SessaoFalsa:
    def __init__(self, destinos):
        self.destinos = destinos
        self.chamadas = []

    def get(self, url, **kwargs):
        self.chamadas.append((url, kwargs))
        return self.destinos[url]


class TestarLinksGoogleMaps(unittest.TestCase):
    def test_prioriza_local_marcado_em_vez_do_centro(self):
        url = ("https://www.google.com/maps/place/Endereco/"
               "@-5.100000,-42.100000,17z/data=!3d-5.144740!4d-42.788570")
        par, origem = extrair_link(url)
        self.assertEqual(par, (-5.14474, -42.78857))
        self.assertEqual(origem, "Local marcado")

    def test_links_explicitos_sem_rede(self):
        links = [
            "https://www.google.com/maps/search/?api=1&query=-5.12%2C-42.78",
            "https://www.google.com/maps/place/-5.14,-42.79",
            "https://maps.google.com/?q=-5.15,-42.80",
        ]
        esperado = [(-5.12, -42.78), (-5.14, -42.79), (-5.15, -42.8)]
        self.assertEqual([extrair_link(link)[0] for link in links], esperado)

    def test_centro_apenas_quando_explicitamente_aceito(self):
        url = "https://www.google.com/maps/@-5.15,-42.80,17z"
        with self.assertRaisesRegex(ValueError, "Sem coordenada"):
            extrair_link(url)
        self.assertEqual(extrair_link(url, aceitar_centro=True)[0], (-5.15, -42.8))

    def test_link_encurtado_valida_todos_redirecionamentos(self):
        curto = "https://maps.app.goo.gl/ABCDE"
        longo = "https://www.google.com/maps/place/Local/data=!3d-5.123!4d-42.456"
        sessao = SessaoFalsa({curto: Resposta(302, longo), longo: Resposta(200)})
        self.assertEqual(extrair_link(curto, sessao=sessao)[0], (-5.123, -42.456))
        self.assertTrue(all(not kw["allow_redirects"] for _, kw in sessao.chamadas))

    def test_bloqueia_host_estranho_e_redirecionamento_externo(self):
        for url in (
            "http://www.google.com/maps/@-5,-42",
            "https://www.google.com.evil.test/maps/@-5,-42",
            "https://127.0.0.1/maps/",
            "https://www.google.com:8443/maps/",
            "https://usuario@www.google.com/maps/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validar_url(url)
        curto = "https://maps.app.goo.gl/ABCDE"
        sessao = SessaoFalsa({curto: Resposta(302, "http://127.0.0.1/")})
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            extrair_link(curto, sessao=sessao)
        self.assertEqual(len(sessao.chamadas), 1)

    def test_rotas_nao_criam_ponto(self):
        with self.assertRaisesRegex(ValueError, "rotas"):
            extrair_link("https://www.google.com/maps/dir/-5.1,-42.8/-5.2,-42.9")

    def test_resultados_mantem_ordem_com_erro_intermediario(self):
        links = (
            "https://www.google.com/maps/?q=-5.1,-42.1\n"
            "https://outro-site.com/mapa\n"
            "https://www.google.com/maps/?q=-5.3,-42.3"
        )
        resultado = extrair_lista_links(links)
        self.assertEqual([x["status"] for x in resultado], ["OK", "ERRO", "OK"])
        self.assertEqual([x["ponto"] for x in resultado], ["P1", "—", "P2"])
        self.assertEqual([x["linha"] for x in resultado], [1, 2, 3])

    def test_limite_de_cem_links(self):
        url = "https://www.google.com/maps/?q=-5.1,-42.1"
        self.assertEqual(len(extrair_lista_links("\n".join([url] * 100))), 100)
        with self.assertRaisesRegex(ValueError, "100"):
            extrair_lista_links("\n".join([url] * 101))

    def test_integracao_preenche_estado_da_pesquisa_existente(self):
        self.assertIn('key="m8_links_google_maps_entrada"', SOURCE)
        self.assertIn('key="m8_links_aceitar_centro"', SOURCE)
        self.assertIn('st.session_state["m8_coordenadas_pesquisa"]', SOURCE)
        self.assertIn('st.session_state["m8_ponto_pesquisado"] = tuple(coordenadas_importadas)', SOURCE)
        self.assertIn('extrair_lista_links(', SOURCE)
        self.assertIn('key="m8_coordenadas_pesquisa"', SOURCE)
        self.assertIn("Matrícula mais próxima", SOURCE)


if __name__ == "__main__":
    unittest.main()
