"""Cena sintética própria: depósito, afastamento e retorno, sem vídeo/pesos."""

import hashlib
import json
from pathlib import Path


def write_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def reference(path):
    return {
        "path": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def make_recording(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "source.json", {"scene": "depósito e retorno sintéticos; CC0-1.0"})
    write_json(root / "config.json", {})
    write_json(
        root / "annotations.json",
        {
            "session": "synthetic-deposit",
            "events": [{"kind": "bag_unattended", "t": 5.0, "t_end": 40.0}],
        },
    )
    frames = []
    for index in range(46):
        x = 100 if index < 5 or index >= 40 else 1100
        frames.append(
            {
                "schema_version": 1,
                "frame_index": index,
                "t": float(index),
                "status": "observed",
                "detections": [
                    {"track_id": 1, "cls": "person", "bbox": [x, 0, x + 100, 200]},
                    {"track_id": 9, "cls": "suitcase", "bbox": [210, 150, 250, 200]},
                ],
            }
        )
    path = root / "predictions.jsonl"
    path.write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8", newline="\n"
    )
    manifest = {
        "schema_version": 1,
        "sequence_id": "synthetic-deposit",
        "mode": "predictions",
        "source": {
            "kind": "synthetic",
            "origin": "generated fixture",
            "redistribution": "CC0-1.0",
            "file": reference(root / "source.json"),
        },
        "predictions": {**reference(path), "provenance": "synthetic"},
        "annotations": reference(root / "annotations.json"),
        "config": reference(root / "config.json"),
        "geometry": {
            "kind": "uniform_scale",
            "metres_per_pixel": 0.01,
            "limitation": "Cena sintética plana; não representa câmera real.",
        },
        "clock": {"fps": 1.0, "start_s": 0.0, "end_s": 45.0, "frame_count": 46},
        "classes": ["person", "suitcase"],
        "producer": {
            "code_revision": "synthetic-fixture-v1",
            "detector": "synthetic/1",
            "tracker": "synthetic/1",
            "weights_sha256": None,
            "environment": {"generator": "tests.recorded_fixture"},
            "command": ["synthetic-fixture"],
            "seeds": {},
        },
        "evaluation": {
            "kinds": ["bag_unattended"],
            "lag_window_s": 27.0,
            "slack_before_s": 1.0,
            "target_rfa": 0.5,
        },
    }
    write_json(root / "manifest.json", manifest)
    return root / "manifest.json"


def rewrite_cache(root, frames):
    path = root / "predictions.jsonl"
    path.write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8", newline="\n"
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["predictions"].update(reference(path))
    write_json(root / "manifest.json", manifest)
