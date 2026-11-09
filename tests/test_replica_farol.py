"""Testes isolados do espelho Supabase -> Google Sheets; sem credenciais reais."""
import unittest
from unittest.mock import patch

from scripts import replicar_acompanhamento_farol as replica


class PlanilhaFalsa:
    def __init__(self, cabecalho, linhas=()):
        self.cabecalho = list(cabecalho)
        self.linhas = [list(l) for l in linhas]
        self.lotes = []
        self.append_calls = []

    def get_all_values(self):
        return [list(self.cabecalho)] + [list(row) for row in self.linhas]

    def batch_update(self, updates, value_input_option):
        assert value_input_option == "RAW"
        self.lotes.extend(updates)
        for op in updates:
            pos = int(op["range"].split(":")[0][1:]) - 2
            self.linhas[pos] = list(op["values"][0])

    def append_rows(self, linhas, value_input_option):
        assert value_input_option == "RAW"
        self.append_calls.extend(linhas)
        self.linhas.extend([list(l) for l in linhas])


class PlanilhaLivro:
    def __init__(self, tabelas):
        self.tabelas = tabelas

    def worksheet(self, nome):
        return self.tabelas[nome]


class BancoFalso:
    def __init__(self, dados, eventos):
        self.dados = dados
        self.eventos = list(eventos)
        self.ack = []
        self.status = []
        self.erros = []

    def fila_pendente(self):
        return [dict(v) for v in self.eventos if v["id"] not in self.ack]

    def paginas(self, tabela, filtro=None):
        return [dict(v) for v in self.dados[tabela]]

    def por_chaves(self, tabela, chaves):
        return [dict(v) for v in self.dados[tabela]
                if str(v[replica.FONTES[tabela][1]]) in chaves]

    def confirmar(self, ids):
        self.ack.extend(ids)

    def pendentes(self):
        return len(self.fila_pendente())

    def salvar_status(self, sucesso, criados=0, atualizados=0, erro=""):
        self.status.append((sucesso, criados, atualizados, erro))


class TesteReplica(unittest.TestCase):
    def setUp(self):
        self.table = "farol_acomp_fichas"
        self.cols = replica.FONTES[self.table][2]
        self.ficha = {c: "" for c in self.cols}
        self.ficha.update({"id": "125", "titulo": "Atendimento validado",
                           "status": "Em Andamento", "municipio": "Teresina"})
        self.sheet = PlanilhaFalsa(self.cols)
        self.livro = PlanilhaLivro({"eventos": self.sheet,
                                  "vinculos_farol_producao": PlanilhaFalsa(replica.FONTES["farol_acomp_vinculos"][2]),
                                  "movimentacoes": PlanilhaFalsa(replica.FONTES["farol_acomp_movimentacoes"][2])})
        self.banco = BancoFalso({t: [] for t in replica.FONTES}, [
            {"id": 1, "tabela": self.table, "chave": "125", "operacao": "INSERT"}
        ])
        self.banco.dados[self.table] = [self.ficha]

    def _rodar(self, full=False):
        with patch.object(replica, "Supabase", return_value=self.banco), \
             patch.object(replica, "cliente_google", return_value=(None, self.livro)):
            replica.executar(full=full)

    def test_importa_novo_e_repetir_nao_duplica(self):
        self._rodar()
        self.assertEqual(len(self.sheet.linhas), 1)
        self.assertEqual(self.sheet.linhas[0][0], "125")
        self.assertEqual(self.banco.ack, [1])
        self._rodar(full=True)
        self.assertEqual(len(self.sheet.linhas), 1)
        self.assertEqual(len(self.sheet.append_calls), 1)

    def test_atualiza_mesma_linha(self):
        self._rodar()
        self.ficha["status"] = "Concluído"
        self.banco.eventos.append({"id": 2, "tabela": self.table,
                                    "chave": "125", "operacao": "UPDATE"})
        self._rodar()
        self.assertEqual(self.sheet.linhas[0][3], "Concluído")
        self.assertEqual(len(self.sheet.linhas), 1)
        self.assertEqual(self.banco.ack, [1, 2])

    def test_planilha_corrompida_bloqueia_ack(self):
        self.sheet.linhas = [["125"] + [""] * (len(self.cols)-1),
                             ["125"] + [""] * (len(self.cols)-1)]
        with self.assertRaises(RuntimeError):
            self._rodar()
        self.assertEqual(self.banco.ack, [])
        self.assertFalse(self.banco.status[-1][0])

    def test_exclusao_nunca_apaga_ficha_antiga(self):
        self.sheet.linhas = [["125"] + [""] * (len(self.cols)-1)]
        self.banco.dados[self.table] = []
        self.banco.eventos = [{"id": 3, "tabela": self.table,
                               "chave": "125", "operacao": "DELETE"}]
        excluidos = PlanilhaFalsa(("EVENTO_FILA", "TABELA", "CHAVE", "REGISTRADO_EM"))
        self.livro.tabelas["replica_exclusoes"] = excluidos
        self._rodar()
        self.assertEqual(len(self.sheet.linhas), 1)
        self.assertEqual(excluidos.linhas[0][2], "125")
        self.assertEqual(self.banco.ack, [3])

    def test_cabecalho_divergente_impede_escrita(self):
        self.sheet.cabecalho[1] = "TITULO_ERRADO"
        with self.assertRaises(RuntimeError):
            self._rodar()
        self.assertEqual(self.banco.ack, [])


if __name__ == "__main__":
    unittest.main()
