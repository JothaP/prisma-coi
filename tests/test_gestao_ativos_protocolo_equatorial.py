"""Testes dos campos de protocolo Equatorial na Gestão de Ativos.

Inspeciona formulário e valida backend sem conectar ao Supabase.
"""
import ast
from pathlib import Path
import unittest


RAIZ = Path(__file__).resolve().parents[1]
FICHA = RAIZ / "pages" / "10_Gestao_de_Ativos.py"
BACKEND = RAIZ / "gestao_ativos_backend.py"


def _backend_sem_banco():
    arvore = ast.parse(BACKEND.read_text(encoding="utf-8"))
    nos = [
        no for no in arvore.body
        if (isinstance(no, ast.Assign)
            and any(isinstance(dest, ast.Name) and dest.id in {"CAMPOS_EDITAVEIS", "STATUS"}
                    for dest in no.targets))
        or (isinstance(no, ast.FunctionDef) and no.name == "_validar_campos")
    ]
    if len(nos) != 3:
        raise AssertionError("Não foi possível isolar CAMPOS_EDITAVEIS, STATUS e _validar_campos")
    codigo = compile(ast.fix_missing_locations(ast.Module(body=nos, type_ignores=[])),
                     str(BACKEND), "exec")
    contexto = {"ErroPRISMA": ValueError}
    exec(codigo, contexto)
    return contexto


def _cadastro_base():
    return {
        "planta": "PL-THE-PCO0001", "nome": None, "matricula": None,
        "status": "parado", "ultima_os_operacao": None,
        "data_ultima_os_operacao": None, "ultima_os_eletromecanica": None,
        "data_ultima_os_eletromecanica": None, "observacao": None,
        "municipio": "TERESINA",
    }


class TestGestaoAtivosProtocoloEquatorial(unittest.TestCase):
    def test_novos_campos_editaveis_e_validacao(self):
        backend = _backend_sem_banco()
        self.assertIn("protocolo_equatorial", backend["CAMPOS_EDITAVEIS"])
        self.assertIn("data_protocolo_equatorial", backend["CAMPOS_EDITAVEIS"])
        registro = _cadastro_base()
        registro.update({
            "protocolo_equatorial": "  EQ-2026-12345  ",
            "data_protocolo_equatorial": "2026-11-17",
        })
        dados = backend["_validar_campos"](registro)
        self.assertEqual(dados["protocolo_equatorial"], "EQ-2026-12345")
        self.assertEqual(dados["data_protocolo_equatorial"], "2026-11-17")
        self.assertEqual(dados["status"], "parado")

    def test_campos_opcionais_para_ativos_existentes(self):
        backend = _backend_sem_banco()
        dados = backend["_validar_campos"](_cadastro_base())
        self.assertIsNone(dados["protocolo_equatorial"])
        self.assertIsNone(dados["data_protocolo_equatorial"])

    def test_ficha_apresenta_campos_no_local_correto(self):
        codigo = FICHA.read_text(encoding="utf-8")
        formulario = codigo.split("def _campos_formulario(", 1)[1].split(
            "def _limpar_cache(", 1)[0]
        self.assertLess(formulario.index('"Data — Eletromecânica"'),
                        formulario.index('"Protocolo Equatorial"'))
        self.assertLess(formulario.index('"Protocolo Equatorial"'),
                        formulario.index('"Data — Protocolo"'))
        self.assertLess(formulario.index('"Data — Protocolo"'),
                        formulario.index('"Município"'))
        self.assertIn('"protocolo_equatorial": protocolo_equatorial', formulario)
        self.assertIn('"data_protocolo_equatorial": (', formulario)
        self.assertIn('data_protocolo_equatorial.isoformat()', formulario)

    def test_listagem_e_edicao_preservam_campos(self):
        codigo = BACKEND.read_text(encoding="utf-8")
        self.assertIn('"protocolo_equatorial,data_protocolo_equatorial,"', codigo)
        self.assertIn('dados = _validar_campos(campos)', codigo)
        self.assertIn('registros = _tabela("prisma_ativos", method="PATCH"', codigo)
        self.assertIn('registros = _tabela("prisma_ativos", method="POST"', codigo)
        self.assertIn('campos = {campo: atual.get(campo) for campo in CAMPOS_EDITAVEIS}',
                      FICHA.read_text(encoding="utf-8"))

    def test_sintaxe(self):
        ast.parse(FICHA.read_text(encoding="utf-8"))
        ast.parse(BACKEND.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
