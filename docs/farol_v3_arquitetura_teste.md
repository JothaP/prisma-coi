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

O menu **Ambiente de Teste → Farol Operacional V3 — Diário** abre as áreas:

- **Panorama do dia:** upload CSV/XLSX, filtragem pelo dia antes de calcular os agrupamentos, mapa sem desenho nem mescla, O.S. inclusive fora de concentrações, indicadores e lista de concentrações.
- **Investigação:** escolha de uma concentração, listagem das O.S., leitura opcional de eventos e pressões de arquivo e consulta somente leitura aos cadastros locais de poços e loggers, dentro de 750 metros do centro.
- **Acompanhamentos:** ficha própria por agrupamento, com status, relato, causa provável, causa confirmada, validações e movimentações. Cópia explícita entre fichas. Fichas em aberto da sessão podem ser consultadas sem dados diários recarregados.

Configurações iniciais: raio de **750 m**, mínimo de **3 O.S.**, agrupamento geográfico **dentro do mesmo município**. O código visual (ex.: THE C001) é apenas identificador para o dia; a chave interna inclui data, município e conjunto de O.S.

## Limites deliberados desta primeira etapa

- Os registros de O.S. e fichas do laboratório são mantidos apenas na **sessão do Streamlit**. Não há gravações no Supabase nem em planilhas de produção.
- O analista pode **exportar e restaurar** um JSON das fichas experimentais. Isso é um meio provisório de validar o acompanhamento e não substitui persistência operacional com auditoria e controle de concorrência.
- Quando a composição de O.S. de um agrupamento muda após nova importação no mesmo dia, a chave baseada no conjunto de O.S. muda. **Não** se religa fichas automaticamente. A estratégia definitiva de continuidade e identidade precisará ser testada antes de implantar o V3 em produção.
- O protótipo usa arquivos enviados manualmente. Ainda não automatiza o fluxo de importação diário do Farol; a escolha da fonte definitiva será validada após medir desempenho com arquivos reais.
- A relação de evento é uma sugestão por município, bairro e intervalo temporal; não substitui análise territorial operacional validada.
- O raio operacional de 750 m dos ativos e medição tem como referência inicial o **centro da concentração**. Mais tarde poderemos comparar com a distribuição espacial das O.S.
- Arquivos não são guardados permanentemente. O protótipo não elimina ou altera os dados existentes de oito dias do Módulo 3.

## Critérios de aceitação no laboratório

1. Um arquivo com oito dias gera apenas O.S. da data corrente no V3, antes do processamento espacial.
2. Duas concentrações distintas de um mesmo bairro são visualizadas e acompanhadas separadamente.
3. Ao concluir uma ficha, a outra não sofre qualquer mudança.
4. O botão de cópia reutiliza apenas campos escolhidos, sem copiar conclusão ou movimentações.
5. Fichas em aberto continuam disponíveis na sessão sem recalcular dados antigos e podem ser exportadas/restauradas.
6. Eventos encerrados antes da data, medições anteriores e eventos de outros bairros/municípios não entram automaticamente nas evidências do dia.
7. O Módulo 3 continua idêntico no PR; o teste não escreve em nenhum serviço de produção.
8. Em arquivos reais, medir tempo de leitura, filtragem, cálculo geográfico e tempo de abertura da investigação. O objetivo é reduzir o custo de processamento sem perder O.S. do dia.

## Próxima etapa, após o piloto

Validar o dia operacional com base real, revisar as regras de identidade em reimportações e só então adicionar **persistência exclusiva de teste** com isolamento e auditoria, separada da base de produção. A passagem do V3 ao Módulo 3 requer autorização explícita e uma migração planejada.
