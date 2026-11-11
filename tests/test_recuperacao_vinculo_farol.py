"""Regressões de recuperação de vínculo de concentração dinâmica no Farol.

Carrega somente funções puras via AST; não acessa Streamlit, Supabase ou Google.
"""
import ast
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import unittest
import unicodedata
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parents[1]
SRC = RAIZ / "integracao_acompanhamento_producao.py"
TEXTO = SRC.read_text(encoding="utf-8")
NOMES = {
    "chave_concentracao", "_normalizar_municipio_vinculo",
    "_distancia_centros_m", "sugerir_recuperacoes",
}
arvore = ast.parse(TEXTO, filename=str(SRC))
funcoes = [n for n in arvore.body if isinstance(n, ast.FunctionDef) and n.name in NOMES]
assert {n.name for n in funcoes} == NOMES
ns = {
    "hashlib": hashlib, "json": json, "math": math, "re": re,
    "unicodedata": unicodedata, "datetime": datetime,
    "timedelta": timedelta, "ZoneInfo": ZoneInfo,
}
exec(compile(ast.Module(body=funcoes, type_ignores=[]), str(SRC), "exec"), ns)


class RecuperacaoVinculoTest(unittest.TestCase):
    def setUp(self):
        self.mais_cedo = {
            "chave": "a"*32,
            "evento_id": "101",
            "municipio": "TERESINA",
            "bairros": "Centro",
            "os_origem": "1001;1002;1003",
            "latitude": "-5.0900",
            "longitude": "-42.8000",
            "criado_em": "11/11/2026 11:05",
        }
        self.agora = datetime(2026, 11, 11, 14, 27)

    def opcoes(self, vinculos=None, cidade="Teresina",
               os=None, lat=-5.0905, lon=-42.8005,
               chaves_ativas=()):
        return ns["sugerir_recuperacoes"](
            vinculos if vinculos is not None else [self.mais_cedo],
            cidade, lat, lon,
            os if os is not None else ["1001", "1002", "1003", "1004"],
            chaves_ativas=chaves_ativas, agora=self.agora,
        )

    def test_chave_muda_quando_nova_os_entre(self):
        func = ns["chave_concentracao"]
        antiga = func("TERESINA", -5.09, -42.80, ["1001", "1002", "1003"])
        nova = func("TERESINA", -5.09, -42.80, ["1001", "1002", "1003", "1004"])
        self.assertNotEqual(antiga, nova)
        self.assertEqual(nova, func("TERESINA", -5.09, -42.80,
                                   ["1004", "1003", "1001", "1002"]))

    def test_recupera_caso_atual_com_nova_os(self):
        opcoes = self.opcoes()
        self.assertEqual(len(opcoes), 1)
        self.assertEqual(opcoes[0]["evento_id"], "101")
        self.assertEqual(opcoes[0]["os_em_comum"], 3)

    def test_chave_antiga_ativa_nao_sugerida(self):
        self.assertEqual(self.opcoes(chaves_ativas={"a"*32}), [])

    def test_municipios_diferentes_nao_sao_candidatos(self):
        self.assertEqual(self.opcoes(cidade="Timon"), [])

    def test_baixa_sobreposicao_nao_vincula(self):
        self.assertEqual(self.opcoes(os=["1001", "9000", "9001"]), [])
        self.assertEqual(self.opcoes(os=["1001", "1002", "2000", "2001", "2002"]), [])

    def test_centro_distante_nao_vincula(self):
        self.assertEqual(self.opcoes(lat=-5.2, lon=-42.8), [])

    def test_historico_muito_antigo_nao_vincula(self):
        antigo = dict(self.mais_cedo, criado_em="20/10/2026 11:05")
        self.assertEqual(self.opcoes(vinculos=[antigo]), [])

    def test_nao_reutiliza_data_futura(self):
        futuro = dict(self.mais_cedo, criado_em="12/11/2026 11:05")
        self.assertEqual(self.opcoes(vinculos=[futuro]), [])

    def test_identidade_mesma_ficha_deduplicada(self):
        outra = dict(self.mais_cedo, chave="b"*32,
                     os_origem="1001;1002", latitude="-5.0910")
        opcoes = self.opcoes(vinculos=[self.mais_cedo, outra])
        self.assertEqual(len(opcoes), 1)
        self.assertEqual(opcoes[0]["chave_anterior"], "a"*32)

    def test_invalida_coordenada_ausente(self):
        registro = dict(self.mais_cedo, latitude="")
        self.assertEqual(self.opcoes(vinculos=[registro]), [])

    def test_nao_cria_vinculo_automaticamente(self):
        pagina = (RAIZ / "pages" / "3_Farol_Operacional.py").read_text(encoding="utf-8")
        self.assertIn("Confirmar recuperação do vínculo", pagina)
        self.assertIn("if recuperar:", pagina)
        self.assertIn("if not confirmado:", pagina)
        self.assertIn("recuperar_vinculo_confirmado(", pagina)
        self.assertIn("possível divisão/fusão", pagina)
        self.assertIn("chaves_ativas=chaves_ativas", pagina)
        backend = (RAIZ / "farol_acomp_supabase_backend.py").read_text(encoding="utf-8")
        self.assertIn("/rest/v1/rpc/farol_acomp_recuperar_vinculo_tx", backend)


if __name__ == "__main__":
    unittest.main()
