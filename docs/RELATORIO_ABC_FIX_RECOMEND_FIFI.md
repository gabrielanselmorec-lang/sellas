# Aplicação da fix `fix_recomend_fifi`

## Escopo

A especificação contém 40 críticas ao relatório ABC. A metodologia v2 já havia corrigido a separação entre descrição e previsão, leakage, Wilson, normalização e cadeias. Esta rodada cria a metodologia `abc-methodology-v3` e fecha as lacunas verificáveis restantes sem converter ausência de registro em ausência de comportamento.

## Matriz de atendimento

| # | Crítica | Tratamento na v3 |
|---:|---|---|
| 1 | Frequência tratada como previsão | Modo descritivo continua explícito; predição só aparece após validação. |
| 2 | Unidade ambígua | Alvo, unidade, horizonte e data de referência são obrigatórios no modo preditivo. |
| 3 | Viés de seleção | Aviso automático quando todos os intervalos observados possuem episódio. |
| 4 | Ausência de negativos | Modelo recusa bases sem positivos e negativos observáveis. |
| 5 | Leakage | Consequência, gravidade, função e identificadores continuam bloqueados como features. |
| 6 | Sem validação futura | Holdout cronológico por sessão e backtesting rolling-origin. |
| 7 | Sem baselines | Compara prevalência, sempre ausência, sempre ocorrência, ambiente e antecedente quando disponíveis. |
| 8 | Métricas insuficientes | Brier, Log Loss, PR-AUC, ROC-AUC, precisão, recall, especificidade, matriz, falsos alertas e eventos perdidos. |
| 9 | Wilson não auditável | Exibe k, n, nível, método e separa Wilson bruto de posterior Beta. |
| 10 | Dependência entre episódios | IC por bootstrap agrupado por sessão, com seed e número de reamostragens. |
| 11 | Pesos arbitrários | Mantidos como pesos internos versionados, com justificativa e aviso de ausência de validação clínica. |
| 12 | Risco dominado por frequência | Frequência e peso permanecem separados; mapa informa quando a gravidade não varia. |
| 13 | Ausência de C2 | Alerta automático de falta de suporte empírico para gravidade intensa. |
| 14 | Cadeias raras no corpo | Corpo usa suporte mínimo 8; lista completa fica no apêndice. |
| 15 | Suavização simples | Não é usada como correção de baixa amostra; posterior Beta é opção separada e identificada. |
| 16 | Estabilidade opaca | Critérios versionados por ocorrências, sessões, dias, concentração e confiança. |
| 17 | Estabilidades contraditórias | Qualidade geral e estabilidade temporal são exibidas em objetos e seções distintas. |
| 18 | Taxonomia inconsistente | Normalização versionada com preservação do valor oficial original. |
| 19 | “Do nada” | Normalizado para “Antecedente não identificado no registro”. |
| 20 | Sem relatório de qualidade | Auditoria de duplicidade, ausências, timestamps, sobreposição, taxonomia, classificação, observadores, sessões e horas. |
| 21 | Sem rastreabilidade | IDs faltantes, origem, taxonomia, dataset, pipeline e hash da execução são registrados. |
| 22 | Duplicidade/segmentação | Detecta IDs duplicados, linhas idênticas, sobreposição e intervalos temporais inválidos. |
| 23 | Sem exposição | Horas, oportunidades sem ocorrência, episódios/hora e exposição por ambiente/data. |
| 24 | Período curto | Alerta automático para recortes menores que 28 dias. |
| 25 | Um paciente | Validade externa e generalização permanecem explicitamente desconhecidas. |
| 26 | Overfitting | L2, baselines, holdout, backtesting e filtro de suporte; ganho precisa superar o melhor baseline. |
| 27 | Título excessivo | “Relatório de análise ABC fechada” e nome de arquivo `relatorio_analise_abc_*`. |
| 28 | Observado e previsto misturados | Seções descritiva e preditiva separadas; seção preditiva é bloqueada quando inválida. |
| 29 | Gráfico com métricas incompatíveis | Gráficos separam proporções observadas, gravidade e resultados preditivos validados. |
| 30 | Mapa pouco discriminativo | Aviso visual quando o peso de gravidade não varia. |
| 31 | Tabela extensa | Top com suporte mínimo; apêndice integral. |
| 32 | Textos truncados | Rótulos de gráficos são quebrados em linhas e tabelas mantêm o texto completo. |
| 33 | Ambientes inconsistentes | Filtro analítico e ambiente da visualização aparecem separadamente; exposição é discriminada por ambiente. |
| 34 | Contagem diária sem normalização | Usa episódios por hora observada quando a exposição é calculável; caso contrário rotula como contagem. |
| 35 | Sem calibração | Intercepto, inclinação, ECE e curva de calibração fora da amostra. |
| 36 | Sem avaliação operacional | Limiar, alertas, falsos alertas, eventos detectados/perdidos e oportunidades avaliadas. |
| 37 | Sem versão | Versões de dataset, taxonomia, pipeline, modelo, features, parâmetros e hash SHA-256. |
| 38 | Sem drift | PSI por feature, mudança de prevalência e limites versionados; escopo treino-teste declarado. |
| 39 | Sem explicação individual | Contribuições em log-odds da última oportunidade de validação, com aviso de não causalidade. |
| 40 | Sem generalização | Status de generalização desconhecida aparece no contrato e nas limitações. |

## Regras conservadoras

- Ausência de dado nunca é convertida em “não ocorreu”.
- Itens não avaliáveis aparecem como “Não avaliável”, não como zero.
- Taxas só existem com intervalo observado e duração positiva.
- Drift treino-teste não é apresentado como monitoramento prospectivo.
- Explicações de coeficientes não são apresentadas como causalidade.
- Registros legados oficiais preservam o valor original e recebem valor normalizado em campo separado.

## Validação

Os testes específicos cobrem auditoria, exposição, bootstrap por sessão, hash reproduzível, baselines, backtesting, operação, drift, explicabilidade e conteúdo do relatório. Validação final em 20/07/2026: **159 testes aprovados**, sem falhas; permaneceram 40 avisos de depreciação preexistentes.
