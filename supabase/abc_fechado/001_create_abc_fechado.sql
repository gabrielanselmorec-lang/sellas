-- ABC fechado por intervalos para Supabase/Postgres.
-- Rode este arquivo no SQL Editor do Supabase antes dos seeds.

create extension if not exists pgcrypto;

create table if not exists public.abc_instrument_versions (
  id uuid primary key default gen_random_uuid(),
  codigo varchar(80) not null,
  nome varchar(200) not null,
  versao varchar(30) not null default '1',
  ativo boolean not null default true,
  metadados jsonb not null default '{}'::jsonb,
  criado_em timestamptz not null default now(),
  unique (codigo, versao)
);

create table if not exists public.abc_sessions (
  id uuid primary key default gen_random_uuid(),
  patient_token uuid not null,
  service_id uuid,
  data_inicio timestamptz not null,
  data_fim timestamptz not null,
  timezone varchar(80) not null default 'America/Sao_Paulo',
  ambiente varchar(160) not null default 'Nao informado',
  observacao_completa boolean not null default false,
  instrumento_versao varchar(30) not null default '1',
  criado_em timestamptz not null default now(),
  constraint chk_abc_sessions_periodo check (data_fim > data_inicio)
);

create index if not exists ix_abc_sessions_patient_inicio
  on public.abc_sessions (patient_token, data_inicio);

create table if not exists public.abc_intervals (
  id uuid primary key default gen_random_uuid(),
  sessao_id uuid not null references public.abc_sessions(id) on delete cascade,
  inicio timestamptz not null,
  fim timestamptz not null,
  timezone varchar(80) not null default 'America/Sao_Paulo',
  duracao_planejada_minutos integer not null default 5,
  status_observacao varchar(30) not null default 'observed',
  atraso_registro_segundos integer,
  observador_token uuid,
  instrumento_versao varchar(30) not null default '1',
  criado_em timestamptz not null default now(),
  constraint uq_abc_intervals_sessao_inicio unique (sessao_id, inicio),
  constraint chk_abc_intervals_periodo check (fim > inicio),
  constraint chk_abc_intervals_duracao check (duracao_planejada_minutos > 0),
  constraint chk_abc_intervals_status check (
    status_observacao in ('observed', 'not_observed', 'not_applicable', 'invalid')
  )
);

create index if not exists ix_abc_intervals_inicio
  on public.abc_intervals (inicio);

create table if not exists public.abc_categories (
  id uuid primary key default gen_random_uuid(),
  codigo varchar(80) not null,
  nome varchar(200) not null,
  tipo varchar(20) not null,
  definicao_operacional text,
  versao integer not null default 1,
  ativa boolean not null default true,
  service_id uuid,
  organization_id uuid,
  criado_em timestamptz not null default now(),
  constraint uq_abc_categories_codigo_versao unique (codigo, versao),
  constraint chk_abc_categories_tipo check (tipo in ('antecedente', 'comportamento', 'consequencia'))
);

create index if not exists ix_abc_categories_tipo_ativa
  on public.abc_categories (tipo, ativa);

create table if not exists public.abc_interval_events (
  id uuid primary key default gen_random_uuid(),
  intervalo_id uuid not null references public.abc_intervals(id) on delete cascade,
  categoria_id uuid not null references public.abc_categories(id),
  ocorreu boolean,
  frequencia integer,
  duracao_segundos integer,
  intensidade smallint,
  confianca_registro numeric(4,3),
  fonte varchar(40) not null default 'registro_fechado',
  revisado_humano boolean not null default false,
  criado_em timestamptz not null default now(),
  constraint uq_abc_event_intervalo_categoria unique (intervalo_id, categoria_id),
  constraint chk_abc_event_frequencia check (frequencia is null or frequencia >= 0),
  constraint chk_abc_event_duracao check (duracao_segundos is null or duracao_segundos >= 0),
  constraint chk_abc_event_intensidade check (intensidade is null or intensidade between 0 and 5),
  constraint chk_abc_event_confianca check (confianca_registro is null or confianca_registro between 0 and 1)
);

create index if not exists ix_abc_events_intervalo
  on public.abc_interval_events (intervalo_id);

create table if not exists public.abc_action_logs (
  id uuid primary key default gen_random_uuid(),
  patient_token uuid not null,
  acao varchar(40) not null,
  intervalo_id uuid,
  categoria_id uuid,
  snapshot jsonb not null default '{}'::jsonb,
  criado_em timestamptz not null default now(),
  constraint chk_abc_action_logs_acao check (
    acao in ('registro_adicionado', 'registro_removido', 'categoria_criada')
  )
);

create index if not exists ix_abc_action_logs_patient_time
  on public.abc_action_logs (patient_token, criado_em);

alter table public.abc_instrument_versions enable row level security;
alter table public.abc_sessions enable row level security;
alter table public.abc_intervals enable row level security;
alter table public.abc_categories enable row level security;
alter table public.abc_interval_events enable row level security;
alter table public.abc_action_logs enable row level security;

-- Desenvolvimento local apenas, se quiser liberar leitura/escrita autenticada rapidamente:
-- create policy "abc_authenticated_all_instruments" on public.abc_instrument_versions for all to authenticated using (true) with check (true);
-- create policy "abc_authenticated_all_sessions" on public.abc_sessions for all to authenticated using (true) with check (true);
-- create policy "abc_authenticated_all_intervals" on public.abc_intervals for all to authenticated using (true) with check (true);
-- create policy "abc_authenticated_all_categories" on public.abc_categories for all to authenticated using (true) with check (true);
-- create policy "abc_authenticated_all_events" on public.abc_interval_events for all to authenticated using (true) with check (true);
-- create policy "abc_authenticated_all_action_logs" on public.abc_action_logs for all to authenticated using (true) with check (true);
