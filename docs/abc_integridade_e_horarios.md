# Integridade dos registros ABC e visualização por horário

Os ABCs inseridos manualmente no Sellas constituem uma fonte independente. As consultas, os gráficos, as respostas calculadas e os relatórios utilizam o mesmo recorte de datas, ambiente, comportamento e classificação.

## Horários e navegação

A primeira aba, **Horários**, agrupa episódios em faixas de uma hora por padrão, como **08:00 às 08:59** e **09:00 às 09:59**. O destaque informa a faixa com mais registros, o total, o percentual e os dias que sustentam a contagem. Empates recebem o mesmo destaque. É possível ordenar as barras pelo horário ou pela quantidade e consultar comportamentos, ambientes e contagens por dia dentro de uma faixa.

As demais abas são **Evolução**, **Contextos**, **Cadeias**, **Registros** e **Relatórios**. As subdivisões e tabelas com altura limitada reduzem a rolagem. Um filtro sem resultados mantém o cadastro e o histórico disponíveis. Os filtros também oferecem faixas de 30 e 15 minutos.

Os gráficos contam episódios. A quantidade de respostas de cada episódio aparece separadamente. Os percentuais usam todos os episódios do recorte; registros sem horário são informados separadamente e não viram acontecimentos à meia-noite. Faixas sem registros não comprovam ausência de comportamento.

## Gravação e qualidade

- Reenvios com o mesmo identificador são tratados de forma idempotente.
- Possíveis duplicações exigem conferência explícita antes de uma nova inclusão semelhante.
- A precisão do horário, a referência da folha, o observador, a frequência e a duração medida são preservados.
- A política de considerar classificações não informadas como C1 mantém a origem dessa atribuição auditável. Isso não fabrica critérios negativos de lesão.
- Episódios individuais não criam tempo de observação artificial. Taxas por hora dependem de janelas de observação completas e explícitas.
- Cadeias temporais precisam ter seus dois episódios dentro do recorte analisado. Instantes isolados de episódios não são apresentados como medidas do intervalo entre o fim de C e o início de A.
- O contexto enviado à análise assistida usa campos permitidos, sem referências de folhas, observadores ou identificações de atendimentos.

## Migração e verificação

A migração `f6a7b8c9d0e1_abc_episode_integrity.py` acrescenta metadados dos intervalos e controle de reenvios sem remover os registros existentes. Revisar e aplicar migrações no ambiente de destino faz parte da atualização da instalação.

Os testes cobrem limites das faixas, horário de Brasília, empates, precisão desconhecida, contagem de episódios versus respostas, filtros, abas, rotas e relatórios. Os testes PostgreSQL são opcionais e utilizam esquemas temporários isolados quando `SELLAS_TEST_POSTGRES_URL` está configurada. A auditoria reutilizável em `scripts/verify_abc_patients.py` limita a leitura à transação atual.

Dados clínicos, exportações, cópias de segurança e relatórios internos de pacientes não fazem parte desta publicação.
