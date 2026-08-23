"""Interface do detector.

Trocável de propósito. O YOLO26 é AGPL-3.0 e faz atribuição um-pra-um
(NMS-free), o que pode alterar a distribuição de confiança de que o
ByteTrack depende no segundo passe de associação. Se isso se confirmar na
medição, trocar por RF-DETR ou D-FINE (Apache-2.0) deve custar um adapter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from ..types import BAG_CLASSES, PERSON_CLASS, Detection

RELEVANT_CLASSES = BAG_CLASSES | {PERSON_CLASS}


@runtime_checkable
class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detecta pessoas e bagagens num frame BGR."""
        ...


def filter_relevant(detections: list[Detection], min_confidence: float = 0.35) -> list[Detection]:
    """Descarta classes fora do escopo e detecções de baixa confiança."""
    return [d for d in detections if d.cls in RELEVANT_CLASSES and d.confidence >= min_confidence]
