"""Calibração de câmera em arquivo, com resíduo de reprojeção.

O PETS2007 distribuía calibração e o CAVIAR não — foi por isso que caímos numa
escala global com cerca de 30% de erro. Material gravado por nós não precisa
disso: quatro pontos medidos com fita métrica no chão resolvem a homografia.

**O resíduo é obrigatório.** Medição feita às pressas produz homografia errada
em silêncio, e aí todo limiar em metros passa a mentir junto, sem sintoma.
Acima de `MAX_RESIDUAL_M` o carregador recusa.

O limite de 0,25m vem de comparação com o que já existe: a escala global do
CAVIAR carrega ~30% de erro, o que num cenário de oito metros passa de dois
metros. Uma calibração pior que 0,25m não compra nada sobre isso, então
recusar é mais honesto que aceitar e fingir precisão.

A recusa olha o **pior** ponto, não a média. Média dilui: com doze pontos, um
canto medido 2m fora dá média de 17cm e passa no limite — que é exatamente o
erro silencioso que este módulo existe para pegar. "Nenhum ponto medido erra
mais de 25cm" é um contrato mais forte, e não precisa de uma segunda constante
arbitrária para valer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .ground_plane import GroundPlane
from .types import Point

MAX_RESIDUAL_M = 0.25


@dataclass(frozen=True)
class Calibration:
    camera: str
    note: str
    plane: GroundPlane
    residual_m: float
    """Erro médio. Serve para relatar a qualidade da medição."""
    worst_residual_m: float
    """Erro do pior ponto. É por ele que a calibração é aceita ou recusada."""


def reprojection_errors(
    plane: GroundPlane,
    pixels: Sequence[tuple[float, float]],
    world: Sequence[Point],
) -> list[float]:
    """Erro, em metros, de cada ponto medido contra o reprojetado."""
    return [
        plane.project(px, py).distance_to(alvo)
        for (px, py), alvo in zip(pixels, world, strict=True)
    ]


def reprojection_residual(
    plane: GroundPlane,
    pixels: Sequence[tuple[float, float]],
    world: Sequence[Point],
) -> float:
    """Erro médio, em metros, entre o ponto medido e o reprojetado."""
    erros = reprojection_errors(plane, pixels, world)
    return sum(erros) / len(erros) if erros else 0.0


def load_calibration(path: Path | str) -> Calibration:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    camera = str(data.get("camera", "")).strip()
    if not camera:
        raise ValueError(f"{path}: campo 'camera' obrigatório e não vazio")

    note = str(data.get("note", "")).strip()
    if not note:
        raise ValueError(
            f"{path}: campo 'note' obrigatório — registre como o chão foi medido, "
            f"porque daqui a seis meses ninguém lembra"
        )

    correspondencias = data.get("correspondences", [])
    if len(correspondencias) < 4:
        raise ValueError(f"{path}: homografia exige ao menos 4 correspondências")

    pixels = [(float(c["pixel"][0]), float(c["pixel"][1])) for c in correspondencias]
    mundo = [Point(float(c["world"][0]), float(c["world"][1])) for c in correspondencias]

    plane = GroundPlane.from_correspondences(pixels, [(p.x, p.y) for p in mundo])
    erros = reprojection_errors(plane, pixels, mundo)
    media = sum(erros) / len(erros)
    pior = max(erros)

    if pior > MAX_RESIDUAL_M:
        indice = erros.index(pior)
        raise ValueError(
            f"{path}: resíduo de reprojeção de {pior:.2f}m no ponto {indice} "
            f"(pixel {pixels[indice]}) excede o limite de {MAX_RESIDUAL_M}m; "
            f"média de {media:.2f}m. A medição do chão provavelmente está errada, "
            f"e aceitar faria todo limiar em metros mentir em silêncio."
        )

    return Calibration(
        camera=camera, note=note, plane=plane, residual_m=media, worst_residual_m=pior
    )
