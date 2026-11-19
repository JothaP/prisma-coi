# Farol Histórico — especificação inicial

Módulo independente do Farol Operacional (M3) para análise retrospectiva de um período livre.

## Uso

1. Abrir o cartão **Farol Histórico** no Hub, com permissão \`historico\`.
2. Importar um ou vários arquivos de O.S. (CSV/XLS/XLSX), e opcionalmente eventos.
3. Escolher datas de início e fim; não existe retenção de 8 dias no estudo.
4. Examinar série temporal, pontos individuais, concentrações acumuladas, eventos, ativos e detalhes de cada concentração.
5. Exportar as bases filtradas e os relatórios oferecidos pelo motor do M3.

## Isolamento

- Os dados importados ficam somente no \`st.session_state\` do navegador; não há novos bancos, tabelas ou uploads ao Supabase/Sheets.
- Não lê PONTOS/EVENTOS nem fichas e vínculos persistentes do M3. Somente os cadastros locais versionados de poços, loggers e polígonos são reutilizados.
- Nenhuma limpeza automática, atualização, edição, vínculo, ficha de acompanhamento nem operação de exclusão do M3 é ativada.
- A função de autorização de escrita do clone falha fechada e os métodos de acesso a planilhas são bloqueados.
- O calendário filtra as O.S. pela data de emissão, inclusive as datas-limite. Eventos com início/término compatíveis (com tolerância operacional) entram no estudo.
- A formação das concentrações do mapa é **acumulada espacialmente no período**. Concentrações recorrentes NÃO equivalem a eventos simultâneos; o gráfico temporal contextualiza os dias.
- O resultado é reproduzível apenas se as mesmas bases de entrada forem importadas novamente.

## Acesso e manutenção

- Chave de permissão: \`historico\`, exibida na Gestão de Acessos.
- Por segurança, novo módulo começa restrito aos não administradores até concessão de permissão.
- A manutenção global ou específica pode ser agendada no Controle de Manutenção.
- Executar o SQL versionado em \`sql/prisma_farol_historico_permissoes.sql\` no projeto Supabase antes da liberação ao público.

## Validação sugerida

- Importar uma semana e depois vários meses (sem limitar a 8 dias).
- Testar O.S. sem coordenadas, concentração vazia e arquivos com O.S. repetidas.
- Verificar que o recorte inclui ambas as datas e que um evento de fora do período é excluído.
- Verificar que o Módulo 3 e as tabelas operacionais não sofreram gravações.
