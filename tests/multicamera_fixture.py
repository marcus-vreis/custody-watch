"""Duas vistas sintéticas calibradas; nenhum vídeo ou detector real."""

import json
from pathlib import Path

from tests.recorded_fixture import make_recording, reference, rewrite_cache, write_json


def make_registry(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cameras = []
    for camera_id, shift, offset in [("cam-a", 0, 0), ("cam-b", 100, -1)]:
        folder = root / camera_id
        path = make_recording(folder)
        rows = [
            json.loads(line) for line in (folder / "predictions.jsonl").read_text().splitlines()
        ]
        rows = rows[:4]
        for row in rows:
            row["t"] -= offset
            for det in row["detections"]:
                det["bbox"][0] += shift
                det["bbox"][2] += shift
        if camera_id == "cam-b":
            rows[1]["detections"] = []
            rows[2].update(status="missing", detections=[])
        rewrite_cache(folder, rows)
        write_json(folder / "annotations.json", {"session": camera_id, "events": []})
        calibration = folder / "calibration.json"
        write_json(
            calibration,
            {
                "camera": camera_id,
                "note": "Geometria sintética exata; não é medição física.",
                "correspondences": [
                    {"pixel": [x + shift, y], "world": [x / 100, y / 100]}
                    for x, y in [(0, 0), (800, 0), (0, 600), (800, 600)]
                ],
            },
        )
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.update(sequence_id=camera_id)
        manifest["clock"] = {"fps": 1, "start_s": -offset, "end_s": 3 - offset, "frame_count": 4}
        manifest["annotations"] = reference(folder / "annotations.json")
        manifest["geometry"] = {"kind": "calibration", "file": reference(calibration)}
        write_json(path, manifest)
        cameras.append(
            {
                "camera_id": camera_id,
                "floor_id": "terreo",
                "segment_id": "segment-1",
                "position_m": [-1 if camera_id == "cam-a" else 9, 0],
                "coverage_m": [[0, 0], [8, 0], [8, 6], [0, 6]],
                "projection_frame_id": "hall-m",
                "recording": {**reference(path), "path": f"{camera_id}/manifest.json"},
                "clock": {
                    "offset_s": offset,
                    "uncertainty_s": 0 if camera_id == "cam-a" else 0.05,
                    "method": "Offsets sintéticos declarados; sem sincronização automática.",
                },
            }
        )
    registry = {
        "schema_version": 1,
        "site_id": "synthetic-hall",
        "floors": [
            {
                "floor_id": "terreo",
                "coordinate_frame": {
                    "frame_id": "hall-m",
                    "unit": "m",
                    "origin": "Canto sudoeste do hall sintético",
                    "x_axis": "Leste",
                    "y_axis": "Norte",
                },
            }
        ],
        "cameras": cameras,
    }
    path = root / "registry.json"
    write_json(path, registry)
    return path
