import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_replay import frame_to_dict, iter_synchronized
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import reference, rewrite_cache, write_json


def test_offsets_alinham_instantes_sem_depender_da_ordem_das_cameras(tmp_path):
    path = make_registry(tmp_path)
    first = [frame_to_dict(f) for f in iter_synchronized(load_camera_registry(path))]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"].reverse()
    write_json(path, data)
    second = [frame_to_dict(f) for f in iter_synchronized(load_camera_registry(path))]

    assert first == second
    assert [(row["t"], row["camera_id"]) for row in first] == [
        (t, camera_id) for t in range(4) for camera_id in ["cam-a", "cam-b"]
    ]
    assert [row["media_t"] for row in first if row["camera_id"] == "cam-b"] == [1, 2, 3, 4]


def test_ids_iguais_preservam_namespace_e_proveniencia(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    rows = [frame_to_dict(f) for f in iter_synchronized(registry)]
    a, b = rows[:2]

    assert a["tracks"][0]["track_key"] == {
        "camera_id": "cam-a",
        "segment_id": "segment-1",
        "local_track_id": 1,
    }
    assert b["tracks"][0]["track_key"] == {
        "camera_id": "cam-b",
        "segment_id": "segment-1",
        "local_track_id": 1,
    }
    assert a["tracks"][0]["projected_position_m"] == pytest.approx([1.5, 2])
    assert b["tracks"][0]["projected_position_m"] == pytest.approx([1.5, 2])
    assert a["tracks"][0]["position_m"] is None
    assert b["tracks"][0]["position_m"] is None
    assert all(row["provenance"] == "synthetic" and row["mode"] == "predictions" for row in rows)
    assert all("global_track_id" not in track for row in rows for track in row["tracks"])
    assert all("events" not in row for row in rows)


def test_frame_vazio_e_ausente_nao_sao_interpolados(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    rows = [frame_to_dict(f) for f in iter_synchronized(registry) if f.camera.camera_id == "cam-b"]

    assert [(row["t"], row["status"], len(row["tracks"])) for row in rows] == [
        (0, "observed", 2),
        (1, "observed", 0),
        (2, "missing", 0),
        (3, "observed", 2),
    ]


def test_posicao_sem_referencial_comum_nao_vira_posicao_na_planta(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["projection_frame_id"] = None
    write_json(path, data)

    row = frame_to_dict(next(iter_synchronized(load_camera_registry(path))))

    assert row["projection_frame_id"] is None
    assert row["tracks"][0]["position_m"] is None
    assert row["tracks"][0]["bbox"] == [100, 0, 200, 200]


def test_incerteza_desconhecida_nao_e_tratada_como_zero(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["clock"]["uncertainty_s"] = None
    write_json(path, data)
    rows = [frame_to_dict(f) for f in iter_synchronized(load_camera_registry(path))]

    assert rows[2]["time_interval_s"] is None
    assert rows[3]["time_interval_s"] == pytest.approx([0.95, 1.05])


def test_janela_de_replay_e_inclusiva_no_tempo_nominal_sem_repetir_observacao(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    rows = list(iter_synchronized(registry, start_s=1, end_s=2))

    assert [row.t for row in rows] == [1, 1, 2, 2]
    assert list(iter_synchronized(registry, start_s=20)) == []
    assert len(list(iter_synchronized(registry))) == 8


@pytest.mark.parametrize(
    "bounds", [{"start_s": 2, "end_s": 1}, {"start_s": True}, {"end_s": float("nan")}]
)
def test_recusa_janela_invalida(tmp_path, bounds):
    registry = load_camera_registry(make_registry(tmp_path))

    with pytest.raises(ValueError):
        list(iter_synchronized(registry, **bounds))


def test_tempo_global_pode_ser_negativo(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["clock"]["offset_s"] = -10
    write_json(path, data)

    assert next(iter_synchronized(load_camera_registry(path))).t == -10


def test_soma_de_offset_e_incerteza_nao_pode_transbordar(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["clock"].update(offset_s=1.7e308, uncertainty_s=1.7e308)
    write_json(path, data)

    with pytest.raises(ValueError, match="finito"):
        load_camera_registry(path)


def test_offset_nao_pode_apagar_a_resolucao_dos_timestamps(tmp_path):
    path = make_registry(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["clock"].update(offset_s=1e100, uncertainty_s=0)
    write_json(path, data)

    with pytest.raises(ValueError, match="resolução"):
        load_camera_registry(path)


def test_cache_sem_linha_nao_gera_frame_novo_no_replay(tmp_path):
    path = make_registry(tmp_path)
    folder = tmp_path / "cam-b"
    rows = [json.loads(line) for line in (folder / "predictions.jsonl").read_text().splitlines()]
    del rows[1]
    rewrite_cache(folder, rows)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][1]["recording"].update(
        {**reference(folder / "manifest.json"), "path": "cam-b/manifest.json"}
    )
    write_json(path, data)

    registry = load_camera_registry(path)
    frames = [item for item in iter_synchronized(registry) if item.camera.camera_id == "cam-b"]

    assert [item.t for item in frames] == [0, 2, 3]
    assert registry.cameras[1].recording.frame_counts["missing"] == 2


def test_oracle_continua_rotulado_por_camera(tmp_path):
    path = make_registry(tmp_path)
    manifest = tmp_path / "cam-b" / "manifest.json"
    sequence = json.loads(manifest.read_text(encoding="utf-8"))
    sequence["mode"] = "oracle"
    sequence["predictions"]["provenance"] = "ground_truth"
    write_json(manifest, sequence)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][1]["recording"].update({**reference(manifest), "path": "cam-b/manifest.json"})
    data["cameras"][1]["segment_id"] = "segment-2"
    write_json(path, data)

    rows = [frame_to_dict(item) for item in iter_synchronized(load_camera_registry(path))]

    assert rows[0]["mode"] == "predictions"
    assert rows[1]["mode"] == "oracle"
    assert rows[1]["provenance"] == "ground_truth"
    assert rows[1]["tracks"][0]["track_key"]["segment_id"] == "segment-2"


def _run_cli(path, output):
    script = Path(__file__).parents[1] / "scripts" / "replay_multicamera.py"
    return subprocess.run(
        [sys.executable, str(script), str(path), "--output", str(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_cli_grava_replay_e_contexto_sem_fabricar_metrica_de_tracking(tmp_path):
    path = make_registry(tmp_path / "input")
    output = tmp_path / "result"

    result = _run_cli(path, output)

    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (output / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert summary["site_id"] == "synthetic-hall"
    assert summary["schema_version"] == 2
    assert all(row["schema_version"] == 2 for row in rows)
    assert rows[0]["tracks"][0]["position_m"] is None
    assert rows[0]["tracks"][0]["projected_position_m"] == pytest.approx([1.5, 2])
    assert summary["frames_written"] == 8
    assert summary["cameras"][0]["clock"]["offset_s"] == 0
    assert summary["cameras"][1]["clock"]["uncertainty_s"] == 0.05
    assert summary["cameras"][0]["recording_sha256"]
    assert len(rows) == 8
    assert sum(row["status"] == "missing" for row in rows) == 1
    assert "metrics" not in summary


def test_cli_recusa_sobrescrever_output_existente(tmp_path):
    path = make_registry(tmp_path / "input")
    output = tmp_path / "result"
    output.mkdir()
    marker = output / "summary.json"
    marker.write_bytes(b"preservar")

    result = _run_cli(path, output)

    assert result.returncode == 1
    assert marker.read_bytes() == b"preservar"
    assert not (output / "frames.jsonl").exists()


def test_cli_invalida_antes_de_criar_output(tmp_path):
    path = make_registry(tmp_path / "input")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["recording"]["sha256"] = "0" * 64
    write_json(path, data)
    output = tmp_path / "result"

    result = _run_cli(path, output)

    assert result.returncode == 1
    assert not output.exists()


def test_cli_recusa_projecao_no_infinito_antes_de_publicar_replay(tmp_path):
    path = make_registry(tmp_path / "input")
    folder = path.parent / "cam-a"
    calibration = folder / "calibration.json"
    write_json(
        calibration,
        {
            "camera": "cam-a",
            "note": "Homografia de teste com horizonte no pé da primeira pessoa.",
            "correspondences": [
                {"pixel": [x, y], "world": [x / (x - 150), y / (x - 150)]}
                for x, y in [(0, 0), (300, 0), (0, 300), (300, 300)]
            ],
        },
    )
    manifest = folder / "manifest.json"
    sequence = json.loads(manifest.read_text(encoding="utf-8"))
    sequence["geometry"]["file"] = reference(calibration)
    write_json(manifest, sequence)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["recording"].update({**reference(manifest), "path": "cam-a/manifest.json"})
    write_json(path, data)
    output = tmp_path / "result"

    result = _run_cli(path, output)

    assert result.returncode == 1
    assert "infinito" in result.stderr
    assert not output.exists()
