-- Compatibilidade para bancos que ja executaram 001_create_abc_fechado.sql.

alter table if exists public.abc_sessions
  add column if not exists ambiente varchar(160) not null default 'Nao informado';

comment on column public.abc_sessions.ambiente is
  'Ambiente informado no registro ABC fechado, como sala de terapia, sala de aula ou casa.';
