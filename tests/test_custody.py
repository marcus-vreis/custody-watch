from custody_watch.custody import resolve_removal, update_attendance
from custody_watch.party import PartyManager
from custody_watch.types import Bag, BagState, Observation, Point


def person(track_id: int, x: float, y: float, t: float = 0.0) -> Observation:
    return Observation(track_id=track_id, cls="person", position=Point(x, y), t=t)


def owned_bag(party_id: int = 1) -> Bag:
    return Bag(bag_id=100, anchor=Point(0.0, 0.0), owner_party=party_id)


def test_dono_perto_mantem_bagagem_acompanhada():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)

    update_attendance(bag, [person(1, 1.0, 0.0)], manager, t=0.0)

    assert bag.state is BagState.ACOMPANHADA
    assert bag.unattended_since is None


def test_dono_longe_por_menos_de_25s_ainda_nao_e_desacompanhada():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)

    update_attendance(bag, [person(1, 10.0, 0.0, t=0.0)], manager, t=0.0)
    update_attendance(bag, [person(1, 10.0, 0.0, t=10.0)], manager, t=10.0)

    assert bag.state is not BagState.DESACOMPANHADA
    assert bag.unattended_since == 0.0


def test_dono_longe_por_mais_de_25s_torna_desacompanhada():
    """Limiares 3m / 25s vêm do protocolo do PETS2007."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)

    update_attendance(bag, [person(1, 10.0, 0.0, t=0.0)], manager, t=0.0)
    update_attendance(bag, [person(1, 10.0, 0.0, t=26.0)], manager, t=26.0)

    assert bag.state is BagState.DESACOMPANHADA


def test_dono_que_volta_reseta_o_cronometro():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)

    update_attendance(bag, [person(1, 10.0, 0.0, t=0.0)], manager, t=0.0)
    update_attendance(bag, [person(1, 1.0, 0.0, t=5.0)], manager, t=5.0)

    assert bag.state is BagState.ACOMPANHADA
    assert bag.unattended_since is None


def test_qualquer_membro_forte_do_grupo_conta_como_dono_presente():
    """Posse pertence ao grupo, não ao indivíduo."""
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])
    bag = owned_bag(party.party_id)

    update_attendance(bag, [person(2, 1.0, 0.0)], manager, t=0.0)

    assert bag.state is BagState.ACOMPANHADA


def test_estranho_perto_nao_conta_como_dono_presente():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)

    update_attendance(bag, [person(99, 0.5, 0.0, t=0.0)], manager, t=0.0)
    update_attendance(bag, [person(99, 0.5, 0.0, t=26.0)], manager, t=26.0)

    assert bag.state is BagState.DESACOMPANHADA


def test_bagagem_ambigua_nao_muda_de_estado_por_presenca():
    """Regra P3: incerteza suprime, nunca gera."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    bag.state = BagState.AMBIGUA

    update_attendance(bag, [person(1, 50.0, 0.0, t=0.0)], manager, t=0.0)
    update_attendance(bag, [person(1, 50.0, 0.0, t=99.0)], manager, t=99.0)

    assert bag.state is BagState.AMBIGUA


def test_bagagem_orfa_nao_acumula_tempo_de_desacompanhada():
    manager = PartyManager()
    bag = Bag(bag_id=100, anchor=Point(0.0, 0.0), owner_party=None)

    update_attendance(bag, [person(1, 50.0, 0.0)], manager, t=100.0)

    assert bag.unattended_since is None


def test_membro_do_grupo_leva_a_bagagem_e_retirada_legitima():
    """O caso que mais gerava falso positivo sem o party system."""
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])
    bag = owned_bag(party.party_id)

    resolve_removal(bag, carrier_track=2, party_manager=manager)

    assert bag.state is BagState.RETIRADA_DONO


def test_estranho_leva_a_bagagem_e_evento_n3():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)

    resolve_removal(bag, carrier_track=99, party_manager=manager)

    assert bag.state is BagState.RETIRADA_ESTRANHO


def test_vinculo_fraco_nao_legitima_a_retirada():
    """Regra P1 no ponto onde ela vira decisão operacional.

    Quem apenas sentou perto não pode sair com a mala.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    manager.join_weak(party.party_id, track_id=99)
    bag = owned_bag(party.party_id)

    resolve_removal(bag, carrier_track=99, party_manager=manager)

    assert bag.state is BagState.RETIRADA_ESTRANHO


def test_bagagem_ambigua_nunca_gera_alerta_de_retirada():
    """Regra P3: chutar qual mala é qual geraria um furto que não aconteceu."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    bag.state = BagState.AMBIGUA

    resolve_removal(bag, carrier_track=99, party_manager=manager)

    assert bag.state is BagState.AMBIGUA


def test_bagagem_orfa_removida_e_evento_n3():
    manager = PartyManager()
    bag = Bag(bag_id=100, anchor=Point(0.0, 0.0), owner_party=None)

    resolve_removal(bag, carrier_track=99, party_manager=manager)

    assert bag.state is BagState.RETIRADA_ESTRANHO


def test_estado_terminal_nao_transiciona_de_novo():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    bag = owned_bag(party.party_id)
    bag.state = BagState.RETIRADA_DONO

    resolve_removal(bag, carrier_track=99, party_manager=manager)

    assert bag.state is BagState.RETIRADA_DONO
