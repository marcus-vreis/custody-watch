"""Journal incremental, rotacionado e sem restauração implícita de custódia."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from .events import Event
from .journal_reader import validate_record

START_REASONS = frozenset({"start", "reconnected"})
END_REASONS = frozenset({"user_stop", "end_of_file", "initialization_failed"})
INTERRUPTION_REASONS = frozenset(
    {
        "read_failed",
        "capture_exception",
        "processing_exception",
        "source_stale",
    }
)


class JournalWriter:
    """Cada registro é flushado; fsync é opt-in e tem confirmação separada.

    IO é serializado por uma trava própria. Consultar a saúde não aguarda
    o disco: usa outra trava, mantida só para atualizar contadores.
    A primeira falha impede novas escritas; o leitor recupera o prefixo.
    """

    def __init__(
        self,
        directory: Path | str,
        *,
        session_id: str | None = None,
        max_records_per_file: int = 10000,
        fsync: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(max_records_per_file) is not int or max_records_per_file <= 0:
            raise ValueError("max_records_per_file: esperado inteiro positivo")
        if type(fsync) is not bool:
            raise ValueError("fsync: esperado booleano")
        self.session_id = session_id if session_id is not None else uuid4().hex
        if not isinstance(self.session_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", self.session_id
        ):
            raise ValueError("session_id: slug inválido")
        base = Path(directory)
        base.mkdir(parents=True, exist_ok=True)
        self.directory = base / self.session_id
        self.directory.mkdir(exist_ok=False)
        self._max_records = max_records_per_file
        self._fsync = fsync
        self._clock = clock
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._handle = None
        self._part = self._part_records = self._next_sequence = 0
        self._active_segment = None
        self._state = "writing"
        self._closed = False
        self._error = None
        self._written_events = self._unconfirmed_events = self._written_records = 0
        self._pending_records = self._segments = 0
        self._last_written_sequence = self._synced_through = None
        if self.start_segment(reason="start") is None:
            raise OSError("não foi possível iniciar o journal")

    @property
    def segment_id(self) -> str | None:
        with self._state_lock:
            return self._active_segment

    def _close_handle(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()

    def _write(self, kind: str, payload: dict, segment_id: str) -> bool:
        record = {
            "schema_version": 1,
            "session_id": self.session_id,
            "segment_id": segment_id,
            "sequence": self._next_sequence,
            "kind": kind,
            "payload": payload,
        }
        stage = "serialization_failed"
        try:
            validate_record(record)
            line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
            with self._state_lock:
                self._pending_records = 1
            stage = "write_failed"
            if self._handle is not None and self._part_records >= self._max_records:
                self._close_handle()
                self._part += 1
                self._part_records = 0
            if self._handle is None:
                if self._part > 999999:
                    raise OSError("limite de partes do journal atingido")
                self._handle = (self.directory / f"{self._part:06d}.jsonl").open(
                    "x",
                    encoding="utf-8",
                    newline="\n",
                )
            if self._handle.write(line) != len(line):
                raise OSError("escrita parcial do registro")
            self._handle.flush()
            self._part_records += 1
            self._next_sequence += 1
            with self._state_lock:
                self._written_records += 1
                self._last_written_sequence = record["sequence"]
                self._written_events += int(kind == "event")
            if self._fsync:
                stage = "sync_failed"
                os.fsync(self._handle.fileno())
                with self._state_lock:
                    self._synced_through = record["sequence"]
            return True
        except (OSError, TypeError, ValueError):
            with self._state_lock:
                self._state, self._error = "failed", stage
            with suppress(OSError):
                self._close_handle()
            return False
        finally:
            with self._state_lock:
                self._pending_records = 0

    def _lifecycle(self, reason, gap_start_s=None, gap_end_s=None) -> dict:
        payload = {"reason": reason, "local_monotonic_s": self._clock()}
        if gap_start_s is not None:
            payload["gap_start_s"] = gap_start_s
        if gap_end_s is not None:
            payload["gap_end_s"] = gap_end_s
        return payload

    def start_segment(
        self, *, reason: str = "reconnected", gap_start_s=None, gap_end_s=None
    ) -> str | None:
        if reason not in START_REASONS:
            raise ValueError("reason de início inválido")
        with self._io_lock:
            if self._closed or self._state == "failed":
                return None
            if self._active_segment is not None:
                raise ValueError("feche o segmento anterior antes de iniciar outro")
            segment_id = uuid4().hex
            if not self._write(
                "segment_start", self._lifecycle(reason, gap_start_s, gap_end_s), segment_id
            ):
                return None
            with self._state_lock:
                self._active_segment = segment_id
                self._segments += 1
            return segment_id

    def emit(self, event: Event, *, segment_id: str | None = None) -> bool:
        """False significa falta de confirmação; o evento pode existir no disco.

        Quem liga um EventLog a um segmento pode passar o ID fixo para recusar
        um evento tardio daquela associação após uma reconexão.
        """
        with self._io_lock:
            if (
                self._closed
                or self._state == "failed"
                or self._active_segment is None
                or (segment_id is not None and segment_id != self._active_segment)
            ):
                with self._state_lock:
                    self._unconfirmed_events += 1
                return False
            written = self._write("event", event.to_dict(), self._active_segment)
            if not written:
                with self._state_lock:
                    self._unconfirmed_events += 1
            return written

    def _finish_segment(
        self, interrupted: bool, reason: str, gap_start_s=None, gap_end_s=None
    ) -> bool:
        if self._active_segment is None:
            return True
        if self._closed or self._state == "failed":
            return False
        kind = "segment_interrupted" if interrupted else "segment_end"
        if not self._write(
            kind, self._lifecycle(reason, gap_start_s, gap_end_s), self._active_segment
        ):
            return False
        with self._state_lock:
            self._active_segment = None
        return True

    def finish_segment(
        self,
        *,
        interrupted: bool = False,
        reason: str = "user_stop",
        gap_start_s=None,
        gap_end_s=None,
    ) -> bool:
        if reason not in (INTERRUPTION_REASONS if interrupted else END_REASONS):
            raise ValueError("reason de encerramento inválido")
        with self._io_lock:
            return self._finish_segment(interrupted, reason, gap_start_s, gap_end_s)

    def close(self, *, reason: str = "user_stop") -> None:
        if reason not in END_REASONS:
            raise ValueError("reason de fechamento inválido")
        with self._io_lock:
            if self._closed:
                return
            self._finish_segment(False, reason)
            try:
                self._close_handle()
            except OSError:
                with self._state_lock:
                    self._state, self._error = "failed", "close_failed"
            with self._state_lock:
                self._closed = True
                if self._state != "failed":
                    self._state = "closed"

    def snapshot(self) -> dict:
        with self._state_lock:
            return {
                "state": self._state,
                "error": self._error,
                "closed": self._closed,
                "session_id": self.session_id,
                "segment_id": self._active_segment,
                "written_events": self._written_events,
                "unconfirmed_events": self._unconfirmed_events,
                "written_records": self._written_records,
                "pending_records": self._pending_records,
                "last_written_sequence": self._last_written_sequence,
                "synced_through": self._synced_through,
                "fsync": self._fsync,
                "part_index": self._part,
                "segments": self._segments,
            }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.finish_segment(interrupted=True, reason="processing_exception")
        self.close()
