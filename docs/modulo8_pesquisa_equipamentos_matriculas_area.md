# Módulo 8 — Busca por matrícula, planta de ativo e logger

## Pesquisa integrada

O campo de pesquisa da Consulta Operacional tem três categorias:

- **Matrícula:** preserva o funcionamento anterior, com vários números
  separados por vírgula, espaço ou quebra de linha. A fonte é o índice
  SQLite (ou Excel) de `dados/matriculas.xlsx`.
- **Planta de ativo:** permite buscar a identificação da planta
  (`IDENTIFICACAO_ATIVO`, por exemplo `PL-THE-PCO0356`), o
  identificador interno `ID_POCO`, ou o nome `NOME_POCO`
  (por exemplo `THE-148`). Fonte `dados/pocos.csv`.
- **Logger:** permite buscar `IDENTIFICACAO_ATIVO` (por exemplo
  `FFDA78`) ou `ID_LOGGER`. Fonte `dados/loggers.csv`.

Na busca por equipamento, é possível informar vários códigos
separados por vírgula, ponto e vírgula ou quebra de linha. O sistema
prioriza correspondência exata, ignorando a diferença de maiúsculas,
hífens, espaços e acentos; quando não há identificação exata,
procura pelo início do código/nome (mínimo quatro caracteres).

A busca **não depende de matrículas** e enquadra os marcadores dos
equipamentos encontrados. Casos sem coordenadas válidas não são
localizados no mapa e produzem aviso. A seleção não modifica os
cadastros originais.

Para proteger o mapa contra pesquisas genéricas, aplicam-se limites de
**20 termos e 40 resultados visuais**; o usuário recebe aviso se
precisar refinar a pesquisa.

## Matrículas em polígonos

Quando um polígono ou retângulo é desenhado no mapa, a consulta espacial
existente reutiliza o índice de matrículas e devolve todos os registros
cadastrais cujas coordenadas estão dentro do perímetro ou na borda.

O mapa agora inclui a camada separada **Matrículas dentro do polígono**,
que fica ativada por padrão e pode ser ligada/desligada pelo controle de
camadas Leaflet. Os marcadores são agrupados dinamicamente conforme o
zoom, e **ao passar o mouse sobre um marcador individual** é exibido
o rótulo `Matrícula: <número>`. Clicar abre um popup com matrícula e
bairro. Não são acrescentados rótulos fixos para todas as matrículas,
evitando sobreposição visual.

O mapa limita a camada a **2.500 pontos únicos** para manter o
desempenho. Se a área contiver mais pontos, há aviso explícito;
**a tabela e a exportação CSV continuam incluindo todos os registros**,
sem truncamento. Limites de tamanho de polígono e consultas de rede já
existentes permanecem inalterados.

## Compatibilidade

- **Exibir rede da área** segue disponível, usando apenas o recorte
  KMZ/KML da rede que cruza a geometria desenhada.
- **Localizar ponto por coordenadas** permanece independente.
- As matrículas consultadas individualmente conservam marcadores,
  popups, anéis de proximidade, cotas e rótulos opcionais.
- A busca por ativos e loggers reutiliza o mesmo cadastro já usado nas
  consultas de proximidade, com informações de equipamento no popup.
- Elevações DSM/MDE continuam restritas aos pontos pesquisados e
  equipamentos exibidos, sem tentar calcular cotas para milhares de
  matrículas de uma área.
- Nenhuma consulta escreve em Excel/SQLite, Supabase ou nos cadastros
  originais.

## Validação

O workflow `.github/workflows/testar-rotulos-modulo8.yml`
executa os testes de regressão dos rótulos existentes e
`tests/test_m8_busca_ativos_matriculas_area.py` (busca por
planta/logger, isolamento dos cadastros, limites de resultados,
deduplicação dos pontos e renderização Folium com tooltip).
O workflow `.github/workflows/testar-gpkg-modulos-3-8.yml`
preserva os testes territoriais GeoPackage do Módulo 8.

A interface e os tempos de carregamento devem ser conferidos com
uma área operacional real no Streamlit após a implantação.
