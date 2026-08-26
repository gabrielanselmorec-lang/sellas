# Motor ABC fechado por intervalos

## Objetivo

O modulo ABC fechado separa tres tarefas:

1. registro do que aconteceu em cada intervalo observado;
2. analise descritiva de associacoes ABC;
3. preparacao de dados temporais para predicao futura.

O modulo nao confirma causa, funcao comportamental ou manutencao por consequencia. Ele calcula padroes observados para revisao clinica.

## Entidades

- `abc_instrument_versions`: versao do instrumento fechado.
- `abc_sessions`: sessao observada por paciente e servico.
- `abc_intervals`: intervalos discretos, normalmente de 5 minutos.
- `abc_categories`: categorias fechadas de antecedente, comportamento e consequencia.
- `abc_interval_events`: valor observado por categoria em cada intervalo.

## Semantica dos valores

- `true`: evento observado.
- `false`: ausencia confirmada em intervalo efetivamente observado.
- `null`: desconhecido, nao registrado ou impossivel determinar.
- `not_observed`: intervalo nao observado, fora do denominador.
- `not_applicable`: categoria ou intervalo nao aplicavel.

Ausencia de registro nao vira `false`.

## Arredondamento temporal

A funcao `floor_timestamp_to_interval(timestamp, interval_minutes)` recebe timestamp completo, trabalha em UTC e arredonda para baixo.

Exemplos:

- `10:32`, intervalo 5 minutos -> `10:30`;
- `10:35`, intervalo 5 minutos -> `10:35`;
- `23:59`, intervalo 5 minutos -> `23:55`.

## Periodo de analise

Nao ha data fixa no codigo. As configuracoes sao:

- `ABC_ANALYSIS_START_DATE`;
- `ABC_ANALYSIS_END_DATE`;
- `ABC_INCLUDE_WEEKENDS`;
- `ABC_INTERVAL_MINUTES`;
- `ABC_MINIMUM_VALID_INTERVALS`.

Finais de semana sao incluidos por padrao. O denominador e o numero de intervalos realmente observados.

## Metricas descritivas

Para cada par antecedente-comportamento, sao calculados:

- intervalos observados validos;
- intervalos com antecedente;
- intervalos com comportamento;
- intervalos com ambos;
- `P(B)`;
- `P(B|A)`;
- `P(B|nao A)`;
- diferenca absoluta de risco;
- risco relativo;
- lift;
- odds ratio com correcao de continuidade;
- Phi/Matthews;
- intervalo de confianca de Wilson;
- qualidade da estimativa.

O mesmo principio e aplicado a comportamento-consequencia.

## Formulas

```text
P(B=1 | A=1) = N(A=1,B=1) / N(A=1)
P(B=1) = N(B=1) / N(intervalos observados validos)
Lift(A,B) = P(B=1 | A=1) / P(B=1)
RD = P(B=1 | A=1) - P(B=1 | A=0)
OR = (n11*n00) / (n10*n01)
phi = (n11*n00 - n10*n01) / sqrt((n11+n10)(n01+n00)(n11+n01)(n10+n00))
```

## Predicao temporal

Para prever o proximo intervalo:

```text
Y(t+1)=1 se o comportamento ocorrer no intervalo seguinte
P(Y(t+1)=1 | X_t)
```

Permitido:

- antecedentes do intervalo atual;
- comportamentos anteriores;
- consequencias de intervalos anteriores;
- consequencias do intervalo ja encerrado apenas para prever intervalo futuro;
- hora, contexto, atividade e qualidade do dado;
- anotacoes com `authored_at <= landmark_ts`.

Proibido:

- consequencia do mesmo comportamento que esta sendo previsto no mesmo intervalo;
- resumo final de sessao;
- anotacao escrita depois do evento;
- total futuro da sessao;
- informacao retroativa sem validade temporal.

## Endpoints

- `GET /api/abc/config`
- `POST /api/abc/time/floor`
- `POST /api/abc/instrument-versions`
- `GET /api/abc/categories`
- `POST /api/abc/categories`
- `POST /api/abc/sessions`
- `GET /api/abc/sessions`
- `POST /api/abc/sessions/{session_id}/intervals`
- `GET /api/abc/sessions/{session_id}/intervals`
- `POST /api/abc/intervals/{interval_id}/events`
- `GET /api/abc/analysis/conditional-probabilities`
- `GET /api/abc/analysis/associations`
- `GET /api/abc/analysis/timeline`
- `GET /api/abc/prediction/rows`
- `GET /api/abc/reports/summary`

## Resumo imprimivel

O endpoint `GET /api/abc/reports/summary` e o contrato unico para previa,
Word e PDF. Ele recebe paciente, periodo, ambiente, inclusao de finais de
semana, escopo das cadeias e opcao de anonimizar o nome. O backend calcula os
totais; o frontend nao recalcula denominadores.

Regras importantes:

- sessoes e intervalos sao contados por identificadores distintos;
- mais de um evento no mesmo intervalo conta como uma unica ocorrencia de
  intervalo;
- apenas intervalos `observed` entram no denominador de ocorrencia;
- `not_observed`, `invalid`, `not_applicable` e valores nulos permanecem
  semanticamente distintos de `false`;
- cadeias rejeitadas ou censuradas nao sao impressas como resultado clinico;
- associacoes, lift e sequencias temporais sao descritivos e nao confirmam
  causa nem funcao comportamental.

A geracao grava metadados em `abc_report_audit_logs`: paciente tokenizado,
responsavel informado, periodo, filtros, formato, anonimizacao e versao da
logica. O conteudo clinico integral nao e persistido no log tecnico.

Detalhes do payload, impressao, dicionario de dados e limitacoes estao em
`docs/abc_printable_summary.md`.

## Supabase

Ha scripts prontos em `supabase/abc_fechado`:

- `001_create_abc_fechado.sql`: cria tabelas, indices, constraints e RLS.
- `002_seed_abc_categorias.sql`: adiciona versao inicial do instrumento e categorias fechadas.

Esses arquivos podem ser abertos no SQL Editor do Supabase e executados em ordem.

## Visualizacoes

- matriz antecedente-comportamento;
- matriz comportamento-consequencia;
- linha temporal;
- proporcao por intervalos observados;
- mapa descritivo de associacoes ABC.

Aviso obrigatorio:

```text
As associacoes apresentadas nao confirmam causa ou funcao comportamental e precisam ser interpretadas por profissional qualificado.
```

## Limitacoes

- O modulo calcula associacoes descritivas, nao inferencias funcionais.
- A predicao ABC por intervalo ainda e dataset/contrato inicial; treino especifico por intervalo deve ser expandido em fase posterior.
- A API clinica local ainda nao possui identidade autenticada nem papeis por
  usuario. A existencia do paciente e validada e a geracao e auditada, mas o
  bloqueio por permissao individual depende da futura camada de autenticacao.
