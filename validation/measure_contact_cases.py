"""Reproduz casos exploratórios fixos da #78 com os recortes oracle locais."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

from custody_watch.camera_registry import load_camera_registry
from custody_watch.ground_contact_evaluation import evaluate_ground_contacts
from custody_watch.ground_contacts import load_ground_contacts
from custody_watch.public_multicamera import (
    nvidia_observations,
    read_nvidia_prefix,
    wildtrack_observations,
)
from custody_watch.recorded_predictions import file_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for dataset in ("nvidia", "wildtrack"):
        parser.add_argument(f"--{dataset}-registry", type=Path, required=True)
        parser.add_argument(f"--{dataset}-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="arquivo JSON novo")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output já existe; escolha arquivo novo")
    repo = Path(__file__).resolve().parents[1]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    result = {
        "date": "2026-11-07",
        "runs_per_dataset": 1,
        "seed": None,
        "parent_revision": revision,
        "working_tree_dirty": bool(dirty),
        "code_sha256": {
            name: file_digest(repo / name)
            for name in [
                "src/custody_watch/ground_contacts.py",
                "src/custody_watch/ground_contact_evaluation.py",
                "validation/measure_contact_cases.py",
            ]
        },
        "scope": (
            "Estimativas visuais exploratórias de modelo, sem revisão humana; "
            "um contato declarado por base, escolhido antes de comparar com gabarito. "
            "Não mede precisão de produção nem melhora de detector."
        ),
        "datasets": [],
    }
    for dataset, camera_id, source_frame in [
        ("nvidia", "Camera_0019", 75),
        ("wildtrack", "C1", 25),
    ]:
        registry = load_camera_registry(getattr(args, f"{dataset}_registry"))
        evidence_path = repo / "validation" / f"ground-contacts-{dataset}-2026-11-07.json"
        contacts = load_ground_contacts(evidence_path, registry)
        report = evaluate_ground_contacts(registry, contacts)
        rows = report.pop("observations")
        source = getattr(args, f"{dataset}_source")
        if dataset == "nvidia":
            truth_path = source / "ground_truth.prefix.json.part"
            truth = read_nvidia_prefix(truth_path, [source_frame])
            people = nvidia_observations(truth[str(source_frame)], camera_id)
        else:
            truth_path = source / "annotations_positions" / f"{source_frame:08d}.json"
            people = wildtrack_observations(json.loads(truth_path.read_text(encoding="utf-8")), 0)
        reference = {person.track_id: [person.world.x, person.world.y] for person in people}
        comparisons = []
        for row in rows:
            if not row["evidence_present"]:
                continue
            expected = reference[row["local_track_id"]]
            projected = row["contact_projection_m"]
            comparisons.append(
                {
                    **row,
                    "source_frame": source_frame,
                    "reference_world_m": expected,
                    "reference_provenance": "ground_truth",
                    "contact_error_m": None
                    if projected is None
                    else math.dist(projected, expected),
                    "bbox_error_m": None
                    if row["bbox_projection_m"] is None
                    else math.dist(row["bbox_projection_m"], expected),
                }
            )
        errors = [
            row["contact_error_m"] for row in comparisons if row["contact_error_m"] is not None
        ]
        if len(errors) != 1 or len(comparisons) != 2:
            raise ValueError("O experimento fixo exige um contato e duas declarações por base.")
        result["datasets"].append(
            {
                "dataset": dataset,
                "mode": "oracle",
                **report,
                "reference_input": {
                    "name": str(truth_path.relative_to(source)).replace("\\", "/"),
                    "sha256": file_digest(truth_path),
                },
                "compared_contacts": len(errors),
                "contact_error_m": errors[0],
                "selected_observations": comparisons,
            }
        )
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        output.write(serialized)
    print(
        json.dumps(
            [
                {
                    "dataset": d["dataset"],
                    "counts": d["counts"],
                    "compared_contacts": d["compared_contacts"],
                    "contact_error_m": d["contact_error_m"],
                }
                for d in result["datasets"]
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
