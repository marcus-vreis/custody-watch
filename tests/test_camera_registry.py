import json
from copy import deepcopy
from pathlib import Path

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.recorded_predictions import load_recording
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import reference, write_json


def test_carrega_ambiente_com_referencial_e_duas_vistas(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))

    assert registry.site_id == "synthetic-hall"
    assert registry.floors[0].coordinate_frame.frame_id == "hall-m"
    assert registry.floors[0].coordinate_frame.unit == "m"
    assert registry.floors[0].coordinate_frame.origin == "Canto sudoeste do hall sintético"
    assert {camera.camera_id for camera in registry.cameras} == {"cam-a", "cam-b"}
    camera = registry.cameras[1]
    assert camera.position_m.x == 9
    assert len(camera.coverage_m) == 4
    assert camera.projection_frame_id == "hall-m"
    assert camera.clock.offset_s == -1
    assert camera.clock.uncertainty_s == 0.05
    assert camera.recording.manifest["sequence_id"] == "cam-b"
    assert camera.recording.source_verified is True
    assert len(registry.manifest_sha256) == 64


@pytest.mark.parametrize(
    "change,diagnostic",
    [
        (lambda m: m.update(schema_version=True), "schema_version"),
        (lambda m: m.update(schema_version=2), "schema_version"),
        (lambda m: m.update(extra="silencioso"), "desconhecidos"),
        (lambda m: m.update(site_id=" "), "site_id"),
        (lambda m: m.update(cameras=[]), "cameras"),
        (lambda m: m.update(floors=[]), "floors"),
        (lambda m: m["cameras"].append(deepcopy(m["cameras"][0])), "camera_id.*duplicado"),
        (lambda m: m["floors"].append(deepcopy(m["floors"][0])), "floor_id.*duplicado"),
        (lambda m: m["cameras"][0].update(floor_id="inexistente"), "floor_id.*inexistente"),
        (lambda m: m["cameras"][0].update(projection_frame_id="outro"), "projection_frame_id"),
        (lambda m: m["cameras"][0].update(segment_id=""), "segment_id"),
        (lambda m: m["cameras"][0].update(position_m=[True, 0]), "position_m"),
        (lambda m: m["cameras"][0].update(position_m=["1", 0]), "position_m"),
        (lambda m: m["cameras"][0].update(position_m=[1]), "position_m"),
        (lambda m: m["cameras"][0].update(coverage_m=[[0, 0], [1, 1]]), "coverage_m"),
        (lambda m: m["cameras"][0].update(coverage_m=[[0, 0], [1, 1], [2, 2]]), "coverage_m"),
        (lambda m: m["cameras"][0]["clock"].update(offset_s=True), "offset_s"),
        (lambda m: m["cameras"][0]["clock"].update(uncertainty_s=-1), "uncertainty_s"),
        (lambda m: m["cameras"][0]["clock"].update(method=""), "method"),
        (lambda m: m["floors"][0]["coordinate_frame"].update(unit="px"), "unit"),
        (lambda m: m["floors"][0]["coordinate_frame"].update(x_axis=""), "x_axis"),
        (lambda m: m["cameras"][0].pop("projection_frame_id"), "ausentes"),
    ],
)
def test_recusa_cadastro_invalido_sem_corrigir_silenciosamente(tmp_path, change, diagnostic):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)

    with pytest.raises(ValueError, match=diagnostic):
        load_camera_registry(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 10**400])
def test_recusa_numeros_nao_finitos_no_cadastro(tmp_path, value):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["clock"]["offset_s"] = value
    write_json(path, data)

    with pytest.raises(ValueError, match="finito"):
        load_camera_registry(path)


def test_referencial_nao_pode_ser_reutilizado_por_outro_andar(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    floor = deepcopy(data["floors"][0])
    floor["floor_id"] = "superior"
    data["floors"].append(floor)
    write_json(path, data)

    with pytest.raises(ValueError, match="frame_id.*duplicado"):
        load_camera_registry(path)


def test_metadados_desconhecidos_ficam_explicitamente_nulos(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0].update(position_m=None, coverage_m=None, projection_frame_id=None)
    data["cameras"][0]["clock"]["uncertainty_s"] = None
    write_json(path, data)

    camera = load_camera_registry(path).cameras[0]

    assert camera.position_m is None
    assert camera.coverage_m is None
    assert camera.projection_frame_id is None
    assert camera.clock.uncertainty_s is None


@pytest.mark.parametrize(
    "change,diagnostic",
    [
        (lambda ref: ref.update(path="missing.json"), "arquivo não encontrado"),
        (lambda ref: ref.update(path="C:/outside.json"), "relativo"),
        (lambda ref: ref.update(size_bytes=True), "size_bytes"),
        (lambda ref: ref.update(size_bytes=1), "tamanho"),
        (lambda ref: ref.update(sha256="0" * 64), "hash"),
        (lambda ref: ref.update(sha256="x"), "sha256"),
    ],
)
def test_referencia_de_gravacao_exige_arquivo_e_integridade(tmp_path, change, diagnostic):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["cameras"][0]["recording"])
    write_json(path, data)

    with pytest.raises(ValueError, match=diagnostic):
        load_camera_registry(path)


def test_hash_do_grupo_nao_substitui_validacao_das_predicoes(tmp_path):
    path = make_registry(tmp_path)
    cache = tmp_path / "cam-a" / "predictions.jsonl"
    cache.write_bytes(cache.read_bytes().replace(b'"track_id": 1', b'"track_id": 2', 1))

    with pytest.raises(ValueError, match="hash"):
        load_camera_registry(path)


def test_recusa_chaves_json_duplicadas(tmp_path):
    path = make_registry(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicada"):
        load_camera_registry(path)


def test_grupo_nao_altera_contrato_estrito_da_sequencia(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest = tmp_path / "cam-a" / "manifest.json"
    assert load_recording(manifest).manifest["schema_version"] == 1
    sequence = json.loads(manifest.read_text(encoding="utf-8"))
    sequence["camera_id"] = "cam-a"
    write_json(manifest, sequence)
    data["cameras"][0]["recording"].update({**reference(manifest), "path": "cam-a/manifest.json"})
    write_json(path, data)

    with pytest.raises(ValueError, match="desconhecidos"):
        load_camera_registry(path)


def test_fixture_versionado_e_portatil_e_carregavel():
    root = Path(__file__).parent / "fixtures" / "multicamera" / "two-cameras"
    registry = load_camera_registry(root / "registry.json")

    assert len(registry.cameras) == 2
    assert all(
        camera.recording.manifest["source"]["kind"] == "synthetic" for camera in registry.cameras
    )
    assert all(b"\r\n" not in path.read_bytes() for path in root.rglob("*.json*"))
