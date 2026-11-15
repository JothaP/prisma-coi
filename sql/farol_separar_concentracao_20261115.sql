-- PRISMA Módulo 3: separação explícita de concentrações que compartilham ficha.
-- Migração a aplicar antes de publicar a interface. Não altera dados existentes
-- ao ser instalada. O RPC é atômico e mantém fichas/históricos anteriores.
CREATE OR REPLACE FUNCTION public.farol_acomp_separar_concentracao_tx(
    p_chave text,
    p_ficha_atual text,
    p_versao_esperada bigint,
    p_dados jsonb,
    p_autor uuid
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path TO ''
AS $func$
DECLARE
    v_vinculo public.farol_acomp_vinculos%ROWTYPE;
    v_ficha public.farol_acomp_fichas%ROWTYPE;
    v_vinculos bigint;
    v_novo_id text;
    v_movimento_id text;
    v_hora text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.prisma_perfis
        WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario')
    ) THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_SEM_PERMISSAO';
    END IF;
    IF p_chave IS NULL OR p_chave !~ '^[a-f0-9]{32}$'
       OR coalesce(btrim(p_ficha_atual),'')=''
       OR p_versao_esperada IS NULL THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_CONTEXTO_INVALIDO';
    END IF;
    PERFORM public.farol_acomp_validar_entrada(p_dados,p_autor);
    -- Serializa com a conciliação de ocorrências, além de bloquear o vínculo.
    PERFORM pg_advisory_xact_lock(hashtext('FAROL_OCORRENCIAS_PERSISTENTES_V1'));
    SELECT * INTO v_vinculo
      FROM public.farol_acomp_vinculos
      WHERE chave=p_chave AND evento_id=p_ficha_atual
      FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_VINCULO_ALTERADO';
    END IF;
    SELECT * INTO v_ficha
      FROM public.farol_acomp_fichas WHERE id=p_ficha_atual FOR UPDATE;
    IF NOT FOUND OR v_ficha.versao<>p_versao_esperada THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_VERSAO_ALTERADA';
    END IF;
    SELECT count(*) INTO v_vinculos FROM public.farol_acomp_vinculos
      WHERE evento_id=p_ficha_atual;
    IF v_vinculos<2 THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_FICHA_NAO_COMPARTILHADA';
    END IF;
    IF lower(btrim(p_dados->>'municipio'))<>lower(btrim(v_vinculo.municipio)) THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_MUNICIPIO_DIVERGENTE';
    END IF;
    -- Não desmembrar uma mesclagem operacional expressamente confirmada.
    IF EXISTS (
        SELECT 1 FROM public.farol_mesclas_operacionais
        WHERE ativo AND chave_acompanhamento=p_chave
    ) THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_MESCLAGEM_ATIVA';
    END IF;

    v_hora:=to_char(clock_timestamp() AT TIME ZONE 'America/Fortaleza',
                    'DD/MM/YYYY HH24:MI');
    v_novo_id:=nextval('public.farol_acomp_fichas_id_seq'::regclass)::text;

    INSERT INTO public.farol_acomp_fichas (
        id,titulo,qtd_reclamacoes,status,ultima_atualizacao,unidade,regional,
        municipio,data_identificacao,descricao,arcgis,sia,loggers,retorno_operacao,
        finalizado,data_finalizacao,causa_identificada,origem_hash
    ) VALUES (
        v_novo_id,p_dados->>'titulo',p_dados->>'qtd_reclamacoes',
        p_dados->>'status',v_hora,p_dados->>'unidade',
        p_dados->>'regional',p_dados->>'municipio',v_hora,p_dados->>'descricao',
        p_dados->>'arcgis',p_dados->>'sia',p_dados->>'loggers',
        p_dados->>'retorno_operacao',p_dados->>'finalizado',
        p_dados->>'data_finalizacao',p_dados->>'causa_identificada',
        encode(sha256(convert_to(p_dados::text || ':' || v_novo_id,'UTF8')),'hex')
    );

    UPDATE public.farol_acomp_vinculos SET
        evento_id=v_novo_id,
        origem_hash=encode(sha256(convert_to(
            row_to_json(v_vinculo)::text || ':' || v_novo_id,'UTF8')),'hex')
    WHERE chave=p_chave AND evento_id=p_ficha_atual;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'FAROL_SEPARACAO_VINCULO_ALTERADO';
    END IF;

    v_movimento_id:=nextval('public.farol_acomp_movimentacoes_id_seq'::regclass)::text;
    INSERT INTO public.farol_acomp_movimentacoes (
        id,evento_id,data_hora,descricao,status_momento,origem_hash
    ) VALUES (
        v_movimento_id,v_novo_id,v_hora,
        left('Ficha independente criada a partir da concentração. Relato: ' ||
             coalesce(p_dados->>'descricao',''),40000),
        p_dados->>'status',
        encode(sha256(convert_to(v_movimento_id || ':' || v_novo_id,'UTF8')),'hex')
    );
    INSERT INTO public.prisma_auditoria (
        autor_id,acao,detalhes,modulo,tipo,resultado,registro_id
    ) VALUES (
        p_autor,'alterar',
        'Separação explícita de concentração; ficha e movimentações anteriores preservadas',
        '3','ALTERACAO','SUCESSO',v_novo_id
    );
    RETURN jsonb_build_object(
        'id',v_novo_id,'versao',1,'chave',p_chave,
        'ficha_anterior',p_ficha_atual,'historico_anterior_preservado',true
    );
END;
$func$;

REVOKE ALL ON FUNCTION public.farol_acomp_separar_concentracao_tx(
    text,text,bigint,jsonb,uuid
) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_separar_concentracao_tx(
    text,text,bigint,jsonb,uuid
) TO service_role;
