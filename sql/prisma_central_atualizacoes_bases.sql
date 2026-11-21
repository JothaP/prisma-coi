-- Central de Atualização de Bases: snapshots e troca transacional da área
-- temporária de poços. Nenhuma base é substituída nesta migração.
CREATE TABLE IF NOT EXISTS public.prisma_atualizacoes_backups (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tabela text NOT NULL CHECK (tabela = 'prisma_ativos_importacao'),
  registros jsonb NOT NULL CHECK (jsonb_typeof(registros) = 'array'),
  total_registros integer NOT NULL CHECK (total_registros >= 0),
  autor_id uuid REFERENCES public.prisma_perfis(id) ON DELETE SET NULL,
  motivo text NOT NULL CHECK (motivo IN ('substituicao', 'restauracao')),
  criado_em timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.prisma_atualizacoes_backups ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.prisma_atualizacoes_backups FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.prisma_atualizacoes_backups TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.prisma_atualizacoes_backups_id_seq TO service_role;

CREATE OR REPLACE FUNCTION public.prisma_atualizacoes_substituir_pocos_tx(
  p_autor uuid, p_linhas jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = ''
AS $$
DECLARE
  n integer;
  anteriores integer;
  backup_id bigint;
BEGIN
  IF p_autor IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.prisma_perfis
     WHERE id = p_autor AND perfil = 'admin' AND status = 'ativo'
  ) THEN
    RAISE EXCEPTION 'ATUALIZACOES_SEM_PERMISSAO';
  END IF;
  IF jsonb_typeof(p_linhas) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_linhas) NOT BETWEEN 1 AND 10000 THEN
    RAISE EXCEPTION 'ATUALIZACOES_VOLUME_INVALIDO';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_linhas) as item(linha)
    WHERE jsonb_typeof(item.linha) IS DISTINCT FROM 'object'
       OR COALESCE(length(item.linha->>'IDENTIFICACAO_ATIVO'),0) NOT BETWEEN 1 AND 250
       OR COALESCE(length(item.linha->>'MUNICIPIO'),0) > 250
       OR COALESCE(length(item.linha->>'ID_POCO'),0) > 250
       OR COALESCE(length(item.linha->>'NOME_POCO'),0) > 250
       OR COALESCE(length(item.linha->>'LATITUDE'),0) > 32
       OR COALESCE(length(item.linha->>'LONGITUDE'),0) > 32
       OR COALESCE(length(item.linha->>'LATITUDE'),0) = 0
       OR COALESCE(length(item.linha->>'LONGITUDE'),0) = 0
  ) THEN
    RAISE EXCEPTION 'ATUALIZACOES_REGISTRO_INVALIDO';
  END IF;
  SELECT count(*)::int INTO n FROM jsonb_array_elements(p_linhas);
  IF (
    SELECT count(DISTINCT lower(btrim(item.linha->>'IDENTIFICACAO_ATIVO')))
    FROM jsonb_array_elements(p_linhas) as item(linha)
  ) <> n THEN
    RAISE EXCEPTION 'ATUALIZACOES_IDENTIFICADOR_DUPLICADO';
  END IF;

  LOCK TABLE public.prisma_ativos_importacao IN EXCLUSIVE MODE;
  SELECT count(*)::int INTO anteriores FROM public.prisma_ativos_importacao;
  INSERT INTO public.prisma_atualizacoes_backups
    (tabela, registros, total_registros, autor_id, motivo)
  SELECT 'prisma_ativos_importacao',
    COALESCE(jsonb_agg(to_jsonb(t)), '[]'::jsonb), anteriores,
    p_autor, 'substituicao'
  FROM public.prisma_ativos_importacao t
  RETURNING id INTO backup_id;

  DELETE FROM public.prisma_ativos_importacao;
  INSERT INTO public.prisma_ativos_importacao
    ("ID_POCO","IDENTIFICACAO_ATIVO","NOME_POCO","MUNICIPIO","LATITUDE","LONGITUDE")
  SELECT COALESCE(item.linha->>'ID_POCO',''),
         btrim(item.linha->>'IDENTIFICACAO_ATIVO'),
         COALESCE(item.linha->>'NOME_POCO',''),
         COALESCE(item.linha->>'MUNICIPIO',''),
         COALESCE(item.linha->>'LATITUDE',''),
         COALESCE(item.linha->>'LONGITUDE','')
  FROM jsonb_array_elements(p_linhas) as item(linha);

  INSERT INTO public.prisma_auditoria
    (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES (p_autor,'atualizar_base_pocos',
    format('Substituída área de importação de poços; antes=%s; depois=%s; backup=%s',
           anteriores,n,backup_id),
    'gestao','ALTERACAO','SUCESSO',backup_id::text);

  RETURN jsonb_build_object('concluido',true,'antes',anteriores,
                             'depois',n,'backup_id',backup_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.prisma_atualizacoes_restaurar_pocos_tx(
  p_autor uuid, p_backup_id bigint
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = ''
AS $$
DECLARE
  dados jsonb;
  n integer;
  backup_id bigint;
BEGIN
  IF p_autor IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.prisma_perfis
     WHERE id=p_autor AND perfil='admin' AND status='ativo'
  ) THEN
    RAISE EXCEPTION 'ATUALIZACOES_SEM_PERMISSAO';
  END IF;
  SELECT registros INTO dados
  FROM public.prisma_atualizacoes_backups
  WHERE id=p_backup_id AND tabela='prisma_ativos_importacao';
  IF dados IS NULL THEN RAISE EXCEPTION 'ATUALIZACOES_BACKUP_INEXISTENTE'; END IF;
  LOCK TABLE public.prisma_ativos_importacao IN EXCLUSIVE MODE;
  SELECT count(*)::int INTO n FROM public.prisma_ativos_importacao;
  INSERT INTO public.prisma_atualizacoes_backups
    (tabela,registros,total_registros,autor_id,motivo)
  SELECT 'prisma_ativos_importacao',
     COALESCE(jsonb_agg(to_jsonb(t)),'[]'::jsonb),
     n,p_autor,'restauracao'
  FROM public.prisma_ativos_importacao t
  RETURNING id INTO backup_id;

  DELETE FROM public.prisma_ativos_importacao;
  INSERT INTO public.prisma_ativos_importacao
     ("ID_POCO","IDENTIFICACAO_ATIVO","NOME_POCO","MUNICIPIO","LATITUDE","LONGITUDE")
  SELECT COALESCE(item.linha->>'ID_POCO',''),
         COALESCE(item.linha->>'IDENTIFICACAO_ATIVO',''),
         COALESCE(item.linha->>'NOME_POCO',''),
         COALESCE(item.linha->>'MUNICIPIO',''),
         COALESCE(item.linha->>'LATITUDE',''),
         COALESCE(item.linha->>'LONGITUDE','')
  FROM jsonb_array_elements(dados) as item(linha);

  INSERT INTO public.prisma_auditoria
    (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES (p_autor,'restaurar_base_pocos',
    format('Backup restaurado=%s; backup anterior criado=%s',p_backup_id,backup_id),
    'gestao','ALTERACAO','SUCESSO',p_backup_id::text);
  RETURN jsonb_build_object('concluido',true,'restaurado_de',p_backup_id,
     'backup_anterior',backup_id, 'total',jsonb_array_length(dados));
END;
$$;

REVOKE ALL ON FUNCTION public.prisma_atualizacoes_substituir_pocos_tx(uuid,jsonb)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.prisma_atualizacoes_restaurar_pocos_tx(uuid,bigint)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.prisma_atualizacoes_substituir_pocos_tx(uuid,jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.prisma_atualizacoes_restaurar_pocos_tx(uuid,bigint)
  TO service_role;
NOTIFY pgrst, 'reload schema';
