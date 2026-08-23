"""Adapter YOLO26 (Ultralytics)."""

from __future__ import annotations

import numpy as np

from ..types import Detection
from .base import filter_relevant


class YoloDetector:
    def __init__(self, weights: str = "yolo26s.pt", min_confidence: float = 0.35) -> None:
        from ultralytics import YOLO  # import tardio: pesado

        self._model = YOLO(weights)
        self._min_confidence = min_confidence

    def detect(self, frame: np.ndarray) -> list[Detection]:
        results = self._model(frame, verbose=False)
        detections: list[Detection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                detections.append(
                    Detection(
                        cls=names[int(box.cls)],
                        bbox=(x1, y1, x2, y2),
                        confidence=float(box.conf),
                    )
                )
        return filter_relevant(detections, self._min_confidence)
