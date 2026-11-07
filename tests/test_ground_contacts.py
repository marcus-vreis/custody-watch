import json
from copy import deepcopy

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.ground_contacts import load_ground_contacts
from tests.ground_contacts_fixture import make_contacts
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import write_json
from tests.test_multicamera_media import make_video_registry


@pytest.fixture
def contact_input(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    path = make_contacts(tmp_path / "contacts.json", registry)
    return path, registry


def test_carrega_evidencia_vinculada_a_camera_segmento_sequencia_e_observacao(contact_input):
    path, registry = contact_input
    evidence = load_ground_contacts(path, registry)

    assert evidence.registry_sha256 == registry.manifest_sha256
    assert len(evidence.manifest_sha256) == 64
    camera = evidence.cameras[0]
    assert camera.camera_id == "cam-a"
    assert camera.segment_id == "segment-1"
    assert camera.sequence_sha256 == registry.cameras[0].recording.manifest_sha256
    assert camera.image_size_px == (800, 600)
    assert camera.observations[0].point_px == (150, 200)
    assert camera.observations[0].status == "observed"
    assert camera.observations[0].provenance == "synthetic"
    assert camera.observations[1].point_px is None
    assert camera.observations[1].status == "occluded"


@pytest.mark.parametrize(
    "change,diagnostic",
    [
        (lambda d: d.update(schema_version=True), "schema_version"),
        (lambda d: d.update(schema_version=2), "schema_version"),
        (lambda d: d.update(extra=1), "desconhecidos"),
        (lambda d: d.update(registry_sha256="0" * 64), "registry_sha256"),
        (lambda d: d.update(registry_sha256="inválido"), "registry_sha256"),
        (lambda d: d.update(cameras={}), "cameras"),
        (lambda d: d["cameras"].append(deepcopy(d["cameras"][0])), "duplicada"),
        (lambda d: d["cameras"][0].update(camera_id="outra"), "camera_id"),
        (lambda d: d["cameras"][0].update(segment_id="outro"), "segment_id"),
        (lambda d: d["cameras"][0].update(sequence_sha256="0" * 64), "sequence_sha256"),
        (lambda d: d["cameras"][0].update(image_size_px=[True, 600]), "image_size_px"),
        (lambda d: d["cameras"][0].update(image_size_px=[0, 600]), "image_size_px"),
        (lambda d: d["cameras"][0].update(image_size_px=[800]), "image_size_px"),
        (lambda d: d["cameras"][0].update(extra=1), "desconhecidos"),
        (
            lambda d: d["cameras"][0]["observations"].append(
                deepcopy(d["cameras"][0]["observations"][0])
            ),
            "duplicada",
        ),
        (lambda d: d["cameras"][0]["observations"][0].update(frame_index=99), "frame_index"),
        (lambda d: d["cameras"][0]["observations"][0].update(frame_index=True), "frame_index"),
        (lambda d: d["cameras"][1]["observations"][0].update(frame_index=2), "observado"),
        (lambda d: d["cameras"][1]["observations"][0].update(frame_index=1), "local_track_id"),
        (lambda d: d["cameras"][0]["observations"][0].update(local_track_id=2), "local_track_id"),
        (lambda d: d["cameras"][0]["observations"][0].update(local_track_id=-1), "local_track_id"),
        (lambda d: d["cameras"][0]["observations"][0].update(status="visible"), "status"),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=None), "point_px"),
        (lambda d: d["cameras"][0]["observations"][1].update(point_px=[200, 200]), "point_px"),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=[800, 0]), "domínio"),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=[0, 600]), "domínio"),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=[-1, 200]), "domínio"),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=[True, 200]), "finito"),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=["1", 200]), "finito"),
        (
            lambda d: d["cameras"][0]["observations"][0].update(point_px=[float("nan"), 200]),
            "finito",
        ),
        (lambda d: d["cameras"][0]["observations"][0].update(point_px=[10**400, 200]), "finito"),
        (
            lambda d: d["cameras"][0]["observations"][0].update(provenance="ground_truth"),
            "provenance",
        ),
        (lambda d: d["cameras"][0]["observations"][0].update(method=" "), "method"),
        (lambda d: d["cameras"][0]["observations"][0].update(extra=1), "desconhecidos"),
    ],
)
def test_recusa_evidencia_incompativel_sem_corrigir_silenciosamente(
    contact_input, change, diagnostic
):
    path, registry = contact_input
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)

    with pytest.raises(ValueError, match=diagnostic):
        load_ground_contacts(path, registry)


def test_nao_aceita_chaves_json_duplicadas(contact_input):
    path, registry = contact_input
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicada"):
        load_ground_contacts(path, registry)


def test_ausencia_de_evidencia_e_permitida_e_nao_significa_contato(contact_input):
    path, registry = contact_input
    write_json(
        path, {"schema_version": 1, "registry_sha256": registry.manifest_sha256, "cameras": []}
    )

    assert load_ground_contacts(path, registry).cameras == ()


def test_fonte_video_nao_aceita_proveniencia_sintetica(tmp_path):
    path, _ = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    evidence = make_contacts(tmp_path / "contacts.json", registry)

    with pytest.raises(ValueError, match="synthetic"):
        load_ground_contacts(evidence, registry)
