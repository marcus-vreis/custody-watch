from custody_watch.party import PartyManager, is_comoving
from custody_watch.types import Bond, Observation, Point


def track(track_id: int, points: list[tuple[float, float]]) -> list[Observation]:
    return [
        Observation(track_id=track_id, cls="person", position=Point(x, y), t=float(i))
        for i, (x, y) in enumerate(points)
    ]


def test_pessoas_paradas_nao_estao_co_movendo():
    """A armadilha central: zero correlaciona perfeitamente com zero.

    Sem exigir deslocamento mínimo, todos os sentados na praça de alimentação
    viram uma party única — e o exploit volta pela porta dos fundos.
    """
    a = track(1, [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)])
    b = track(2, [(1.0, 0.0), (1.0, 0.0), (1.0, 0.0)])

    assert is_comoving(a, b) is False


def test_pessoas_andando_juntas_estao_co_movendo():
    a = track(1, [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)])
    b = track(2, [(0.5, 0.0), (2.5, 0.0), (4.5, 0.0)])

    assert is_comoving(a, b) is True


def test_pessoas_andando_separadas_nao_estao_co_movendo():
    a = track(1, [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)])
    b = track(2, [(0.0, 10.0), (2.0, 10.0), (4.0, 10.0)])

    assert is_comoving(a, b) is False


def test_track_curto_demais_nao_e_co_movimento():
    assert is_comoving(track(1, [(0.0, 0.0)]), track(2, [(0.5, 0.0)])) is False


def test_chegada_conjunta_forma_party_com_vinculo_forte():
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])

    assert party.owns(1) is True
    assert party.owns(2) is True


def test_proximidade_estatica_gera_apenas_vinculo_fraco():
    """Regra P1: sentar perto não transfere posse de bagagem."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    manager.join_weak(party.party_id, track_id=99)

    assert party.members[99] is Bond.WEAK
    assert party.owns(99) is False


def test_entrada_tardia_exige_co_movimento_sustentado():
    """Vínculo forte tardio precisa de 5m de deslocamento conjunto.

    Assimetria intencional: quanto mais tarde o vínculo se forma, mais caro
    ele deve ser, porque é o caminho que um atacante exploraria.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])

    curto = manager.try_join_strong(party.party_id, 2, track(1, [(0.0, 0.0), (1.0, 0.0)]),
                                    track(2, [(0.5, 0.0), (1.5, 0.0)]))
    assert curto is False
    assert party.owns(2) is False

    longo = manager.try_join_strong(party.party_id, 2, track(1, [(0.0, 0.0), (6.0, 0.0)]),
                                    track(2, [(0.5, 0.0), (6.5, 0.0)]))
    assert longo is True
    assert party.owns(2) is True


def test_vinculo_fraco_pode_ser_promovido_a_forte():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    manager.join_weak(party.party_id, 2)

    manager.try_join_strong(party.party_id, 2, track(1, [(0.0, 0.0), (6.0, 0.0)]),
                            track(2, [(0.5, 0.0), (6.5, 0.0)]))

    assert party.members[2] is Bond.STRONG


def test_party_of_localiza_o_grupo_de_uma_pessoa():
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])

    assert manager.party_of(1) == party.party_id
    assert manager.party_of(404) is None
