"""Prepara fila reproduzível de quadros para anotar; não valida confiança."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from custody_watch.camera_registry import load_camera_registry
from custody_watch.contact_review_queue import prepare_contact_review_queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="cadastro local de câmeras v1")
    parser.add_argument("--seed", required=True, type=int, help="seed explícita, de 0 a 2**32-1")
    parser.add_argument(
        "--frames-per-camera", type=int, default=6, help="até 1000 quadros por câmera"
    )
    parser.add_argument("--min-gap-s", type=float, default=1.0, help="intervalo mínimo por câmera")
    parser.add_argument(
        "--output", required=True, type=Path, help="arquivo novo; pasta deve existir"
    )
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output já existe; fila anterior preservada")
        report = prepare_contact_review_queue(
            load_camera_registry(args.registry),
            seed=args.seed,
            frames_per_camera=args.frames_per_camera,
            min_gap_s=args.min_gap_s,
        )
        text = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(text)
        frames = sum(camera["selected_frames"] for camera in report["cameras"])
        print(f"{frames} quadros para revisão; nenhuma evidência ou posição qualificada gerada")
        return 0
    except (ValueError, OSError) as error:
        print(f"Fila recusada: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
