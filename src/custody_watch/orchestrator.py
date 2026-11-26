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

import threading
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from itertools import combinations
from typing import NamedTuple

from .alerts import AlertItem, build_queue
from .bag_registry import BagRegistry
from .config import Config
from .custody import resolve_removal, update_attendance
from .events import Event, EventKind, EventLog
from .flags import FlagStore, flag_contact, flag_for_removal, flag_proximity
from .ground_plane import GroundPlane
from .party import TIME_EPSILON_S, PartyManager, cobre, excede, is_comoving
from .reid import TrackLinker
from .terminal_bag_archive import TerminalBagArchive
from .tracking import PlausibilityGate, TrackedDetection, anchor_vetoes, to_observations
from .types import (
    BAG_CLASSES,
    PERSON_CLASS,
    TERMINAL_BAG_STATES,
    Bag,
    BagState,
    FlagLevel,
    Observation,
    Point,
)

MAX_APONTADAS = 200
"""Teto de bagagens apontadas pelo operador. Um cliente em laço criaria
âncoras até o processo cair, e nenhuma sala de monitoramento aponta duzentas
bagagens numa sessão."""

BAG_APONTADA = "suitcase"
"""Classe da observação que o operador cria. O registro não guarda classe --
bagagem é bagagem -- mas a observação precisa de uma que seja de bagagem para
ser coerente com o resto do pipeline."""

MIN_MISSING_FRAMES = 2
"""Quadros seguidos sem observação abaixo dos quais nenhuma ausência é oclusão.

Vale junto com `pipeline.missing_before_occluded_s`, nunca no lugar dele.
Falha do detector é evento por quadro, não por tempo: a 3 fps uma falha
isolada já dura 0,33 s, mais que o limiar em segundos, e sem o piso todo
tropeço de uma fonte lenta abria um intervalo de oclusão -- a lista de
candidatos acumulava quem só passou por perto até a atribuição não apontar
mais ninguém em particular. A duração física se mede em segundos; a
repetição da falha, em quadros. A 25 fps o piso não decide nada: os 0,18 s
já pedem cinco quadros. Fica no código, e não na config, porque abaixo de
dois o tropeço volta, e acima disso quem se ajusta é o limiar em segundos."""


class _Ausencia(NamedTuple):
    """Quanto algo ficou sem ser visto, nas duas réguas da oclusão."""

    segundos: float
    quadros: int


_SEM_AUSENCIA = _Ausencia(0.0, 0)


def _ausencia_ate(visto: _Visto, t: float, quadro: int) -> _Ausencia:
    """Ausência de um track visto em `visto` até o quadro `quadro`, no
    instante `t`, contado como o último quadro em que ele faltou."""
    return _Ausencia(t - visto.t, quadro - visto.quadro)


class _Visto(NamedTuple):
    """Instante e quadro da última observação de um track."""

    t: float
    quadro: int


def _quem_esta_em(tracked: Iterable[TrackedDetection], px: float, py: float) -> int | None:
    """Quem o operador clicou. A menor caixa que contém o ponto: com duas
    pessoas sobrepostas, a de trás contém a da frente, e a resposta certa é a
    que o operador enxergou -- a menor."""
    candidatos = [
        d
        for d in tracked
        if d.cls == PERSON_CLASS and d.bbox[0] <= px <= d.bbox[2] and d.bbox[1] <= py <= d.bbox[3]
    ]
    if not candidatos:
        return None
    menor = min(candidatos, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
    return menor.track_id


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


def _centro(posicoes: list[Point]) -> Point:
    """Média de posições. Metros, como tudo deste lado do plano do chão."""
    return Point(
        sum(p.x for p in posicoes) / len(posicoes),
        sum(p.y for p in posicoes) / len(posicoes),
    )


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

    amostras: list[tuple[float, Point]]
    """Janela deslizante das posições observadas, com o instante de cada uma.

    Deslizante e não reiniciável: uma excursão de ruído não pode zerar a
    espera, ou bagagem parada sob detector ruidoso nunca assenta.
    """
    depositor: int | None = None
    """Quem estava ao alcance quando a bagagem chegou neste lugar.

    Guardado aqui, e não decidido no instante em que a bagagem vira âncora,
    porque quem deposita **sai andando**: é a definição do abandono. Medido no
    CAVIAR, decidir a posse dois segundos depois já encontra o dono a mais de
    três metros, a bagagem nasce órfã, e o único abandono anotado do dataset
    deixa de ser detectado.
    """

    def media(self) -> Point:
        """Onde a bagagem está, estimado por todas as amostras da janela.

        Uma amostra carrega o erro de posição inteiro do detector; a média de
        n carrega ele dividido por raiz de n.
        """
        return _centro([posicao for _, posicao in self.amostras])

    def desliza(self, t: float, posicao: Point, janela: float) -> None:
        """Acrescenta a amostra de `t` e descarta as que a janela não usa mais.

        Fica a amostra mais nova que já está a `janela` de `t`, e todas as
        depois dela: é ela que mostra que a janela foi coberta. Cortar em
        `>= t - janela` só a mantinha quando caía exatamente no corte -- com
        `janela * fps` inteiro, e se a subtração em ponto flutuante desse
        exata. A 3,25 fps as amostras cobriam no máximo 6 / 3,25 = 1,85 s dos
        2 s, `parada` nunca via a janela cheia e nenhuma bagagem virava âncora.
        Assim a decisão sai no primeiro quadro em que o repouso vence a
        janela, como as demais réguas em segundos.
        """
        self.amostras.append((t, posicao))
        cobrem = [i for i, (ti, _) in enumerate(self.amostras) if cobre(t - ti, janela)]
        inicio = cobrem[-1] if cobrem else 0
        self.amostras = self.amostras[inicio:]

    def parada(self, janela: float, limiar: float) -> bool:
        """As duas metades da janela concordam sobre onde a bagagem está?

        É esta a pergunta, e não "a última amostra está perto da primeira".
        Comparar amostras individuais mede o **ruído**: medido na varredura de
        envelope, 4px de erro de posição de bagagem -- o dobro do que se mediu
        em pessoa no CAVIAR -- deixavam o sistema mudo, porque cada excursão
        do ruído zerava a espera e nenhuma bagagem chegava a assentar.

        Comparar médias de metades divide o ruído por raiz de n e preserva o
        que interessa: bagagem em trânsito tem metades que discordam na
        direção do movimento, e nenhuma quantidade de amostras conserta isso.

        As duas metades estão separadas por meia janela, então dobrar a
        diferença entre elas estima o deslocamento ao longo da janela
        inteira -- que é a grandeza que `moved_threshold_m` mede. O dobro não
        é fator de ajuste: sem ele o teste ficaria duas vezes mais permissivo
        com bagagem em movimento do que o limiar diz ser.

        O corte efetivo é de velocidade: `v <= limiar/janela`, um quarto de
        metro por segundo com os defaults.
        """
        if not self.amostras or not cobre(self.amostras[-1][0] - self.amostras[0][0], janela):
            return False

        meio = self.amostras[0][0] + janela / 2.0
        primeira = [posicao for t, posicao in self.amostras if t < meio]
        segunda = [posicao for t, posicao in self.amostras if t >= meio]
        if not primeira or not segunda:
            return False

        deslocamento = 2.0 * _centro(primeira).distance_to(_centro(segunda))
        return deslocamento <= limiar


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

    def __init__(
        self,
        plane: GroundPlane,
        config: Config,
        events: EventLog | None = None,
        *,
        terminal_archive: TerminalBagArchive | None = None,
    ) -> None:
        self.plane = plane
        self.config = config

        self.events = events if events is not None else EventLog()
        self.flags = FlagStore()
        self.parties = PartyManager(config.party)
        self.registry = BagRegistry(config.registry, terminal_archive=terminal_archive)
        self.linker = TrackLinker(config.reid)
        self.gate = PlausibilityGate(config.pipeline.max_observation_speed_ms)

        self.history: dict[int, list[Observation]] = {}
        self.missing: dict[int, _Ausencia] = {}
        """Ausência de cada bagagem que falta, nas duas réguas. A cada quadro
        em que ela falta soma-se um quadro e o intervalo desde o quadro
        anterior: conta os mesmos quadros que o antigo contador contava, e o
        que cada um durou. Sai quando a bagagem volta a ser vista na âncora."""
        self.pending_bags: dict[int, _Pendente] = {}
        """Bagagens vistas mas ainda não paradas. Ver `anchoring`."""
        self.vetoes: dict[int, str] = {}
        """Tracks de bagagem que a imagem do quadro atual diz que não podem
        virar âncora, e por quê. Ver `tracking.anchor_vetoes`."""
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
        self.bag_track_last_seen: dict[int, _Visto] = {}
        """Instante e quadro em que cada track bruto de bagagem foi observado
        pela última vez.

        Diz se um track está voltando de uma ausência -- é só nesse caso que a
        posição pode desmentir o id (ver `_confere_ancora`). Guarda apenas os
        tracks cuja ausência ainda não venceu as duas réguas
        (`missing_before_occluded_s` e `MIN_MISSING_FRAMES`): ausente daqui
        significa "voltando", então podar não perde nada e o mapa não cresce
        com a sessão.
        """

        # Marcações para não repetir o mesmo flag a cada frame.
        self.near_since: dict[tuple[int, int], float] = {}
        self.proximity_flagged: set[tuple[int, int]] = set()
        self.contact_flagged: set[tuple[int, int]] = set()
        self.together_since: dict[tuple[int, int], float] = {}

        self.frames = 0
        self.t = 0.0
        self.previous_t = 0.0
        """Instante do quadro anterior. Ausência se mede até o último quadro
        em que a coisa faltou, então é ele que dá a duração de cada falta."""
        self.last_merge_t = 0.0
        """Instante da última fusão de grupos, ou do primeiro quadro enquanto
        não houve nenhuma. É dele que `pipeline.merge_every_s` conta."""

    def merge_due(self) -> bool:
        """A fusão vence no primeiro quadro a `merge_every_s` da anterior.

        Cadência em segundos, não em quadros: 25 quadros eram 1 s a 25 fps e
        8,3 s a 3 fps, e a mesma cena formava o grupo -- e decidia quem é dono
        -- em tempos reais diferentes conforme a fonte. Conta da fusão que de
        fato rodou, então o intervalo real fica entre a cadência e a cadência
        mais um quadro, em qualquer fps.
        """
        if self.frames == 1:
            self.last_merge_t = self.t
        if not cobre(self.t - self.last_merge_t, self.config.pipeline.merge_every_s):
            return False
        self.last_merge_t = self.t
        return True

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

    def esquece_proximidade_interrompida(self) -> None:
        """Cronômetro de proximidade só conta tempo em que a pessoa foi vista.

        Roda antes de `resolve_people` registrar o quadro atual, então o
        último instante em `history` é o anterior a este. A tolerância é
        `party.max_gap_s`, a lacuna que `is_comoving` já aceita como
        observação contínua: abaixo dela é rajada de detector e o cronômetro
        segue; acima, a chave sai. Sem isso, quem esteve perto, sumiu de
        quadro e voltou recebia N1 ou vínculo fraco no ato, com o tempo fora
        de quadro contado como permanência -- e as chaves de quem nunca
        voltava ficavam para sempre.
        """
        tolerancia = self.config.party.max_gap_s + TIME_EPSILON_S

        def sumiu(pessoa: int) -> bool:
            janela = self.history.get(pessoa)
            return not janela or self.t - janela[-1].t > tolerancia

        for chave in [c for c in self.near_since if sumiu(c[0])]:
            del self.near_since[chave]
        for chave in [c for c in self.together_since if sumiu(c[0]) or sumiu(c[1])]:
            del self.together_since[chave]

    # --- grupos ---------------------------------------------------------

    def merge_parties(self, people: list[Observation]) -> None:
        """Funde quem se provar co-movendo, e não trata como dono quem só ficou perto."""
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
        """Proximidade estática prolongada. Não transfere posse nem isenta do flag."""
        chave = (min(a.track_id, b.track_id), max(a.track_id, b.track_id))
        desde = self.together_since.setdefault(chave, self.t)
        if not cobre(self.t - desde, self.config.party.weak_bond_s):
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

        Track já conhecido que volta de uma ausência fora da própria âncora
        passa pela mesma pergunta em `_confere_ancora`.
        """
        candidatas = self.registry.occluded_near(
            observation.position, self.config.registry.reidentification_radius_m
        )
        if not candidatas:
            return None

        if len(candidatas) > 1:
            self.registry.mark_ambiguous_candidates(
                [bag.bag_id for bag in candidatas], t=self.t, events=self.events
            )
            return AMBIGUOUS

        return self._readota(observation, candidatas[0])

    def _readota(self, observation: Observation, bag: Bag, anterior: Bag | None = None) -> Bag:
        """Faz o track responder por `bag` e registra a religação.

        `anterior` é a bagagem pela qual o track respondia até aqui, quando
        havia uma: sai na evidência para que a troca seja reconstruível.
        """
        self.registry.link_track(observation.track_id, bag.bag_id)
        evidence: dict = {
            "from_track": observation.track_id,
            "occluded_s": self.t - bag.occluded_since,
            "distance_m": bag.anchor.distance_to(observation.position),
        }
        if anterior is not None:
            evidence["previous_bag"] = anterior.bag_id
        self.events.emit(
            Event(
                kind=EventKind.TRACK_RELINKED,
                t_start=self.t,
                t_end=self.t,
                subject=None,
                bag=bag.bag_id,
                party=bag.owner_party,
                evidence=evidence,
            )
        )
        return bag

    def _voltando_de_ausencia(self, track_id: int) -> bool:
        """O track ficou fora de quadro tempo bastante para ter sido ocluído?

        Mesmas réguas da bagagem (`_ausencia_basta`), medidas até o último
        quadro em que o track faltou -- o anterior a este, em que ele volta.
        Uma falha curta do detector, ou de um quadro só numa fonte lenta, não é
        ausência, e não pode abrir a troca de identidade.
        """
        visto = self.bag_track_last_seen.get(track_id)
        if visto is None:
            return True
        return self._ausencia_basta(_ausencia_ate(visto, self.previous_t, self.frames - 1))

    def _ausencia_basta(self, ausencia: _Ausencia) -> bool:
        """A ausência já é oclusão? As duas réguas, juntas: a duração física
        em segundos e a falha repetida em quadros (`MIN_MISSING_FRAMES`)."""
        if ausencia.quadros < MIN_MISSING_FRAMES:
            return False
        return cobre(ausencia.segundos, self.config.pipeline.missing_before_occluded_s)

    def _confere_ancora(self, observation: Observation, known: Bag) -> Bag | None:
        """Regra P2 para track já conhecido: a posição desmente o id?

        Duas malas iguais somem juntas e voltam cada uma na própria âncora,
        com os ids trocados -- aparência nenhuma impede isso. Seguir o id lia
        as duas como deslocadas, e cada dono que levasse a sua era acusado de
        levar a do outro. Então, quando um track volta de uma ausência fora da
        própria âncora e sobre a de outra bagagem ocluída, ele passa a
        responder por ela, como faria um track novo em `adopt_occluded`.

        Só para track que volta de ausência: mala levada em plena vista que
        cruza a âncora de uma vizinha ocluída continua sendo ela mesma -- é
        `carry_away` quem decide. E só para custódia aberta: id terminal não
        troca de bagagem.

        Mais de uma âncora ocluída no raio de readoção (a própria inclusive):
        P3, todas AMBIGUA e o track descartado, como em `adopt_occluded`.
        Devolve `None` nesse caso; senão, a bagagem que responde pelo track.
        """
        if known.state in TERMINAL_BAG_STATES:
            return known
        if not self._voltando_de_ausencia(observation.track_id):
            return known
        if not self.registry.has_moved(observation):
            return known

        candidatas = self.registry.occluded_near(
            observation.position, self.config.registry.reidentification_radius_m
        )
        outras = [bag for bag in candidatas if bag.bag_id != known.bag_id]
        if not outras:
            return known

        if len(candidatas) > 1:
            self.registry.mark_ambiguous_candidates(
                [bag.bag_id for bag in candidatas], t=self.t, events=self.events
            )
            self.ambiguous_tracks.add(observation.track_id)
            return None

        return self._readota(observation, outras[0], anterior=known)

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

        if cobre(self.t - bag.occluded_since, self.config.custody.min_occlusion_report_s):
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

        Membro forte ao alcance da posição ATUAL da bagagem basta, mesmo sem
        ser o mais próximo: numa fila, o dono puxa a própria mala e quem vem
        atrás fica mais perto dela. Enquanto ele a acompanha, acusar outro é
        chute (P3). O dono que fica sentado enquanto a mala é levada deixa de
        alcançá-la, e daí em diante decide-se como antes.

        Para todo o resto, o deslocamento precisa se sustentar por
        `carry_confirm_s` antes de valer. Um quadro só não é bagagem sendo
        levada — é ruído de projeção, e decidir nele é o mesmo defeito que o
        caminho de desaparecimento já fechou. Confirmado, empate exato na
        menor distância vira AMBIGUA: o escolhido sairia da ordem da lista.
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

        if self._strong_owner_in_reach(bag, observation, people):
            bag.moved_since = None
            return False

        if bag.moved_since is None:
            bag.moved_since = self.t
        if not cobre(self.t - bag.moved_since, self.config.custody.carry_confirm_s):
            return True

        if not perto:
            # Bagagem que anda sozinha é mais provavelmente artefato de
            # projeção que bagagem levitando. P3 manda suprimir o alerta — mas
            # em silêncio ninguém consegue reconstruir por que a bagagem
            # morreu, então o evento sai com os números que decidiram.
            self._carry_ambiguous(bag, observation, carrier, set())
            return True

        empatados = self._tied_nearest(observation, people)
        if len(empatados) > 1:
            # Como no timeout de oclusão: mais de um candidato não tem como
            # ser escolhido, e P3 manda suprimir em vez de chutar.
            self._carry_ambiguous(bag, observation, carrier, empatados)
            return True

        resolve_removal(bag, carrier.track_id, self.parties, t=self.t, events=self.events)
        flag = flag_for_removal(bag, carrier.track_id, self.t, self.config.flags)
        if flag is not None:
            self.flags.add(flag)
        return True

    def _strong_owner_in_reach(
        self, bag: Bag, observation: Observation, people: list[Observation]
    ) -> bool:
        """Algum membro FORTE do grupo dono alcança a posição atual da bagagem?

        Vínculo fraco não conta -- P1: quem só sentou perto não legitima a
        retirada, então também não a torna incerta.
        """
        party = None if bag.is_orphan else self.parties.get(bag.owner_party)
        if party is None:
            return False
        raio = self.config.pipeline.owner_search_radius_m
        return any(
            party.owns(p.track_id) and p.position.distance_to(observation.position) <= raio
            for p in people
        )

    @staticmethod
    def _tied_nearest(observation: Observation, people: list[Observation]) -> set[int]:
        """Quem empata exatamente na menor distância. Exige alguém em `people`."""
        distancias = {p.track_id: p.position.distance_to(observation.position) for p in people}
        menor = min(distancias.values())
        return {tid for tid, d in distancias.items() if d == menor}

    def _carry_ambiguous(
        self,
        bag: Bag,
        observation: Observation,
        carrier: Observation | None,
        candidatos: set[int],
    ) -> None:
        """Marca AMBIGUA e diz por quê, com os números que decidiram."""
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
                    "moved_m": bag.anchor.distance_to(observation.position),
                    "nearest_person_m": (
                        None
                        if carrier is None
                        else carrier.position.distance_to(observation.position)
                    ),
                    "anchor": [bag.anchor.x, bag.anchor.y],
                    "candidate_people": sorted(candidatos),
                    "neighbour_bags": [],
                },
            )
        )

    def _remember_depositor(self, pendente: _Pendente, people: list[Observation]) -> None:
        """O primeiro que aparecer ao alcance responde pelo depósito.

        O primeiro, e não o último: a cada quadro que passa o dono está mais
        longe e um estranho qualquer está mais perto, então esperar só piora a
        evidência.
        """
        if pendente.depositor is not None:
            return

        lugar = pendente.media()
        carrier = _nearest(people, lugar)
        if (
            carrier is not None
            and carrier.position.distance_to(lugar) <= self.config.pipeline.owner_search_radius_m
        ):
            pendente.depositor = carrier.track_id

    def anchoring(self, observation: Observation, people: list[Observation]) -> int | None:
        """A bagagem ficou parada o bastante para virar âncora?

        Não é só uma espera: enquanto a observação não se sustenta no mesmo
        lugar, ela não é bagagem depositada — é mala na mão de alguém. Um
        registro aberto ali produz uma custódia que nunca existiu, e a perda
        dela vira acusação contra quem estava carregando.

        A janela desliza em vez de reiniciar, e quem decide é `parada`:
        bagagem puxada nunca tem duas metades que concordem, e bagagem no chão
        passa a ter assim que para -- sem que uma excursão de ruído zere a
        espera.

        Devolve `_NAO_ANCORA` enquanto não parou, e quem depositou quando
        parar -- `None` ali significa que ninguém estava ao alcance, e a
        bagagem nasce órfã.
        """
        janela = self.config.custody.rest_confirm_s
        pendente = self.pending_bags.setdefault(observation.track_id, _Pendente(amostras=[]))

        pendente.desliza(self.t, observation.position, janela)
        self._remember_depositor(pendente, people)

        if not pendente.parada(janela, self.config.registry.moved_threshold_m):
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
            if pendente.amostras and pendente.amostras[-1][0] >= corte
        }

    def identify(self, observation: Observation, people: list[Observation]) -> Bag | None:
        """A qual bagagem registrada esta observação pertence, se a alguma.

        Devolve `None` quando a observação não deve produzir efeito nenhum:
        track já declarado ambíguo, readoção recusada por ambiguidade,
        bagagem vetada pela imagem, ou bagagem que ainda não parou.

        Track conhecido também passa pela âncora (`_confere_ancora`): voltando
        de uma ausência sobre a âncora de outra bagagem ocluída, a posição
        manda, não o id.

        O veto vem depois da readoção de propósito: ele fala de âncora nova.
        Uma bagagem já registrada que passa a ser carregada é justamente o
        que `carry_away` precisa ver -- vetá-la faria o furto virar sumiço.
        """
        if observation.track_id in self.ambiguous_tracks:
            return None

        known = self.registry.get_by_track(observation.track_id)
        if known is not None:
            return self._confere_ancora(observation, known)

        adotada = self.adopt_occluded(observation)
        if adotada is AMBIGUOUS:
            # A observação é descartada, e o track fica descartado daqui em
            # diante: registrá-lo como bagagem nova criaria uma bagagem
            # fantasma exatamente onde a ambiguidade acabou de ser declarada.
            self.ambiguous_tracks.add(observation.track_id)
            return None
        if adotada is not None:
            return adotada

        if observation.track_id in self.vetoes:
            # Cortada pela borda, pequena demais para o detector ser confiável,
            # ou dentro da silhueta de alguém: nenhuma delas é bagagem
            # depositada. A espera recomeça do zero quando o veto cair --
            # uma mochila que sai das costas e vai para o chão é outro fato.
            self.pending_bags.pop(observation.track_id, None)
            return None

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

        Custódia encerrada não é pergunta em aberto: a órfã levada e devolvida
        ao lugar dentro da janela iria para quem a levou.
        """
        if bag.state in TERMINAL_BAG_STATES:
            return
        if not bag.is_orphan:
            return
        if excede(self.t - bag.anchored_at, self.config.custody.ownership_window_s):
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
        self.esquece_contatos_do_dono(bag)

    def esquece_contatos_do_dono(self, bag: Bag) -> None:
        """Quem virou dono não é estranho a esta bagagem.

        A posse pode chegar depois do contato -- por `claim_ownership`, que
        tenta a cada quadro enquanto a bagagem é órfã, ou pelo operador, que
        aponta a bagagem primeiro e o dono depois. Medido no MEVA com a tela
        ao vivo: a dona da bolsa apontada entrou na fila em primeiro lugar,
        por tocar na própria bolsa nos segundos entre os dois cliques.

        Custódia encerrada fica como foi decidida: o N3 descreve quem levou a
        bagagem naquele instante (P4), e posse que chega depois -- por
        `claim_ownership` com a bagagem devolvida ao lugar -- não o desfaz.
        """
        if bag.owner_party is None or bag.state in TERMINAL_BAG_STATES:
            return
        # Só vínculo forte é dono (P1). Membro fraco segue estranho a ela.
        for pessoa in self.flags.people():
            if self.parties.owns(bag.owner_party, pessoa):
                self.flags.drop(pessoa, bag.bag_id)
        self.contact_flagged = {
            (pessoa, bagagem)
            for pessoa, bagagem in self.contact_flagged
            if bagagem != bag.bag_id or not self.parties.owns(bag.owner_party, pessoa)
        }

    def observe_bags(self, bags: list[Observation], people: list[Observation]) -> set[int]:
        seen: set[int] = set()

        for observation in bags:
            conhecida = self.identify(observation, people)
            # Depois de `identify`, que precisa saber se o track estava ausente.
            self.bag_track_last_seen[observation.track_id] = _Visto(self.t, self.frames)
            if conhecida is None:
                continue
            if self.registry.is_archived(conhecida.bag_id):
                seen.add(conhecida.bag_id)
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
            # O detector enfim a viu: a partir daqui o silêncio dele volta a
            # significar alguma coisa sobre ela.
            bag.apontada = False
            self.end_occlusion(bag)
            bag.moved_since = None
            self.claim_ownership(bag, people)

        self.forget_stale_pending()
        self.forget_absent_bag_tracks()
        return seen

    def forget_absent_bag_tracks(self) -> None:
        """Track fora do mapa já conta como voltando de ausência, então quem
        passou do limiar sai sem mudar decisão nenhuma. A ausência é medida
        como `_voltando_de_ausencia` a mediria no quadro seguinte, se o track
        faltasse a partir deste."""
        self.bag_track_last_seen = {
            track: visto
            for track, visto in self.bag_track_last_seen.items()
            if not self._ausencia_basta(_ausencia_ate(visto, self.t, self.frames))
        }

    def relational_flags(self, people: list[Observation]) -> None:
        """Flags N1 e N2. Regra P4: relação pessoa-bagagem, nunca atributo."""
        for bag in self.registry.unresolved():
            if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
                continue

            for person in people:
                # Só vínculo forte isenta (P1): o fraco não isenta nem apaga o flag.
                if self.parties.owns(bag.owner_party, person.track_id):
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
                    cobre(decorrido, self.config.pipeline.proximity_flag_s)
                    and chave not in self.proximity_flagged
                ):
                    self.proximity_flagged.add(chave)
                    self.flags.add(
                        flag_proximity(person.track_id, bag, self.t, decorrido, self.config.flags)
                    )

    def resolve_removals(self, seen: set[int], people: list[Observation]) -> None:
        for bag in self.registry.unresolved():
            if bag.state in TERMINAL_BAG_STATES or bag.state is BagState.AMBIGUA:
                continue

            # A attendance roda também para bagagem invisível, contra a âncora
            # congelada: a posição dela é conhecida e as pessoas também. Sem
            # isso, ocluir em ciclos congela o cronômetro e o limiar de 25s
            # nunca completa.
            update_attendance(
                bag, people, self.parties, self.t, self.config.custody, events=self.events
            )

            if bag.apontada:
                # O detector nunca viu esta bagagem -- foi por isso que alguém
                # a apontou. Ele seguir sem ver não é notícia, e resolver esse
                # silêncio por prazo declararia AMBIGUA toda âncora apontada
                # trinta segundos depois do clique.
                continue

            if bag.bag_id in seen:
                continue

            antes = self.missing.get(bag.bag_id, _SEM_AUSENCIA)
            ausencia = _Ausencia(antes.segundos + (self.t - self.previous_t), antes.quadros + 1)
            self.missing[bag.bag_id] = ausencia
            if not self._ausencia_basta(ausencia):
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

            if not cobre(self.t - bag.occluded_since, self.config.custody.max_occlusion_s):
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


class LiveSession:
    """A mesma sessão de `run_session`, alimentada um quadro de cada vez.

    Numa câmera não existe fim de vídeo, e `run_session` só entrega a fila
    quando os quadros acabam. Esta é o corpo daquele laço virando método: a
    fila, os eventos e o estado de cada bagagem ficam consultáveis entre um
    quadro e outro, que é o que uma tela ao vivo precisa.

    `run_session` é um laço sobre ela, então as duas não têm como divergir.
    """

    def __init__(
        self,
        plane: GroundPlane,
        config: Config | None = None,
        *,
        events: EventLog | None = None,
        terminal_archive: TerminalBagArchive | None = None,
    ) -> None:
        self._plane = plane
        self._s = _Session(
            plane, config or Config(), events=events, terminal_archive=terminal_archive
        )
        self._retention = self._s.registry.retention_status()
        self._trava = threading.Lock()
        """Apontar vem da thread do HTTP; alimentar vem da do detector. Sem a
        trava, um clique no meio de um quadro leria um registro pela metade."""
        self._apontadas: dict[int, tuple[float, float]] = {}
        self._ultimo_tracked: list[TrackedDetection] = []

    def feed(self, t: float, tracked: list[TrackedDetection]) -> None:
        with self._trava:
            try:
                if self._s.registry.archive_failed:
                    raise OSError("arquivo terminal falhou; encerre esta sessão")
                self._ultimo_tracked = list(tracked)
                self._alimenta(t, tracked)
            finally:
                self._retention = self._s.registry.retention_status()

    def retention_status(self) -> dict:
        """Último feed confirmado; HTTP não espera a trava de IO do feed."""
        status = self._retention
        if self._s.registry.archive_failed:
            status = replace(status, state="failed")
        return asdict(status)

    def _alimenta(self, t: float, tracked: list[TrackedDetection]) -> None:
        s = self._s
        pipeline = s.config.pipeline
        # No primeiro quadro não há anterior: falta nenhuma pode durar o
        # intervalo desde o zero do relógio.
        s.previous_t = s.t if s.frames else t
        s.frames += 1
        s.t = t

        # O portao roda antes de tudo: uma observacao que implica velocidade
        # impossivel e artefato de projecao, e alimenta a medida de extensao
        # como se fosse deslocamento real.
        observations = s.gate.filter(to_observations(tracked, self._plane, t))
        bags = [o for o in observations if o.cls in BAG_CLASSES]
        s.esquece_proximidade_interrompida()
        people = s.resolve_people(observations, tracked)

        # Fundir é O(n²) sobre pares próximos, com is_comoving por dentro.
        # A cada frame seria desperdício: grupos não se formam em 40 ms.
        if s.merge_due():
            s.merge_parties(people)

        s.vetoes = anchor_vetoes(tracked, pipeline.min_bag_height_px)
        seen = s.observe_bags(bags, people)
        s.relational_flags(people)
        s.resolve_removals(seen, people)
        s.registry.archive_terminal_bags()

    @property
    def t(self) -> float:
        return self._s.t

    @property
    def frames(self) -> int:
        return self._s.frames

    @property
    def events(self) -> EventLog:
        return self._s.events

    def queue(self) -> list[AlertItem]:
        """A fila como está agora, ranqueada pelo mesmo `build_queue`."""
        return build_queue(self._s.flags, self._s.t, self._s.config.alerts)

    def anchors(self) -> list[Bag]:
        """Bagagens registradas cuja custódia ainda está em aberto."""
        return [
            b
            for b in self._s.registry.unresolved()
            if b.state not in TERMINAL_BAG_STATES and b.state is not BagState.AMBIGUA
        ]

    def bag_for_track(self, track_id: int) -> Bag | None:
        """A bagagem registrada por trás de um track bruto, se houver."""
        return self._s.registry.get_by_track(track_id)

    def canonical_person(self, track_id: int) -> int:
        return self._s.linker.links().get(track_id, track_id)

    def party_of(self, track_id: int) -> int | None:
        return self._s.parties.party_of(self.canonical_person(track_id))

    # --- apontar --------------------------------------------------------

    def aponta_bagagem(self, px: float, py: float) -> Bag:
        """O operador diz que ali no chão tem uma bagagem.

        Existe porque o detector não vê: medido no MEVA, nos 90s em que cada
        bolsa furtada fica parada antes do furto, sai zero caixa de bagagem
        sobre ela -- e nem modelo maior nem recorte ampliado mudam isso. Numa
        sala de monitoramento tem alguém olhando a tela, e o que falta não é
        modelo, é a informação de que aquilo é uma bagagem.

        Nasce **órfã**, e é aqui que a regra P1 não abre exceção para humano:
        apontar diz onde, não de quem. Dar posse a quem está mais perto é o
        ataque que P1 fecha -- quem está sentado ao lado da bagagem parada
        pode ser exatamente quem vai levá-la.

        `px`, `py` são o pixel clicado, e valem o pé da bagagem: é onde ela
        toca o chão, que é o que o plano projeta.
        """
        with self._trava:
            if len(self._apontadas) >= MAX_APONTADAS:
                raise ValueError(
                    f"já há {len(self._apontadas)} bagagens apontadas, o teto é {MAX_APONTADAS}"
                )

            s = self._s
            posicao = self._plane.project(px, py)
            observacao = Observation(
                track_id=self._proximo_id_apontado(), cls=BAG_APONTADA, position=posicao, t=s.t
            )
            bag = s.registry.observe(observacao, events=s.events, evidence={"origem": "operador"})
            bag.apontada = True
            bag.occluded_since = s.t
            """Invisível desde que nasceu, porque é verdade: é a informação
            que faz a regra P3 suprimir o desacompanhamento quando o dono
            também sai de quadro."""
            self._apontadas[bag.bag_id] = (px, py)
            self._retention = s.registry.retention_status()
            return bag

    def aponta_dono(self, bag_id: int, px: float, py: float) -> Bag:
        """O operador diz de quem é a bagagem, apontando a pessoa.

        Recusa quando não há ninguém no ponto: sem alguém ali não há a quem
        atribuir, e inventar um dono é fabricar justamente a evidência que
        separa furto de retirada legítima.
        """
        with self._trava:
            s = self._s
            bag = s.registry.get(bag_id)
            if bag is None:
                raise ValueError(f"bagagem {bag_id} não existe")
            if s.registry.is_archived(bag_id):
                raise ValueError("custódia arquivada é somente leitura")
            if bag.state in TERMINAL_BAG_STATES:
                # Com ou sem arquivo terminal: dono apontado depois da retirada
                # apagaria o N3 de quem a levou em `esquece_contatos_do_dono`.
                raise ValueError(
                    f"custódia da bagagem {bag_id} já está encerrada "
                    f"({bag.state.name}); não aceita outro dono"
                )

            dono = _quem_esta_em(self._ultimo_tracked, px, py)
            if dono is None:
                raise ValueError(f"ninguém no ponto ({px:.0f}, {py:.0f})")

            s.registry.assign_owner(
                bag.bag_id,
                s.party_for(self.canonical_person(dono)),
                t=s.t,
                events=s.events,
                evidence={"origem": "operador"},
            )
            s.esquece_contatos_do_dono(bag)
            return bag

    def apontadas(self) -> dict[int, tuple[float, float]]:
        """Onde desenhar cada bagagem apontada: ela não tem caixa de detector,
        e sem o pixel ficaria invisível para quem a apontou."""
        with self._trava:
            return dict(self._apontadas)

    def _proximo_id_apontado(self) -> int:
        """Ids negativos: o espaço dos positivos é do detector, e colidir com
        um track faria a bagagem apontada ser confundida com uma detecção."""
        return min(self._apontadas, default=0) - 1

    def person_level(self, track_id: int) -> FlagLevel | None:
        """O nível mais alto já sinalizado para a pessoa por trás deste track."""
        flags = self._s.flags.for_person(self.canonical_person(track_id))
        return max((f.level for f in flags), default=None)

    def result(self) -> SessionResult:
        s = self._s
        return SessionResult(
            events=s.events,
            queue=self.queue(),
            frames=s.frames,
            duration_s=s.t,
            flags=s.flags,
            links=s.linker.links(),
            bag_links=s.registry.links(),
        )


def run_session(
    frames: Iterator[tuple[float, list[TrackedDetection]]],
    plane: GroundPlane,
    config: Config | None = None,
) -> SessionResult:
    sessao = LiveSession(plane, config)
    for t, tracked in frames:
        sessao.feed(t, tracked)
    return sessao.result()


def removal_outcomes(result: SessionResult) -> dict[BagState, int]:
    """Contagem por desfecho de custódia, para relatório."""
    counts: dict[BagState, int] = {}
    for event in result.events:
        if event.kind is EventKind.BAG_REMOVED_BY_STRANGER:
            counts[BagState.RETIRADA_ESTRANHO] = counts.get(BagState.RETIRADA_ESTRANHO, 0) + 1
        elif event.kind is EventKind.BAG_REMOVED_BY_OWNER:
            counts[BagState.RETIRADA_DONO] = counts.get(BagState.RETIRADA_DONO, 0) + 1
    return counts


__all__ = ["Bag", "LiveSession", "SessionResult", "removal_outcomes", "run_session"]
