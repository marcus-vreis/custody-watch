"""Saúde da fonte e idade da imagem publicada; nenhum sinal entra na custódia."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

TERMINAL_STATES = frozenset({"interrupted", "completed", "stopped"})
REASONS = frozenset(
    {None, "read_failed", "capture_exception", "processing_exception", "user_stop", "end_of_file"}
)


def _finite(value: float, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: esperado número finito")
    return float(value)


class SourceHealth:
    """Estado O(1), consultável mesmo se a leitura ou o detector bloquearem.

    O relógio é monotônico e local. Nenhuma destas medidas inclui o tempo
    anterior à entrega do frame pelo backend de captura.
    """

    def __init__(
        self, kind: str, stale_after_s: float = 5.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if kind not in ("live", "file"):
            raise ValueError("kind: esperado live ou file")
        if _finite(stale_after_s, "timeout") <= 0:
            raise ValueError("timeout: precisa ser positivo")
        self.kind = kind
        self.stale_after_s = float(stale_after_s)
        self._clock = clock
        self._created_at = _finite(clock(), "clock")
        self._lock = threading.Lock()
        self._state = "initializing"
        self._reason = None
        self._received_frames = self._processed_frames = 0
        self._received_at = self._processed_at = self._image_received_at = None
        self._captured_media_t = self._processed_media_t = self._processing_delay_s = None

    def received(self, media_t: float, received_at: float | None = None) -> float:
        media_t = _finite(media_t, "media_t")
        if media_t < 0:
            raise ValueError("media_t: precisa ser não negativo")
        at = _finite(self._clock() if received_at is None else received_at, "received_at")
        with self._lock:
            self._received_frames += 1
            self._received_at, self._captured_media_t = at, media_t
            if self._state not in TERMINAL_STATES:
                self._state = "receiving"
        return at

    def processed(self, media_t: float, received_at: float) -> None:
        media_t = _finite(media_t, "media_t")
        if media_t < 0:
            raise ValueError("media_t: precisa ser não negativo")
        received_at = _finite(received_at, "received_at")
        now = _finite(self._clock(), "clock")
        with self._lock:
            self._processed_frames += 1
            self._processed_at, self._processed_media_t = now, media_t
            self._image_received_at = received_at
            self._processing_delay_s = max(0.0, now - received_at)

    def finish(self, state: str, reason: str | None = None) -> None:
        if state not in TERMINAL_STATES:
            raise ValueError("state: esperado estado terminal")
        if reason not in REASONS:
            raise ValueError("reason: use apenas código operacional conhecido")
        with self._lock:
            self._state, self._reason = state, reason

    def snapshot(self) -> dict:
        now = _finite(self._clock(), "clock")
        with self._lock:

            def age(at):
                return None if at is None else max(0.0, now - at)

            state = self._state
            reception = self._received_at if self._received_at is not None else self._created_at
            if state not in TERMINAL_STATES and now - reception > self.stale_after_s:
                state = "stale"
            image_age = age(self._image_received_at)
            return {
                "kind": self.kind,
                "state": state,
                "received_frames": self._received_frames,
                "processed_frames": self._processed_frames,
                "received_age_s": age(self._received_at),
                "processed_age_s": age(self._processed_at),
                "image_age_s": image_age,
                "processing_delay_s": self._processing_delay_s,
                "captured_media_t": self._captured_media_t,
                "processed_media_t": self._processed_media_t,
                "historical": state in TERMINAL_STATES
                or state == "stale"
                or (image_age is not None and image_age > self.stale_after_s),
                "stale_after_s": self.stale_after_s,
                "reason": self._reason,
            }
