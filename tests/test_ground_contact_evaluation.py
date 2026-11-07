import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.ground_contact_evaluation import evaluate_ground_contacts
from custody_watch.ground_contacts import load_ground_contacts
from custody_watch.multicamera_replay import frame_to_dict, iter_synchronized
from custody_watch.types import Point
from tests.ground_contacts_fixture import make_contacts
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import reference, write_json
from tests.test_multicamera_media import make_video_registry


def test_mede_cobertura_sem_liberar_posicao_ou_usar_bbox_como_evidencia(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    path = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["observations"][0]["point_px"] = [160, 210]
    write_json(path, data)
    before = [frame_to_dict(f) for f in iter_synchronized(registry)]

    report = evaluate_ground_contacts(registry, load_ground_contacts(path, registry))

    assert report["schema_version"] == 1
    assert report["counts"] == {
        "observations": 12,
        "labeled": 4,
        "unlabeled": 8,
        "observed_contacts": 2,
        "occluded_contacts": 1,
        "unknown_contacts": 9,
        "contact_projections": 2,
        "projection_abstentions": 10,
        "qualified_positions": 0,
        "association_abstentions": 12,
    }
    rows = report["observations"]
    first = rows[0]
    assert first["contact_projection_m"] == pytest.approx([1.6, 2.1])
    assert first["bbox_projection_m"] == pytest.approx([1.5, 2])
    assert first["contact_provenance"] == "synthetic"
    assert first["image_bounds"] == "synthetic_declared"
    occluded = next(
        r
        for r in rows
        if r["camera_id"] == "cam-a" and r["frame_index"] == 0 and r["local_track_id"] == 9
    )
    assert occluded["contact_status"] == "occluded"
    assert occluded["contact_projection_m"] is None
    assert occluded["projection_refusal"] == "contact_occluded"
    assert all(r["position_m"] is None and r["association_eligible"] is False for r in rows)
    assert before == [frame_to_dict(f) for f in iter_synchronized(registry)]


def test_contato_sem_referencial_comum_e_borda_nao_inventam_posicao_aceita(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    registry = replace(
        registry,
        cameras=(replace(registry.cameras[0], projection_frame_id=None), registry.cameras[1]),
    )
    path = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][1]["observations"][0]["point_px"] = [0, 0]
    write_json(path, data)

    rows = evaluate_ground_contacts(registry, load_ground_contacts(path, registry))["observations"]

    assert rows[0]["contact_projection_m"] is None
    assert rows[0]["projection_refusal"] == "no_common_frame"
    other = next(r for r in rows if r["camera_id"] == "cam-b")
    assert other["at_image_boundary"] is True
    assert other["association_eligible"] is False


def test_dimensoes_declaradas_sao_conferidas_contra_video_local(tmp_path):
    path, _ = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    evidence_path = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    data["cameras"][0]["image_size_px"] = [400, 240]
    for observation in data["cameras"][0]["observations"]:
        observation["provenance"] = "model_prediction"
    write_json(evidence_path, data)
    report = evaluate_ground_contacts(registry, load_ground_contacts(evidence_path, registry))
    first = report["observations"][0]
    assert first["image_bounds"] == "verified_source"
    assert first["contact_provenance"] == "model_prediction"
    assert first["association_eligible"] is False
    data["cameras"][0]["image_size_px"] = [401, 240]
    write_json(evidence_path, data)

    evidence = load_ground_contacts(evidence_path, registry)
    with pytest.raises(ValueError, match="image_size_px"):
        evaluate_ground_contacts(registry, evidence)


def _run_cli(registry, contacts, output):
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts/evaluate_ground_contacts.py"),
            str(registry),
            str(contacts),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_cli_exporta_hashes_proveniencia_contagens_e_nao_sobrescreve(tmp_path):
    registry_path = make_registry(tmp_path / "input")
    registry = load_camera_registry(registry_path)
    contacts_path = make_contacts(tmp_path / "contacts.json", registry)
    output = tmp_path / "result"

    result = _run_cli(registry_path, contacts_path, output)

    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (output / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert summary["registry_sha256"] == registry.manifest_sha256
    assert (
        summary["evidence_sha256"] == load_ground_contacts(contacts_path, registry).manifest_sha256
    )
    assert len(rows) == summary["counts"]["observations"] == 12
    assert all(r["position_m"] is None for r in rows)
    original = (output / "summary.json").read_bytes()
    assert _run_cli(registry_path, contacts_path, output).returncode == 1
    assert (output / "summary.json").read_bytes() == original


def test_cli_recusa_evidencia_invalida_antes_de_criar_saida(tmp_path):
    path = make_registry(tmp_path / "input")
    registry = load_camera_registry(path)
    contacts = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(contacts.read_text(encoding="utf-8"))
    data["registry_sha256"] = "0" * 64
    write_json(contacts, data)
    output = tmp_path / "result"

    result = _run_cli(path, contacts, output)

    assert result.returncode == 1
    assert not output.exists()


def test_fonte_alterada_nao_e_rebaixada_para_dimensoes_desconhecidas(tmp_path):
    path, video = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    evidence_path = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    data["cameras"][0]["image_size_px"] = [400, 240]
    for observation in data["cameras"][0]["observations"]:
        observation["provenance"] = "human_review"
    write_json(evidence_path, data)
    evidence = load_ground_contacts(evidence_path, registry)
    video.write_bytes(b"alterado")

    with pytest.raises(ValueError, match="integridade"):
        evaluate_ground_contacts(registry, evidence)


def test_projecao_de_contato_nao_finita_e_recusada_sem_fallback_para_bbox(tmp_path, monkeypatch):
    registry = load_camera_registry(make_registry(tmp_path))
    path = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["observations"][0]["point_px"] = [160, 210]
    write_json(path, data)
    plane = registry.cameras[0].recording.plane
    project = plane.project
    monkeypatch.setattr(
        plane, "project", lambda x, y: Point(float("nan"), 0) if x == 160 else project(x, y)
    )

    evidence = load_ground_contacts(path, registry)
    with pytest.raises(ValueError, match="contact_projection_m"):
        evaluate_ground_contacts(registry, evidence)


def test_video_offline_preserva_qualificador_de_dimensoes_nao_verificadas(tmp_path):
    path, _ = make_video_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest = tmp_path / "cam-a/manifest.json"
    sequence = json.loads(manifest.read_text(encoding="utf-8"))
    sequence["source"]["file"]["path"] = None
    write_json(manifest, sequence)
    data["cameras"][0]["recording"].update({**reference(manifest), "path": "cam-a/manifest.json"})
    write_json(path, data)
    registry = load_camera_registry(path)
    contacts = make_contacts(tmp_path / "contacts.json", registry)
    data = json.loads(contacts.read_text(encoding="utf-8"))
    for observation in data["cameras"][0]["observations"]:
        observation["provenance"] = "model_prediction"
    write_json(contacts, data)

    report = evaluate_ground_contacts(registry, load_ground_contacts(contacts, registry))

    assert report["observations"][0]["image_bounds"] == "unavailable"
    assert report["observations"][0]["position_m"] is None
    assert report["observations"][0]["association_eligible"] is False
