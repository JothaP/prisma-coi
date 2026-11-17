-- PRISMA | Modulo 9 | Fechamentos e reparos de rede.
-- Aplicado no Supabase em 17/11/2026.
-- Base independente de public.prisma_ativos; não há migração nem exclusão de poços.
CREATE TABLE IF NOT EXISTS public.prisma_fechamentos_rede (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    municipio text NOT NULL CHECK (length(btrim(municipio)) BETWEEN 1 AND 100),
    lider text,
    equipe text,
    matricula text,
    os text,
    bairro text NOT NULL CHECK (length(btrim(bairro)) BETWEEN 1 AND 160),
    data date NOT NULL,
    status text NOT NULL CHECK (
        status IN ('pendente','em_andamento','concluido','cancelado')
    ),
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    criado_por uuid,
    atualizado_por uuid,
    criado_em timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now()
);

-- Exclusivo do servidor Streamlit (chave service_role, nunca navegador).
ALTER TABLE public.prisma_fechamentos_rede ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.prisma_fechamentos_rede FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.prisma_fechamentos_rede TO service_role;

CREATE INDEX IF NOT EXISTS prisma_fechamentos_rede_municipio_status_idx
    ON public.prisma_fechamentos_rede (municipio, status, data DESC);
CREATE INDEX IF NOT EXISTS prisma_fechamentos_rede_bairro_idx
    ON public.prisma_fechamentos_rede (bairro);

-- Reutiliza o gatilho leve de atualizado_em do cadastro existente.
-- idempotente para ambientes de homologação onde o gatilho já exista.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid='public.prisma_fechamentos_rede'::regclass
          AND tgname='prisma_fechamentos_rede_atualizacao_trg'
    ) THEN
        CREATE TRIGGER prisma_fechamentos_rede_atualizacao_trg
            BEFORE UPDATE ON public.prisma_fechamentos_rede
            FOR EACH ROW
            EXECUTE FUNCTION public.prisma_ativos_atualizar_data();
    END IF;
END $$;
