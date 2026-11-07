"use strict";

// Copiar apenas o descriptor da imagem, sem emprestar tracks históricos de
// outros quadros. O modal conserva esse instante mesmo após novas respostas.
function inspectionTarget(camera, snapshot) {
  const image = snapshot.image;
  if (!image || camera.camera_id !== snapshot.camera_id || camera.segment_id !== snapshot.segment_id) return null;
  if (!Number.isInteger(image.frame_index) || image.frame_index < 0) return null;
  if (![image.t, image.media_t, image.age_s].every(Number.isFinite) || image.age_s < 0) return null;
  const query = new URLSearchParams({
    camera_id: camera.camera_id,
    segment_id: camera.segment_id,
    frame_index: String(image.frame_index),
    resolution: "source",
  });
  return Object.freeze({
    camera_id: camera.camera_id,
    segment_id: camera.segment_id,
    frame_index: image.frame_index,
    t: image.t,
    media_t: image.media_t,
    age_s: image.age_s,
    historical: image.historical,
    kind: image.kind,
    mode: camera.mode,
    provenance: camera.provenance,
    url: `/api/image?${query}`,
  });
}

function inspectionZoom(value, delta) {
  if (!Number.isFinite(value) || !Number.isFinite(delta)) return 1;
  return Math.max(1, Math.min(4, value + delta));
}

if (typeof module !== "undefined") module.exports = {inspectionTarget, inspectionZoom};
