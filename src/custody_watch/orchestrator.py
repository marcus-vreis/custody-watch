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
recolhida, então ausência sustentada é o único sinal disponível — mas decidir
no instante do sumiço é o defeito: quem está mais perto ali é, com a mesma
frequência, quem só passava na frente. Agora a ausência **adia** a decisão:
abre uma janela de oclusão que acumula quem esteve ao alcance da âncora
enquanto a bagagem esteve invisível, e é a saída da janela quem decide. Volta
a ser vista: nada aconteceu, `BAG_OCCLUDED` registra o intervalo. Estoura
`max_occlusion_s` primeiro: resolve com quem foi lembrado, ou suprime sob a
regra P3 se sobrou zero ou mais de um candidato.
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
from .tracking import PlausibilityGate, TrackedDetection, to_observations
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
        self.gate = PlausibilityGate(config.pipeline.max_observation_speed_ms)

        self.history: dict[int, list[Observation]] = {}
        self.missing: dict[int, int] = {}

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

    def adopt_occluded(self, observation: Observation) -> Bag | None:
        """Bagagem que sumiu e voltou com track novo readota a âncora ocluída.

        Caso estrito, e só ele: exatamente uma candidata no raio. Com mais de
        uma não há como saber qual é qual, e chutar corromperia o mapa de
        posse — então todas viram AMBIGUA, que é a regra P3.

        Não é o P2 completo. `observe()` segue indexando por `track_id`, e
        quarenta malas pretas idênticas continuam em aberto.
        """
        candidatas = self.registry.occluded_near(
            observation.position, self.config.registry.moved_threshold_m
        )
        if not candidatas:
            return None

        if len(candidatas) > 1:
            self.registry.mark_ambiguous_neighbours(
                candidatas[0].bag_id, t=self.t, events=self.events
            )
            return None

        bag = candidatas[0]
        self.registry.link_track(observation.track_id, bag.bag_id)
        self.events.emit(
            Event(
                kind=EventKind.TRACK_RELINKED,
                t_start=self.t,
                t_end=self.t,
                subject=None,
                bag=bag.bag_id,
                party=bag.owner_party,
                evidence={
                    "from_track": observation.track_id,
                    "occluded_s": self.t - bag.occluded_since,
                    "distance_m": bag.anchor.distance_to(observation.position),
                },
            )
        )
        return bag

    def end_occlusion(self, bag: Bag) -> None:
        """A bagagem voltou. Registra o intervalo e limpa o estado."""
        if bag.occluded_since is None:
            return

        self.events.emit(
            Event(
                kind=EventKind.BAG_OCCLUDED,
                t_start=bag.occluded_since,
                t_end=self.t,
                subject=None,
                bag=bag.bag_id,
                party=bag.owner_party,
                evidence={"candidates": sorted(bag.occlusion_candidates)},
            )
        )
        bag.occluded_since = None
        bag.occlusion_candidates.clear()

    def carry_away(self, bag: Bag, observation: Observation, people: list[Observation]) -> bool:
        """A âncora saiu do lugar. Isso é perda de custódia, ou não é?

        Devolve `True` quando a custódia foi resolvida — e nesse caso a âncora
        **não** é movida: ela marca onde a custódia foi perdida, e é esse ponto
        que o recorte de clipe mostra ao operador. Deixá-la seguir a bagagem
        faria o clipe apontar para onde o ladrão estava ao sair de cena.

        Bagagem andando com quem legitimamente a possui não é evento nenhum: é
        o caso comum, o dono chegando com a própria mala ou saindo com ela. A
        regra P1 já diz que posse flui por vínculo forte, então um membro forte
        movendo a bagagem é custódia continuando, não terminando.

        Tratar isso como retirada declarava a bagagem terminal meio segundo
        depois de ela aparecer — antes mesmo de ser depositada — e matava todo
        o resto da sessão.
        """
        if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
            # Custódia já decidida. Sem esta guarda, `flag_for_removal` roda a
            # cada frame enquanto a bagagem se afasta, e o score do alerta passa
            # a medir quantos quadros o furto durou em vez da gravidade dele.
            return True

        carrier = _nearest(people, observation.position)
        if (
            carrier is None
            or carrier.position.distance_to(observation.position)
            > self.config.pipeline.owner_search_radius_m
        ):
            # Bagagem que anda sozinha é fisicamente estranho. Chutar um dono
            # corromperia o mapa de posse, então P3: suprime.
            bag.state = BagState.AMBIGUA
            return True

        party = None if bag.is_orphan else self.parties.get(bag.owner_party)
        if party is not None and party.owns(carrier.track_id):
            return False

        resolve_removal(bag, carrier.track_id, self.parties, t=self.t, events=self.events)
        flag = flag_for_removal(bag, carrier.track_id, self.t, self.config.flags)
        if flag is not None:
            self.flags.add(flag)
        return True

    def observe_bags(self, bags: list[Observation], people: list[Observation]) -> set[int]:
        seen: set[int] = set()

        for observation in bags:
            known = self.registry.get_by_track(observation.track_id)
            if known is None:
                known = self.adopt_occluded(observation)

            # A bagagem andou. Resolver ANTES de observe(), que moveria a
            # âncora junto com quem a está levando. Quando `carry_away` devolve
            # False não houve perda de custódia -- é o dono movendo a própria
            # bagagem -- e a âncora acompanha normalmente.
            if (
                known is not None
                and self.registry.has_moved(observation)
                and self.carry_away(known, observation, people)
            ):
                seen.add(known.bag_id)
                continue

            bag = self.registry.observe(observation, events=self.events)
            seen.add(bag.bag_id)
            self.missing.pop(bag.bag_id, None)
            self.end_occlusion(bag)

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
            if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
                continue

            # A attendance roda também para bagagem invisível, contra a âncora
            # congelada: a posição dela é conhecida e as pessoas também. Sem
            # isso, ocluir em ciclos congela o cronômetro e o limiar de 25s
            # nunca completa.
            update_attendance(
                bag, people, self.parties, self.t, self.config.custody, events=self.events
            )

            if bag.bag_id in seen:
                continue

            self.missing[bag.bag_id] = self.missing.get(bag.bag_id, 0) + 1
            if self.missing[bag.bag_id] < self.config.pipeline.missing_frames_before_occluded:
                continue

            if bag.occluded_since is None:
                bag.occluded_since = self.t

            # Quem esteve ao alcance da âncora DURANTE a ausência. Decidir no
            # instante do sumiço é o defeito: ali a informação que separa
            # passante de ladrão ainda não existe.
            bag.occlusion_candidates.update(
                pessoa.track_id
                for pessoa in people
                if pessoa.position.distance_to(bag.anchor)
                <= self.config.pipeline.owner_search_radius_m
            )

            if self.t - bag.occluded_since < self.config.custody.max_occlusion_s:
                continue

            self.resolve_occlusion_timeout(bag)

    def resolve_occlusion_timeout(self, bag: Bag) -> None:
        """A bagagem não voltou. Agora sim resolve, com quem esteve nela.

        Zero candidatos: sumiu sem ninguém ao alcance. Vários: não há como
        escolher. Nos dois casos P3 manda suprimir, não gerar.
        """
        if len(bag.occlusion_candidates) != 1:
            bag.state = BagState.AMBIGUA
            self.events.emit(
                Event(
                    kind=EventKind.BAG_AMBIGUOUS,
                    t_start=bag.occluded_since,
                    t_end=self.t,
                    subject=None,
                    bag=bag.bag_id,
                    party=bag.owner_party,
                    evidence={"candidates": sorted(bag.occlusion_candidates)},
                )
            )
            return

        carrier = next(iter(bag.occlusion_candidates))
        resolve_removal(bag, carrier, self.parties, t=self.t, events=self.events)
        flag = flag_for_removal(bag, carrier, self.t, self.config.flags)
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

        # O portao roda antes de tudo: uma observacao que implica velocidade
        # impossivel e artefato de projecao, e alimenta a medida de extensao
        # como se fosse deslocamento real.
        observations = session.gate.filter(to_observations(tracked, plane, t))
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
