# ABC fechado no Supabase

Esta pasta contem SQL pronto para criar o modulo de ABC fechado no Supabase.

## Como usar

1. Abra o projeto no Supabase.
2. Va em `SQL Editor`.
3. Rode primeiro `001_create_abc_fechado.sql`.
4. Rode depois `002_seed_abc_categorias.sql`.
5. Se as tabelas ja existiam antes do campo de ambiente, rode `003_add_ambiente.sql`.
6. Rode `004_create_action_logs.sql` para manter o historico de inclusoes e remocoes.
7. Rode `005_create_temporal_chains.sql` para habilitar cadeias entre episódios.
8. Confirme no `Table Editor` que apareceram:
   - `abc_instrument_versions`
   - `abc_sessions`
   - `abc_intervals`
   - `abc_categories`
   - `abc_interval_events`
   - `abc_action_logs`
   - `abc_chain_configs`
   - `abc_transition_rules`
   - `abc_chain_candidates`
   - `abc_chain_stats`
   - `abc_chain_review_logs`

O backend iniciado por `iniciar_sellas.bat` tambem valida esse esquema e aplica
as criacoes idempotentes automaticamente usando `SELLAS_DATABASE_URL`.

## Regra clinica central

O ABC fechado separa:

- registro do intervalo;
- analise descritiva de associacoes;
- predicao temporal futura.

As associacoes nao confirmam causa ou funcao comportamental.

A cadeia temporal usa `B_n -> C_n => A_(n+1) -> B_(n+1)`, bloqueia o mesmo
intervalo e sessões distintas por padrão e exige regra versionada para códigos
diferentes. O resultado continua sendo hipótese descritiva, sujeita a revisão humana.

## Valores booleanos

- `true`: evento observado.
- `false`: ausencia confirmada em intervalo observado.
- `null`: desconhecido ou impossivel determinar.
- `not_observed`: intervalo fora do denominador.
- `not_applicable`: categoria nao aplicavel.

## Observacao sobre RLS

O arquivo cria tabelas com RLS habilitado. As policies permissivas ficam comentadas no final do SQL. Para producao, crie policies por organizacao/servico/paciente antes de liberar uso real.
