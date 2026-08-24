"""Fonte de vídeo com detecção e tracking reais.

Até aqui todo o pipeline foi alimentado com as caixas do ground truth do
CAVIAR. `YoloDetector` existe e é testado contra um fake, mas nunca foi
instanciado — não há sequer um arquivo de peso em disco. Metade da percepção
do sistema é, portanto, inteiramente não verificada.

Este módulo é o caminho que permite verificá-la, e é também o primeiro tijolo
da ingestão da plataforma: trocar arquivo por RTSP depois é trocar de onde
vêm os quadros, não reescrever o pipeline.

`VideoFrame` carrega instante, imagem e detecções juntos porque o orquestrador
precisa dos dois primeiros e o recorte de clipe precisa da imagem. Decodificar
duas vezes seria desperdício e abriria espaço para as duas passagens
divergirem.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .detectors.base import RELEVANT_CLASSES
from .reid import describe
from .tracking import TrackedDetection, video_fps
from .types import PERSON_CLASS

DEFAULT_WEIGHTS = "yolo26s.pt"
DEFAULT_TRACKER = "bytetrack.yaml"
DEFAULT_MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class VideoFrame:
    t: float
    image: np.ndarray
    tracked: list[TrackedDetection] = field(default_factory=list)


def parse_result(
    result: Any, min_confidence: float, with_appearance: bool = False
) -> list[TrackedDetection]:
    """Converte um resultado do ultralytics em detecções rastreadas.

    Função pura de propósito: é o que permite testar a conversão sem carregar
    peso de modelo nenhum.

    Detecção sem `track_id` é descartada. O tracker ainda não a associou a
    nada, e sem identidade não há custódia a acompanhar.
    """
    boxes = getattr(result, "boxes", None)
    if boxes is None or boxes.id is None:
        return []

    nomes = result.names
    imagem = getattr(result, "orig_img", None)
    tracked: list[TrackedDetection] = []

    for box, track_id in zip(boxes, boxes.id.tolist(), strict=True):
        classe = nomes[int(box.cls[0])]
        if classe not in RELEVANT_CLASSES:
            continue
        if float(box.conf[0]) < min_confidence:
            continue

        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])

        aparencia = None
        if with_appearance and classe == PERSON_CLASS and imagem is not None:
            recorte = imagem[max(int(y1), 0) : max(int(y2), 0), max(int(x1), 0) : max(int(x2), 0)]
            if recorte.size:
                aparencia = describe(recorte)

        tracked.append(
            TrackedDetection(
                track_id=int(track_id),
                cls=classe,
                bbox=(x1, y1, x2, y2),
                appearance=aparencia,
            )
        )

    return tracked


class VideoSource:
    """Arquivo de vídeo em quadros com detecção e tracking.

    O instante vem de `video_fps`, que lê o arquivo e falha alto em vez de
    assumir 25 fps. Assumir distorceria o limiar de 25 segundos da máquina de
    custódia em 17% num vídeo de 30 fps.
    """

    def __init__(
        self,
        path: Path | str,
        weights: str = DEFAULT_WEIGHTS,
        tracker: str = DEFAULT_TRACKER,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        with_appearance: bool = False,
        fps: float | None = None,
    ) -> None:
        self.path = Path(path)
        self.weights = weights
        self.tracker = tracker
        self.min_confidence = min_confidence
        self.with_appearance = with_appearance
        self.fps = fps if fps is not None else video_fps(self.path)

    def __iter__(self) -> Iterator[VideoFrame]:
        from ultralytics import YOLO  # import tardio: pesado

        modelo = YOLO(self.weights)
        stream = modelo.track(
            source=str(self.path),
            tracker=self.tracker,
            stream=True,
            persist=True,
            verbose=False,
        )

        for indice, resultado in enumerate(stream):
            yield VideoFrame(
                t=indice / self.fps,
                image=resultado.orig_img,
                tracked=parse_result(resultado, self.min_confidence, self.with_appearance),
            )

    def observations(self) -> Iterator[tuple[float, list[TrackedDetection]]]:
        """Formato que `run_session` consome."""
        for quadro in self:
            yield quadro.t, quadro.tracked
