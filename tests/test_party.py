import random

import pytest

from custody_watch.party import PartyManager, is_comoving
from custody_watch.types import Bond, Observation, Point


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


def sentado(
    track_id: int,
    x: float,
    segundos: float = 10.0,
    fps: float = 25.0,
    jitter_m: float = 0.02,
    seed: int = 0,
) -> list[Observation]:
    """Pessoa imóvel, como um detector real a enxerga.

    Nenhum detector produz a mesma coordenada duas vezes. Testar imobilidade com
    `(0.0, 0.0)` repetido protege contra um cenário fisicamente impossível — e
    dois centímetros de jitter, um quarto de pixel no fundo da cena, bastavam
    para derrubar a guarda quando ela media caminho percorrido.
    """
    rng = random.Random(seed)
    amostras = int(segundos * fps)
    return [
        Observation(
            track_id=track_id,
            cls="person",
            position=Point(
                x + rng.uniform(-jitter_m, jitter_m),
                rng.uniform(-jitter_m, jitter_m),
            ),
            t=i / fps,
        )
        for i in range(amostras)
    ]


# --- is_comoving: a armadilha do zero -----------------------------------------


def test_pessoas_paradas_nao_estao_co_movendo():
    """Zero correlaciona perfeitamente com zero.

    Sem exigir extensão mínima, todos os sentados na praça de alimentação viram
    uma party única.
    """
    a = track(1, [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)])
    b = track(2, [(1.0, 0.0), (1.0, 0.0), (1.0, 0.0)])

    assert is_comoving(a, b) is False


def test_jitter_de_deteccao_nao_conta_como_deslocamento():
    """Duas pessoas sentadas, dez segundos, ruído realista de detector.

    Somando passo a passo, cada uma "percorre" cerca de nove metros sem sair do
    lugar. É assim que uma métrica de caminho entrega posse de bagagem a quem
    apenas sentou ao lado.
    """
    a = sentado(1, x=0.0, seed=7)
    b = sentado(2, x=1.0, seed=8)

    assert is_comoving(a, b) is False


def test_perambular_no_lugar_nao_conta_como_deslocamento():
    """Andar de um lado para o outro num raio de um metro não é ir a lugar algum.

    Sete segundos perambulando acumulam seis metros de caminho — mais que o
    limiar de entrada tardia — sem que ninguém saia de um círculo de dois
    metros.
    """
    vitima = track(
        1, [(0.0, 0.0), (0.1, 0.0), (0.2, 0.0), (0.3, 0.0), (0.2, 0.0), (0.1, 0.0), (0.0, 0.0)]
    )
    ladrao = track(
        2, [(0.5, 0.0), (1.5, 0.0), (0.5, 0.0), (1.5, 0.0), (0.5, 0.0), (1.5, 0.0), (0.5, 0.0)]
    )

    assert is_comoving(vitima, ladrao) is False

    manager = PartyManager()
    party = manager.form_on_arrival([1])
    assert manager.try_join_strong(party.party_id, 2, vitima, ladrao) is False
    assert party.owns(2) is False


def test_is_comoving_e_simetrica():
    a = track(1, [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (10.0, 0.0)], t0=0.0)
    b = track(2, [(0.2, 0.0), (1.2, 0.0), (2.2, 0.0)], t0=0.5)

    assert is_comoving(a, b) == is_comoving(b, a)


def test_apenas_um_parado_nao_e_co_movimento():
    """A geometria do ladrão passando pela vítima sentada.

    Cada track precisa da própria evidência de movimento — não basta um dos
    dois ter andado.
    """
    parado = track(1, [(0.0, 0.0), (0.05, 0.0), (0.0, 0.0)])
    andando = track(2, [(0.5, 0.0), (1.0, 0.0), (1.5, 0.0)])

    assert is_comoving(parado, andando) is False
    assert is_comoving(andando, parado) is False


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


# --- is_comoving: casamento por tempo -----------------------------------------


def test_janelas_de_tempo_disjuntas_nao_sao_co_movimento():
    """Percorrer o mesmo corredor um minuto depois não é andar junto.

    Casar por índice de lista compararia a primeira observação de cada um como
    se fossem simultâneas.
    """
    vitima = track(1, [(float(x), 0.0) for x in range(16)], t0=0.0)
    ladrao = track(2, [(float(x), 0.0) for x in range(16)], t0=60.0)

    assert is_comoving(vitima, ladrao) is False


def test_seguir_atras_com_entrada_tardia_nao_e_co_movimento():
    """Seguir a oito metros de distância não é andar junto.

    Quem vem atrás entra em cena depois e tem o track deslocado. Este é o caso
    comum, não a borda — e era classificado como co-movimento.
    """
    vitima = track(1, [(float(x), 0.0) for x in range(16)], t0=0.0)
    ladrao = track(2, [(float(x), 0.0) for x in range(8)], t0=8.0)

    posicao_da_vitima = {o.t: o.position for o in vitima}
    for observacao in ladrao:
        assert posicao_da_vitima[observacao.t].distance_to(observacao.position) == 8.0

    assert is_comoving(vitima, ladrao) is False


def test_sobreposicao_temporal_insuficiente_nao_e_co_movimento():
    a = track(1, [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)], t0=0.0)
    b = track(2, [(0.5, 0.0), (1.5, 0.0)], t0=2.5)

    assert is_comoving(a, b) is False


def test_amostras_esparsas_nao_sao_sobreposicao_continua():
    """Três fotografias separadas por um minuto não são co-movimento sustentado.

    Contagem de amostras não é duração. Como tracks fragmentados são o caso
    normal, um ladrão cujo track sobrevive nos três instantes em que passou
    perto da vítima receberia vínculo forte sem nunca ter andado com ela.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    vitima = track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)], t0=0.0, dt=60.0)
    ladrao = track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)], t0=0.0, dt=60.0)

    assert vitima[-1].t == 120.0
    assert is_comoving(vitima, ladrao) is False
    assert manager.try_join_strong(party.party_id, 2, vitima, ladrao) is False


def test_tolerancia_temporal_e_respeitada():
    """A guarda temporal precisa de teste próprio.

    Sem ele, afrouxar `tolerance_s` passaria despercebido — os outros testes
    falham por outras guardas antes de chegar nesta.
    """
    a = track(1, [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)], t0=0.0, dt=1.0)
    b = track(2, [(0.5, 0.0), (2.5, 0.0), (4.5, 0.0)], t0=0.4, dt=1.0)

    assert is_comoving(a, b, tolerance_s=0.5) is True
    assert is_comoving(a, b, tolerance_s=0.1) is False


def test_ida_e_volta_conta_como_deslocamento_conjunto():
    """Caminho percorrido, não deslocamento líquido.

    Um casal que anda vinte metros e volta ao mesmo banco percorreu quarenta
    metros colado. Medir de ponta a ponta daria zero e os separaria.
    """
    ida_volta = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (10.0, 0.0), (0.0, 0.0)]
    a = track(1, ida_volta)
    b = track(2, [(x + 0.5, y) for x, y in ida_volta])

    assert is_comoving(a, b) is True


# --- formação de grupo --------------------------------------------------------


def test_chegada_conjunta_forma_party_com_vinculo_forte():
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])

    assert party.owns(1) is True
    assert party.owns(2) is True


def test_form_on_arrival_rejeita_grupo_vazio():
    with pytest.raises(ValueError, match="ao menos um"):
        PartyManager().form_on_arrival([])


def test_form_on_arrival_rejeita_pessoa_ja_afiliada():
    """Uma pessoa pertence a no máximo um grupo.

    Sem isso, `_party_of` e `Party.members` divergem e `same_party_strong`
    passa a decidir com base em estado inconsistente.
    """
    manager = PartyManager()
    manager.form_on_arrival([1])

    with pytest.raises(ValueError, match="já pertencem"):
        manager.form_on_arrival([1, 2])


# --- vínculo fraco ------------------------------------------------------------


def test_proximidade_estatica_gera_apenas_vinculo_fraco():
    """Regra P1: sentar perto não transfere posse de bagagem."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])

    assert manager.join_weak(party.party_id, track_id=99) is True
    assert party.members[99] is Bond.WEAK
    assert party.owns(99) is False


def test_join_weak_nao_rouba_pessoa_de_outro_grupo():
    """Sentar perto de estranhos não pode tirar ninguém da própria família."""
    manager = PartyManager()
    familia = manager.form_on_arrival([1, 2])
    outro = manager.form_on_arrival([3])

    assert manager.join_weak(outro.party_id, track_id=2) is False
    assert 2 not in outro.members
    assert manager.party_of(2) == familia.party_id


# --- entrada tardia com vínculo forte -----------------------------------------


def test_entrada_tardia_exige_co_movimento_sustentado():
    """Vínculo forte tardio precisa de 5m de caminho percorrido junto.

    Assimetria intencional: quanto mais tarde o vínculo se forma, mais caro ele
    deve ser, porque é o caminho que um atacante exploraria.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])

    curto = manager.try_join_strong(
        party.party_id,
        2,
        track(1, [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]),
        track(2, [(0.5, 0.0), (1.5, 0.0), (2.5, 0.0)]),
    )
    assert curto is False
    assert party.owns(2) is False

    longo = manager.try_join_strong(
        party.party_id,
        2,
        track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)]),
        track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)]),
    )
    assert longo is True
    assert party.owns(2) is True


def test_vinculo_fraco_pode_ser_promovido_a_forte():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    manager.join_weak(party.party_id, 2)

    manager.try_join_strong(
        party.party_id,
        2,
        track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)]),
        track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)]),
    )

    assert party.members[2] is Bond.STRONG


def test_fuga_apos_a_janela_comum_nao_conta_como_deslocamento_conjunto():
    """Os frames em que o ladrão se afasta não podem contar como esforço junto.

    Medir o caminho sobre o track inteiro deixava a própria fuga satisfazer o
    requisito de deslocamento sustentado.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])

    vitima = track(1, [(0.0, 0.0), (0.3, 0.0), (0.6, 0.0)])
    fuga = [(2.0 * k, 0.0) for k in range(1, 11)]
    ladrao = track(2, [(0.5, 0.0), (1.0, 0.0), (1.5, 0.0), *fuga])

    assert manager.try_join_strong(party.party_id, 2, vitima, ladrao) is False
    assert party.owns(2) is False


def test_nao_se_promove_com_o_proprio_track():
    """`is_comoving(x, x)` é trivialmente verdadeiro — não pode virar posse."""
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    ladrao = track(2, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)])

    assert manager.try_join_strong(party.party_id, 2, ladrao, ladrao) is False
    assert party.owns(2) is False


def test_membro_forte_nao_se_auto_promove():
    """Exercita a guarda `member_id == track_id` de verdade.

    O teste do track alheio para antes dela, em `party.owns`.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    proprio = track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)])

    assert manager.try_join_strong(party.party_id, 1, proprio, proprio) is False


def test_membro_parado_nao_promove_candidato():
    """Os dois precisam ter coberto terreno, não só o candidato.

    Medir apenas o candidato deixaria passar quem andou ao lado de um membro
    que não saiu do lugar — e quem não saiu do lugar não estava indo a canto
    nenhum acompanhado.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    membro_parado = track(1, [(0.0, 0.0), (0.3, 0.0), (0.6, 0.0)])
    candidato = track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)])

    assert manager.try_join_strong(party.party_id, 2, membro_parado, candidato) is False
    assert party.owns(2) is False


def test_track_vazio_nao_promove():
    manager = PartyManager()
    party = manager.form_on_arrival([1])

    assert manager.try_join_strong(party.party_id, 2, [], []) is False


def test_track_com_ids_misturados_e_rejeitado():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    misturado = [
        Observation(track_id=1, cls="person", position=Point(0.0, 0.0), t=0.0),
        Observation(track_id=5, cls="person", position=Point(3.0, 0.0), t=1.0),
    ]

    with pytest.raises(ValueError, match="único track_id"):
        manager.try_join_strong(
            party.party_id, 2, misturado, track(2, [(0.5, 0.0), (3.5, 0.0)])
        )


def test_candidate_track_de_outro_id_e_rejeitado():
    manager = PartyManager()
    party = manager.form_on_arrival([1])

    with pytest.raises(ValueError, match="não pertence ao track_id"):
        manager.try_join_strong(
            party.party_id,
            2,
            track(1, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)]),
            track(77, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)]),
        )


def test_co_movimento_com_membro_fraco_nao_promove():
    """O vínculo não é transitivo a partir de membros fracos.

    Senão dois cúmplices bastam: um senta perto da vítima (custo zero, só
    WEAK), o outro anda ao lado dele longe dela, e vira dono da bagagem.
    """
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    manager.join_weak(party.party_id, 10)

    promovido = manager.try_join_strong(
        party.party_id,
        11,
        track(10, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)]),
        track(11, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)]),
    )

    assert promovido is False
    assert party.owns(11) is False


def test_co_movimento_nao_migra_pessoa_entre_grupos():
    """Andar ao lado de um estranho numa fila não dissolve a própria família."""
    manager = PartyManager()
    familia = manager.form_on_arrival([1, 2])
    outro = manager.form_on_arrival([3])

    migrou = manager.try_join_strong(
        outro.party_id,
        2,
        track(3, [(0.0, 0.0), (3.0, 0.0), (6.0, 0.0)]),
        track(2, [(0.5, 0.0), (3.5, 0.0), (6.5, 0.0)]),
    )

    assert migrou is False
    assert manager.party_of(2) == familia.party_id
    assert manager.same_party_strong(1, 2) is True


# --- consultas ----------------------------------------------------------------


def test_party_of_localiza_o_grupo_de_uma_pessoa():
    manager = PartyManager()
    party = manager.form_on_arrival([1, 2])

    assert manager.party_of(1) == party.party_id
    assert manager.party_of(404) is None


def test_same_party_strong_exige_vinculo_forte_nos_dois():
    manager = PartyManager()
    party = manager.form_on_arrival([1])
    manager.join_weak(party.party_id, 2)

    assert manager.same_party_strong(1, 2) is False

    casal = manager.form_on_arrival([3, 4])
    assert manager.same_party_strong(3, 4) is True
    assert casal.owns(3) is True


def test_same_party_strong_falso_para_grupos_diferentes():
    manager = PartyManager()
    manager.form_on_arrival([1])
    manager.form_on_arrival([2])

    assert manager.same_party_strong(1, 2) is False
    assert manager.same_party_strong(1, 404) is False
