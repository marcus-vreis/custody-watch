#!/usr/bin/env python3
"""Deriva a anotação de abandono do CAVIAR a partir do ground truth XML.

O CAVIAR anota a bagagem como objeto próprio, com frame exato de quando
aparece e de quando some. O instante em que ela passa a estar desacompanhada
é derivável: é o primeiro frame em que nenhuma pessoa está a menos de
`unattended_distance_m` dela.

Derivado por script, não digitado à mão. O XML já tem os números, e transcrever
introduziria erro sem comprar nada.

Um frame isolado sem ninguém por perto não é abandono — é oscilação de
anotação, ou o dono virando de lado. Por isso a condição precisa se sustentar
por `DEBOUNCE_S` antes de valer, e o instante anotado é o **início** da corrida
sustentada, não o fim dela. Anotar o fim empurraria o ground truth para perto
do instante em que o sistema emite, e o casamento assimétrico deixaria de
medir o atraso que existe para medir.

    uv run python scripts/annotate_caviar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from custody_watch.annotations import GroundTruthEvent, save_annotations
from custody_watch.caviar import (
    CAVIAR_FPS,
    SCENARIOS,
    _parse_frames,
    estimate_metres_per_pixel,
    ground_plane,
)
from custody_watch.config import Config
from custody_watch.events import EventKind
from custody_watch.ground_plane import GroundPlane

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
SAIDA = RAIZ / "data" / "annotations" / "caviar"

DEBOUNCE_S = 1.0
"""Quanto a ausência precisa durar para contar como abandono.

Curto de propósito. O objetivo é filtrar ruído de anotação, não reproduzir o
limiar do sistema — se fosse `unattended_time_s`, a anotação coincidiria com a
emissão e a janela assimétrica não teria o que medir.
"""


def _serie(
    quadros: list[tuple[int, list]], plane: GroundPlane, limite: float
) -> tuple[list[tuple[int, bool]], set[int]]:
    """Por frame em que há bagagem: o número do frame e se ela está sozinha."""
    serie: list[tuple[int, bool]] = []
    ids: set[int] = set()

    for numero, caixas in quadros:
        bagagens = [c for c in caixas if c.is_bag]
        if not bagagens:
            continue
        ids.update(c.track_id for c in bagagens)

        pessoas = [plane.foot_point(c.bbox) for c in caixas if not c.is_bag]
        sozinha = all(
            all(p.distance_to(plane.foot_point(b.bbox)) > limite for p in pessoas) for b in bagagens
        )
        serie.append((numero, sozinha))

    return serie, ids


def _primeira_corrida(
    serie: list[tuple[int, bool]], duracao_s: float
) -> tuple[int, int, bool] | None:
    """Primeira corrida de ausência que dura ao menos `duracao_s`.

    Devolve `(inicio, fim, truncada)`. `truncada` é verdadeiro quando a corrida
    ainda valia ao fim da série — ou seja, o fim é um limite inferior, não o
    instante em que o estado acabou. A distinção é o que separa "o dono voltou"
    de "a câmera parou", e é ela que impede que o comprimento do clipe vire um
    positivo perdido.

    Não assume que os frames sejam contíguos: mede a corrida em tempo, pelo
    número do frame, e não em quantidade de amostras.
    """
    inicio: int | None = None
    firme = False

    for numero, sozinha in serie:
        if not sozinha:
            if firme:
                assert inicio is not None
                return inicio, numero, False
            inicio = None
            continue

        if inicio is None:
            inicio = numero
        if (numero - inicio) / CAVIAR_FPS >= duracao_s:
            firme = True

    if firme:
        assert inicio is not None
        return inicio, serie[-1][0], True

    return None


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        return 1

    plane = ground_plane(estimate_metres_per_pixel(DATA))
    limite = Config().custody.unattended_distance_m
    print(f"limite de custodia {limite}m, debounce {DEBOUNCE_S}s\n")

    for scenario, xml in SCENARIOS.items():
        quadros = _parse_frames(DATA / scenario / xml)
        serie, ids = _serie(quadros, plane, limite)

        if not serie:
            print(f"{scenario:<22} bagagem nunca aparece no ground truth")
            save_annotations([], SAIDA / f"{scenario}.json", session=f"caviar/{scenario}")
            continue

        if len(ids) > 1:
            print(f"{scenario:<22} {len(ids)} bagagens ({sorted(ids)}) — anotacao trata em bloco")
        bag_id = min(ids)

        cru = next((n for n, sozinha in serie if sozinha), None)
        corrida = _primeira_corrida(serie, DEBOUNCE_S)

        if corrida is None:
            marca = "sem abandono sustentado" if cru is None else f"so oscilacao (frame {cru})"
            print(f"{scenario:<22} {marca}")
            save_annotations([], SAIDA / f"{scenario}.json", session=f"caviar/{scenario}")
            continue

        inicio, fim, truncada = corrida
        abandono = inicio / CAVIAR_FPS
        termino = fim / CAVIAR_FPS
        razao = "cortado pelo fim da anotacao" if truncada else "alguem voltou"
        ruido = "" if cru == inicio else f"  (cru {cru / CAVIAR_FPS:.1f}s, debounce moveu)"
        print(
            f"{scenario:<22} {abandono:6.1f}s -> {termino:6.1f}s "
            f"({termino - abandono:5.1f}s, {razao})  bagagem {bag_id}{ruido}"
        )

        save_annotations(
            [
                GroundTruthEvent(
                    kind=EventKind.BAG_UNATTENDED,
                    t=abandono,
                    t_end=termino,
                    truncated=truncada,
                    bag=bag_id,
                    note=(
                        f"derivado do ground truth XML: corrida de ao menos "
                        f"{DEBOUNCE_S}s sem pessoa a menos de {limite}m da bagagem, "
                        f"encerrada porque {razao}"
                    ),
                )
            ],
            SAIDA / f"{scenario}.json",
            session=f"caviar/{scenario}",
        )

    print(f"\nanotacoes em {SAIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
