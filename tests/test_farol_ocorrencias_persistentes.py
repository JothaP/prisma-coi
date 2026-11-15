"""Regressões de identidade persistente e aglutinação de eventos Farol M3.

Testes puros: sem acesso ao Supabase nem ao Streamlit.
"""
import unittest
from pathlib import Path
import pandas as pd

from farol_ocorrencias_persistentes import (
    chaves_eventos_confirmados, resolver_ocorrencias,
)

ROOT=Path(__file__).resolve().parents[1]


def contexto(chave, os, *, eventos=(),lat=-5.09,lon=-42.80,cidade="Teresina"):
    return {
        "chave":chave,"os":list(os),"municipio":cidade,"bairros":"CENTRO",
        "latitude":lat,"longitude":lon,"multiplos_municipios":False,
        "evento_chaves":tuple(eventos),
    }


def vinculo(chave,fic,os,*,lat=-5.09,lon=-42.80,cidade="Teresina"):
    return {
        "chave":chave,"evento_id":fic,"os_origem":";".join(os),
        "municipio":cidade,"latitude":str(lat),"longitude":str(lon),
        "criado_em":"10/11/2026 09:00",
    }


class TestesOcorrenciasPersistentes(unittest.TestCase):
    def setUp(self):
        self.fichas={
            "164":{"id":"164","status":"Em Andamento","finalizado":"Não"},
            "168":{"id":"168","status":"Em Andamento","finalizado":"Não"},
        }
        self.evento="e"*32
        self.historico=[
            vinculo("a"*32,"164",["101","102","103"]),
            vinculo("b"*32,"168",["201","202","203"],lat=-5.22,lon=-42.88),
        ]

    def resolver(self, contextos,vinculos=None,ancoras=None,fichas=None):
        return resolver_ocorrencias(
            contextos, self.historico if vinculos is None else vinculos,
            self.fichas if fichas is None else fichas,
            [] if ancoras is None else ancoras,
        )

    def test_os_entrando_e_saindo_conservam_ficha(self):
        ctx={"THE C002":contexto("c"*32,["101","102","104","105"])}
        dados,manuais,lote=self.resolver(ctx)
        self.assertEqual(dados["THE C002"]["evento_id"],"164")
        self.assertEqual(dados["THE C002"]["motivo"],"continuidade")
        self.assertEqual(lote[0]["chave_anterior"],"a"*32)
        self.assertEqual(lote[0]["os"],["101","102","104","105"])
        self.assertFalse(manuais)

    def test_evento_aglutina_concentracoes_distantes_na_mesma_ficha(self):
        ctx={
            "THE C001":contexto("a"*32,["101","102","103"],eventos=[self.evento]),
            "THE C002":contexto("b"*32,["201","202","203"],eventos=[self.evento],
                                 lat=-5.22,lon=-42.88),
        }
        dados,_,lote=self.resolver(ctx)
        self.assertEqual({r["evento_id"] for r in dados.values()},{"164"})
        self.assertTrue(all(r["agrupados"]==2 for r in dados.values()))
        self.assertEqual(len(lote),2)
        self.assertEqual({item["motivo"] for item in lote},{"evento"})
        self.assertEqual({item["evento_chave"] for item in lote},{self.evento})

    def test_ancora_permanente_associa_concentracao_nova_distante(self):
        ctx={"THE C003":contexto("c"*32,["999","998","997"],
                                eventos=[self.evento],lat=-5.42,lon=-42.40)}
        a=[{"evento_chave":self.evento,"ficha_id":"164","municipio":"TERESINA"}]
        dados,manuais,lote=self.resolver(ctx,ancoras=a)
        self.assertEqual(dados["THE C003"]["evento_id"],"164")
        self.assertEqual(lote[0]["ficha_id"],"164")
        self.assertFalse(manuais)

    def test_evento_confirmado_sem_ficha_nao_cria_automaticamente(self):
        ctx={"THE C008":contexto("d"*32,["601","602","603"],eventos=[self.evento])}
        dados,manuais,lote=self.resolver(ctx)
        self.assertFalse(dados)
        self.assertFalse(lote)
        self.assertFalse(manuais)

    def test_eventos_diferentes_nao_unem_fichas(self):
        ctx={
            "THE C001":contexto("a"*32,["101","102","103"],eventos=["d"*32]),
            "THE C002":contexto("b"*32,["201","202","203"],eventos=["e"*32],
                                 lat=-5.22,lon=-42.88),
        }
        dados,_,lote=self.resolver(ctx)
        self.assertEqual(dados["THE C001"]["evento_id"],"164")
        self.assertEqual(dados["THE C002"]["evento_id"],"168")
        self.assertEqual(len(lote),2)

    def test_eventos_sobrepostos_na_mesma_concentracao_impedem_uniao(self):
        ctx={"THE C001":contexto("a"*32,["101","102","103"],
             eventos=["e"*32,"d"*32])}
        dados,_,lote=self.resolver(ctx)
        self.assertEqual(dados["THE C001"]["motivo"],"exato")
        self.assertFalse(lote)

    def test_regioes_diferentes_nao_cruzam_sem_ficha(self):
        ctx={"API C001":contexto("c"*32,["101","102","105"],cidade="Picos")}
        dados,manuais,lote=self.resolver(ctx)
        self.assertFalse(dados)
        self.assertFalse(manuais)
        self.assertFalse(lote)

    def test_divisao_sem_evento_nao_atribui_mesma_ficha_a_duas(self):
        ctx={
            "THE C001":contexto("c"*32,["101","102","104"]),
            "THE C002":contexto("d"*32,["101","103","105"]),
        }
        dados,manuais,lote=self.resolver(ctx)
        self.assertFalse(dados)
        self.assertEqual(set(manuais),set(ctx))
        self.assertFalse(lote)

    def test_concentracao_com_duas_fichas_historicas_nao_escolhe_uma(self):
        v=[
            vinculo("a"*32,"164",["101","102","103"]),
            vinculo("b"*32,"168",["101","102","104"]),
        ]
        dados,manuais,lote=self.resolver(
            {"THE C002":contexto("c"*32,["101","102","105"])},
            vinculos=v
        )
        self.assertFalse(dados)
        self.assertEqual(len(manuais["THE C002"]),2)
        self.assertFalse(lote)

    def test_ancora_existente_sem_mudanca_nao_gera_nova_gravacao(self):
        ctx={"THE C001":contexto("a"*32,["101","102","103"],eventos=[self.evento])}
        ancora=[{"evento_chave":self.evento,"ficha_id":"164","municipio":"Teresina"}]
        dados,_,lote=self.resolver(ctx,ancoras=ancora)
        self.assertEqual(dados["THE C001"]["evento_id"],"164")
        self.assertFalse(lote)

    def test_vinculo_exato_entre_eventos_ancorados_nao_pode_ser_absorvido(self):
        ctx={"THE C001":contexto("a"*32,["101","102","103"],eventos=[self.evento])}
        a=[{"evento_chave":"f"*32,"ficha_id":"164","municipio":"Teresina"}]
        dados,_,lote=self.resolver(ctx,ancoras=a)
        self.assertEqual(dados["THE C001"]["motivo"],"exato")
        self.assertFalse(lote)

    def test_temporal_evento_usa_indices_os_e_nao_contagem_territorial(self):
        evento={
            "Status":"Em andamento", "Início":"13/11/2026 08:00",
            "Término Real":"", "Cidade":"TERESINA",
            "_indices_os_correspondentes":(5,),
            "Protocolo":"EVENTO X",
        }
        os=pd.DataFrame([
            {"Dt. Emissão":"13/11/2026 09:00"},
            {"Dt. Emissão":"13/11/2026 10:00"},
        ],index=[5,8])
        ev=pd.DataFrame([evento])
        f=lambda x:pd.to_datetime(x,dayfirst=True,errors="coerce")
        ch=chaves_eventos_confirmados(
            os,ev,lambda _e:self.evento,f,
            lambda x:("Em andamento","#f00"),
            pd.Timestamp("2026-11-13 12:00"),
        )
        self.assertEqual(ch,(self.evento,))
        os.loc[5,"Dt. Emissão"]="13/11/2026 05:00"
        ch=chaves_eventos_confirmados(
            os,ev,lambda _e:self.evento,f,
            lambda x:("Em andamento","#f00"),pd.Timestamp("2026-11-13 12:00"),
        )
        self.assertFalse(ch)

    def test_integracao_nao_grava_uma_por_expander(self):
        pagina=(ROOT/"pages"/"3_Farol_Operacional.py").read_text(encoding="utf-8")
        backend=(ROOT/"farol_acomp_supabase_backend.py").read_text(encoding="utf-8")
        sql=(ROOT/"sql"/"farol_ocorrencias_persistentes.sql").read_text(encoding="utf-8")
        self.assertIn("sincronizar_ocorrencias_lote(",pagina)
        self.assertIn("dados_eventos_por_codigo[str(codigo)]",pagina)
        self.assertNotIn("gravacoes_nesta_interacao",pagina)
        self.assertIn("/rest/v1/rpc/farol_acomp_sincronizar_ocorrencias_tx",backend)
        self.assertIn("UPDATE public.farol_acomp_vinculos",sql)
        self.assertIn("INSERT INTO public.farol_acomp_incorporacoes",sql)
        self.assertIn("INSERT INTO public.prisma_auditoria",sql)
        self.assertNotIn("DELETE FROM public.farol_acomp_",sql)


if __name__ == "__main__":
    unittest.main()
