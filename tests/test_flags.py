import math

import pytest

from custody_watch.flags import (
    TAU_S,
    FlagStore,
    flag_contact,
    flag_for_removal,
    flag_proximity,
    score,
)
from custody_watch.types import Bag, BagState, Flag, FlagLevel, Point


def make_flag(t: float, weight: float = 1.0, level: FlagLevel = FlagLevel.N2) -> Flag:
    return Flag(
        kind="teste", level=level, person=1, bag=2, t=t, weight=weight,
        explanation="teste",
    )


def test_flag_recente_vale_o_peso_integral():
    assert score([make_flag(t=100.0, weight=3.0)], now=100.0) == pytest.approx(3.0)


def test_flag_de_uma_meia_vida_atras_decai_por_e():
    """Sem decaimento, quem passou 4h no aeroporto vira suspeito por existir."""
    result = score([make_flag(t=0.0, weight=1.0)], now=TAU_S)
    assert result == pytest.approx(1.0 / math.e, rel=1e-6)


def test_flags_acumulam():
    flags = [make_flag(t=50.0, weight=1.0), make_flag(t=50.0, weight=2.0)]
    assert score(flags, now=50.0) == pytest.approx(3.0)


def test_flag_no_futuro_e_ignorado():
    assert score([make_flag(t=200.0)], now=100.0) == pytest.approx(0.0)


def test_lista_vazia_tem_score_zero():
    assert score([], now=100.0) == pytest.approx(0.0)


def test_retirada_por_estranho_gera_n3():
    bag = Bag(bag_id=2, anchor=Point(0.0, 0.0), owner_party=1)
    bag.state = BagState.RETIRADA_ESTRANHO

    flag = flag_for_removal(bag, carrier_track=99, t=42.0)

    assert flag is not None
    assert flag.level is FlagLevel.N3
    assert flag.person == 99
    assert "00:42" in flag.explanation
    assert "grupo 1" in flag.explanation


def test_retirada_de_bagagem_orfa_cita_a_ausencia_de_dono():
    bag = Bag(bag_id=7, anchor=Point(0.0, 0.0), owner_party=None)
    bag.state = BagState.RETIRADA_ESTRANHO

    flag = flag_for_removal(bag, carrier_track=99, t=90.0)

    assert flag is not None
    assert flag.level is FlagLevel.N3
    assert "sem dono identificado" in flag.explanation
    assert "01:30" in flag.explanation


def test_retirada_pelo_dono_nao_gera_flag():
    bag = Bag(bag_id=2, anchor=Point(0.0, 0.0), owner_party=1)
    bag.state = BagState.RETIRADA_DONO

    assert flag_for_removal(bag, carrier_track=1, t=42.0) is None


def test_bagagem_ambigua_nao_gera_flag():
    """Regra P3: incerteza suprime alerta, nunca gera."""
    bag = Bag(bag_id=2, anchor=Point(0.0, 0.0), owner_party=1)
    bag.state = BagState.AMBIGUA

    assert flag_for_removal(bag, carrier_track=99, t=42.0) is None


def test_flag_de_proximidade_e_n1_e_cita_a_duracao():
    bag = Bag(bag_id=2, anchor=Point(0.0, 0.0), owner_party=1)

    flag = flag_proximity(person=99, bag=bag, t=125.0, seconds_near=95.0)

    assert flag.level is FlagLevel.N1
    assert "95s" in flag.explanation
    assert "02:05" in flag.explanation


def test_flag_de_contato_e_n2_e_cita_o_grupo_dono():
    bag = Bag(bag_id=2, anchor=Point(0.0, 0.0), owner_party=7)

    flag = flag_contact(person=99, bag=bag, t=60.0)

    assert flag.level is FlagLevel.N2
    assert "grupo 7" in flag.explanation


def test_store_agrupa_flags_por_pessoa():
    store = FlagStore()
    store.add(make_flag(t=10.0, weight=1.0))
    store.add(Flag("outro", FlagLevel.N1, person=2, bag=3, t=10.0, weight=5.0,
                   explanation="x"))

    assert store.score(person=1, now=10.0) == pytest.approx(1.0)
    assert store.score(person=2, now=10.0) == pytest.approx(5.0)
    assert store.score(person=404, now=10.0) == pytest.approx(0.0)


def test_store_lista_flags_da_pessoa_em_ordem_cronologica():
    store = FlagStore()
    store.add(make_flag(t=30.0))
    store.add(make_flag(t=10.0))

    assert [f.t for f in store.for_person(1)] == [10.0, 30.0]


def test_store_lista_as_pessoas_com_flag():
    store = FlagStore()
    store.add(make_flag(t=10.0))
    store.add(Flag("k", FlagLevel.N1, person=5, bag=None, t=10.0, weight=1.0,
                   explanation="x"))

    assert set(store.people()) == {1, 5}
