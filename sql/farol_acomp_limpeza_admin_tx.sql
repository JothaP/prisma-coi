-- Exclusão administrativa opcional das fichas de acompanhamento do Farol (M3).
-- A criação desta rotina não altera dados; a limpeza exige invocação explícita.
-- Não altera a retenção das réplicas históricas/backup no Google Drive.
CREATE OR REPLACE FUNCTION public.farol_acomp_limpar_tudo_admin_tx(p_autor uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  n_fichas bigint;
  n_vinculos bigint;
  n_movimentacoes bigint;
  n_eventos bigint;
  n_incorporacoes bigint;
BEGIN
  IF p_autor IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.prisma_perfis
    WHERE id = p_autor AND perfil = 'admin' AND status = 'ativo'
  ) THEN
    RAISE EXCEPTION 'FAROL_LIMPEZA_SEM_PERMISSAO';
  END IF;

  -- A transação impede que fichas sejam removidas sem os relacionamentos
  -- obrigatórios: todas as exclusões são revertidas se alguma etapa falhar.
  -- Descarta pendências de INSERT/UPDATE anteriores para que a replicação
  -- não tente reaplicar uma ficha já eliminada.
  DELETE FROM public.farol_acomp_replica_fila
  WHERE processado_em IS NULL
    AND tabela IN ('farol_acomp_fichas','farol_acomp_vinculos',
                   'farol_acomp_movimentacoes');

  -- O projeto aplica a proteção SQL 'DELETE requires a WHERE clause' (21000).
  -- Os filtros abaixo usam chaves NOT NULL verificadas no schema: removem
  -- todos os registros somente dentro desta operação administrativa.
  -- FKs RESTRICT exigem remoção dos registros vinculados primeiro.
  DELETE FROM public.farol_acomp_eventos_persistentes
  WHERE evento_chave IS NOT NULL;
  GET DIAGNOSTICS n_eventos = ROW_COUNT;

  DELETE FROM public.farol_acomp_incorporacoes
  WHERE ficha_incorporada IS NOT NULL;
  GET DIAGNOSTICS n_incorporacoes = ROW_COUNT;

  DELETE FROM public.farol_acomp_vinculos
  WHERE chave IS NOT NULL;
  GET DIAGNOSTICS n_vinculos = ROW_COUNT;

  DELETE FROM public.farol_acomp_movimentacoes
  WHERE id IS NOT NULL;
  GET DIAGNOSTICS n_movimentacoes = ROW_COUNT;

  DELETE FROM public.farol_acomp_fichas
  WHERE id IS NOT NULL;
  GET DIAGNOSTICS n_fichas = ROW_COUNT;

  -- Registro persistente da remoção com identificador administrativo.
  -- A auditoria participa da mesma transação, sem expor conteúdo das fichas.
  INSERT INTO public.prisma_auditoria
    (autor_id, acao, detalhes, modulo, tipo, resultado, registro_id)
  VALUES
    (p_autor, 'limpar_fichas_farol',
     format('Fichas=%s; vinculos=%s; movimentacoes=%s; eventos=%s; incorporacoes=%s',
            n_fichas,n_vinculos,n_movimentacoes,n_eventos,n_incorporacoes),
     '3', 'EXCLUSAO', 'SUCESSO', 'limpeza_fichas_modulo_3');

  RETURN jsonb_build_object(
    'concluido', true,
    'fichas', n_fichas,
    'vinculos', n_vinculos,
    'movimentacoes', n_movimentacoes,
    'eventos_persistentes', n_eventos,
    'incorporacoes', n_incorporacoes
  );
END;
$$;

-- Apenas chamadas de serviço do servidor podem executar a exclusão.
REVOKE ALL ON FUNCTION public.farol_acomp_limpar_tudo_admin_tx(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_limpar_tudo_admin_tx(uuid)
  TO service_role;
NOTIFY pgrst, 'reload schema';
