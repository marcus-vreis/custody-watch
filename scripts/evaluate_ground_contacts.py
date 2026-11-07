"""Avalia declarações de contato no chão, separadas do replay e da custódia."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import cv2

from custody_watch.camera_registry import load_camera_registry
from custody_watch.ground_contact_evaluation import evaluate_ground_contacts
from custody_watch.ground_contacts import load_ground_contacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="cadastro de câmeras v1")
    parser.add_argument("contacts", type=Path, help="evidências de contato v1")
    parser.add_argument("--output", type=Path, required=True, help="diretório novo de resultados")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output já existe; resultados anteriores são preservados")
        registry = load_camera_registry(args.registry)
        report = evaluate_ground_contacts(registry, load_ground_contacts(args.contacts, registry))
        observations = report.pop("observations")
        summary = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as spool:
            for observation in observations:
                spool.write(json.dumps(observation, ensure_ascii=False, allow_nan=False) + "\n")
            args.output.mkdir(parents=True, exist_ok=False)
            spool.seek(0)
            with (args.output / "observations.jsonl").open(
                "w", encoding="utf-8", newline="\n"
            ) as output:
                shutil.copyfileobj(spool, output)
            (args.output / "summary.json").write_text(summary, encoding="utf-8", newline="\n")
        counts = report["counts"]
        print(
            f"{counts['observations']} observações; "
            f"{counts['observed_contacts']} contatos declarados; 0 posições qualificadas"
        )
        return 0
    except (ValueError, OSError, cv2.error) as error:
        print(f"Avaliação recusada: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
