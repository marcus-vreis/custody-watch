"use strict";

const ContactReview = (() => {
  const inspector = typeof module !== "undefined" && module.exports
    ? require("./camera_inspector.js") : {inspectionTarget};
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const vector = (value, size) => Array.isArray(value) && value.length === size && value.every(Number.isFinite);
  const text = value => typeof value === "string" && value.trim() !== "";
  const fail = message => { throw new Error(message); };
  const imageSize = value => Array.isArray(value) && value.length === 2 && value.every(v => integer(v) && v > 0);
  const freezePoint = point => point === null ? null : Object.freeze([...point]);
  const key = (context, id) => JSON.stringify([context.registry_sha256, context.camera_id, context.segment_id, context.sequence_sha256, context.frame_index, id]);

  function copyTrack(track, camera) {
    const id = track?.track_key;
    if (!id || id.camera_id !== camera.camera_id || id.segment_id !== camera.segment_id || !integer(id.local_track_id)) return null;
    if (!vector(track.bbox, 4) || track.bbox[2] <= track.bbox[0] || track.bbox[3] <= track.bbox[1]) return null;
    if (!text(track.cls) || !text(track.position_status) || track.position_m !== null || track.association_eligible !== false) return null;
    if (track.projected_position_m !== null && !vector(track.projected_position_m, 2)) return null;
    return Object.freeze({
      local_track_id: id.local_track_id, cls: track.cls,
      bbox: Object.freeze([...track.bbox]),
      projected_position_m: freezePoint(track.projected_position_m),
      position_status: track.position_status,
    });
  }

  function createReviewContext(config, camera, snapshot) {
    const target = inspector.inspectionTarget(camera, snapshot);
    if (!target || target.kind !== "video" || camera.source_kind !== "video" || !integer(target.frame_index)) return null;
    if (!hash(config.registry_sha256) || !hash(camera.sequence_sha256)) return null;
    if (target.registry_sha256 !== config.registry_sha256 || target.sequence_sha256 !== camera.sequence_sha256) return null;
    if (!Array.isArray(snapshot.image.tracks)) return null;
    const tracks = snapshot.image.tracks.map(track => copyTrack(track, camera));
    if (tracks.some(track => track === null)) return null;
    if (new Set(tracks.map(track => track.local_track_id)).size !== tracks.length) return null;
    tracks.sort((a, b) => a.local_track_id - b.local_track_id);
    return Object.freeze({...target, tracks: Object.freeze(tracks)});
  }

  function sourcePoint(clientX, clientY, rect, size) {
    if (!rect || !imageSize(size)) return null;
    if (![clientX, clientY, rect.left, rect.top, rect.width, rect.height].every(Number.isFinite)) return null;
    if (rect.width <= 0 || rect.height <= 0) return null;
    const point = [(clientX - rect.left) * size[0] / rect.width, (clientY - rect.top) * size[1] / rect.height];
    return point.every((v, i) => v >= 0 && v < size[i]) ? point : null;
  }

  function normalizeRecord(record) {
    if (!hash(record.registry_sha256) || !hash(record.sequence_sha256) || !text(record.camera_id) || !text(record.segment_id)) fail("Vínculo com o ambiente inválido.");
    if (!integer(record.frame_index) || !integer(record.local_track_id)) fail("ID ou quadro inválido.");
    if (!imageSize(record.image_size_px)) fail("Dimensões da imagem indisponíveis.");
    if (!["observed", "occluded", "unknown"].includes(record.status)) fail("Escolha o estado do contato.");
    if (record.status === "observed") {
      if (!vector(record.point_px, 2) || record.point_px.some((v, i) => v < 0 || v >= record.image_size_px[i])) fail("Ponto fora da imagem original ou indisponível.");
    } else if (record.point_px !== null) fail("Este estado exige ponto nulo.");
    if (!["model_prediction", "human_review"].includes(record.provenance)) fail("Escolha a origem da anotação.");
    if (record.provenance === "human_review" && record.human_confirmed !== true) fail("Declare a revisão pessoal para usar essa origem.");
    if (!text(record.method)) fail("Descreva o método da anotação.");
    return Object.freeze({
      registry_sha256: record.registry_sha256, camera_id: record.camera_id,
      segment_id: record.segment_id, sequence_sha256: record.sequence_sha256,
      frame_index: record.frame_index, local_track_id: record.local_track_id,
      image_size_px: Object.freeze([...record.image_size_px]),
      status: record.status, point_px: freezePoint(record.point_px),
      provenance: record.provenance, method: record.method.trim(),
      human_confirmed: record.provenance === "human_review" && record.human_confirmed === true,
    });
  }

  function createDraft(context, size, trackId, input) {
    if (!context?.tracks.some(track => track.local_track_id === trackId)) fail("Selecione um ID deste quadro.");
    return normalizeRecord({
      registry_sha256: context.registry_sha256, camera_id: context.camera_id,
      segment_id: context.segment_id, sequence_sha256: context.sequence_sha256,
      frame_index: context.frame_index, local_track_id: trackId, image_size_px: size,
      status: input.status, point_px: input.point_px, provenance: input.provenance,
      method: input.method, human_confirmed: input.human_confirmed,
    });
  }

  function contactsExport(registrySha256, records) {
    if (!hash(registrySha256)) fail("Vínculo com o ambiente inválido.");
    const cameras = new Map(), seen = new Set();
    for (const value of records) {
      const record = normalizeRecord(value);
      if (record.registry_sha256 !== registrySha256) fail("Rascunho pertence a outro ambiente.");
      const id = key(record, record.local_track_id);
      if (seen.has(id)) fail("Remova a anotação anterior antes de substituí-la.");
      seen.add(id);
      if (!cameras.has(record.camera_id)) cameras.set(record.camera_id, {
        camera_id: record.camera_id, segment_id: record.segment_id,
        sequence_sha256: record.sequence_sha256, image_size_px: [...record.image_size_px], observations: [],
      });
      const camera = cameras.get(record.camera_id);
      if (camera.segment_id !== record.segment_id || camera.sequence_sha256 !== record.sequence_sha256) fail("Câmera vinculada a outro segmento ou sequência.");
      if (camera.image_size_px.some((v, i) => v !== record.image_size_px[i])) fail("Dimensões incompatíveis para esta câmera.");
      camera.observations.push({
        frame_index: record.frame_index, local_track_id: record.local_track_id,
        status: record.status, point_px: record.point_px === null ? null : [...record.point_px],
        provenance: record.provenance, method: record.method,
      });
    }
    const ordered = [...cameras.values()].sort((a, b) => a.camera_id < b.camera_id ? -1 : a.camera_id > b.camera_id ? 1 : 0);
    for (const camera of ordered) camera.observations.sort((a, b) => a.frame_index - b.frame_index || a.local_track_id - b.local_track_id);
    return {schema_version: 1, registry_sha256: registrySha256, cameras: ordered};
  }

  class ReviewStore {
    #records = new Map();
    constructor(registrySha256, limit = 1000) {
      if (!hash(registrySha256) || !integer(limit) || limit < 1 || limit > 1000) fail("Limite ou ambiente inválido.");
      this.registrySha256 = registrySha256;
      this.limit = limit;
      Object.freeze(this);
    }
    get size() { return this.#records.size; }
    get(context, id) { return this.#records.get(key(context, id)) || null; }
    remove(context, id) { return this.#records.delete(key(context, id)); }
    add(value) {
      const record = normalizeRecord(value), id = key(record, record.local_track_id);
      if (this.#records.has(id)) fail("Remova a anotação anterior antes de substituí-la.");
      if (this.#records.size >= this.limit) fail("Limite de rascunho atingido; exporte antes de recarregar a página.");
      contactsExport(this.registrySha256, [...this.#records.values(), record]);
      this.#records.set(id, record);
    }
    export() { return contactsExport(this.registrySha256, [...this.#records.values()]); }
  }
  return {createReviewContext, sourcePoint, createDraft, contactsExport, ReviewStore};
})();

if (typeof module !== "undefined") module.exports = ContactReview;
