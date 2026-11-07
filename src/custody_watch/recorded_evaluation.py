"""Avaliação CPU da lógica sobre predições congeladas, sem consultar vídeo."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from .annotations import match_events
from .events import EventKind
from .metrics import ScoredEvent, p_miss_at_rfa
from .orchestrator import run_session
from .recorded_predictions import RecordedSequence


def _runtime() -> dict:
    # Inclui arquivos ainda não commitados: só HEAD não identifica uma mudança local.
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    revision = None
    dirty = None
    root = Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        revision, dirty = head.stdout.strip(), bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass  # Wheel sem checkout: o fingerprint do código continua disponível.
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "package_version": version("custody-watch"),
        "logic_sha256": digest.hexdigest(),
        "code_revision": revision,
        "working_tree_dirty": dirty,
        "libraries": {name: version(name) for name in ("numpy", "scipy", "opencv-python")},
    }


def evaluate_recording(recording: RecordedSequence) -> dict:
    manifest = recording.manifest
    policy = manifest["evaluation"]
    clock = manifest["clock"]
    duration = clock["end_s"] - clock["start_s"]
    result = run_session(recording.observations(), recording.plane, recording.config)
    metrics = {}
    for name in policy["kinds"]:
        kind = EventKind(name)
        truth = [event for event in recording.truth if event.kind is kind]
        eligible, short, censored = [], [], []
        for event in truth:
            verdict = (
                event.sustained_for(recording.config.custody.unattended_time_s)
                if kind is EventKind.BAG_UNATTENDED
                else True
            )
            {True: eligible, False: short, None: censored}[verdict].append(event)
        matched = match_events(
            result.events,
            eligible,
            kinds={kind},
            lag_window_s=policy["lag_window_s"],
            slack_before_s=policy["slack_before_s"],
        )
        # Previsões sobre um estado cujo fim não é observável ficam sem julgamento.
        uncertain = [
            event
            for event in matched.spurious
            if any(
                annotation.t - policy["slack_before_s"] <= event.t_start <= clock["end_s"]
                for annotation in censored
            )
        ]
        spurious = [event for event in matched.spurious if event not in uncertain]
        operating_point = None
        refusal = None
        if not eligible:
            refusal = "nenhum positivo mensurável desta classe"
        elif uncertain:
            refusal = "censura impede verificar o orçamento de falsos alarmes"
        if refusal is None:
            scores = [ScoredEvent(1.0, True) for _ in matched.matched] + [
                ScoredEvent(1.0, False) for _ in spurious
            ]
            operating_point = asdict(
                p_miss_at_rfa(
                    scores,
                    duration / 60.0,
                    policy["target_rfa"],
                    total_positives=len(eligible),
                )
            )
            if operating_point["threshold"] == float("inf"):
                operating_point["threshold"] = None  # JSON válido; aceita nenhum evento.
        metrics[name] = {
            "annotated": len(truth),
            "eligible": len(eligible),
            "short": len(short),
            "censored": len(censored),
            "matched": len(matched.matched),
            "missed": len(matched.missed),
            "spurious": len(spurious),
            "unscored_predictions": len(uncertain),
            "delays_s": [event.t_start - annotation.t for annotation, event in matched.matched],
            "false_alarms_per_minute": None if uncertain else len(spurious) / (duration / 60.0),
            "operating_point": operating_point,
            "p_miss_refusal": refusal,
        }
    status = "ok"
    if not any(row["operating_point"] is not None for row in metrics.values()):
        status = (
            "incomplete_annotation"
            if any(row["eligible"] for row in metrics.values())
            else "no_measurable_positives"
        )
    return {
        "schema_version": 1,
        "sequence_id": manifest["sequence_id"],
        "mode": manifest["mode"],
        "source_kind": manifest["source"]["kind"],
        "status": status,
        "duration_s": duration,
        "video_minutes": duration / 60.0,
        "frames": recording.frame_counts,
        "manifest_sha256": recording.manifest_sha256,
        "inputs": manifest,
        "source_file_verified": recording.source_verified,
        "evaluator": _runtime(),
        "resolved_config": asdict(recording.config),
        "matching_policy": "temporal_by_kind; bag/subject identities are not scored",
        "score_policy": "binary_event_presence; this is not queue ranking evaluation",
        "metrics": metrics,
        "events": [event.to_dict() for event in result.events],
        "annotations": [asdict(event) | {"kind": event.kind.value} for event in recording.truth],
    }
