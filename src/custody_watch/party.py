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

from .types import Bond, Observation, Party

PROXIMITY_M = 2.0
LATE_JOIN_EXTENT_M = 5.0
MIN_EXTENT_M = 0.5
MIN_OVERLAP_SAMPLES = 3
MIN_OVERLAP_S = 2.0
MAX_GAP_S = 2.0
TIME_TOLERANCE_S = 0.5
WEAK_BOND_S = 60.0


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

    Pressupõe que os dois tracks compartilham uma grade de tempo: `track_video`
    carimba todas as observações de um frame com o mesmo instante. Grades
    defasadas produzem menos pares, nunca pares errados.
    """
    if not track_a or not track_b:
        return []

    nearest_b_of = {
        id(a): min(track_b, key=lambda b: abs(b.t - a.t)) for a in track_a
    }

    pairs: list[tuple[Observation, Observation]] = []
    for observation in track_b:
        nearest = min(track_a, key=lambda a: abs(a.t - observation.t))
        if abs(nearest.t - observation.t) > tolerance_s:
            continue
        if nearest_b_of[id(nearest)] is not observation:
            continue
        pairs.append((nearest, observation))
    return pairs


def _overlap_is_continuous(
    pairs: Sequence[tuple[Observation, Observation]],
    min_overlap_s: float = MIN_OVERLAP_S,
    max_gap_s: float = MAX_GAP_S,
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
    if times[-1] - times[0] < min_overlap_s:
        return False
    return all(
        later - earlier <= max_gap_s
        for earlier, later in zip(times, times[1:], strict=False)
    )


def is_comoving(
    track_a: Sequence[Observation],
    track_b: Sequence[Observation],
    min_extent_m: float = MIN_EXTENT_M,
    max_separation_m: float = PROXIMITY_M,
    min_overlap: int = MIN_OVERLAP_SAMPLES,
    tolerance_s: float = TIME_TOLERANCE_S,
) -> bool:
    """Duas pessoas co-movem se andaram JUNTAS, no mesmo intervalo de tempo.

    Exigir extensão mínima nos dois tracks é essencial: duas pessoas paradas têm
    vetor de velocidade zero, e zero correlaciona perfeitamente com zero.

    Extensão e separação são medidas apenas sobre a janela em que os dois
    aparecem simultaneamente. Sem isso, os frames em que um deles se afasta
    contariam como esforço conjunto.

    Simétrica nos argumentos.
    """
    pairs = _pair_by_time(track_a, track_b, tolerance_s)
    if len(pairs) < min_overlap:
        return False
    if not _overlap_is_continuous(pairs):
        return False

    paired_a = [a for a, _ in pairs]
    paired_b = [b for _, b in pairs]
    if _extent(paired_a) < min_extent_m or _extent(paired_b) < min_extent_m:
        return False

    return all(a.position.distance_to(b.position) <= max_separation_m for a, b in pairs)


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

    def __init__(self) -> None:
        self._parties: dict[int, Party] = {}
        self._party_of: dict[int, int] = {}
        self._next_id = 1

    def get(self, party_id: int) -> Party | None:
        return self._parties.get(party_id)

    def party_of(self, track_id: int) -> int | None:
        return self._party_of.get(track_id)

    def form_on_arrival(self, track_ids: Sequence[int]) -> Party:
        """Grupo formado na entrada da cena — vínculo forte.

        Aceita evidência mais fraca que a entrada tardia porque é o momento
        natural de formação e o custo de simular é alto: exigiria o atacante já
        estar acompanhando a vítima antes.
        """
        if not track_ids:
            raise ValueError("um grupo precisa de ao menos um membro")

        already = sorted(tid for tid in track_ids if tid in self._party_of)
        if already:
            raise ValueError(f"tracks já pertencem a um grupo: {already}")

        party = Party(
            party_id=self._next_id, members={tid: Bond.STRONG for tid in track_ids}
        )
        self._parties[party.party_id] = party
        for track_id in track_ids:
            self._party_of[track_id] = party.party_id
        self._next_id += 1
        return party

    def join_weak(self, party_id: int, track_id: int) -> bool:
        """Proximidade estática. NÃO transfere posse — apenas atenua o flag.

        Recusa quem já tem grupo: sentar perto de estranhos não pode tirar
        ninguém da própria família.
        """
        if track_id in self._party_of:
            return False

        self._parties[party_id].members[track_id] = Bond.WEAK
        self._party_of[track_id] = party_id
        return True

    def try_join_strong(
        self,
        party_id: int,
        track_id: int,
        member_track: Sequence[Observation],
        candidate_track: Sequence[Observation],
    ) -> bool:
        """Promove a vínculo forte após co-movimento sustentado com um membro
        que já tem vínculo forte.

        O vínculo não é transitivo a partir de membros fracos: dois cúmplices —
        um que senta perto da vítima e outro que anda ao lado dele, longe dela —
        não podem obter posse da bagagem.
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

        if not is_comoving(member_track, candidate_track):
            return False

        pairs = _pair_by_time(member_track, candidate_track)
        member_extent = _extent([a for a, _ in pairs])
        candidate_extent = _extent([b for _, b in pairs])
        # Os dois precisam ter coberto terreno. Medir só o candidato deixaria
        # passar quem andou ao lado de um membro que não saiu do lugar.
        if min(member_extent, candidate_extent) < LATE_JOIN_EXTENT_M:
            return False

        party.members[track_id] = Bond.STRONG
        self._party_of[track_id] = party_id
        return True

    def same_party_strong(self, track_a: int, track_b: int) -> bool:
        party_id = self.party_of(track_a)
        if party_id is None or party_id != self.party_of(track_b):
            return False

        party = self._parties[party_id]
        return party.owns(track_a) and party.owns(track_b)
