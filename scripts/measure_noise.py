#!/usr/bin/env python3
"""Mede o ruído do detector contra o ground truth do CAVIAR.

O simulador de envelope precisa saber o que a percepção erra de verdade. Para
**pessoa** existe amostra: 1677 detecções pareadas. Para **bagagem** não —
uma em 1686 — e é por isso que a bagagem vira a variável varrida em vez de
parâmetro estimado.

Três eixos, que são os que a lógica de custódia sente:

- falha de detecção, e o comprimento das rajadas de falha
- erro de posição do pé da caixa, em pixel
- troca de id, quando o tracker reatribui

    uv run python scripts/measure_noise.py
"""

from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from custody_watch.caviar import SCENARIOS, _parse_frames
from custody_watch.tracking import iou
from custody_watch.video import VideoSource

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
IOU_MINIMO = 0.5


@dataclass
class _Amostra:
    """O que a medição acumula ao longo dos quatro clipes."""

    anotadas: int = 0
    pareadas: int = 0
    erros_x: list[float] = field(default_factory=list)
    erros_y: list[float] = field(default_factory=list)
    rajadas: list[int] = field(default_factory=list)
    trocas: int = 0


def _pe(caixa: tuple[float, float, float, float]) -> tuple[float, float]:
    """Base da caixa — é ela que o `ground_plane` projeta no chão."""
    x1, _, x2, y2 = caixa
    return ((x1 + x2) / 2.0, y2)


def _melhor_par(caixa, pessoas):
    """A detecção de maior IoU acima do mínimo, ou `None`."""
    melhor, melhor_iou = None, IOU_MINIMO

    for deteccao in pessoas:
        valor = iou(deteccao.bbox, caixa.bbox)
        if valor >= melhor_iou:
            melhor, melhor_iou = deteccao, valor

    return melhor


def _mede_clipe(scenario: str, xml: str, amostra: _Amostra) -> None:
    """Percorre um clipe, pareando cada pessoa anotada com uma detecção."""
    anotado = dict(_parse_frames(DATA / scenario / xml))
    atribuido: dict[int, int] = {}
    ausente: dict[int, int] = defaultdict(int)

    for indice, quadro in enumerate(VideoSource(DATA / scenario / f"{scenario}.mpg")):
        verdade = anotado.get(indice)
        if verdade is None:
            continue

        pessoas = [d for d in quadro.tracked if d.cls == "person"]

        for caixa in (c for c in verdade if not c.is_bag):
            amostra.anotadas += 1
            melhor = _melhor_par(caixa, pessoas)

            if melhor is None:
                ausente[caixa.track_id] += 1
                continue

            if ausente[caixa.track_id]:
                amostra.rajadas.append(ausente[caixa.track_id])
                ausente[caixa.track_id] = 0

            amostra.pareadas += 1
            ax, ay = _pe(caixa.bbox)
            dx, dy = _pe(melhor.bbox)
            amostra.erros_x.append(dx - ax)
            amostra.erros_y.append(dy - ay)

            anterior = atribuido.get(caixa.track_id)
            if anterior is not None and anterior != melhor.track_id:
                amostra.trocas += 1
            atribuido[caixa.track_id] = melhor.track_id

    amostra.rajadas.extend(v for v in ausente.values() if v)


def _relata(amostra: _Amostra) -> None:
    fracao = amostra.pareadas / amostra.anotadas

    print(f"pessoas anotadas   : {amostra.anotadas}")
    print(f"pareadas (IoU>={IOU_MINIMO}): {amostra.pareadas}  ({fracao:.1%})")
    print(f"taxa de falha      : {1 - fracao:.1%}")
    print()

    print("erro do pe da caixa, em pixel:")
    for nome, valores in (("dx", amostra.erros_x), ("dy", amostra.erros_y)):
        p95 = sorted(abs(v) for v in valores)[int(0.95 * len(valores))]
        print(
            f"  {nome}: media {st.mean(valores):+.2f}  "
            f"desvio {st.pstdev(valores):.2f}  p95 |{p95:.2f}|"
        )

    print()
    print(f"rajadas de falha   : {len(amostra.rajadas)} rajadas")
    if amostra.rajadas:
        ordenado = sorted(amostra.rajadas)
        print(
            f"  mediana {st.median(ordenado):.0f} quadros, "
            f"p95 {ordenado[int(0.95 * len(ordenado))]} quadros, "
            f"maxima {max(ordenado)} quadros"
        )
    print(f"trocas de id       : {amostra.trocas} em {amostra.pareadas} pareamentos")


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        return 1

    amostra = _Amostra()
    for scenario, xml in SCENARIOS.items():
        _mede_clipe(scenario, xml, amostra)

    _relata(amostra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
