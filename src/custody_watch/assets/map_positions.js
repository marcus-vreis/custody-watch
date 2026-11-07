"use strict";

// O cache v1 não contém evidência de contato com o chão. Nunca promover a
// base de uma caixa (incluindo oracle) a localização para associação.
function mapPosition(track, showDiagnostics = false) {
  const statuses = ["ground_contact_unknown", "image_border"];
  const point = track.projected_position_m;
  if (showDiagnostics !== true || !statuses.includes(track.position_status)) return null;
  if (!Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite)) return null;
  return {point, diagnostic: true};
}

if (typeof module !== "undefined") module.exports = {mapPosition};
