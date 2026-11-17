"""Regressões do salvamento pelo popup do mapa da Gestão de Ativos.

Executa o mesmo JavaScript da ponte com um Leaflet e Streamlit simulados.
Não usa dados reais nem executa PATCH no Supabase.
"""
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


FONTE = Path(__file__).resolve().parents[1] / "pages" / "10_Gestao_de_Ativos.py"


def _javascript_pontestatus():
    codigo = FONTE.read_text(encoding="utf-8")
    trecho = re.search(r"class PonteStatusFolium\(MacroElement\):.*?Template\(r\"\"\"(.*?)\"\"\"\)", codigo, re.S)
    if not trecho:
        raise AssertionError("Ponte do popup de status não encontrada")
    corpo = trecho.group(1).split("{% macro script(this, kwargs) %}", 1)[1]
    return corpo.split("{% endmacro %}", 1)[0].replace("{{this._parent.get_name()}}", "globalThis.mapa")


JS_TESTE = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const ponte = fs.readFileSync(0, "utf8");
const eventos = {};
const chamados = [];
const radio = { value: "parado" };
const mensagem = { textContent: "" };
const botao = { disabled: false };
let handlerSubmit = null;
let handlerCapture = false;
let formularioAberto = false;
let fallback = 0;
let tooltip = "Informações do ativo";
const marcador = {
  getLatLng: () => ({ lat: -5.08, lng: -42.8 }),
  getTooltip: () => ({getContent: () => tooltip}),
  setTooltipContent: (v) => { tooltip = v; },
  fire: () => { fallback++; }
};
const form = {
  dataset: { ativo: "12345678-1234-1234-1234-123456789012", atual: "operando" },
  matches: s => s === ".ga-status-form",
  querySelector: s => s.includes("ga-status-mensagem") ? mensagem : s.includes("ga-status-salvar") ? botao : radio
};
const container = {
  addEventListener: (name, fn, capture) => {
    if (name === "submit") { handlerSubmit = fn; handlerCapture = capture; }
  }
};
global.L = { DomEvent: {
  disableClickPropagation: () => {}, disableScrollPropagation: () => {}
}};
global.mapa = {
  _popup: { _source: marcador },
  getContainer: () => container,
  on: (nome, callback) => { eventos[nome] = callback; }
};
global.window = {
  __GLOBAL_DATA__: { last_object_clicked_count: 2, lat_lng_clicked: null },
  Streamlit: { setComponentValue: (payload) => chamados.push(payload) }
};
eval(ponte);
assert.equal(typeof handlerSubmit, "function", "listener deve existir ANTES de abrir popup");
assert.equal(handlerCapture, true, "listener deve capturar primeiro envio");
assert.equal(typeof eventos.popupopen, "function");

// PRIMEIRA submissão sem evento popupopen: antigo código perdia este clique.
const evt = {target: form, preventDefault(){}, stopPropagation(){}};
handlerSubmit(evt);
assert.equal(chamados.length, 1);
assert.deepEqual(Object.keys(chamados[0]).sort(), [
  "last_clicked", "last_object_clicked_count", "last_object_clicked_tooltip"
]);
assert.equal(chamados[0].last_object_clicked_count, 3);
assert.match(chamados[0].last_object_clicked_tooltip,
  /^PRISMA_STATUS=12345678-1234-1234-1234-123456789012\|parado\|[0-9]{10,16}_[a-z0-9]{4,12}$/);
assert.equal(botao.disabled, true);
assert.equal(fallback, 0);
handlerSubmit(evt);
assert.equal(chamados.length, 1, "Dois submits não podem duplicar o PATCH");

// Abertura do popup deve apenas configurar o marcador e impedir propagação.
eventos.popupopen({ popup: { getElement: () => ({
  querySelector: () => form
}), _source: marcador }});
assert.equal(form.__prismaMarcador, marcador);

// Fallback: mesma ação, sem duplicar o envio quando a API direta não existir.
const form2 = {
  dataset: { ativo: "12345678-1234-1234-1234-123456789012", atual: "operando" },
  matches: form.matches,
  querySelector: form.querySelector
};
global.window.Streamlit = null;
handlerSubmit({ ...evt, target: form2 });
assert.equal(fallback, 1);
assert.match(tooltip, /^PRISMA_STATUS=/);

// Status igual ao atual não deve enviar requisição.
const form3 = {
  dataset: { ativo: "12345678-1234-1234-1234-123456789012", atual: "parado" },
  matches: form.matches,
  querySelector: form.querySelector
};
handlerSubmit({ ...evt, target: form3 });
assert.equal(fallback, 1);

// Novo status deve chegar ao servidor já no primeiro clique.
radio.value = "em_observacao";
global.window.Streamlit = { setComponentValue: (payload) => chamados.push(payload) };
const novoFormulario = {
  dataset: { ativo: "12345678-1234-1234-1234-123456789012", atual: "operando" },
  matches: form.matches, querySelector: form.querySelector
};
handlerSubmit({ ...evt, target: novoFormulario });
assert.equal(chamados.length, 2);
assert.match(chamados[1].last_object_clicked_tooltip, /\\|em_observacao\\|/);
console.log("Ponte de status: primeira submissão e Em Observação capturados.");
"""


class TestGestaoAtivosStatusMapa(unittest.TestCase):
    def test_script_popup_enviando_status(self):
        if not shutil.which("node"):
            self.skipTest("Node.js não instalado")
        resultado = subprocess.run(
            ["node", "-e", JS_TESTE], input=_javascript_pontestatus(),
            text=True, capture_output=True, timeout=15, check=False
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)

    def test_gravacao_permanece_servidor_com_permissao_e_versao(self):
        codigo = FONTE.read_text(encoding="utf-8")
        self.assertIn('pode_editar_status = pode_executar_acao("9", "editar") and HISTORICO_DISPONIVEL', codigo)
        self.assertIn('if ativo_id not in visiveis:', codigo)
        self.assertIn('atual = obter_ativo(ativo_id)', codigo)
        self.assertIn('salvo = editar_ativo(ativo_id, campos, atual.get("atualizado_em"))', codigo)
        self.assertIn('_auditar("editar", salvo)', codigo)
        self.assertIn('_limpar_cache()', codigo)
        self.assertIn('key=f"ga_mapa_interativo_{st.session_state.get(\'ga_versao_mapa\', 0)}"', codigo)

    def test_em_observacao_integrado_na_ficha_mapa_e_backend(self):
        import re
        fonte = FONTE.read_text(encoding="utf-8")
        backend = (FONTE.parent.parent / "gestao_ativos_backend.py").read_text(
            encoding="utf-8")
        self.assertIn('"em_observacao": "Em Observação"', fonte)
        self.assertIn('"em_observacao": "#9333EA"', fonte)
        self.assertIn('"em_observacao","inativo","validar"', fonte)
        self.assertIn('"em_observacao"', backend)
        arvore = ast.parse(fonte)
        padroes = [
            no.value.args[0].value for no in ast.walk(arvore)
            if isinstance(no, ast.Assign)
            and any(isinstance(x, ast.Name) and x.id == "sinal_status"
                    for x in no.targets)
            and isinstance(no.value, ast.Call)
            and no.value.args and isinstance(no.value.args[0], ast.Constant)
        ]
        self.assertEqual(len(padroes), 1)
        self.assertIsNotNone(re.fullmatch(
            padroes[0],
            "PRISMA_STATUS=12345678-1234-1234-1234-123456789012"
            "|em_observacao|1763420000000_abcdefgh",
        ))

    def test_selecao_de_ativo_nao_remonta_o_mapa_antes_do_submit(self):
        """Selecionar marcador não pode mudar JS do folium por atributo show.

        O st_folium gera a identidade pelo hash do JavaScript mesmo com key.
        Um popup com show dependente da ficha recria o iframe entre a abertura
        do marcador e o clique em Atualizar status.
        """
        fonte = FONTE.read_text(encoding="utf-8")
        trecho_mapa = fonte.split(
            'pode_editar_status = pode_executar_acao("9", "editar") and HISTORICO_DISPONIVEL', 1
        )[1].split('resultado = st_folium(', 1)[0]
        self.assertNotIn('id_selecionado', trecho_mapa)
        self.assertNotIn('ga_painel', trecho_mapa)
        arvore = ast.parse(trecho_mapa)
        popups = [
            no for no in ast.walk(arvore)
            if isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr == "Popup"
            and isinstance(no.func.value, ast.Name)
            and no.func.value.id == "folium"
        ]
        self.assertEqual(len(popups), 2)  # poço + fechamento 🚧
        self.assertTrue(all(
            all(chave.arg != "show" for chave in popup.keywords)
            for popup in popups
        ), "Popup com show dinâmico reinicia iframe no clique do marcador")
        self.assertIn('_status_popup_html(registro, pode_editar_status)', trecho_mapa)
        # A recriação intencional permanece reservada à gravação confirmada.
        self.assertIn('st.session_state["ga_versao_mapa"] = ', fonte)
        self.assertIn('_limpar_cache()', fonte)

    def test_codigo_sintaticamente_valido(self):
        ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))


if __name__ == "__main__":
    unittest.main()
