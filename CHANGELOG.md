# Changelog

## ABC methodology v3 — 20/07/2026

- Acrescenta auditoria de qualidade e rastreabilidade, exposição por hora e IC agrupado por sessão.
- Amplia a validação preditiva com baselines múltiplos, backtesting, operação, drift e explicabilidade.
- Filtra cadeias raras no corpo, preserva lista integral no apêndice e elimina truncamentos gráficos.
- Versiona dataset, pipeline, modelo, features, parâmetros e hash da execução.

## 2026-07-20

- Revisada a metodologia do relatório ABC: proporções de episódios não são mais chamadas de previsão do próximo registro.
- Adicionado modo descritivo explícito, com `k/n`, Wilson reproduzível e posterior Beta como alternativa separada.
- Adicionado caminho preditivo com regressão logística L2, holdout cronológico por sessão, baseline, Brier, Log Loss, PR-AUC, ROC-AUC e calibração, condicionado à presença de negativos observáveis.
- Registros legados passaram a ser oficiais no modelo atual por migração auditável, preservando valor original, valor normalizado, regra e versão.
- Corrigidas as categorias “Do nada” e “Manejo fisíco” sem apagar o valor original.
- “Perigo (%)” foi substituído por peso médio configurado de gravidade; não classificados e risco exploratório são mostrados separadamente.
- Cadeias temporais receberam `transition_id` determinístico, deduplicação e estabilidade baseada também em sessões e dias.
- Reorganizados DOCX/PDF em seções clínicas e metodológicas; cadeias de baixo suporte permanecem no apêndice.
- Adicionado endpoint atual `/api/abc/estimativa-descritiva`; `/api/abc/previsao` permanece como alias depreciado.
- Removido o gerador Excel do caminho sincrono de inclusao, edicao e remocao ABC, evitando timeout HTTP de 90 segundos apos o banco ja ter confirmado a operacao.
- A exportacao Excel agora roda em segundo plano e consolida novas alteracoes recebidas durante uma geracao em andamento.
- A interface diferencia confirmacao do registro e exportacao pendente, sem afirmar que o arquivo terminou antes da conclusao.
- Corrigidos deadlocks intermitentes ao abrir o modulo ABC enquanto rotas de cadeias inicializavam o schema.
- A inicializacao das tabelas ABC e de cadeias agora e serializada, executada uma unica vez no startup da API e protegida por advisory lock do PostgreSQL.
- Removido DDL da auditoria executado durante cada geracao de relatorio.
- Alteracoes de constraints passaram a ocorrer somente quando a definicao realmente precisa ser atualizada.
- Leituras analiticas repetem, com backoff curto, apenas SQLSTATEs transitorios `40P01`, `40001` e `55P03`.
- Adicionados testes para retry de deadlock e idempotencia da inicializacao do schema.

## 2026-07-17

- Adicionado resumo clinico imprimivel do ABC fechado com previa no Streamlit.
- Adicionado `GET /api/abc/reports/summary` como contrato unico dos calculos de previa e exportacao.
- Redesenhado o PDF em A4 com explicacao do ABC, estados de observacao, frequencias, associacoes, cadeias, indicadores, formulas, qualidade e limitacoes.
- Adicionados filtros de periodo, finais de semana, graficos, cadeias candidatas/revisadas e anonimizacao do paciente.
- Adicionada auditoria de geracao sem gravar o conteudo clinico completo em logs tecnicos.
- Corrigidos denominadores para intervalos distintos observados, preservando `null` e `not_observed` como estados diferentes de ausencia.
- Adicionados testes do resumo, anonimizacao, cadeias, PDF, CSS de impressao, interface e auditoria.

## 2026-07-16

- Adicionada uma interpretação direta, em linguagem simples, à previsão comportamental.
- Adicionada geração sob demanda de relatórios Word e PDF com gráficos, probabilidades, projeção mensal, faixa de incerteza e fórmulas.
- Adicionado relatório Word/PDF próprio na aba ABC fechada, com previsão contextual, cadeias A-B-C, C1/C2, função, mapa de risco e cadeias temporais.
- Adicionada seleção entre uma cadeia específica e todas as cadeias no relatório ABC, com listagem integral e guia de interpretação dos indicadores.
- Mantidos os mesmos resultados do modelo analítico na tela e nos documentos, sem criar uma segunda lógica de cálculo.
- Adicionados testes que abrem os artefatos e confirmam o conteúdo e a presença do gráfico no Word.

## 2026-07-14

- Adicionado módulo auditável de cadeias temporais no ABC fechado.
- Adicionadas configuração e regras versionadas, revisão humana e estatísticas 2x2.
- Adicionadas matriz de transição, timeline e features protegidas por landmark.
- Preservada a análise A-B-C descritiva do mesmo episódio como módulo separado.
- Corrigida a exibição de motivos vazios e adicionada aprovação auditável das cadeias atuais.
