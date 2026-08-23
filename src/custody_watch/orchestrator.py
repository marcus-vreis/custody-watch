"""Liga os módulos num pipeline que consome frames e produz uma fila de alertas.

Até aqui cada módulo era testado isoladamente. Este é o primeiro código que os
faz conversar, e por isso é onde as suposições de cada um encostam na
realidade.

## Por que ninguém começa com grupo

A primeira versão deste módulo dava a cada track novo um grupo de um. Parecia
a escolha conservadora — começar separado e exigir evidência para unir — mas
**bloqueava toda fusão**. `try_join_strong` recusa migração entre grupos, para
que andar ao lado de um estranho numa fila não dissolva a própria família; e
`join_weak` recusa quem já tem grupo. Com todo mundo já afiliado, as duas
guardas rejeitavam tudo, e o sistema de grupos — a defesa contra o maior
falso positivo do projeto, casais e famílias compartilhando bagagem — ficava
sem efeito no pipeline apesar de testado e revisado.

Agora pessoas começam **sem grupo**. O grupo nasce de evidência: co-movimento
sustentado funde dois tracks, e quem precisa ser dono de uma bagagem ganha um
grupo de um na hora.

## Quando uma bagagem foi levada

O ground truth do CAVIAR simplesmente para de anotar a bagagem quando ela é
recolhida. Então ausência sustentada conta como retirada, e o carregador é a
pessoa mais próxima da âncora — considerando tanto o último frame em que a
bagagem apareceu quanto o momento atual, porque quem levou pode ter chegado
depois de o tracker perder a bagagem.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from itertools import combinations

from .alerts import AlertItem, build_queue
from .bag_registry import BagRegistry
from .config import Config
from .custody import resolve_removal, update_attendance
from .events import Event, EventKind, EventLog
from .flags import FlagStore, flag_contact, flag_for_removal, flag_proximity
from .ground_plane import GroundPlane
from .party import PartyManager, is_comoving
from .reid import TrackLinker
from .tracking import TrackedDetection, to_observations
from .types import BAG_CLASSES, TERMINAL_BAG_STATES, Bag, BagState, Observation


@dataclass
class SessionResult:
    events: EventLog
    queue: list[AlertItem]
    frames: int
    duration_s: float
    flags: FlagStore = field(default_factory=FlagStore)
    links: dict[int, int] = field(default_factory=dict)
    """Mapa de id bruto para canônico. O recorte de clipe precisa do inverso:
    o alerta cita o id canônico, e os frames trazem os brutos."""

    def raw_ids(self, canonical_id: int) -> set[int]:
        brutos = {raw for raw, canon in self.links.items() if canon == canonical_id}
        brutos.add(canonical_id)
        return brutos


def _nearest(people: Iterable[Observation], target) -> Observation | None:
    candidates = list(people)
    if not candidates:
        return None
    return min(candidates, key=lambda p: p.position.distance_to(target))


class _Session:
    """Estado de uma passagem sobre um vídeo. Descartado ao fim."""

    def __init__(self, plane: GroundPlane, config: Config) -> None:
        self.plane = plane
        self.config = config

        self.events = EventLog()
        self.flags = FlagStore()
        self.parties = PartyManager(config.party)
        self.registry = BagRegistry(config.registry)
        self.linker = TrackLinker(config.reid)

        self.history: dict[int, list[Observation]] = {}
        self.missing: dict[int, int] = {}
        self.last_people: dict[int, list[Observation]] = {}

        # Marcações para não repetir o mesmo flag a cada frame.
        self.near_since: dict[tuple[int, int], float] = {}
        self.proximity_flagged: set[tuple[int, int]] = set()
        self.contact_flagged: set[tuple[int, int]] = set()
        self.together_since: dict[tuple[int, int], float] = {}

        self.frames = 0
        self.t = 0.0

    # --- percepção ------------------------------------------------------

    def resolve_people(self, observations: list[Observation], tracked) -> list[Observation]:
        """Religa tracks fragmentados antes de qualquer decisão de posse."""
        appearances = {d.track_id: d.appearance for d in tracked}
        people: list[Observation] = []

        for raw in observations:
            if raw.cls in BAG_CLASSES:
                continue

            decision = self.linker.observe(
                raw.track_id, self.t, raw.position, appearances.get(raw.track_id)
            )
            if not decision.settled:
                continue

            if decision.linked_from is not None:
                self.events.emit(
                    Event(
                        kind=EventKind.TRACK_RELINKED,
                        t_start=self.t,
                        t_end=self.t,
                        subject=decision.canonical_id,
                        bag=None,
                        party=self.parties.party_of(decision.canonical_id),
                        evidence={
                            "from_track": raw.track_id,
                            "similarity": decision.similarity,
                            "margin": decision.margin,
                        },
                    )
                )

            people.append(replace(raw, track_id=decision.canonical_id))

        for person in people:
            janela = self.history.setdefault(person.track_id, [])
            janela.append(person)
            corte = self.t - self.config.pipeline.history_window_s
            self.history[person.track_id] = [o for o in janela if o.t >= corte]

        return people

    # --- grupos ---------------------------------------------------------

    def merge_parties(self, people: list[Observation]) -> None:
        """Funde quem se provar co-movendo, e atenua quem só ficou perto."""
        for a, b in combinations(people, 2):
            if a.position.distance_to(b.position) > self.config.party.proximity_m:
                self.together_since.pop(
                    (min(a.track_id, b.track_id), max(a.track_id, b.track_id)), None
                )
                continue

            pa = self.parties.party_of(a.track_id)
            pb = self.parties.party_of(b.track_id)
            if pa is not None and pb is not None:
                continue

            ha = self.history.get(a.track_id, [])
            hb = self.history.get(b.track_id, [])

            if is_comoving(ha, hb, self.config.party):
                if pa is None and pb is None:
                    # Mesmo formando do zero, a barra é a da entrada tardia:
                    # aqui não dá para observar chegada em cena, então "andaram
                    # juntos" é tudo que se sabe, e é a evidência mais fraca.
                    novo = self.parties.form_on_arrival([a.track_id], t=self.t, events=self.events)
                    self.parties.try_join_strong(
                        novo.party_id, b.track_id, ha, hb, events=self.events
                    )
                elif pa is not None:
                    self.parties.try_join_strong(pa, b.track_id, ha, hb, events=self.events)
                else:
                    self.parties.try_join_strong(pb, a.track_id, hb, ha, events=self.events)
                continue

            self._maybe_weak_bond(a, b, pa, pb)

    def _maybe_weak_bond(self, a, b, pa: int | None, pb: int | None) -> None:
        """Proximidade estática prolongada. Não transfere posse — atenua o flag."""
        chave = (min(a.track_id, b.track_id), max(a.track_id, b.track_id))
        desde = self.together_since.setdefault(chave, self.t)
        if self.t - desde < self.config.party.weak_bond_s:
            return

        if pa is not None and pb is None:
            self.parties.join_weak(pa, b.track_id, t=self.t, events=self.events)
        elif pb is not None and pa is None:
            self.parties.join_weak(pb, a.track_id, t=self.t, events=self.events)

    def party_for(self, track_id: int) -> int:
        """Grupo de quem precisa ter posse. Nasce aqui se ainda não existe."""
        existente = self.parties.party_of(track_id)
        if existente is not None:
            return existente
        return self.parties.form_on_arrival([track_id], t=self.t, events=self.events).party_id

    # --- bagagem --------------------------------------------------------

    def observe_bags(self, bags: list[Observation], people: list[Observation]) -> set[int]:
        seen: set[int] = set()

        for observation in bags:
            seen.add(observation.track_id)
            self.missing.pop(observation.track_id, None)

            known = self.registry.get(observation.track_id)
            bag = self.registry.observe(observation, events=self.events)

            if known is None:
                carrier = _nearest(people, bag.anchor)
                if (
                    carrier is not None
                    and carrier.position.distance_to(bag.anchor)
                    <= self.config.pipeline.owner_search_radius_m
                ):
                    self.registry.assign_owner(
                        bag.bag_id, self.party_for(carrier.track_id), t=self.t, events=self.events
                    )

            self.last_people[bag.bag_id] = people

        return seen

    def relational_flags(self, people: list[Observation]) -> None:
        """Flags N1 e N2. Regra P4: relação pessoa-bagagem, nunca atributo."""
        for bag in self.registry.all():
            if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
                continue

            for person in people:
                if (
                    bag.owner_party is not None
                    and self.parties.party_of(person.track_id) == bag.owner_party
                ):
                    continue

                chave = (person.track_id, bag.bag_id)
                distancia = person.position.distance_to(bag.anchor)

                if distancia <= self.config.pipeline.contact_radius_m:
                    if chave not in self.contact_flagged:
                        self.contact_flagged.add(chave)
                        self.flags.add(
                            flag_contact(person.track_id, bag, self.t, self.config.flags)
                        )
                    continue

                if distancia > self.config.pipeline.proximity_radius_m:
                    self.near_since.pop(chave, None)
                    continue

                desde = self.near_since.setdefault(chave, self.t)
                decorrido = self.t - desde
                if (
                    decorrido >= self.config.pipeline.proximity_flag_s
                    and chave not in self.proximity_flagged
                ):
                    self.proximity_flagged.add(chave)
                    self.flags.add(
                        flag_proximity(person.track_id, bag, self.t, decorrido, self.config.flags)
                    )

    def resolve_removals(self, seen: set[int], people: list[Observation]) -> None:
        for bag in self.registry.all():
            if bag.state in TERMINAL_BAG_STATES:
                continue
            if bag.bag_id in seen:
                update_attendance(
                    bag, people, self.parties, self.t, self.config.custody, events=self.events
                )
                continue

            self.missing[bag.bag_id] = self.missing.get(bag.bag_id, 0) + 1
            if self.missing[bag.bag_id] < self.config.pipeline.missing_frames_before_removal:
                continue

            # Quem está em cena agora tem preferência: alguém que apareceu no
            # último frame da bagagem e já saiu não a levou. O último frame só
            # entra quando não há mais ninguém — é o caso do CAVIAR, cuja
            # anotação para no instante exato da retirada.
            candidatos = people or self.last_people.get(bag.bag_id, [])

            carrier = _nearest(candidatos, bag.anchor)
            if carrier is None:
                continue

            resolve_removal(bag, carrier.track_id, self.parties, t=self.t, events=self.events)
            flag = flag_for_removal(bag, carrier.track_id, self.t, self.config.flags)
            if flag is not None:
                self.flags.add(flag)


def run_session(
    frames: Iterator[tuple[float, list[TrackedDetection]]],
    plane: GroundPlane,
    config: Config | None = None,
) -> SessionResult:
    session = _Session(plane, config or Config())
    pipeline = session.config.pipeline

    for t, tracked in frames:
        session.frames += 1
        session.t = t

        observations = to_observations(tracked, plane, t)
        bags = [o for o in observations if o.cls in BAG_CLASSES]
        people = session.resolve_people(observations, tracked)

        # Fundir é O(n²) sobre pares próximos, com is_comoving por dentro.
        # A cada frame seria desperdício: grupos não se formam em 40 ms.
        if session.frames % pipeline.merge_every_frames == 0:
            session.merge_parties(people)

        seen = session.observe_bags(bags, people)
        session.relational_flags(people)
        session.resolve_removals(seen, people)

    return SessionResult(
        events=session.events,
        queue=build_queue(session.flags, session.t, session.config.alerts),
        frames=session.frames,
        duration_s=session.t,
        flags=session.flags,
        links=session.linker.links(),
    )


def removal_outcomes(result: SessionResult) -> dict[BagState, int]:
    """Contagem por desfecho de custódia, para relatório."""
    counts: dict[BagState, int] = {}
    for event in result.events:
        if event.kind is EventKind.BAG_REMOVED_BY_STRANGER:
            counts[BagState.RETIRADA_ESTRANHO] = counts.get(BagState.RETIRADA_ESTRANHO, 0) + 1
        elif event.kind is EventKind.BAG_REMOVED_BY_OWNER:
            counts[BagState.RETIRADA_DONO] = counts.get(BagState.RETIRADA_DONO, 0) + 1
    return counts


__all__ = ["Bag", "SessionResult", "removal_outcomes", "run_session"]
