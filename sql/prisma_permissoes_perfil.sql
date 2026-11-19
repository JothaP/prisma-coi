-- Permissões padrão editáveis por tipo de usuário (PRISMA-COI).
-- Os administradores e a Gestão de Acessos não podem ser configurados aqui.
-- Não migra ou altera permissões individuais existentes.
CREATE TABLE IF NOT EXISTS public.prisma_permissoes_perfil (
    perfil text NOT NULL CHECK (perfil IN ('usuario', 'operador')),
    modulo text NOT NULL CHECK (
        modulo IN ('1','2','3','4','5','6','7','8','9','historico','teste')
    ),
    acao text NOT NULL CHECK (
        acao IN ('visualizar','criar','importar','editar','excluir','exportar','relatorios')
    ),
    permitido boolean NOT NULL,
    CONSTRAINT prisma_permissoes_perfil_pkey PRIMARY KEY (perfil, modulo, acao)
);

ALTER TABLE public.prisma_permissoes_perfil ENABLE ROW LEVEL SECURITY;
-- Sem acesso direto dos usuários finais ao catálogo de permissões.
-- O backend autenticado do PRISMA usa a chave de serviço apenas no servidor.
REVOKE ALL ON TABLE public.prisma_permissoes_perfil FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.prisma_permissoes_perfil TO service_role;
