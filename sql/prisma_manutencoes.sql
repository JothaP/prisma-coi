
-- PRISMA: manutenção global e manutenção por módulo, sem alterar dados operacionais.
CREATE TABLE IF NOT EXISTS public.prisma_manutencoes (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 escopo text NOT NULL CHECK (escopo IN ('global','modulo')),
 modulo text,
 inicio timestamptz NOT NULL,
 fim timestamptz,
 motivo text NOT NULL CHECK (length(btrim(motivo)) BETWEEN 5 AND 500),
 mensagem text NOT NULL DEFAULT '',
 criado_por uuid NOT NULL REFERENCES public.prisma_perfis(id),
 criado_em timestamptz NOT NULL DEFAULT now(),
 cancelado_em timestamptz,
 cancelado_por uuid REFERENCES public.prisma_perfis(id),
 CONSTRAINT manutencao_escopo_modulo CHECK (
   (escopo='global' AND modulo IS NULL)
   OR (escopo='modulo' AND modulo IN
       ('1','2','3','4','5','6','7','8','9','teste'))
 ),
 CONSTRAINT manutencao_periodo CHECK (fim IS NULL OR fim>inicio),
 CONSTRAINT manutencao_mensagem CHECK (length(mensagem)<=600)
);
CREATE INDEX IF NOT EXISTS idx_prisma_manutencoes_ativas
 ON public.prisma_manutencoes(inicio, fim, escopo, modulo)
 WHERE cancelado_em IS NULL;
ALTER TABLE public.prisma_manutencoes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.prisma_manutencoes FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON TABLE public.prisma_manutencoes TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.prisma_manutencoes_id_seq TO service_role;

CREATE OR REPLACE FUNCTION public.prisma_agendar_manutencao_tx(
  p_escopo text, p_modulo text, p_inicio timestamptz, p_fim timestamptz,
  p_motivo text, p_mensagem text, p_autor uuid
) RETURNS bigint
LANGUAGE plpgsql SECURITY INVOKER
SET search_path TO ''
AS $func$
DECLARE
 v_id bigint;
 v_modulo text;
BEGIN
 IF NOT EXISTS (
    SELECT 1 FROM public.prisma_perfis
    WHERE id=p_autor AND status='ativo' AND perfil='admin'
 ) THEN RAISE EXCEPTION 'MANUTENCAO_SEM_PERMISSAO'; END IF;
 v_modulo:=CASE WHEN p_escopo='global' THEN NULL ELSE p_modulo END;
 IF p_inicio IS NULL OR p_inicio < now()-interval '2 minutes'
    OR (p_fim IS NOT NULL AND p_fim<=p_inicio)
    OR p_escopo NOT IN ('global','modulo') OR p_escopo IS NULL
    OR (p_escopo='modulo' AND
        coalesce(v_modulo,'') NOT IN ('1','2','3','4','5','6','7','8','9','teste'))
    OR length(btrim(coalesce(p_motivo,''))) NOT BETWEEN 5 AND 500
    OR length(coalesce(p_mensagem,''))>600
 THEN RAISE EXCEPTION 'MANUTENCAO_PARAMETROS_INVALIDOS'; END IF;

 PERFORM pg_advisory_xact_lock(hashtext('PRISMA_MANUTENCAO:' || p_escopo || ':' || coalesce(v_modulo,'')));
 IF EXISTS (
   SELECT 1 FROM public.prisma_manutencoes
   WHERE cancelado_em IS NULL
     AND escopo=p_escopo AND modulo IS NOT DISTINCT FROM v_modulo
     AND tstzrange(inicio,fim,'[)') && tstzrange(p_inicio,p_fim,'[)')
 ) THEN RAISE EXCEPTION 'MANUTENCAO_CONFLITO_PERIODO'; END IF;
 INSERT INTO public.prisma_manutencoes (
   escopo,modulo,inicio,fim,motivo,mensagem,criado_por
 ) VALUES (
   p_escopo,v_modulo,p_inicio,p_fim,btrim(p_motivo),btrim(coalesce(p_mensagem,'')),p_autor
 ) RETURNING id INTO v_id;
 INSERT INTO public.prisma_auditoria(autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
 VALUES(p_autor,'criar',
  'Manutenção '||p_escopo||coalesce(' do módulo '||v_modulo,'')||
  ' agendada de '||to_char(p_inicio AT TIME ZONE 'America/Fortaleza','DD/MM/YYYY HH24:MI')||
  ' até '||coalesce(to_char(p_fim AT TIME ZONE 'America/Fortaleza','DD/MM/YYYY HH24:MI'),'suspensão manual')||
  '. Motivo: '||btrim(p_motivo),
  'administracao','CRIACAO','SUCESSO',v_id::text);
 RETURN v_id;
END;
$func$;

CREATE OR REPLACE FUNCTION public.prisma_cancelar_manutencao_tx(
 p_id bigint, p_autor uuid
) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER
SET search_path TO ''
AS $func$
DECLARE v_escopo text; v_modulo text; v_inicio timestamptz; v_fim timestamptz;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.prisma_perfis
     WHERE id=p_autor AND status='ativo' AND perfil='admin')
 THEN RAISE EXCEPTION 'MANUTENCAO_SEM_PERMISSAO'; END IF;
 UPDATE public.prisma_manutencoes
 SET cancelado_em=now(),cancelado_por=p_autor
 WHERE id=p_id AND cancelado_em IS NULL
 RETURNING escopo,modulo,inicio,fim INTO v_escopo,v_modulo,v_inicio,v_fim;
 IF NOT FOUND THEN RAISE EXCEPTION 'MANUTENCAO_NAO_ENCONTRADA_OU_ENCERRADA'; END IF;
 INSERT INTO public.prisma_auditoria(autor_id,acao,detalhes,modulo,tipo,resultado,registro_id)
 VALUES(p_autor,'editar','Manutenção '||v_escopo||
   coalesce(' do módulo '||v_modulo,'')||' encerrada/cancelada antecipadamente.',
   'administracao','ALTERACAO','SUCESSO',p_id::text);
 RETURN true;
END;
$func$;

REVOKE ALL ON FUNCTION public.prisma_agendar_manutencao_tx(text,text,timestamptz,timestamptz,text,text,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.prisma_agendar_manutencao_tx(text,text,timestamptz,timestamptz,text,text,uuid) TO service_role;
REVOKE ALL ON FUNCTION public.prisma_cancelar_manutencao_tx(bigint,uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.prisma_cancelar_manutencao_tx(bigint,uuid) TO service_role;
