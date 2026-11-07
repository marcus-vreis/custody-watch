"""Importa recortes públicos locais para o laboratório em modo oracle."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2

from custody_watch.public_multicamera import build_sample


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=["nvidia", "wildtrack"])
    parser.add_argument("source", type=Path, help="pasta local com imagens/vídeos e anotações")
    parser.add_argument("--output", type=Path, required=True, help="diretório novo para o recorte")
    parser.add_argument(
        "--frames",
        required=True,
        help="índices originais uniformemente espaçados, separados por vírgula",
    )
    parser.add_argument(
        "--cameras", required=True, help="IDs de ao menos duas câmeras, separados por vírgula"
    )
    parser.add_argument("--source-revision", help="revisão/ETag da fonte dos dados")
    args = parser.parse_args()
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if dirty:
            revision += "+dirty"
        report = build_sample(
            args.dataset,
            args.source,
            args.output,
            [int(value) for value in args.frames.split(",")],
            args.cameras.split(","),
            code_revision=revision,
            source_revision=args.source_revision,
        )
        print(
            json.dumps(
                {
                    "dataset": args.dataset,
                    "mode": "oracle",
                    "integration_status": report["integration_status"],
                    "frames_per_camera": report["frames_per_camera"],
                    "cameras": report["cameras"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (
        ValueError,
        OSError,
        TypeError,
        KeyError,
        cv2.error,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Importação recusada: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
