# Módulo 1 — agrupamento dinâmico dos marcadores de pressão

## Objetivo
Substituir a sobreposição dos cartões de pressão no mapa amplo por
agrupamentos que se dividem ao aproximar o zoom, mantendo cada medição.

## Navegação
- **Visualização > Medições individuais:** pontos próximos são agregados
  pelo Leaflet.markercluster, com um número representando a quantidade
  de medições **após os filtros atuais**.
- **Clique no círculo:** o mapa aproxima automaticamente a extensão do
  grupo; novos grupos menores surgem à medida que o zoom aumenta.
- **Zoom detalhado (15 ou superior):** cada ponto individual exibe seu
  rótulo de pressão (e cota, quando disponível) se a caixa
  **Exibir rótulos** estiver marcada. Antes disso, somente os pins e
  números dos agrupamentos aparecem.
- **Coordenadas coincidentes:** no zoom máximo, clique no agrupamento
  para espalhar os marcadores (`spiderfy`) e acessar cada popup.
- **Visualização > Por bairro:** permanece com a apresentação consolidada
  original; o agrupamento não é aplicado neste modo.

## Criticidade das cores
A cor de cada agrupamento representa a **pior faixa presente**, não a
média das medições. Isso evita ocultar um ponto sem pressão em um grupo
predominantemente normal. Prioridades e cores são as mesmas do mapa:

| Prioridade | Faixa | Cor |
| --- | --- | --- |
| 5 | Sem pressão = 0 MCA | vermelho |
| 4 | Baixa pressão, > 0 e ≤ 5 MCA | amarelo |
| 3 | Em atenção, > 5 e < 10 MCA | rosa |
| 2 | Normal, 10 a 15 MCA | azul |
| 1 | Alta pressão, > 15 MCA | verde |

A dica de um círculo mostra quantas medições estão agrupadas e quantas
são 0 MCA ou baixa pressão. A contagem é de medições, não de loggers
únicos, pois o mapa original exibe medições individuais.
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
- A função JS de cor utiliza somente uma prioridade inteira do próprio
  marcador; não injeta dados externos no HTML dos agrupamentos.

## Validação
Workflow `.github/workflows/testar-clusters-baixa-pressao.yml`,
com regressões para limites das cinco faixas, paleta existente, render
de MarkerCluster, opções de expansão/spiderfy, modo por bairro, filtros,
chave do `st_folium` e rótulos somente em zoom próximo.

A validação automatizada não substitui uma conferência visual de zoom
e popups no navegador depois do deploy.
