# Extracao de anotacoes comportamentais da bHave

## Objetivo

Adicionar as anotacoes clinicas/comportamentais dos atendimentos como fonte de dados para a previsao comportamental. O fluxo esperado e:

```text
Paciente -> Atendimentos -> Data -> Anotacoes -> Extracao -> Features -> Modelo preditivo
```

A extracao automatica e uma camada de apoio. Casos ambiguos devem ser revisados por humano antes de uso clinico real.

## Schema interno

As anotacoes sao adaptadas para um schema interno com campos como:

- `patient_id`;
- `appointment_id`;
- `appointment_date`;
- `raw_note_text`;
- `cleaned_note_text`;
- `extracted_behaviors`;
- `binary_occurrences`;
- `extracted_antecedents`;
- `extracted_consequences`;
- `extracted_intensity`;
- `extracted_frequency`;
- `extracted_duration`;
- `extracted_context`;
- `extraction_confidence`;
- `extraction_method`;
- `requires_human_review`;
- `human_confirmed`;
- `human_corrected`.

O adaptador aceita variacoes de campos como `notes`, `clinical_notes`, `behavioral_notes`, `session_summary`, `evolution_notes`, `appointment_id`, `session_id`, `professional_id` e `therapist_id`.

## Normalizacao de texto

A pipeline inicial usa regras:

1. converter para lowercase;
2. remover acentos;
3. remover pontuacao irregular;
4. preservar numeros;
5. remover espacos duplicados.

Exemplo:

```text
"Não houve CRISE!" -> "nao houve crise"
```

## Identificacao de comportamentos

O modulo `backend/app/services/note_extraction.py` contem um dicionario configuravel de comportamentos e sinonimos, incluindo:

- agressao;
- autolesao;
- fuga;
- esquiva;
- choro;
- grito;
- birra/crise/desregulacao;
- jogar objetos;
- destruicao;
- oposicao;
- estereotipia;
- comportamento-problema.

## Negacao

A extracao procura termos de negacao antes do comportamento:

- `nao apresentou`;
- `nao houve`;
- `nao ocorreram`;
- `sem`;
- `ausencia de`;
- `antes de apresentar`.

Exemplo:

```text
Nao apresentou agressao nem autolesao.
```

Resultado:

```json
{
  "behavior_agressao_occurred": 0,
  "behavior_autolesao_occurred": 0
}
```

Ausencias gerais, como `sem intercorrencias` ou `sem comportamento-problema`, geram:

```json
{
  "general_problem_behavior_occurred": 0
}
```

## Incerteza e revisao humana

Termos como `possivel`, `pareceu`, `quase`, `tentou` e `inicio de` reduzem a confianca e marcam `requires_human_review = true`.

Exemplo:

```text
Pareceu iniciar uma crise, mas foi redirecionado antes de apresentar agressao.
```

Resultado esperado:

- crise possivel;
- agressao negada;
- redirecionamento extraido;
- revisao humana obrigatoria.

## Frequencia

A extracao reconhece numeros e numeros por extenso:

- `3 vezes` -> `3`;
- `duas vezes` -> `2`;
- `varias vezes` -> `multiple`;
- `muitas vezes` -> `high_frequency`.

## Intensidade

Termos de intensidade sao mapeados para escala ordinal:

- leve/baixo: `1`;
- moderado: `2`;
- intenso/intensa/intensamente/grave/severo/forte/alto: `3`.

## Duracao

Padroes suportados:

- `por 5 minutos`;
- `durou 10 min`;
- `aproximadamente 15 minutos`;
- `poucos segundos`;
- `toda a sessao`.

Quando possivel, o resultado e salvo em minutos.

## Antecedentes

Tags extraidas:

- `demanda`;
- `negacao_acesso`;
- `retirada_item`;
- `transicao`;
- `espera`;
- `mudanca_rotina`;
- `frustracao`;
- `barulho`;
- `correcao`;
- `redirecionamento`.

## Consequencias

Tags extraidas:

- `atencao`;
- `fuga_demanda`;
- `acesso_item`;
- `pausa`;
- `redirecionamento`;
- `bloqueio`;
- `ajuda_fisica`;
- `ajuda_verbal`;
- `demanda_mantida`.

## Contexto

Tags extraidas:

- sala;
- escola;
- casa;
- clinica;
- mesa/atividade de mesa;
- banheiro;
- refeicao;
- transicao;
- grupo;
- individual;
- atividade academica;
- brincadeira;
- atividade motora;
- chegada/inicio da sessao;
- saida/fim da sessao.

## Transformacao em features

As extracoes geram features como:

- `occurrence_from_note`;
- `note_extracted_frequency`;
- `note_extracted_intensity`;
- `note_extracted_duration`;
- `note_antecedent_demanda`;
- `note_antecedent_transicao`;
- `note_antecedent_negacao_acesso`;
- `note_consequence_atencao`;
- `note_consequence_pausa`;
- `note_context_inicio_sessao`;
- `note_context_fim_sessao`;
- `note_context_atividade_mesa`;
- `note_extraction_confidence`;
- `note_requires_human_review`.

Essas features entram em `X_t` junto dos registros estruturados.

## Prioridade dos dados

A prioridade aplicada e:

1. correcao humana confirmada;
2. registro estruturado da bHave;
3. extracao automatica da anotacao;
4. unknown/null quando nao houver evidencia suficiente.

Se houver `human_corrected = true`, a transformacao em features usa a correcao humana.

## Prevencao de vazamento

Para prever a proxima sessao, o pipeline usa apenas dados e anotacoes ate a sessao atual. A variavel-alvo continua sendo a ocorrencia futura:

```text
Y_t,h = 1 se o comportamento ocorrer na janela futura h
```

A divisao de treino/teste continua temporal.

## Endpoints

- `GET /api/patients`
- `GET /api/patients/{patient_id}/appointments`
- `GET /api/patients/{patient_id}/appointments?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `GET /api/appointments/{appointment_id}`
- `GET /api/appointments/{appointment_id}/notes`
- `POST /api/appointments/{appointment_id}/notes/extract`
- `POST /api/notes/batch-extract`
- `GET /api/notes/extractions/{appointment_id}`
- `POST /api/notes/extractions/{appointment_id}/confirm`
- `POST /api/ml/features/from-notes`
- `POST /api/ml/features`
- `POST /api/ml/train`
- `POST /api/ml/predict`

## Revisao humana

O endpoint de confirmacao permite salvar:

- comportamento corrigido;
- ocorrencia sim/nao;
- frequencia;
- intensidade;
- duracao;
- antecedentes;
- consequencias;
- contexto;
- profissional que corrigiu.

Correcoes humanas sao persistidas e usadas com prioridade na geracao de features.

## Relatorio

O export em `/api/reports/export` inclui `note_extractions`, permitindo relatar:

- anotacoes analisadas;
- comportamentos extraidos;
- ocorrencias binarias;
- frequencia, intensidade e duracao;
- antecedentes e consequencias;
- confianca;
- revisao humana.

## Limitacoes

- A extracao inicial e baseada em regras.
- Pode haver falsos positivos ou falsos negativos em textos muito complexos.
- Negacoes longas ou frases contraditorias ainda exigem revisao humana.
- O uso clinico real depende de validacao, governanca e revisao profissional.

## Proximos passos

- Tela completa de edicao/correcao com modal.
- Aprendizado supervisionado a partir das correcoes humanas.
- SHAP/NLP para explicabilidade textual.
- Modelos de linguagem clinicamente validados para extracao contextual.
