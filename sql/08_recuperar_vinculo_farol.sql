-- PRISMA / Farol: acrescenta novo vínculo para ficha EXISTENTE, sem substituir
-- a chave anterior, sem alterar ficha ou movimentações, com auditoria.
-- Usado somente após confirmação explícita no painel do Módulo 3.
CREATE OR REPLACE FUNCTION public.farol_acomp_recuperar_vinculo_tx(
  p_chave_anterior text, p_evento_id text, p_contexto jsonb, p_autor uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $func$
DECLARE
  v_antigo public.farol_acomp_vinculos%ROWTYPE;
  v_chave text;
  v_municipio text;
  v_os_novas text[];
  v_os_antigas text[];
  v_compartilhadas int;
  v_existente text;
  v_inserida text;
  v_contexto jsonb;
  v_criado text;
BEGIN
  -- Serviço usa credencial privada e repassa o UUID do analista autenticado.
  IF NOT EXISTS (
    SELECT 1 FROM public.prisma_perfis
    WHERE id = p_autor AND status = 'ativo' AND perfil IN ('admin','usuario')
  ) THEN
    RAISE EXCEPTION 'Usuário sem perfil ativo para recuperar vínculo';
  END IF;

  IF p_contexto IS NULL OR jsonb_typeof(p_contexto) <> 'object'
     OR p_chave_anterior IS NULL OR p_chave_anterior !~ '^[a-f0-9]{32}$'
     OR coalesce(p_evento_id,'') = '' THEN
    RAISE EXCEPTION 'Contexto de recuperação inválido';
  END IF;

  v_chave := btrim(coalesce(p_contexto->>'chave',''));
  v_municipio := btrim(coalesce(p_contexto->>'municipio',''));
  IF v_chave !~ '^[a-f0-9]{32}$'
     OR v_chave = p_chave_anterior OR v_municipio = ''
     OR btrim(coalesce(p_contexto->>'os_origem','')) = '' THEN
    RAISE EXCEPTION 'Concentração sem chave nova, município ou O.S.';
  END IF;

  -- Bloqueia fonte no momento da reavaliação; nunca faz DELETE nem UPDATE.
  SELECT * INTO v_antigo FROM public.farol_acomp_vinculos
  WHERE chave = p_chave_anterior AND evento_id = p_evento_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Vínculo anterior não encontrado. Atualize a página.';
  END IF;

  IF regexp_replace(
        translate(upper(v_antigo.municipio),
                  'ÁÀÂÃÉÊÍÓÔÕÚÜÇ','AAAAEEIOOOUUC'),
        '[^A-Z0-9]', '', 'g'
     ) <> regexp_replace(
        translate(upper(v_municipio),
                  'ÁÀÂÃÉÊÍÓÔÕÚÜÇ','AAAAEEIOOOUUC'),
        '[^A-Z0-9]', '', 'g'
     ) THEN
    RAISE EXCEPTION 'Recuperação entre municípios diferentes é proibida';
  END IF;

  SELECT array_agg(DISTINCT btrim(os)) INTO v_os_antigas
  FROM unnest(string_to_array(v_antigo.os_origem,';')) os
  WHERE btrim(os) <> '';
  SELECT array_agg(DISTINCT btrim(os)) INTO v_os_novas
  FROM unnest(string_to_array(p_contexto->>'os_origem',';')) os
  WHERE btrim(os) <> '';
  IF coalesce(cardinality(v_os_antigas),0) < 2
     OR coalesce(cardinality(v_os_novas),0) < 2 THEN
    RAISE EXCEPTION 'Vínculo sem O.S. suficientes para reconciliação segura';
  END IF;

  SELECT count(*) INTO v_compartilhadas
  FROM unnest(v_os_antigas) os WHERE os = ANY(v_os_novas);
  IF v_compartilhadas < 2
     OR v_compartilhadas * 2 < cardinality(v_os_antigas)
     OR v_compartilhadas * 2 < cardinality(v_os_novas) THEN
    RAISE EXCEPTION 'Sobreposição de O.S. insuficiente para recuperação segura';
  END IF;

  SELECT evento_id INTO v_existente
  FROM public.farol_acomp_vinculos WHERE chave = v_chave;
  IF FOUND THEN
    IF v_existente = p_evento_id THEN
      RETURN jsonb_build_object('id',p_evento_id,'chave',v_chave,'ja_vinculado',true);
    END IF;
    RAISE EXCEPTION 'Nova concentração já vinculada a outra ficha';
  END IF;

  v_criado := to_char(clock_timestamp() AT TIME ZONE 'America/Fortaleza',
                      'DD/MM/YYYY HH24:MI');
  v_contexto := p_contexto || jsonb_build_object('criado_em',v_criado);
  INSERT INTO public.farol_acomp_vinculos
    (chave,evento_id,municipio,bairros,latitude,longitude,os_origem,
     criado_em,origem_hash)
  VALUES
    (v_chave,p_evento_id,v_municipio,
     coalesce(p_contexto->>'bairros',''),
     coalesce(p_contexto->>'latitude',''),
     coalesce(p_contexto->>'longitude',''),
     array_to_string(v_os_novas,';'),v_criado,
     encode(sha256(convert_to(v_contexto::text || ':' || p_evento_id,'UTF8')),'hex'))
  ON CONFLICT (chave) DO NOTHING
  RETURNING chave INTO v_inserida;

  IF v_inserida IS NULL THEN
    SELECT evento_id INTO v_existente
    FROM public.farol_acomp_vinculos WHERE chave = v_chave;
    IF v_existente = p_evento_id THEN
      RETURN jsonb_build_object('id',p_evento_id,'chave',v_chave,'ja_vinculado',true);
    END IF;
    RAISE EXCEPTION 'Vínculo alterado por outra sessão. Atualize a página.';
  END IF;

  INSERT INTO public.prisma_auditoria
    (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES
    (p_autor,'alterar',
     'Recuperação confirmada de acompanhamento do Farol; chave anterior preservada',
     '3','ALTERACAO','SUCESSO',p_evento_id);
  RETURN jsonb_build_object('id',p_evento_id,'chave',v_chave,'ja_vinculado',false,
                            'os_em_comum',v_compartilhadas);
END;
$func$;

REVOKE ALL ON FUNCTION public.farol_acomp_recuperar_vinculo_tx(
  text,text,jsonb,uuid
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_recuperar_vinculo_tx(
  text,text,jsonb,uuid
) TO service_role;
