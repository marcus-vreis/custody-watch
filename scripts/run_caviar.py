#!/usr/bin/env python3
"""Roda o orquestrador nos clipes do CAVIAR e reporta o que aconteceu.

O CAVIAR **não contém furtos**. Verificado no ground truth: em `LeftBag` e
`LeftBag_AtChair` a bagagem é retirada por um track de id diferente do que a
depositou, mas os dois nunca coexistem em cena — o track do dono termina e um
novo aparece cerca de cinco segundos depois, com a mesma aparência. É
fragmentação de tracking, não furto. Em `LeftBag_PickedUp` é literalmente o
mesmo id. Em `LeftBox` ninguém recolhe.

Isso torna impossível medir `P_miss`: sem evento positivo, não há o que perder.
Mas torna possível medir algo que testa o design de forma mais dura — **quantos
alarmes de furto o sistema gera em vídeos onde ninguém furta nada**. Todo
`bag_removed_by_stranger` aqui é, por construção, um alarme falso.

    uv run python scripts/run_caviar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from custody_watch.caviar import SCENARIOS, estimate_metres_per_pixel, ground_plane, load_clip
from custody_watch.events import EventKind
from custody_watch.orchestrator import run_session

DATA = Path(__file__).resolve().parent.parent / "data" / "caviar"


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        print("rode primeiro: uv run python scripts/download_caviar.py", file=sys.stderr)
        return 1

    escala = estimate_metres_per_pixel(DATA)
    plane = ground_plane(escala)

    print(f"escala estimada: {escala:.4f} m/px  (largura do frame ~= {384 * escala:.1f} m)")
    print("APROXIMADA: perspectiva ignorada, ver docstring de caviar.py\n")

    cabecalho = (
        f"{'clipe':<22}{'frames':>7}{'dur':>7}{'aband':>7}{'dono':>6}{'ESTRANHO':>10}{'fila':>6}"
    )
    print(cabecalho)
    print("-" * len(cabecalho))

    total_falsos = 0
    total_minutos = 0.0

    for scenario in SCENARIOS:
        result = run_session(load_clip(DATA, scenario), plane)

        aband = len(result.events.of_kind(EventKind.BAG_UNATTENDED))
        dono = len(result.events.of_kind(EventKind.BAG_REMOVED_BY_OWNER))
        estranho = len(result.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER))

        total_falsos += estranho
        total_minutos += result.duration_s / 60.0

        print(
            f"{scenario:<22}{result.frames:>7}{result.duration_s:>6.0f}s"
            f"{aband:>7}{dono:>6}{estranho:>10}{len(result.queue):>6}"
        )

    print("-" * len(cabecalho))
    print(f"\nvideo total: {total_minutos:.1f} min, com ZERO furtos reais")
    print(f"alarmes de furto emitidos: {total_falsos}")
    if total_minutos > 0:
        print(f"taxa de falso alarme: {total_falsos / total_minutos:.2f} por minuto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
