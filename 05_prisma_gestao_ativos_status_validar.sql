-- PRISMA / Módulo 9 — inclusão do status 'validar'
-- Executar UMA VEZ no SQL Editor do Supabase do PRISMA.
-- Não altera os registros existentes e não interfere no Farol.
-- Preserva os estados: operando, parado, em_manutencao e inativo.

BEGIN;

DO $$
DECLARE
    coluna_status SMALLINT;
    restricao RECORD;
BEGIN
    IF to_regclass('public.prisma_ativos') IS NULL THEN
        RAISE EXCEPTION 'Tabela public.prisma_ativos não encontrada.';
    END IF;

    SELECT attnum INTO coluna_status
    FROM pg_attribute
    WHERE attrelid = 'public.prisma_ativos'::regclass
      AND attname = 'status' AND NOT attisdropped;

    IF coluna_status IS NULL THEN
        RAISE EXCEPTION 'Coluna status ausente em public.prisma_ativos.';
    END IF;

    -- Remove verificações anteriores que enumeravam os status antigos,
    -- inclusive se tiverem nomes personalizados.
    FOR restricao IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.prisma_ativos'::regclass
          AND contype = 'c'
          AND coluna_status = ANY(conkey)
          AND pg_get_constraintdef(oid) ILIKE '%operando%'
          AND pg_get_constraintdef(oid) ILIKE '%parado%'
    LOOP
        EXECUTE format(
            'ALTER TABLE public.prisma_ativos DROP CONSTRAINT %I',
            restricao.conname
        );
    END LOOP;

    -- Segurança para execuções repetidas (nome padrão da nova regra).
    ALTER TABLE public.prisma_ativos
        DROP CONSTRAINT IF EXISTS prisma_ativos_status_check;

    ALTER TABLE public.prisma_ativos
        ADD CONSTRAINT prisma_ativos_status_check
        CHECK (
            status IS NULL
            OR status IN (
                'operando', 'parado', 'em_manutencao', 'inativo', 'validar'
            )
        );
END $$;

COMMIT;

-- Validação opcional:
-- SELECT status, count(*) FROM public.prisma_ativos
-- GROUP BY status ORDER BY status;
