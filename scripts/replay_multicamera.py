"""Valida ambiente e exporta replay multicâmera em CPU, sem detector ou custódia."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_replay import frame_to_dict, iter_synchronized


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="cadastro de câmeras schema_version 1")
    parser.add_argument("--output", type=Path, required=True, help="diretório novo para o replay")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output já existe; escolha outro diretório para preservar o resultado")
        registry = load_camera_registry(args.registry)
        summary = {
            "schema_version": 1,
            "site_id": registry.site_id,
            "registry_sha256": registry.manifest_sha256,
            "clock": "t = media_t + offset_s; segundos relativos ao experimento",
            "floors": [asdict(floor) for floor in registry.floors],
            "cameras": [
                {
                    "camera_id": camera.camera_id,
                    "floor_id": camera.floor_id,
                    "segment_id": camera.segment_id,
                    "position_m": None if camera.position_m is None else asdict(camera.position_m),
                    "coverage_m": None
                    if camera.coverage_m is None
                    else [asdict(point) for point in camera.coverage_m],
                    "projection_frame_id": camera.projection_frame_id,
                    "clock": asdict(camera.clock),
                    "recording_sha256": camera.recording.manifest_sha256,
                    "source_verified": camera.recording.source_verified,
                    "mode": camera.recording.manifest["mode"],
                    "source_kind": camera.recording.manifest["source"]["kind"],
                    "provenance": camera.recording.manifest["predictions"]["provenance"],
                    "frame_counts": camera.recording.frame_counts,
                }
                for camera in sorted(registry.cameras, key=lambda camera: camera.camera_id)
            ],
        }
        # Um spool evita manter uma segunda cópia do cache na RAM. Só publicar
        # o diretório depois de validar também todas as projeções/serializações.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as spool:
            count = 0
            for item in iter_synchronized(registry):
                spool.write(
                    json.dumps(frame_to_dict(item), ensure_ascii=False, allow_nan=False) + "\n"
                )
                count += 1
            summary["frames_written"] = count
            serialized = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            args.output.mkdir(parents=True, exist_ok=False)
            spool.seek(0)
            with (args.output / "frames.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
                shutil.copyfileobj(spool, handle)
            (args.output / "summary.json").write_text(serialized, encoding="utf-8", newline="\n")
        print(
            f"{registry.site_id}: {len(registry.cameras)} câmeras, {count} quadros; "
            "sem associação global"
        )
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(f"Replay recusado: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
