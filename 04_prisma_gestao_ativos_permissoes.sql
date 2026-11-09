-- PRISMA — Gestão de Ativos (Módulo 9)
-- Execute no Supabase SQL Editor uma única vez.
-- Amplia somente os módulos aceitos pelo cadastro de permissões.
-- Nenhuma tabela do Farol é alterada.
begin;

alter table public.prisma_permissoes
    drop constraint if exists prisma_permissoes_modulo_check;

alter table public.prisma_permissoes
    add constraint prisma_permissoes_modulo_check
    check (modulo in ('1','2','3','4','5','6','7','8','9','teste'));

commit;

-- Administradores: acesso imediato ao Módulo 9.
-- Demais usuários: acesso somente após liberação em Gestão de Acessos.
