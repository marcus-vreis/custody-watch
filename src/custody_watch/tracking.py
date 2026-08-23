"""Ponte entre o tracker e a camada de lógica.

A partir daqui nada mais fala em pixels.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
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


def track_video(
    video_path: Path,
    weights: str = "yolo26s.pt",
    tracker: str = "bytetrack.yaml",
    fps: float | None = None,
) -> Iterator[tuple[float, list[TrackedDetection]]]:
    """Roda detector + tracker sobre um vídeo, produzindo (tempo, tracks) por frame.

    Gerador para não carregar o vídeo inteiro em memória.
    """
    from ultralytics import YOLO

    from .detectors.base import RELEVANT_CLASSES

    frame_rate = fps if fps is not None else video_fps(video_path)

    model = YOLO(weights)
    results = model.track(
        source=str(video_path), tracker=tracker, stream=True, persist=True, verbose=False
    )

    for frame_index, result in enumerate(results):
        tracked: list[TrackedDetection] = []
        if result.boxes is not None and result.boxes.id is not None:
            names = result.names
            for box, track_id in zip(result.boxes, result.boxes.id.tolist(), strict=True):
                cls_name = names[int(box.cls)]
                if cls_name not in RELEVANT_CLASSES:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                tracked.append(
                    TrackedDetection(track_id=int(track_id), cls=cls_name, bbox=(x1, y1, x2, y2))
                )
        yield frame_index / frame_rate, tracked


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
    "MAX_OBSERVATION_SPEED_MS",
    "PlausibilityGate",
    "TrackedDetection",
    "to_observations",
    "track_video",
    "video_fps",
]
