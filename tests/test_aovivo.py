"""O laço ao vivo: quadro entra, a fila sai -- e câmera atrasada perde o quadro
velho em vez de acumular atraso."""

import threading
import time

import cv2
import numpy as np
import pytest

from custody_watch.aovivo import CameraAoVivo, fonte_de, processa, quadros_de_arquivo
from custody_watch.config import Config
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel
from custody_watch.tracking import TrackedDetection
from tests.test_occlusion import PLANO, cena_com_furto


def _detector_de(cena):
    """Detector falso que devolve, na ordem, as caixas de uma cena sintética."""
    proximas = iter([tracked for _, tracked in cena])
    return lambda _imagem: next(proximas)


def _quadros_de(cena):
    return ((t, np.zeros((120, 160, 3), np.uint8)) for t, _ in cena)


def test_processa_poe_o_furto_na_tela():
    cena = list(cena_com_furto())
    sessao = LiveSession(PLANO, Config())
    painel = Painel()

    processa(_quadros_de(cena), _detector_de(cena), sessao, painel)

    assert painel.jpeg() is not None
    fila = painel.estado()["fila"]
    assert fila[0]["nivel"] == "N3"
    assert painel.estado()["quadros"] == len(cena)


def test_processa_para_quando_pedem():
    cena = list(cena_com_furto())
    parar = threading.Event()
    parar.set()
    sessao = LiveSession(PLANO, Config())

    processa(_quadros_de(cena), _detector_de(cena), sessao, Painel(), parar=parar)

    assert sessao.frames == 0


def _pessoas_em_perspectiva():
    """Gente de 100px no fundo e de 400px na frente: uma escala só não serve."""
    for i in range(40):
        altura = 100.0 if i % 2 else 400.0
        yield i * 0.1, [TrackedDetection(i % 2, "person", (10.0, 10.0, 60.0, 10.0 + altura))]


def test_escala_uniforme_em_cena_com_perspectiva_vira_aviso_na_tela():
    """Na página de revisão o aviso sai no terminal, depois do fim. Ao vivo não
    há fim: o aviso tem que aparecer para quem está olhando a tela."""
    cena = list(_pessoas_em_perspectiva())
    painel = Painel()

    processa(
        _quadros_de(cena),
        _detector_de(cena),
        LiveSession(PLANO, Config()),
        painel,
        escala_uniforme=True,
    )

    assert any("--calibration" in aviso for aviso in painel.estado()["avisos"])


def test_calibracao_medida_nao_recebe_aviso_de_escala():
    """A homografia trata a perspectiva; é para isso que ela existe."""
    cena = list(_pessoas_em_perspectiva())
    painel = Painel()

    processa(
        _quadros_de(cena),
        _detector_de(cena),
        LiveSession(PLANO, Config()),
        painel,
        escala_uniforme=False,
    )

    assert painel.estado()["avisos"] == []


class _CapturaInstantanea:
    """Câmera falsa que entrega quadros mais rápido do que qualquer detector
    consegue consumir. O número do quadro vai gravado no primeiro pixel."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.lidos = 0
        self.liberada = False

    def read(self):
        if self.lidos >= self.total:
            return False, None
        imagem = np.full((4, 4, 3), 0, np.uint8)
        imagem[0, 0, 0] = self.lidos % 256
        imagem[0, 0, 1] = self.lidos // 256
        self.lidos += 1
        return True, imagem

    def release(self) -> None:
        self.liberada = True


def _numero(imagem) -> int:
    return int(imagem[0, 0, 0]) + 256 * int(imagem[0, 0, 1])


def test_camera_lenta_de_consumir_perde_quadro_velho_e_nao_acumula_atraso():
    """Processar todo quadro de uma câmera mais rápida que o detector é ficar
    cada vez mais atrasado: em uma hora, o operador estaria vendo o passado.
    O quadro que importa é sempre o mais novo."""
    captura = _CapturaInstantanea(400)
    vistos = []

    for _, imagem in CameraAoVivo(captura):
        vistos.append(_numero(imagem))
        time.sleep(0.005)

    assert len(vistos) < 400
    assert vistos == sorted(set(vistos))
    assert vistos[-1] == 399
    assert captura.liberada


def test_camera_ao_vivo_marca_o_tempo_pelo_relogio():
    """Quadro perdido é tempo que passou. Contar quadros em vez de relógio
    encolheria cada limiar em segundos na proporção dos quadros perdidos."""
    relogio = iter(float(i) for i in range(100, 1000))
    tempos = [t for t, _ in CameraAoVivo(_CapturaInstantanea(5), relogio=lambda: next(relogio))]

    assert tempos[0] >= 0.0
    assert tempos == sorted(tempos)


def test_camera_ao_vivo_para_quando_pedem():
    parar = threading.Event()
    camera = CameraAoVivo(_CapturaInstantanea(10**9), parar=parar)

    for i, _ in enumerate(camera):
        if i == 3:
            parar.set()

    assert camera.encerrada.wait(2.0)


def test_arquivo_com_passo_mantem_o_tempo_do_video(tmp_path):
    """Pular quadro para caber no processador não pode encolher o tempo: o
    instante continua sendo o do vídeo."""
    caminho = tmp_path / "curto.avi"
    escritor = cv2.VideoWriter(str(caminho), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (32, 24))
    for _ in range(10):
        escritor.write(np.zeros((24, 32, 3), np.uint8))
    escritor.release()

    tempos = [t for t, _ in quadros_de_arquivo(caminho, passo=3)]

    assert tempos == pytest.approx([0.0, 0.3, 0.6, 0.9])


@pytest.mark.parametrize(
    ("texto", "alvo", "ao_vivo"),
    [
        ("0", 0, True),
        ("2", 2, True),
        ("rtsp://cam.local/stream", "rtsp://cam.local/stream", True),
        ("http://cam.local/mjpg", "http://cam.local/mjpg", True),
        ("gravacao.mp4", "gravacao.mp4", False),
    ],
)
def test_fonte_de_distingue_camera_de_arquivo(texto, alvo, ao_vivo):
    assert fonte_de(texto) == (alvo, ao_vivo)
