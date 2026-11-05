"""Limites estruturais do harness; sem asserts de FPS ou bytes dependentes do host."""

import importlib.util
import json
import subprocess
import sys
from collections import deque
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from custody_watch.config import Config
from custody_watch.event_journal import JournalWriter
from custody_watch.events import EventLog
from custody_watch.ground_plane import GroundPlane
from custody_watch.journal_reader import iter_journal
from custody_watch.lifecycle_probe import lifecycle_smoke
from custody_watch.orchestrator import LiveSession
from custody_watch.session_probe import RETENTION, SessionProbe, synthetic_frames
from custody_watch.types import BagState, Observation, Point


def session(tail=8, sink=None):
    return LiveSession(
        GroundPlane(np.diag([0.01, 0.01, 1.0])),
        Config(),
        events=EventLog(max_events=tail, sink=sink),
    )


def test_prefixos_usam_as_mesmas_observacoes_seed_e_falhas():
    short = list(synthetic_frames(200, seed=7))
    long = list(synthetic_frames(400, seed=7))
    assert short == long[:200]
    assert any(f.dropped for f in short)
    assert sum(f.occluded for f in short) > 2
    assert any(f.quiet for f in short)
    assert short[0].tracked[0].track_id != short[100].tracked[0].track_id


def test_colecao_nova_exige_atualizar_instrumentacao_e_contrato_de_retencao():
    live = session()
    state = live._s
    expected = [
        (live, {"_apontadas", "_ultimo_tracked"}),
        (
            state,
            {
                "history",
                "missing",
                "pending_bags",
                "vetoes",
                "ambiguous_tracks",
                "near_since",
                "proximity_flagged",
                "contact_flagged",
                "together_since",
            },
        ),
        (state.registry, {"_bags", "_by_track", "_unresolved"}),
        (state.linker, {"_windows", "_canonical", "_pending", "_pending_since"}),
        (state.parties, {"_parties", "_party_of"}),
        (state.flags, {"_by_person"}),
        (state.gate, {"_last"}),
        (state.events, {"_events"}),
    ]
    for obj, fields in expected:
        collections = {
            key for key, value in vars(obj).items() if isinstance(value, (dict, list, set, deque))
        }
        assert collections == fields


def test_medicao_nao_altera_semantica_nem_inventa_eventos_em_intervalo_quieto():
    plain, measured = session(), session()
    before_quiet = None
    with SessionProbe(measured) as probe:
        for frame in synthetic_frames(100, seed=7):
            if frame.dropped:
                continue
            plain.feed(frame.t, list(frame.tracked))
            probe.feed(frame.t, list(frame.tracked))
            if frame.index == 79:
                before_quiet = measured.events.total_events
        counts = probe.counts()
        assert probe.timings()["feed"]["calls"] == measured.frames
        assert probe.timings()["observe_bags"]["calls"] == measured.frames
    assert [e.to_dict() for e in plain.events] == [e.to_dict() for e in measured.events]
    assert measured.events.total_events == before_quiet
    assert counts["events_retained"] <= 8
    assert counts.keys() == RETENTION.keys()
    assert all(type(value) is int and value >= 0 for value in counts.values())
    json.dumps(counts, allow_nan=False)


def test_snapshot_expoe_crescimento_que_a_cauda_de_eventos_esconde():
    measured = session(tail=2)
    cuts = {}
    with SessionProbe(measured) as probe:
        for frame in synthetic_frames(400, seed=7):
            if not frame.dropped:
                probe.feed(frame.t, list(frame.tracked))
            if frame.index in (199, 399):
                cuts[frame.index] = probe.counts()
    assert cuts[399]["history_identities"] > cuts[199]["history_identities"]
    assert cuts[399]["linker_windows"] > cuts[199]["linker_windows"]
    assert cuts[399]["events_retained"] <= 2
    assert cuts[399]["events_total"] > cuts[399]["events_retained"]
    assert measured.anchors(), "medir não pode apagar posse/âncora ativa"


def test_indice_nao_terminal_preserva_eventos_estados_fila_e_posse_da_varredura_completa():
    baseline, indexed = session(tail=1000), session(tail=1000)
    baseline._s.registry.unresolved = baseline._s.registry.all
    for frame in synthetic_frames(1200, seed=7):
        if not frame.dropped:
            baseline.feed(frame.t, list(frame.tracked))
            indexed.feed(frame.t, list(frame.tracked))
    assert [e.to_dict() for e in indexed.events] == [e.to_dict() for e in baseline.events]
    assert [asdict(b) for b in indexed._s.registry.all()] == [
        asdict(b) for b in baseline._s.registry.all()
    ]
    assert indexed.queue() == baseline.queue()
    assert len(indexed._s.registry.unresolved()) < len(indexed._s.registry.all())


def test_caminhos_por_frame_e_painel_nao_pedem_registro_completo_de_terminais():
    live = session()
    registry = live._s.registry
    for identity in range(100):
        bag = registry.observe(Observation(identity, "suitcase", Point(0.0, 0.0), 0.0))
        bag.state = BagState.RETIRADA_DONO
    current = registry.observe(Observation(101, "suitcase", Point(5.0, 5.0), 0.0))
    registry.assign_owner(101, 44)
    live.feed(0.0, [])  # primeira consulta retira recém-terminais do índice

    class NoScan(dict):
        def values(self):
            pytest.fail("feed/anchors não podem varrer o registro histórico completo")

    registry._bags = NoScan(registry._bags)
    live.feed(0.2, [])
    assert live.anchors() == [current]
    assert current.owner_party == 44
    assert registry.get(1).state is BagState.RETIRADA_DONO


def test_probe_restaura_metodos_apos_excecao():
    measured = session()
    original = measured._s.observe_bags
    attributes = set(vars(measured._s))
    try:
        with SessionProbe(measured):
            raise RuntimeError("interrupção sintética")
    except RuntimeError:
        pass
    assert measured._s.observe_bags == original
    assert set(vars(measured._s)) == attributes


def test_journal_e_prefixo_continuam_legiveis_apos_interrupcao(tmp_path):
    writer = JournalWriter(tmp_path, max_records_per_file=3)
    measured = session(sink=writer.emit)
    with SessionProbe(measured) as probe:
        for frame in synthetic_frames(100, seed=7):
            if not frame.dropped:
                probe.feed(frame.t, list(frame.tracked))
    writer.finish_segment(interrupted=True, reason="read_failed")
    writer.close()
    rows = list(iter_journal(writer.directory))
    assert sum(r["kind"] == "event" for r in rows) == measured.events.total_events
    assert rows[-1]["kind"] == "segment_interrupted"


def test_smoke_de_falhas_fecha_leitores_e_preserva_estado_consultavel(tmp_path):
    result = lifecycle_smoke(tmp_path)
    assert result["recovered"]["status"]["state"] == "stopped"
    assert result["recovered"]["opened"] == 2
    assert result["exhausted"]["status"]["state"] == "exhausted"
    assert result["blocked"]["status"]["reason"] == "cleanup_unconfirmed"
    assert result["blocked"]["opened"] == 1
    for case in result.values():
        assert case["opened"] == case["released"]
        assert case["owned_reader_alive"] is False
        assert case["journal_records"] >= 2
        assert case["panel_readable"] is True


def test_cli_publica_falha_de_orcamento_e_resultado_com_proveniencia(tmp_path):
    output = tmp_path / "run"
    script = Path(__file__).resolve().parents[1] / "scripts" / "soak_session.py"
    command = [
        sys.executable,
        str(script),
        "--frames",
        "300",
        "--sample-every",
        "100",
        "--max-identities",
        "1",
        "--output",
        str(output),
    ]
    run = subprocess.run(command, capture_output=True, timeout=10)
    assert run.returncode == 2, run.stderr.decode(errors="replace")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "budget_exceeded"
    assert "history_identities" in summary["exceeded"]
    assert summary["resources_ok"] is True
    assert len(summary["revision"]) == 40
    assert summary["simulated_s"] == 59.8
    assert summary["frames"]["received"] == 300
    assert summary["frames"]["processed"] + summary["frames"]["discarded"] == 300
    assert len((output / "snapshots.jsonl").read_text().splitlines()) == 3
    second = subprocess.run(command, capture_output=True, timeout=10)
    assert second.returncode == 2
    assert b"output" in second.stderr


def test_ctrl_c_deixa_resumo_e_journal_legiveis(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "soak_session.py"
    spec = importlib.util.spec_from_file_location("soak_interrupt", script)
    program = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(program)
    output = tmp_path / "interrupted"
    monkeypatch.setattr(sys, "argv", [str(script), "--output", str(output), "--sample-every", "10"])
    original = program.SessionProbe.feed

    def interrupt(probe, t, tracks):
        if t >= 6.0:
            raise KeyboardInterrupt
        return original(probe, t, tracks)

    monkeypatch.setattr(program.SessionProbe, "feed", interrupt)
    try:
        code = program.main()
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C deve fechar o journal e preservar resumo do último checkpoint")
    assert code == 130
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "interrupted"
    assert summary["frames"]["received"] == 30
    rows = list(iter_journal(next((output / "journal").iterdir())))
    assert rows[-1]["kind"] == "segment_end"
    assert rows[-1]["payload"]["reason"] == "user_stop"
