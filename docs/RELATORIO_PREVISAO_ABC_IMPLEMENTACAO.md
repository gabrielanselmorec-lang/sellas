# Implementação do relatório ABC v3

## Problema e impacto

O relatório anterior apresentava uma proporção de episódios como previsão do próximo registro, combinava fatores posteriores ao alvo e exibia pesos de gravidade como “perigo (%)”. Isso poderia superestimar capacidade preditiva, misturar incertezas incompatíveis e induzir leitura clínica inadequada.

## Solução

- novo núcleo `app/services/abc_methodology.py`, independente da renderização;
- separação explícita entre resultados descritivos e preditivos;
- Wilson para `k/n` bruto e posterior Beta como opção separada;
- regressão logística L2 somente com negativos observáveis e holdout cronológico por sessão;
- bloqueio automático de features posteriores ao alvo e identificadores;
- objeto auditável de qualidade da evidência;
- normalização versionada preservando valores legados oficiais;
- peso médio configurado de gravidade e índice exploratório com rótulos corretos;
- `transition_id` determinístico e deduplicação;
- endpoint atual `/api/abc/estimativa-descritiva` e alias legado depreciado `/api/abc/previsao`;
- relatório reorganizado em onze seções, com cadeias completas no apêndice;
- interface atualizada para a semântica descritiva.

### Complementos da v3 (`fix_recomend_fifi`)

- auditoria verificável de duplicidades, timestamps, sobreposições, taxonomia, observadores e rastreabilidade;
- exposição por hora, por ambiente e por data, sem criar denominadores ausentes;
- intervalo por bootstrap agrupado por sessão;
- baselines múltiplos e backtesting progressivo;
- avaliação operacional por limiar, falsos alertas e eventos perdidos;
- PSI de drift treino-validação e status explícito de generalização desconhecida;
- explicação individual por contribuição em log-odds;
- hash SHA-256 do dataset, parâmetros e execução;
- suporte mínimo no corpo do relatório e rótulos gráficos sem truncamento.

## Arquivos principais

- `app/services/abc_methodology.py`
- `app/services/abc_closed.py`
- `app/services/abc_chains.py`
- `app/services/abc_prediction_report.py`
- `app/web/dashboard.py`
- `api.py`
- `app/config/abc_methodology.json`
- `alembic/versions/e5f6a7b8c9d0_abc_methodology_v2.py`
- `supabase/abc_fechado/006_abc_methodology_v2.sql`
- `tests/test_abc_methodology.py`
- `tools/generate_abc_methodology_example.py`

## Compatibilidade e migração

Os campos `probabilidade_prevista` e `indice_perigo` continuam temporariamente presentes como aliases de compatibilidade, acompanhados de marca de depreciação ou do novo campo canônico. A interface e os relatórios não usam a terminologia legada. O schema preserva valores originais e adiciona colunas normalizadas.

Aplicação da migração:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

## Testes e geração

Testes dirigidos:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_abc_methodology.py tests\test_abc_closed_service.py tests\test_abc_temporal_chains.py tests\test_abc_prediction_report.py -q
```

Suíte completa:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Relatório sintético:

```powershell
.\.venv\Scripts\python.exe tools\generate_abc_methodology_example.py
```

Os artefatos atuais são gravados em `output/abc_methodology_v3/`; a pasta v2 permanece apenas como registro da rodada anterior.

## Comparação anterior x atual

| Antes | Atual |
|---|---|
| “Probabilidade do próximo registro” em base de episódios | Frequência observada entre episódios; risco absoluto bloqueado |
| Escore contextual + Wilson do contexto | `k/n` + Wilson reproduzível, ou posterior Beta separada |
| “Perigo (%)” | Peso médio configurado de gravidade |
| Função predominante próxima da previsão | Hipótese registrada em seção clínica separada |
| Repetições temporais individuais | Transições deduplicadas e agrupadas |
| Estabilidade vaga | Critérios versionados de ocorrências, sessões e dias |
| Valor legado substituído na leitura | Original preservado + normalizado versionado |

## Limitações restantes

- o fluxo legado não contém oportunidades negativas suficientes para promover o modo preditivo;
- calibração e ganho sobre baseline só são apresentados quando uma base de oportunidades observáveis for fornecida;
- validade externa continua não avaliada;
- aliases legados devem ser removidos apenas em futura versão maior com migração anunciada.

## Critérios de aceitação

O código exige numerador/denominador reproduzíveis, não cria negativos artificiais, separa treino/teste por sessão, bloqueia leakage, contabiliza não classificados, preserva valores originais, deduplica transições, trata função como hipótese e versiona metodologia/regras.

Validação final da versão 3 em 20/07/2026: **159 testes aprovados**, sem falhas. Permaneceram 40 avisos de depreciação preexistentes de SQLAlchemy, FastAPI, PyPDF2 e Joblib/NumPy; nenhum deles altera o resultado desta entrega.
