# Fórmulas e contratos do relatório ABC v2

## Proporção frequencista

Para `k` ocorrências em `n` episódios comparáveis:

`p = k / n`

O intervalo de Wilson é calculado somente para essa proporção binomial bruta. O relatório mostra `k`, `n`, método, nível, estimativa e limites. As validações exigem `k` e `n` inteiros, `n > 0`, `0 <= k <= n` e `0 <= limite inferior <= p <= limite superior <= 1`.

## Posterior Beta

Com prior `Beta(alpha, beta)`:

- posterior: `Beta(k + alpha, n - k + beta)`;
- média posterior: `(k + alpha) / (n + alpha + beta)`;
- intervalo de credibilidade: quantis da posterior Beta.

O intervalo bayesiano nunca é chamado de intervalo de confiança.

## Cadeias ABC

No mesmo ambiente `E`, período e unidade:

- `P(A|E) = n(A,E) / n(E)`;
- `P(B|A,E) = n(A,B,E) / n(A,E)`;
- `P(C|A,B,E) = n(A,B,C,E) / n(A,B,E)`;
- `P(A,B,C|E) = n(A,B,C,E) / n(E)`.

A identidade fatorada só é válida quando os denominadores são compatíveis e não há duplicidade:

`P(A,B,C|E) = P(A|E) * P(B|A,E) * P(C|A,B,E)`

Lift é associação e retorna nulo quando o denominador é zero ou o suporte configurado é insuficiente.

## Gravidade e risco exploratório

Pesos versionados padrão:

- `peso(C1) = 0,20`;
- `peso(C2) = 1,00`.

`peso_medio_gravidade = soma(pesos classificados) / quantidade_classificada`

`risco_exploratorio = frequencia_observada * peso_medio_gravidade`

Esses pesos não são probabilidades clínicas nem escala validada. Não classificados aparecem separadamente.

## Transições temporais

`gap = inicio_evento_seguinte - fim_evento_anterior`

Uma transição aceita exige paciente igual, eventos distintos, timestamps válidos, `gap >= 0`, gap máximo configurado, regra de sessão/ambiente explícita e ausência de duplicação.

`transition_id = SHA256(patient_id, previous_event_id, next_event_id, rule_version)`

Estabilidade exige simultaneamente número mínimo de ocorrências, sessões e dias, além de dispersão fora de uma única sessão.

## Métricas preditivas

Quando o modo preditivo é válido, são produzidos Brier Score, Log Loss, PR-AUC, ROC-AUC secundária, sensibilidade, especificidade, VPP, VPN, matriz de confusão, intercepto e inclinação de calibração, ECE e curva de calibração. O modelo é comparado com a prevalência histórica do treino.
