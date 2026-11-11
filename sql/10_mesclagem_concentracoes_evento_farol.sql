-- Agrupamentos de O.S. por evento e polígono, independentes da numeração C001.
-- As fichas e movimentações originais permanecem intactas.
CREATE TABLE IF NOT EXISTS public.farol_mesclas_operacionais (
    evento_chave text PRIMARY KEY,
    municipio text NOT NULL,
    evento_rotulo text NOT NULL,
    codigo_mescla text NOT NULL,
    chave_acompanhamento text NOT NULL UNIQUE,
    codigos_origem jsonb NOT NULL,
    os_origem jsonb NOT NULL,
    poligono jsonb,
    criado_por uuid NOT NULL,
    criado_em timestamptz NOT NULL DEFAULT now(),
    alterado_em timestamptz NOT NULL DEFAULT now(),
    ativo boolean NOT NULL DEFAULT true,
    CONSTRAINT farol_mescla_os_array CHECK (jsonb_typeof(os_origem)='array'),
    CONSTRAINT farol_mescla_grupos_array CHECK (jsonb_typeof(codigos_origem)='array'),
    CONSTRAINT farol_mescla_poligono_tipo CHECK (
        poligono IS NULL OR
        (jsonb_typeof(poligono)='object' AND poligono->>'type'='Polygon')
    )
);
ALTER TABLE public.farol_mesclas_operacionais ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.farol_mesclas_operacionais FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.farol_mesclas_operacionais TO service_role;

CREATE OR REPLACE FUNCTION public.farol_salvar_mescla_tx(
    p_evento_chave text, p_municipio text, p_evento_rotulo text,
    p_codigo text, p_chave_acomp text, p_codigos jsonb,
    p_os jsonb, p_poligono jsonb, p_chaves_origem text[],
    p_autor uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path=''
AS $fn$
DECLARE
    v_fichas text[];
    v_ficha text;
    v_ficha_atual text;
    v_os text[];
    v_codigos text[];
    v_criado text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.prisma_perfis
        WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario')
    ) THEN
        RAISE EXCEPTION 'Usuário sem perfil para mesclagem';
    END IF;
    IF coalesce(p_evento_chave,'') !~ '^[0-9a-f]{32}$'
       OR coalesce(p_chave_acomp,'') !~ '^[0-9a-f]{32}$'
       OR btrim(coalesce(p_municipio,''))=''
       OR btrim(coalesce(p_evento_rotulo,''))=''
       OR coalesce(p_codigo,'') !~ '^(THE|API|TIM) M[0-9]{4,}$'
       OR p_os IS NULL OR jsonb_typeof(p_os)<>'array'
       OR p_codigos IS NULL OR jsonb_typeof(p_codigos)<>'array'
       OR p_poligono IS NOT NULL AND (
         jsonb_typeof(p_poligono)<>'object' OR
         p_poligono->>'type'<>'Polygon' OR
         jsonb_typeof(p_poligono->'coordinates')<>'array'
       )
    THEN RAISE EXCEPTION 'Parâmetros inválidos para a mesclagem'; END IF;

    SELECT array_agg(DISTINCT btrim(x)) INTO v_os
    FROM jsonb_array_elements_text(p_os) x WHERE btrim(x)<>'';
    SELECT array_agg(DISTINCT btrim(x)) INTO v_codigos
    FROM jsonb_array_elements_text(p_codigos) x WHERE btrim(x)<>'';
    IF coalesce(cardinality(v_os),0)<6 OR coalesce(cardinality(v_codigos),0)<2 THEN
        RAISE EXCEPTION 'Mesclagem exige pelo menos duas concentrações e seis O.S.';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext(p_evento_chave));

    -- Uma O.S. não pode simultaneamente pertencer a outra mesclagem ativa.
    IF EXISTS (
        SELECT 1 FROM public.farol_mesclas_operacionais m,
             jsonb_array_elements_text(m.os_origem) n
        WHERE m.ativo AND m.evento_chave<>p_evento_chave AND n=ANY(v_os)
    ) THEN RAISE EXCEPTION 'O.S. já pertence a outra mesclagem ativa'; END IF;

    SELECT array_agg(DISTINCT evento_id) INTO v_fichas
    FROM public.farol_acomp_vinculos
    WHERE chave=ANY(coalesce(p_chaves_origem,ARRAY[]::text[]));
    IF coalesce(cardinality(v_fichas),0)>1 THEN
        RAISE EXCEPTION 'Concentrações têm fichas diferentes: unificação automática bloqueada';
    END IF;
    v_ficha:=v_fichas[1];
    SELECT evento_id INTO v_ficha_atual
    FROM public.farol_acomp_vinculos WHERE chave=p_chave_acomp;
    IF FOUND AND v_ficha IS NOT NULL AND v_ficha_atual<>v_ficha THEN
        RAISE EXCEPTION 'A mesclagem já tem outra ficha de acompanhamento';
    END IF;
    IF v_ficha_atual IS NOT NULL THEN v_ficha:=v_ficha_atual; END IF;

    INSERT INTO public.farol_mesclas_operacionais (
        evento_chave,municipio,evento_rotulo,codigo_mescla,
        chave_acompanhamento,codigos_origem,os_origem,poligono,
        criado_por
    ) VALUES (
        p_evento_chave,p_municipio,p_evento_rotulo,p_codigo,
        p_chave_acomp,p_codigos,p_os,p_poligono,p_autor
    )
    ON CONFLICT (evento_chave) DO UPDATE SET
        municipio=EXCLUDED.municipio,
        evento_rotulo=EXCLUDED.evento_rotulo,
        codigo_mescla=EXCLUDED.codigo_mescla,
        chave_acompanhamento=EXCLUDED.chave_acompanhamento,
        codigos_origem=EXCLUDED.codigos_origem,
        os_origem=EXCLUDED.os_origem,
        poligono=EXCLUDED.poligono,
        alterado_em=now(), ativo=true;

    IF v_ficha IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.farol_acomp_fichas WHERE id=v_ficha
        ) THEN RAISE EXCEPTION 'Ficha antiga não encontrada'; END IF;
        v_criado:=to_char(clock_timestamp() AT TIME ZONE 'America/Fortaleza','DD/MM/YYYY HH24:MI');
        INSERT INTO public.farol_acomp_vinculos (
            chave,evento_id,municipio,bairros,latitude,longitude,
            os_origem,criado_em,origem_hash
        ) VALUES (
            p_chave_acomp,v_ficha,p_municipio,'Mesclagem de evento','','',
            array_to_string(v_os,';'),v_criado,
            encode(sha256(convert_to(p_evento_chave||':'||v_ficha,'UTF8')),'hex')
        )
        ON CONFLICT(chave) DO NOTHING;
        SELECT evento_id INTO v_ficha_atual FROM public.farol_acomp_vinculos
        WHERE chave=p_chave_acomp;
        IF v_ficha_atual<>v_ficha THEN
            RAISE EXCEPTION 'Chave mesclada vinculada a outra ficha'; END IF;
    END IF;

    INSERT INTO public.prisma_auditoria (
        autor_id,acao,detalhes,modulo,tipo,resultado,registro_id
    ) VALUES (
        p_autor,'alterar',
        'Mesclagem de concentrações por evento; vínculos e históricos antigos preservados',
        '3','ALTERACAO','SUCESSO',p_evento_chave
    );
    RETURN jsonb_build_object(
        'evento_chave',p_evento_chave,
        'chave_acompanhamento',p_chave_acomp,
        'evento_id',v_ficha,
        'qtd_os',cardinality(v_os),
        'qtd_concentracoes',cardinality(v_codigos)
    );
END;
$fn$;

REVOKE ALL ON FUNCTION public.farol_salvar_mescla_tx(
    text,text,text,text,text,jsonb,jsonb,jsonb,text[],uuid
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.farol_salvar_mescla_tx(
    text,text,text,text,text,jsonb,jsonb,jsonb,text[],uuid
) TO service_role;
