"""Contrato da sidebar para leitura do manual institucional PDF versionado."""
from pathlib import Path
import unittest
import hashlib

ROOT=Path(__file__).resolve().parents[1]
PAGINA=ROOT/"pages"/"4_4_Analise_de_Logger.py"
MANUAL=ROOT/"manuais"/"Manual_de_Uso_Analise_de_Logger_PRISMA_COI_Aegea.pdf"
SHA256_ORIGINAL="0fc4a60d649c0b6ce0fb41807ab0ae989658966118ccd42b767c9a26b13868d7"


class TesteManualSidebar(unittest.TestCase):
    def test_sidebar_faz_download_de_pdf_local_sem_url_externa(self):
        fonte=PAGINA.read_text(encoding="utf-8")
        self.assertIn('Path(__file__).resolve().parent.parent', fonte)
        self.assertIn('"manuais"', fonte)
        self.assertIn('"Manual_de_Uso_Analise_de_Logger_PRISMA_COI_Aegea.pdf"', fonte)
        self.assertIn("caminho_manual.read_bytes()", fonte)
        self.assertIn('bytes_manual.startswith(b"%PDF")', fonte)
        self.assertIn("st.download_button(", fonte)
        self.assertIn('mime="application/pdf"', fonte)
        self.assertIn('key="btn_download_manual_analise_logger"', fonte)
        self.assertIn("disabled=True", fonte)

    @unittest.skipUnless(MANUAL.is_file(), "O PDF ainda não foi incluído no GitHub.")
    def test_pdf_publicado_corresponde_ao_manual_aprovado(self):
        conteudo=MANUAL.read_bytes()
        self.assertTrue(conteudo.startswith(b"%PDF-"))
        self.assertEqual(hashlib.sha256(conteudo).hexdigest(), SHA256_ORIGINAL)
        self.assertGreater(len(conteudo), 100_000)


if __name__=="__main__":
    unittest.main()
