-- Módulo 9 — Fechamentos de rede: dados exigidos no encerramento.
-- Aplicar após sql/prisma_fechamentos_rede.sql.
-- Sem tabela de histórico de alterações, sem atualizar fichas existentes.
ALTER TABLE public.prisma_fechamentos_rede
  ADD COLUMN IF NOT EXISTS os_abertura text,
  ADD COLUMN IF NOT EXISTS data_abertura date;

ALTER TABLE public.prisma_fechamentos_rede
  DROP CONSTRAINT IF EXISTS prisma_fechamentos_rede_status_check;

ALTER TABLE public.prisma_fechamentos_rede
  ADD CONSTRAINT prisma_fechamentos_rede_status_check
  CHECK (status IN ('pendente', 'em_andamento', 'encerrado', 'concluido', 'cancelado'));

-- A opção Concluído continua aceita apenas para eventuais registros legados;
-- na interface o usuário passa a selecionar Encerrado.
ALTER TABLE public.prisma_fechamentos_rede
  ADD CONSTRAINT prisma_fechamentos_rede_encerramento_check
  CHECK (
    status NOT IN ('encerrado', 'concluido')
    OR (
      NULLIF(BTRIM(os_abertura), '') IS NOT NULL
      AND LENGTH(BTRIM(os_abertura)) <= 80
      AND data_abertura IS NOT NULL
    )
  );