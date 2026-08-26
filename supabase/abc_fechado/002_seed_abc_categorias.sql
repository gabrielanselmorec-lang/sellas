-- Seeds iniciais do ABC fechado.
-- Rode depois de 001_create_abc_fechado.sql.

insert into public.abc_instrument_versions (codigo, nome, versao, ativo, metadados)
values
  ('ABC_FECHADO', 'Instrumento ABC fechado por intervalos', '1', true, '{"interval_minutes": 5}'::jsonb)
on conflict (codigo, versao) do update
set nome = excluded.nome,
    ativo = excluded.ativo,
    metadados = excluded.metadados;

insert into public.abc_categories (codigo, nome, tipo, definicao_operacional, versao, ativa)
values
  ('PEDIDO_NEGADO', 'Pedido negado', 'antecedente', 'Acesso, item ou pedido negado antes do comportamento.', 1, true),
  ('DEMANDA', 'Demanda apresentada', 'antecedente', 'Instrução, tarefa ou solicitação apresentada ao paciente.', 1, true),
  ('TRANSICAO', 'Transição', 'antecedente', 'Mudança de ambiente, atividade ou rotina.', 1, true),
  ('ESPERA', 'Espera', 'antecedente', 'Período de espera antes de acesso, atividade ou instrução.', 1, true),
  ('AGRESSAO_FISICA', 'Agressão física', 'comportamento', 'Contato físico agressivo observável, como bater, chutar, morder ou empurrar.', 1, true),
  ('CHORO', 'Choro', 'comportamento', 'Choro audível ou visível durante o intervalo observado.', 1, true),
  ('FUGA_ESQUIVA', 'Fuga ou esquiva', 'comportamento', 'Tentativa observável de sair, evitar ou interromper demanda/atividade.', 1, true),
  ('GRITO', 'Grito', 'comportamento', 'Vocalização alta ou grito observável no intervalo.', 1, true),
  ('PAUSA', 'Pausa', 'consequencia', 'Interrupção temporária da demanda ou atividade após o comportamento.', 1, true),
  ('ATENCAO', 'Atenção social', 'consequencia', 'Atenção social apresentada após o comportamento.', 1, true),
  ('REDIRECIONAMENTO', 'Redirecionamento', 'consequencia', 'Redirecionamento verbal, gestual ou físico após o comportamento.', 1, true),
  ('ACESSO_ITEM', 'Acesso a item', 'consequencia', 'Acesso a item, brinquedo ou atividade apos o comportamento.', 1, true)
on conflict (codigo, versao) do update
set nome = excluded.nome,
    tipo = excluded.tipo,
    definicao_operacional = excluded.definicao_operacional,
    ativa = excluded.ativa;
