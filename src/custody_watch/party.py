"""Formação e manutenção de grupos (spec §4.3).

Regra P1: posse de bagagem pertence ao grupo, não ao indivíduo, e só se
estende a membros com vínculo forte.

Se pertencer a um grupo dependesse apenas de proximidade e tempo, um ladrão
sentaria ao lado da vítima por três minutos e o sistema o declararia dono da
bagagem. Ele não burlaria o sistema — usaria o sistema como projetado.
"""

from __future__ import annotations

from collections.abc import Sequence

from .types import Bond, Observation, Party

PROXIMITY_M = 2.0
LATE_JOIN_DISPLACEMENT_M = 5.0
MIN_DISPLACEMENT_M = 0.5
WEAK_BOND_S = 60.0


def _displacement(track: Sequence[Observation]) -> float:
    if len(track) < 2:
        return 0.0
    return track[0].position.distance_to(track[-1].position)


def is_comoving(
    track_a: Sequence[Observation],
    track_b: Sequence[Observation],
    min_displacement_m: float = MIN_DISPLACEMENT_M,
    max_separation_m: float = PROXIMITY_M,
) -> bool:
    """Duas pessoas co-movem se andaram JUNTAS, não apenas ficaram perto.

    Exigir deslocamento mínimo é essencial: duas pessoas paradas têm vetor de
    velocidade zero, e zero correlaciona perfeitamente com zero.
    """
    if len(track_a) < 2 or len(track_b) < 2:
        return False
    if _displacement(track_a) < min_displacement_m:
        return False
    if _displacement(track_b) < min_displacement_m:
        return False
    return all(
        a.position.distance_to(b.position) <= max_separation_m
        for a, b in zip(track_a, track_b, strict=False)
    )


class PartyManager:
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
        natural de formação e o custo de simular é alto: exigiria o atacante
        já estar acompanhando a vítima antes.
        """
        party = Party(party_id=self._next_id, members={tid: Bond.STRONG for tid in track_ids})
        self._parties[party.party_id] = party
        for track_id in track_ids:
            self._party_of[track_id] = party.party_id
        self._next_id += 1
        return party

    def join_weak(self, party_id: int, track_id: int) -> None:
        """Proximidade estática. NÃO transfere posse — apenas atenua o flag."""
        party = self._parties[party_id]
        if party.members.get(track_id) is Bond.STRONG:
            return
        party.members[track_id] = Bond.WEAK
        self._party_of.setdefault(track_id, party_id)

    def try_join_strong(
        self,
        party_id: int,
        track_id: int,
        member_track: Sequence[Observation],
        candidate_track: Sequence[Observation],
    ) -> bool:
        """Promove a vínculo forte se houve co-movimento sustentado."""
        party = self._parties[party_id]
        if not is_comoving(member_track, candidate_track):
            return False
        if _displacement(candidate_track) < LATE_JOIN_DISPLACEMENT_M:
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
