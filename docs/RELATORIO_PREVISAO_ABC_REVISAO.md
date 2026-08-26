# Revisão técnica do relatório ABC

## Diagnóstico

A implementação anterior misturava quatro grandezas: proporção histórica entre episódios, escore contextual ponderado, peso de gravidade C1/C2 e hipótese funcional. O endpoint `/api/abc/previsao` chamava essa composição de probabilidade do próximo registro e aplicava um intervalo de Wilson calculado apenas sobre o subconjunto contextual. Como a base principal contém episódios ABC registrados e não todas as oportunidades observáveis sem comportamento, não há denominador para risco absoluto.

Também foram confirmados os seguintes problemas:

- suavização Beta(1,1) apresentada junto de intervalo de Wilson da proporção bruta;
- consequência, gravidade e função visualmente próximas de um valor preditivo, embora sejam posteriores ou semanticamente distintas;
- rótulo “perigo” exibido como percentual, apesar de C1=0,20 e C2=1,00 serem pesos internos;
- registros sem C1/C2 contabilizados, mas sem penalização auditável da qualidade;
- categorias legadas sem trilha explícita entre valor original e normalizado;
- “Do nada” passível de leitura literal, em vez de antecedente não identificado;
- “Manejo fisíco” sem correção versionada;
- transições temporais sem identificador determinístico único;
- estabilidade temporal dependente sobretudo de repetição e confiança, sem exigir cobertura por dias;
- relatório com medidas heterogêneas no mesmo gráfico e textos cortados por reticências.

## Unidade estatística encontrada

A linha usada pela análise principal representa um episódio ABC fechado com antecedente, comportamento e consequência selecionados. Os intervalos de observação fornecem cobertura para o resumo descritivo, mas o fluxo legado de episódios não fornece automaticamente exemplos negativos válidos para um comportamento-alvo específico. Ausência de episódio não é ausência observada.

## Decisão metodológica

O modo padrão passou a ser `analysis_mode="descriptive"`, com o estimando `relative_distribution_among_recorded_episodes`. O modo preditivo só pode ser produzido por `fit_validated_logistic_model` quando existem:

1. alvo e horizonte configurados;
2. oportunidades positivas e negativas observáveis;
3. features anteriores ao alvo;
4. separação cronológica por sessão;
5. baseline;
6. métricas fora da amostra e calibração.

## Registros legados oficiais

Os registros legados são tratados como oficiais. A migração não apaga nem substitui silenciosamente o valor de origem:

- `nome_original` e `funcao_hipotese_original` preservam o valor oficial;
- `nome_normalizado` e `funcao_hipotese_normalizada` alimentam as análises atuais;
- regra e versão de normalização ficam registradas;
- consultas usam o valor normalizado quando disponível;
- nenhuma ausência ou classe negativa é inventada.

## Escopo dos arquivos

Foram limitadas as mudanças a serviços, schema, API, dashboard, relatórios, testes, documentação e ferramentas diretamente relacionados ao ABC. Componentes de previsão de habilidades e outros módulos clínicos não foram alterados.
