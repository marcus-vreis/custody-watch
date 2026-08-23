"""Registro de bagagens por âncora espacial (spec §4.4).

Regra P2: bagagem parada não teleporta, então a posição é a identidade.
Aparência é inútil aqui — quarenta malas pretas idênticas derrotam qualquer
re-identificação, e é exatamente isso que faz um tracker trocar IDs sob
oclusão.

Regra P3: quando a reassociação é incerta, marcar AMBIGUA e calar.
"""

from __future__ import annotations

from .config import RegistryConfig
from .events import Event, EventKind, EventLog
from .types import Bag, BagState, Observation

DEFAULT_REGISTRY = RegistryConfig()

MOVED_THRESHOLD_M = DEFAULT_REGISTRY.moved_threshold_m
AMBIGUITY_RADIUS_M = DEFAULT_REGISTRY.ambiguity_radius_m


class BagRegistry:
    def __init__(self, config: RegistryConfig = DEFAULT_REGISTRY) -> None:
        self._bags: dict[int, Bag] = {}
        self._config = config

    def get(self, bag_id: int) -> Bag | None:
        return self._bags.get(bag_id)

    def all(self) -> list[Bag]:
        return list(self._bags.values())

    def observe(self, observation: Observation, events: EventLog | None = None) -> Bag:
        """Registra ou atualiza uma bagagem.

        Movimento sub-limiar não move a âncora: jitter de detector não deve ser
        lido como a bagagem sendo carregada.

        Emite `BAG_APPEARED` apenas para bagagem nova — uma já conhecida não
        emite, senão cada frame geraria um evento. `t` vem de `observation.t`.
        Quando `events` é `None`, nada é emitido.
        """
        bag = self._bags.get(observation.track_id)
        if bag is None:
            bag = Bag(
                bag_id=observation.track_id,
                anchor=observation.position,
                last_seen=observation.t,
            )
            self._bags[bag.bag_id] = bag

            if events is not None:
                events.emit(
                    Event(
                        kind=EventKind.BAG_APPEARED,
                        t_start=observation.t,
                        t_end=observation.t,
                        subject=None,
                        bag=bag.bag_id,
                        party=bag.owner_party,
                        evidence={"anchor": [bag.anchor.x, bag.anchor.y]},
                    )
                )
            return bag

        if bag.anchor.distance_to(observation.position) >= self._config.moved_threshold_m:
            bag.anchor = observation.position
        bag.last_seen = observation.t
        return bag

    def has_moved(self, observation: Observation) -> bool:
        bag = self._bags.get(observation.track_id)
        if bag is None:
            return False
        return bag.anchor.distance_to(observation.position) >= self._config.moved_threshold_m

    def assign_owner(
        self,
        bag_id: int,
        party_id: int,
        t: float = 0.0,
        events: EventLog | None = None,
    ) -> None:
        """Vinculação por back-tracing: quem depositou a bagagem.

        `t` é o instante do frame; o default existe só para não quebrar
        chamadas antigas. Quando `events` é `None`, nada é emitido.
        """
        self._bags[bag_id].owner_party = party_id

        if events is not None:
            events.emit(
                Event(
                    kind=EventKind.BAG_OWNED,
                    t_start=t,
                    t_end=t,
                    subject=None,
                    bag=bag_id,
                    party=party_id,
                    evidence={"party": party_id},
                )
            )

    def mark_ambiguous_neighbours(
        self,
        bag_id: int,
        t: float = 0.0,
        events: EventLog | None = None,
    ) -> list[int]:
        """Marca a bagagem e todas as vizinhas dentro do raio como AMBIGUA.

        Chamado quando uma oclusão se resolve com baixa confiança. Marcar todas
        as candidatas é deliberado: não há como saber qual é qual, e chutar
        corromperia o mapa de posse.

        Emite um único `BAG_AMBIGUOUS`, com o alvo em `bag` e a lista completa
        de afetados na evidência — não um evento por bagagem marcada. `t` é o
        instante do frame; o default existe só para não quebrar chamadas
        antigas. Quando `events` é `None`, nada é emitido.
        """
        target = self._bags[bag_id]
        affected: list[int] = []
        for other in self._bags.values():
            if target.anchor.distance_to(other.anchor) <= self._config.ambiguity_radius_m:
                other.state = BagState.AMBIGUA
                affected.append(other.bag_id)

        if events is not None:
            events.emit(
                Event(
                    kind=EventKind.BAG_AMBIGUOUS,
                    t_start=t,
                    t_end=t,
                    subject=None,
                    bag=bag_id,
                    party=target.owner_party,
                    evidence={
                        "neighbours": affected,
                        "radius_m": self._config.ambiguity_radius_m,
                    },
                )
            )
        return affected
