# Central de Atualização de Bases — PRISMA

**Entrada:** Hub Central → Administração → Atualização de Bases — GitHub e Supabase.
**Acesso:** somente perfis administrativos ativos. O servidor confirma novamente
a sessão, o perfil no Supabase e a autorização antes de cada gravação.

## 1. GitHub: arquivos do repositório

- Consulta a árvore completa do repositório configurado na branch `main`.
- Exibe arquivos de código e configuração em **somente leitura**; substituição
  permitida exclusivamente para arquivos de dados existentes sob `dados/`
  ou `geodados/` e extensões: CSV, XLSX, XLS, GPKG, KMZ, KML, TIF/TIFF,
  GeoJSON ou ZIP.
- O novo arquivo deve ter a mesma extensão que o selecionado.
- Verifica estrutura do arquivo, e para `loggers.csv` e `pocos.csv`, os
  cabeçalhos, identificadores e coordenadas dos registros importados.
- A comparação de CSV de loggers/poços agrupa **todas as ocorrências** por
  `IDENTIFICACAO_ATIVO` na base antiga; duplicidades e identificações vazias
  preexistentes são mostradas no painel, **sem bloquear** a comparação.
  Grupos repetidos não são reduzidos silenciosamente a uma linha.
- A nova base CSV continua exigindo identificações preenchidas e únicas;
  se houver duplicidade no novo upload, o erro indica identificação e
  números dos registros. As métricas de adicionados, removidos e modificados
  contam **identificações distintas**; quando a quantidade/conteúdo das linhas
  associadas a uma identificação muda, ela é considerada modificada.
- O sistema não apaga registros do cadastro antigo durante a análise.
  **Publicar uma nova versão é uma substituição integral do arquivo**:
  registros ausentes da nova base não estarão presentes após a publicação.
- Ao confirmar a substituição, confere o SHA da versão atual para impedir
  sobrescrever edição concorrente e publica um commit na branch configurada.
- **Recuperação:** o histórico de commits do GitHub preserva a versão anterior.
  A aplicação passa a ler o novo arquivo após disponibilização/deploy da branch.
- **Tamanho máximo via painel:** 35 MB por arquivo.

### Configuração necessária no Streamlit Secrets

A credencial para atualizar arquivos diretamente do servidor Streamlit não
é a mesma conexão GitHub usada pelo assistente. É necessário configurar:

```toml
PRISMA_GITHUB_REPO = "JothaP/prisma-coi"
PRISMA_GITHUB_BRANCH = "main"
PRISMA_GITHUB_TOKEN = "COLE_AQUI_UM_TOKEN_REAL_COM_PERMISSAO_CONTENTS_WRITE"
```

Crie um token **fine-grained** restrito a esse repositório com **Contents:
Read and write**. Configure-o apenas no gerenciador de segredos do serviço
de hospedagem Streamlit, nunca em código, arquivo do repositório ou
mensagens. Sem a chave, o painel informa que a gravação está indisponível;
em repositório privado, a leitura também depende dela.

## 2. Supabase — Tabelas

- Disponibiliza consulta e CSV de tabelas operacionais previamente autorizadas
  (ativos, fechamentos, manutenções, fichas e relações).
- **Substituição integral da área temporária de poços**
  `public.prisma_ativos_importacao`: importar CSV/XLSX com os seis campos
  `ID_POCO`, `IDENTIFICACAO_ATIVO`, `NOME_POCO`, `MUNICIPIO`,
  `LATITUDE`, `LONGITUDE`. A confirmação deve ser explícita.
- A operação invoca RPC transacional, cria snapshot da base anterior em
  `prisma_atualizacoes_backups`, realiza a troca e registra auditoria.
- Snapshots podem ser restaurados na própria interface, com confirmação.
  A restauração cria também cópia do estado que estiver ativo.
- **ATENÇÃO:** essa área de importação NÃO é o cadastro operacional
  `prisma_ativos`. O procedimento não sincroniza automaticamente entre
  CSV GitHub, `prisma_ativos_importacao` e `prisma_ativos`. Este último
  possui vínculos e campos de operação que não devem ser apagados por upload
  genérico. A edição específica dos ativos permanece no módulo Gestão de Campo.
- Limite de 10.000 registros por operação; tabelas críticas como
  `prisma_perfis`, `prisma_permissoes` e `prisma_auditoria` não são editáveis.
- A RPC e a tabela de backups não podem ser usadas diretamente por
  `anon` ou `authenticated`. A chave privilegiada fica no servidor,
  e o banco valida o perfil administrativo ativo mais uma vez.

## 3. Supabase — Storage

- Lista buckets e objetos existentes no projeto, inclusive subpastas.
- Substitui apenas arquivos existentes de tipos operacionais permitidos.
- Guarda a versão anterior em
  `_backup_atualizacoes/<timestamp>_<uuid>/<caminho-original>` no mesmo
  bucket antes de gravar a nova versão.
- Permite localizar e restaurar backups do arquivo selecionado, criando
  nova cópia da versão que será substituída.
- Máximo de 35 MB para o arquivo novo e para a cópia anterior. Arquivos
  acima desse limite exigem processo de transferência dedicado.
- O Supabase Storage não fornece versionamento S3 nativo; por isso os
  arquivos de backup são cópias independentes.
- Pode existir atraso de propagação de cache/CDN após uma substituição.

## 4. Auditoria e limitações

Cada atualização GitHub/Storage gera evento em `prisma_auditoria` após
a gravação; a troca/restauração da tabela usa auditoria dentro da RPC.
A operação não exclui backups antigos automaticamente.

Uma troca de arquivo via GitHub não altera tabelas Supabase e vice-versa.
A central não executa código nem SQL arbitrário e não permite alterações
de arquivos `*.py`, `*.sql`, segredos ou configurações.

**Implantação:** a migração SQL apenas cria funções e armazenamento de
snapshots — não importa nem substitui bases automaticamente. A aplicação
depende das credenciais já existentes do Supabase e do token GitHub adicional.

**Primeira validação recomendada:** usar um arquivo CSV operacional em
ambiente controlado, verificar o commit, a publicação e a leitura no módulo
consumidor. Para o Supabase, conferir uma exportação e a listagem de backups
antes de utilizar a função de substituição integral.
