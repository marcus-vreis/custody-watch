"""Ensaio CPU opt-in: prefixos determinísticos e duração real explicitamente separada."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
import threading
import time
import tracemalloc
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np

from custody_watch.config import Config
from custody_watch.event_journal import JournalWriter
from custody_watch.events import EventLog
from custody_watch.ground_plane import GroundPlane
from custody_watch.journal_reader import iter_journal
from custody_watch.lifecycle_probe import lifecycle_smoke
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel, estado
from custody_watch.resource_probe import memory_snapshot
from custody_watch.session_probe import RETENTION, SessionProbe, synthetic_frames


def positive(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("esperado número finito positivo")
    return value


def positive_int(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("esperado inteiro positivo")
    return value


def _git(*args):
    root = Path(__file__).resolve().parents[1]
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="diretório novo")
    parser.add_argument("--frames", type=positive_int, default=800)
    parser.add_argument("--sample-every", type=positive_int, default=100)
    parser.add_argument(
        "--wall-seconds",
        type=positive,
        help="substitui limite de frames por prazo real; NÃO é câmera real",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--event-tail", type=positive_int, default=64)
    parser.add_argument(
        "--max-identities",
        type=positive_int,
        default=64,
        help="limiar diagnóstico sintético, não capacidade aprovada de produção",
    )
    parser.add_argument("--max-rss-mib", type=positive, help="orçamento opt-in deste host")
    parser.add_argument("--max-feed-ms", type=positive, help="teto opt-in do máximo feed observado")
    parser.add_argument("--profile-memory", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output já existe; resultados anteriores são preservados")
    args.output.mkdir(parents=True, exist_ok=False)
    config = Config()
    budgets = {key: args.max_identities for key in RETENTION if key != "events_total"}
    budgets.update(
        events_retained=args.event_tail,
        history_samples=args.max_identities * 64,
        gate_tracks=args.max_identities * 3,
        appearance_samples=args.max_identities * config.reid.max_samples,
        pending_bag_samples=args.max_identities * 16,
        flags=args.max_identities * 8,
    )
    environment = {
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "numpy": version("numpy"),
        "opencv": version("opencv-python"),
    }
    metadata = {
        "schema_version": 1,
        "mode": "synthetic_cpu",
        "started_utc": datetime.now(UTC).isoformat(),
        "revision": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "command": [sys.executable, *sys.argv],
        "environment": environment,
        "seed": args.seed,
        "step_s": 0.2,
        "cycle_frames": 100,
        "config": asdict(config),
        "retention": RETENTION,
        "budgets": budgets,
        "resource_budgets": {"rss_mib": args.max_rss_mib, "feed_max_ms": args.max_feed_ms},
        "budget_scope": "limiares diagnósticos para cena com até três tracks por frame; não SLA",
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": {
            str(path.relative_to(Path(__file__).resolve().parents[1])).replace(
                "\\", "/"
            ): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__).resolve().parents[1] / "src").rglob("*.py")
        },
    }
    if args.profile_memory:
        tracemalloc.start()
    started = time.perf_counter()
    writer = JournalWriter(args.output / "journal")
    session = LiveSession(
        GroundPlane(np.diag([0.01, 0.01, 1.0])),
        config,
        events=EventLog(max_events=args.event_tail, sink=writer.emit),
    )
    panel = Painel()
    image = np.zeros((24, 32, 3), np.uint8)
    received = processed = dropped = 0
    exceeded = set()
    last = None
    interrupted = False
    baseline_threads = threading.active_count()
    original_sink = session.events._sink
    try:
        with SessionProbe(session) as probe:
            # Cronometrar o sink também: feed inclui o IO de cada evento.
            session.events._sink = lambda event: probe.measure("journal_emit", writer.emit, event)
            with (
                (args.output / "snapshots.jsonl").open("x", encoding="utf-8") as raw,
                (args.output / "curves.csv").open("x", encoding="utf-8", newline="") as curve_file,
            ):
                fields = [
                    "input_frames",
                    "simulated_s",
                    "wall_s",
                    *RETENTION,
                    "rss_bytes",
                    "python_traced_bytes",
                    "feed_total_s",
                    "feed_max_s",
                ]
                curve = csv.DictWriter(curve_file, fieldnames=fields)
                curve.writeheader()

                def sample(frame):
                    def publish():
                        panel.atualiza(image, estado(session))

                    probe.measure("panel", publish)
                    counts = probe.counts()
                    timing = probe.timings()
                    memory = memory_snapshot()
                    over = [key for key, limit in budgets.items() if counts[key] > limit]
                    if (
                        args.max_rss_mib is not None
                        and memory["rss_bytes"] is not None
                        and memory["rss_bytes"] > args.max_rss_mib * 1024**2
                    ):
                        over.append("rss_bytes")
                    if args.max_feed_ms is not None and (
                        timing.get("feed", {}).get("max_s", 0.0) * 1000 > args.max_feed_ms
                    ):
                        over.append("feed_max_s")
                    exceeded.update(over)
                    row = {
                        "input_frames": received,
                        "simulated_s": frame.t,
                        "wall_s": time.perf_counter() - started,
                        "counts": counts,
                        "memory": memory,
                        "timings": timing,
                        "frames": {
                            "received": received,
                            "processed": processed,
                            "discarded": dropped,
                        },
                        "exceeded": over,
                        "threads_total": threading.active_count(),
                        "owned_captures": 0,
                    }
                    raw.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    raw.flush()
                    curve.writerow(
                        {
                            "input_frames": received,
                            "simulated_s": frame.t,
                            "wall_s": row["wall_s"],
                            **counts,
                            "rss_bytes": memory["rss_bytes"],
                            "python_traced_bytes": memory["python_traced_bytes"],
                            "feed_total_s": timing.get("feed", {}).get("total_s", 0.0),
                            "feed_max_s": timing.get("feed", {}).get("max_s", 0.0),
                        }
                    )
                    return row

                for frame in synthetic_frames(
                    None if args.wall_seconds else args.frames, seed=args.seed
                ):
                    received += 1
                    if frame.dropped:
                        dropped += 1
                    else:
                        tracks = probe.measure("substitute_detector", list, frame.tracked)
                        probe.feed(frame.t, tracks)
                        processed += 1
                    if received % args.sample_every == 0:
                        last = sample(frame)
                    if args.wall_seconds and time.perf_counter() - started >= args.wall_seconds:
                        break
                if last is None or last["input_frames"] != received:
                    last = sample(frame)
        writer.finish_segment(interrupted=True, reason="read_failed")
    except KeyboardInterrupt:
        interrupted = True
    finally:
        session.events._sink = original_sink
        writer.close()
        if args.profile_memory:
            tracemalloc.stop()
    journal_records = sum(1 for _ in iter_journal(writer.directory))
    if last is None:
        last = {
            "input_frames": 0,
            "simulated_s": 0.0,
            "counts": probe.counts(),
            "frames": {"received": 0, "processed": 0, "discarded": 0},
            "memory": memory_snapshot(),
            "timings": probe.timings(),
        }
    faults = {} if interrupted else lifecycle_smoke(args.output / "faults")
    resources_ok = all(
        not f["owned_reader_alive"] and f["opened"] == f["released"] for f in faults.values()
    )
    faults_ok = not interrupted and (
        faults["recovered"]["status"]["state"] == "stopped"
        and faults["exhausted"]["status"]["state"] == "exhausted"
        and faults["blocked"]["status"]["reason"] == "cleanup_unconfirmed"
        and all(f["journal_records"] >= 2 and f["panel_readable"] for f in faults.values())
    )
    unverified = (
        ["rss_budget"]
        if args.max_rss_mib is not None and last["memory"]["rss_bytes"] is None
        else []
    )
    result = metadata | {
        "wall_s": time.perf_counter() - started,
        "simulated_s": last["simulated_s"],
        "frames": last["frames"],
        "last_snapshot": last,
        "exceeded": sorted(exceeded),
        "journal_records": journal_records,
        "faults": faults,
        "baseline_threads": baseline_threads,
        "final_threads": threading.active_count(),
        "resources_ok": resources_ok if not interrupted else None,
        "faults_ok": faults_ok if not interrupted else None,
        "unverified": unverified,
        "observed_panel_readable": bool(panel.estado()),
        "status": (
            "interrupted"
            if interrupted
            else "resource_failed"
            if not resources_ok or not faults_ok
            else "unverified_budget"
            if unverified
            else "budget_exceeded"
            if exceeded
            else "within_diagnostic_budgets"
        ),
        "limits": [
            "uma seed; nenhuma validade estatística de população",
            "mídia virtual não é câmera real; detector substituto não mede percepção",
            "tracemalloc cobre alocações rastreadas após start, não todo heap nativo/VRAM",
            "RSS/working set inclui heap nativo e pode incluir páginas compartilhadas",
            "timings incluem instrumentação; sem meta universal de FPS",
            "nenhuma política de retenção foi alterada; crescimento remete à #30",
        ],
    }
    (args.output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        f"{result['status']}: {last['frames']['received']} frames sintéticos, "
        f"{last['simulated_s']:.1f}s simulados, "
        f"{result['wall_s']:.2f}s reais; recursos liberados: {resources_ok}"
    )
    return (
        130
        if interrupted
        else 0
        if not exceeded and resources_ok and faults_ok and not unverified
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
