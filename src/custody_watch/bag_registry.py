"""Registro de bagagens por âncora espacial (spec §4.4).

Regra P2: bagagem parada não teleporta, então a posição é a identidade.
Aparência é inútil aqui — quarenta malas pretas idênticas derrotam qualquer
re-identificação, e é exatamente isso que faz um tracker trocar IDs sob
oclusão.

Regra P3: quando a reassociação é incerta, marcar AMBIGUA e calar.
"""

from __future__ import annotations

from .config import RegistryConfig
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

    def observe(self, observation: Observation) -> Bag:
        """Registra ou atualiza uma bagagem.

        Movimento sub-limiar não move a âncora: jitter de detector não deve ser
        lido como a bagagem sendo carregada.
        """
        bag = self._bags.get(observation.track_id)
        if bag is None:
            bag = Bag(
                bag_id=observation.track_id,
                anchor=observation.position,
                last_seen=observation.t,
            )
            self._bags[bag.bag_id] = bag
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

    def assign_owner(self, bag_id: int, party_id: int) -> None:
        """Vinculação por back-tracing: quem depositou a bagagem."""
        self._bags[bag_id].owner_party = party_id

    def mark_ambiguous_neighbours(self, bag_id: int) -> list[int]:
        """Marca a bagagem e todas as vizinhas dentro do raio como AMBIGUA.

        Chamado quando uma oclusão se resolve com baixa confiança. Marcar todas
        as candidatas é deliberado: não há como saber qual é qual, e chutar
        corromperia o mapa de posse.
        """
        target = self._bags[bag_id]
        affected: list[int] = []
        for other in self._bags.values():
            if target.anchor.distance_to(other.anchor) <= self._config.ambiguity_radius_m:
                other.state = BagState.AMBIGUA
                affected.append(other.bag_id)
        return affected
