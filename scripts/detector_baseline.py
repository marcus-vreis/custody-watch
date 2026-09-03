#!/usr/bin/env python3
"""Mede o detector contra o ground truth do CAVIAR.

Primeira medição da metade de percepção do sistema. Até aqui todo o pipeline
foi alimentado com as caixas anotadas: `YoloDetector` existia, era testado
contra um fake, e nunca tinha sido instanciado.

O resultado esperado é ruim. A bagagem no CAVIAR tem 18x14 pixels, e o limiar
de "objeto pequeno" do COCO é 32x32 — a mala está abaixo dele. **Ruim é uma
resposta útil:** transforma "o detector provavelmente não acha" num número, e
é esse número que justifica exigir resolução moderna na gravação encenada.

    uv run python scripts/detector_baseline.py
"""

from __future__ import annotations

import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

from custody_watch.caviar import SCENARIOS, _parse_frames
from custody_watch.tracking import iou
from custody_watch.video import VideoSource

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
IOU_MINIMO = 0.5


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        print("rode primeiro: uv run python scripts/download_caviar.py", file=sys.stderr)
        return 1

    print(f"IoU mínimo {IOU_MINIMO}, detector YOLO26\n")
    cabecalho = (
        f"{'clipe':<22}{'classe':>10}{'anotadas':>10}{'achadas':>9}{'recall':>9}{'altura':>10}"
    )
    print(cabecalho)
    print("-" * len(cabecalho))

    geral_total: dict[str, int] = defaultdict(int)
    geral_achado: dict[str, int] = defaultdict(int)

    for scenario, xml in SCENARIOS.items():
        anotado = dict(_parse_frames(DATA / scenario / xml))

        achados: dict[str, int] = defaultdict(int)
        total: dict[str, int] = defaultdict(int)
        alturas: dict[str, list[float]] = defaultdict(list)
        detectadas = 0

        inicio = time.perf_counter()
        quadros = 0

        for indice, quadro in enumerate(VideoSource(DATA / scenario / f"{scenario}.mpg")):
            quadros += 1
            detectadas += len(quadro.tracked)

            # O MPEG pode decodificar um numero de quadros diferente do que o
            # XML anota. Quadro sem anotacao correspondente nao entra na conta:
            # nao ha verdade contra a qual comparar.
            verdade = anotado.get(indice)
            if verdade is None:
                continue

            for caixa in verdade:
                classe = "suitcase" if caixa.is_bag else "person"
                total[classe] += 1
                alturas[classe].append(caixa.h)
                if any(
                    d.cls == classe and iou(d.bbox, caixa.bbox) >= IOU_MINIMO
                    for d in quadro.tracked
                ):
                    achados[classe] += 1

        decorrido = time.perf_counter() - inicio

        for classe in ("person", "suitcase"):
            if not total[classe]:
                continue
            geral_total[classe] += total[classe]
            geral_achado[classe] += achados[classe]
            print(
                f"{scenario:<22}{classe:>10}{total[classe]:>10}{achados[classe]:>9}"
                f"{achados[classe] / total[classe]:>8.1%}"
                f"{st.mean(alturas[classe]):>8.0f}px"
            )

        print(
            f"{'':22}{quadros} quadros, {detectadas} deteccoes, "
            f"{decorrido:.1f}s ({quadros / max(decorrido, 1e-9):.0f} fps)\n"
        )

    print("-" * len(cabecalho))
    for classe in ("person", "suitcase"):
        if geral_total[classe]:
            print(
                f"{'TOTAL':<22}{classe:>10}{geral_total[classe]:>10}"
                f"{geral_achado[classe]:>9}"
                f"{geral_achado[classe] / geral_total[classe]:>8.1%}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
