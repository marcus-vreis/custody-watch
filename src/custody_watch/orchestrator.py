"""Liga os módulos num pipeline que consome frames e produz uma fila de alertas.

Até aqui cada módulo era testado isoladamente. Este é o primeiro código que os
faz conversar, e por isso é onde as suposições de cada um encostam na
realidade.

Duas decisões que o v1 não tinha tomado, porque só aparecem quando existe um
consumidor de frames:

**Quando um grupo se forma.** `form_on_arrival` espera receber quem chegou
junto, mas ninguém decidia quem é "junto". Aqui cada track novo forma um grupo
de um, e `try_join_strong` funde depois quem se provar co-movendo. É a escolha
conservadora: começar separado e exigir evidência para unir, em vez do inverso.

**Quando uma bagagem foi levada.** O ground truth do CAVIAR simplesmente para
de anotar a bagagem quando ela é recolhida. Então ausência sustentada por
`missing_frames_before_removal` conta como retirada, e o carregador é a pessoa
mais próxima da âncora no último frame em que a bagagem apareceu.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace

from .alerts import AlertItem, build_queue
from .bag_registry import BagRegistry
from .config import Config
from .custody import resolve_removal, update_attendance
from .events import Event, EventKind, EventLog
from .flags import FlagStore, flag_for_removal
from .ground_plane import GroundPlane
from .party import PartyManager
from .reid import TrackLinker
from .tracking import TrackedDetection, to_observations
from .types import BAG_CLASSES, TERMINAL_BAG_STATES, BagState, Observation

MISSING_FRAMES_BEFORE_REMOVAL = 5
OWNER_SEARCH_RADIUS_M = 3.0


@dataclass
class SessionResult:
    events: EventLog
    queue: list[AlertItem]
    frames: int
    duration_s: float
    flags: FlagStore = field(default_factory=FlagStore)


def _nearest_person(people: Iterable[Observation], target) -> Observation | None:
    candidates = list(people)
    if not candidates:
        return None
    return min(candidates, key=lambda p: p.position.distance_to(target))


def run_session(
    frames: Iterator[tuple[float, list[TrackedDetection]]],
    plane: GroundPlane,
    config: Config | None = None,
    missing_frames_before_removal: int = MISSING_FRAMES_BEFORE_REMOVAL,
) -> SessionResult:
    config = config or Config()

    events = EventLog()
    flag_store = FlagStore()
    parties = PartyManager(config.party)
    registry = BagRegistry(config.registry)
    linker = TrackLinker(config.reid)

    missing: dict[int, int] = {}
    last_people: dict[int, list[Observation]] = {}
    frame_count = 0
    last_t = 0.0

    for t, tracked in frames:
        frame_count += 1
        last_t = t

        observations = to_observations(tracked, plane, t)
        aparencias = {d.track_id: d.appearance for d in tracked}
        bags = [o for o in observations if o.cls in BAG_CLASSES]

        # O religador roda antes de qualquer decisão de posse: um track que é
        # continuação de outro precisa herdar o grupo, não formar um novo.
        people = []
        for bruto in observations:
            if bruto.cls in BAG_CLASSES:
                continue

            resolucao = linker.observe(
                bruto.track_id, t, bruto.position, aparencias.get(bruto.track_id)
            )
            if not resolucao.settled:
                # Perfil ainda ruidoso demais. O track existe, mas não recebe
                # posse de bagagem até se estabilizar.
                continue

            if resolucao.linked_from is not None:
                events.emit(
                    Event(
                        kind=EventKind.TRACK_RELINKED,
                        t_start=t,
                        t_end=t,
                        subject=resolucao.canonical_id,
                        bag=None,
                        party=parties.party_of(resolucao.canonical_id),
                        evidence={
                            "from_track": bruto.track_id,
                            "similarity": resolucao.similarity,
                            "margin": resolucao.margin,
                        },
                    )
                )

            people.append(replace(bruto, track_id=resolucao.canonical_id))

        # Cada track novo começa como grupo de um. Unir exige evidência.
        for person in people:
            if parties.party_of(person.track_id) is None:
                parties.form_on_arrival([person.track_id], t=t, events=events)

        seen_now: set[int] = set()
        for observation in bags:
            seen_now.add(observation.track_id)
            missing.pop(observation.track_id, None)

            known = registry.get(observation.track_id)
            bag = registry.observe(observation, events=events)

            if known is None:
                # Back-tracing: quem depositou a bagagem vira o dono.
                carrier = _nearest_person(people, bag.anchor)
                if (
                    carrier is not None
                    and carrier.position.distance_to(bag.anchor) <= OWNER_SEARCH_RADIUS_M
                ):
                    party_id = parties.party_of(carrier.track_id)
                    if party_id is not None:
                        registry.assign_owner(bag.bag_id, party_id, t=t, events=events)

            last_people[bag.bag_id] = people

        for bag in registry.all():
            if bag.state in TERMINAL_BAG_STATES:
                continue
            if bag.bag_id in seen_now:
                update_attendance(bag, people, parties, t, config.custody, events=events)
                continue

            missing[bag.bag_id] = missing.get(bag.bag_id, 0) + 1
            if missing[bag.bag_id] < missing_frames_before_removal:
                continue

            # Quem levou pode estar no último frame em que a bagagem apareceu
            # — é o caso do CAVIAR, cuja anotação para no instante da retirada —
            # ou ter chegado depois, se o tracker perdeu a bagagem antes. Olhar
            # só para trás faria o segundo caso atribuir a retirada ao dono.
            candidatos = {o.track_id: o for o in last_people.get(bag.bag_id, [])}
            candidatos.update({o.track_id: o for o in people})

            carrier = _nearest_person(candidatos.values(), bag.anchor)
            if carrier is None:
                continue

            resolve_removal(bag, carrier.track_id, parties, t=t, events=events)
            flag = flag_for_removal(bag, carrier.track_id, t, config.flags)
            if flag is not None:
                flag_store.add(flag)

    return SessionResult(
        events=events,
        queue=build_queue(flag_store, last_t, config.alerts),
        frames=frame_count,
        duration_s=last_t,
        flags=flag_store,
    )


def removal_outcomes(result: SessionResult) -> dict[BagState, int]:
    """Contagem por desfecho de custódia, para relatório."""
    counts: dict[BagState, int] = {}
    for event in result.events:
        if event.kind.value == "bag_removed_by_stranger":
            counts[BagState.RETIRADA_ESTRANHO] = counts.get(BagState.RETIRADA_ESTRANHO, 0) + 1
        elif event.kind.value == "bag_removed_by_owner":
            counts[BagState.RETIRADA_DONO] = counts.get(BagState.RETIRADA_DONO, 0) + 1
    return counts
