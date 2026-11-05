"""Eventos como unidade de primeira classe.

O sistema não raciocina sobre frames — raciocina sobre janelas de tempo.
`is_comoving` recebe trajetórias, a máquina de custódia trabalha em deltas.
Este módulo torna isso explícito e, mais importante, serializável.

O campo `evidence` é o que faz a auditoria funcionar. Um evento de vínculo
forte carrega as medidas que o justificaram — extensão de cada trajetória,
duração da sobreposição, separação máxima. Quando o operador contesta um
alerta, a decisão é reproduzível a partir do log, sem o vídeo.

O export legado é JSONL, uma linha por evento. Persistência incremental com
sessão, segmento e rotação pertence a `event_journal.py`.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable, Iterator
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

    Sem `max_events`, mantém o comportamento legado de histórico completo.
    Com limite, `len` e export JSONL cobrem somente a cauda; `total_events`
    conta todas as emissões. O histórico completo fica com o sink opcional.
    """

    def __init__(
        self,
        events: list[Event] | None = None,
        *,
        max_events: int | None = None,
        sink: Callable[[Event], bool] | None = None,
    ) -> None:
        if max_events is not None and (type(max_events) is not int or max_events <= 0):
            raise ValueError("max_events: esperado inteiro positivo")
        initial = list(events or [])
        self._events = initial if max_events is None else deque(initial, maxlen=max_events)
        self._bounded = max_events is not None
        self._sink = sink
        self._total_events = len(initial)
        self._tail_lock = threading.Lock()
        self._emit_lock = threading.Lock()

    def emit(self, event: Event) -> None:
        # A cauda continua disponível mesmo se o sink não confirmar a escrita.
        # Consulta da UI não segura a trava de IO enquanto lê a cauda em RAM.
        with self._emit_lock:
            if self._sink is not None:
                self._sink(event)
            with self._tail_lock:
                self._events.append(event)
                self._total_events += 1

    @property
    def total_events(self) -> int:
        with self._tail_lock:
            return self._total_events

    def of_kind(self, kind: EventKind) -> list[Event]:
        with self._tail_lock:
            return [event for event in self._events if event.kind is kind]

    def __iter__(self) -> Iterator[Event]:
        with self._tail_lock:
            return iter(tuple(self._events) if self._bounded else self._events)

    def __reversed__(self) -> Iterator[Event]:
        """Do mais recente para o mais antigo. A tela ao vivo quer só a
        cauda. Só a cauda limitada vira snapshot, para impedir mutação de uma
        deque durante a iteração. O modo legado não copia o histórico todo."""
        with self._tail_lock:
            return reversed(tuple(self._events) if self._bounded else self._events)

    def __len__(self) -> int:
        with self._tail_lock:
            return len(self._events)

    def to_jsonl(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for event in self:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def from_jsonl(cls, path: Path | str) -> EventLog:
        events = [
            Event.from_dict(json.loads(line))
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(events)
