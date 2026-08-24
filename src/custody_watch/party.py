"""Formação e manutenção de grupos (spec §4.3).

Regra P1: posse de bagagem pertence ao grupo, não ao indivíduo, e só se
estende a membros com vínculo forte.

Se pertencer a um grupo dependesse apenas de proximidade e tempo, um ladrão
sentaria ao lado da vítima por três minutos e o sistema o declararia dono da
bagagem. Ele não burlaria o sistema — usaria o sistema como projetado.

Duas rodadas de revisão adversarial derrubaram este módulo, ambas pelo mesmo
caminho: o de co-movimento, que o design trata como caro e que na prática
saía barato.

A primeira: a comparação casava as duas listas de observações por índice e
ignorava `Observation.t`. Um ladrão seguindo a vítima a oito metros entra em
cena mais tarde, tem o track deslocado, e era declarado co-movendo sem nunca
ter chegado perto dela. Trajetórias passaram a ser casadas por instante, com
toda evidência medida só na janela em que os dois aparecem simultaneamente.

A segunda: a métrica de movimento. Deslocamento líquido separava um casal que
anda até a loja e volta ao mesmo banco; caminho percorrido, que o substituiu,
media quanto o acumulador subiu em vez de se a pessoa foi a algum lugar —
perambular no lugar, ou o próprio ruído do detector, satisfaziam o limiar.
Ver `_extent`.

A lição das duas: aqui, uma métrica que erra o sinal não gera um falso
alerta, gera um ladrão com posse legítima da bagagem alheia.
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import PartyConfig
from .events import Event, EventKind, EventLog
from .types import Bond, Observation, Party

DEFAULT_PARTY = PartyConfig()

PROXIMITY_M = DEFAULT_PARTY.proximity_m
LATE_JOIN_EXTENT_M = DEFAULT_PARTY.late_join_extent_m
MIN_EXTENT_M = DEFAULT_PARTY.min_extent_m
MIN_OVERLAP_SAMPLES = DEFAULT_PARTY.min_overlap_samples
MIN_OVERLAP_S = DEFAULT_PARTY.min_overlap_s
MAX_GAP_S = DEFAULT_PARTY.max_gap_s
TIME_TOLERANCE_S = DEFAULT_PARTY.time_tolerance_s

# Instantes vem de `frame_index / fps`, entao diferencas quase nunca caem
# exatas: 2.4 - 0.4 da 2.0000000000000004. Sem esta folga, uma cadencia
# exatamente no limite e aceita ou rejeitada conforme o erro acumulado.
TIME_EPSILON_S = 1e-9
WEAK_BOND_S = DEFAULT_PARTY.weak_bond_s


def _extent(track: Sequence[Observation]) -> float:
    """Extensão da trajetória: diagonal da caixa que contém todos os pontos.

    Nem deslocamento líquido, nem caminho percorrido — as duas alternativas
    óbvias falham em casos opostos, e cada uma já quebrou este módulo uma vez.

    Deslocamento líquido (ponta a ponta) dá zero para um casal que anda vinte
    metros até a loja e volta ao mesmo banco, separando um grupo legítimo.

    Caminho percorrido (soma dos passos) mede quanto o acumulador subiu, não se
    a pessoa foi a algum lugar. Um ladrão perambulando num raio de um metro ao
    lado da vítima acumula seis metros em sete segundos. Pior: ruído de
    detecção soma linearmente — a dois centímetros de jitter, um quarto de
    pixel no fundo da cena, uma pessoa imóvel acumula cinco metros em dez
    segundos a 25 fps.

    Extensão acerta os quatro casos: pequena para quem ficou onde estava,
    grande para quem cobriu terreno, ida e volta inclusive.
    """
    if not track:
        return 0.0
    xs = [observation.position.x for observation in track]
    ys = [observation.position.y for observation in track]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    return (width**2 + height**2) ** 0.5


def _nearest_indices(source: Sequence[Observation], target: Sequence[Observation]) -> list[int]:
    """Para cada item de `target`, o índice do item de `source` mais próximo no tempo.

    Varredura de dois ponteiros, O(n+m), sobre listas ordenadas por `t`. A busca
    ingênua com `min()` era O(n·m) e dominava o pipeline: no CAVIAR consumia 47%
    do tempo total, 34 ms por chamada.

    O avanço só acontece quando o próximo é **estritamente** melhor, para que
    empates fiquem com o índice mais antigo — o mesmo desempate de `min()`.
    """
    indices: list[int] = []
    i = 0
    for item in target:
        while i + 1 < len(source) and abs(source[i + 1].t - item.t) < abs(source[i].t - item.t):
            i += 1
        indices.append(i)
    return indices


def _pair_by_time(
    track_a: Sequence[Observation],
    track_b: Sequence[Observation],
    tolerance_s: float = TIME_TOLERANCE_S,
) -> list[tuple[Observation, Observation]]:
    """Casa observações por instante, nunca por posição na lista.

    Tracks têm comprimentos e janelas diferentes o tempo todo: gente entra em
    cena depois, a projeção descarta pontos na linha do horizonte, o tracker
    perde e recupera. Casar por índice compara instantes não relacionados.

    O casamento exige vizinhança mútua — `a` é a mais próxima de `b` no tempo
    *e* vice-versa. Sem isso, várias observações de um track colapsariam sobre
    a mesma do outro, e a função não seria simétrica nos argumentos.

    Pressupõe que os dois tracks compartilham uma grade de tempo: `VideoSource`
    carimba todas as observações de um frame com o mesmo instante. Grades
    defasadas produzem menos pares, nunca pares errados.

    Ordena defensivamente: a varredura de dois ponteiros exige ordem temporal,
    e sobre entrada já ordenada o custo é linear.
    """
    if not track_a or not track_b:
        return []

    a = sorted(track_a, key=lambda o: o.t)
    b = sorted(track_b, key=lambda o: o.t)

    mais_proximo_em_a = _nearest_indices(a, b)
    mais_proximo_em_b = _nearest_indices(b, a)

    pairs: list[tuple[Observation, Observation]] = []
    for j, observation in enumerate(b):
        i = mais_proximo_em_a[j]
        if abs(a[i].t - observation.t) > tolerance_s:
            continue
        if mais_proximo_em_b[i] != j:
            continue
        pairs.append((a[i], observation))
    return pairs


def _overlap_is_continuous(
    pairs: Sequence[tuple[Observation, Observation]],
    config: PartyConfig = DEFAULT_PARTY,
) -> bool:
    """A sobreposição precisa ser contínua no tempo, não três fotografias.

    Contagem de amostras não é duração: três observações separadas por um
    minuto cada satisfariam qualquer mínimo de amostras. Como tracks
    fragmentados são o caso normal, um ladrão cujo track sobrevive nos três
    instantes em que passou perto da vítima receberia vínculo forte sem nunca
    ter andado com ela.
    """
    if not pairs:
        return False

    times = [b.t for _, b in pairs]
    if times[-1] - times[0] < config.min_overlap_s - TIME_EPSILON_S:
        return False
    return all(
        later - earlier <= config.max_gap_s + TIME_EPSILON_S
        for earlier, later in zip(times, times[1:], strict=False)
    )


def _comovement(
    track_a: Sequence[Observation],
    track_b: Sequence[Observation],
    config: PartyConfig = DEFAULT_PARTY,
) -> list[tuple[Observation, Observation]] | None:
    """Os pares casados quando as duas pessoas co-movem, `None` caso contrário.

    Devolve os pares em vez de um booleano para que quem precise das medidas —
    `try_join_strong` precisa das extensões — não tenha que refazer o
    casamento. Recalcular custava uma segunda passada completa por promoção.
    """
    pairs = _pair_by_time(track_a, track_b, config.time_tolerance_s)
    if len(pairs) < config.min_overlap_samples:
        return None
    if not _overlap_is_continuous(pairs, config):
        return None

    paired_a = [a for a, _ in pairs]
    paired_b = [b for _, b in pairs]
    if _extent(paired_a) < config.min_extent_m or _extent(paired_b) < config.min_extent_m:
        return None

    if not all(a.position.distance_to(b.position) <= config.proximity_m for a, b in pairs):
        return None

    return pairs


def is_comoving(
    track_a: Sequence[Observation],
    track_b: Sequence[Observation],
    config: PartyConfig = DEFAULT_PARTY,
) -> bool:
    """Duas pessoas co-movem se andaram JUNTAS, no mesmo intervalo de tempo.

    Exigir extensão mínima nos dois tracks é essencial: duas pessoas paradas têm
    vetor de velocidade zero, e zero correlaciona perfeitamente com zero.

    Extensão e separação são medidas apenas sobre a janela em que os dois
    aparecem simultaneamente. Sem isso, os frames em que um deles se afasta
    contariam como esforço conjunto.

    Simétrica nos argumentos.
    """
    return _comovement(track_a, track_b, config) is not None


def _single_track_id(track: Sequence[Observation]) -> int:
    """Um track é de uma pessoa só. Misturar ids é erro de chamador."""
    ids = {observation.track_id for observation in track}
    if len(ids) != 1:
        raise ValueError(f"track deve conter um único track_id, contém {sorted(ids)}")
    return ids.pop()


class PartyManager:
    """Uma pessoa pertence a no máximo um grupo.

    `_party_of` e `Party.members` são mantidos consistentes por construção:
    todo caminho de escrita passa pelos métodos abaixo, e nenhum deles registra
    alguém num grupo sem registrar o inverso.
    """

    def __init__(self, config: PartyConfig = DEFAULT_PARTY) -> None:
        self._parties: dict[int, Party] = {}
        self._party_of: dict[int, int] = {}
        self._next_id = 1
        self._config = config

    def get(self, party_id: int) -> Party | None:
        return self._parties.get(party_id)

    def party_of(self, track_id: int) -> int | None:
        return self._party_of.get(track_id)

    def form_on_arrival(
        self,
        track_ids: Sequence[int],
        t: float = 0.0,
        events: EventLog | None = None,
    ) -> Party:
        """Grupo formado na entrada da cena — vínculo forte.

        Aceita evidência mais fraca que a entrada tardia porque é o momento
        natural de formação e o custo de simular é alto: exigiria o atacante já
        estar acompanhando a vítima antes.

        `t` é o instante do frame em que o grupo se formou; o default existe só
        para não quebrar chamadas antigas — o orquestrador deve sempre passar o
        instante real do frame. Quando `events` é `None`, nada é emitido.
        """
        if not track_ids:
            raise ValueError("um grupo precisa de ao menos um membro")

        already = sorted(tid for tid in track_ids if tid in self._party_of)
        if already:
            raise ValueError(f"tracks já pertencem a um grupo: {already}")

        party = Party(party_id=self._next_id, members={tid: Bond.STRONG for tid in track_ids})
        self._parties[party.party_id] = party
        for track_id in track_ids:
            self._party_of[track_id] = party.party_id
        self._next_id += 1

        if events is not None:
            events.emit(
                Event(
                    kind=EventKind.PARTY_FORMED,
                    t_start=t,
                    t_end=t,
                    subject=None,
                    bag=None,
                    party=party.party_id,
                    evidence={"members": sorted(track_ids)},
                )
            )
        return party

    def join_weak(
        self,
        party_id: int,
        track_id: int,
        t: float = 0.0,
        events: EventLog | None = None,
    ) -> bool:
        """Proximidade estática. NÃO transfere posse — apenas atenua o flag.

        Recusa quem já tem grupo: sentar perto de estranhos não pode tirar
        ninguém da própria família.

        `t` é o instante do frame; o default existe só para não quebrar
        chamadas antigas — o orquestrador deve sempre passar o instante real do
        frame. Recusa não emite nada: evento é registro do que aconteceu, não
        do que foi cogitado. Quando `events` é `None`, nada é emitido.
        """
        if track_id in self._party_of:
            return False

        self._parties[party_id].members[track_id] = Bond.WEAK
        self._party_of[track_id] = party_id

        if events is not None:
            events.emit(
                Event(
                    kind=EventKind.PARTY_JOINED_WEAK,
                    t_start=t,
                    t_end=t,
                    subject=track_id,
                    bag=None,
                    party=party_id,
                    evidence={"party": party_id},
                )
            )
        return True

    def try_join_strong(
        self,
        party_id: int,
        track_id: int,
        member_track: Sequence[Observation],
        candidate_track: Sequence[Observation],
        events: EventLog | None = None,
    ) -> bool:
        """Promove a vínculo forte após co-movimento sustentado com um membro
        que já tem vínculo forte.

        O vínculo não é transitivo a partir de membros fracos: dois cúmplices —
        um que senta perto da vítima e outro que anda ao lado dele, longe dela —
        não podem obter posse da bagagem.

        Não recebe `t`: o instante vem das próprias trajetórias, e o intervalo
        do evento emitido é a janela de sobreposição entre elas. Recusa não
        emite nada. Quando `events` é `None`, nada é emitido.
        """
        party = self._parties[party_id]
        if not member_track or not candidate_track:
            return False

        member_id = _single_track_id(member_track)
        if _single_track_id(candidate_track) != track_id:
            raise ValueError("candidate_track não pertence ao track_id informado")
        if member_id == track_id:
            return False
        if not party.owns(member_id):
            return False

        current = self._party_of.get(track_id)
        if current is not None and current != party_id:
            return False

        if not is_comoving(member_track, candidate_track, self._config):
            return False

        pairs = _pair_by_time(member_track, candidate_track)
        member_extent = _extent([a for a, _ in pairs])
        candidate_extent = _extent([b for _, b in pairs])
        # Os dois precisam ter coberto terreno. Medir só o candidato deixaria
        # passar quem andou ao lado de um membro que não saiu do lugar.
        if min(member_extent, candidate_extent) < self._config.late_join_extent_m:
            return False

        party.members[track_id] = Bond.STRONG
        self._party_of[track_id] = party_id

        if events is not None:
            max_separation_m = max(a.position.distance_to(b.position) for a, b in pairs)
            events.emit(
                Event(
                    kind=EventKind.PARTY_JOINED_STRONG,
                    t_start=pairs[0][1].t,
                    t_end=pairs[-1][1].t,
                    subject=track_id,
                    bag=None,
                    party=party_id,
                    evidence={
                        "extent_member_m": member_extent,
                        "extent_candidate_m": candidate_extent,
                        "overlap_s": pairs[-1][1].t - pairs[0][1].t,
                        "max_separation_m": max_separation_m,
                    },
                )
            )
        return True

    def same_party_strong(self, track_a: int, track_b: int) -> bool:
        party_id = self.party_of(track_a)
        if party_id is None or party_id != self.party_of(track_b):
            return False

        party = self._parties[party_id]
        return party.owns(track_a) and party.owns(track_b)
