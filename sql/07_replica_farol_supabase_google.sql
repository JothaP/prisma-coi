-- PRISMA / Farol — replica assincrona Supabase -> Google Sheets (versao 1).
-- EXECUTAR UMA VEZ no SQL Editor do projeto do PRISMA.
-- Nao modifica fichas, vinculos ou movimentacoes ja existentes.
-- Nova fila e status: apenas service_role. Gatilhos sao transacionais.
BEGIN;

CREATE TABLE IF NOT EXISTS public.farol_acomp_replica_fila (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tabela text NOT NULL CHECK (tabela IN (
    'farol_acomp_fichas', 'farol_acomp_vinculos', 'farol_acomp_movimentacoes'
  )),
  chave text NOT NULL,
  operacao text NOT NULL CHECK (operacao IN ('INSERT','UPDATE','DELETE')),
  criado_em timestamptz NOT NULL DEFAULT now(),
  processado_em timestamptz,
  CONSTRAINT replica_fila_chave_nao_vazia CHECK (length(chave) > 0)
);
CREATE INDEX IF NOT EXISTS farol_acomp_replica_fila_pendente_idx
  ON public.farol_acomp_replica_fila(id)
  WHERE processado_em IS NULL;

CREATE TABLE IF NOT EXISTS public.farol_acomp_replica_status (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  ultima_execucao timestamptz,
  ultimo_sucesso timestamptz,
  situacao text NOT NULL DEFAULT 'NAO_CONFIGURADO'
    CHECK (situacao IN ('NAO_CONFIGURADO','SUCESSO','FALHA')),
  pendentes integer NOT NULL DEFAULT 0 CHECK (pendentes >= 0),
  enviados integer NOT NULL DEFAULT 0 CHECK (enviados >= 0),
  atualizados integer NOT NULL DEFAULT 0 CHECK (atualizados >= 0),
  ultimo_erro text NOT NULL DEFAULT '',
  atualizado_em timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.farol_acomp_replica_status(id)
VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.farol_acomp_enfileirar_replica()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $func$
DECLARE
  pk text;
BEGIN
  IF TG_TABLE_NAME NOT IN ('farol_acomp_fichas','farol_acomp_vinculos',
                           'farol_acomp_movimentacoes') THEN
    RAISE EXCEPTION 'Tabela nao autorizada para replica';
  END IF;
  IF TG_OP = 'DELETE' THEN
    pk := CASE WHEN TG_TABLE_NAME = 'farol_acomp_vinculos'
         THEN to_jsonb(OLD)->>'chave' ELSE to_jsonb(OLD)->>'id' END;
  ELSE
    pk := CASE WHEN TG_TABLE_NAME = 'farol_acomp_vinculos'
         THEN to_jsonb(NEW)->>'chave' ELSE to_jsonb(NEW)->>'id' END;
  END IF;
  IF pk IS NULL OR pk = '' THEN
    RAISE EXCEPTION 'Registro sem chave para replica';
  END IF;
  INSERT INTO public.farol_acomp_replica_fila(tabela,chave,operacao)
  VALUES (TG_TABLE_NAME,pk,TG_OP);
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$func$;

DROP TRIGGER IF EXISTS farol_replica_ficha ON public.farol_acomp_fichas;
CREATE TRIGGER farol_replica_ficha
  AFTER INSERT OR UPDATE OR DELETE ON public.farol_acomp_fichas
  FOR EACH ROW EXECUTE FUNCTION public.farol_acomp_enfileirar_replica();

DROP TRIGGER IF EXISTS farol_replica_vinculo ON public.farol_acomp_vinculos;
CREATE TRIGGER farol_replica_vinculo
  AFTER INSERT OR UPDATE OR DELETE ON public.farol_acomp_vinculos
  FOR EACH ROW EXECUTE FUNCTION public.farol_acomp_enfileirar_replica();

DROP TRIGGER IF EXISTS farol_replica_movimentacao ON public.farol_acomp_movimentacoes;
CREATE TRIGGER farol_replica_movimentacao
  AFTER INSERT OR UPDATE OR DELETE ON public.farol_acomp_movimentacoes
  FOR EACH ROW EXECUTE FUNCTION public.farol_acomp_enfileirar_replica();

ALTER TABLE public.farol_acomp_replica_fila ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.farol_acomp_replica_status ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.farol_acomp_replica_fila FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.farol_acomp_replica_status FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.farol_acomp_replica_fila_id_seq FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.farol_acomp_enfileirar_replica()
  FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.farol_acomp_replica_fila, public.farol_acomp_replica_status TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.farol_acomp_replica_fila_id_seq TO service_role;
COMMIT;

-- Verifique as estruturas sem ler conteudo operacional:
SELECT
  (SELECT count(*) FROM public.farol_acomp_replica_fila WHERE processado_em IS NULL)
    AS eventos_pendentes,
  (SELECT situacao FROM public.farol_acomp_replica_status WHERE id=1)
    AS situacao_replicacao;
