"""Task 14a: party.py emite eventos com a evidência da decisão.

Cobre apenas a emissão em si -- as guardas que decidem aceitar ou recusar já
têm cobertura própria em `tests/test_party.py`. Aqui o que importa é: o
evento certo sai quando a decisão é aceita, nada sai quando é recusada, e a
evidência carrega os números reais que justificaram a promoção.
"""

from __future__ import annotations

import pytest

from custody_watch.events import EventKind, EventLog
from custody_watch.party import PartyManager
from custody_watch.types import Observation, Point


def track(
    track_id: int,
    points: list[tuple[float, float]],
    t0: float = 0.0,
    dt: float = 1.0,
) -> list[Observation]:
    """Track de uma pessoa.

    `t0` e `dt` existem porque tracks reais não começam juntos nem têm o mesmo
    comprimento: gente entra em cena depois, a projeção descarta pontos na linha
    do horizonte, o tracker perde e recupera. Um helper que sempre gerasse
    `t = índice` tornaria a confusão entre índice e tempo indetectável.
    """
    return [
        Observation(track_id=track_id, cls="person", position=Point(x, y), t=t0 + i * dt)
        for i, (x, y) in enumerate(points)
    ]


# --- form_on_arrival -----------------------------------------------------------


def test_form_on_arrival_emite_party_formed_com_membros_na_evidencia():
    manager = PartyManager()
    events = EventLog()

    party = manager.form_on_arrival([2, 1], t=5.0, events=events)

    formed = events.of_kind(EventKind.PARTY_FORMED)
    assert len(formed) == 1
    event = formed[0]
    assert event.t_start == 5.0
    assert event.t_end == 5.0
    assert event.subject is None
    assert event.bag is None
    assert event.party == party.party_id
    assert event.evidence == {"members": [1, 2]}


# --- join_weak -------------------------------------------------------------


def test_join_weak_aceito_emite_party_joined_weak():
    manager = PartyManager()
    events = EventLog()
    party = manager.form_on_arrival([1])

    aceito = manager.join_weak(party.party_id, track_id=99, t=12.0, events=events)

    assert aceito is True
    joined = events.of_kind(EventKind.PARTY_JOINED_WEAK)
    assert len(joined) == 1
    event = joined[0]
    assert event.t_start == 12.0
    assert event.t_end == 12.0
    assert event.subject == 99
    assert event.bag is None
    assert event.party == party.party_id
    assert event.evidence == {"party": party.party_id}


def test_join_weak_recusado_nao_emite():
    """Sentar perto de estranhos não pode tirar ninguém da própria família --
    e a tentativa recusada não deixa rastro no log."""
    manager = PartyManager()
    events = EventLog()
    familia = manager.form_on_arrival([1, 2])
    manager.form_on_arrival([3])

    recusado = manager.join_weak(familia.party_id, track_id=2, events=events)

    assert recusado is False
    assert len(events) == 0


# --- try_join_strong ---------------------------------------------------------


def test_try_join_strong_aceito_emite_party_joined_strong():
    manager = PartyManager()
    events = EventLog()
    party = manager.form_on_arrival([1])

    membro = track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)])
    candidato = track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)])

    promovido = manager.try_join_strong(party.party_id, 2, membro, candidato, events=events)

    assert promovido is True
    strong = events.of_kind(EventKind.PARTY_JOINED_STRONG)
    assert len(strong) == 1
    event = strong[0]
    assert event.subject == 2
    assert event.bag is None
    assert event.party == party.party_id


def test_try_join_strong_recusado_por_extensao_insuficiente_nao_emite():
    manager = PartyManager()
    events = EventLog()
    party = manager.form_on_arrival([1])

    curto_membro = track(1, [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
    curto_candidato = track(2, [(0.5, 0.0), (1.5, 0.0), (2.5, 0.0)])

    promovido = manager.try_join_strong(
        party.party_id, 2, curto_membro, curto_candidato, events=events
    )

    assert promovido is False
    assert len(events) == 0


def test_evidencia_do_vinculo_forte_tem_quatro_chaves_plausiveis():
    """Valores conferidos à mão a partir das trajetórias abaixo.

    Ambos os tracks têm passo de 1s em t=0,1,2 -- casamento por tempo pareia
    índice a índice. `extent_member_m` e `extent_candidate_m` são a diagonal
    da caixa que contém os três pontos de cada trajetória (6m de largura, 0 de
    altura, os dois casos); `overlap_s` é a janela de 0s a 2s; a separação em
    cada par é sempre 0.5m (deslocamento constante em x), logo o máximo é 0.5.
    """
    manager = PartyManager()
    events = EventLog()
    party = manager.form_on_arrival([1])

    membro = track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)])
    candidato = track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)])

    manager.try_join_strong(party.party_id, 2, membro, candidato, events=events)

    event = events.of_kind(EventKind.PARTY_JOINED_STRONG)[0]
    evidence = event.evidence

    assert set(evidence) == {
        "extent_member_m",
        "extent_candidate_m",
        "overlap_s",
        "max_separation_m",
    }
    assert evidence["extent_member_m"] == pytest.approx(6.0)
    assert evidence["extent_candidate_m"] == pytest.approx(6.0)
    assert evidence["overlap_s"] == pytest.approx(2.0)
    assert evidence["max_separation_m"] == pytest.approx(0.5)


def test_intervalo_do_vinculo_forte_cobre_janela_de_sobreposicao():
    manager = PartyManager()
    events = EventLog()
    party = manager.form_on_arrival([1])

    membro = track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)], t0=0.0, dt=1.0)
    candidato = track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)], t0=0.0, dt=1.0)

    manager.try_join_strong(party.party_id, 2, membro, candidato, events=events)

    event = events.of_kind(EventKind.PARTY_JOINED_STRONG)[0]
    assert event.t_start == membro[0].t
    assert event.t_end == membro[-1].t


# --- events=None não muda comportamento --------------------------------------


def test_events_none_nao_levanta_e_comportamento_e_identico():
    manager_sem = PartyManager()
    manager_com = PartyManager()

    party_sem = manager_sem.form_on_arrival([1], events=None)
    party_com = manager_com.form_on_arrival([1], events=EventLog())
    assert party_sem.members == party_com.members

    aceito_sem = manager_sem.join_weak(party_sem.party_id, 99, events=None)
    aceito_com = manager_com.join_weak(party_com.party_id, 99, events=EventLog())
    assert aceito_sem is True
    assert aceito_com is True

    membro = track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)])
    candidato = track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)])
    promovido_sem = manager_sem.try_join_strong(
        party_sem.party_id, 2, membro, candidato, events=None
    )
    promovido_com = manager_com.try_join_strong(
        party_com.party_id, 2, membro, candidato, events=EventLog()
    )
    assert promovido_sem is True
    assert promovido_com is True
