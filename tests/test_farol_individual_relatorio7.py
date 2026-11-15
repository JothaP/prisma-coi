"""Regressões da simplificação M3/M7: fichas individuais e PDF sem página de eventos."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAROL = ROOT / "pages" / "3_Farol_Operacional.py"
MODULO7 = ROOT / "pages" / "7_Relatorios_Integrados.py"
POPUP = ROOT / "acompanhamento_operacional_farol.py"


def _resumo_eventos():
    arvore = ast.parse(MODULO7.read_text(encoding="utf-8"))
    funcoes = [
        item for item in arvore.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "ri7_resumo_eventos_relacionados"
    ]
    if len(funcoes) != 1:
        raise AssertionError("Resumo de eventos do Módulo 7 ausente/duplicado")
    escopo = {}
    exec(compile(ast.Module(body=funcoes, type_ignores=[]), "<ri7>", "exec"), escopo)
    return escopo["ri7_resumo_eventos_relacionados"]


class TesteConcentracoesIndividuais(unittest.TestCase):
    def test_painel_farol_sem_mesclagem_nem_desenho(self):
        codigo = FAROL.read_text(encoding="utf-8")
        for trecho in (
            "renderizar_painel_mescla(", "aplicar_mesclas_farol(",
            "renderizar_painel_desfazer_mesclas(", "last_active_drawing",
            "adicionar_desenho_mapa(", "sincronizar_ocorrencias_lote(",
            "Desenhar polígono", "Mesclar concentrações",
        ):
            self.assertNotIn(trecho, codigo)
        self.assertIn("criar_concentracoes(", codigo)
        self.assertIn("📋 Copiar dados de outra concentração", codigo)
        self.assertIn("chave_atual = chave_concentracao(", codigo)

    def test_copia_explicita_nao_altera_status_origem(self):
        codigo = POPUP.read_text(encoding="utf-8")
        self.assertIn("Aplicar cópia ao formulário", codigo)
        self.assertIn("st.session_state[chave_copia_estado]", codigo)
        self.assertIn('status_para_interface(evento.get("status", ""))', codigo)
        self.assertNotIn('status_para_interface(dados_iniciais.get("status", ""))', codigo)
        self.assertIn('contexto_independente = dict(contexto, evento_chave="")', codigo)
        self.assertIn("sb_separar(", codigo)
        self.assertIn("seu historico permanecem", codigo.lower().replace("histórico", "historico"))

    def test_resumo_evento_prioriza_descricao_e_ativo(self):
        resumo = _resumo_eventos()
        valor = resumo([
            {
                "Protocolo": "", "Serviço": "Manutenção de Rede",
                "Descrição do Serviço": "Reparo de adutora",
                "Código do Ativo": "PL-THE-123",
            }
        ])
        self.assertEqual(valor, "1 — Reparo de adutora / PL-THE-123")
        self.assertNotIn("Protocolo", valor)

    def test_resumo_sem_ativo_usa_descricao(self):
        resumo = _resumo_eventos()
        self.assertEqual(
            resumo([{"Descrição do Serviço": "Correção elétrica", "Código do Ativo": ""}]),
            "1 — Correção elétrica",
        )
        self.assertEqual(
            resumo([{"Serviço": "Manutenção", "Código do Ativo": "Não informado"}]),
            "1 — Manutenção",
        )
        self.assertEqual(resumo([]), "Nenhum evento relacionado")

    def test_pdf_integrado_sem_paginas_de_eventos_ou_mesclagem(self):
        codigo = MODULO7.read_text(encoding="utf-8")
        self.assertIn("ri7_resumo_eventos_relacionados(eventos)", codigo)
        self.assertNotIn("ri7_pagina_eventos_continuacao(", codigo)
        self.assertNotIn("aplicar_mesclagens_do_modulo_3(", codigo)
        self.assertNotIn("ri7_desenhar_area_mesclada(", codigo)
        self.assertIn("ri7_pagina_descricao_continuacao(", codigo)


if __name__ == "__main__":
    unittest.main()
