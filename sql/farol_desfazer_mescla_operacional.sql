-- PRISMA / Farol Operacional — desfazimento seguro de AGRUPAMENTO.
-- Não apaga nem redistribui fichas, vínculos, O.S., históricos ou incorporações.
CREATE OR REPLACE FUNCTION public.farol_desfazer_mescla_tx(
    p_evento_chave text,
    p_alterado_em timestamptz,
    p_autor uuid
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path TO ''
AS $func$
DECLARE
    v_mescla public.farol_mesclas_operacionais%ROWTYPE;
    v_qtd_codigos integer;
    v_qtd_os integer;
BEGIN
    IF p_evento_chave IS NULL OR p_evento_chave !~ '^[a-f0-9]{32}$'
       OR p_alterado_em IS NULL
    THEN
        RAISE EXCEPTION 'MESCLA_PARAMETROS_INVALIDOS';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.prisma_perfis
        WHERE id=p_autor AND status='ativo'
          AND perfil IN ('admin','usuario')
    ) THEN
        RAISE EXCEPTION 'MESCLA_SEM_PERMISSAO';
    END IF;

    -- Mesmo lock usado para criar/atualizar uma mesclagem deste evento.
    PERFORM pg_advisory_xact_lock(hashtext(p_evento_chave));

    SELECT * INTO v_mescla
    FROM public.farol_mesclas_operacionais
    WHERE evento_chave=p_evento_chave
    FOR UPDATE;

    IF NOT FOUND OR v_mescla.ativo IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'MESCLA_JA_DESFEITA_OU_INEXISTENTE';
    END IF;
    IF v_mescla.alterado_em IS DISTINCT FROM p_alterado_em THEN
        RAISE EXCEPTION 'MESCLA_ATUALIZADA_RECARREGAR';
    END IF;
    IF jsonb_typeof(v_mescla.codigos_origem)<>'array'
       OR jsonb_typeof(v_mescla.os_origem)<>'array' THEN
        RAISE EXCEPTION 'MESCLA_DADOS_INVALIDOS';
    END IF;
    v_qtd_codigos := jsonb_array_length(v_mescla.codigos_origem);
    v_qtd_os := jsonb_array_length(v_mescla.os_origem);

    -- APENAS esta coluna deixa de ser considerada por aplicar_mesclas().
    -- Não tocar nas tabelas farol_acomp_*, nem excluir a própria mesclagem.
    UPDATE public.farol_mesclas_operacionais
    SET ativo=false, alterado_em=now()
    WHERE evento_chave=p_evento_chave AND ativo=true;

    INSERT INTO public.prisma_auditoria (
        autor_id,acao,detalhes,modulo,tipo,resultado,registro_id
    ) VALUES (
        p_autor,'alterar',
        'Agrupamento de concentrações desfeito no Farol. Evento: ' ||
        left(coalesce(v_mescla.evento_rotulo,''),240) ||
        '; município: ' || left(coalesce(v_mescla.municipio,''),120) ||
        '; concentrações: ' || v_qtd_codigos ||
        '; O.S.: ' || v_qtd_os ||
        '. Fichas, vínculos e movimentações permanecem preservados.',
        '3','ALTERACAO','SUCESSO',p_evento_chave
    );

    RETURN jsonb_build_object(
        'evento_chave',p_evento_chave,
        'codigo_mescla',v_mescla.codigo_mescla,
        'qtd_concentracoes',v_qtd_codigos,
        'qtd_os',v_qtd_os,
        'agrupamento_ativo',false,
        'acompanhamentos_preservados',true
    );
END;
$func$;

REVOKE ALL ON FUNCTION public.farol_desfazer_mescla_tx(text,timestamptz,uuid)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.farol_desfazer_mescla_tx(text,timestamptz,uuid)
TO service_role;

COMMENT ON FUNCTION public.farol_desfazer_mescla_tx(text,timestamptz,uuid)
IS 'Desativa somente a apresentação da mesclagem operacional; preserva integralmente fichas e históricos.';
