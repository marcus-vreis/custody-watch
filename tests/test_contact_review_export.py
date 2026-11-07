import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.ground_contacts import load_ground_contacts
from custody_watch.multicamera_view import ReplayView
from tests.test_multicamera_media import make_video_registry


@pytest.mark.parametrize(
    "status,point", [("observed", [150, 200]), ("occluded", None), ("unknown", None)]
)
def test_exportacao_do_modulo_do_painel_e_aceita_pelo_leitor_estrito(tmp_path, status, point):
    registry_path, _ = make_video_registry(tmp_path)
    registry = load_camera_registry(registry_path)
    view = ReplayView(registry)
    payload = {
        "config": view.config(),
        "camera": view.config()["cameras"][0],
        "snapshot": view.snapshot(0)["cameras"][0],
        "status": status,
        "point": point,
    }
    node = shutil.which("node")
    assert node, "Os testes de interoperabilidade do painel exigem Node.js."
    module = Path(__file__).parents[1] / "src/custody_watch/assets/contact_review.js"
    script = """
const review = require(process.argv[1]);
const data = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const context = review.createReviewContext(data.config, data.camera, data.snapshot);
const draft = review.createDraft(context, [400,240], 1, {
  status: data.status, point_px: data.point, provenance: 'model_prediction',
  method: 'Estimativa simulada no teste de interoperabilidade.'
});
const store = new review.ReviewStore(data.config.registry_sha256);
store.add(draft);
process.stdout.write(JSON.stringify(store.export()));
"""
    result = subprocess.run(
        [node, "-e", script, str(module)],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=15,
    )
    exported = tmp_path / "exported.json"
    exported.write_text(result.stdout, encoding="utf-8")

    evidence = load_ground_contacts(exported, registry)

    assert evidence.registry_sha256 == registry.manifest_sha256
    assert evidence.cameras[0].sequence_sha256 == registry.cameras[0].recording.manifest_sha256
    observation = evidence.cameras[0].observations[0]
    assert observation.local_track_id == 1
    assert observation.frame_index == 0
    assert observation.status == status
    assert observation.point_px == (None if point is None else tuple(point))
    assert observation.provenance == "model_prediction"
