# Módulo 3 — limpeza administrativa opcional das fichas

A rotina preexistente de limpeza de PONTOS, EVENTOS, FAROL_AREAS_EDITADAS
e controle de atualizações permanece inalterada por padrão.

O botão administrativo **Limpar dados (opção de incluir fichas)** abre o
diálogo anterior, agora com uma opção **desmarcada por padrão** para também
excluir as fichas de acompanhamento.

Ao marcar, o administrador deve confirmar separadamente a perda de fichas,
movimentações e vínculos e digitar **EXCLUIR FICHAS**. Sem essas confirmações,
o botão permanece desabilitado.

## Fonte ativa

- Supabase: a RPC `farol_acomp_limpar_tudo_admin_tx` valida um administrador
  ativo no próprio banco e remove, na mesma transação, âncoras de eventos,
  registros de incorporações, vínculos, movimentações e fichas. O evento é
  auditado em `prisma_auditoria`. Pendências de replicação antigas são
  descartadas antes de enfileirar os tombstones das exclusões.
- Google Sheets (modo legado): valida todos os cabeçalhos antes de começar,
  limpa as abas `eventos`, `movimentacoes`, `vinculos_farol_producao`
  encontradas na planilha independente do acompanhamento e restaura seus
  cabeçalhos. As abas nunca são apagadas.

O primeiro estágio de exclusão das fichas ocorre antes da limpeza das abas
operacionais. Se a exclusão de fichas falhar, **PONTOS e EVENTOS não são
apagados por esta tentativa**. Se o primeiro estágio concluir e a limpeza
operacional falhar, o diálogo exibe a falha parcial para intervenção manual;
não existe transação distribuída entre Google e Supabase.

A limpeza elimina dados da fonte ativa, **não exclui cópias arquivadas,
réplicas históricas ou backups externos**. No modo Supabase, a réplica legada
do Google Sheets mantém cópias antigas, conforme sua política de retenção.
Não está previsto apagar histórico de auditoria.

## Controles de autorização

- Interface do botão/modal: somente perfil `admin`.
- Rotina Python (executada em servidor): sessão validada, perfil `admin`,
  permissão `3/excluir` e ID de administrador identificado.
- RPC SQL: verifica se `p_autor` corresponde a um administrador ativo.
  `SECURITY INVOKER` e `EXECUTE` exclusivo para `service_role`.

A criação da função SQL **não executa a exclusão**. A limpeza só acontece
quando o administrador a confirma no Módulo 3.
