
-- O.S. vinculadas manualmente a eventos no Farol Operacional.
-- Não modifica O.S. nem eventos de origem; registra decisão reversível e auditada.
CREATE TABLE IF NOT EXISTS public.farol_vinculos_os_manuais (
 numero_os text NOT NULL CHECK(length(numero_os) BETWEEN 1 AND 100),
 evento_chave text NOT NULL CHECK(evento_chave ~ '^[a-f0-9]{32}$'),
 municipio text NOT NULL CHECK(length(btrim(municipio)) BETWEEN 2 AND 120),
 protocolo text NOT NULL DEFAULT '',
 servico text NOT NULL DEFAULT '',
 codigo_ativo text NOT NULL DEFAULT '',
 inicio text NOT NULL DEFAULT '',
 evento_rotulo text NOT NULL DEFAULT '',
 justificativa text NOT NULL DEFAULT '',
 criado_por uuid NOT NULL REFERENCES public.prisma_perfis(id),
 criado_em timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(numero_os, evento_chave)
);
CREATE INDEX IF NOT EXISTS idx_farol_vinc_manual_os ON public.farol_vinculos_os_manuais(numero_os);
ALTER TABLE public.farol_vinculos_os_manuais ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.farol_vinculos_os_manuais FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, DELETE ON public.farol_vinculos_os_manuais TO service_role;

CREATE OR REPLACE FUNCTION public.farol_adicionar_vinculos_os_manuais_tx(
 p_evento_chave text,p_municipio text,p_protocolo text,p_servico text,
 p_codigo_ativo text,p_inicio text,p_evento_rotulo text,
 p_numeros_os text[],p_justificativa text,p_autor uuid
) RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path TO '' AS $func$
DECLARE v_lista text[]; v_qtd integer;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.prisma_perfis
  WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario'))
 THEN RAISE EXCEPTION 'PERMISSAO_MANUAL_NEGADA'; END IF;
 SELECT coalesce(array_agg(DISTINCT upper(btrim(x))),ARRAY[]::text[]) INTO v_lista
 FROM unnest(p_numeros_os) AS x WHERE btrim(x)<>'';
 IF coalesce(p_evento_chave,'') !~ '^[a-f0-9]{32}$'
    OR length(btrim(coalesce(p_municipio,''))) NOT BETWEEN 2 AND 120
    OR cardinality(v_lista) NOT BETWEEN 1 AND 200
    OR length(coalesce(p_justificativa,''))>1200
    OR EXISTS (SELECT 1 FROM unnest(v_lista) AS os
       WHERE length(os)>100 OR os !~ '^[A-Z0-9./_-]+$')
 THEN RAISE EXCEPTION 'PARAMETROS_VINCULO_MANUAL_INVALIDOS'; END IF;
 INSERT INTO public.farol_vinculos_os_manuais (
   numero_os,evento_chave,municipio,protocolo,servico,codigo_ativo,
   inicio,evento_rotulo,justificativa,criado_por
 ) SELECT os,p_evento_chave,upper(btrim(p_municipio)),
   left(coalesce(p_protocolo,''),180),
   left(coalesce(p_servico,''),240),
   left(coalesce(p_codigo_ativo,''),180),
   left(coalesce(p_inicio,''),90),
   left(coalesce(p_evento_rotulo,''),240),
   coalesce(p_justificativa,''),p_autor FROM unnest(v_lista) AS os
 ON CONFLICT (numero_os,evento_chave) DO NOTHING;
 GET DIAGNOSTICS v_qtd=ROW_COUNT;
 IF v_qtd>0 THEN INSERT INTO public.prisma_auditoria
  (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES (p_autor,'alterar','Inclusão de '||v_qtd||' vínculo(s) manual(is), evento '||
    p_evento_chave||', município '||upper(btrim(p_municipio))||
    ', O.S.: '||array_to_string(v_lista,', '),
    '3','ALTERACAO','SUCESSO',p_evento_chave); END IF;
 RETURN v_qtd;
END;
$func$;

CREATE OR REPLACE FUNCTION public.farol_remover_vinculos_os_manuais_tx(
 p_evento_chave text,p_numeros_os text[],p_autor uuid
) RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path TO '' AS $func$
DECLARE v_lista text[]; v_qtd integer;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.prisma_perfis
  WHERE id=p_autor AND status='ativo' AND perfil IN ('admin','usuario'))
 THEN RAISE EXCEPTION 'PERMISSAO_MANUAL_NEGADA'; END IF;
 SELECT coalesce(array_agg(DISTINCT upper(btrim(x))),ARRAY[]::text[]) INTO v_lista
 FROM unnest(p_numeros_os) AS x WHERE btrim(x)<>'';
 IF coalesce(p_evento_chave,'') !~ '^[a-f0-9]{32}$'
    OR cardinality(v_lista) NOT BETWEEN 1 AND 200
    OR EXISTS (SELECT 1 FROM unnest(v_lista) AS os
      WHERE length(os)>100 OR os !~ '^[A-Z0-9./_-]+$')
 THEN RAISE EXCEPTION 'PARAMETROS_VINCULO_MANUAL_INVALIDOS'; END IF;
 DELETE FROM public.farol_vinculos_os_manuais
 WHERE evento_chave=p_evento_chave AND numero_os=ANY(v_lista);
 GET DIAGNOSTICS v_qtd=ROW_COUNT;
 IF v_qtd>0 THEN INSERT INTO public.prisma_auditoria
  (autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
  VALUES (p_autor,'excluir','Remoção de '||v_qtd||
    ' vínculo(s) manual(is), evento '||p_evento_chave||
    ', O.S.: '||array_to_string(v_lista,', '),
    '3','EXCLUSAO','SUCESSO',p_evento_chave); END IF;
 RETURN v_qtd;
END;
$func$;

REVOKE ALL ON FUNCTION public.farol_adicionar_vinculos_os_manuais_tx(text,text,text,text,text,text,text,text[],text,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_adicionar_vinculos_os_manuais_tx(text,text,text,text,text,text,text,text[],text,uuid) TO service_role;
REVOKE ALL ON FUNCTION public.farol_remover_vinculos_os_manuais_tx(text,text[],uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.farol_remover_vinculos_os_manuais_tx(text,text[],uuid) TO service_role;
