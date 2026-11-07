"""Reconexão sintética: sem rede, pesos, torch ou continuidade de custódia."""

import threading

import numpy as np
import pytest

from custody_watch.aovivo import PermanentSourceError
from custody_watch.config import Config
from custody_watch.event_journal import JournalWriter
from custody_watch.journal_reader import iter_journal
from custody_watch.live_recovery import LiveRecovery, RecoveryPolicy
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel
from custody_watch.source_health import SourceHealth
from custody_watch.tracking import TrackedDetection
from tests.test_occlusion import PLANO


class Capture:
    def __init__(self, frames=0, gate=None):
        self.frames, self.gate, self.released = frames, gate, False

    def read(self):
        if self.frames:
            self.frames -= 1
            return True, np.zeros((24, 32, 3), np.uint8)
        if self.gate is not None:
            assert self.gate.wait(2)
        return False, None

    def release(self):
        self.released = True


def build(tmp_path, factory, **kwargs):
    health = SourceHealth("live")
    writer = JournalWriter(tmp_path)
    panel = Painel(source_health=health)
    recovery = LiveRecovery(
        capture_factory=factory,
        detector_factory=kwargs.pop("detector_factory", lambda: lambda _image: []),
        session_factory=lambda log: LiveSession(PLANO, Config(), events=log),
        journal=writer,
        painel=panel,
        health=health,
        stop=kwargs.pop("stop", threading.Event()),
        policy=kwargs.pop("policy", RecoveryPolicy(attempts=2, backoff_s=0.001)),
        **kwargs,
    )
    return recovery, writer, panel


def test_reconnect_recria_detector_sessao_e_namespace_sem_herdar_dono(tmp_path):
    stop = threading.Event()
    first_done, second_done = threading.Event(), threading.Event()
    captures = [Capture(1, first_done), Capture(1, second_done)]
    opened, detectors, sessions = [], [], []

    def factory():
        if opened:
            assert captures[0].released, "não pode sobrepor leitores nem reset global do tracker"
        capture = captures[len(opened)]
        opened.append(capture)
        return capture

    def detector_factory():
        marker = object()
        detectors.append(marker)
        return lambda _image: [
            TrackedDetection(1, "person", (1.0, 1.0, 8.0, 20.0)),
            TrackedDetection(2, "suitcase", (3.0, 10.0, 5.0, 20.0)),
        ]

    def on_session(session, segment_id):
        if session is not None:
            sessions.append((session, segment_id))

    recovery, writer, panel = build(
        tmp_path, factory, stop=stop, detector_factory=detector_factory, on_session=on_session
    )

    def observed():
        session = recovery.session
        if len(sessions) == 1:
            bag = session.aponta_bagagem(4.0, 20.0)
            session.aponta_dono(bag.bag_id, 4.0, 20.0)
            assert session.anchors()[0].owner_party is not None
            first_done.set()
        else:
            assert session.anchors() == []
            assert session.person_level(1) is None
            stop.set()
            second_done.set()

    recovery.on_processed = observed
    try:
        recovery.run()
    finally:
        first_done.set()
        second_done.set()
        writer.close()
    assert len(detectors) == 2
    assert sessions[0][0] is not sessions[1][0]
    assert sessions[0][1] != sessions[1][1]
    assert all(c.released for c in captures)
    assert panel.estado()["segment_id"] == sessions[1][1]
    rows = list(iter_journal(writer.directory))
    assert [r["kind"] for r in rows].count("segment_start") == 2
    old_events = [r for r in rows if r["kind"] == "event"]
    assert old_events and all(r["segment_id"] == sessions[0][1] for r in old_events)
    starts = [r["payload"] for r in rows if r["kind"] == "segment_start"]
    assert starts[1]["gap_end_s"] >= starts[1]["gap_start_s"]


def test_esgota_tentativas_com_backoff_limitado_e_sem_erro_bruto(tmp_path):
    captures, waits = [], []

    def factory():
        capture = Capture()
        captures.append(capture)
        return capture

    recovery, writer, _ = build(
        tmp_path,
        factory,
        policy=RecoveryPolicy(attempts=3, backoff_s=2.0, max_backoff_s=3.0),
        wait=lambda delay: waits.append(delay) or False,
    )
    try:
        recovery.run()
        assert recovery.snapshot()["state"] == "exhausted"
        assert recovery.snapshot()["retries"] == 3
    finally:
        writer.close()
    assert len(captures) == 4
    assert waits == [2.0, 3.0, 3.0]
    assert all(c.released for c in captures)


def test_falha_permanente_nao_tenta_reabrir(tmp_path):
    calls = []

    def factory():
        calls.append(1)
        raise PermanentSourceError("rtsp://secret:password@camera/?token=x")

    recovery, writer, _ = build(tmp_path, factory)
    try:
        recovery.run()
        status = recovery.snapshot()
        assert status["state"] == "failed"
        assert status["reason"] == "permanent_source_error"
        assert "password" not in str(status)
    finally:
        writer.close()
    assert calls == [1]


def test_release_bloqueado_falha_sem_acumular_capturas(tmp_path):
    unblock = threading.Event()
    calls = []

    class Blocked(Capture):
        def release(self):
            assert unblock.wait(2)
            super().release()

    def factory():
        calls.append(1)
        return Blocked()

    recovery, writer, _ = build(tmp_path, factory, policy=RecoveryPolicy(close_timeout_s=0.01))
    try:
        recovery.run()
        assert recovery.snapshot()["reason"] == "cleanup_unconfirmed"
        assert calls == [1]
    finally:
        unblock.set()
        assert recovery.camera.wait_closed(1)
        writer.close()


def test_cancelamento_durante_backoff_nao_abre_outra_captura(tmp_path):
    calls = []
    recovery, writer, _ = build(
        tmp_path, lambda: calls.append(1) or Capture(), wait=lambda _delay: True
    )
    try:
        recovery.run()
        assert recovery.snapshot()["state"] == "stopped"
    finally:
        writer.close()
    assert calls == [1]


def test_abertura_inicial_falha_e_primeiro_frame_posterior_delimita_lacuna(tmp_path):
    stop = threading.Event()
    gate = threading.Event()
    captures = iter([Capture(), Capture(1, gate)])
    recovery, writer, _ = build(tmp_path, lambda: next(captures), stop=stop)

    def processed():
        stop.set()
        gate.set()

    recovery.on_processed = processed
    try:
        recovery.run()
        assert recovery.snapshot()["state"] == "stopped"
    finally:
        gate.set()
        writer.close()
    starts = [r for r in iter_journal(writer.directory) if r["kind"] == "segment_start"]
    assert len(starts) == 2
    assert starts[-1]["payload"]["gap_end_s"] >= starts[-1]["payload"]["gap_start_s"]


def test_erro_de_detector_e_fatal_e_nao_vaza_mensagem(tmp_path):
    calls = []

    def detector():
        raise RuntimeError("private-location/secret.pt")

    recovery, writer, _ = build(
        tmp_path, lambda: calls.append(1) or Capture(1), detector_factory=detector
    )
    try:
        recovery.run()
        assert recovery.snapshot()["reason"] == "processing_failed"
        assert "secret" not in str(recovery.snapshot())
        assert calls == [1]
    finally:
        writer.close()


def test_journal_falho_nao_permite_reconectar(tmp_path, monkeypatch):
    calls = []
    recovery, writer, _ = build(tmp_path, lambda: calls.append(1) or Capture())

    def disk_failure():
        raise OSError("disk full")

    monkeypatch.setattr(writer, "_close_handle", disk_failure)
    # Rotacionar na próxima escrita produz falha real no writer.
    writer._max_records = 1
    try:
        recovery.run()
        assert recovery.snapshot()["reason"] == "journal_failed"
        assert calls == [1]
    finally:
        writer.close()


def test_release_com_erro_nao_e_confirmacao_de_cleanup(tmp_path):
    calls = []

    class BadRelease(Capture):
        def release(self):
            raise OSError("backend release error")

    recovery, writer, _ = build(tmp_path, lambda: calls.append(1) or BadRelease())
    try:
        recovery.run()
        assert recovery.snapshot()["reason"] == "cleanup_unconfirmed"
        assert calls == [1]
    finally:
        writer.close()


def test_evento_real_interrompe_backoff_longo(tmp_path):
    stop, waiting = threading.Event(), threading.Event()
    calls = []

    def wait(delay):
        waiting.set()
        return stop.wait(delay)

    recovery, writer, _ = build(
        tmp_path,
        lambda: calls.append(1) or Capture(),
        stop=stop,
        wait=wait,
        policy=RecoveryPolicy(backoff_s=30.0),
    )
    worker = threading.Thread(target=recovery.run, daemon=True)
    worker.start()
    try:
        assert waiting.wait(1)
        stop.set()
        worker.join(0.5)
        assert not worker.is_alive()
        assert recovery.snapshot()["state"] == "stopped"
        assert calls == [1]
    finally:
        stop.set()
        worker.join(1)
        writer.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempts": -1},
        {"attempts": True},
        {"backoff_s": 0},
        {"backoff_s": float("nan")},
        {"max_backoff_s": float("inf")},
        {"close_timeout_s": -1},
    ],
)
def test_policy_recusa_limites_invalidos(kwargs):
    with pytest.raises(ValueError):
        RecoveryPolicy(**kwargs)
