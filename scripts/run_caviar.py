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

O relatório compara as duas configurações porque a fragmentação de track era a
causa dominante desses alarmes, e `reid.py` existe para atacá-la.

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

    largura = 22 + 6 + 17 + 28
    print(f"{'':<28}{'-- sem re-ID --':>17}{'------- com re-ID -------':>28}")
    print(
        f"{'clipe':<22}{'dur':>6}{'dono':>8}{'ESTRANHO':>9}"
        f"{'dono':>10}{'ESTRANHO':>9}{'religou':>9}"
    )
    print("-" * largura)

    falsos = {False: 0, True: 0}
    minutos = 0.0

    for scenario in SCENARIOS:
        linha = f"{scenario:<22}"
        for reid in (False, True):
            resultado = run_session(load_clip(DATA, scenario, with_appearance=reid), plane)
            dono = len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_OWNER))
            estranho = len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER))
            falsos[reid] += estranho

            if reid:
                religou = len(resultado.events.of_kind(EventKind.TRACK_RELINKED))
                linha += f"{dono:>10}{estranho:>9}{religou:>9}"
            else:
                minutos += resultado.duration_s / 60.0
                linha += f"{resultado.duration_s:>5.0f}s{dono:>8}{estranho:>9}"
        print(linha)

    print("-" * largura)
    print(f"\n{minutos:.1f} min de video, com ZERO furtos reais.")
    print(f"alarmes falsos:  {falsos[False]} sem re-ID  ->  {falsos[True]} com re-ID")
    print(
        f"por minuto:      {falsos[False] / minutos:.2f}          ->  {falsos[True] / minutos:.2f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
