-- Farol Histórico (módulo somente de estudo, sem persistência operacional)
-- Expande os módulos admitidos para permissões e janelas de manutenção.
-- Os dados existentes nas duas tabelas são preservados.
ALTER TABLE public.prisma_permissoes
    DROP CONSTRAINT IF EXISTS prisma_permissoes_modulo_check;
ALTER TABLE public.prisma_permissoes
    ADD CONSTRAINT prisma_permissoes_modulo_check
    CHECK (modulo = ANY (ARRAY[
        '1','2','3','4','5','6','7','8','9','teste','historico'
    ]));

ALTER TABLE public.prisma_manutencoes
    DROP CONSTRAINT IF EXISTS manutencao_escopo_modulo;
ALTER TABLE public.prisma_manutencoes
    ADD CONSTRAINT manutencao_escopo_modulo
    CHECK (
        (escopo = 'global' AND modulo IS NULL)
        OR (escopo = 'modulo' AND modulo = ANY (ARRAY[
            '1','2','3','4','5','6','7','8','9','teste','historico'
        ]))
    );
