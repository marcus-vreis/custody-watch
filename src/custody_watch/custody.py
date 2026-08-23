"""Máquina de estados de custódia (spec §4.4).

Limiares 3m / 25s vêm do protocolo do PETS2007, o que torna o ground truth
comparável ao benchmark sem trabalho extra.

É aqui que a Regra P1 vira decisão operacional: `Party.owns` exige vínculo
forte, então quem apenas sentou perto da vítima não legitima a retirada.
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import CustodyConfig
from .party import PartyManager
from .types import TERMINAL_BAG_STATES, Bag, BagState, Observation

DEFAULT_CUSTODY = CustodyConfig()

UNATTENDED_DISTANCE_M = DEFAULT_CUSTODY.unattended_distance_m
UNATTENDED_TIME_S = DEFAULT_CUSTODY.unattended_time_s


def update_attendance(
    bag: Bag,
    people: Sequence[Observation],
    party_manager: PartyManager,
    t: float,
    config: CustodyConfig = DEFAULT_CUSTODY,
) -> Bag:
    """ACOMPANHADA <-> DESACOMPANHADA conforme a distância do grupo dono.

    Qualquer membro do grupo serve: a posse pertence ao grupo, não ao
    indivíduo. Bagagem órfã não acumula tempo — não há dono cuja ausência
    signifique alguma coisa.
    """
    if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
        return bag
    if bag.is_orphan:
        return bag

    owner_nearby = any(
        party_manager.party_of(p.track_id) == bag.owner_party
        and p.position.distance_to(bag.anchor) <= config.unattended_distance_m
        for p in people
    )

    if owner_nearby:
        bag.state = BagState.ACOMPANHADA
        bag.unattended_since = None
    elif bag.unattended_since is None:
        bag.unattended_since = t
    elif t - bag.unattended_since >= config.unattended_time_s:
        bag.state = BagState.DESACOMPANHADA

    return bag


def resolve_removal(bag: Bag, carrier_track: int, party_manager: PartyManager) -> Bag:
    """A bagagem se moveu: quem a levou pertence ao grupo dono?

    `Party.owns` exige vínculo FORTE. Um membro de vínculo fraco — alguém que
    apenas sentou perto — não legitima a retirada.
    """
    if bag.state is BagState.AMBIGUA or bag.state in TERMINAL_BAG_STATES:
        return bag

    if bag.is_orphan:
        bag.state = BagState.RETIRADA_ESTRANHO
        return bag

    party = party_manager.get(bag.owner_party)
    legitimate = party is not None and party.owns(carrier_track)
    bag.state = BagState.RETIRADA_DONO if legitimate else BagState.RETIRADA_ESTRANHO
    return bag
