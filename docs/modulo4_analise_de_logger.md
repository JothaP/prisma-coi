# Módulo 4.4 — Análise de Logger

## Estrutura

- **Monitor de Variação:** compara duas exportações em horários diferentes,
  vinculando medições pelo **Galaxia ID**.
- **Análise de Comportamento:** analisa um histórico individual para
  verificar se a **última medição do arquivo** é habitual para aquele
  horário e se houve movimento recente importante.

O acesso fica em **Ferramentas Operacionais > Análise de Logger**;
as duas modalidades são abas dentro de uma página Streamlit.

## Monitor de Variação — vários arquivos durante o dia

1. Selecione um ou vários XLSX/XLS/CSV no **único campo de upload**.
2. Clique **Adicionar bases ao monitoramento**.
3. Mais tarde, selecione novos arquivos no mesmo campo e adicione-os.
   As leituras anteriores continuam disponíveis **nesta sessão**.
4. A ferramenta cruza o **Galaxia ID + horário efetivo da medição**,
   removendo repetições exatas do mesmo logger no mesmo horário.
5. O painel calcula a variação entre as duas últimas medições cronológicas
   distintas por logger e mantém **todos os horários válidos** no gráfico
   individual, com pressão mínima, máxima, média e variação do período.
6. **Limpar monitoramento** reinicia a série, o contador de arquivos e
   os resultados da sessão.
7. O resultado pode ser exportado em CSV; filtros por regional, cidade,
   status, instalação, tipo de ponto e tendência são preservados.

**Importante:** um arquivo novo não significa uma leitura mais recente.
O programa usa **Data da última medição** de cada logger para ordenar
os pontos. Com um único horário, timestamp repetido ou pressão ausente,
o logger fica **Não comparável**, em vez de "Estável". Uma leitura mais
recente inválida não é substituída por uma leitura mais antiga. Para
gráficos, somente os pontos com data e pressão válidas são exibidos.

Um arquivo XLSX cujos metadados não possam ser interpretados pelo leitor
normal do Excel (erro como `invalid literal for int() with base 10:
'Infinity'`) recebe uma segunda tentativa usando as células XML
originais. Esse modo não interpreta fórmulas sem resultados calculados
nem realiza conversão de unidade. Se a recuperação falhar, o erro é
exibido para o arquivo específico e os demais arquivos válidos podem
ser adicionados sem descartá-los.

O tipo de ponto Ativo/Residência é estimado pelo nome/endereço, conforme
a heurística original; não constitui cadastro confirmado.

O mapeamento de 243 localidades e respectivas regionais, bases e zonas
foi preservado do Colab. Para municípios não mapeados, a
regional/base/zona pode vir da exportação.

## Análise de Comportamento — interpretação operacional

Há duas modalidades dentro da aba:

- **Individual:** importe um histórico XLSX/XLS/CSV; caso contenha vários
  Galaxia ID, selecione um. O diagnóstico é calculado somente sobre esse ID.
- **Em lote:** importe vários históricos simultaneamente, inclusive um arquivo
  com medições de múltiplos Galaxia ID. Equipamentos são avaliados separadamente.
  Arquivos com o mesmo ID se complementam; arquivos sem ID são identificados
  pelo nome, **sem afirmar que o nome corresponde a um Galaxia ID**.

O lote oferece totais por prioridade, filtros por direção do desvio e
prioridade, panorama completo, exportação CSV e seleção de um equipamento
para detalhar o diagnóstico e o gráfico. Limites técnicos: 100 arquivos e
150 equipamentos por processamento. Erros de um logger não impedem a
avaliação dos demais e são exibidos no panorama.

### Dimensões independentes

1. **Comportamento histórico:** `Dentro do padrão`, `Abaixo do padrão`,
   `Acima do padrão`, `Atípico dentro da faixa` ou
   `Referência insuficiente`. Acima do padrão não é alerta automático
   de falta de água. A referência usa somente dias anteriores e
   horários ±30 min da última leitura.
2. **Tendência recente:** variação líquida e amplitude entre os valores
   disponíveis na janela final de 90 minutos, com alerta para lacunas
   superiores a 30 minutos. A ausência de queda recente **não descarta**
   uma condição historicamente reduzida ou prolongada.
3. **Prioridade de investigação:** combinação de anomalia de pressão
   versus histórico, persistência próxima de zero e, opcionalmente,
   um limite mínimo operacional fornecido pelo operador. Não equivale
   a ocorrência de FA confirmada, diagnóstico de causa ou laudo hidráulico.

### Referência e parâmetros

Preserva a regra estatística original de comparação:
- Janela **±30 minutos** ao horário da última medição;
- **≥5 dias anteriores e ≥25 medições**;
- Quantis 5% e 95%, tolerância `max(0,8; 7% da amplitude)`;
- Ao menos 10% das medições de referência próximas à leitura;
- A média global do histórico não é utilizada para definir direção.
  Exibe-se a **mediana do horário**, a faixa de referência e o
  desvio da leitura final em relação à mediana.

Uma leitura pode estar **dentro da faixa dos quantis** e ainda ser pouco
frequente, caso menos de 10% dos registros estejam próximos dela. Nessa
situação, indica `Atípico dentro da faixa`, em vez de classificá-la
incorretamente como acima ou abaixo dos limites.

### Persistência, recuperação e lacunas

- Um patamar persistente abaixo da faixa daquele horário exige
  **pelo menos três medições** e **15 min**, sem lacunas superiores a
  **30 min**, dentro dos 90 minutos que precedem a leitura final.
  Esse indicador é **local à janela recente**: não prova uma
  interrupção de várias horas com os dados disponíveis.
- Pressão próxima de zero é definida para fins de **triagem** como
  `≤0,5 mca`. Uma ocorrência prolongada pode ser marcada como
  `Alta prioridade`, mas requer confirmação operacional e
  verificação da integridade do sensor.
- A detecção de recuperação busca valores próximos de zero nas
  últimas **12 horas**, seguidos de pelo menos duas leituras
  com valor `≥5 mca`, com a última atingindo a faixa de
  referência quando esta existir. É **sinal de recuperação
  nas medições**, não confirmação de abastecimento normalizado.
- Não extrapolar leituras por lacunas ou interpretar um único
  registro baixo como queda persistente.

O limite mínimo operacional específico é **opcional** e é informado
pelo operador; o sistema não assume uma pressão universal adequada.
No **lote**, caso habilitado, o mesmo limite se aplica a todos os
loggers, e a interface exige confirmação de sua aplicabilidade.

**Unidade:** a interface apresenta pressão em **mca**. A exportação deve
conter mca; não há conversão automática de bar.

**Atenção:** identificação de risco hidráulico ou impacto regional de
abastecimento exige avaliação contextual da rede, topografia, operação,
histórico de manutenções e, quando necessário, validação em campo. O
sistema não calcula interdependência hidráulica dos loggers nesta versão.

## Segurança e armazenamento

- Autenticação e autorização: acesso ao **Módulo 4** do PRISMA;
- As bases são processadas em memória e acumuladas no
  `st.session_state` do usuário, com cache temporário de leitura. **Nenhum
  registro é gravado no Supabase, Google Sheets ou em disco da aplicação**;
- O histórico acumulado fica disponível somente na sessão ativa do
  Streamlit: recarregamento profundo, expiração de sessão ou reinício do
  servidor podem apagar esses dados e exigir nova importação;
- Os dados vêm apenas dos arquivos enviados pelo operador;
- Nenhuma integração automática com Galax.ia/Andrômeda foi configurada;
- Não há monitoramento contínuo, agendamento nem captura de novas bases;
- Indicadores do Monitor calculam diferenças pontuais, não equivalem ao
  diagnóstico do padrão histórico do Analisador.
- Limite aplicado no backend de **40 MB por arquivo**, como proteção
  de desempenho; o limite global da instância Streamlit também se aplica.

## Testes

`.github/workflows/testar-analise-logger-m4.yml` verifica sintaxe e
`tests/test_analise_logger_m4.py` cobre leituras XLSX/CSV, normalização
de valores, integridade do mapeamento regional, variação versus
estabilidade, dados ausentes, timestamps repetidos/retrocedidos,
dias/mínimo histórico, padrão dentro/fora, salto persistente e
navegação/autorização do Módulo 4.

`tests/test_logger_diagnostico_lote_m4.py` cobre direção de desvios,
permanência de baixa pressão, recuperação, referências insuficientes,
limiar operacional opcional, isolamento por Galaxia ID e falhas parciais
em lotes.

`tests/test_monitor_logger_incremental_m4.py` acrescenta regressões
para cinco coletas ao longo do dia, importações em lotes consecutivos,
série completa por logger, ausência de falso "Estável" e leitura de
contingência XLSX em caso de `Infinity`.

A avaliação visual deve ser feita no Streamlit após a publicação.
