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
let handleSubmit = null;
const form = {
  dataset: { ativo: "12345678-1234-1234-1234-123456789012", atual: "operando" },
  querySelector: s => s.includes("ga-status-mensagem") ? mensagem : s.includes("ga-status-salvar") ? botao : radio,
  addEventListener: (nome, callback) => { if (nome === "submit") handleSubmit = callback; }
};
const element = { querySelector: () => form };
let fallback = 0;
let tooltip = "Informações do ativo";
const marcador = {
  getLatLng: () => ({ lat: -5.08, lng: -42.8 }),
  getTooltip: () => ({getContent: () => tooltip}),
  setTooltipContent: (v) => { tooltip = v; },
  fire: () => { fallback++; }
};
global.L = {
  DomEvent: { disableClickPropagation: () => {}, disableScrollPropagation: () => {} }
};
global.mapa = { on: (nome, callback) => { eventos[nome] = callback; } };
global.window = {
  __GLOBAL_DATA__: { last_object_clicked_count: 2, lat_lng_clicked: null },
  Streamlit: { setComponentValue: (payload) => chamados.push(payload) }
};
eval(ponte);
assert.equal(typeof eventos.popupopen, "function");
eventos.popupopen({ popup: { getElement: () => element, _source: marcador }});
assert.equal(typeof handleSubmit, "function");
const evt = { preventDefault(){}, stopPropagation(){} };
handleSubmit(evt);
assert.equal(chamados.length, 1);
assert.deepEqual(Object.keys(chamados[0]).sort(), [
  "last_clicked", "last_object_clicked_count", "last_object_clicked_tooltip"
]);
assert.equal(chamados[0].last_object_clicked_count, 3);
assert.match(chamados[0].last_object_clicked_tooltip,
  /^PRISMA_STATUS=12345678-1234-1234-1234-123456789012\|parado\|[0-9]{10,16}_[a-z0-9]{4,12}$/);
assert.equal(botao.disabled, true);
assert.equal(fallback, 0);
handleSubmit(evt);
assert.equal(chamados.length, 1, "Dois submits não podem duplicar o PATCH");

// O caminho alternativo não remove o comando da tooltip antes de ser capturado.
const form2 = {
  dataset: { ativo: "12345678-1234-1234-1234-123456789012", atual: "operando" },
  querySelector: form.querySelector,
  addEventListener: (nome, fn) => { if (nome === "submit") handleSubmit = fn; }
};
global.window.Streamlit = null;
eventos.popupopen({popup:{ getElement: () => ({querySelector: () => form2}), _source: marcador }});
handleSubmit(evt);
assert.equal(fallback, 1);
assert.match(tooltip, /^PRISMA_STATUS=/);
console.log("Ponte de status validada.");
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

    def test_codigo_sintaticamente_valido(self):
        ast.parse(FONTE.read_text(encoding="utf-8"), filename=str(FONTE))


if __name__ == "__main__":
    unittest.main()
