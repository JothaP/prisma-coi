// Executa a função real dos clusters em Node para validar média, cor e contagem.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const codigo = fs.readFileSync("bp_agrupamento_marcadores.py", "utf8");
const encontrada = codigo.match(/FUNCAO_ICONE_CLUSTER = r"""([\s\S]*?)"""/);
assert.ok(encontrada, "Função JS de agrupamento não encontrada");

const L = {
  divIcon: (obj) => obj,
  point: (x, y) => ({ x, y }),
};
const criarIcone = vm.runInNewContext("(" + encontrada[1] + ")", { L });

function gerarGrupo(medicoes, categorias) {
  const filhos = medicoes.map((pressao, i) => ({
    options: { bpPressureMca: pressao, bpPressureRank: categorias[i] },
  }));
  return criarIcone({
    getAllChildMarkers: () => filhos,
    getChildCount: () => filhos.length,
  });
}

// Média 10 MCA: 0 + 10 + 20. A cor continua a pior faixa (vermelho).
const grupoComZero = gerarGrupo([0, 10, 20], [5, 2, 1]);
assert.match(grupoComZero.html, /<span class="bp-cluster-numero">10,0<\/span>/);
assert.match(grupoComZero.html, /<span class="bp-cluster-unidade">MCA<\/span>/);
assert.match(grupoComZero.html, /--bp-cluster-color:#FF5C60/);
assert.match(grupoComZero.html, /3 de 3 medições/);
assert.match(grupoComZero.html, /0 MCA: 1/);
assert.doesNotMatch(grupoComZero.html, /class="bp-cluster-numero">3<\/span>/);

// Decimais e conversão pt-BR sem arredondar cada medição previamente.
const decimal = gerarGrupo([1, 4], [4, 4]);
assert.match(decimal.html, /<span class="bp-cluster-numero">2,5<\/span>/);
assert.match(decimal.html, /--bp-cluster-color:#F8DC00/);
assert.match(decimal.html, /2 de 2 medições/);

const normal = gerarGrupo([12, 14, 16], [2, 2, 1]);
assert.match(normal.html, /<span class="bp-cluster-numero">14,0<\/span>/);
assert.match(normal.html, /--bp-cluster-color:#2563EB/);

// Segurança: dados ausentes jamais viram pressão zero por coerção implícita.
const incompleto = gerarGrupo([undefined, 8], [3, 3]);
assert.match(incompleto.html, /<span class="bp-cluster-numero">8,0<\/span>/);
assert.match(incompleto.html, /1 de 2 medições/);

const negativos = gerarGrupo([-1, 5], [4, 4]);
assert.match(negativos.html, /<span class="bp-cluster-numero">2,0<\/span>/);

console.log("Média em MCA, criticidade, contagem, decimais e ausentes: OK");
