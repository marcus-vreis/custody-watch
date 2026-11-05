import threading

import numpy as np
import pytest

from custody_watch.aovivo import CameraAoVivo, CaptureGap, PermanentSourceError
from custody_watch.source_health import SourceHealth


def test_factory_bloqueada_nao_bloqueia_cancelamento_nem_abandona_captura_tardia():
    entered, release = threading.Event(), threading.Event()
    stop = threading.Event()

    class Capture:
        released = False

        def read(self):
            pytest.fail("não pode ler depois de pedir parada durante open")

        def release(self):
            self.released = True

    capture = Capture()

    def factory():
        entered.set()
        assert release.wait(2)
        return capture

    camera = CameraAoVivo(
        None, capture_factory=factory, parar=stop, source_health=SourceHealth("live")
    )
    stream = iter(camera)
    done = threading.Event()
    consumer = threading.Thread(target=lambda: (list(stream), done.set()), daemon=True)
    consumer.start()
    try:
        assert entered.wait(1)
        stop.set()
        assert done.wait(0.5)
        camera.close()
        assert camera.wait_closed(0.01) is False
    finally:
        release.set()
        consumer.join(1)
        assert camera.wait_closed(1)
    assert capture.released


def test_fim_de_leitura_e_confirmacao_de_release_sao_distintos():
    releasing, release = threading.Event(), threading.Event()

    class Capture:
        def read(self):
            return False, None

        def release(self):
            releasing.set()
            assert release.wait(2)

    camera = CameraAoVivo(Capture(), source_health=SourceHealth("live"))
    try:
        assert list(camera) == []
        assert releasing.wait(1)
        assert camera.wait_closed(0.01) is False
    finally:
        release.set()
        assert camera.wait_closed(1)


def test_gap_de_recepcao_nao_entrega_frame_posterior_a_associacao_antiga():
    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    clock = Clock()
    proceed = threading.Event()

    class Capture:
        reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, np.zeros((4, 4, 3), np.uint8)
            if self.reads == 2:
                assert proceed.wait(2)
                clock.now = 110.0
                return True, np.zeros((4, 4, 3), np.uint8)
            return False, None

        def release(self):
            pass

    health = SourceHealth("live", clock=clock)
    camera = CameraAoVivo(Capture(), relogio=clock, source_health=health)
    stream = iter(camera)
    try:
        assert next(stream)[0] == 0.0
        proceed.set()
        with pytest.raises(CaptureGap) as error:
            next(stream)
        assert error.value.start_s == 100.0
        assert error.value.end_s == 110.0
    finally:
        proceed.set()
        camera.close(reason="source_stale")
        assert camera.wait_closed(1)


def test_factory_permanente_fica_identificada_sem_publicar_erro_bruto():
    def factory():
        raise PermanentSourceError("permanent_failure")

    camera = CameraAoVivo(None, capture_factory=factory, source_health=SourceHealth("live"))

    assert list(camera) == []
    assert camera.wait_closed(1)
    assert camera.permanent_error is True


def test_fechar_captura_ainda_nao_iterada_libera_recurso():
    class Capture:
        released = False

        def release(self):
            self.released = True

    capture = Capture()
    camera = CameraAoVivo(capture)

    camera.close()

    assert camera.wait_closed(1)
    assert capture.released
