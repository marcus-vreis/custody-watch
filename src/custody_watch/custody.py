"""Máquina de estados de custódia (spec §4.4).

Limiares 3m / 25s vêm do protocolo do PETS2007, o que torna o ground truth
comparável ao benchmark sem trabalho extra.

É aqui que a Regra P1 vira decisão operacional: `Party.owns` exige vínculo
forte, então quem apenas sentou perto da vítima não legitima a retirada.
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import CustodyConfig
from .events import Event, EventKind, EventLog
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
    events: EventLog | None = None,
) -> Bag:
    """ACOMPANHADA <-> DESACOMPANHADA conforme a distância do grupo dono.

    Qualquer membro do grupo serve: a posse pertence ao grupo, não ao
    indivíduo. Bagagem órfã não acumula tempo — não há dono cuja ausência
    signifique alguma coisa.

    Roda a cada frame, mas só emite na TRANSIÇÃO de estado: emitir toda vez
    que a bagagem *está* desacompanhada afogaria o log num vídeo longo.
    Quando `events` é `None`, nada é emitido.
    """
    if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
        return bag
    if bag.is_orphan:
        return bag

    distancias = [
        p.position.distance_to(bag.anchor)
        for p in people
        if party_manager.party_of(p.track_id) == bag.owner_party
    ]
    mais_proximo = min(distancias) if distancias else None
    owner_nearby = mais_proximo is not None and mais_proximo <= config.unattended_distance_m

    previous_state = bag.state

    if owner_nearby:
        bag.state = BagState.ACOMPANHADA
        bag.unattended_since = None
    elif bag.unattended_since is None:
        bag.unattended_since = t
    elif t - bag.unattended_since >= config.unattended_time_s:
        bag.state = BagState.DESACOMPANHADA

    if events is None or bag.state is previous_state:
        return bag

    if bag.state is BagState.DESACOMPANHADA:
        events.emit(
            Event(
                kind=EventKind.BAG_UNATTENDED,
                t_start=t,
                t_end=t,
                subject=None,
                bag=bag.bag_id,
                party=bag.owner_party,
                evidence={
                    "distance_m": mais_proximo,
                    "elapsed_s": t - bag.unattended_since,
                },
            )
        )
    elif previous_state is BagState.DESACOMPANHADA and bag.state is BagState.ACOMPANHADA:
        events.emit(
            Event(
                kind=EventKind.BAG_REATTENDED,
                t_start=t,
                t_end=t,
                subject=None,
                bag=bag.bag_id,
                party=bag.owner_party,
                evidence={"distance_m": mais_proximo},
            )
        )

    return bag


def resolve_removal(
    bag: Bag,
    carrier_track: int,
    party_manager: PartyManager,
    t: float = 0.0,
    events: EventLog | None = None,
) -> Bag:
    """A bagagem se moveu: quem a levou pertence ao grupo dono?

    `Party.owns` exige vínculo FORTE. Um membro de vínculo fraco — alguém que
    apenas sentou perto — não legitima a retirada.

    `t` é o instante do frame; o default existe só para não quebrar chamadas
    antigas — o orquestrador deve sempre passar o instante real do frame.
    Quando `events` é `None`, nada é emitido.
    """
    if bag.state is BagState.AMBIGUA or bag.state in TERMINAL_BAG_STATES:
        return bag

    if bag.is_orphan:
        bag.state = BagState.RETIRADA_ESTRANHO
    else:
        party = party_manager.get(bag.owner_party)
        legitimate = party is not None and party.owns(carrier_track)
        bag.state = BagState.RETIRADA_DONO if legitimate else BagState.RETIRADA_ESTRANHO

    if events is not None:
        if bag.state is BagState.RETIRADA_DONO:
            events.emit(
                Event(
                    kind=EventKind.BAG_REMOVED_BY_OWNER,
                    t_start=t,
                    t_end=t,
                    subject=carrier_track,
                    bag=bag.bag_id,
                    party=bag.owner_party,
                    evidence={"carrier": carrier_track, "bond": "strong"},
                )
            )
        else:
            events.emit(
                Event(
                    kind=EventKind.BAG_REMOVED_BY_STRANGER,
                    t_start=t,
                    t_end=t,
                    subject=carrier_track,
                    bag=bag.bag_id,
                    party=bag.owner_party,
                    evidence={"carrier": carrier_track, "owner_party": bag.owner_party},
                )
            )

    return bag
