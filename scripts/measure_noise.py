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
from pathlib import Path

from custody_watch.caviar import SCENARIOS, _parse_frames
from custody_watch.video import VideoSource

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
IOU_MINIMO = 0.5


def iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    uniao = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / uniao if uniao > 0 else 0.0


def pe(caixa: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, _, x2, y2 = caixa
    return ((x1 + x2) / 2.0, y2)


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        return 1

    erros_x: list[float] = []
    erros_y: list[float] = []
    rajadas: list[int] = []
    trocas = 0
    total_anotado = 0
    total_pareado = 0

    for scenario, xml in SCENARIOS.items():
        anotado = dict(_parse_frames(DATA / scenario / xml))
        atribuido: dict[int, int] = {}
        ausente: dict[int, int] = defaultdict(int)

        for indice, quadro in enumerate(VideoSource(DATA / scenario / f"{scenario}.mpg")):
            verdade = anotado.get(indice)
            if verdade is None:
                continue

            pessoas = [d for d in quadro.tracked if d.cls == "person"]

            for caixa in verdade:
                if caixa.is_bag:
                    continue
                total_anotado += 1

                melhor, melhor_iou = None, IOU_MINIMO
                for det in pessoas:
                    valor = iou(det.bbox, caixa.bbox)
                    if valor >= melhor_iou:
                        melhor, melhor_iou = det, valor

                if melhor is None:
                    ausente[caixa.track_id] += 1
                    continue

                if ausente[caixa.track_id]:
                    rajadas.append(ausente[caixa.track_id])
                    ausente[caixa.track_id] = 0

                total_pareado += 1
                ax, ay = pe(caixa.bbox)
                dx, dy = pe(melhor.bbox)
                erros_x.append(dx - ax)
                erros_y.append(dy - ay)

                anterior = atribuido.get(caixa.track_id)
                if anterior is not None and anterior != melhor.track_id:
                    trocas += 1
                atribuido[caixa.track_id] = melhor.track_id

        rajadas.extend(v for v in ausente.values() if v)

    print(f"pessoas anotadas   : {total_anotado}")
    print(f"pareadas (IoU>=0.5): {total_pareado}  ({total_pareado / total_anotado:.1%})")
    print(f"taxa de falha      : {1 - total_pareado / total_anotado:.1%}\n")

    print("erro do pe da caixa, em pixel:")
    for nome, valores in (("dx", erros_x), ("dy", erros_y)):
        print(
            f"  {nome}: media {st.mean(valores):+.2f}  desvio {st.pstdev(valores):.2f}  "
            f"p95 |{sorted(abs(v) for v in valores)[int(0.95 * len(valores))]:.2f}|"
        )

    print(f"\nrajadas de falha   : {len(rajadas)} rajadas")
    if rajadas:
        ordenado = sorted(rajadas)
        print(
            f"  mediana {st.median(ordenado):.0f} quadros, "
            f"p95 {ordenado[int(0.95 * len(ordenado))]} quadros, "
            f"maxima {max(ordenado)} quadros"
        )
    print(f"trocas de id       : {trocas} em {total_pareado} pareamentos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
