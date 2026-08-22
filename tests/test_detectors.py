import numpy as np

from custody_watch.detectors.base import Detector, filter_relevant
from custody_watch.types import Detection


class FakeDetector:
    """Prova que o Protocol é satisfazível sem carregar peso de modelo."""

    def __init__(self, canned: list[Detection]) -> None:
        self._canned = canned

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return self._canned


def test_fake_detector_satisfaz_o_protocol():
    detector: Detector = FakeDetector([])
    assert detector.detect(np.zeros((4, 4, 3), dtype=np.uint8)) == []


def test_filter_relevant_mantem_pessoas_e_bagagens():
    detections = [
        Detection("person", (0, 0, 1, 1), 0.9),
        Detection("suitcase", (0, 0, 1, 1), 0.8),
        Detection("backpack", (0, 0, 1, 1), 0.7),
        Detection("handbag", (0, 0, 1, 1), 0.7),
    ]
    assert len(filter_relevant(detections, min_confidence=0.5)) == 4


def test_filter_relevant_descarta_classes_irrelevantes():
    detections = [
        Detection("person", (0, 0, 1, 1), 0.9),
        Detection("dog", (0, 0, 1, 1), 0.9),
        Detection("chair", (0, 0, 1, 1), 0.9),
    ]
    kept = filter_relevant(detections, min_confidence=0.5)
    assert [d.cls for d in kept] == ["person"]


def test_filter_relevant_aplica_limiar_de_confianca():
    detections = [
        Detection("person", (0, 0, 1, 1), 0.9),
        Detection("suitcase", (0, 0, 1, 1), 0.2),
    ]
    kept = filter_relevant(detections, min_confidence=0.5)
    assert [d.cls for d in kept] == ["person"]
