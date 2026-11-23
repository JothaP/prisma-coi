# Farol Módulo 3 — correção manual da localização de O.S.

## Objetivo
Permitir que um operador com permissão de **editar** no Módulo 3
corrija apenas **latitude** e **longitude** de uma O.S. importada,
inclusive sem coordenadas válidas, sem reimportar o lote.

## Como utilizar

1. No **Panorama**, clique no marcador de uma O.S. e escolha
   **✏️ Corrigir localização desta O.S.**; na análise individual há
   o mesmo botão.
2. Para O.S. sem marcador, abra o painel
   **✏️ Corrigir coordenadas pelo número da O.S.** e informe exatamente
   o número cadastrado na data consultada.
3. Na janela, clique na nova localização no mapa ou preencha
   **Nova latitude** e **Nova longitude** (vírgula ou ponto decimal).
4. Revise o deslocamento apresentado quando for maior que 5 km e
   clique **💾 Salvar coordenadas**.
5. Para desfazer, reabra a O.S., marque a confirmação e selecione
   **↩️ Restaurar coordenadas importadas**.

## Persistência e rastreabilidade

- Correções são salvas **somente após ação explícita**, no Google Sheets
  configurado em `farol_producao_spreadsheet_id`, aba
  **`FAROL_COORDENADAS_OS`**. A aba é criada apenas no primeiro salvamento.
- Cada ação **CORRIGIR** ou **RESTAURAR** adiciona uma nova linha.
  O histórico guarda a chave estável `OS:<Nº da O.S>`, coordenadas
  corrigidas, coordenadas importadas ao editar, data/hora, autor e ID.
- A última ação por O.S. determina a posição utilizada na leitura do M3.
- A aba **`PONTOS` não é alterada** por essas correções.
  Uma nova importação de O.S. não sobrescreve as correções ativas.
- **RESTAURAR** volta às coordenadas da versão importada que estiver
  armazenada em PONTOS, sem excluir as linhas anteriores do histórico.
- Coordenadas corrigidas são aplicadas em memória **antes** de formar
  concentrações, calcular raio/proximidade e montar mapas/PDFs do M3.
  Uma O.S. sem coordenada pode passar a aparecer no panorama.
- A alteração é restrita ao **Módulo 3**. Outros módulos que leiam
  diretamente a aba PONTOS continuam usando a localização importada,
  até que sejam adaptados explicitamente.
- A leitura é cacheada por 45 s, mas a gravação limpa o cache
  imediatamente e atualiza o mapa da sessão.
- Cabeçalho/histórico inválidos são tratados como erro e bloqueiam o
  uso de correções, para não apresentar coordenadas incorretas.
- A rotina de limpeza administrativa padrão do Farol não apaga a aba
  de histórico de coordenadas; ela não é uma base de O.S.
- A página impede correção por perfis sem autorização `editar`.
  Antes de salvar há uma segunda validação de permissão.

## Limitações / cuidados

- A O.S. precisa ter `Nº da O.S` preenchido.
- É necessário que o usuário informe coordenadas válidas (não zero,
  latitude entre -90 e 90, longitude entre -180 e 180).
- A detecção de edição concorrente é de **melhor esforço** via releitura
  antes do `append_row`; o Google Sheets não fornece transação serializável
  para duas operações simultâneas.
- Correções antigas continuam preservadas mesmo após limpeza da base
  `PONTOS`; como a chave é o número da O.S., só são usadas novamente se
  a mesma O.S. for reimportada.
- Ao começar, o mapa de edição fica centrado na posição disponível da O.S.;
  caso não exista, parte da região de Teresina **somente como orientação**.
  Esse centro padrão não é salvo sem clique/edição explícitos.

## Testes e publicação
A rotina `tests/test_farol_coordenadas_os.py` usa planilhas simuladas e
verifica validação, gravação append-only, versão concorrente, reimportação,
restauração, isolamento entre O.S. e integração antes do cálculo dos grupos.
O workflow `validar-farol-coordenadas-os.yml` executa esses testes.
