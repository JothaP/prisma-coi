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

## Análise de Comportamento

1. Importe o histórico XLSX/XLS/CSV do logger.
2. Se o arquivo possuir vários **Galaxia ID**, selecione um deles,
   sem juntar históricos de aparelhos diferentes.
3. Examine classificação, diagnóstico textual, métricas e curva de pressão.
4. Se necessário, baixe o diagnóstico TXT.

O algoritmo mantém os parâmetros enviados no Colab:
- Referência temporal: **±30 minutos** do horário da última medição;
- Dias anteriores mínimos: **5**;
- Registros da faixa horária mínimos: **25**;
- Quantis 5 e 95 com tolerância `max(0,8; 7% da amplitude)`;
- Exigência de ao menos 10% dos registros de referência próximos ao
  valor atual;
- Busca saltos persistentes nos **últimos 90 minutos**, com amplitude
  mínima de 3 unidades e variação relativa de 10%;
- Fallback de avaliação de tendência dentro da última hora.

Se os requisitos de referência não forem alcançados, apresenta
**Dados insuficientes**, sem inventar classificação de normalidade.

**Unidade:** a interface apresenta pressão em **mca**. Confirme que a
planilha exportada já utiliza mca. Os limites preservados do código
original são numéricos e não realizam conversão automática de bar/mca.

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

`tests/test_monitor_logger_incremental_m4.py` acrescenta regressões
para cinco coletas ao longo do dia, importações em lotes consecutivos,
série completa por logger, ausência de falso "Estável" e leitura de
contingência XLSX em caso de `Infinity`.

A avaliação visual deve ser feita no Streamlit após a publicação.
