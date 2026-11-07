"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { replayTarget } = require("../src/custody_watch/assets/replay_link.js");

const config = {
  floors: [{floor_id:"ground"}, {floor_id:"upper"}],
  cameras: [
    {camera_id:"Camera_0008",floor_id:"ground"},
    {camera_id:"Camera_0019",floor_id:"ground"},
    {camera_id:"Camera_0020",floor_id:"upper"}
  ],
  timeline: {start_s:-0.5,end_s:3.5}
};

test("abre o caso da NVIDIA na câmera e instante solicitados", () => {
  assert.deepEqual(replayTarget("?t=2.5&camera=Camera_0019", config), {
    t:2.5, camera_id:"Camera_0019", floor_id:"ground"
  });
});

test("seleciona o andar da câmera conhecida", () => {
  assert.equal(replayTarget("?camera=Camera_0020", config).floor_id,"upper");
});

test("mantém o início padrão sem parâmetros", () => {
  assert.deepEqual(replayTarget("",config), {t:0,camera_id:"Camera_0008",floor_id:"ground"});
});

for (const query of ["?t=NaN", "?t=Infinity", "?t=abc", "?t=", "?t=2&t=3"]) {
  test(`recusa instante inválido ou ambíguo: ${query}`, () => {
    assert.equal(replayTarget(query,config).t,0);
  });
}

test("limita instante ao intervalo existente", () => {
  assert.equal(replayTarget("?t=-10",config).t,-0.5);
  assert.equal(replayTarget("?t=100",config).t,3.5);
});

test("preserva instante negativo válido", () => {
  assert.equal(replayTarget("?t=-0.25",config).t,-0.25);
});

test("não seleciona câmera inexistente ou parâmetro repetido", () => {
  assert.equal(replayTarget("?camera=unknown",config).camera_id,"Camera_0008");
  assert.equal(replayTarget("?camera=Camera_0019&camera=Camera_0020",config).camera_id,"Camera_0008");
});
