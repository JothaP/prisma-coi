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
         "mensagem_publica", "status_historico", "texto_inicio",
         "descricao_abrangencia", "avisos_publicos", "assinatura_avisos"}
selected = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names]
assert {x.name for x in selected} == names
now = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
data = []
amb = {
    "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
    "FUSO": ZoneInfo("America/Fortaleza"),
    "AVISO_ANTECEDENCIA": timedelta(minutes=30),
    "MODULOS": {"3": "Farol Operacional", "8": "Consulta Operacional"},
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

    def test_aviso_desde_o_agendamento_independente_da_janela_30min(self):
        data.extend([
            registro(10, "global", None, now+timedelta(days=3),
                     now+timedelta(days=3,hours=2,minutes=30)),
            registro(11, "modulo", "3", now+timedelta(days=4),
                     now+timedelta(days=4,hours=1)),
        ])
        anteriores = amb["avisos_publicos"](agora=now)
        self.assertEqual({r["id"] for _,r in anteriores}, {10,11})
        self.assertTrue(all(status=="agendada" for status,_ in anteriores))
        self.assertEqual(
            amb["avisos_publicos"](agora=now+timedelta(days=3,minutes=-20))[0][0],
            "iminente"
        )
        self.assertEqual(
            amb["avisos_publicos"](agora=now+timedelta(days=3,minutes=20))[0][0],
            "em_andamento"
        )
        self.assertEqual(amb["descricao_abrangencia"](data[1]),"Farol Operacional")
        self.assertIn("2026", amb["texto_inicio"](data[0]))

    def test_assinatura_detecta_novo_agendamento_e_cancelamento(self):
        primeira = amb["assinatura_avisos"](agora=now)
        data.append(registro(9,"modulo","8",now+timedelta(days=4),
                             now+timedelta(days=4,hours=1)))
        segunda = amb["assinatura_avisos"](agora=now)
        self.assertNotEqual(primeira, segunda)
        data.clear()
        self.assertEqual(amb["assinatura_avisos"](agora=now), primeira)

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
        self.assertIn('format="DD/MM/YYYY"', page)
        self.assertIn('key="prisma_man_data_inicio"', page)
        self.assertIn('key="prisma_man_hora_inicio"', page)
        self.assertIn('key="prisma_man_data_fim"', page)
        self.assertIn('key="prisma_man_hora_fim"', page)
        self.assertIn("assinatura_previa", page)
        self.assertIn("confirmado_banco", page)
        self.assertIn("renderizar_avisos_publicos()", hub)
        self.assertIn("monitorar_avisos_login()", hub)
        self.assertIn("prisma_auditoria", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("service_role", sql)


if __name__ == "__main__":
    unittest.main()
