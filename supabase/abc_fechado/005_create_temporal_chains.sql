-- Cadeias temporais B_n -> C_n => A_(n+1) -> B_(n+1).
-- Execute depois de 001-004. O SQL e idempotente para o uso no Supabase.

alter table if exists public.abc_interval_events
  add column if not exists onset_ts timestamptz,
  add column if not exists offset_ts timestamptz;

alter table if exists public.abc_intervals
  add column if not exists indice_intervalo integer;

alter table if exists public.abc_intervals
  drop constraint if exists chk_abc_intervals_status;
alter table if exists public.abc_intervals
  add constraint chk_abc_intervals_status check (
    status_observacao in ('observed', 'partial', 'not_observed', 'not_applicable', 'invalid')
  );

create table if not exists public.abc_chain_configs (
  id uuid primary key default gen_random_uuid(),
  config_version varchar(30) not null unique,
  config jsonb not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

insert into public.abc_chain_configs (config_version, config, active)
values (
  '1',
  '{"interval_minutes":5,"max_lag_seconds":300,"min_confidence":0.9,"require_observed_end":true,"allow_cross_session_chain":false,"include_same_interval_transition":false,"break_on_not_observed":true,"minimum_valid_intervals":20,"chain_min_repetitions":3,"use_transition_ontology":true,"legacy_contiguous_sessions":true}'::jsonb,
  true
)
on conflict (config_version) do nothing;

create table if not exists public.abc_transition_rules (
  id uuid primary key default gen_random_uuid(),
  from_consequence_code varchar(80) not null,
  to_antecedent_code varchar(80) not null,
  relation_type varchar(30) not null default 'mapped',
  active boolean not null default true,
  rule_version varchar(30) not null default '1',
  rationale text,
  created_at timestamptz not null default now(),
  unique (from_consequence_code, to_antecedent_code, rule_version),
  constraint chk_abc_transition_relation check (relation_type in ('exact', 'mapped', 'clinical_review'))
);

create table if not exists public.abc_chain_candidates (
  id uuid primary key default gen_random_uuid(),
  patient_token uuid not null,
  from_interval_id uuid not null references public.abc_intervals(id) on delete cascade,
  to_interval_id uuid not null references public.abc_intervals(id) on delete cascade,
  from_behavior_event_id uuid references public.abc_interval_events(id) on delete set null,
  from_consequence_event_id uuid references public.abc_interval_events(id) on delete set null,
  to_antecedent_event_id uuid references public.abc_interval_events(id) on delete set null,
  to_behavior_event_id uuid references public.abc_interval_events(id) on delete set null,
  from_consequence_code varchar(80),
  to_antecedent_code varchar(80),
  next_behavior_code varchar(80),
  origin_behavior_code varchar(80),
  delta_seconds integer not null,
  same_session boolean not null default false,
  session_relation varchar(40) not null default 'same_session',
  chain_confidence numeric(5,4),
  validation_status varchar(20) not null default 'candidate',
  rejection_reason varchar(120),
  rule_type varchar(30),
  rule_version varchar(30),
  config_version varchar(30) not null,
  origin_end_ts timestamptz not null,
  destination_start_ts timestamptz not null,
  completed_at timestamptz not null,
  environment varchar(160),
  reviewed_by varchar(160),
  reviewed_at timestamptz,
  review_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (from_interval_id, to_interval_id, from_consequence_code, to_antecedent_code),
  constraint chk_abc_chain_status check (validation_status in ('candidate', 'accepted', 'rejected', 'censored')),
  constraint chk_abc_chain_distinct_interval check (from_interval_id <> to_interval_id)
);

create index if not exists ix_abc_chain_candidates_patient_time
  on public.abc_chain_candidates (patient_token, completed_at);
create index if not exists ix_abc_chain_candidates_status
  on public.abc_chain_candidates (validation_status);

create table if not exists public.abc_chain_stats (
  id uuid primary key default gen_random_uuid(),
  patient_token uuid not null,
  from_consequence_code varchar(80) not null,
  to_antecedent_code varchar(80) not null,
  next_behavior_code varchar(80) not null,
  n_exposures integer not null,
  n_transitions integer not null,
  n_chain_behavior integer not null,
  p_transition numeric(10,6),
  p_behavior_given_chain numeric(10,6),
  baseline_probability numeric(10,6),
  difference_in_risk numeric(10,6),
  lift numeric(12,6),
  odds_ratio numeric(14,6),
  risk_ratio numeric(14,6),
  phi numeric(10,6),
  ci_low numeric(10,6),
  ci_high numeric(10,6),
  fisher_exact_pvalue numeric(12,8),
  stability_score numeric(10,6),
  evidence_quality varchar(40),
  insufficient_sample boolean not null default true,
  config_version varchar(30) not null,
  updated_at timestamptz not null default now(),
  unique (patient_token, from_consequence_code, to_antecedent_code, next_behavior_code, config_version)
);

create table if not exists public.abc_chain_review_logs (
  id uuid primary key default gen_random_uuid(),
  chain_candidate_id uuid not null references public.abc_chain_candidates(id) on delete cascade,
  previous_status varchar(20) not null,
  new_status varchar(20) not null,
  reviewed_by varchar(160) not null,
  review_note text,
  reviewed_at timestamptz not null default now()
);

update public.abc_interval_events e
set onset_ts = coalesce(e.onset_ts, i.inicio),
    offset_ts = coalesce(e.offset_ts, i.inicio)
from public.abc_intervals i
where i.id = e.intervalo_id and e.ocorreu is true;

alter table public.abc_chain_configs enable row level security;
alter table public.abc_transition_rules enable row level security;
alter table public.abc_chain_candidates enable row level security;
alter table public.abc_chain_stats enable row level security;
alter table public.abc_chain_review_logs enable row level security;

comment on table public.abc_chain_candidates is
  'Hipoteses descritivas temporais; nao confirmam causa ou funcao comportamental.';
