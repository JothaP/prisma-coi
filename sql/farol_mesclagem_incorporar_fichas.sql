-- PRISMA / Farol Operacional — incorporar acompanhamentos de um mesmo evento
-- A ficha mais antiga permanece; fichas recentes e movimentações são preservadas.
-- Aplicado no Supabase. Script de referência para reprodução controlada.
CREATE TABLE IF NOT EXISTS public.farol_acomp_incorporacoes (
  ficha_incorporada text PRIMARY KEY REFERENCES public.farol_acomp_fichas(id) ON DELETE RESTRICT,
  ficha_principal text NOT NULL REFERENCES public.farol_acomp_fichas(id) ON DELETE RESTRICT,
  evento_chave text NOT NULL,
  os_recebidas jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(os_recebidas)='array'),
  incorporado_por uuid REFERENCES public.prisma_perfis(id) ON DELETE SET NULL,
  incorporado_em timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT farol_incorporacao_fichas_distintas CHECK (ficha_incorporada<>ficha_principal)
);
ALTER TABLE public.farol_acomp_incorporacoes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.farol_acomp_incorporacoes FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON TABLE public.farol_acomp_incorporacoes TO service_role;

CREATE OR REPLACE FUNCTION public.farol_incorporar_mescla_tx(p_evento_chave text, p_municipio text, p_evento_rotulo text, p_codigo text, p_chave_acomp text, p_codigos jsonb, p_os jsonb, p_poligono jsonb, p_chaves_origem text[], p_autor uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
DECLARE
  v_fichas text[];
  v_principal text;
  v_recente text;
  v_os jsonb;
  v_codigos jsonb;
  v_os_anteriores jsonb;
  v_os_novas jsonb;
  v_resultado jsonb;
  v_criado text;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.prisma_perfis
     WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario'))
  THEN RAISE EXCEPTION 'Usuário sem perfil para mesclagem'; END IF;
  IF p_os IS NULL OR jsonb_typeof(p_os)<>'array'
      OR p_codigos IS NULL OR jsonb_typeof(p_codigos)<>'array'
      OR coalesce(p_evento_chave,'') !~ '^[0-9a-f]{32}$'
      OR coalesce(p_chave_acomp,'') !~ '^[0-9a-f]{32}$'
  THEN RAISE EXCEPTION 'Parâmetros inválidos para mesclagem'; END IF;

  PERFORM pg_advisory_xact_lock(hashtext(p_evento_chave));
  SELECT coalesce(os_origem,'[]'::jsonb),coalesce(codigos_origem,'[]'::jsonb)
  INTO v_os_anteriores,v_codigos
  FROM public.farol_mesclas_operacionais
  WHERE evento_chave=p_evento_chave AND ativo FOR UPDATE;
  v_os_anteriores:=coalesce(v_os_anteriores,'[]'::jsonb);
  v_codigos:=coalesce(v_codigos,'[]'::jsonb);

  SELECT coalesce(jsonb_agg(x ORDER BY x),'[]'::jsonb) INTO v_os
  FROM (
    SELECT DISTINCT trim(value) x FROM jsonb_array_elements_text(p_os || v_os_anteriores)
    WHERE trim(value)<>''
  ) s;
  SELECT coalesce(jsonb_agg(x ORDER BY x),'[]'::jsonb) INTO v_codigos
  FROM (
    SELECT DISTINCT trim(value) x FROM jsonb_array_elements_text(p_codigos || v_codigos)
    WHERE trim(value)<>''
  ) s;
  SELECT coalesce(jsonb_agg(x ORDER BY x),'[]'::jsonb) INTO v_os_novas
  FROM (
    SELECT value AS x FROM jsonb_array_elements_text(v_os)
    EXCEPT SELECT value AS x FROM jsonb_array_elements_text(v_os_anteriores)
  ) s;
  IF jsonb_array_length(v_os)<6 OR jsonb_array_length(v_codigos)<2 THEN
    RAISE EXCEPTION 'Mesclagem exige pelo menos duas concentrações e seis O.S.';
  END IF;
  -- Nunca unir eventos de diferentes municípios nem roubar O.S. de outro evento.
  IF EXISTS (
    SELECT 1 FROM public.farol_acomp_vinculos
    WHERE chave=ANY(coalesce(p_chaves_origem,ARRAY[]::text[]))
      AND upper(trim(municipio))<>upper(trim(p_municipio))
  ) THEN RAISE EXCEPTION 'Ficha de outro município: mesclagem bloqueada'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.farol_mesclas_operacionais m,
        jsonb_array_elements_text(m.os_origem) x
    WHERE m.ativo AND m.evento_chave<>p_evento_chave
      AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_os) os WHERE os.value=x.value)
  ) THEN RAISE EXCEPTION 'O.S. já pertence a outra mesclagem ativa'; END IF;

  SELECT array_agg(DISTINCT evento_id) INTO v_fichas
  FROM public.farol_acomp_vinculos
  WHERE chave=ANY(coalesce(p_chaves_origem,ARRAY[]::text[]))
     OR chave=p_chave_acomp;
  IF coalesce(cardinality(v_fichas),0)>0 THEN
    IF (SELECT count(*) FROM public.farol_acomp_fichas WHERE id=ANY(v_fichas))
       <>cardinality(v_fichas)
    THEN RAISE EXCEPTION 'Ficha antiga não encontrada'; END IF;
    SELECT f.id INTO v_principal
    FROM public.farol_acomp_fichas f WHERE f.id=ANY(v_fichas)
    ORDER BY CASE WHEN f.data_identificacao ~ '^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$'
              THEN to_timestamp(f.data_identificacao,'DD/MM/YYYY HH24:MI')
              ELSE f.importado_em END ASC NULLS LAST,
             CASE WHEN f.id ~ '^[0-9]+$' THEN f.id::numeric END ASC NULLS LAST,
             f.importado_em ASC, f.id ASC LIMIT 1;
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.farol_acomp_incorporacoes
    WHERE ficha_incorporada=ANY(coalesce(v_fichas,ARRAY[]::text[]))
       AND ficha_principal IS DISTINCT FROM v_principal
  ) THEN RAISE EXCEPTION 'Ficha previamente incorporada em outro acompanhamento'; END IF;

  -- A ficha mais recente deixa de ter vínculos ativos; ficha e movimentações
  -- permanecem no histórico, com mapeamento reversível e auditável.
  FOR v_recente IN
    SELECT DISTINCT id FROM unnest(coalesce(v_fichas,ARRAY[]::text[])) id
    WHERE id<>v_principal
  LOOP
    IF EXISTS (
      SELECT 1 FROM public.farol_acomp_vinculos
      WHERE evento_id=v_recente AND upper(trim(municipio))<>upper(trim(p_municipio))
    ) THEN RAISE EXCEPTION 'Ficha recente também possui vínculos em outro município'; END IF;
    INSERT INTO public.farol_acomp_incorporacoes
      (ficha_incorporada,ficha_principal,evento_chave,os_recebidas,incorporado_por)
    VALUES (v_recente,v_principal,p_evento_chave,v_os_novas,p_autor)
    ON CONFLICT(ficha_incorporada) DO NOTHING;
    IF NOT EXISTS (
      SELECT 1 FROM public.farol_acomp_incorporacoes
      WHERE ficha_incorporada=v_recente AND ficha_principal=v_principal
    ) THEN RAISE EXCEPTION 'Ficha incorporada em outro acompanhamento'; END IF;
    UPDATE public.farol_acomp_vinculos SET evento_id=v_principal WHERE evento_id=v_recente;
    INSERT INTO public.prisma_auditoria
      (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
    VALUES (p_autor,'alterar',
      'Ficha recente #'||v_recente||' incorporada na principal #'||v_principal||
      '; O.S. transferidas sem apagar ficha ou movimentações originais',
      '3','ALTERACAO','SUCESSO',p_evento_chave);
  END LOOP;

  -- Operação já existente valida os limites e atualiza a mesclagem.
  v_resultado:=public.farol_salvar_mescla_tx(
    p_evento_chave,p_municipio,p_evento_rotulo,p_codigo,p_chave_acomp,
    v_codigos,v_os,p_poligono,p_chaves_origem,p_autor
  );
  IF v_principal IS NOT NULL THEN
    IF coalesce(v_resultado->>'evento_id','')<>v_principal THEN
      RAISE EXCEPTION 'Inconsistência na ficha principal: transação cancelada';
    END IF;
    v_criado:=to_char(clock_timestamp() AT TIME ZONE 'America/Fortaleza','DD/MM/YYYY HH24:MI');
    UPDATE public.farol_acomp_vinculos
    SET os_origem=(SELECT string_agg(value,';' ORDER BY value)
                   FROM jsonb_array_elements_text(v_os))
    WHERE chave=p_chave_acomp AND evento_id=v_principal;
    UPDATE public.farol_acomp_fichas
    SET qtd_reclamacoes=jsonb_array_length(v_os)::text,
        ultima_atualizacao=v_criado,versao=versao+1
    WHERE id=v_principal AND qtd_reclamacoes IS DISTINCT FROM jsonb_array_length(v_os)::text;
  END IF;
  RETURN v_resultado || jsonb_build_object(
    'qtd_os_acrescentadas',jsonb_array_length(v_os_novas),
    'ficha_principal',v_principal,
    'fichas_incorporadas',to_jsonb(coalesce(
      ARRAY(SELECT id FROM unnest(coalesce(v_fichas,ARRAY[]::text[])) id
            WHERE id<>v_principal),
      ARRAY[]::text[]
    ))
  );
END;
$function$
;

REVOKE ALL ON FUNCTION public.farol_incorporar_mescla_tx(text,text,text,text,text,jsonb,jsonb,jsonb,text[],uuid)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_incorporar_mescla_tx(text,text,text,text,text,jsonb,jsonb,jsonb,text[],uuid)
  TO service_role;
