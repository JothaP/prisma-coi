-- PRISMA | Farol Operacional | Correção pontual da RPC de criação.
-- Não altera nenhuma ficha, vínculo ou movimentação existente.
-- Execute no SQL Editor do Supabase antes de repetir o teste transacional.
BEGIN;
CREATE OR REPLACE FUNCTION public.farol_acomp_criar_tx(
  p_dados jsonb, p_contexto jsonb, p_autor uuid)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = ''
AS $fn$
DECLARE
  v_ficha_id text;
  v_mov_id text;
  v_chave text;
BEGIN
  PERFORM public.farol_acomp_validar_entrada(p_dados,p_autor);
  IF p_contexto IS NULL OR jsonb_typeof(p_contexto) <> 'object' THEN
    RAISE EXCEPTION 'Contexto de concentração inválido';
  END IF;
  v_chave := btrim(coalesce(p_contexto->>'chave',''));
  IF v_chave !~ '^[a-f0-9]{32}$'
     OR nullif(btrim(coalesce(p_contexto->>'municipio','')),'') IS NULL
     OR nullif(btrim(coalesce(p_contexto->>'os_origem','')),'') IS NULL
  THEN
    RAISE EXCEPTION 'Concentração sem identidade estável ou O.S. de origem';
  END IF;
  IF EXISTS(SELECT 1 FROM public.farol_acomp_vinculos WHERE chave=v_chave) THEN
    RAISE EXCEPTION 'Concentração já vinculada a um acompanhamento';
  END IF;
  v_ficha_id := nextval('public.farol_acomp_fichas_id_seq'::regclass)::text;
  INSERT INTO public.farol_acomp_fichas
    (id,titulo,qtd_reclamacoes,status,ultima_atualizacao,unidade,regional,
     municipio,data_identificacao,descricao,arcgis,sia,loggers,retorno_operacao,
     finalizado,data_finalizacao,causa_identificada,origem_hash)
  VALUES (
    v_ficha_id,p_dados->>'titulo',p_dados->>'qtd_reclamacoes',
    p_dados->>'status',p_dados->>'ultima_atualizacao',p_dados->>'unidade',
    p_dados->>'regional',p_dados->>'municipio',p_dados->>'data_identificacao',
    p_dados->>'descricao',p_dados->>'arcgis',p_dados->>'sia',p_dados->>'loggers',
    p_dados->>'retorno_operacao',p_dados->>'finalizado',p_dados->>'data_finalizacao',
    p_dados->>'causa_identificada',
    encode(sha256(convert_to(p_dados::text || ':' || v_ficha_id,'UTF8')),'hex')
  );
  INSERT INTO public.farol_acomp_vinculos
    (chave,evento_id,municipio,bairros,latitude,longitude,os_origem,criado_em,origem_hash)
  VALUES (
    v_chave,v_ficha_id,p_contexto->>'municipio',coalesce(p_contexto->>'bairros',''),
    coalesce(p_contexto->>'latitude',''),coalesce(p_contexto->>'longitude',''),
    p_contexto->>'os_origem',coalesce(p_contexto->>'criado_em',''),
    encode(sha256(convert_to(p_contexto::text || ':' || v_ficha_id,'UTF8')),'hex')
  );
  v_mov_id := nextval('public.farol_acomp_movimentacoes_id_seq'::regclass)::text;
  INSERT INTO public.farol_acomp_movimentacoes
    (id,evento_id,data_hora,descricao,status_momento,origem_hash)
  VALUES (v_mov_id,v_ficha_id,p_dados->>'ultima_atualizacao',
    left('Registro inicial criado. Relato: ' || (p_dados->>'descricao'),40000),
    p_dados->>'status',
    encode(sha256(convert_to(v_mov_id || ':' || v_ficha_id,'UTF8')),'hex'));
  INSERT INTO public.prisma_auditoria
    (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES (p_autor,'criar','Acompanhamento do Farol criado','3','CRIACAO','SUCESSO',v_ficha_id);
  RETURN jsonb_build_object('id',v_ficha_id,'versao',1,
                            'movimentacao_id',v_mov_id,'chave',v_chave);
END;
$fn$;
REVOKE ALL ON FUNCTION public.farol_acomp_criar_tx(jsonb,jsonb,uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.farol_acomp_criar_tx(jsonb,jsonb,uuid)
  TO service_role;
COMMIT;

-- Verificação isolada da expressão de texto; não grava registros.
SELECT left('Registro inicial criado. Relato: ' ||
            ('{"descricao":"Acompanhamento validado"}'::jsonb->>'descricao'), 40000)
       AS descricao_teste;
