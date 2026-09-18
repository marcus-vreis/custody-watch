"""Ponte entre o tracker e a camada de lógica.

A partir daqui nada mais fala em pixels.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import cv2

from .ground_plane import GroundPlane
from .reid import Appearance
from .types import Observation


@dataclass(frozen=True)
class TrackedDetection:
    """Detecção com ID de track atribuído, ainda em pixels.

    `appearance` é opcional e só existe quando a fonte de frames consegue
    produzi-la. É o último ponto do pipeline que sabe o que é um pixel: daqui
    para frente a assinatura viaja como vetor opaco.
    """

    track_id: int
    cls: str
    bbox: tuple[float, float, float, float]
    appearance: Appearance | None = None
    touches_bottom: bool = False
    """A caixa encosta na borda de baixo do quadro.

    O pé da caixa é o ponto que o plano do chão projeta. Cortada pela borda,
    o pé vira a borda da imagem, e a projeção passa a dar sempre a mesma
    linha — a bagagem parece parada enquanto é puxada. Só quem produziu a
    detecção sabe o tamanho do quadro, então é lá que a marca nasce.
    """


CONTIDA_NA_PESSOA = 0.8
"""Fração da caixa da bagagem dentro da caixa de uma pessoa a partir da qual
ela está na silhueta de alguém. Bolsa em cima do banco ao lado de quem senta
fica perto de um terço — é o caso que o veto não pode pegar, porque era assim
que estavam as bolsas furtadas no MEVA."""

ACIMA_DOS_PES = 0.1
"""Quanto, em fração da altura da pessoa, a base da bagagem precisa estar
acima dos pés dela para não estar no chão. Mala apoiada ao lado dos pés tem a
base na mesma linha; mochila nas costas fica a mais de um terço da altura."""


def _contida(dentro: tuple[float, ...], fora: tuple[float, ...]) -> float:
    ix = max(0.0, min(dentro[2], fora[2]) - max(dentro[0], fora[0]))
    iy = max(0.0, min(dentro[3], fora[3]) - max(dentro[1], fora[1]))
    area = (dentro[2] - dentro[0]) * (dentro[3] - dentro[1])
    return ix * iy / area if area > 0 else 0.0


def _carregada(bolsa: tuple[float, ...], pessoas: list[tuple[float, ...]]) -> bool:
    for p in pessoas:
        altura = p[3] - p[1]
        if _contida(bolsa, p) >= CONTIDA_NA_PESSOA and p[3] - bolsa[3] > ACIMA_DOS_PES * altura:
            return True
    return False


def anchor_vetoes(tracked: Iterable[TrackedDetection], min_bag_height_px: float) -> dict[int, str]:
    """Bagagens que a imagem diz que não podem virar âncora, e por quê.

    Três perguntas que só têm resposta em pixel — nenhuma delas é distância,
    e distância continua em metros. Todas saíram de vídeo real:

    - **cortada**: encosta na borda de baixo, e o ponto no chão é inobservável
    - **pequena**: abaixo da altura em que o detector é confiável; `0` desliga,
      o que só é honesto para caixa anotada
    - **carregada**: dentro da silhueta de uma pessoa e acima dos pés dela.
      Parar não é depositar: a mochila de quem espera parado passa no teste
      de repouso junto com a pessoa

    Vale só para âncora NOVA. Bagagem já registrada segue sendo observada, ou
    o ladrão que a carrega a faria sumir em vez de gerar retirada.
    """
    itens = list(tracked)
    pessoas = [d.bbox for d in itens if d.cls == "person"]
    vetos: dict[int, str] = {}
    for d in itens:
        if d.cls == "person":
            continue
        if d.touches_bottom:
            vetos[d.track_id] = "cortada"
        elif d.bbox[3] - d.bbox[1] < min_bag_height_px:
            vetos[d.track_id] = "pequena"
        elif _carregada(d.bbox, pessoas):
            vetos[d.track_id] = "carregada"
    return vetos


def iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Interseção sobre união de duas caixas, em pixel.

    Vive aqui porque é a primitiva que pareia detecção com anotação, e dois
    scripts de medição precisavam dela — copiada, ela sairia de sincronia com
    a definição que os números publicados usaram.
    """
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    inter = (ix2 - ix1) * (iy2 - iy1)
    uniao = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / uniao if uniao > 0 else 0.0


def to_observations(
    tracked: Iterable[TrackedDetection], plane: GroundPlane, t: float
) -> list[Observation]:
    """Projeta tracks para o plano do chão.

    Detecções que projetam no infinito (linha do horizonte) são descartadas
    silenciosamente — é ruído geométrico esperado, não erro de programação.
    """
    observations: list[Observation] = []
    for item in tracked:
        try:
            position = plane.foot_point(item.bbox)
        except ValueError:
            continue
        observations.append(
            Observation(track_id=item.track_id, cls=item.cls, position=position, t=t)
        )
    return observations


def video_fps(video_path: Path) -> float:
    """Lê a taxa de quadros do arquivo.

    Falha alto em vez de assumir um valor. O fps converte índice de frame em
    segundos, e os segundos alimentam o limiar de 25s da máquina de custódia:
    assumir 25 fps num vídeo de 30 encolheria todos os intervalos em 17%.
    """
    capture = cv2.VideoCapture(str(video_path))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
    finally:
        capture.release()

    if fps <= 0.0 or not math.isfinite(fps):
        raise ValueError(f"não foi possível ler o fps de {video_path}; passe fps= explicitamente")
    return fps


MAX_OBSERVATION_SPEED_MS = 25.0


class PlausibilityGate:
    """Descarta observações que implicam velocidade impossível para uma pessoa.

    Existe por um cenário que a revisão adversarial construiu: um artefato de
    projeção **correlacionado** entre dois tracks — bounding boxes truncando
    juntas quando alguém passa na frente das duas — faz os dois pontos de apoio
    saltarem na mesma direção. A caixa da trajetória cresce, a separação não
    muda, e `_extent` conclui que as duas pessoas cobriram terreno juntas sem
    ninguém ter saído do lugar.

    O limiar vem de medição, não de palpite. Nas quatro sequências do CAVIAR, a
    velocidade quadro a quadro tem mediana de 1,34 m/s, p99 de 7,5 m/s e máximo
    de 20,1 m/s — e esse máximo já é ruído de anotação, não movimento. O salto
    do artefato descrito na revisão implicava 150 m/s. Em 25 m/s o portão pega o
    artefato e não encosta em nada real: nas mesmas sequências, corta zero
    observações.

    Guarda estado por track e é descartado ao fim da sessão.
    """

    def __init__(self, max_speed_ms: float = MAX_OBSERVATION_SPEED_MS) -> None:
        self._max_speed_ms = max_speed_ms
        self._last: dict[int, Observation] = {}
        self.rejected = 0

    def accept(self, observation: Observation) -> bool:
        anterior = self._last.get(observation.track_id)
        if anterior is not None and observation.t > anterior.t:
            velocidade = anterior.position.distance_to(observation.position) / (
                observation.t - anterior.t
            )
            if velocidade > self._max_speed_ms:
                # A observação anterior segue sendo a referência: aceitar a
                # implausível como novo ponto de partida deixaria o salto
                # passar em duas etapas.
                self.rejected += 1
                return False

        self._last[observation.track_id] = observation
        return True

    def filter(self, observations: Iterable[Observation]) -> list[Observation]:
        return [o for o in observations if self.accept(o)]


__all__ = [
    "iou",
    "MAX_OBSERVATION_SPEED_MS",
    "PlausibilityGate",
    "TrackedDetection",
    "to_observations",
    "video_fps",
]
