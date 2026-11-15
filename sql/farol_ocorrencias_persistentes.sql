-- PRISMA / Farol M3: ocorrencias persistentes por ficha e evento.
-- As fichas existentes sao as identidades permanentes. Nunca exclui fichas,
-- historicos ou vinculos antigos. Reconciliacao somente no fluxo do Farol.
CREATE TABLE IF NOT EXISTS public.farol_acomp_eventos_persistentes (
    evento_chave text PRIMARY KEY CHECK (evento_chave ~ '^[0-9a-f]{32}$'),
    ficha_id text NOT NULL REFERENCES public.farol_acomp_fichas(id) ON DELETE RESTRICT,
    municipio text NOT NULL CHECK (length(btrim(municipio))>0),
    criado_em timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS farol_acomp_eventos_persistentes_ficha_idx
    ON public.farol_acomp_eventos_persistentes(ficha_id);
ALTER TABLE public.farol_acomp_eventos_persistentes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.farol_acomp_eventos_persistentes FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON TABLE public.farol_acomp_eventos_persistentes TO service_role;

-- Registro em lote: uma requisicao e uma transacao, sem gravacoes por cartao.
-- Motivos: 'evento' = chave exata de evento validado por O.S. e horario pelo
-- servidor Streamlit; 'continuidade' = correspondencia de O.S. revalidada no SQL.
CREATE OR REPLACE FUNCTION public.farol_acomp_sincronizar_ocorrencias_tx(
    p_itens jsonb, p_autor uuid
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path TO ''
AS $func$
DECLARE
    v_evento text;
    v_municipio text;
    v_ficha text;
    v_principal text;
    v_outro text;
    v_alvo text;
    v_item jsonb;
    v_origem text;
    v_chave text;
    v_chave_antiga text;
    v_os text[];
    v_os_antigas text[];
    v_existe text;
    v_num integer := 0;
    v_incorporadas integer := 0;
    v_eventos integer := 0;
    v_eventos_processados integer := 0;
    v_candidatos text[];
    v_tem_outros_eventos boolean;
    v_lat1 double precision;
    v_lon1 double precision;
    v_lat2 double precision;
    v_lon2 double precision;
    v_distancia double precision;
    v_comuns integer;
    v_hora text := to_char(clock_timestamp() AT TIME ZONE 'America/Fortaleza','DD/MM/YYYY HH24:MI');
BEGIN
    IF p_itens IS NULL OR jsonb_typeof(p_itens)<>'array'
       OR jsonb_array_length(p_itens)>500
    THEN RAISE EXCEPTION 'OCORRENCIAS_LOTE_INVALIDO'; END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.prisma_perfis
      WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario')
    ) THEN RAISE EXCEPTION 'OCORRENCIAS_SEM_PERMISSAO'; END IF;
    IF jsonb_array_length(p_itens)=0 THEN
        RETURN jsonb_build_object('vinculos',0,'incorporacoes',0,'eventos',0);
    END IF;
    -- Evita corridas entre usuarios na mesma carga. A transacao inteira
    -- e atomicamente revertida em qualquer conflito.
    PERFORM pg_advisory_xact_lock(hashtext('FAROL_OCORRENCIAS_PERSISTENTES_V1'));

    IF EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_itens) t
      WHERE jsonb_typeof(t)<>'object'
        OR coalesce(t->>'chave','') !~ '^[a-f0-9]{32}$'
        OR coalesce(t->>'ficha_id','')=''
        OR coalesce(t->>'municipio','')=''
        OR jsonb_typeof(t->'os')<>'array'
        OR jsonb_array_length(t->'os')<2
        OR jsonb_array_length(t->'os')>1000
        OR coalesce(t->>'motivo','') NOT IN ('evento','continuidade')
        OR (t->>'motivo'='evento' AND coalesce(t->>'evento_chave','') !~ '^[a-f0-9]{32}$')
        OR (t->>'motivo'='continuidade'
            AND coalesce(t->>'chave_anterior','') !~ '^[a-f0-9]{32}$')
    ) THEN RAISE EXCEPTION 'OCORRENCIAS_DADOS_INVALIDOS'; END IF;
    IF EXISTS (
      SELECT chave FROM (
        SELECT t->>'chave' AS chave, count(DISTINCT t->>'ficha_id') AS fichas
        FROM jsonb_array_elements(p_itens) t GROUP BY t->>'chave'
      ) s WHERE fichas>1
    ) THEN RAISE EXCEPTION 'OCORRENCIAS_CHAVE_AMBIGUA'; END IF;

    -- Primeiro verificar se o id sugerido pertence de fato ao historico
    -- do contexto ou a ancora de evento existente.
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_itens)
    LOOP
      v_ficha:=v_item->>'ficha_id';
      v_chave:=v_item->>'chave';
      v_chave_antiga:=v_item->>'chave_anterior';
      v_evento:=v_item->>'evento_chave';
      IF NOT EXISTS(SELECT 1 FROM public.farol_acomp_fichas WHERE id=v_ficha) THEN
        RAISE EXCEPTION 'OCORRENCIAS_FICHA_INEXISTENTE';
      END IF;
      IF NOT (
        EXISTS(SELECT 1 FROM public.farol_acomp_vinculos
          WHERE evento_id=v_ficha AND chave IN (v_chave,v_chave_antiga))
        OR (v_item->>'motivo'='evento' AND EXISTS(
          SELECT 1 FROM public.farol_acomp_eventos_persistentes
          WHERE evento_chave=v_evento AND ficha_id=v_ficha))
      ) THEN
        RAISE EXCEPTION 'OCORRENCIAS_SEM_EVIDENCIA_PERSISTENTE';
      END IF;
    END LOOP;

    -- Um evento confirmado pode reunir concentracoes distantes. Se ja
    -- existirem fichas distintas, adotar a mais antiga e incorporar as
    -- demais sem excluir os seus registros ou movimentacoes.
    FOR v_evento IN
      SELECT DISTINCT t->>'evento_chave'
      FROM jsonb_array_elements(p_itens) t WHERE t->>'motivo'='evento'
    LOOP
      SELECT count(DISTINCT lower(btrim(t->>'municipio')))
        INTO v_eventos FROM jsonb_array_elements(p_itens) t
        WHERE t->>'evento_chave'=v_evento;
      IF v_eventos<>1 THEN RAISE EXCEPTION 'OCORRENCIAS_EVENTO_CIDADES_DIFERENTES'; END IF;
      v_eventos_processados:=v_eventos_processados+1;
      SELECT btrim(t->>'municipio') INTO v_municipio
        FROM jsonb_array_elements(p_itens) t
        WHERE t->>'evento_chave'=v_evento LIMIT 1;

      SELECT array_agg(DISTINCT id) INTO v_candidatos
      FROM (
        SELECT t->>'ficha_id' AS id FROM jsonb_array_elements(p_itens) t
        WHERE t->>'evento_chave'=v_evento
        UNION
        SELECT ficha_id FROM public.farol_acomp_eventos_persistentes
        WHERE evento_chave=v_evento
      ) fontes;
      SELECT ficha_id INTO v_principal
      FROM public.farol_acomp_eventos_persistentes
      WHERE evento_chave=v_evento FOR UPDATE;
      IF v_principal IS NULL THEN
        SELECT id INTO v_principal
        FROM public.farol_acomp_fichas
        WHERE id=ANY(v_candidatos)
        ORDER BY importado_em ASC, 
          CASE WHEN id ~ '^[0-9]+$' THEN id::numeric END ASC NULLS LAST, id
        LIMIT 1;
      END IF;
      IF v_principal IS NULL THEN RAISE EXCEPTION 'OCORRENCIAS_SEM_FICHA'; END IF;
      -- Nao permitir que uma ficha associada a outro evento seja
      -- absorvida silenciosamente por este.
      IF EXISTS(
        SELECT 1 FROM public.farol_acomp_eventos_persistentes
        WHERE ficha_id=ANY(v_candidatos) AND evento_chave<>v_evento
      ) THEN RAISE EXCEPTION 'OCORRENCIAS_FICHA_COM_OUTRO_EVENTO'; END IF;

      FOR v_outro IN SELECT unnest(v_candidatos)
      LOOP
        IF v_outro=v_principal THEN CONTINUE; END IF;
        IF EXISTS(SELECT 1 FROM public.farol_acomp_incorporacoes
                 WHERE ficha_incorporada=v_outro AND ficha_principal<>v_principal)
        THEN RAISE EXCEPTION 'OCORRENCIAS_INCORPORACAO_CONFLITANTE'; END IF;
        IF EXISTS(SELECT 1 FROM public.farol_acomp_vinculos
                  WHERE evento_id=v_outro AND lower(btrim(municipio))<>lower(btrim(v_municipio)))
        THEN RAISE EXCEPTION 'OCORRENCIAS_INCORPORACAO_OUTRO_MUNICIPIO'; END IF;

        INSERT INTO public.farol_acomp_incorporacoes
          (ficha_incorporada,ficha_principal,evento_chave,os_recebidas,incorporado_por)
        VALUES(v_outro,v_principal,v_evento,'[]'::jsonb,p_autor)
        ON CONFLICT(ficha_incorporada) DO NOTHING;
        IF NOT EXISTS(SELECT 1 FROM public.farol_acomp_incorporacoes
          WHERE ficha_incorporada=v_outro AND ficha_principal=v_principal)
        THEN RAISE EXCEPTION 'OCORRENCIAS_INCORPORACAO_CONFLITANTE'; END IF;

        UPDATE public.farol_acomp_vinculos
           SET evento_id=v_principal WHERE evento_id=v_outro;
        v_incorporadas:=v_incorporadas+1;
      END LOOP;

      INSERT INTO public.farol_acomp_eventos_persistentes
        (evento_chave,ficha_id,municipio)
      VALUES(v_evento,v_principal,v_municipio)
      ON CONFLICT(evento_chave) DO NOTHING;
      SELECT ficha_id INTO v_alvo FROM public.farol_acomp_eventos_persistentes
      WHERE evento_chave=v_evento;
      IF v_alvo<>v_principal THEN RAISE EXCEPTION 'OCORRENCIAS_EVENTO_CONCORRENTE'; END IF;
    END LOOP;

    -- Persistir apenas novas composicoes de O.S.; nao reescrever as antigas.
    FOR v_item IN SELECT value FROM jsonb_array_elements(p_itens)
    LOOP
      v_chave:=v_item->>'chave';
      v_ficha:=v_item->>'ficha_id';
      v_origem:=v_item->>'motivo';
      v_chave_antiga:=v_item->>'chave_anterior';
      v_evento:=v_item->>'evento_chave';
      v_municipio:=btrim(v_item->>'municipio');
      IF v_origem='evento' THEN
        SELECT ficha_id INTO v_ficha FROM public.farol_acomp_eventos_persistentes
        WHERE evento_chave=v_evento;
        IF v_ficha IS NULL THEN RAISE EXCEPTION 'OCORRENCIAS_EVENTO_SEM_ANCORA'; END IF;
      ELSE
        SELECT evento_id INTO v_existe FROM public.farol_acomp_vinculos
          WHERE chave=v_chave_antiga AND evento_id=v_ficha;
        IF NOT FOUND THEN RAISE EXCEPTION 'OCORRENCIAS_CONTINUIDADE_NAO_LOCALIZADA'; END IF;
        SELECT array_agg(DISTINCT btrim(value)) INTO v_os_antigas
        FROM unnest(string_to_array((
           SELECT os_origem FROM public.farol_acomp_vinculos
           WHERE chave=v_chave_antiga
        ),';')) AS value WHERE btrim(value)<>'';
        SELECT array_agg(DISTINCT btrim(value)) INTO v_os
        FROM jsonb_array_elements_text(v_item->'os') AS value WHERE btrim(value)<>'';
        SELECT count(*) INTO v_comuns
        FROM unnest(v_os) x WHERE x=ANY(v_os_antigas);
        IF coalesce(v_comuns,0)<2
           OR v_comuns*2<coalesce(cardinality(v_os_antigas),0)
           OR v_comuns*2<coalesce(cardinality(v_os),0)
        THEN RAISE EXCEPTION 'OCORRENCIAS_SOBREPOSICAO_INSUFICIENTE'; END IF;
        BEGIN
          SELECT latitude::double precision,longitude::double precision
            INTO v_lat1,v_lon1 FROM public.farol_acomp_vinculos WHERE chave=v_chave_antiga;
          v_lat2:=(v_item->>'latitude')::double precision;
          v_lon2:=(v_item->>'longitude')::double precision;
        EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
          RAISE EXCEPTION 'OCORRENCIAS_COORDENADAS_INVALIDAS';
        END;
        IF NOT (v_lat1 BETWEEN -90 AND 90 AND v_lat2 BETWEEN -90 AND 90
            AND v_lon1 BETWEEN -180 AND 180 AND v_lon2 BETWEEN -180 AND 180)
        THEN RAISE EXCEPTION 'OCORRENCIAS_COORDENADAS_INVALIDAS'; END IF;
        v_distancia:=6371000*acos(least(1.0,greatest(-1.0,
             sin(radians(v_lat1))*sin(radians(v_lat2))
             +cos(radians(v_lat1))*cos(radians(v_lat2))
              *cos(radians(v_lon1-v_lon2)))));
        IF v_distancia IS NULL OR v_distancia>750
        THEN RAISE EXCEPTION 'OCORRENCIAS_CENTROS_DISTANTES'; END IF;
      END IF;

      SELECT evento_id INTO v_existe FROM public.farol_acomp_vinculos
      WHERE chave=v_chave FOR UPDATE;
      IF FOUND THEN
        IF v_existe<>v_ficha THEN RAISE EXCEPTION 'OCORRENCIAS_VINCULO_CONFLITANTE'; END IF;
        CONTINUE;
      END IF;
      SELECT array_agg(DISTINCT btrim(value)) INTO v_os
      FROM jsonb_array_elements_text(v_item->'os') AS value WHERE btrim(value)<>'';
      IF coalesce(cardinality(v_os),0)<2
      THEN RAISE EXCEPTION 'OCORRENCIAS_OS_INSUFICIENTES'; END IF;

      INSERT INTO public.farol_acomp_vinculos
        (chave,evento_id,municipio,bairros,latitude,longitude,os_origem,criado_em,origem_hash)
      VALUES (
        v_chave,v_ficha,v_municipio,coalesce(v_item->>'bairros',''),
        coalesce(v_item->>'latitude',''),coalesce(v_item->>'longitude',''),
        array_to_string(v_os,';'),v_hora,
        encode(sha256(convert_to(v_item::text||':'||v_ficha,'UTF8')),'hex')
      ) ON CONFLICT(chave) DO NOTHING;
      IF NOT EXISTS(SELECT 1 FROM public.farol_acomp_vinculos
                    WHERE chave=v_chave AND evento_id=v_ficha)
      THEN RAISE EXCEPTION 'OCORRENCIAS_CONFLITO_SIMULTANEO'; END IF;
      v_num:=v_num+1;
    END LOOP;

    IF v_num>0 OR v_incorporadas>0 THEN
      INSERT INTO public.prisma_auditoria
        (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
      VALUES(p_autor,'alterar',
        'Sincronizacao de ocorrencias persistentes: '||v_num||' novos vinculos; '||
        v_incorporadas||' fichas incorporadas. Fichas e movimentacoes originais preservadas.',
        '3','ALTERACAO','SUCESSO','OCORRENCIAS_PERSISTENTES');
    END IF;
    RETURN jsonb_build_object(
      'vinculos',v_num,'incorporacoes',v_incorporadas,
      'eventos',v_eventos_processados,'preservou_historico',true
    );
END;
$func$;

REVOKE ALL ON FUNCTION public.farol_acomp_sincronizar_ocorrencias_tx(jsonb,uuid)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_sincronizar_ocorrencias_tx(jsonb,uuid)
  TO service_role;

CREATE OR REPLACE FUNCTION public.farol_acomp_criar_ocorrencia_tx(
    p_dados jsonb, p_contexto jsonb, p_autor uuid, p_evento_chave text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path TO ''
AS $func$
DECLARE v_resultado jsonb;
BEGIN
    IF p_evento_chave !~ '^[a-f0-9]{32}$' OR p_evento_chave IS NULL
    THEN RAISE EXCEPTION 'OCORRENCIAS_EVENTO_INVALIDO'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.prisma_perfis
      WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario'))
    THEN RAISE EXCEPTION 'OCORRENCIAS_SEM_PERMISSAO'; END IF;
    PERFORM pg_advisory_xact_lock(hashtext('FAROL_OCORRENCIAS_PERSISTENTES_V1'));
    IF EXISTS(SELECT 1 FROM public.farol_acomp_eventos_persistentes
      WHERE evento_chave=p_evento_chave)
    THEN RAISE EXCEPTION 'OCORRENCIAS_EVENTO_JA_ACOMPANHADO'; END IF;
    v_resultado:=public.farol_acomp_criar_tx(p_dados,p_contexto,p_autor);
    INSERT INTO public.farol_acomp_eventos_persistentes
       (evento_chave,ficha_id,municipio)
    VALUES(p_evento_chave,v_resultado->>'id',p_contexto->>'municipio');
    RETURN v_resultado;
END;
$func$;
REVOKE ALL ON FUNCTION public.farol_acomp_criar_ocorrencia_tx(jsonb,jsonb,uuid,text)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_criar_ocorrencia_tx(jsonb,jsonb,uuid,text)
  TO service_role;

-- Historico agregado inclui movimentacoes da ficha principal e incorporadas
-- sem modificar os dados originais. Consultado somente ao abrir a ficha.
CREATE OR REPLACE FUNCTION public.farol_acomp_historico_ocorrencia(
    p_ficha_id text
) RETURNS TABLE (id text, evento_id text, origem_ficha_id text,
                 data_hora text, descricao text, status_momento text)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path TO ''
AS $func$
 SELECT m.id, p_ficha_id AS evento_id, m.evento_id AS origem_ficha_id,
        m.data_hora,m.descricao,m.status_momento
 FROM public.farol_acomp_movimentacoes m
 WHERE m.evento_id=p_ficha_id
    OR EXISTS(SELECT 1 FROM public.farol_acomp_incorporacoes i
              WHERE i.ficha_principal=p_ficha_id AND i.ficha_incorporada=m.evento_id)
 ORDER BY CASE WHEN m.id ~ '^[0-9]+
$func$;
REVOKE ALL ON FUNCTION public.farol_acomp_historico_ocorrencia(text)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_historico_ocorrencia(text)
  TO service_role;
 THEN m.id::bigint END NULLS LAST,m.id;
$func$;
REVOKE ALL ON FUNCTION public.farol_acomp_historico_ocorrencia(text)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_historico_ocorrencia(text)
  TO service_role;
