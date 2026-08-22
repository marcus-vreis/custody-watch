"""Fila ranqueada de alertas (spec §4.6).

O sistema não acusa. Ele ordena.

Com um furto real a cada ~30.000 eventos de custódia, nenhum limiar produz
precisão utilizável — um detector binário é matematicamente inviável. O
operador trabalha do topo para baixo até onde o turno permite, e a fila
degrada bem quando o volume sobe.

A explicação é requisito, não enfeite: sem ela o operador não consegue
revisar nem contestar, e desliga o sistema na segunda semana.
"""

from __future__ import annotations

from dataclasses import dataclass

from .flags import FlagStore
from .types import FlagLevel

CLIP_MARGIN_S = 10.0
QUEUE_MIN_LEVEL = FlagLevel.N2
OPERATOR_HOURLY_BUDGET = 25


@dataclass(frozen=True)
class AlertItem:
    person: int
    score: float
    top_level: FlagLevel
    clip_start: float
    clip_end: float
    explanations: list[str]


def build_queue(
    store: FlagStore,
    now: float,
    clip_margin_s: float = CLIP_MARGIN_S,
    min_level: FlagLevel = QUEUE_MIN_LEVEL,
) -> list[AlertItem]:
    """Monta a fila ranqueada por score decrescente.

    Pessoas cujos flags são todos N1 não entram: acumulam contexto mas não
    consomem tempo de operador.
    """
    items: list[AlertItem] = []

    for person in store.people():
        flags = store.for_person(person)
        if not flags:
            continue

        top_level = max(f.level for f in flags)
        if top_level < min_level:
            continue

        # Âncora do clipe é o flag mais grave, não o mais recente: o operador
        # precisa ver o evento de custódia, não o ruído que veio depois.
        gravest = max(flags, key=lambda f: (f.level, f.t))
        items.append(
            AlertItem(
                person=person,
                score=store.score(person, now),
                top_level=top_level,
                clip_start=max(0.0, gravest.t - clip_margin_s),
                clip_end=gravest.t + clip_margin_s,
                explanations=[f.explanation for f in flags],
            )
        )

    return sorted(items, key=lambda item: item.score, reverse=True)


class AlertQueue:
    def __init__(self, store: FlagStore) -> None:
        self._store = store

    def top(self, now: float, limit: int = OPERATOR_HOURLY_BUDGET) -> list[AlertItem]:
        """Os `limit` itens mais relevantes.

        O default corresponde ao orçamento estimado de um operador por hora:
        um clipe de 15s revisado em cerca de 30 segundos.
        """
        return build_queue(self._store, now)[:limit]
