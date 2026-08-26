-- Auditoria append-only para inclusoes, remocoes e categorias personalizadas.

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

alter table public.abc_action_logs enable row level security;
