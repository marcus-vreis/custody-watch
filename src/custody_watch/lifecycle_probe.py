"""Ensaio curto de falhas sintéticas; sem rede, câmera ou modelo real."""

from __future__ import annotations

import threading

import numpy as np

from .config import Config
from .event_journal import JournalWriter
from .ground_plane import GroundPlane
from .journal_reader import iter_journal
from .live_recovery import LiveRecovery, RecoveryPolicy
from .orchestrator import LiveSession
from .painel import Painel
from .source_health import SourceHealth


def lifecycle_smoke(directory) -> dict:
    """Interrupção, recuperação, esgotamento e read bloqueado com cleanup final.

    O caso bloqueado deve falhar visivelmente, não criar outra captura. Só
    o harness libera a chamada fake em seguida e confirma a saída da thread.
    A espera real de .1 s exercita o consumidor, não simula horas de câmera.
    """
    return {case: _case(case, directory) for case in ("recovered", "exhausted", "blocked")}


def _case(case, directory):
    stop, unblock, consumed = threading.Event(), threading.Event(), threading.Event()
    captures = []

    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    clock = Clock()
    health = SourceHealth("live", clock=clock)
    writer = JournalWriter(directory, clock=clock)
    panel = Painel(source_health=health)

    class Capture:
        def __init__(self, index):
            self.index, self.reads, self.released = index, 0, False

        def read(self):
            self.reads += 1
            if case == "blocked":
                clock.now += 6.0
                unblock.wait(2.0)
                return False, None
            if case == "recovered" and self.index == 1:
                if self.reads == 1:
                    return True, np.zeros((24, 32, 3), np.uint8)
                consumed.wait(2.0)
            return False, None

        def release(self):
            self.released = True

    def factory():
        capture = Capture(len(captures))
        captures.append(capture)
        return capture

    def published():
        stop.set()
        consumed.set()

    recovery = LiveRecovery(
        capture_factory=factory,
        detector_factory=lambda: lambda _: [],
        session_factory=lambda log: LiveSession(
            GroundPlane(np.diag([0.01, 0.01, 1.0])), Config(), events=log
        ),
        journal=writer,
        painel=panel,
        health=health,
        stop=stop,
        clock=clock,
        policy=RecoveryPolicy(attempts=2, backoff_s=0.001, close_timeout_s=0.01),
        on_processed=published,
        wait=lambda _: False,
    )
    try:
        recovery.run()
        status = recovery.snapshot()
    finally:
        unblock.set()
        consumed.set()
        closed = recovery.camera.wait_closed(1.0)
        writer.close()
    rows = list(iter_journal(writer.directory))
    return {
        "status": status,
        "opened": len(captures),
        "released": sum(c.released for c in captures),
        "owned_reader_alive": not closed,
        "journal_records": len(rows),
        "panel_readable": bool(panel.estado()),
        "health": health.snapshot(),
    }
