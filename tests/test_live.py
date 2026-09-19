"""A sessão alimentada quadro a quadro, que é o que ao vivo exige.

`run_session` só devolve a fila quando o vídeo acaba, e numa câmera não
existe fim. A sessão incremental é o corpo daquele laço virando método — e
por isso o teste que importa é o de equivalência: alimentar quadro a quadro
tem que dar exatamente o que o lote dá.
"""

from custody_watch.config import Config
from custody_watch.events import EventKind
from custody_watch.orchestrator import LiveSession, run_session
from custody_watch.types import FlagLevel
from tests.test_occlusion import PLANO, cena_com_furto, some_e_nao_volta


def _alimenta(quadros, config=None) -> LiveSession:
    sessao = LiveSession(PLANO, config or Config())
    for t, tracked in quadros:
        sessao.feed(t, tracked)
    return sessao


def test_quadro_a_quadro_da_o_mesmo_resultado_que_o_lote():
    lote = run_session(cena_com_furto(), PLANO, Config())
    ao_vivo = _alimenta(cena_com_furto()).result()

    assert [e.kind for e in ao_vivo.events] == [e.kind for e in lote.events]
    assert [(i.person, i.top_level) for i in ao_vivo.queue] == [
        (i.person, i.top_level) for i in lote.queue
    ]
    assert ao_vivo.frames == lote.frames


def test_a_fila_existe_antes_do_fim():
    """É a razão de a classe existir: o operador vê o furto quando ele
    acontece, não quando alguém desliga a câmera."""
    sessao = LiveSession(PLANO, Config())
    viu_n3_em = None
    for t, tracked in cena_com_furto():
        sessao.feed(t, tracked)
        if viu_n3_em is None and any(i.top_level is FlagLevel.N3 for i in sessao.queue()):
            viu_n3_em = t

    assert viu_n3_em is not None
    assert viu_n3_em < sessao.t


def test_estado_corrente_para_desenhar():
    """A tela precisa saber, a cada quadro, quais bagagens são âncora e em que
    nível está cada pessoa -- sem reconstruir isso do log."""
    sessao = _alimenta(some_e_nao_volta())

    assert sessao.frames > 0
    assert sessao.events.of_kind(EventKind.BAG_APPEARED)
    assert all(b.bag_id for b in sessao.anchors())
    assert sessao.person_level(10**9) is None
