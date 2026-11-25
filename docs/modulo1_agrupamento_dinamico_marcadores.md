# Módulo 1 — agrupamento dinâmico dos marcadores de pressão

## Objetivo
Substituir a sobreposição dos cartões de pressão no mapa amplo por
agrupamentos que se dividem ao aproximar o zoom, mantendo cada medição.

## Navegação
- **Visualização > Medições individuais:** pontos próximos são agregados
  pelo Leaflet.markercluster. O número dentro do círculo representa a
  **média aritmética da pressão das medições agrupadas**, em **MCA**
  (uma casa decimal, vírgula decimal), após os filtros ativos.
  Exemplo: 0, 10 e 20 MCA produzem a média **10,0 MCA**.
- **Clique no círculo:** o mapa aproxima automaticamente a extensão do
  grupo; novos grupos menores surgem à medida que o zoom aumenta.
- **Zoom detalhado (15 ou superior):** cada ponto individual exibe seu
  rótulo de pressão (e cota, quando disponível) se a caixa
  **Exibir rótulos** estiver marcada. Antes disso, somente os pins e
  médias em MCA dos agrupamentos aparecem.

### Alternativa: Sem agrupamento (visualização anterior)

Na linha de filtros do mapa, à direita de **Exibir rótulos**, há a caixa
**Sem agrupamento**, inicialmente **desmarcada**.

- **Desmarcada (padrão):** usa o agrupamento dinâmico por zoom. Os
  círculos apresentam a **média de pressão em MCA**, com a cor da
  faixa mais crítica. Os rótulos de cada medição aparecem apenas no
  zoom 15 ou superior, se **Exibir rótulos** estiver ativo.
- **Marcada:** no modo **Medições individuais**, remove inteiramente
  a camada de clusters: **cada medição é renderizada no local original
  como marcador individual**, com seu popup, cor e pressão. Com
  **Exibir rótulos** marcado, os cartões de pressão (e cota de terreno,
  quando disponível) ficam **visíveis em qualquer nível de zoom**,
  como antes da implantação dos agrupamentos.
- **Exibir rótulos** desmarcado: mantém apenas os pins individuais
  e os popups, sem cartões, em ambos os modos.
- **Visualização > Por bairro:** não tem agrupamento por proximidade;
  a opção **Sem agrupamento** fica desabilitada e o comportamento
  consolidado por bairro permanece inalterado.

O mapa é remontado ao alternar o modo para impedir que clusters
e marcadores avulsos de uma visualização anterior permaneçam sobrepostos.
Quando muitos pontos estiverem presentes, o modo sem agrupamento poderá
apresentar sobreposição visual e consumir mais recursos do navegador.
Os filtros, dados cadastrais, classificação, tabelas e PDF não são
afetados.

- **Coordenadas coincidentes:** no zoom máximo, clique no agrupamento
  para espalhar os marcadores (`spiderfy`) e acessar cada popup.
- **Visualização > Por bairro:** permanece com a apresentação consolidada
  original; o agrupamento não é aplicado neste modo.

## Criticidade das cores
O **valor** do agrupamento representa a média de pressão em MCA.
A **cor** representa a pior faixa de pressão presente, não a faixa da
média. Ex.: 0, 10 e 20 MCA dão média 10,0 MCA com **círculo vermelho**,
porque há um ponto sem pressão. Isso evita ocultar situações críticas.
Prioridades e cores são as mesmas do mapa:

| Prioridade | Faixa | Cor |
| --- | --- | --- |
| 5 | Sem pressão = 0 MCA | vermelho |
| 4 | Baixa pressão, > 0 e ≤ 5 MCA | amarelo |
| 3 | Em atenção, > 5 e < 10 MCA | rosa |
| 2 | Normal, 10 a 15 MCA | azul |
| 1 | Alta pressão, > 15 MCA | verde |

A dica de um círculo mostra a média, a quantidade de medições consideradas,
quantos pontos estão em 0 MCA, quantos estão em baixa pressão e a pior
faixa. A média é das **medições individuais**, não de equipamentos únicos,
pois o mapa original apresenta as medições individuais.
Valores negativos seguem a classificação histórica de baixa pressão.

## Limites e cuidados
- Não altera o banco, a planilha, os dados, o filtro de faixas, os
  indicadores, a tabela ou os PDFs.
- Os popups individuais mantêm bairro, município, matrícula, pressão,
  classificação, data, ID e observação.
- O zoom detalhado evita desenhar dezenas de cartões durante a visão
  regional; os pontos não são descartados.
- `chunkedLoading` e `removeOutsideVisibleBounds` limitam impacto no
  navegador em listas maiores.
- A camada Sanear e o tipo de mapa (padrão, satélite e terreno) são
  preservados.
- A função JS utiliza somente a pressão numérica e a prioridade da faixa
  no próprio marcador; não injeta nomes, matrículas ou endereços no HTML
  dos agrupamentos.
- O número impresso **não é a quantidade**. A quantidade permanece no
  detalhe ao passar o mouse sobre o agrupamento.

## Validação
Workflow `.github/workflows/testar-clusters-baixa-pressao.yml`,
com regressões para limites das cinco faixas, paleta existente, render
de MarkerCluster, opções de expansão/spiderfy, modo por bairro, filtros,
alternância entre clusters e pins diretos, rótulos em qualquer zoom
no modo sem agrupamento e chave distinta do `st_folium`. O teste
`tests/test_bp_cluster_media.js` executa a função real de média em JS
com grupos contendo 0 MCA, pressão normal, valores decimais, negativos
e dados ausentes.

A validação automatizada não substitui uma conferência visual de zoom
e popups no navegador depois do deploy.
