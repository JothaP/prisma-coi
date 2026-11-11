-- Continuidade automática de acompanhamento: somente acréscimo inequívoco
-- de O.S. à MESMA concentração. Nunca altera ficha, movimentações ou vínculo
-- anterior. Uma nova chave aponta para a mesma ficha e fica auditada.
CREATE OR REPLACE FUNCTION public.farol_acomp_continuar_auto_tx(
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
  v_ja_existente text;
  v_inserida text;
  v_criado text;
  v_data_anterior timestamp without time zone;
  v_contexto jsonb;
  v_lat1 double precision;
  v_lon1 double precision;
  v_lat2 double precision;
  v_lon2 double precision;
  v_ang double precision;
  v_distancia double precision;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.prisma_perfis
    WHERE id = p_autor AND status='ativo' AND perfil IN ('admin','usuario')
  ) THEN
    RAISE EXCEPTION 'Usuário sem permissão para continuidade automática';
  END IF;
  IF p_contexto IS NULL OR jsonb_typeof(p_contexto) <> 'object'
     OR coalesce(p_chave_anterior,'') !~ '^[a-f0-9]{32}$'
     OR coalesce(p_evento_id,'') = '' THEN
    RAISE EXCEPTION 'Dados insuficientes para continuidade automática';
  END IF;
  v_chave := btrim(coalesce(p_contexto->>'chave',''));
  v_municipio := btrim(coalesce(p_contexto->>'municipio',''));
  IF v_chave !~ '^[a-f0-9]{32}$'
     OR v_chave=p_chave_anterior OR v_municipio=''
     OR btrim(coalesce(p_contexto->>'os_origem',''))='' THEN
    RAISE EXCEPTION 'Chave, município ou O.S. da concentração inválidos';
  END IF;

  SELECT * INTO v_antigo FROM public.farol_acomp_vinculos
  WHERE chave=p_chave_anterior AND evento_id=p_evento_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Vínculo original ausente ou alterado';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.farol_acomp_fichas
    WHERE id=p_evento_id AND coalesce(finalizado,'') <> 'Sim'
      AND coalesce(status,'') NOT ILIKE 'Conclu%'
  ) THEN
    RAISE EXCEPTION 'Ficha concluída ou indisponível não pode ser continuada automaticamente';
  END IF;
  IF regexp_replace(
       translate(upper(v_antigo.municipio), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC'),
       '[^A-Z0-9]', '', 'g'
     ) <> regexp_replace(
       translate(upper(v_municipio), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC'),
       '[^A-Z0-9]', '', 'g'
     ) THEN
    RAISE EXCEPTION 'Municípios diferentes não permitem continuidade';
  END IF;

  SELECT array_agg(DISTINCT btrim(x)) INTO v_os_antigas
  FROM unnest(string_to_array(v_antigo.os_origem,';')) AS x
  WHERE btrim(x)<>'';
  SELECT array_agg(DISTINCT btrim(x)) INTO v_os_novas
  FROM unnest(string_to_array(p_contexto->>'os_origem',';')) AS x
  WHERE btrim(x)<>'';
  IF coalesce(cardinality(v_os_antigas),0)<3
     OR coalesce(cardinality(v_os_novas),0)<=coalesce(cardinality(v_os_antigas),0)
     OR cardinality(v_os_novas)>2*cardinality(v_os_antigas)
     OR NOT(v_os_antigas <@ v_os_novas) THEN
    RAISE EXCEPTION 'Acréscimo de O.S. não é inequívoco; revisão manual necessária';
  END IF;
  IF v_antigo.criado_em !~ '^[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}$' THEN
    RAISE EXCEPTION 'Data do vínculo anterior inválida';
  END IF;
  v_data_anterior := to_timestamp(v_antigo.criado_em, 'DD/MM/YYYY HH24:MI')::timestamp;
  IF v_data_anterior > (clock_timestamp() AT TIME ZONE 'America/Fortaleza') + interval '10 minutes'
     OR v_data_anterior < (clock_timestamp() AT TIME ZONE 'America/Fortaleza') - interval '8 days' THEN
    RAISE EXCEPTION 'Vínculo antigo demais para continuidade automática';
  END IF;

  -- Coordenadas válidas e deslocamento máximo de 750 m entre os centros.
  BEGIN
    v_lat1 := v_antigo.latitude::double precision;
    v_lon1 := v_antigo.longitude::double precision;
    v_lat2 := (p_contexto->>'latitude')::double precision;
    v_lon2 := (p_contexto->>'longitude')::double precision;
  EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
    RAISE EXCEPTION 'Coordenadas indisponíveis para validação da continuidade';
  END;
  IF NOT (
    v_lat1 BETWEEN -90 AND 90 AND v_lat2 BETWEEN -90 AND 90
    AND v_lon1 BETWEEN -180 AND 180 AND v_lon2 BETWEEN -180 AND 180
  ) THEN
    RAISE EXCEPTION 'Coordenadas inválidas para continuidade';
  END IF;
  v_ang :=
      sin(radians(v_lat1))*sin(radians(v_lat2))
      + cos(radians(v_lat1))*cos(radians(v_lat2))
        * cos(radians(v_lon1-v_lon2));
  v_distancia := 6371000*acos(least(1.0, greatest(-1.0, v_ang)));
  IF v_distancia IS NULL OR v_distancia>750 THEN
    RAISE EXCEPTION 'Centros distintos; revisão manual necessária';
  END IF;

  SELECT evento_id INTO v_ja_existente
  FROM public.farol_acomp_vinculos WHERE chave=v_chave;
  IF FOUND THEN
    IF v_ja_existente = p_evento_id THEN
      RETURN jsonb_build_object('id',p_evento_id,'chave',v_chave,
                                'ja_vinculado',true);
    END IF;
    RAISE EXCEPTION 'Esta concentração já pertence a outra ficha';
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
    SELECT evento_id INTO v_ja_existente
    FROM public.farol_acomp_vinculos WHERE chave=v_chave;
    IF v_ja_existente=p_evento_id THEN
      RETURN jsonb_build_object('id',p_evento_id,'chave',v_chave,
                                'ja_vinculado',true);
    END IF;
    RAISE EXCEPTION 'Concentração modificada em outra sessão';
  END IF;

  INSERT INTO public.prisma_auditoria
    (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES
    (p_autor,'alterar','Continuidade automática de acompanhamento do Farol: novo conjunto de O.S.; vínculo anterior e histórico preservados',
     '3','ALTERACAO','SUCESSO',p_evento_id);

  RETURN jsonb_build_object(
    'id',p_evento_id,'chave',v_chave,'ja_vinculado',false,
    'os_anteriores',cardinality(v_os_antigas),
    'os_atuais',cardinality(v_os_novas)
  );
END;
$func$;

REVOKE ALL ON FUNCTION public.farol_acomp_continuar_auto_tx(text,text,jsonb,uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_continuar_auto_tx(text,text,jsonb,uuid)
  TO service_role;
