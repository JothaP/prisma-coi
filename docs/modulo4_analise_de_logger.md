# Módulo 4.4 — Análise de Logger

## Estrutura

- **Monitor de Variação:** compara duas exportações em horários diferentes,
  vinculando medições pelo **Galaxia ID**.
- **Análise de Comportamento:** analisa um histórico individual para
  verificar se a **última medição do arquivo** é habitual para aquele
  horário e se houve movimento recente importante.

O acesso fica em **Ferramentas Operacionais > Análise de Logger**;
as duas modalidades são abas dentro de uma página Streamlit.

## Monitor de Variação

1. No campo **Base anterior**, envie XLSX/XLS/CSV do primeiro momento.
2. Em **Base posterior**, envie XLSX/XLS/CSV do momento seguinte.
3. Se cada momento foi exportado em diversos arquivos, envie todos os
   arquivos daquele momento no mesmo campo. O último timestamp por
   Galaxia ID prevalece dentro de cada conjunto.
4. Examine indicadores, filtros por regional/cidade/status/instalação/
   tipo/tendência, tabela de variações, gráfico de maiores diferenças
   e consulta individual.
5. Use exportação CSV para salvar o resultado filtrado.

**Importante:** só há comparação válida quando existem duas leituras
numéricas com timestamps e a leitura da base posterior é **mais recente**.
Mesma data/hora de medição (mesmo que os arquivos tenham sido baixados
em horários diferentes) significa **sem nova medição**, não "estável".
Outros casos com falta de dado, logger presente em uma base apenas, ou
retrocesso do timestamp ficam em **Não comparável**, com justificativa.

O tipo de ponto Ativo/Residência é estimado pelo nome/endereço conforme
a heurística do código original; não constitui cadastro confirmado.

O mapeamento de 243 localidades e respectivas regionais, bases e zonas
foi preservado do Colab enviado. Para municípios não mapeados, a
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
- As bases são processadas em memória com cache temporário; **nenhum
  registro é gravado no Supabase, Google Sheets ou em disco da aplicação**;
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

A avaliação visual deve ser feita no Streamlit após a publicação.
