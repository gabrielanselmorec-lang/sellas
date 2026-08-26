# Resumo clinico imprimivel do ABC fechado

## Finalidade

O resumo organiza os registros ABC fechados para revisao clinica, previa na
tela e exportacao em PDF. Ele descreve frequencias, associacoes e sequencias
temporais. Nenhum indicador confirma causalidade, funcao comportamental ou
efeito de uma intervencao.

## Fluxo

1. O usuario seleciona paciente, ambiente, periodo e escopo das cadeias.
2. O Streamlit solicita `GET /api/abc/reports/summary`.
3. A API filtra intervalos e eventos e calcula um unico payload auditavel.
4. A previa apresenta os principais resultados e alertas de qualidade.
5. O PDF usa o mesmo payload; nao existe uma segunda logica de calculo.
6. A geracao registra somente metadados de auditoria.

## Interface

A secao `Resumo ABC para impressao` oferece:

- selecao de periodo;
- uma cadeia A-B-C ou todas as cadeias do recorte;
- cadeias temporais revisadas e candidatas ou somente revisadas;
- inclusao de finais de semana;
- inclusao de graficos;
- explicacao do ABC fechado;
- limitacoes clinicas;
- ocultacao da identificacao do paciente;
- `Visualizar resumo`;
- `Imprimir resumo ABC`;
- download do Word e `Salvar como PDF`.

## Contrato da API

Endpoint: `GET /api/abc/reports/summary`.

Parametros:

| Parametro | Tipo | Regra |
|---|---|---|
| `paciente` | texto | Paciente existente no banco clinico |
| `data_inicio` | data | Inicio inclusivo do recorte |
| `data_fim` | data | Fim inclusivo do recorte |
| `ambiente` | texto opcional | Restringe os intervalos ao ambiente |
| `incluir_finais_semana` | booleano | Inclui sabado e domingo |
| `incluir_cadeias_candidatas` | booleano | Inclui candidatas ainda nao rejeitadas |
| `apenas_cadeias_revisadas` | booleano | Mantem somente cadeias aceitas |
| `incluir_graficos` | booleano | Registra a escolha de apresentacao |
| `anonimizar_paciente` | booleano | Substitui o nome por identificador generico |
| `gerado_por` | texto | Responsavel informado para auditoria |
| `formato` | enum | `preview`, `pdf` ou `docx` |

Estrutura principal da resposta:

```json
{
  "report_metadata": {},
  "patient": {},
  "observation_summary": {},
  "top_antecedents": [],
  "top_behaviors": [],
  "top_consequences": [],
  "antecedent_behavior_associations": [],
  "behavior_consequence_associations": [],
  "behavior_chains": [],
  "data_quality": {},
  "descriptive_summary": "",
  "clinical_disclaimer": ""
}
```

## Dicionario de dados do resumo

| Campo | Significado |
|---|---|
| `sessions` | Sessoes distintas presentes nos intervalos filtrados |
| `total_records` | Eventos ABC completos incluidos no recorte |
| `behavior_occurrences` | Registros com comportamento preenchido |
| `total_intervals` | Intervalos distintos em qualquer estado |
| `observed_intervals` | Intervalos efetivamente observados |
| `not_observed_intervals` | Intervalos sem observacao valida |
| `invalid_intervals` | Intervalos excluidos por inconsistencia |
| `coverage` | `observed_intervals / total_intervals`, limitado a 100% |
| `occurrence_intervals` | Intervalos observados com pelo menos um evento |
| `occurrence_percentage` | `occurrence_intervals / observed_intervals` |
| `missing_percentage` | Proporcao de intervalos nao observados, invalidos ou nao aplicaveis |
| `probabilidade_condicional` | Frequencia de B quando A esteve presente |
| `probabilidade_baseline` | Frequencia geral de B no recorte observado |
| `diferenca_risco` | Probabilidade condicional menos baseline |
| `lift` | Probabilidade condicional dividida pelo baseline |
| `repeticoes` | Elos temporais agrupados na mesma cadeia |
| `sessoes` | Sessoes distintas em que a cadeia temporal apareceu |
| `confianca_media` | Media das confiancas registradas para os elos |
| `status` | Candidata ou revisada e aceita |

## Regras analiticas

- O denominador de ocorrencia usa somente intervalos `observed`.
- Mais de um evento no mesmo intervalo nao eleva o percentual acima de 100%.
- `null`, `not_observed`, `not_applicable` e `invalid` nunca viram `false`.
- Cadeias temporais sao agrupadas por `B_n -> C_n => A_(n+1) -> B_(n+1)`.
- Cadeias rejeitadas e censuradas nao sao apresentadas como achados clinicos.
- O texto automatico e deterministico e deriva das metricas do payload.
- A probabilidade e uma frequencia contextual observada, nao uma garantia para
  um episodio individual.

## Conteudo do PDF

O PDF A4 possui fundo branco, rodape com numeracao, tabelas repetiveis e
graficos rasterizados. As secoes incluem identificacao autorizada, periodo,
resumo descritivo, explicacao A-B-C, estados do intervalo, cobertura,
frequencias, linha temporal, perigo C1/C2, associacoes A-B e B-C, cadeias do
mesmo registro, cadeias temporais, guia de indicadores, formulas e limitacoes.

O CSS de tela tambem possui `@media print` para esconder navegacao e controles,
repetir cabecalhos e evitar cortes internos em tabelas e graficos.

## Auditoria e seguranca

A tabela `abc_report_audit_logs` armazena paciente tokenizado, responsavel,
filtros, formato, anonimizacao e versao da logica. O conteudo clinico integral
nao e gravado no log tecnico.

A API atual valida a existencia do paciente. O backend clinico local ainda nao
possui autenticacao com identidade e papeis por usuario; portanto, o bloqueio
por permissao individual deve ser implementado antes de disponibilizacao em
rede ou uso real. A anonimizacao reduz exposicao no artefato, mas nao substitui
controle de acesso, consentimento, politica de retencao e revisao LGPD.

## Limitacoes clinicas

Associacao nao e causalidade. Cadeia temporal nao demonstra reforco nem funcao.
O relatorio nao deve ser usado isoladamente para prescrever intervencoes,
restringir oportunidades ou atribuir responsabilidade. Os resultados precisam
de revisao por profissional qualificado e confronto com observacao direta,
definicao operacional, concordancia entre observadores e avaliacao funcional.
