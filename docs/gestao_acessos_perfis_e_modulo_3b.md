# PRISMA-COI — Módulo 3B e permissões por tipo de usuário

## Identificação do Farol Histórico

O cartão do Farol Histórico aparece no Hub como **MÓDULO 3B**. A numeração dos
módulos 1 a 9, e dos componentes administrativos, não foi alterada.
A chave interna de autorização continua sendo `historico`, para preservar
permissões preexistentes, regras de manutenção e compatibilidade do Supabase.
O arquivo permanece `pages/12_Farol_Historico.py`.

## Gestão de Acessos

A administração oferece duas abas:

- **Acessos individuais:** mantida a gestão das contas, status, e-mails,
  espelhamento e ajustes por módulo e ação.
- **Acessos por tipo de usuário:** ajusta a matriz padrão para `usuario`
  e `operador`. `admin` conserva acesso integral obrigatório.

Precedência (da mais forte para a mais fraca):

1. Exclusividade de `gestao` para administrador e leitura universal do módulo 9.
2. Para administradores, acesso integral, sem restrição individual.
3. Exceções registradas na tabela `prisma_permissoes` para uma pessoa.
4. Regras do seu perfil em `prisma_permissoes_perfil`.
5. Valores padrão legados do `prisma_backend.py`, enquanto um perfil ainda
   não tiver regras salvas.

As configurações de tipo são aplicadas aos usuários já existentes e aos
novos, na próxima validação da sessão (até cerca de 25 segundos), desde que
não existam regras individuais que prevaleçam. Uma ação somente é executável
quando **Visualizar** também está habilitado.

Um administrador pode usar **Restaurar permissões do perfil** na aba individual,
com confirmação explícita, para apagar as exceções daquele usuário e fazê-lo
herdar o padrão corrente do seu tipo. A ação não altera contas, outros usuários
ou a configuração global de perfis.

## Segurança e auditoria

A tabela `public.prisma_permissoes_perfil` foi criada com RLS ativo e sem
acesso direto para `anon` ou `authenticated`. O backend usa a chave de
serviço no servidor Streamlit. Alterações dos padrões e restaurações são
auditadas em `prisma_auditoria`, sem registrar consultas simples.

A mudança não altera as tabelas de operação hidráulica nem o Módulo 3.
