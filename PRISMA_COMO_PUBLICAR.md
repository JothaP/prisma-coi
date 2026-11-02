# PRISMA — preparação do clone

1. Crie um projeto Supabase isolado para o PRISMA: https://supabase.com/dashboard.
2. Em SQL Editor, execute o conteúdo de `prisma_setup.sql`.
3. Em Authentication → Providers → Email, mantenha confirmação de e-mail habilitada.
4. Em Authentication → Email Templates, personalize **Confirm signup** para enviar o código `{{ .Token }}`. Personalize também **Reset Password** para usar o mesmo código. O padrão de e-mails com links não funciona com os campos de código desta interface.
5. Configure SMTP próprio em Supabase Authentication para entrega corporativa de e-mails. Confira limites e autenticação do remetente.
6. No Streamlit Community Cloud, abra o aplicativo PRISMA → Settings → Secrets. Copie os campos do `secrets.example.toml` (com valores reais obtidos diretamente dos painéis, jamais pelo chat) e as configurações já usadas pelos módulos existentes.
7. O e-mail em `PRISMA_ADMIN_EMAIL` será autorizado como primeiro administrador APÓS confirmar o código, desde que não exista outro administrador ativo. Nunca configure esse campo com e-mail de terceiros.
8. Importe os arquivos atualizados para o repositório GitHub `prisma-coi` e publique `app.py` na branch `main`.
9. No primeiro acesso: Criar conta → Confirmar e-mail → Entrar. O administrador inicial é ativado automaticamente após confirmar sua conta.
10. Solicitações seguintes ficam pendentes até aprovação em "Gestão de Acessos".

## Atenção

- A autenticação do PRISMA está isolada do SAI original. O app não usa mais `SENHA_ADMIN`, `SENHA_USUARIO` ou `SENHA_OPERADOR`.
- Esta versão não implementa cookies persistentes de sessão: ao recarregar completamente a aba, pode ser preciso entrar novamente.
- O clone pode utilizar as MESMAS planilhas de produção se seus Secrets forem copiados. Não execute testes destrutivos com dados reais.
- As permissões de **abertura dos módulos** são verificadas diretamente em cada página. As ações de gravação do Módulo 1 estão protegidas por permissões individuais; outros módulos ainda contêm restrições legadas por perfil. Antes de liberar colaboradores, audite a autorização de cada ação sensível (importar, editar, excluir, aprovar e gerar relatórios) nos módulos restantes.
- `PRISMA_SUPABASE_SERVICE_ROLE_KEY` possui alto privilégio e jamais deve ser enviado ao navegador, a arquivos públicos, ao repositório ou logs.
- O serviço Supabase deve estar operacional para usar o PRISMA. Sem configuração, o app falha de forma fechada, sem permitir login por senha compartilhada.

## Automação

O workflow anterior do GitHub Actions foi arquivado em `workflows_arquivados/` no CLONE, para evitar duas automações importando simultaneamente as mesmas bases de produção. O workflow original do SAI permanece ativo no repositório anterior. Não ative o job no PRISMA sem decidir explicitamente migrar a automação.
