-- Módulo 9 / Gestão de Campo — reset independente dos fechamentos.
-- Instalar esta função NÃO apaga os registros existentes.
-- A exclusão só acontece quando um admin executa a RPC com confirmação.
-- Exclusivamente no servidor PRISMA, com service_role.
CREATE OR REPLACE FUNCTION public.prisma_fechamentos_resetar_registros_tx(
    p_autor uuid, p_confirmacao text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $func$
DECLARE
    v_admin_nome text;
    v_admin_email text;
    v_excluidos integer := 0;
BEGIN
    IF p_confirmacao IS DISTINCT FROM 'EXCLUIR TODOS OS FECHAMENTOS' THEN
        RAISE EXCEPTION 'Confirmação de exclusão de fechamentos inválida.';
    END IF;
    SELECT nome, email INTO v_admin_nome, v_admin_email
      FROM public.prisma_perfis
     WHERE id = p_autor AND perfil = 'admin' AND status = 'ativo';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Somente administradores ativos podem excluir fechamentos.';
    END IF;

    -- Lock, remoção e auditoria formam uma única transação.
    LOCK TABLE public.prisma_fechamentos_rede IN SHARE ROW EXCLUSIVE MODE;
    DELETE FROM public.prisma_fechamentos_rede;
    GET DIAGNOSTICS v_excluidos = ROW_COUNT;

    INSERT INTO public.prisma_auditoria (
        autor_id, acao, detalhes, modulo, tipo, resultado, registro_id
    ) VALUES (
        p_autor, 'excluir_todos_fechamentos',
        format(
            'Exclusão administrativa M9; %s fechamentos/reparos apagados; cadastro de ativos preservado.',
            v_excluidos
        ),
        '9', 'EXCLUSAO', 'SUCESSO', 'RESET_GESTAO_FECHAMENTOS'
    );
    RETURN jsonb_build_object('fechamentos_excluidos', v_excluidos);
END;
$func$;

-- Impede execução RPC por clientes autenticados ou anônimos.
REVOKE ALL ON FUNCTION public.prisma_fechamentos_resetar_registros_tx(uuid, text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.prisma_fechamentos_resetar_registros_tx(uuid, text)
  TO service_role;
