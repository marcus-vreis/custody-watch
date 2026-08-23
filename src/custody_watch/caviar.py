"""Leitura do dataset CAVIAR para dentro do pipeline.

O CAVIAR substitui o PETS2007, que morreu (ver `scripts/download_caviar.py`).
Ele anota a bagagem como objeto próprio, com `role=leaving object`, o que
permite exercitar a camada de lógica sem detector — as caixas do ground truth
entram direto em `to_observations`.

## A calibração que não existe

O PETS2007 distribuía calibração de câmera; o CAVIAR não. A técnica padrão
para suprir isso é ajustar a altura das pessoas em pixels contra a linha da
imagem: num plano de chão visto em perspectiva, `h_px = c * (y_pé - y_horizonte)`.

**Isso não funciona nesta câmera.** Ajustado sobre 5416 caixas de pessoa dos
quatro clipes utilizáveis, o modelo explica 0% da variância: inclinação de
-0,0018 e horizonte em y=17848, numa imagem de 288 linhas. A causa está na
geometria — a câmera do saguão do INRIA é grande-angular apontada de cima, e
nessa configuração todo mundo fica a distância parecida da lente. A altura
média é 31,7px com desvio de 10,5px, e esse desvio é postura, não profundidade.

Então usamos **escala global isotrópica**, derivada da altura média de pessoa
contra `PERSON_HEIGHT_M`. As distâncias resultantes são estimativas com erro
da ordem de 30%, e a perspectiva residual é ignorada. Para medir se o sistema
grita furto onde não há furto, isso basta; para um `P_miss` publicável, não.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ground_plane import GroundPlane
from .tracking import TrackedDetection

CAVIAR_FPS = 25.0
PERSON_HEIGHT_M = 1.7
MIN_BOX_HEIGHT_PX = 8
BAG_ROLE = "leaving object"

# Cenário -> arquivo de ground truth. `LeftBag_BehindChair` fica de fora:
# a bagagem não é anotada como objeto nele, então não há o que rastrear.
SCENARIOS = {
    "LeftBag": "lb1gt.xml",
    "LeftBag_AtChair": "lb2gt.xml",
    "LeftBag_PickedUp": "lbpugt.xml",
    "LeftBox": "lbgt.xml",
}


@dataclass(frozen=True)
class AnnotatedBox:
    track_id: int
    is_bag: bool
    xc: float
    yc: float
    w: float
    h: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            self.xc - self.w / 2,
            self.yc - self.h / 2,
            self.xc + self.w / 2,
            self.yc + self.h / 2,
        )


def _parse_frames(xml_path: Path) -> list[tuple[int, list[AnnotatedBox]]]:
    root = ET.parse(xml_path).getroot()
    frames: list[tuple[int, list[AnnotatedBox]]] = []

    for frame in root.findall(".//frame"):
        boxes: list[AnnotatedBox] = []
        for obj in frame.findall(".//object"):
            box = obj.find(".//box")
            if box is None:
                continue
            role = obj.find(".//role")
            boxes.append(
                AnnotatedBox(
                    track_id=int(obj.get("id")),
                    is_bag=role is not None and role.text == BAG_ROLE,
                    xc=float(box.get("xc")),
                    yc=float(box.get("yc")),
                    w=float(box.get("w")),
                    h=float(box.get("h")),
                )
            )
        frames.append((int(frame.get("number")), boxes))

    return frames


def estimate_metres_per_pixel(data_root: Path) -> float:
    """Escala global a partir da altura média de pessoa.

    Grosseira de propósito: o ajuste em perspectiva não converge nesta câmera
    (ver docstring do módulo). Uma escala única erra a profundidade, mas erra
    de forma previsível e documentada, o que é melhor que um horizonte
    inventado.
    """
    alturas: list[float] = []
    for scenario, xml_name in SCENARIOS.items():
        xml_path = data_root / scenario / xml_name
        # Download parcial não deve derrubar a estimativa: a escala é a média
        # sobre milhares de caixas, e três clipes chegam ao mesmo número.
        if not xml_path.exists():
            continue
        for _, boxes in _parse_frames(xml_path):
            alturas.extend(b.h for b in boxes if not b.is_bag and b.h >= MIN_BOX_HEIGHT_PX)

    if not alturas:
        raise ValueError(f"nenhuma caixa de pessoa encontrada em {data_root}")

    return PERSON_HEIGHT_M / (sum(alturas) / len(alturas))


def ground_plane(metres_per_pixel: float) -> GroundPlane:
    """Homografia de escala pura. Sem perspectiva, sem correção de distorção."""
    return GroundPlane(np.diag([metres_per_pixel, metres_per_pixel, 1.0]))


def load_clip(
    data_root: Path, scenario: str, fps: float = CAVIAR_FPS
) -> Iterator[tuple[float, list[TrackedDetection]]]:
    """Frames anotados como o tracker os entregaria.

    Os ids do CAVIAR são reaproveitados entre pessoas e bagagens no mesmo
    clipe, então a bagagem recebe um espaço de ids deslocado para não colidir
    com o de pessoas.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"cenário desconhecido {scenario!r}; conhecidos {sorted(SCENARIOS)}")

    for number, boxes in _parse_frames(data_root / scenario / SCENARIOS[scenario]):
        tracked = [
            TrackedDetection(
                track_id=b.track_id + (BAG_ID_OFFSET if b.is_bag else 0),
                cls="suitcase" if b.is_bag else "person",
                bbox=b.bbox,
            )
            for b in boxes
        ]
        yield number / fps, tracked


BAG_ID_OFFSET = 1000
