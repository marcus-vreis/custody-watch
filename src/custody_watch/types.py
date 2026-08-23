"""Estruturas de dados compartilhadas por todo o pipeline.

Toda posição em `Point` está em METROS no plano do chão, nunca em pixels.
Distância em pixels varia com a profundidade — 50px no fundo da cena são
~4m, na frente são ~40cm. Ver spec §4.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto

PERSON_CLASS = "person"
BAG_CLASSES = frozenset({"suitcase", "backpack", "handbag"})


@dataclass(frozen=True)
class Point:
    """Ponto no plano do chão, em metros."""

    x: float
    y: float

    def distance_to(self, other: Point) -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


@dataclass(frozen=True)
class Detection:
    """Saída crua do detector, em pixels."""

    cls: str
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    confidence: float


@dataclass(frozen=True)
class Observation:
    """Detecção já rastreada e projetada para o plano do chão."""

    track_id: int
    cls: str
    position: Point
    t: float  # segundos desde o início do vídeo


class Bond(Enum):
    """Força do vínculo de uma pessoa com um grupo."""

    STRONG = "strong"
    WEAK = "weak"


@dataclass
class Party:
    party_id: int
    members: dict[int, Bond] = field(default_factory=dict)

    def owns(self, track_id: int) -> bool:
        """Regra P1: posse só flui por vínculo forte.

        Vínculo fraco (apenas sentou perto) mantém o flag, só atenua o peso.
        Sem esta assimetria, um ladrão que senta ao lado da vítima por três
        minutos vira dono legítimo da bagagem.
        """
        return self.members.get(track_id) is Bond.STRONG

    def strong_members(self) -> set[int]:
        return {tid for tid, bond in self.members.items() if bond is Bond.STRONG}


class BagState(Enum):
    NOVA = auto()
    ACOMPANHADA = auto()
    DESACOMPANHADA = auto()
    AMBIGUA = auto()
    RETIRADA_DONO = auto()
    RETIRADA_ESTRANHO = auto()


TERMINAL_BAG_STATES = frozenset({BagState.RETIRADA_DONO, BagState.RETIRADA_ESTRANHO})


@dataclass
class Bag:
    """Bagagem rastreada.

    `anchor` é a última posição conhecida. Regra P2: bagagem parada não
    teleporta, então a posição é a identidade — reassociação usa proximidade
    da âncora, não aparência.
    """

    bag_id: int
    anchor: Point
    state: BagState = BagState.NOVA
    owner_party: int | None = None  # None => órfã
    last_seen: float = 0.0
    unattended_since: float | None = None

    @property
    def is_orphan(self) -> bool:
        return self.owner_party is None


class FlagLevel(IntEnum):
    N1 = 1  # fraco, acumula mas não entra na fila
    N2 = 2  # relacional, entra na fila
    N3 = 3  # evento de custódia, topo da fila


@dataclass(frozen=True)
class Flag:
    """Regra P4: um flag descreve a relação entre uma pessoa e uma bagagem
    ao longo do tempo. Nunca um atributo estático da pessoa.
    """

    kind: str
    level: FlagLevel
    person: int
    bag: int | None
    t: float
    weight: float
    explanation: str  # frase em português, mostrada ao operador
