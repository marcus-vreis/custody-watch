"""Emissão de flags e score com decaimento (spec §4.5).

Regra P4: um flag descreve a relação entre uma pessoa e uma bagagem ao longo
do tempo. Nunca um atributo estático da pessoa.

Um flag do tipo "não carrega bagagem" foi deliberadamente rejeitado: dispara
em 20-30% da população de um aeroporto, não carrega informação, e marca
sistematicamente trabalhadores do aeroporto.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from .types import Bag, BagState, Flag, FlagLevel

TAU_S = 900.0  # meia-vida do decaimento: 15 min

WEIGHT_N1 = 1.0
WEIGHT_N2 = 3.0
WEIGHT_N3 = 10.0


def _timestamp(t: float) -> str:
    minutes, seconds = divmod(int(t), 60)
    return f"{minutes:02d}:{seconds:02d}"


def score(flags: Iterable[Flag], now: float, tau: float = TAU_S) -> float:
    """Soma dos pesos com decaimento exponencial.

    Sem o decaimento, tempo de permanência viraria suspeição: quem passou
    quatro horas no aeroporto acumularia score por existir.
    """
    return sum(f.weight * math.exp(-(now - f.t) / tau) for f in flags if f.t <= now)


def flag_for_removal(bag: Bag, carrier_track: int, t: float) -> Flag | None:
    """Emite N3 quando a bagagem sai com quem não é do grupo dono."""
    if bag.state is not BagState.RETIRADA_ESTRANHO:
        return None

    if bag.is_orphan:
        explanation = (
            f"Bagagem {bag.bag_id}, sem dono identificado, foi retirada em {_timestamp(t)}."
        )
    else:
        explanation = (
            f"Bagagem {bag.bag_id} do grupo {bag.owner_party} foi retirada "
            f"em {_timestamp(t)} por pessoa fora do grupo."
        )

    return Flag(
        kind="retirada_por_estranho",
        level=FlagLevel.N3,
        person=carrier_track,
        bag=bag.bag_id,
        t=t,
        weight=WEIGHT_N3,
        explanation=explanation,
    )


def flag_proximity(person: int, bag: Bag, t: float, seconds_near: float) -> Flag:
    """N1: permanência prolongada junto a bagagem de outro grupo."""
    return Flag(
        kind="permanencia_bagagem_alheia",
        level=FlagLevel.N1,
        person=person,
        bag=bag.bag_id,
        t=t,
        weight=WEIGHT_N1,
        explanation=(
            f"Permaneceu {int(seconds_near)}s a menos de 2m da bagagem "
            f"{bag.bag_id}, de outro grupo, até {_timestamp(t)}."
        ),
    )


def flag_contact(person: int, bag: Bag, t: float) -> Flag:
    """N2: contato físico com bagagem de outro grupo."""
    return Flag(
        kind="contato_bagagem_alheia",
        level=FlagLevel.N2,
        person=person,
        bag=bag.bag_id,
        t=t,
        weight=WEIGHT_N2,
        explanation=(
            f"Tocou na bagagem {bag.bag_id}, do grupo {bag.owner_party}, em {_timestamp(t)}."
        ),
    )


class FlagStore:
    def __init__(self, tau: float = TAU_S) -> None:
        self._by_person: dict[int, list[Flag]] = defaultdict(list)
        self._tau = tau

    def add(self, flag: Flag) -> None:
        self._by_person[flag.person].append(flag)

    def for_person(self, person: int) -> list[Flag]:
        return sorted(self._by_person.get(person, []), key=lambda f: f.t)

    def score(self, person: int, now: float) -> float:
        return score(self._by_person.get(person, []), now, self._tau)

    def people(self) -> list[int]:
        return list(self._by_person)
