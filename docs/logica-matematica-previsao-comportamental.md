# Logica matematica da previsao comportamental

## Objetivo

A aplicacao estima a probabilidade de ocorrencia de um comportamento-alvo em uma janela futura configuravel, usando historico comportamental extraido da bHave. O MVP usa como padrao a proxima sessao.

A previsao e uma ferramenta de apoio a decisao e nao substitui a avaliacao clinica do analista do comportamento.

## Variavel-alvo

A variavel-alvo binaria e:

```text
Y_t,h = 1 se o comportamento X ocorrer na janela futura h
Y_t,h = 0 se o comportamento X nao ocorrer na janela futura h
```

Onde:

- `t` e a sessao atual.
- `h` e a janela futura de previsao.
- No MVP, `h = 1`, isto e, proxima sessao.

Para evitar vazamento de dados, as features de `X_t` usam somente dados ate a sessao `t`. O alvo e criado por deslocamento futuro do indicador de ocorrencia.

## Probabilidade prevista

O sistema estima:

```text
p_t,h = P(Y_t,h = 1 | X_t)
```

A resposta da API sempre retorna uma probabilidade entre `0` e `1`, alem da classificacao de risco.

## Features implementadas

As features temporais e contextuais incluem:

- frequencia historica do comportamento;
- frequencia nas ultimas `N` sessoes;
- media da frequencia nas ultimas `N` sessoes;
- frequencia nos ultimos `N` dias;
- media movel de frequencia;
- media movel de duracao;
- media movel de intensidade;
- risco recente por media movel exponencial;
- tempo desde a ultima ocorrencia;
- sessoes desde a ultima ocorrencia;
- taxa recente de ocorrencia no mesmo ambiente;
- taxa recente com mesmo antecedente;
- taxa recente com consequencia semelhante;
- funcao hipotetizada;
- horario da sessao;
- dia da semana;
- terapeuta/aplicador;
- estrategias utilizadas;
- nivel de prompt;
- indice de independencia;
- tendencia recente de aumento ou reducao.

## Frequencia recente

Para `N` sessoes:

```text
F_recente(t,N) = soma dos eventos do comportamento X nas ultimas N sessoes
F_media(t,N) = F_recente(t,N) / N
```

O valor padrao e `N = 5`, configuravel no request.

## Risco recente exponencial

O risco suavizado usa:

```text
R_t = alpha * y_t + (1 - alpha) * R_t-1
```

O valor padrao e `alpha = 0.3`, configuravel no request.

## Baseline historico

Antes dos modelos supervisionados, o sistema calcula:

```text
p_baseline = numero de sessoes com comportamento X / numero total de sessoes avaliadas
```

Esse valor aparece nas respostas de treino e previsao para comparacao clinica.

## Modelos disponiveis

### Baseline historico

Retorna sempre a taxa historica de ocorrencia.

### Regressao logistica

Modelo supervisionado principal do MVP:

```text
p = 1 / (1 + exp(-z))
z = beta_0 + beta_1*x_1 + ... + beta_n*x_n
```

Usa:

- imputacao de valores ausentes;
- padronizacao de numericas;
- one-hot encoding para categoricas;
- `class_weight="balanced"` para desbalanceamento.

### Random Forest

Segundo modelo supervisionado:

```text
p = media das probabilidades estimadas pelas arvores
```

Usa `predict_proba` para retornar a probabilidade da classe positiva.

### XGBoost

Se `xgboost` estiver instalado, a aplicacao adiciona `XGBClassifier` com objetivo:

```text
binary:logistic
```

Forma conceitual:

```text
F_M(x) = F_0(x) + eta * soma(f_m(x))
p = 1 / (1 + exp(-F_M(x)))
```

Se nao estiver instalado, a API informa que o suporte esta preparado, mas inativo.

### Modelo de contagem experimental

Estrutura conceitual para Poisson:

```text
Y ~ Poisson(lambda)
log(lambda) = beta_0 + soma(beta_j*x_j)
lambda = exp(beta_0 + soma(beta_j*x_j))
P(Y >= 1) = 1 - exp(-lambda)
```

No MVP, esse modulo fica como apoio experimental.

## Classificacao de risco

Limiar padrao:

- risco baixo: `p < 0.30`;
- risco moderado: `0.30 <= p < 0.70`;
- risco alto: `p >= 0.70`.

Os limiares podem ser configurados por variaveis de ambiente.

## Avaliacao do modelo

A API calcula:

- accuracy;
- precision;
- recall;
- F1-score;
- ROC-AUC, quando aplicavel;
- PR-AUC;
- matriz de confusao;
- Brier Score.

O Brier Score avalia a qualidade probabilistica:

```text
BS = media((p_i - y_i)^2)
```

Quanto menor o Brier Score, melhor a calibracao probabilistica.

## Calibracao

A calibracao usa `CalibratedClassifierCV` com metodo `sigmoid` quando ha amostra suficiente. Quando a amostra e pequena para validacao cruzada, o sistema usa Platt scaling em holdout temporal:

```text
p_calibrado = sigmoid(a * p_bruto + b)
```

Quando ha volume maior de dados, o metodo `isotonic` tambem fica preparado para uso.

O objetivo clinico e que, quando o modelo aponta risco de 70% para casos semelhantes, aproximadamente 70% desses casos apresentem ocorrencia real.

## Explicabilidade

Para regressao logistica, a API expoe coeficientes beta.

Para Random Forest e XGBoost, a API expoe importancias de features.

Quando SHAP estiver disponivel em etapa futura, a explicacao local podera seguir:

```text
f(x) = phi_0 + soma(phi_j)
```

A resposta de previsao tambem traduz os principais fatores para linguagem clinica, por exemplo: frequencia recente, media movel, intensidade recente, ambiente, antecedente e sessoes desde a ultima ocorrencia.

## Prevencao de vazamento

O pipeline usa divisao temporal:

- treino: sessoes mais antigas;
- teste: sessoes mais recentes.

As features sao calculadas com `shift(1)`, medias moveis passadas e contagens historicas anteriores a janela-alvo.

## Endpoints

- `POST /api/ml/features`
- `POST /api/ml/train`
- `POST /api/ml/predict`
- `GET /api/ml/metrics`
- `GET /api/ml/calibration`
- `GET /api/ml/feature-importance`
- `GET /api/predictions/history`

## Interpretacao

Use a previsao como sinal analitico:

- Compare `probability` com `baseline_probability`.
- Observe o risco clinico.
- Leia os fatores associados.
- Verifique metricas e Brier Score antes de confiar na probabilidade.
- Revise drift, governanca e LGPD antes de uso real.

## Limitacoes

- O MVP depende da qualidade, consistencia e granularidade dos registros da bHave.
- Timestamps incompletos limitam janelas em minutos.
- Poucos dados reduzem a estabilidade de calibracao e importancia.
- O modelo nao substitui avaliacao funcional, julgamento clinico nem decisao do analista do comportamento.

## Proximos passos

- Ativar XGBoost quando a dependencia for desejada.
- Evoluir modelo de contagem Poisson/Binomial Negativa.
- Avaliar sobrevivencia para tempo ate proxima ocorrencia.
- Avaliar processos de Hawkes para eventos autocorrelacionados.
- Adicionar SHAP para explicabilidade local.
- Expandir validacao por paciente, periodo, ambiente e terapeuta.
