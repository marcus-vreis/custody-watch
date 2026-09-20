"""O operador aponta a bagagem que o detector não vê.

Medido no MEVA: nos 90s em que cada bolsa furtada fica parada antes do furto,
o detector produz **zero** caixa de bagagem sobre ela -- e nem modelo maior
nem recorte ampliado mudam isso (issue #51). Numa sala de monitoramento,
porém, existe alguém olhando a tela, e o que falta ali não é modelo melhor: é
a informação de que aquilo no chão é uma bagagem, e de quem ela é.

Apontar é asserção humana, registrada como tal no log. Não substitui o
detector em nada: a âncora apontada entra no mesmo registro, com as mesmas
regras -- desacompanhamento, contato de estranho, posse do grupo.
"""

import pytest

from custody_watch.config import Config
from custody_watch.events import EventKind
from custody_watch.orchestrator import LiveSession
from custody_watch.tracking import TrackedDetection
from custody_watch.types import FlagLevel
from tests.test_occlusion import PLANO, caixa

FPS = 25.0
DONO, ESTRANHO = 1, 2
ALTURA_PX = 170.0

LUGAR_M = (10.5, 10.0)
LUGAR_PX = (1050.0, 1000.0)
"""O mesmo ponto em pixels: o plano dos testes é 1px = 1cm."""


def _dono_x(t: float) -> float:
    """Fica ao lado da bagagem até 6s e depois se afasta a 1 m/s."""
    return 10.0 if t < 6.0 else min(10.0 + (t - 6.0), 25.0)


def _estranho_x(t: float) -> float:
    """Chega na bagagem aos 35s, fica encostado nela, e sai aos 45s."""
    if t < 35.0:
        return max(10.5 - (35.0 - t), 0.5)
    if t < 45.0:
        return 10.5
    return min(10.5 + (t - 45.0), 25.0)


def cena(duracao_s: float = 60.0, com_estranho: bool = False):
    """Ninguém detecta bagagem nenhuma: é exatamente o caso do MEVA."""
    for i in range(int(duracao_s * FPS)):
        t = i / FPS
        tracked = [TrackedDetection(DONO, "person", caixa(_dono_x(t), 10.0, ALTURA_PX))]
        if com_estranho and 25.0 <= t <= 55.0:
            tracked.append(
                TrackedDetection(ESTRANHO, "person", caixa(_estranho_x(t), 10.0, ALTURA_PX))
            )
        yield t, tracked


def _centro_da_pessoa(tracked, track_id: int) -> tuple[float, float]:
    det = next(d for d in tracked if d.track_id == track_id)
    x0, y0, x1, y1 = det.bbox
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _roda(duracao_s=60.0, com_estranho=False, com_dono=True, aponta_em=2.0, dono_em=None):
    """`dono_em` separa os dois cliques no tempo, que é o que acontece na tela:
    primeiro o operador aponta a bagagem, depois procura o dono."""
    sessao = LiveSession(PLANO, Config())
    apontada = None
    dono_apontado = False
    for t, tracked in cena(duracao_s, com_estranho):
        sessao.feed(t, tracked)
        if apontada is None and t >= aponta_em:
            apontada = sessao.aponta_bagagem(*LUGAR_PX)
        if com_dono and apontada is not None and not dono_apontado and t >= (dono_em or aponta_em):
            sessao.aponta_dono(apontada.bag_id, *_centro_da_pessoa(tracked, DONO))
            dono_apontado = True
    return sessao, apontada


def test_apontar_cria_ancora_no_lugar_apontado():
    sessao, bagagem = _roda(duracao_s=4.0, com_dono=False)

    assert bagagem in sessao.anchors()
    assert bagagem.anchor.x == pytest.approx(LUGAR_M[0], abs=0.05)
    assert bagagem.anchor.y == pytest.approx(LUGAR_M[1], abs=0.05)


def test_o_log_diz_que_foi_o_operador():
    """Asserção humana tem que ser distinguível de detecção na auditoria: é a
    diferença entre 'o sistema viu' e 'alguém disse que estava ali'."""
    sessao, bagagem = _roda(duracao_s=4.0, com_dono=False)

    nascimento = [
        e for e in sessao.events.of_kind(EventKind.BAG_APPEARED) if e.bag == bagagem.bag_id
    ]
    assert [e.evidence.get("origem") for e in nascimento] == ["operador"]


def test_ancora_apontada_nasce_orfa():
    """Apontar diz onde, não de quem. Dar posse a quem está mais perto é
    exatamente o ataque que a regra P1 fecha -- quem está ao lado da bagagem
    parada pode ser o ladrão sentado ao lado dela."""
    _, bagagem = _roda(duracao_s=4.0, com_dono=False)

    assert bagagem.owner_party is None


def test_apontar_o_dono_liga_a_bagagem_a_quem_esta_na_caixa():
    sessao, bagagem = _roda(duracao_s=4.0)

    assert bagagem.owner_party is not None
    assert bagagem.owner_party == sessao.party_of(DONO)
    posse = [e for e in sessao.events.of_kind(EventKind.BAG_OWNED) if e.bag == bagagem.bag_id]
    assert [e.evidence.get("origem") for e in posse] == ["operador"]


def test_apontar_o_dono_fora_de_qualquer_pessoa_recusa():
    """Sem ninguém no ponto não há a quem atribuir, e inventar um dono aqui
    seria fabricar a evidência que decide furto de retirada legítima."""
    sessao, bagagem = _roda(duracao_s=4.0, com_dono=False)

    with pytest.raises(ValueError, match="ninguém"):
        sessao.aponta_dono(bagagem.bag_id, 5.0, 5.0)

    assert bagagem.owner_party is None


def test_apontar_dono_de_bagagem_que_nao_existe_recusa():
    sessao, _ = _roda(duracao_s=4.0, com_dono=False)

    with pytest.raises(ValueError, match="bagagem"):
        sessao.aponta_dono(123456, *LUGAR_PX)


def test_bagagem_apontada_fica_desacompanhada_quando_o_dono_se_afasta():
    """O que apontar compra: a bagagem que o detector nunca viu passa a ser
    vigiada pelas mesmas regras das outras."""
    sessao, bagagem = _roda()

    desacompanhada = [
        e for e in sessao.events.of_kind(EventKind.BAG_UNATTENDED) if e.bag == bagagem.bag_id
    ]
    assert desacompanhada


def test_sem_dono_apontado_nao_ha_desacompanhamento():
    """Bagagem órfã não acumula tempo: não há dono cuja ausência signifique
    alguma coisa. Apontar só a bagagem não fabrica alarme."""
    sessao, bagagem = _roda(com_dono=False)

    assert not sessao.events.of_kind(EventKind.BAG_UNATTENDED)


def test_estranho_que_encosta_na_bagagem_apontada_entra_na_fila():
    sessao, _ = _roda(com_estranho=True)

    fila = sessao.queue()
    assert [i.person for i in fila] == [ESTRANHO]
    assert fila[0].top_level >= FlagLevel.N2
    assert "bagagem" in " ".join(fila[0].explanations)


def test_a_tela_sabe_onde_desenhar_a_apontada():
    """A apontada não tem caixa de detector: sem guardar o pixel, ela seria
    invisível justamente para quem a apontou."""
    sessao, bagagem = _roda(duracao_s=4.0, com_dono=False)

    assert sessao.apontadas() == {bagagem.bag_id: LUGAR_PX}


def test_apontar_demais_recusa():
    """Um cliente em laço criaria âncoras até o processo cair."""
    sessao = LiveSession(PLANO, Config())
    sessao.feed(0.0, [TrackedDetection(DONO, "person", caixa(10.0, 10.0, ALTURA_PX))])

    with pytest.raises(ValueError, match="apontadas"):
        for i in range(500):
            sessao.aponta_bagagem(1000.0 + i, 1000.0)


def test_silencio_do_detector_nao_declara_a_apontada_ambigua():
    """Ela foi apontada porque o detector não a vê. Ele continuar não vendo
    não é notícia: tratar a ausência como sumiço declararia AMBIGUA toda
    âncora apontada 30s depois do clique, e com ela morreriam o
    desacompanhamento e o contato de estranho."""
    sessao, bagagem = _roda()

    assert bagagem in sessao.anchors()
    assert not [
        e for e in sessao.events.of_kind(EventKind.BAG_AMBIGUOUS) if e.bag == bagagem.bag_id
    ]


def test_quando_o_detector_enfim_ve_a_bagagem_ela_volta_a_ser_uma_bagagem_comum():
    """Aí o silêncio dele volta a significar alguma coisa, e a mesma âncora
    segue -- com o dono e a história que já tinha, sem nascer de novo."""
    sessao, bagagem = _roda(duracao_s=4.0)
    vista = TrackedDetection(500, "suitcase", caixa(*LUGAR_M, 40.0))
    for i in range(50):
        sessao.feed(
            4.0 + i / FPS, [TrackedDetection(DONO, "person", caixa(10.0, 10.0, ALTURA_PX)), vista]
        )

    assert [b.bag_id for b in sessao.anchors()] == [bagagem.bag_id]
    assert not bagagem.apontada


def test_quem_vira_dono_deixa_de_ser_suspeito_de_tocar_na_propria_bagagem():
    """Entre apontar a bagagem e apontar o dono passam alguns segundos, e
    neles o dono está encostado numa bagagem que ainda não é de ninguém.

    Medido no MEVA com a tela ao vivo: a dona da bolsa entrou na fila em
    primeiro lugar, por tocar na própria bolsa. Flag contra quem se revela
    dono não é evidência de nada.
    """
    sessao, _ = _roda(duracao_s=10.0, aponta_em=2.0, dono_em=5.0)

    assert sessao.queue() == []


def test_clique_em_caixas_sobrepostas_escolhe_a_menor():
    """Aconteceu no MEVA: a mesma mulher sentada saiu em duas caixas quase
    iguais, uma com as pernas e outra sem, e o clique no meio caía nas duas.
    A menor é a que quem clicou estava enxergando.

    O preço, que é do detector e não daqui: a caixa duplicada não vira dona, e
    segue tratada como estranha se encostar na bagagem.
    """
    sessao = LiveSession(PLANO, Config())
    grande = TrackedDetection(1, "person", (900.0, 580.0, 1250.0, 900.0))
    pequena = TrackedDetection(2, "person", (950.0, 580.0, 1200.0, 810.0))
    sessao.feed(0.0, [grande, pequena])
    bagagem = sessao.aponta_bagagem(*LUGAR_PX)

    sessao.aponta_dono(bagagem.bag_id, 1075.0, 700.0)

    assert bagagem.owner_party == sessao.party_of(pequena.track_id)
