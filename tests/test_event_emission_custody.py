"""Task 14b: custody.py e bag_registry.py emitem eventos com a evidência da
decisão.

O ponto crítico é `update_attendance`: roda a cada frame, mas só pode emitir
na TRANSIÇÃO de estado. Emitir a cada chamada enquanto a bagagem permanece
desacompanhada afogaria o log -- um vídeo de 54s a 25fps chamaria a função
mais de mil vezes.
"""

from __future__ import annotations

from custody_watch.bag_registry import BagRegistry
from custody_watch.custody import resolve_removal, update_attendance
from custody_watch.events import EventKind, EventLog
from custody_watch.party import PartyManager
from custody_watch.types import Bag, BagState, Observation, Point


def person(track_id: int, x: float, y: float, t: float = 0.0) -> Observation:
    return Observation(track_id=track_id, cls="person", position=Point(x, y), t=t)


def bag_obs(track_id: int, x: float, y: float, t: float = 0.0) -> Observation:
    return Observation(track_id=track_id, cls="suitcase", position=Point(x, y), t=t)


def owned_bag(party_id: int = 1) -> Bag:
    return Bag(bag_id=100, anchor=Point(0.0, 0.0), owner_party=party_id)


# --- bag_registry.observe -----------------------------------------------------


def test_observe_bagagem_nova_emite_bag_appeared_com_a_ancora():
    registry = BagRegistry()
    events = EventLog()

    bag = registry.observe(bag_obs(1, 5.0, 5.0, t=0.0), events=events)

    appeared = events.of_kind(EventKind.BAG_APPEARED)
    assert len(appeared) == 1
    event = appeared[0]
    assert event.t_start == 0.0
    assert event.t_end == 0.0
    assert event.subject is None
    assert event.bag == bag.bag_id
    assert event.evidence == {"anchor": [5.0, 5.0]}


def test_observe_bagagem_conhecida_nao_emite():
    registry = BagRegistry()
    events = EventLog()
    registry.observe(bag_obs(1, 5.0, 5.0, t=0.0), events=events)

    registry.observe(bag_obs(1, 5.05, 4.98, t=1.0), events=events)

    assert len(events) == 1  # só o BAG_APPEARED da primeira chamada


# --- bag_registry.assign_owner -------------------------------------------------


def test_assign_owner_emite_bag_owned():
    registry = BagRegistry()
    events = EventLog()
    registry.observe(bag_obs(1, 5.0, 5.0, t=0.0))

    registry.assign_owner(bag_id=1, party_id=42, t=3.0, events=events)

    owned = events.of_kind(EventKind.BAG_OWNED)
    assert len(owned) == 1
    event = owned[0]
    assert event.t_start == 3.0
    assert event.t_end == 3.0
    assert event.bag == 1
    assert event.party == 42
    assert event.evidence == {"party": 42}


# --- bag_registry.mark_ambiguous_neighbours ------------------------------------


def test_mark_ambiguous_neighbours_emite_um_evento_com_vizinhos_e_raio():
    registry = BagRegistry()
    events = EventLog()
    registry.observe(bag_obs(1, 5.0, 5.0, t=0.0))
    registry.observe(bag_obs(2, 5.4, 5.0, t=0.0))

    affected = registry.mark_ambiguous_neighbours(bag_id=1, t=7.0, events=events)

    ambiguous = events.of_kind(EventKind.BAG_AMBIGUOUS)
    assert len(ambiguous) == 1
    event = ambiguous[0]
    assert event.t_start == 7.0
    assert event.bag == 1
    assert set(event.evidence["neighbours"]) == set(affected) == {1, 2}
    assert event.evidence["radius_m"] == registry._config.ambiguity_radius_m


# --- custody.update_attendance --------------------------------------------------


def test_update_attendance_emite_bag_unattended_na_transicao():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    events = EventLog()

    update_attendance(bag, [person(1, 10.0, 0.0, t=0.0)], manager, t=0.0, events=events)
    update_attendance(bag, [person(1, 10.0, 0.0, t=26.0)], manager, t=26.0, events=events)

    assert bag.state is BagState.DESACOMPANHADA
    unattended = events.of_kind(EventKind.BAG_UNATTENDED)
    assert len(unattended) == 1
    event = unattended[0]
    assert event.t_start == 26.0
    assert event.t_end == 26.0
    assert event.subject is None
    assert event.bag == bag.bag_id
    assert event.evidence == {"distance_m": 10.0, "elapsed_s": 26.0}


def test_update_attendance_nao_emite_de_novo_enquanto_permanece_desacompanhada():
    """O teste que prova que a máquina de estados não afoga o log."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    events = EventLog()

    update_attendance(bag, [person(1, 10.0, 0.0, t=0.0)], manager, t=0.0, events=events)
    update_attendance(bag, [person(1, 10.0, 0.0, t=26.0)], manager, t=26.0, events=events)
    # A bagagem já está DESACOMPANHADA; chamadas subsequentes no mesmo estado
    # não podem gerar eventos novos.
    update_attendance(bag, [person(1, 10.0, 0.0, t=27.0)], manager, t=27.0, events=events)
    update_attendance(bag, [person(1, 10.0, 0.0, t=28.0)], manager, t=28.0, events=events)
    update_attendance(bag, [person(1, 10.0, 0.0, t=29.0)], manager, t=29.0, events=events)

    assert len(events.of_kind(EventKind.BAG_UNATTENDED)) == 1


def test_update_attendance_emite_bag_reattended_quando_dono_volta():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    events = EventLog()

    update_attendance(bag, [person(1, 10.0, 0.0, t=0.0)], manager, t=0.0, events=events)
    update_attendance(bag, [person(1, 10.0, 0.0, t=26.0)], manager, t=26.0, events=events)
    assert bag.state is BagState.DESACOMPANHADA

    update_attendance(bag, [person(1, 1.0, 0.0, t=27.0)], manager, t=27.0, events=events)

    assert bag.state is BagState.ACOMPANHADA
    reattended = events.of_kind(EventKind.BAG_REATTENDED)
    assert len(reattended) == 1
    event = reattended[0]
    assert event.t_start == 27.0
    assert event.subject is None
    assert event.bag == bag.bag_id
    assert event.evidence == {"distance_m": 1.0}


def test_distance_m_e_none_quando_nenhum_membro_do_grupo_esta_em_cena():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    events = EventLog()

    update_attendance(bag, [person(99, 0.5, 0.0, t=0.0)], manager, t=0.0, events=events)
    update_attendance(bag, [person(99, 0.5, 0.0, t=26.0)], manager, t=26.0, events=events)

    unattended = events.of_kind(EventKind.BAG_UNATTENDED)
    assert len(unattended) == 1
    assert unattended[0].evidence["distance_m"] is None


# --- custody.resolve_removal -----------------------------------------------------


def test_resolve_removal_emite_bag_removed_by_owner():
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])
    bag = owned_bag(party.party_id)
    events = EventLog()

    resolve_removal(bag, carrier_track=2, party_manager=manager, t=15.0, events=events)

    removed = events.of_kind(EventKind.BAG_REMOVED_BY_OWNER)
    assert len(removed) == 1
    event = removed[0]
    assert event.t_start == 15.0
    assert event.subject == 2
    assert event.bag == bag.bag_id
    assert event.evidence == {"carrier": 2, "bond": "strong"}


def test_resolve_removal_emite_bag_removed_by_stranger_com_carregador_e_grupo():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    events = EventLog()

    resolve_removal(bag, carrier_track=99, party_manager=manager, t=20.0, events=events)

    removed = events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)
    assert len(removed) == 1
    event = removed[0]
    assert event.t_start == 20.0
    assert event.subject == 99
    assert event.bag == bag.bag_id
    assert event.evidence == {"carrier": 99, "owner_party": party.party_id}


def test_bagagem_ambigua_nao_emite_evento_de_retirada():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    bag.state = BagState.AMBIGUA
    events = EventLog()

    resolve_removal(bag, carrier_track=99, party_manager=manager, t=5.0, events=events)

    assert bag.state is BagState.AMBIGUA
    assert len(events) == 0


# --- round-trip de sessão inteira -----------------------------------------------


def test_round_trip_sessao_inteira_via_jsonl(tmp_path):
    """Encena: bagagem aparece, ganha dono, fica desacompanhada, é levada por
    um estranho. Grava o log em JSONL e relê -- a prova de que a auditoria
    funciona sem o vídeo.
    """
    registry = BagRegistry()
    manager = PartyManager()
    events = EventLog()

    party = manager.form_on_arrival([1], t=0.0, events=events)
    bag = registry.observe(bag_obs(100, 0.0, 0.0, t=0.0), events=events)
    registry.assign_owner(bag_id=100, party_id=party.party_id, t=0.0, events=events)

    update_attendance(bag, [person(1, 10.0, 0.0, t=1.0)], manager, t=1.0, events=events)
    update_attendance(bag, [person(1, 10.0, 0.0, t=27.0)], manager, t=27.0, events=events)
    assert bag.state is BagState.DESACOMPANHADA

    resolve_removal(bag, carrier_track=99, party_manager=manager, t=30.0, events=events)
    assert bag.state is BagState.RETIRADA_ESTRANHO

    kinds = [event.kind for event in events]
    assert kinds == [
        EventKind.PARTY_FORMED,
        EventKind.BAG_APPEARED,
        EventKind.BAG_OWNED,
        EventKind.BAG_UNATTENDED,
        EventKind.BAG_REMOVED_BY_STRANGER,
    ]

    path = tmp_path / "session.jsonl"
    events.to_jsonl(path)
    reloaded = EventLog.from_jsonl(path)

    assert list(reloaded) == list(events)
