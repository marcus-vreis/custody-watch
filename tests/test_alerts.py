import pytest

from custody_watch.alerts import AlertQueue, build_queue
from custody_watch.flags import TAU_S, FlagStore
from custody_watch.types import Flag, FlagLevel


def flag(person: int, level: FlagLevel, weight: float, t: float = 0.0) -> Flag:
    return Flag(
        kind="k",
        level=level,
        person=person,
        bag=1,
        t=t,
        weight=weight,
        explanation=f"pessoa {person}",
    )


def test_fila_ordena_por_score_decrescente():
    store = FlagStore()
    store.add(flag(1, FlagLevel.N2, weight=3.0))
    store.add(flag(2, FlagLevel.N3, weight=10.0))

    queue = build_queue(store, now=0.0)

    assert [item.person for item in queue] == [2, 1]


def test_n1_sozinho_nao_entra_na_fila():
    """N1 acumula contexto mas não consome tempo de operador."""
    store = FlagStore()
    store.add(flag(1, FlagLevel.N1, weight=1.0))

    assert build_queue(store, now=0.0) == []


def test_n1_acompanhado_de_n2_entra_e_soma_no_score():
    store = FlagStore()
    store.add(flag(1, FlagLevel.N1, weight=1.0))
    store.add(flag(1, FlagLevel.N2, weight=3.0))

    queue = build_queue(store, now=0.0)

    assert len(queue) == 1
    assert queue[0].score == 4.0


def test_store_vazio_produz_fila_vazia():
    assert build_queue(FlagStore(), now=0.0) == []


def test_item_carrega_todas_as_explicacoes():
    store = FlagStore()
    store.add(flag(1, FlagLevel.N2, weight=3.0, t=10.0))
    store.add(flag(1, FlagLevel.N3, weight=10.0, t=20.0))

    item = build_queue(store, now=20.0)[0]

    assert len(item.explanations) == 2
    assert item.top_level is FlagLevel.N3


def test_decaimento_reordena_a_fila():
    """Um flag grave mas antigo cede lugar a um mais leve e recente."""
    store = FlagStore()
    store.add(flag(1, FlagLevel.N2, weight=10.0, t=0.0))
    store.add(flag(2, FlagLevel.N2, weight=5.0, t=TAU_S))

    queue = build_queue(store, now=TAU_S)

    assert [item.person for item in queue] == [2, 1]
    assert queue[1].score == pytest.approx(10.0 / 2.718281828, rel=1e-4)


def test_janela_de_clipe_cobre_o_flag_mais_grave():
    store = FlagStore()
    store.add(flag(1, FlagLevel.N3, weight=10.0, t=100.0))

    item = build_queue(store, now=100.0, clip_margin_s=10.0)[0]

    assert item.clip_start == 90.0
    assert item.clip_end == 110.0


def test_clip_start_nunca_e_negativo():
    store = FlagStore()
    store.add(flag(1, FlagLevel.N3, weight=10.0, t=3.0))

    assert build_queue(store, now=3.0, clip_margin_s=10.0)[0].clip_start == 0.0


def test_clipe_ancora_no_flag_mais_grave_nao_no_mais_recente():
    """O operador precisa ver o evento de custódia, não o ruído posterior."""
    store = FlagStore()
    store.add(flag(1, FlagLevel.N3, weight=10.0, t=100.0))
    store.add(flag(1, FlagLevel.N2, weight=3.0, t=500.0))

    item = build_queue(store, now=500.0, clip_margin_s=10.0)[0]

    assert item.clip_start == 90.0
    assert item.clip_end == 110.0


def test_alert_queue_limita_ao_orcamento_do_operador():
    store = FlagStore()
    for person in range(10):
        store.add(flag(person, FlagLevel.N2, weight=float(person)))

    queue = AlertQueue(store).top(now=0.0, limit=3)

    assert [item.person for item in queue] == [9, 8, 7]
