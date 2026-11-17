# Farol Operacional V3 — especificação do laboratório

## Princípios aprovados

1. O V3 é operacional **diário**: trabalha somente com as reclamações cuja **data de emissão é o dia corrente**, no fuso America/Fortaleza. Não consulta nem agrupa uma janela acumulada de oito dias.
2. A unidade de análise é cada **concentração independente**. O bairro, um evento ou a proximidade entre grupos não autorizam a mesclagem de duas concentrações ou de duas fichas.
3. Uma **ficha de acompanhamento** registra decisões e movimentações operacionais e deve permanecer acessível mesmo depois do dia de origem. Não precisa ser recalculada a partir das O.S. antigas.
4. **Copiar dados** é ato explícito do analista e serve para preencher o destino com relato, hipóteses e validações. **Não** copia status, conclusão, quantitativo, identidade ou movimentações, nem altera a origem.
5. Eventos e medições são **evidências indicativas**, não causas confirmadas; só são consultados quando o analista solicita investigação.
6. **Histórico Operacional** será um projeto separado, futuro, para carregar arquivos de períodos escolhidos sob demanda. Não precisa replicar todo o banco histórico de O.S.
7. Na fase de laboratório, o **Módulo 3 de produção e sua base de acompanhamentos permanecem intocados**.

## Protótipo implementado

O **Ambiente de Teste é dedicado exclusivamente ao Farol Operacional V3**. Não há menu para as ferramentas experimentais antigas; seus códigos legados não são importados pela página. O V3 abre as áreas:

- **Panorama do dia:** botão **Consultar reclamações de hoje no Módulo 3** para ler a aba PONTOS em modo somente leitura, selecionar somente a data corrente e formar os agrupamentos. Upload CSV/XLSX e exemplo fictício permanecem como alternativas de teste. Mapa sem desenho nem mescla, incluindo O.S. fora de concentrações, indicadores e lista de concentrações.
- **Investigação:** escolha de concentração, listagem de O.S., leitura sob demanda da aba EVENTOS do Módulo 3, com opção de substituir eventos por arquivo; consulta aos cadastros locais de poços e loggers já usados pelo M3; medição de pressão por arquivo opcional. Ativos e medições dentro de 750 metros do centro.
- **Acompanhamentos:** ficha própria por agrupamento, com status, relato, causa provável, causa confirmada, validações e movimentações. Cópia explícita entre fichas. Fichas em aberto da sessão podem ser consultadas sem dados diários recarregados.

Configurações iniciais: raio de **750 m**, mínimo de **3 O.S.**, agrupamento geográfico **dentro do mesmo município**. O código visual (ex.: THE C001) é apenas identificador para o dia; a chave interna inclui data, município e conjunto de O.S.

## Limites deliberados desta primeira etapa

- Os registros de O.S. e fichas do laboratório são mantidos apenas na **sessão do Streamlit**. Não há gravações no Supabase nem em planilhas de produção.
- O analista pode **exportar e restaurar** um JSON das fichas experimentais. Isso é um meio provisório de validar o acompanhamento e não substitui persistência operacional com auditoria e controle de concorrência.
- Quando a composição de O.S. de um agrupamento muda após nova importação no mesmo dia, a chave baseada no conjunto de O.S. muda. **Não** se religa fichas automaticamente. A estratégia definitiva de continuidade e identidade precisará ser testada antes de implantar o V3 em produção.
- O protótipo **lê as abas PONTOS e EVENTOS do Módulo 3** usando a mesma configuração `farol_producao_spreadsheet_id`, com credenciais do Google Sheets **somente leitura**. O acesso exige permissão ao Ambiente de Teste e, para usar essas bases, permissão ao Módulo 3. O PONTOS é recortado por data antes dos cruzamentos; EVENTOS é lido apenas ao investigar. O arquivo da base de oito dias ainda precisa ser transferido do Sheets; **isso não implica processar oito dias**, mas não elimina o custo da leitura remota.
- A conexão é acionada explicitamente pelo analista, sem importação automática nem gravação nas abas de produção.
- A relação de evento é uma sugestão por município, bairro e intervalo temporal; não substitui análise territorial operacional validada.
- O raio operacional de 750 m dos ativos e medição tem como referência inicial o **centro da concentração**. Mais tarde poderemos comparar com a distribuição espacial das O.S.
- Arquivos não são guardados permanentemente. O protótipo não elimina ou altera os dados existentes de oito dias do Módulo 3.

## Critérios de aceitação no laboratório

1. A leitura real de PONTOS do M3, mesmo com dados acumulados, devolve apenas O.S. emitidas hoje antes do processamento; fonte, volume lido e volume descartado ficam identificados.
2. Duas concentrações distintas de um mesmo bairro são visualizadas e acompanhadas separadamente.
3. Ao concluir uma ficha, a outra não sofre qualquer mudança.
4. O botão de cópia reutiliza apenas campos escolhidos, sem copiar conclusão ou movimentações.
5. Fichas em aberto continuam disponíveis na sessão sem recalcular dados antigos e podem ser exportadas/restauradas.
6. Eventos encerrados antes da data, medições anteriores e eventos de outros bairros/municípios não entram automaticamente nas evidências do dia.
7. O Módulo 3 continua idêntico no PR; o V3 não grava, exclui nem modifica qualquer base de produção. A página do Ambiente de Teste exige a permissão `teste`, e o leitor de fontes exige também `3`.
8. Em arquivos reais, medir tempo de leitura, filtragem, cálculo geográfico e tempo de abertura da investigação. O objetivo é reduzir o custo de processamento sem perder O.S. do dia.

## Próxima etapa, após o piloto

Validar o dia operacional com base real, revisar as regras de identidade em reimportações e só então adicionar **persistência exclusiva de teste** com isolamento e auditoria, separada da base de produção. A passagem do V3 ao Módulo 3 requer autorização explícita e uma migração planejada.
