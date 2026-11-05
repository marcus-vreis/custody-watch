import json
import subprocess
import sys
from pathlib import Path

import pytest

from custody_watch.recorded_evaluation import evaluate_recording
from custody_watch.recorded_predictions import load_recording
from tests.recorded_fixture import make_recording, reference, write_json


def test_replay_encontra_positivo_e_repete_eventos_e_metricas(tmp_path):
    recording = load_recording(make_recording(tmp_path))

    first = evaluate_recording(recording)
    second = evaluate_recording(recording)

    assert first == second
    metrics = first["metrics"]["bag_unattended"]
    assert metrics["eligible"] == 1
    assert metrics["matched"] == 1
    assert metrics["missed"] == 0
    assert metrics["spurious"] == 0
    assert metrics["delays_s"] == [25.0]
    assert metrics["operating_point"]["p_miss"] == 0.0
    assert first["duration_s"] == 45.0
    assert first["mode"] == "predictions"


def test_exemplo_publico_preserva_hashes_apos_normalizacao_git():
    root = Path(__file__).parent / "fixtures" / "recorded" / "synthetic-deposit"
    for path in root.iterdir():
        assert b"\r\n" not in path.read_bytes(), f"hash muda com eol=lf: {path.name}"

    report = evaluate_recording(load_recording(root / "manifest.json"))

    assert report["source_kind"] == "synthetic"
    assert report["metrics"]["bag_unattended"]["matched"] == 1


@pytest.mark.parametrize(
    "events,censored",
    [
        ([], 0),
        (
            [
                {
                    "kind": "bag_unattended",
                    "t": 40.0,
                    "t_end": 45.0,
                    "truncated": True,
                }
            ],
            1,
        ),
    ],
)
def test_controle_ou_censura_recusa_p_miss_mas_preserva_espurios(tmp_path, events, censored):
    path = make_recording(tmp_path)
    write_json(tmp_path / "annotations.json", {"session": "synthetic-deposit", "events": events})
    manifest = json.loads(path.read_text())
    manifest["annotations"] = reference(tmp_path / "annotations.json")
    write_json(path, manifest)

    report = evaluate_recording(load_recording(path))

    metrics = report["metrics"]["bag_unattended"]
    assert metrics["eligible"] == 0
    assert metrics["censored"] == censored
    assert metrics["spurious"] == 1
    assert metrics["operating_point"] is None
    assert metrics["false_alarms_per_minute"] == pytest.approx(60 / 45)
    assert report["status"] == "no_measurable_positives"


def test_predicao_nao_e_preenchida_pelo_gabarito(tmp_path):
    from tests.recorded_fixture import rewrite_cache

    path = make_recording(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    for row in rows:
        row["detections"] = []
    rewrite_cache(tmp_path, rows)

    report = evaluate_recording(load_recording(path))

    metrics = report["metrics"]["bag_unattended"]
    assert metrics["eligible"] == metrics["missed"] == 1
    assert metrics["matched"] == 0
    assert metrics["operating_point"]["p_miss"] == 1.0
    assert report["events"] == []


def test_oracle_tem_rotulo_no_relatorio(tmp_path):
    path = make_recording(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["mode"] = "oracle"
    manifest["predictions"]["provenance"] = "ground_truth"
    write_json(path, manifest)

    report = evaluate_recording(load_recording(path))

    assert report["mode"] == "oracle"
    assert report["inputs"]["predictions"]["provenance"] == "ground_truth"


def test_replay_binario_nao_separa_acerto_de_espurio_com_mesmo_score(tmp_path):
    from tests.recorded_fixture import rewrite_cache

    path = make_recording(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    for row in rows:
        # Bolsa fantasma do detector: não há evento correspondente no gabarito.
        row["detections"].append(
            {
                "track_id": 10,
                "cls": "suitcase",
                "bbox": [350, 150, 390, 200],
            }
        )
    rewrite_cache(tmp_path, rows)

    report = evaluate_recording(load_recording(path))

    metrics = report["metrics"]["bag_unattended"]
    assert metrics["matched"] == metrics["spurious"] == 1
    assert metrics["operating_point"] == {
        "threshold": None,
        "p_miss": 1.0,
        "false_alarms": 0,
        "rfa": 0.0,
    }


def test_predicao_sem_julgamento_nao_pode_sustentar_orcamento_de_falsos_alarmes(tmp_path):
    from tests.recorded_fixture import rewrite_cache

    path = make_recording(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    for row in rows:
        row["detections"].append(
            {
                "track_id": 10,
                "cls": "suitcase",
                "bbox": [350, 150, 390, 200],
            }
        )
    rewrite_cache(tmp_path, rows)
    annotations = json.loads((tmp_path / "annotations.json").read_text())
    annotations["events"].append(
        {
            "kind": "bag_unattended",
            "t": 20.0,
            "t_end": 30.0,
            "truncated": True,
        }
    )
    write_json(tmp_path / "annotations.json", annotations)
    manifest = json.loads(path.read_text())
    manifest["annotations"] = reference(tmp_path / "annotations.json")
    write_json(path, manifest)

    report = evaluate_recording(load_recording(path))

    metrics = report["metrics"]["bag_unattended"]
    assert metrics["eligible"] == metrics["matched"] == 1
    assert metrics["unscored_predictions"] == 1
    assert metrics["operating_point"] is None
    assert metrics["false_alarms_per_minute"] is None
    assert "censura" in metrics["p_miss_refusal"]
    assert report["status"] == "incomplete_annotation"


def test_cli_cpu_exporta_json_eventos_e_csv_sem_sobrescrever(tmp_path):
    path = make_recording(tmp_path / "inputs")
    output = tmp_path / "report"
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_recorded.py"
    command = [sys.executable, str(script), str(path), "--output", str(output)]

    result = subprocess.run(command, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert report["metrics"]["bag_unattended"]["matched"] == 1
    assert (output / "events.jsonl").read_text().count('"kind"') == len(report["events"])
    assert "bag_unattended" in (output / "metrics.csv").read_text()
    before = (output / "summary.json").read_bytes()
    repeated = subprocess.run(command, capture_output=True, text=True)
    assert repeated.returncode == 1
    assert (output / "summary.json").read_bytes() == before


def test_cli_hash_invalido_nao_cria_relatorio(tmp_path):
    path = make_recording(tmp_path / "inputs")
    (path.parent / "predictions.jsonl").write_text("adulterado", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_recorded.py"
    output = tmp_path / "report"

    result = subprocess.run(
        [sys.executable, str(script), str(path), "--output", str(output)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert not output.exists()
    assert "tamanho" in result.stderr or "hash" in result.stderr


def test_cli_controle_exporta_resultados_e_sai_com_recusa_explicita(tmp_path):
    path = make_recording(tmp_path / "inputs")
    write_json(path.parent / "annotations.json", {"session": "synthetic-deposit", "events": []})
    manifest = json.loads(path.read_text())
    manifest["annotations"] = reference(path.parent / "annotations.json")
    write_json(path, manifest)
    output = tmp_path / "control"
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_recorded.py"

    result = subprocess.run(
        [sys.executable, str(script), str(path), "--output", str(output)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "RECUSANDO P_miss" in result.stdout
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert report["metrics"]["bag_unattended"]["spurious"] == 1
    assert report["metrics"]["bag_unattended"]["operating_point"] is None
