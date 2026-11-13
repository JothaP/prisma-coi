"""Validação isolada da manutenção programada do PRISMA (sem rede)."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import unittest

ROOT = Path(__file__).resolve().parents[1]
FONTE = ROOT / "prisma_manutencao.py"
tree = ast.parse(FONTE.read_text(encoding="utf-8"))
names = {"_horario", "_visiveis_para_modulo", "situacao", "texto_fim",
         "mensagem_publica", "status_historico"}
selected = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names]
assert {x.name for x in selected} == names
now = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
data = []
amb = {
    "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
    "FUSO": ZoneInfo("America/Fortaleza"),
    "AVISO_ANTECEDENCIA": timedelta(minutes=30),
    "consultar_manutencoes": lambda: data,
}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(FONTE), "exec"), amb)


def registro(id_, escopo, modulo, inicio, fim, cancelado_em=None):
    return {
        "id": id_, "escopo": escopo, "modulo": modulo,
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat() if fim else None,
        "cancelado_em": cancelado_em,
        "mensagem": "",
    }


class TesteManutencao(unittest.TestCase):
    def tearDown(self):
        data.clear()

    def test_sem_janelas_nada_bloqueia(self):
        self.assertEqual(amb["situacao"]("3", agora=now), (None, None))

    def test_global_bloqueia_todos_e_prioriza(self):
        data.extend([
            registro(1, "modulo", "3", now-timedelta(minutes=3), now+timedelta(minutes=40)),
            registro(2, "global", None, now-timedelta(minutes=1), now+timedelta(minutes=20)),
        ])
        self.assertEqual(amb["situacao"]("3", agora=now)[0]["id"], 2)
        self.assertEqual(amb["situacao"]("9", agora=now)[0]["id"], 2)
        self.assertEqual(amb["situacao"](None, agora=now)[0]["id"], 2)

    def test_modulo_nao_bloqueia_outros(self):
        data.append(registro(5, "modulo", "3", now-timedelta(minutes=1), now+timedelta(minutes=1)))
        self.assertEqual(amb["situacao"]("3", agora=now)[0]["id"], 5)
        self.assertIsNone(amb["situacao"]("8", agora=now)[0])
        self.assertIsNone(amb["situacao"](None, agora=now)[0])

    def test_aviso_30_minutos_e_expiracao_sem_cron(self):
        data.append(registro(6, "modulo", "8", now+timedelta(minutes=20),
                             now+timedelta(minutes=50)))
        self.assertEqual(amb["situacao"]("8", agora=now)[1]["id"], 6)
        self.assertIsNone(amb["situacao"]("8", agora=now)[0])
        self.assertEqual(amb["situacao"]("8", agora=now+timedelta(minutes=25))[0]["id"], 6)
        self.assertIsNone(amb["situacao"]("8", agora=now+timedelta(minutes=51))[0])

    def test_ate_encerramento_manual(self):
        data.append(registro(7, "global", None, now-timedelta(minutes=2), None))
        self.assertEqual(amb["situacao"]("2", agora=now+timedelta(days=10))[0]["id"], 7)
        self.assertIn("sem horário", amb["texto_fim"](data[0]))

    def test_historico_e_mensagens(self):
        x = registro(9, "modulo", "3", now-timedelta(hours=3), now-timedelta(hours=1))
        self.assertEqual(amb["status_historico"](x, agora=now), "Concluída")
        x["cancelado_em"] = now.isoformat()
        self.assertEqual(amb["status_historico"](x, agora=now),
                         "Encerrada antecipadamente / cancelada")
        self.assertIn("temporariamente", amb["mensagem_publica"](x))

    def test_integracao_e_paginas(self):
        auth = (ROOT / "auth.py").read_text(encoding="utf-8")
        hub = (ROOT / "app.py").read_text(encoding="utf-8")
        page = (ROOT / "pages" / "11_Controle_de_Manutencao.py").read_text(encoding="utf-8")
        sql = (ROOT / "sql" / "prisma_manutencoes.sql").read_text(encoding="utf-8")
        self.assertIn("verificar_manutencao(modulo)", auth)
        self.assertIn("monitorar_manutencao(modulo)", auth)
        self.assertIn("verificar_manutencao(None)", hub)
        self.assertIn("Controle de Manutenção", hub)
        self.assertNotIn("99_Migracao_Acompanhamento.py", hub)
        self.assertFalse((ROOT / "pages" / "99_Migracao_Acompanhamento.py").exists())
        self.assertIn("st.form", page)
        self.assertIn("prisma_auditoria", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("service_role", sql)


if __name__ == "__main__":
    unittest.main()
