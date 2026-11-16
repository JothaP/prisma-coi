"""Smoke test do protótipo Streamlit fora do Hub e sem fontes reais."""
import unittest

from streamlit.testing.v1 import AppTest


class TesteInterfaceFarolV3(unittest.TestCase):
    def test_abertura_e_mapa_com_dados_ficticios(self):
        app = AppTest.from_string(
            "from farol_v3_teste import renderizar\nrenderizar()\n",
            default_timeout=30,
        )
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any(
            "Farol Operacional V3" in str(x.message)
            for x in app.subheader
        ))
        demo = next(b for b in app.button if "Carregar exemplo fictício" in b.label)
        demo.click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(
            len(app.session_state["farol_v3_teste_operacao"]["concentracoes"]), 2,
        )

    def test_fichas_antigas_acessiveis_sem_upload(self):
        app = AppTest.from_string(
            "from farol_v3_teste import renderizar\nrenderizar()\n",
            default_timeout=30,
        )
        app.run()
        app.session_state["farol_v3_teste_fichas"] = {
            "a" * 24: {
                "chave": "a" * 24, "dia": "2026-11-15",
                "codigo": "THE C001", "municipio": "TERESINA",
                "bairros": "BAIRRO A", "qtd_os": 5,
                "status": "Em acompanhamento", "descricao": "Teste",
                "movimentacoes": [], "atualizado_em": "15/11/2026 18:00",
            }
        }
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any(
            "fichas experimentais" in str(x.value).lower()
            for x in app.info
        ))


if __name__ == "__main__":
    unittest.main()
