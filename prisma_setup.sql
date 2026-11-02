-- PRISMA / Gestão de usuários — execute no SQL Editor do Supabase.
-- Este script cria tabelas NOVAS; não altera o Google Sheets nem a estrutura do SAI.
-- Não cole senhas, tokens ou chaves neste arquivo.

create table if not exists public.prisma_perfis (
 id uuid primary key references auth.users(id) on delete cascade,
 email text not null unique,
 nome text not null default '',
 perfil text not null default 'operador' check (perfil in ('admin','usuario','operador')),
 status text not null default 'pendente' check (status in ('pendente','ativo','bloqueado')),
 criado_em timestamptz not null default now(),
 atualizado_em timestamptz not null default now()
);
create table if not exists public.prisma_permissoes (
 usuario_id uuid not null references public.prisma_perfis(id) on delete cascade,
 modulo text not null check (modulo in ('1','2','3','4','5','6','7','8','teste')),
 acao text not null check (acao in ('visualizar','criar','importar','editar','excluir','exportar','relatorios')),
 permitido boolean not null default false,
 primary key(usuario_id,modulo,acao)
);
create table if not exists public.prisma_auditoria (
 id bigint generated always as identity primary key,
 criado_em timestamptz not null default now(),
 autor_id uuid references public.prisma_perfis(id) on delete set null,
 alvo_id uuid,
 acao text not null,
 detalhes text not null default ''
);
create or replace function public.prisma_cadastrar_usuario()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
 insert into public.prisma_perfis(id,email,nome,perfil,status)
 values (new.id, lower(new.email),left(coalesce(new.raw_user_meta_data->>'nome',''),160),'operador','pendente')
 on conflict(id) do nothing;
 return new;
end;
$$;
drop trigger if exists prisma_on_auth_user_created on auth.users;
create trigger prisma_on_auth_user_created
 after insert on auth.users for each row execute function public.prisma_cadastrar_usuario();

-- Padrão seguro: tabelas não podem ser alteradas nem lidas diretamente por
-- clientes anônimos. Apenas o serviço server-side com service_role opera nelas.
alter table public.prisma_perfis enable row level security;
alter table public.prisma_permissoes enable row level security;
alter table public.prisma_auditoria enable row level security;
revoke all on public.prisma_perfis from anon, authenticated;
revoke all on public.prisma_permissoes from anon, authenticated;
revoke all on public.prisma_auditoria from anon, authenticated;
revoke all on sequence public.prisma_auditoria_id_seq from anon, authenticated;

-- A função de gatilho só deve ser executada via trigger.
revoke all on function public.prisma_cadastrar_usuario() from public;
