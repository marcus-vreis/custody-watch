import json

import pytest

from custody_watch.source_health import SourceHealth


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def test_sem_primeiro_frame_tambem_fica_desatualizado():
    clock = Clock()
    health = SourceHealth("live", clock=clock)
    assert health.snapshot()["state"] == "initializing"
    clock.now += 6
    assert health.snapshot()["state"] == "stale"
    assert health.snapshot()["received_age_s"] is None


def test_relogios_de_captura_imagem_e_processamento_sao_separados():
    clock = Clock()
    health = SourceHealth("live", clock=clock)
    received = health.received(0.0)
    clock.now = 103.0
    health.processed(0.0, received_at=received)
    clock.now = 106.0
    health.received(6.0)

    state = health.snapshot()

    assert state["state"] == "receiving"
    assert state["received_age_s"] == 0
    assert state["processed_age_s"] == 3
    assert state["image_age_s"] == 6
    assert state["processing_delay_s"] == 3
    assert state["historical"] is True
    assert state["received_frames"] == 2
    assert state["processed_frames"] == 1
    assert state["captured_media_t"] == 6
    assert state["processed_media_t"] == 0
    json.dumps(state, allow_nan=False)


@pytest.mark.parametrize("state", ["completed", "interrupted", "stopped"])
def test_processamento_tardio_nao_reabre_estado_terminal(state):
    health = SourceHealth("file")
    received = health.received(0.0)
    health.finish(state)
    health.processed(0.0, received_at=received)

    assert health.snapshot()["state"] == state
    assert health.snapshot()["historical"] is True


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_timeout_invalido_nao_cria_monitor(timeout):
    with pytest.raises(ValueError, match="timeout"):
        SourceHealth("live", stale_after_s=timeout)


def test_reason_nao_aceita_texto_externo():
    health = SourceHealth("live")
    with pytest.raises(ValueError, match="reason"):
        health.finish("interrupted", "rtsp://usuario:senha@cam")


def test_reinicio_preserva_imagem_historica_ate_primeira_publicacao_nova():
    clock = Clock()
    health = SourceHealth("live", clock=clock)
    received = health.received(0.0)
    health.processed(0.0, received_at=received)
    health.finish("interrupted", "read_failed")
    clock.now = 101.0

    health.restart()
    health.received(0.0)

    assert health.snapshot()["state"] == "receiving"
    assert health.snapshot()["historical"] is True
    health.processed(0.0, received_at=101.0)
    assert health.snapshot()["historical"] is False
    assert health.snapshot()["received_frames"] == health.snapshot()["processed_frames"] == 2
