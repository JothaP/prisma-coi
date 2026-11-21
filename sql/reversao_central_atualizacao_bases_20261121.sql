-- Rollback administrativo da Central de Atualização de Bases, sem alterar
-- cadastros, fichas de acompanhamento ou histórico de auditoria do PRISMA.
-- A tabela de backups do módulo só é descartada se estiver completamente vazia.
DROP FUNCTION IF EXISTS public.prisma_atualizacoes_substituir_pocos_tx(uuid, jsonb);
DROP FUNCTION IF EXISTS public.prisma_atualizacoes_restaurar_pocos_tx(uuid, bigint);

DO $$
DECLARE
    quantidade bigint;
BEGIN
    IF to_regclass('public.prisma_atualizacoes_backups') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM public.prisma_atualizacoes_backups'
            INTO quantidade;
        IF quantidade = 0 THEN
            DROP TABLE public.prisma_atualizacoes_backups;
        ELSE
            -- Preservação obrigatória de snapshots caso tenham surgido durante
            -- a remoção da interface; não permitir operação de escrita.
            REVOKE INSERT, UPDATE, DELETE, TRUNCATE
                ON TABLE public.prisma_atualizacoes_backups FROM service_role;
            RAISE NOTICE 'Snapshots da central preservados: %', quantidade;
        END IF;
    END IF;
END
$$;

NOTIFY pgrst, 'reload schema';
