"""Reconstrói uma avaliação em CPU a partir de manifesto e predições cacheadas."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from custody_watch.recorded_evaluation import evaluate_recording
from custody_watch.recorded_predictions import load_recording


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="manifesto schema_version 1")
    parser.add_argument(
        "--output", type=Path, required=True, help="diretório novo para os resultados"
    )
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output já existe; escolha outro diretório para preservar o resultado")
        report = evaluate_recording(load_recording(args.manifest))
        # Serializar antes de criar o diretório também recusa números não JSON.
        summary = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        events = "".join(
            json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
            for event in report["events"]
        )
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "summary.json").write_text(summary, encoding="utf-8")
        (args.output / "events.jsonl").write_text(events, encoding="utf-8")
        fields = [
            "kind",
            "eligible",
            "short",
            "censored",
            "matched",
            "missed",
            "spurious",
            "unscored_predictions",
            "false_alarms_per_minute",
            "p_miss",
            "rfa",
            "threshold",
        ]
        with (args.output / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for kind, metrics in report["metrics"].items():
                point = metrics["operating_point"] or {}
                row = {field: metrics[field] for field in fields if field in metrics}
                row.update({field: point.get(field) for field in ("p_miss", "rfa", "threshold")})
                writer.writerow({"kind": kind, **row})
        print(f"{report['mode']} / {report['source_kind']}: {report['sequence_id']}")
        for kind, metrics in report["metrics"].items():
            print(
                f"{kind}: {metrics['matched']} acertos, {metrics['missed']} perdidos, "
                f"{metrics['spurious']} espúrios; "
                f"{metrics['eligible']} positivos mensuráveis"
            )
            if metrics["p_miss_refusal"]:
                print(f"RECUSANDO P_miss: {metrics['p_miss_refusal']}")
        return 0 if report["status"] == "ok" else 2
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(f"Avaliação recusada: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
