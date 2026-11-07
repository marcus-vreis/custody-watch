import json
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.contact_review_queue import prepare_contact_review_queue
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import reference, rewrite_cache, write_json
from tests.test_multicamera_media import make_video_registry


def refresh_registry(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for camera in data["cameras"]:
        manifest = path.parent / camera["recording"]["path"]
        camera["recording"].update(reference(manifest))
        camera["recording"]["path"] = f"{camera['camera_id']}/manifest.json"
    write_json(path, data)


def queue(path, **kwargs):
    return prepare_contact_review_queue(load_camera_registry(path), seed=7, **kwargs)


def test_fila_e_repetivel_vinculada_e_nao_cria_evidencia(tmp_path):
    path, _ = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    result = queue(path, frames_per_camera=2, min_gap_s=0)

    assert result == queue(path, frames_per_camera=2, min_gap_s=0)
    assert result["schema_version"] == 1
    assert result["kind"] == "ground_contact_review_queue"
    assert result["registry_sha256"] == registry.manifest_sha256
    assert result["selection"] == {
        "algorithm": "sha256_rank_greedy_gap_v1",
        "seed": 7,
        "frames_per_camera": 2,
        "min_gap_s": 0.0,
        "unit": "camera_frame",
    }
    camera = result["cameras"][0]
    assert camera["camera_id"] == "cam-a"
    assert camera["segment_id"] == "segment-1"
    assert camera["sequence_sha256"] == registry.cameras[0].recording.manifest_sha256
    assert camera["prediction_provenance"] == "detector"
    assert camera["candidate_frames"] == 4
    assert camera["selected_frames"] == len(camera["frames"]) == 2
    assert result["excluded_cameras"] == [{"camera_id": "cam-b", "reason": "schematic"}]
    for frame in camera["frames"]:
        assert set(frame) == {"frame_index", "media_t", "global_t", "local_track_ids"}
        assert frame["local_track_ids"] == [1]  # mala #9 não entra na fila de pessoas
        assert frame["media_t"] == frame["global_t"] == frame["frame_index"]
    indices = [frame["frame_index"] for frame in camera["frames"]]
    assert indices == sorted(indices)


def test_intervalo_e_cota_nao_sao_relaxados_quando_faltam_quadros(tmp_path):
    path, _ = make_video_registry(tmp_path)
    frames = queue(path, frames_per_camera=10, min_gap_s=2)["cameras"][0]["frames"]
    assert len(frames) == 2
    assert all(abs(a["global_t"] - b["global_t"]) >= 2 for a, b in combinations(frames, 2))
    assert queue(path, frames_per_camera=10, min_gap_s=100)["cameras"][0]["selected_frames"] == 1
    assert queue(path, frames_per_camera=10, min_gap_s=0)["cameras"][0]["selected_frames"] == 4


def test_missing_vazio_e_quadros_sem_pessoas_ficam_fora(tmp_path):
    path, _ = make_video_registry(tmp_path)
    folder = tmp_path / "cam-a"
    rows = [json.loads(line) for line in (folder / "predictions.jsonl").read_text().splitlines()]
    rows[0].update(status="missing", detections=[])
    rows[1]["detections"] = [rows[1]["detections"][1]]
    rows[2]["detections"] = []
    rewrite_cache(folder, rows)
    refresh_registry(path)

    camera = queue(path)["cameras"][0]

    assert camera["candidate_frames"] == camera["selected_frames"] == 1
    assert camera["frames"] == [
        {"frame_index": 3, "media_t": 3.0, "global_t": 3.0, "local_track_ids": [1]}
    ]


def test_tempo_global_e_origem_oracle_permanecem_explicitos(tmp_path):
    path, _ = make_video_registry(tmp_path)
    folder = tmp_path / "cam-a"
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["predictions"]["provenance"] = "ground_truth"
    manifest["mode"] = "oracle"
    write_json(folder / "manifest.json", manifest)
    refresh_registry(path)
    data = json.loads(path.read_text())
    data["cameras"][0]["clock"]["offset_s"] = -10
    write_json(path, data)

    camera = queue(path, frames_per_camera=10, min_gap_s=0)["cameras"][0]

    assert camera["prediction_provenance"] == "ground_truth"
    assert [frame["global_t"] for frame in camera["frames"]] == [-10, -9, -8, -7]
    assert [frame["media_t"] for frame in camera["frames"]] == [0, 1, 2, 3]


def test_sem_video_local_elegivel_recusa_fila(tmp_path):
    path = make_registry(tmp_path)
    with pytest.raises(ValueError, match="vídeo local"):
        queue(path)


def test_camera_com_video_offline_e_excluida_sem_emprestar_quadros(tmp_path):
    path, _ = make_video_registry(tmp_path)
    folder = tmp_path / "cam-b"
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["source"].update(
        kind="video", file={"path": None, "size_bytes": 0, "sha256": "0" * 64}
    )
    manifest["predictions"]["provenance"] = "detector"
    write_json(folder / "manifest.json", manifest)
    refresh_registry(path)

    result = queue(path)

    assert [camera["camera_id"] for camera in result["cameras"]] == ["cam-a"]
    assert result["excluded_cameras"] == [{"camera_id": "cam-b", "reason": "unavailable"}]


def test_seed_muda_prioridade_sem_duplicar_quadros(tmp_path):
    path, _ = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    selections = set()
    for seed in range(20):
        result = prepare_contact_review_queue(registry, seed=seed, frames_per_camera=1)
        frames = result["cameras"][0]["frames"]
        assert len(frames) == 1
        selections.add(frames[0]["frame_index"])
    assert len(selections) > 1


def test_id_fora_da_precisao_inteira_do_painel_e_recusado(tmp_path):
    path, _ = make_video_registry(tmp_path)
    folder = tmp_path / "cam-a"
    rows = [json.loads(line) for line in (folder / "predictions.jsonl").read_text().splitlines()]
    rows[0]["detections"][0]["track_id"] = 2**53
    rewrite_cache(folder, rows)
    refresh_registry(path)

    with pytest.raises(ValueError, match="local_track_id do painel"):
        queue(path)


@pytest.mark.parametrize(
    "params",
    [
        {"seed": True},
        {"seed": -1},
        {"seed": 2**32},
        {"seed": 0.5},
        {"frames_per_camera": True},
        {"frames_per_camera": 0},
        {"frames_per_camera": 1001},
        {"frames_per_camera": 1.5},
        {"min_gap_s": True},
        {"min_gap_s": -1},
        {"min_gap_s": float("nan")},
        {"min_gap_s": float("inf")},
        {"min_gap_s": "1"},
    ],
)
def test_parametros_invalidos_sao_recusados(tmp_path, params):
    path, _ = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    with pytest.raises(ValueError, match=next(iter(params))):
        prepare_contact_review_queue(registry, **{"seed": 7, **params})


def test_cli_preserva_arquivo_existente_e_publica_fila_nova(tmp_path):
    path, _ = make_video_registry(tmp_path)
    script = Path(__file__).parents[1] / "scripts/prepare_contact_review.py"
    output = tmp_path / "queue.json"
    command = [sys.executable, str(script), str(path), "--seed", "7", "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    original = output.read_bytes()
    assert json.loads(original) == queue(path)
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert second.returncode == 1
    assert "existe" in second.stderr
    assert output.read_bytes() == original


def test_cli_sem_candidatos_nao_cria_arquivo(tmp_path):
    path = make_registry(tmp_path)
    output = tmp_path / "queue.json"
    script = Path(__file__).parents[1] / "scripts/prepare_contact_review.py"
    result = subprocess.run(
        [sys.executable, str(script), str(path), "--seed", "7", "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert not output.exists()
