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
