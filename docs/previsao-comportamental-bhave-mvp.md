# Previsao comportamental bHave - MVP

## Resumo da implementacao

Foi criada uma aplicacao MVP para estimar a probabilidade de ocorrencia de um comportamento-problema na proxima sessao. A solucao inclui backend FastAPI, frontend React/Vite, modo mock com dados clinicamente plausiveis, normalizacao, anonimização, armazenamento local, treinamento de modelos, previsao, explicabilidade simples, historico, relatorio JSON e logs de auditoria.

## Arquitetura criada

- `backend/app/main.py`: API FastAPI e endpoints do MVP.
- `backend/app/services/bhave_client.py`: adapter isolado para futura API real da bHave.
- `backend/app/services/normalizer.py`: schema canonico e mapeamento de campos.
- `backend/app/services/anonymizer.py`: pseudonimizacao de paciente e terapeuta.
- `backend/app/services/mock_data.py`: dados simulados para desenvolvimento.
- `backend/app/services/storage.py`: persistencia local em SQLite via SQLAlchemy, com JSON de apoio.
- `backend/app/services/audit.py`: trilha de auditoria sem PII direta.
- `backend/app/ml/features.py`: engenharia de atributos temporais e contextuais.
- `backend/app/ml/training.py`: baseline, regressao logistica, Random Forest, metricas e previsao.
- `frontend/src/App.tsx`: dashboard operacional.
- `backend/app/ml/model_store.py`: persistencia versionada de artefatos e metadata.
- `backend/app/ml/validation.py`: validacao temporal, por paciente e por periodo.
- `backend/app/ml/drift.py`: estatisticas de referencia e monitoramento de drift.
- `backend/app/services/governance.py`: checklist LGPD, governanca clinica e uso regulado.

## Endpoints disponiveis

- `POST /api/config/bhave`: registra configuracao sem expor token.
- `GET /api/config/bhave/contract`: valida configuracao do adapter real da bHave.
- `POST /api/sync/bhave`: sincroniza API real ou gera dados mock.
- `GET /api/patients`: lista pacientes anonimizados.
- `GET /api/patients/{patient_id}/behaviors`: lista comportamentos do paciente.
- `GET /api/patients/{patient_id}/history`: retorna historico filtravel.
- `POST /api/ml/train`: treina os modelos para paciente/comportamento.
- `POST /api/ml/predict`: estima risco para proxima sessao.
- `GET /api/ml/metrics`: retorna metricas do ultimo treino.
- `GET /api/ml/models`: lista modelos versionados.
- `GET /api/ml/drift`: compara dados atuais com o baseline do modelo versionado.
- `GET /api/predictions/history`: retorna historico de previsoes.
- `GET /api/reports/export`: gera relatorio estruturado.
- `GET /api/governance/lgpd`: retorna checklist LGPD/governanca/regulatorio.

## Como o pipeline de machine learning funciona

1. Recebe registros comportamentais.
2. Normaliza campos variaveis da API para um schema interno.
3. Pseudonimiza paciente e terapeuta.
4. Filtra paciente e comportamento-alvo.
5. Ordena sessoes temporalmente.
6. Cria atributos de frequencia recente, media movel, duracao, intensidade, horario, dia da semana, ambiente, antecedente, consequencia, funcao, terapeuta, estrategias, prompt, independencia, sessoes desde ultima ocorrencia e tendencia.
7. Define o alvo como ocorrencia do comportamento na proxima sessao.
8. Treina baseline historico, regressao logistica e Random Forest.
9. Avalia accuracy, precision, recall, F1, ROC-AUC, matriz de confusao e Brier score.
10. Calibra probabilidades por Platt scaling em holdout temporal quando ha duas classes suficientes.
11. Persiste o modelo selecionado em `storage/models` com metadata JSON, versao e indice.
12. Gera probabilidade, faixa de risco e fatores associados em linguagem legivel.
13. Monitora drift numerico e categorico comparando dados atuais com as estatisticas de referencia do treino.

## Limitacoes do MVP

- O adapter da bHave agora e configuravel por endpoint, cabecalho de autenticacao, chave de payload e paginacao, mas ainda precisa ser validado contra a documentacao oficial final.
- A explicabilidade e propositalmente simples; SHAP ainda nao foi integrado.
- A calibracao explicita ja existe, mas depende de dados suficientes no holdout de calibracao.
- O registro de modelo agora e persistido localmente, mas ainda nao ha registry externo ou aprovacao clinica de versao.
- A validacao temporal, por paciente e por periodo foi adicionada, mas uso real ainda exige validacao prospectiva silenciosa.
- O endpoint LGPD/governanca bloqueia uso de producao por padrao ate revisoes juridica, clinica e regulatoria.

## Proximos passos tecnicos

- Confirmar contrato oficial da API bHave, paginacao, autenticacao e limites em ambiente real.
- Criar migrations formais para banco de dados.
- Migrar artefatos versionados locais para registry governado.
- Comparar Platt scaling atual com calibracao isotonic/CalibratedClassifierCV em bases maiores.
- Adicionar SHAP ou Permutation Importance.
- Criar exportacao PDF/DOCX de relatorio.
- Implementar controle de acesso, perfis e auditoria imutavel.
- Monitorar drift e degradacao de performance.

## Pontos dependentes da API real da bHave

- Nomes definitivos dos campos.
- Semantica de sessoes sem ocorrencia.
- Granularidade temporal real dos eventos.
- Disponibilidade de antecedentes, consequencias e funcao hipotetizada.
- Qualidade e consistencia de intensidade, duracao e frequencia.
- Paginacao, filtros por data, limites de taxa e erros.
- Escopo de permissao do token e politica de dados sensiveis.
