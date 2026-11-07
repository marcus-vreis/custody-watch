"""Saúde operacional observável, independente dos eventos de custódia."""

import json
import queue
import threading
import urllib.request

import numpy as np
import pytest

from custody_watch.aovivo import CameraAoVivo, processa
from custody_watch.config import Config
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel
from custody_watch.servidor import Servidor
from custody_watch.source_health import SourceHealth
from tests.test_occlusion import PLANO


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Capture:
    def __init__(self):
        self.frames = queue.Queue()
        self.reading = threading.Event()
        self.released = False

    def read(self):
        self.reading.set()
        item = self.frames.get(timeout=3.0)
        if isinstance(item, Exception):
            raise item
        return (False, None) if item is None else (True, item)

    def release(self):
        self.released = True


def test_falha_de_captura_fica_visivel_com_a_ultima_imagem_preservada():
    clock = Clock()
    health = SourceHealth("live", clock=clock)
    panel = Painel(source_health=health)
    capture = Capture()
    capture.frames.put(np.zeros((24, 32, 3), np.uint8))
    capture.frames.put(None)
    camera = CameraAoVivo(capture, relogio=clock, source_health=health)

    processa(
        iter(camera),
        lambda _: [],
        LiveSession(PLANO, Config()),
        panel,
        source_health=health,
        capture_time=camera.received_at,
    )

    assert camera.encerrada.wait(1.0)
    assert panel.jpeg().startswith(b"\xff\xd8")
    assert panel.estado()["fonte"]["state"] == "interrupted"
    assert panel.estado()["fonte"]["historical"] is True
    assert panel.estado()["fonte"]["received_frames"] == 1
    assert panel.estado()["fonte"]["processed_frames"] == 1
    assert panel.estado()["eventos"] == []
    assert capture.released


def test_leitura_bloqueada_envelhece_sem_novo_frame_e_para_em_tempo_limitado():
    clock = Clock()
    stop = threading.Event()
    health = SourceHealth("live", stale_after_s=5, clock=clock)
    panel = Painel(source_health=health)
    capture = Capture()
    camera = CameraAoVivo(capture, relogio=clock, parar=stop, source_health=health)
    frames = iter(camera)
    capture.frames.put(np.zeros((24, 32, 3), np.uint8))
    try:
        t, image = next(frames)
        panel.atualiza(image, {"t": t})
        health.processed(t, received_at=camera.received_at(t))
        jpeg = panel.jpeg()
        clock.now = 106.0

        assert panel.estado()["fonte"]["state"] == "stale"
        assert panel.estado()["fonte"]["received_age_s"] == 6.0
        assert panel.jpeg() == jpeg
        stop.set()
        done = threading.Event()
        consumer = threading.Thread(target=lambda: (list(frames), done.set()), daemon=True)
        consumer.start()
        assert done.wait(0.5)
        consumer.join(0.5)
    finally:
        stop.set()
        capture.frames.put(None)
        assert camera.encerrada.wait(1.0)


def test_frames_identicos_novos_continuam_saudaveis():
    clock = Clock()
    stop = threading.Event()
    health = SourceHealth("live", clock=clock)
    capture = Capture()
    camera = CameraAoVivo(capture, relogio=clock, parar=stop, source_health=health)
    frames = iter(camera)
    image = np.zeros((24, 32, 3), np.uint8)
    try:
        for instant in (100.0, 104.0, 108.0):
            clock.now = instant
            capture.frames.put(image.copy())
            next(frames)
            assert health.snapshot()["state"] == "receiving"
        assert health.snapshot()["received_frames"] == 3
    finally:
        stop.set()
        capture.frames.put(None)
        assert camera.encerrada.wait(1.0)
        frames.close()


def test_arquivo_concluido_e_historico_sem_virar_falha():
    clock = Clock()
    health = SourceHealth("file", clock=clock)
    panel = Painel(source_health=health)
    session = LiveSession(PLANO, Config())
    frames = [(0.0, np.zeros((24, 32, 3), np.uint8)), (0.1, np.zeros((24, 32, 3), np.uint8))]

    processa(frames, lambda _: [], session, panel, source_health=health)

    assert panel.estado()["fonte"]["state"] == "completed"
    assert panel.estado()["fonte"]["historical"] is True
    assert panel.estado()["fonte"]["processed_frames"] == 2
    assert panel.estado()["fila"] == []
    assert list(session.result().events) == []


def test_excecao_da_captura_nao_vaza_url_nem_credencial(capsys):
    health = SourceHealth("live")
    capture = Capture()
    capture.frames.put(RuntimeError("rtsp://usuario:segredo-teste@camera/stream"))
    camera = CameraAoVivo(capture, source_health=health)

    assert list(camera) == []
    assert camera.encerrada.wait(1.0)
    assert health.snapshot()["state"] == "interrupted"
    assert health.snapshot()["reason"] == "capture_exception"
    assert "segredo-teste" not in json.dumps(health.snapshot())
    assert "segredo-teste" not in str(capsys.readouterr())


def test_http_informa_idade_nova_sem_receber_ou_processar_frame():
    clock = Clock()
    health = SourceHealth("live", clock=clock)
    panel = Painel(source_health=health)
    health.received(0.0)
    panel.atualiza(np.zeros((24, 32, 3), np.uint8), {"t": 0.0})
    health.processed(0.0, received_at=100.0)
    server = Servidor(panel, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        clock.now = 107.0
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.porta}/estado", timeout=2
        ) as response:
            data = json.loads(response.read())
        assert data["fonte"]["state"] == "stale"
        assert data["fonte"]["image_age_s"] == 7.0
    finally:
        server.encerra()
        thread.join(1.0)


def test_falha_no_detector_marca_interrupcao_sem_evento_operacional_na_fsm():
    health = SourceHealth("file")
    panel = Painel(source_health=health)
    session = LiveSession(PLANO, Config())

    def fails(_image):
        raise ValueError("detector falhou")

    with pytest.raises(ValueError, match="detector falhou"):
        processa(
            [(0.0, np.zeros((24, 32, 3), np.uint8))], fails, session, panel, source_health=health
        )

    assert health.snapshot()["state"] == "interrupted"
    assert session.frames == 0
    assert list(session.result().events) == []
