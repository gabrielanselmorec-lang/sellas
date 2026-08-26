-- Metodologia ABC v2: normalização auditável e transições determinísticas.
create extension if not exists pgcrypto;

alter table public.abc_categories add column if not exists nome_original varchar(200);
alter table public.abc_categories add column if not exists nome_normalizado varchar(200);
alter table public.abc_categories add column if not exists regra_normalizacao varchar(160);
alter table public.abc_categories add column if not exists versao_normalizacao varchar(40);

alter table public.abc_interval_events add column if not exists funcao_hipotese_original varchar(120);
alter table public.abc_interval_events add column if not exists funcao_hipotese_normalizada varchar(120);
alter table public.abc_interval_events add column if not exists versao_normalizacao varchar(40);

update public.abc_categories
set nome_original = coalesce(nome_original, nome),
    nome_normalizado = case
      when lower(trim(nome)) in ('manejo fisíco', 'manejo fisico') then 'Manejo físico'
      when lower(trim(nome)) = 'do nada' then 'Antecedente não identificado no registro'
      else regexp_replace(trim(nome), '\s+', ' ', 'g')
    end,
    regra_normalizacao = case
      when lower(trim(nome)) in ('manejo fisíco', 'manejo fisico') then 'known_alias:consequencia:manejo fisico'
      when lower(trim(nome)) = 'do nada' then 'known_alias:antecedente:do nada'
      else 'trim_whitespace'
    end,
    versao_normalizacao = 'abc-normalization-v1'
where versao_normalizacao is null;

update public.abc_interval_events
set funcao_hipotese_original = coalesce(funcao_hipotese_original, funcao_hipotese),
    funcao_hipotese_normalizada = case
      when funcao_hipotese is null or trim(funcao_hipotese) = '' then 'Não identificada'
      when lower(trim(funcao_hipotese)) in ('fuga ou esquiva', 'fuga/esquiva') then 'Fuga/esquiva'
      else regexp_replace(trim(funcao_hipotese), '\s+', ' ', 'g')
    end,
    versao_normalizacao = 'abc-normalization-v1'
where versao_normalizacao is null;

alter table public.abc_chain_candidates add column if not exists transition_id varchar(64);
update public.abc_chain_candidates
set transition_id = encode(
  digest(
    patient_token::text || E'\x1f' ||
    coalesce(from_behavior_event_id::text, from_interval_id::text) || E'\x1f' ||
    coalesce(to_behavior_event_id::text, to_interval_id::text) || E'\x1f' ||
    coalesce(rule_version, config_version),
    'sha256'
  ), 'hex'
)
where transition_id is null;

create unique index if not exists ix_abc_chain_candidates_transition_id
  on public.abc_chain_candidates (transition_id);

alter table public.abc_chain_candidates alter column transition_id set not null;
