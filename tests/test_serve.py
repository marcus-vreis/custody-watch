"""Montagem da fonte sem abrir câmera nem instanciar detector real."""

import argparse
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve.py"
SPEC = importlib.util.spec_from_file_location("custody_serve_script", SCRIPT)
serve = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serve)


def test_anuncio_mascara_credenciais_e_query_da_fonte(capsys):
    args = argparse.Namespace(
        fonte="rtsp://usuario:senha-teste@camera.local/video?token=privado",
        weights="local.pt",
        min_confidence=0.35,
        host="127.0.0.1",
    )

    serve._anuncia(args, SimpleNamespace(cifrado=False, porta=8765), True)

    output = capsys.readouterr().out
    assert "senha-teste" not in output
    assert "usuario" not in output
    assert "privado" not in output
    assert "camera.local" in output


def test_captura_que_nao_abriu_e_liberada_e_erro_nao_vaza_url(monkeypatch):
    class FailedCapture:
        released = False

        def isOpened(self):
            return False

        def release(self):
            self.released = True

    capture = FailedCapture()
    monkeypatch.setattr(serve.cv2, "VideoCapture", lambda _: capture)
    args = argparse.Namespace(fonte="rtsp://usuario:senha-teste@camera.local/video", passo=1)

    with pytest.raises(ValueError) as error:
        serve._fonte(args, threading.Event())

    assert capture.released
    assert "senha-teste" not in str(error.value)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_timeout_invalido_e_recusado_antes_de_abrir_fonte(monkeypatch, value):
    monkeypatch.setattr(sys, "argv", ["serve.py", "0", "--source-timeout", value])

    with pytest.raises(SystemExit) as error:
        serve._argumentos()

    assert error.value.code == 2


def test_timeout_valido_e_configuravel(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["serve.py", "0", "--source-timeout", "2.5"])

    assert serve._argumentos().source_timeout == 2.5


@pytest.mark.parametrize(
    "extra",
    [
        ["0", "--reconnect-attempts", "-1", "--journal-dir", "journal"],
        ["0", "--reconnect-attempts", "2"],
        ["file.mp4", "--reconnect-attempts", "2", "--journal-dir", "journal"],
        ["0", "--reconnect-attempts", "2", "--journal-dir", "journal", "--passo", "2"],
        ["0", "--reconnect-backoff", "nan"],
        ["0", "--reconnect-close-timeout", "0"],
    ],
)
def test_reconnect_recusa_configuracao_invalida_antes_de_abrir_fonte(monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", ["serve.py", *extra])
    with pytest.raises(SystemExit) as error:
        serve._argumentos()
    assert error.value.code == 2


def test_reconnect_e_opt_in_e_exige_pesos_locais_a_cada_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["serve.py", "0"])
    assert serve._argumentos().reconnect_attempts == 0
    args = argparse.Namespace(weights=str(tmp_path / "missing.pt"), min_confidence=0.35)
    with pytest.raises(ValueError, match="pesos locais"):
        serve._recovery_detector(args)()
    weights = tmp_path / "local.pt"
    weights.write_bytes(b"not-a-real-model")
    args.weights = str(weights)
    seen = []
    monkeypatch.setattr(serve, "detector_yolo", lambda path, **kw: seen.append(path) or object())
    factory = serve._recovery_detector(args)
    assert factory() is not factory()
    assert seen == [str(weights.resolve())] * 2
    weights.rename(tmp_path / "moved.pt")
    with pytest.raises(ValueError, match="pesos locais"):
        factory()


def test_montagem_do_serve_liga_assertivas_humanas_ao_journal(tmp_path):
    from custody_watch.config import Config
    from custody_watch.journal_reader import iter_journal
    from tests.test_occlusion import PLANO

    args = argparse.Namespace(
        journal_dir=tmp_path, journal_part_records=2, journal_fsync=False, event_tail=2
    )
    session, writer = serve._session(args, PLANO, Config())
    try:
        for x in (100.0, 500.0, 900.0):
            session.aponta_bagagem(x, 100.0)
        state = serve._journal_status(writer, session)
        assert state["retained_events"] == 2
        assert state["total_events"] == 3
        assert state["written_events"] == 3
    finally:
        writer.close()
    assert len([r for r in iter_journal(writer.directory) if r["kind"] == "event"]) == 3


def test_sem_journal_a_tela_declara_historico_apenas_em_ram():
    from custody_watch.config import Config
    from tests.test_occlusion import PLANO

    args = argparse.Namespace(
        journal_dir=None, journal_part_records=10, journal_fsync=False, event_tail=2
    )
    session, writer = serve._session(args, PLANO, Config())

    assert writer is None
    assert serve._journal_status(writer, session) == {
        "state": "disabled",
        "retained_events": 0,
        "total_events": 0,
    }


def test_main_fecha_segmento_de_arquivo_e_journal_sem_gpu(tmp_path, monkeypatch):
    import numpy as np

    from custody_watch.journal_reader import iter_journal

    args = argparse.Namespace(
        fonte="synthetic.mp4",
        source_timeout=5.0,
        config=None,
        calibration=None,
        metres_per_pixel=0.01,
        journal_dir=tmp_path,
        journal_part_records=10,
        journal_fsync=False,
        event_tail=10,
        cert=None,
        key=None,
        host="127.0.0.1",
        porta=0,
        weights="unused.pt",
        min_confidence=0.35,
    )
    monkeypatch.setattr(serve, "_argumentos", lambda: args)
    monkeypatch.setattr(
        serve,
        "_fonte",
        lambda *_args, **_kw: (
            [(0.0, np.zeros((24, 32, 3), np.uint8))],
            None,
        ),
    )
    monkeypatch.setattr(serve, "detector_yolo", lambda *_args, **_kw: lambda _image: [])

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve.time, "sleep", interrupt)

    assert serve.main() == 0
    directory = next(tmp_path.iterdir())
    records = list(iter_journal(directory))
    assert [record["kind"] for record in records] == ["segment_start", "segment_end"]
    assert records[-1]["payload"]["reason"] == "end_of_file"
