"""Projeção de pixels para metros no plano do chão.

Todos os limiares do sistema são em metros. Sem esta conversão o limiar de
"3 metros" da máquina de custódia é ficção, porque a mesma distância em
pixels significa coisas diferentes conforme a profundidade.
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from .types import Point


class GroundPlane:
    def __init__(self, homography: np.ndarray) -> None:
        if homography.shape != (3, 3):
            raise ValueError(f"homografia deve ser 3x3, recebida {homography.shape}")
        self._h = np.asarray(homography, dtype=np.float64)

    @classmethod
    def from_correspondences(
        cls,
        pixel_points: Sequence[tuple[float, float]],
        world_points: Sequence[tuple[float, float]],
    ) -> GroundPlane:
        """Estima a homografia a partir de correspondências pixel → metro.

        O PETS2007 distribui calibração de câmera; para vídeo próprio, medir
        quatro pontos de dimensão conhecida no chão.
        """
        if len(pixel_points) < 4 or len(world_points) < 4:
            raise ValueError("homografia exige ao menos 4 correspondências")
        if len(pixel_points) != len(world_points):
            raise ValueError("pixel_points e world_points devem ter o mesmo tamanho")

        h, _ = cv2.findHomography(
            np.array(pixel_points, dtype=np.float64),
            np.array(world_points, dtype=np.float64),
        )
        if h is None:
            raise ValueError("cv2.findHomography falhou — pontos podem ser colineares")
        return cls(h)

    def project(self, px: float, py: float) -> Point:
        vector = self._h @ np.array([px, py, 1.0])
        if abs(vector[2]) < 1e-9:
            raise ValueError(f"ponto ({px}, {py}) projeta no infinito")
        return Point(float(vector[0] / vector[2]), float(vector[1] / vector[2]))

    def foot_point(self, bbox: tuple[float, float, float, float]) -> Point:
        """Base da bbox — onde o objeto toca o chão."""
        x1, _y1, x2, y2 = bbox
        return self.project((x1 + x2) / 2.0, y2)
