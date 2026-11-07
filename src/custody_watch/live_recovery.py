"""Supervisor sequencial: uma lacuna encerra toda associação, nunca a restaura."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from functools import partial
from itertools import chain

from .aovivo import CameraAoVivo, CaptureGap, processa
from .event_journal import JournalWriter
from .events import EventLog


@dataclass(frozen=True)
class RecoveryPolicy:
    attempts: int = 3
    backoff_s: float = 1.0
    max_backoff_s: float = 30.0
    close_timeout_s: float = 1.0

    def __post_init__(self):
        if type(self.attempts) is not int or self.attempts < 0:
            raise ValueError("attempts: esperado inteiro não negativo")
        for field in ("backoff_s", "max_backoff_s", "close_timeout_s"):
            value = getattr(self, field)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field}: esperado número finito positivo")


class LiveRecovery:
    """Abre captura na thread leitora; reseta backend só após confirmar sua saída.

    As fábricas são injetadas para exercitar falhas sem RTSP nem modelo real.
    detector_factory deve construir um backend novo, sem compartilhar predictor.
    O orçamento de retries é por interrupção; publicar um novo frame o renova.
    Guarda apenas a sessão atual; o journal conserva os segmentos anteriores.
    """

    def __init__(
        self,
        *,
        capture_factory,
        detector_factory,
        session_factory,
        journal: JournalWriter,
        painel,
        health,
        stop: threading.Event,
        policy: RecoveryPolicy | None = None,
        event_tail: int = 1000,
        on_session=None,
        on_processed=None,
        escala_uniforme=False,
        clock=time.monotonic,
        wait=None,
    ):
        self.capture_factory = capture_factory
        self.detector_factory = detector_factory
        self.session_factory = session_factory
        self.journal, self.painel, self.health, self.stop = journal, painel, health, stop
        self.policy = policy if policy is not None else RecoveryPolicy()
        self.event_tail = event_tail
        self.on_session, self.on_processed = on_session, on_processed
        self.escala_uniforme, self.clock = escala_uniforme, clock
        self.wait = wait if wait is not None else stop.wait
        self.session = None
        self.camera = None
        self._lock = threading.Lock()
        self._status = {
            "state": "initializing",
            "reason": None,
            "retries": 0,
            "total_retries": 0,
            "backoff_s": None,
            "detector_load_s": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _status_update(self, **values):
        with self._lock:
            self._status.update(values)

    def _published(self):
        self._status_update(state="active", reason=None, retries=0, backoff_s=None)
        if self.on_processed is not None:
            self.on_processed()

    def _route(self, session, segment_id=None):
        if self.on_session is not None:
            self.on_session(session, segment_id)

    def _archive(self, reason, gap_start):
        if self.stop.is_set():
            return self.journal.finish_segment(reason="user_stop")
        return self.journal.finish_segment(interrupted=True, reason=reason, gap_start_s=gap_start)

    def run(self) -> None:
        gap_start = None
        try:
            while not self.stop.is_set():
                if gap_start is None:
                    gap_start = self.clock()
                self._status_update(state="connecting", backoff_s=None)
                self.health.restart()
                camera = self.camera = CameraAoVivo(
                    None,
                    capture_factory=self.capture_factory,
                    relogio=self.clock,
                    parar=self.stop,
                    source_health=self.health,
                )
                reason, fatal = "read_failed", None
                detector = None
                stream = iter(camera)
                try:
                    first = next(stream, None)
                    if first is not None and not self.stop.is_set():
                        if (
                            self.journal.segment_id is None
                            and self.journal.start_segment(
                                gap_start_s=gap_start, gap_end_s=camera.received_at(first[0])
                            )
                            is None
                        ):
                            fatal = "journal_failed"
                        if fatal is None:
                            segment = self.journal.segment_id
                            log = EventLog(
                                max_events=self.event_tail,
                                sink=partial(self.journal.emit, segment_id=segment),
                            )
                            self.session = self.session_factory(log)
                            self._status_update(state="loading_detector")
                            started = self.clock()
                            detector = self.detector_factory()
                            self._status_update(detector_load_s=max(0.0, self.clock() - started))
                            self._route(self.session, segment)
                            processa(
                                chain((first,), stream),
                                detector,
                                self.session,
                                self.painel,
                                parar=self.stop,
                                source_health=self.health,
                                capture_time=camera.received_at,
                                escala_uniforme=self.escala_uniforme,
                                scope={
                                    "session_id": self.journal.session_id,
                                    "segment_id": segment,
                                },
                                on_processed=self._published,
                                propagate_capture_gap=True,
                            )
                            # O closure anterior precisa morrer antes de criar outro YOLO:
                            # BaseTrack._count é global na versão auditada.
                            detector = None
                    reason = self.health.snapshot()["reason"] or "read_failed"
                    if camera.last_received_at is not None:
                        gap_start = camera.last_received_at
                except CaptureGap as gap:
                    reason, gap_start = "source_stale", gap.start_s
                except KeyboardInterrupt:
                    self.stop.set()
                    raise
                except Exception:
                    reason, fatal = "processing_exception", "processing_failed"
                    self.health.finish("interrupted", reason)
                finally:
                    self._route(None)
                    camera.close(reason=reason)
                    closed = camera.wait_closed(self.policy.close_timeout_s)
                    detector = None
                    archived = self._archive(reason, gap_start)
                if not closed:
                    self._status_update(state="failed", reason="cleanup_unconfirmed")
                    return
                if not archived:
                    self._status_update(state="failed", reason="journal_failed")
                    return
                if self.stop.is_set():
                    break
                if camera.permanent_error or fatal is not None:
                    self._status_update(state="failed", reason=fatal or "permanent_source_error")
                    return
                status = self.snapshot()
                if status["retries"] >= self.policy.attempts:
                    self._status_update(state="exhausted", reason=reason)
                    return
                retry = status["retries"] + 1
                # Multiplicação saturada não estoura para um orçamento muito grande.
                delay = min(self.policy.backoff_s, self.policy.max_backoff_s)
                for _ in range(min(retry - 1, 1024)):
                    delay = min(delay * 2.0, self.policy.max_backoff_s)
                    if delay == self.policy.max_backoff_s:
                        break
                self._status_update(
                    state="backoff",
                    reason=reason,
                    retries=retry,
                    total_retries=status["total_retries"] + 1,
                    backoff_s=delay,
                )
                if self.wait(delay):
                    break
            self.health.finish("stopped", "user_stop")
            self._status_update(state="stopped", reason="user_stop", backoff_s=None)
        except KeyboardInterrupt:
            self.stop.set()
            self.health.finish("stopped", "user_stop")
            self._status_update(state="stopped", reason="user_stop")
            raise
