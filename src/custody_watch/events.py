"""Eventos como unidade de primeira classe.

O sistema não raciocina sobre frames — raciocina sobre janelas de tempo.
`is_comoving` recebe trajetórias, a máquina de custódia trabalha em deltas.
Este módulo torna isso explícito e, mais importante, serializável.

O campo `evidence` é o que faz a auditoria funcionar. Um evento de vínculo
forte carrega as medidas que o justificaram — extensão de cada trajetória,
duração da sobreposição, separação máxima. Quando o operador contesta um
alerta, a decisão é reproduzível a partir do log, sem o vídeo.

Formato JSONL: uma linha por evento, append-only e streamável.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class EventKind(StrEnum):
    BAG_APPEARED = "bag_appeared"
    BAG_OWNED = "bag_owned"
    BAG_UNATTENDED = "bag_unattended"
    BAG_REATTENDED = "bag_reattended"
    BAG_AMBIGUOUS = "bag_ambiguous"
    BAG_OCCLUDED = "bag_occluded"
    BAG_REMOVED_BY_OWNER = "bag_removed_by_owner"
    BAG_REMOVED_BY_STRANGER = "bag_removed_by_stranger"
    PARTY_FORMED = "party_formed"
    PARTY_JOINED_STRONG = "party_joined_strong"
    PARTY_JOINED_WEAK = "party_joined_weak"
    TRACK_RELINKED = "track_relinked"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    t_start: float
    t_end: float
    subject: int | None  # track_id da pessoa envolvida
    bag: int | None
    party: int | None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.t_end < self.t_start:
            raise ValueError(f"t_end ({self.t_end}) anterior a t_start ({self.t_start})")

    @property
    def duration_s(self) -> float:
        return self.t_end - self.t_start

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "t_start": self.t_start,
            "t_end": self.t_end,
            "subject": self.subject,
            "bag": self.bag,
            "party": self.party,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Event:
        return cls(
            kind=EventKind(payload["kind"]),
            t_start=float(payload["t_start"]),
            t_end=float(payload["t_end"]),
            subject=payload["subject"],
            bag=payload["bag"],
            party=payload["party"],
            evidence=dict(payload.get("evidence", {})),
        )


class EventLog:
    """Coleção ordenada por emissão, não por tempo de evento.

    A ordem de emissão é a ordem em que o sistema decidiu, que é o que importa
    para reproduzir o raciocínio. Ordenar por `t_start` esconderia decisões
    tomadas fora de ordem.
    """

    def __init__(self, events: list[Event] | None = None) -> None:
        self._events: list[Event] = list(events or [])

    def emit(self, event: Event) -> None:
        self._events.append(event)

    def of_kind(self, kind: EventKind) -> list[Event]:
        return [event for event in self._events if event.kind is kind]

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def to_jsonl(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for event in self._events:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def from_jsonl(cls, path: Path | str) -> EventLog:
        events = [
            Event.from_dict(json.loads(line))
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(events)
