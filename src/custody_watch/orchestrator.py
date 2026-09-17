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

## O registro é de âncoras, e posse é o depósito

Bagagem só entra no registro depois de ficar parada. A regra P2 diz que a
posição é a identidade porque bagagem parada não teleporta — mala em trânsito
não tem essa propriedade, e tratá-la como âncora inventa uma custódia que
nunca existiu, com dono atribuído por proximidade a quem por acaso estava do
lado. Medido no primeiro vídeo real: o dono foi acusado de furtar a própria
mala dois segundos depois de entrar em quadro, e terminou em primeiro na fila.

Daí a posse ser o **depósito**: quem estava junto quando a bagagem parou. E
ela continua sendo tentada enquanto a bagagem for órfã, porque o dono pode
simplesmente não ter sido detectado no quadro do depósito — 5% de falha de
detecção custavam 40% das posses. A janela é o que impede a retentativa de
reabrir o ataque que a P1 fecha: passado o prazo, quem se aproxima de uma
bagagem sem dono não herda a posse dela.

O preço declarado: furto de arrancada, com a bagagem em trânsito na mão do
dono, fica fora. O modelo inteiro é de âncora e depósito, e uma bagagem que
nunca parou nunca teve custódia que se possa perder.

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
from .types import BAG_CLASSES, TERMINAL_BAG_STATES, Bag, BagState, Observation, Point


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
    bag_links: dict[int, int] = field(default_factory=dict)
    """Mesma ideia de `links`, para bagagem: mapa de track bruto para
    `bag_id` canônico. Depois de uma readoção sob oclusão, os frames do vídeo
    trazem o track novo, mas os eventos e a fila citam o `bag_id` antigo -- o
    recorte de clipe precisa do inverso para destacar a caixa certa."""

    def raw_ids(self, canonical_id: int) -> set[int]:
        brutos = {raw for raw, canon in self.links.items() if canon == canonical_id}
        brutos.add(canonical_id)
        return brutos

    def raw_bag_ids(self, canonical_bag_id: int) -> set[int]:
        brutos = {raw for raw, canon in self.bag_links.items() if canon == canonical_bag_id}
        brutos.add(canonical_bag_id)
        return brutos


def _nearest(people: Iterable[Observation], target) -> Observation | None:
    candidates = list(people)
    if not candidates:
        return None
    return min(candidates, key=lambda p: p.position.distance_to(target))


@dataclass
class _Pendente:
    """Detecção de bagagem que ainda não virou âncora.

    Pessoa assenta na re-ID acumulando amostras de aparência; bagagem assenta
    ficando parada. Nos dois casos o pipeline espera antes de deixar a
    observação decidir alguma coisa — e nos dois casos a espera existe porque
    a informação que separa ruído de evento ainda não chegou.
    """

    since: float
    last_t: float
    position: Point
    depositor: int | None = None
    """Quem estava ao alcance quando a bagagem chegou neste lugar.

    Guardado aqui, e não decidido no instante em que a bagagem vira âncora,
    porque quem deposita **sai andando**: é a definição do abandono. Medido no
    CAVIAR, decidir a posse dois segundos depois já encontra o dono a mais de
    três metros, a bagagem nasce órfã, e o único abandono anotado do dataset
    deixa de ser detectado.
    """


class _Ambiguous:
    """Sentinela devolvida por `adopt_occluded` quando a readoção recusa por
    ambiguidade: mais de uma candidata no raio, nenhuma pode ser escolhida.

    Distinta de `None` (nenhuma candidata, registrar como bagagem nova) de
    propósito -- confundir os dois casos é a Finding 3: uma observação
    recusada por ambígua registrada como bagagem nova cria uma bagagem
    fantasma no exato lugar que acabou de ser declarado incerto.
    """

    def __repr__(self) -> str:
        return "AMBIGUOUS"


AMBIGUOUS = _Ambiguous()


class _NaoAncora:
    """Devolvido por `anchoring` enquanto a bagagem não parou.

    Distinto de `None`, que ali significa "parou, e ninguém estava ao alcance
    para responder pelo depósito" -- confundir os dois registraria bagagem em
    trânsito como âncora órfã.
    """

    def __repr__(self) -> str:
        return "NAO_ANCORA"


_NAO_ANCORA = _NaoAncora()


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
        self.pending_bags: dict[int, _Pendente] = {}
        """Bagagens vistas mas ainda não paradas. Ver `anchoring`."""
        self.ambiguous_tracks: set[int] = set()
        """Tracks de bagagem cuja identidade a readoção não conseguiu resolver.

        A recusa vale para o track, não para o quadro nem para o lugar. Marcar
        as vizinhas como AMBIGUA tira as duas do alcance de `occluded_near`,
        então o mesmo track voltaria no quadro seguinte, não encontraria
        candidata nenhuma, e se registraria como bagagem nova — uma bagagem
        fantasma no exato ponto que acabou de ser declarado incerto, pronta
        para receber posse por proximidade e virar acusação.

        Um track novo no mesmo lugar mais tarde é outra pergunta, e passa
        normalmente: a recusa não envenena a região.
        """

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

    def adopt_occluded(self, observation: Observation) -> Bag | _Ambiguous | None:
        """Bagagem que sumiu e voltou com track novo readota a âncora ocluída.

        Caso estrito, e só ele: exatamente uma candidata no raio. Com mais de
        uma não há como saber qual é qual, e chutar corromperia o mapa de
        posse — então todas viram AMBIGUA, que é a regra P3.

        Três desfechos, não dois: `None` quando não há candidata nenhuma, e o
        chamador deve registrar a observação como bagagem nova; o sentinela
        `AMBIGUOUS` quando há mais de uma, e o chamador deve descartar a
        observação inteira -- registrá-la como nova cria uma bagagem fantasma
        no lugar que acabou de ser declarado incerto; e a própria `Bag`
        quando a readoção teve sucesso.

        O raio é próprio, e não o `moved_threshold_m`. São perguntas
        diferentes: aquele é sobre ruído de detector, "a bagagem se moveu?";
        este é sobre quanto uma bagagem pode ter sido empurrada e ainda ser a
        mesma. Com um número só, uma bagagem que voltava a 0,6m da âncora se
        registrava como bagagem nova enquanto a original seguia ocluída rumo
        ao timeout.

        Não é o P2 completo. `observe()` segue indexando por `track_id`, e
        quarenta malas pretas idênticas continuam em aberto.
        """
        candidatas = self.registry.occluded_near(
            observation.position, self.config.registry.reidentification_radius_m
        )
        if not candidatas:
            return None

        if len(candidatas) > 1:
            self.registry.mark_ambiguous_neighbours(
                candidatas[0].bag_id, t=self.t, events=self.events
            )
            return AMBIGUOUS

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
        """A bagagem voltou. Registra o intervalo e limpa o estado.

        Oclusão curta demais não entra no log. O evento existe para dizer ao
        operador em que intervalo a custódia ficou sem observação, e um
        detector oscilante produz um episódio por oscilação: medido, 77
        eventos em 90s sobre uma bagagem parada. Abaixo do piso o evento
        descreve o detector, não a cena -- a rajada de falha medida no CAVIAR
        tem mediana de 12 quadros, cerca de meio segundo.

        O estado é limpo nos dois casos. O piso decide o que vai para o log,
        nunca o que a lógica enxerga.
        """
        if bag.occluded_since is None:
            return

        if self.t - bag.occluded_since >= self.config.custody.min_occlusion_report_s:
            self.events.emit(
                Event(
                    kind=EventKind.BAG_OCCLUDED,
                    t_start=bag.occluded_since,
                    t_end=self.t,
                    subject=None,
                    bag=bag.bag_id,
                    party=bag.owner_party,
                    evidence={
                        "candidate_people": sorted(bag.occlusion_candidates),
                        "neighbour_bags": [],
                    },
                )
            )
        bag.occluded_since = None
        bag.occlusion_candidates.clear()

    def carry_away(self, bag: Bag, observation: Observation, people: list[Observation]) -> bool:
        """A âncora saiu do lugar. Isso é perda de custódia, ou não é?

        Devolve `True` quando a observação já foi tratada e a âncora **não**
        deve ser movida — seja porque a custódia foi resolvida, seja porque
        ainda se está esperando para decidir.

        Bagagem andando com quem legitimamente a possui não é evento nenhum: é
        o caso comum, o dono chegando com a própria mala ou saindo com ela. A
        regra P1 já diz que posse flui por vínculo forte, então um membro forte
        movendo a bagagem é custódia continuando, não terminando.

        Para todo o resto, o deslocamento precisa se sustentar por
        `carry_confirm_s` antes de valer. Um quadro só não é bagagem sendo
        levada — é ruído de projeção, e decidir nele é o mesmo defeito que o
        caminho de desaparecimento já fechou.
        """
        if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
            # Custódia já decidida. Sem esta guarda, `flag_for_removal` roda a
            # cada quadro enquanto a bagagem se afasta, e o score do alerta
            # passa a medir quantos quadros o furto durou, não a gravidade.
            return True

        carrier = _nearest(people, observation.position)
        perto = (
            carrier is not None
            and carrier.position.distance_to(observation.position)
            <= self.config.pipeline.owner_search_radius_m
        )

        if perto:
            party = None if bag.is_orphan else self.parties.get(bag.owner_party)
            if party is not None and party.owns(carrier.track_id):
                bag.moved_since = None
                return False

        if bag.moved_since is None:
            bag.moved_since = self.t
        if self.t - bag.moved_since < self.config.custody.carry_confirm_s:
            return True

        deslocamento = bag.anchor.distance_to(observation.position)

        if not perto:
            # Bagagem que anda sozinha é mais provavelmente artefato de
            # projeção que bagagem levitando. P3 manda suprimir o alerta — mas
            # em silêncio ninguém consegue reconstruir por que a bagagem
            # morreu, então o evento sai com os números que decidiram.
            bag.state = BagState.AMBIGUA
            self.events.emit(
                Event(
                    kind=EventKind.BAG_AMBIGUOUS,
                    t_start=bag.moved_since,
                    t_end=self.t,
                    subject=None,
                    bag=bag.bag_id,
                    party=bag.owner_party,
                    evidence={
                        "moved_m": deslocamento,
                        "nearest_person_m": (
                            None
                            if carrier is None
                            else carrier.position.distance_to(observation.position)
                        ),
                        "anchor": [bag.anchor.x, bag.anchor.y],
                        "candidate_people": [],
                        "neighbour_bags": [],
                    },
                )
            )
            return True

        resolve_removal(bag, carrier.track_id, self.parties, t=self.t, events=self.events)
        flag = flag_for_removal(bag, carrier.track_id, self.t, self.config.flags)
        if flag is not None:
            self.flags.add(flag)
        return True

    def _remember_depositor(self, pendente: _Pendente, people: list[Observation]) -> None:
        """O primeiro que aparecer ao alcance responde pelo depósito.

        O primeiro, e não o último: a cada quadro que passa o dono está mais
        longe e um estranho qualquer está mais perto, então esperar só piora a
        evidência.
        """
        if pendente.depositor is not None:
            return

        carrier = _nearest(people, pendente.position)
        if (
            carrier is not None
            and carrier.position.distance_to(pendente.position)
            <= self.config.pipeline.owner_search_radius_m
        ):
            pendente.depositor = carrier.track_id

    def anchoring(self, observation: Observation, people: list[Observation]) -> int | None:
        """A bagagem ficou parada o bastante para virar âncora?

        Não é só uma espera: enquanto a observação não se sustenta no mesmo
        lugar, ela não é bagagem depositada — é mala na mão de alguém. Um
        registro aberto ali produz uma custódia que nunca existiu, e a perda
        dela vira acusação contra quem estava carregando.

        A contagem reinicia a cada deslocamento acima do limiar. Bagagem
        puxada a meio metro por segundo reinicia a cada segundo e nunca
        completa; bagagem no chão completa e entra.

        Devolve `_NAO_ANCORA` enquanto não parou, e quem depositou quando
        parar -- `None` ali significa que ninguém estava ao alcance, e a
        bagagem nasce órfã.
        """
        pendente = self.pending_bags.get(observation.track_id)
        limiar = self.config.registry.moved_threshold_m

        if pendente is None or pendente.position.distance_to(observation.position) > limiar:
            pendente = _Pendente(since=self.t, last_t=self.t, position=observation.position)
            self.pending_bags[observation.track_id] = pendente
            self._remember_depositor(pendente, people)
            return _NAO_ANCORA

        pendente.last_t = self.t
        self._remember_depositor(pendente, people)
        if self.t - pendente.since < self.config.custody.rest_confirm_s:
            return _NAO_ANCORA

        del self.pending_bags[observation.track_id]
        return pendente.depositor

    def forget_stale_pending(self) -> None:
        """Candidata que sumiu nunca vai parar.

        Sem a poda, o mapa acumula uma entrada por track de bagagem que a cena
        já produziu — e track de bagagem é justamente o que um detector
        oscilante fabrica às dezenas.
        """
        corte = self.t - self.config.custody.rest_confirm_s
        self.pending_bags = {
            track: pendente
            for track, pendente in self.pending_bags.items()
            if pendente.last_t >= corte
        }

    def identify(self, observation: Observation, people: list[Observation]) -> Bag | None:
        """A qual bagagem registrada esta observação pertence, se a alguma.

        Devolve `None` quando a observação não deve produzir efeito nenhum:
        track já declarado ambíguo, readoção recusada por ambiguidade, ou
        bagagem que ainda não parou.
        """
        if observation.track_id in self.ambiguous_tracks:
            return None

        known = self.registry.get_by_track(observation.track_id)
        if known is not None:
            return known

        adotada = self.adopt_occluded(observation)
        if adotada is AMBIGUOUS:
            # A observação é descartada, e o track fica descartado daqui em
            # diante: registrá-lo como bagagem nova criaria uma bagagem
            # fantasma exatamente onde a ambiguidade acabou de ser declarada.
            self.ambiguous_tracks.add(observation.track_id)
            return None
        if adotada is not None:
            return adotada

        depositante = self.anchoring(observation, people)
        if depositante is _NAO_ANCORA:
            return None

        bag = self.registry.observe(observation, events=self.events)
        if depositante is not None:
            self.registry.assign_owner(
                bag.bag_id, self.party_for(depositante), t=self.t, events=self.events
            )
        return bag

    def claim_ownership(self, bag: Bag, people: list[Observation]) -> None:
        """Posse é o depósito: quem estava junto quando a bagagem parou.

        É a rede embaixo do depósito: a posse já sai de `anchoring` quando
        alguém estava ao alcance enquanto a bagagem chegava. Isto aqui cobre o
        caso em que ninguém estava — detector perdendo o dono durante a
        janela inteira, que com 39% de ausência medida não é raro.

        Tentada a cada quadro enquanto a bagagem for órfã, e não uma vez só.
        Uma bagagem sem dono é uma pergunta em aberto, não um fato
        estabelecido.

        A janela é o que impede a retentativa de reabrir o ataque que a regra
        P1 fecha: passado o prazo desde o depósito, quem se aproxima de uma
        bagagem sem dono não herda a posse dela.
        """
        if not bag.is_orphan:
            return
        if self.t - bag.anchored_at > self.config.custody.ownership_window_s:
            return

        carrier = _nearest(people, bag.anchor)
        if (
            carrier is None
            or carrier.position.distance_to(bag.anchor) > self.config.pipeline.owner_search_radius_m
        ):
            return

        self.registry.assign_owner(
            bag.bag_id, self.party_for(carrier.track_id), t=self.t, events=self.events
        )

    def observe_bags(self, bags: list[Observation], people: list[Observation]) -> set[int]:
        seen: set[int] = set()

        for observation in bags:
            conhecida = self.identify(observation, people)
            if conhecida is None:
                continue

            # A bagagem andou. Resolver ANTES de observe(), que moveria a
            # âncora junto com quem a está levando. Quando `carry_away` devolve
            # False não houve perda de custódia -- é o dono movendo a própria
            # bagagem -- e a âncora acompanha normalmente.
            if self.registry.has_moved(observation) and self.carry_away(
                conhecida, observation, people
            ):
                seen.add(conhecida.bag_id)
                continue

            bag = self.registry.observe(observation, events=self.events)
            seen.add(bag.bag_id)
            self.missing.pop(bag.bag_id, None)
            self.end_occlusion(bag)
            bag.moved_since = None
            self.claim_ownership(bag, people)

        self.forget_stale_pending()
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

        `candidate_people` são PESSOAS, e `neighbour_bags` são BAGAGENS. As
        duas chaves saem sempre, das três origens de `BAG_AMBIGUOUS`, porque
        um `kind` com duas formas de evidência obriga o consumidor a ramificar
        pela forma do dicionário -- e ler a chave errada devolvia uma lista de
        inteiros perfeitamente plausível e sem sentido nenhum.

        Limpa `occluded_since`/`occlusion_candidates` nos dois ramos, do
        mesmo jeito que `end_occlusion` faz quando a bagagem volta a ser
        vista. Sem isso, se a bagagem reaparecer mais tarde -- com o mesmo
        track ou religada por `adopt_occluded` -- `observe_bags` acha o campo
        ainda preenchido e `end_occlusion` dispara de novo, emitindo um
        segundo evento sobre um intervalo que este método já resolveu.
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
                    evidence={
                        "candidate_people": sorted(bag.occlusion_candidates),
                        "neighbour_bags": [],
                    },
                )
            )
            bag.occluded_since = None
            bag.occlusion_candidates.clear()
            return

        carrier = next(iter(bag.occlusion_candidates))
        resolve_removal(bag, carrier, self.parties, t=self.t, events=self.events)
        flag = flag_for_removal(bag, carrier, self.t, self.config.flags)
        if flag is not None:
            self.flags.add(flag)
        bag.occluded_since = None
        bag.occlusion_candidates.clear()


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
        bag_links=session.registry.links(),
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
