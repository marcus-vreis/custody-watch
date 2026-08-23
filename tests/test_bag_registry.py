from custody_watch.bag_registry import BagRegistry
from custody_watch.config import RegistryConfig
from custody_watch.types import BagState, Observation, Point


def obs(track_id: int, x: float, y: float, t: float = 0.0) -> Observation:
    return Observation(track_id=track_id, cls="suitcase", position=Point(x, y), t=t)


def test_registra_bagagem_nova():
    registry = BagRegistry()
    bag = registry.observe(obs(1, 5.0, 5.0, t=0.0))

    assert bag.state is BagState.NOVA
    assert bag.anchor == Point(5.0, 5.0)
    assert bag.is_orphan is True


def test_bagagem_parada_mantem_a_ancora():
    """Regra P2: a posição é a identidade. Não usa aparência."""
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))
    bag = registry.observe(obs(1, 5.05, 4.98, t=1.0))

    assert bag.anchor == Point(5.0, 5.0)  # jitter sub-limiar não move a âncora
    assert bag.last_seen == 1.0


def test_bagagem_que_se_move_atualiza_a_ancora():
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))
    bag = registry.observe(obs(1, 9.0, 5.0, t=1.0))

    assert bag.anchor == Point(9.0, 5.0)


def test_detecta_que_a_bagagem_se_moveu():
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))

    assert registry.has_moved(obs(1, 5.1, 5.0, t=1.0)) is False
    assert registry.has_moved(obs(1, 9.0, 5.0, t=1.0)) is True


def test_has_moved_de_bagagem_desconhecida_e_falso():
    assert BagRegistry().has_moved(obs(404, 1.0, 1.0, t=0.0)) is False


def test_bagagens_ambiguas_suprimem_alerta_das_duas():
    """Regra P3. Duas malas idênticas ocluídas juntas: marcar ambas e calar.

    O contrário — chutar qual é qual — corrompe o mapa de posse e gera um
    furto que não aconteceu.
    """
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))
    registry.observe(Observation(2, "suitcase", Point(5.4, 5.0), 0.0))

    ambiguous = registry.mark_ambiguous_neighbours(bag_id=1)

    assert set(ambiguous) == {1, 2}
    assert registry.get(1).state is BagState.AMBIGUA
    assert registry.get(2).state is BagState.AMBIGUA


def test_bagagem_distante_nao_vira_ambigua():
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))
    registry.observe(Observation(2, "suitcase", Point(20.0, 5.0), 0.0))

    ambiguous = registry.mark_ambiguous_neighbours(bag_id=1)

    assert ambiguous == [1]
    assert registry.get(2).state is BagState.NOVA


def test_atribuicao_de_dono_por_back_tracing():
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))
    registry.assign_owner(bag_id=1, party_id=42)

    bag = registry.get(1)
    assert bag.owner_party == 42
    assert bag.is_orphan is False


def test_all_lista_todas_as_bagagens():
    registry = BagRegistry()
    registry.observe(obs(1, 5.0, 5.0, t=0.0))
    registry.observe(Observation(2, "backpack", Point(1.0, 1.0), 0.0))

    assert {bag.bag_id for bag in registry.all()} == {1, 2}


def test_get_de_bagagem_desconhecida_e_none():
    assert BagRegistry().get(404) is None


def test_config_altera_o_limiar_de_movimento():
    """Com limiar de 2m, um deslocamento de 1m deixa de contar como movimento."""
    registry = BagRegistry(RegistryConfig(moved_threshold_m=2.0))
    registry.observe(obs(1, 5.0, 5.0, t=0.0))

    assert registry.has_moved(obs(1, 6.0, 5.0, t=1.0)) is False
    assert registry.has_moved(obs(1, 8.0, 5.0, t=1.0)) is True
