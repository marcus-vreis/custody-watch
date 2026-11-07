"use strict";

// Parâmetros só selecionam valores já presentes no cadastro e no relógio.
function replayTarget(search, config) {
  const params = new URLSearchParams(search);
  const requestedCameras = params.getAll("camera");
  const camera = requestedCameras.length === 1
    ? config.cameras.find(item => item.camera_id === requestedCameras[0]) : undefined;
  const floor = camera?.floor_id || config.floors[0].floor_id;
  const selected = camera || config.cameras.find(item => item.floor_id === floor);
  const requestedTimes = params.getAll("t");
  const parsed = requestedTimes.length === 1 && requestedTimes[0].trim() !== ""
    ? Number(requestedTimes[0]) : NaN;
  const t = Number.isFinite(parsed) ? parsed : 0;
  return {
    t: Math.max(config.timeline.start_s, Math.min(config.timeline.end_s, t)),
    camera_id: selected?.camera_id || null,
    floor_id: floor
  };
}

// Permite testes com node:test; o painel usa o mesmo script no navegador.
if (typeof module !== "undefined") module.exports = { replayTarget };
