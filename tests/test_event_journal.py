"""Persistência incremental real em disco, incluindo falhas e cauda em RAM."""

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from custody_watch.event_journal import JournalWriter
from custody_watch.events import Event, EventKind, EventLog
from custody_watch.journal_reader import iter_journal
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel
from custody_watch.servidor import Servidor
from custody_watch.source_health import SourceHealth
from tests.test_occlusion import PLANO


def event(t=1.0, bag=9):
    return Event(EventKind.BAG_OWNED, t, t, None, bag, 1, {"origem": "operador"})


def test_evento_flushado_e_lido_sem_encerramento_normal(tmp_path):
    writer = JournalWriter(tmp_path, session_id="test-session")
    try:
        assert writer.emit(event()) is True
        diagnostics = []
        rows = list(iter_journal(writer.directory, diagnostics=diagnostics))

        assert rows[-1]["payload"] == event().to_dict()
        assert diagnostics  # Segmento ainda aberto; não fingir encerramento normal.
        assert writer.snapshot()["written_events"] == 1
        assert writer.snapshot()["synced_through"] is None
    finally:
        writer.close()


def test_rotacao_preserva_ordem_e_nao_reutiliza_sessao(tmp_path):
    with JournalWriter(tmp_path, session_id="unique-session", max_records_per_file=2) as writer:
        for t in range(7):
            assert writer.emit(event(float(t)))

    rows = list(iter_journal(writer.directory))
    assert len(list(writer.directory.glob("*.jsonl"))) == 5
    assert [row["sequence"] for row in rows] == list(range(9))
    assert [row["payload"]["t_start"] for row in rows if row["kind"] == "event"] == list(range(7))
    with pytest.raises(FileExistsError):
        JournalWriter(tmp_path, session_id="unique-session")


def test_uma_cauda_limitada_nao_apaga_o_historico_em_disco(tmp_path):
    with JournalWriter(tmp_path) as writer:
        log = EventLog(max_events=3, sink=writer.emit)
        for t in range(20):
            log.emit(event(float(t)))

    assert len(log) == 3
    assert log.total_events == 20
    assert [item.t_start for item in log] == [17, 18, 19]
    assert [item.t_start for item in reversed(log)] == [19, 18, 17]
    assert len([row for row in iter_journal(writer.directory) if row["kind"] == "event"]) == 20


def test_falha_de_escrita_fica_no_estado_sem_apagar_evento_volatil(tmp_path):
    writer = JournalWriter(tmp_path)
    original = writer._handle

    class Fails:
        def write(self, value):
            original.write(value[:12])
            original.flush()
            raise OSError("caminho-pessoal e credencial-teste não podem ir para o painel")

        def close(self):
            original.close()

    writer._handle = Fails()
    log = EventLog(max_events=3, sink=writer.emit)
    panel = Painel(journal_status=writer.snapshot)

    log.emit(event())
    log.emit(event(2.0))

    state = panel.estado()["journal"]
    assert state["state"] == "failed"
    assert state["written_events"] == 0
    assert state["unconfirmed_events"] == 2
    assert len(log) == 2
    assert "credencial-teste" not in json.dumps(state)
    diagnostics = []
    rows = list(iter_journal(writer.directory, diagnostics=diagnostics))
    assert [row["kind"] for row in rows] == ["segment_start"]
    assert diagnostics
    writer.close()


def test_fsync_tem_confirmacao_distinta_de_escrita(tmp_path):
    with JournalWriter(tmp_path, fsync=True) as writer:
        assert writer.emit(event())
        state = writer.snapshot()

        assert state["last_written_sequence"] == 1
        assert state["synced_through"] == 1
        assert state["written_events"] == 1


def test_novo_segmento_nao_aceita_evento_do_segmento_anterior(tmp_path):
    with JournalWriter(tmp_path) as writer:
        old_segment = writer.segment_id
        assert writer.finish_segment(interrupted=True, reason="read_failed")
        new_segment = writer.start_segment(reason="reconnected")
        assert new_segment != old_segment
        assert writer.emit(event(), segment_id=old_segment) is False
        assert writer.emit(event(2), segment_id=new_segment) is True

    rows = list(iter_journal(writer.directory))
    events = [row for row in rows if row["kind"] == "event"]
    assert len(events) == 1
    assert events[0]["segment_id"] == new_segment
    assert events[0]["payload"]["t_start"] == 2


def test_sessao_preserva_origem_humana_e_id_negativo_no_journal(tmp_path):
    with JournalWriter(tmp_path) as writer:
        log = EventLog(max_events=3, sink=writer.emit)
        session = LiveSession(PLANO, events=log)
        bag = session.aponta_bagagem(100.0, 100.0)

        assert session.events is log
        assert bag.bag_id < 0

    rows = [row for row in iter_journal(writer.directory) if row["kind"] == "event"]
    assert rows[0]["payload"]["evidence"]["origem"] == "operador"
    assert rows[0]["payload"]["bag"] == bag.bag_id
    assert set(rows[0]) == {
        "schema_version",
        "session_id",
        "segment_id",
        "sequence",
        "kind",
        "payload",
    }


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_cauda_precisa_de_limite_inteiro_positivo(value):
    with pytest.raises(ValueError, match="max_events"):
        EventLog(max_events=value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_rotacao_precisa_de_limite_inteiro_positivo(tmp_path, value):
    with pytest.raises(ValueError, match="max_records"):
        JournalWriter(tmp_path, max_records_per_file=value)


def test_imagem_historica_nao_aceita_nova_assercao_em_segmento_encerrado(tmp_path):
    with JournalWriter(tmp_path) as writer:
        log = EventLog(max_events=10, sink=writer.emit)
        session = LiveSession(PLANO, events=log)
        health = SourceHealth("live")
        panel = Painel(source_health=health)
        panel.atualiza(np.zeros((24, 32, 3), np.uint8), {})
        health.finish("interrupted", "read_failed")
        writer.finish_segment(interrupted=True, reason="read_failed")
        server = Servidor(panel, "127.0.0.1", 0, sessao=session)
        thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.porta}/ancora",
                data=b'{"x":0.5,"y":0.5}',
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=2)
            assert error.value.code == 409
            assert session.apontadas() == {}
            assert writer.snapshot()["unconfirmed_events"] == 0
        finally:
            server.encerra()
            thread.join(1)


def test_escrita_curta_nao_e_confirmada_como_evento_completo(tmp_path):
    writer = JournalWriter(tmp_path)
    original = writer._handle

    class ShortWrite:
        def write(self, value):
            return original.write(value[:12])

        def flush(self):
            original.flush()

        def close(self):
            original.close()

    writer._handle = ShortWrite()
    try:
        assert writer.emit(event()) is False
        assert writer.snapshot()["written_events"] == 0
        assert writer.snapshot()["unconfirmed_events"] == 1
    finally:
        writer.close()


def test_consulta_de_saude_e_cauda_nao_aguardam_escrita_bloqueada(tmp_path):
    writer = JournalWriter(tmp_path)
    original = writer._handle
    writing, release, queried = threading.Event(), threading.Event(), threading.Event()

    class BlockedWrite:
        def write(self, value):
            writing.set()
            assert release.wait(2)
            return original.write(value)

        def flush(self):
            original.flush()

        def close(self):
            original.close()

    writer._handle = BlockedWrite()
    log = EventLog(max_events=3, sink=writer.emit)
    producer = threading.Thread(target=lambda: log.emit(event()), daemon=True)
    snapshots = []

    def query():
        snapshots.append((writer.snapshot(), len(log)))
        queried.set()

    producer.start()
    try:
        assert writing.wait(1)
        reader = threading.Thread(target=query, daemon=True)
        reader.start()
        assert queried.wait(0.5)
        assert snapshots[0][0]["pending_records"] == 1
        assert snapshots[0][1] == 0
    finally:
        release.set()
        producer.join(1)
        writer.close()


def test_lifecycle_registra_relogio_e_limites_da_lacuna(tmp_path):
    with JournalWriter(tmp_path, clock=lambda: 120.0) as writer:
        writer.finish_segment(interrupted=True, reason="read_failed", gap_start_s=100.0)
        writer.start_segment(reason="reconnected", gap_start_s=100.0, gap_end_s=119.0)

    rows = list(iter_journal(writer.directory))
    assert rows[1]["payload"] == {
        "reason": "read_failed",
        "local_monotonic_s": 120.0,
        "gap_start_s": 100.0,
    }
    assert rows[2]["payload"]["gap_end_s"] == 119.0
    assert rows[2]["payload"]["gap_start_s"] == 100.0
