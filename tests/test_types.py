import pytest

from custody_watch.types import Bag, BagState, Bond, Flag, FlagLevel, Party, Point


def test_distancia_euclidiana_em_metros():
    assert Point(0.0, 0.0).distance_to(Point(3.0, 4.0)) == pytest.approx(5.0)


def test_posse_so_flui_por_vinculo_forte():
    """Regra P1 do spec: vínculo fraco NÃO transfere posse."""
    party = Party(party_id=1, members={10: Bond.STRONG, 20: Bond.WEAK})

    assert party.owns(10) is True
    assert party.owns(20) is False
    assert party.owns(99) is False


def test_bagagem_sem_grupo_e_orfa():
    assert Bag(bag_id=1, anchor=Point(0.0, 0.0)).is_orphan is True
    assert Bag(bag_id=1, anchor=Point(0.0, 0.0), owner_party=7).is_orphan is False


def test_bagagem_nasce_no_estado_nova():
    assert Bag(bag_id=1, anchor=Point(0.0, 0.0)).state is BagState.NOVA


def test_niveis_de_flag_sao_ordenaveis():
    """A fila ranqueia por nível, então a ordem precisa ser total."""
    assert FlagLevel.N3 > FlagLevel.N2 > FlagLevel.N1


def test_flag_exige_explicacao():
    flag = Flag(
        kind="contato_bagagem_alheia",
        level=FlagLevel.N2,
        person=10,
        bag=5,
        t=120.0,
        weight=3.0,
        explanation="Tocou em bagagem do grupo 2 aos 2min00s.",
    )
    assert flag.explanation


def test_bagagem_nasce_visivel():
    """occluded_since e None enquanto o detector a enxerga. None nao e zero:
    zero seria um instante valido de inicio de oclusao."""
    bag = Bag(bag_id=1, anchor=Point(0.0, 0.0))

    assert bag.occluded_since is None
    assert bag.occlusion_candidates == set()


def test_candidatos_de_oclusao_nao_sao_compartilhados():
    """Erro classico de default mutavel: duas bagagens dividindo o mesmo set."""
    a = Bag(bag_id=1, anchor=Point(0.0, 0.0))
    b = Bag(bag_id=2, anchor=Point(5.0, 5.0))

    a.occlusion_candidates.add(99)

    assert b.occlusion_candidates == set()


def test_oclusao_nao_e_estado_de_bagagem():
    """Visibilidade e acompanhamento sao eixos independentes: update_attendance
    escreve bag.state todo frame, entao um BagState.OCLUIDA seria sobrescrito
    no frame seguinte. Uma bagagem pode estar desacompanhada E ocluida."""
    assert not any(estado.name == "OCLUIDA" for estado in BagState)
