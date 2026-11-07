const test = require("node:test");
const assert = require("node:assert/strict");
const {inspectionTarget, inspectionZoom} = require("../src/custody_watch/assets/camera_inspector.js");

const camera = {camera_id: "cam-a", segment_id: "segment-1", mode: "oracle", provenance: "ground_truth"};
function snapshot() {
  return {
    camera_id: "cam-a", segment_id: "segment-1", status: "missing", frame_index: 2,
    image: {
      url: "/api/image?camera_id=cam-a&segment_id=segment-1&frame_index=1",
      frame_index: 1, t: 1, media_t: 2, age_s: 1.5, historical: true, kind: "video",
    },
    last_tracks: [{track_key: {local_track_id: 9}, t: 0}],
  };
}

test("inspeção usa o quadro da imagem histórica, preservando segmento e proveniência", () => {
  const target = inspectionTarget(camera, snapshot());
  assert.equal(target.frame_index, 1);
  assert.equal(target.camera_id, "cam-a");
  assert.equal(target.segment_id, "segment-1");
  assert.equal(target.t, 1);
  assert.equal(target.media_t, 2);
  assert.equal(target.age_s, 1.5);
  assert.equal(target.historical, true);
  assert.equal(target.mode, "oracle");
  assert.equal(target.provenance, "ground_truth");
  assert.equal(target.kind, "video");
  const url = new URL(target.url, "http://localhost");
  assert.equal(url.pathname, "/api/image");
  assert.equal(url.searchParams.get("resolution"), "source");
  assert.equal(url.searchParams.get("frame_index"), "1");
  assert.equal("tracks" in target, false);
});

test("a inspeção fica congelada mesmo após atualizar o estado do replay", () => {
  const current = snapshot();
  const target = inspectionTarget(camera, current);
  current.image.frame_index = 3;
  current.image.age_s = 0;
  current.image.url = "/outra";
  assert.equal(target.frame_index, 1);
  assert.equal(target.age_s, 1.5);
  assert.equal(Object.isFrozen(target), true);
});

test("sem imagem não existe inspeção", () => {
  assert.equal(inspectionTarget(camera, {...snapshot(), image: null}), null);
});

test("câmera ou segmento divergente não herda imagem", () => {
  assert.equal(inspectionTarget({...camera, camera_id: "cam-b"}, snapshot()), null);
  assert.equal(inspectionTarget({...camera, segment_id: "novo"}, snapshot()), null);
});

test("esquema sintético permanece identificado como esquema", () => {
  const state = snapshot();
  state.image.kind = "schematic";
  assert.equal(inspectionTarget(camera, state).kind, "schematic");
});

test("URL é construída pelos IDs, sem reutilizar parâmetros arbitrários", () => {
  const state = snapshot();
  state.image.url = "https://outro.example/arquivo?path=segredo";
  const url = new URL(inspectionTarget(camera, state).url, "http://localhost");
  assert.equal(url.origin, "http://localhost");
  assert.equal(url.searchParams.has("path"), false);
  assert.equal(url.searchParams.get("camera_id"), "cam-a");
});

test("zoom respeita limites e ajuste inicial", () => {
  assert.equal(inspectionZoom(1, -1), 1);
  assert.equal(inspectionZoom(4, 1), 4);
  assert.equal(inspectionZoom(1, 0.5), 1.5);
  assert.equal(inspectionZoom(3, -0.5), 2.5);
  assert.equal(inspectionZoom(NaN, 1), 1);
});

for (const frame_index of [NaN, -1, 1.5, undefined, true]) {
  test(`índice de imagem inválido não abre outra imagem: ${String(frame_index)}`, () => {
    const state = snapshot();
    state.image.frame_index = frame_index;
    assert.equal(inspectionTarget(camera, state), null);
  });
}

test("relógio inválido não é apresentado como imagem válida", () => {
  const state = snapshot();
  state.image.t = Infinity;
  assert.equal(inspectionTarget(camera, state), null);
});

test("inspeção preserva os hashes que vinculam a imagem", () => {
  const state = snapshot();
  state.image.registry_sha256 = "a".repeat(64);
  state.image.sequence_sha256 = "b".repeat(64);
  const target = inspectionTarget(camera, state);
  const url = new URL(target.url, "http://localhost");
  assert.equal(url.searchParams.get("registry_sha256"), state.image.registry_sha256);
  assert.equal(url.searchParams.get("sequence_sha256"), state.image.sequence_sha256);
  assert.equal(target.registry_sha256, state.image.registry_sha256);
  assert.equal(target.sequence_sha256, state.image.sequence_sha256);
});

test("hash incompleto ou inválido impede inspeção com vínculo incorreto", () => {
  const state = snapshot();
  state.image.registry_sha256 = "a".repeat(64);
  assert.equal(inspectionTarget(camera, state), null);
  state.image.sequence_sha256 = "inválido";
  assert.equal(inspectionTarget(camera, state), null);
});
