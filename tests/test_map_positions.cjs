const test = require("node:test");
const assert = require("node:assert/strict");
const {mapPosition} = require("../src/custody_watch/assets/map_positions.js");

const unknown = {
  position_m: null,
  projected_position_m: [1, 2],
  position_status: "ground_contact_unknown",
  association_eligible: false,
};

test("posição incerta fica oculta por padrão", () => {
  assert.equal(mapPosition(unknown, false), null);
  assert.equal(mapPosition(unknown), null);
});

test("diagnóstico é explícito e separado de localização", () => {
  assert.deepEqual(mapPosition(unknown, true), {point: [1, 2], diagnostic: true});
  assert.equal(unknown.position_m, null);
  assert.equal(unknown.association_eligible, false);
});

test("borda inferior não comprova posição mesmo com diagnóstico", () => {
  assert.deepEqual(mapPosition({...unknown, position_status: "image_border"}, true), {
    point: [1, 2], diagnostic: true,
  });
});

test("sem referencial não existe ponto para desenhar", () => {
  assert.equal(mapPosition({...unknown, projected_position_m: null}, true), null);
});

test("resposta legada sem qualificação não vira localização aceita", () => {
  assert.equal(mapPosition({position_m: [1, 2]}, false), null);
  assert.equal(mapPosition({position_m: [1, 2]}, true), null);
});

for (const point of [[NaN, 0], [0, Infinity], [true, 0], ["1", 0], [1], [1, 2, 3]]) {
  test(`ponto inválido recusado: ${String(point)}`, () => {
    assert.equal(mapPosition({...unknown, projected_position_m: point}, true), null);
  });
}
