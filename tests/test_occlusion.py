"""Cenários de custódia sob oclusão, ponta a ponta contra run_session.

Trajetórias sintéticas, escritas à mão. Elas provam que a LÓGICA decide
certo — não que a percepção enxerga. São os cenários que expuseram o
defeito: nenhum dataset público contém alguém parado na frente de uma
bagagem por tempo suficiente.
"""

from collections import Counter

import numpy as np
import pytest

from custody_watch.config import Config
from custody_watch.events import EventKind
from custody_watch.ground_plane import GroundPlane
from custody_watch.orchestrator import run_session
from custody_watch.tracking import TrackedDetection

FPS = 25.0
BAG, DONO, ESTRANHO = 900, 1, 2

# Câmera de cima, 1px = 1cm. Homografia trivial mantém o teste legível:
# o que está sob teste é a lógica de custódia, não a projeção.
PLANO = GroundPlane(np.diag([0.01, 0.01, 1.0]))


def caixa(x_m: float, y_m: float, altura_px: float):
    """Caixa cujo pé cai em (x_m, y_m)."""
    cx, base = x_m * 100.0, y_m * 100.0
    return (cx - altura_px / 4, base - altura_px, cx + altura_px / 4, base)


def dono_x(t: float) -> float:
    """Chega com a bagagem, larga aos 4s, e vai embora entre 6s e 14s.

    O dono PRECISA ir embora, senão a bagagem nunca fica desacompanhada — a
    1m de distância contra um limiar de 3m, ela seguiria ACOMPANHADA o vídeo
    inteiro e metade dos cenários não testaria nada.
    """
    if t < 4.0:
        return 6.0 + t
    if t < 6.0:
        return 10.0
    return min(10.0 + (t - 6.0) * 1.5, 22.0)


def cena(oclusao_s: float, *, bag_id_apos: int = BAG, duracao_s: float = 60.0):
    """Dono larga a bagagem aos 4s e sai. Estranho cruza a cena entre 30s e
    36s e passa em frente à bagagem aos 32s, ocluindo-a por `oclusao_s`.

    A bagagem NUNCA sai do lugar. `oclusao_s=0.0` é o controle: mesma cena,
    sem oclusão nenhuma.
    """
    meia = oclusao_s / 2.0
    for i in range(int(duracao_s * FPS)):
        t = i / FPS
        det = [TrackedDetection(DONO, "person", caixa(dono_x(t), 11.0, 170))]

        if t < 4.0:
            det.append(TrackedDetection(BAG, "suitcase", caixa(6.0 + t, 11.0, 55)))
            yield t, det
            continue

        ocluida = False
        if 30.0 <= t <= 36.0:
            det.append(TrackedDetection(ESTRANHO, "person", caixa(6.0 + (t - 30) * 2, 9.5, 170)))
            ocluida = 32.0 - meia <= t < 32.0 + meia

        if not ocluida:
            bid = BAG if t < 32.0 else bag_id_apos
            det.append(TrackedDetection(bid, "suitcase", caixa(10.0, 10.0, 55)))

        yield t, det


def tipos(resultado, kind):
    return [e for e in resultado.events if e.kind is kind]


def contagem(resultado, exceto=()):
    return Counter(e.kind for e in resultado.events if e.kind not in exceto)


@pytest.mark.parametrize("oclusao_s", [0.2, 0.5, 1.0, 2.0])
def test_passar_na_frente_nao_e_furto(oclusao_s):
    """O defeito medido: a 0.2s de oclusão — 5 quadros a 25fps — o sistema
    declarava a bagagem retirada por quem estava mais perto, que é exatamente
    quem estava ocluindo. A bagagem nunca saiu do lugar em nenhuma das
    parametrizações."""
    resultado = run_session(cena(oclusao_s), PLANO, Config())

    assert tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER) == []
    assert tipos(resultado, EventKind.BAG_REMOVED_BY_OWNER) == []


def test_oclusao_nao_apaga_o_abandono_verdadeiro():
    """Efeito colateral medido do mesmo defeito: a bagagem virava terminal aos
    32s, antes de o limiar de 25s completar, então o BAG_UNATTENDED real nunca
    era emitido. A oclusão não só inventava um furto — ela apagava o único
    evento verdadeiro da cena."""
    controle = run_session(cena(0.0), PLANO, Config())
    ocluida = run_session(cena(2.0), PLANO, Config())

    assert len(tipos(controle, EventKind.BAG_UNATTENDED)) == 1
    assert len(tipos(ocluida, EventKind.BAG_UNATTENDED)) == 1


def test_oclusao_nao_muda_o_desfecho():
    """A afirmação mais forte que dá para fazer: com a bagagem parada, ser
    ocluída ou não ser NÃO PODE mudar o que o sistema conclui. A oclusão é uma
    propriedade da câmera, não da cena."""
    controle = run_session(cena(0.0), PLANO, Config())
    ocluida = run_session(cena(1.0), PLANO, Config())

    assert contagem(ocluida, exceto={EventKind.BAG_OCCLUDED}) == contagem(controle)


DURACAO_ATE_TIMEOUT = 4.0 + 30.0 + Config().custody.max_occlusion_s + 10.0


def some_e_nao_volta(duracao_s: float = DURACAO_ATE_TIMEOUT):
    """Estranho chega aos 30s, para em frente à bagagem, e ela nunca mais é
    detectada. Ele sai de cena aos 40s — ANTES do timeout de oclusão, que cai
    por volta dos 62s.

    É essa saída que torna o teste discriminante: no instante do timeout não
    há ninguém ao alcance, então "quem está mais perto agora" não tem
    resposta. Só `occlusion_candidates` sabe quem esteve lá.
    """
    for i in range(int(duracao_s * FPS)):
        t = i / FPS
        det = [TrackedDetection(DONO, "person", caixa(dono_x(t), 11.0, 170))]
        if t < 4.0:
            det.append(TrackedDetection(BAG, "suitcase", caixa(6.0 + t, 11.0, 55)))
            yield t, det
            continue
        if 30.0 <= t <= 40.0:
            det.append(TrackedDetection(ESTRANHO, "person", caixa(10.0, 9.8, 170)))
        if t < 32.0:
            det.append(TrackedDetection(BAG, "suitcase", caixa(10.0, 10.0, 55)))
        yield t, det


def multidao(duracao_s: float = DURACAO_ATE_TIMEOUT):
    """Duas pessoas param em frente à bagagem aos 30s e ela some. Não há como
    saber qual das duas a levou."""
    for i in range(int(duracao_s * FPS)):
        t = i / FPS
        det = [TrackedDetection(DONO, "person", caixa(dono_x(t), 11.0, 170))]
        if t < 4.0:
            det.append(TrackedDetection(BAG, "suitcase", caixa(6.0 + t, 11.0, 55)))
            yield t, det
            continue
        if t >= 30.0:
            det.append(TrackedDetection(5, "person", caixa(10.3, 10.0, 170)))
            det.append(TrackedDetection(6, "person", caixa(9.7, 10.0, 170)))
        else:
            det.append(TrackedDetection(BAG, "suitcase", caixa(10.0, 10.0, 55)))
        yield t, det


def test_bagagem_que_volta_emite_o_intervalo_de_oclusao():
    """A oclusão vira registro auditável: quanto tempo invisível e quem esteve
    nela. Intervalo, não instante — `Event` já é intervalo."""
    resultado = run_session(cena(1.0), PLANO, Config())

    (evento,) = tipos(resultado, EventKind.BAG_OCCLUDED)
    assert evento.duration_s == pytest.approx(1.0, abs=0.3)
    assert ESTRANHO in evento.evidence["candidates"]


def test_bagagem_que_volta_com_id_novo_e_readotada():
    """O tracker devolve a bagagem com `track_id` novo — foi o que a medição
    mostrou. Sem readoção ela vira "bagagem nova" com posse reatribuída por
    proximidade, e o histórico se perde."""
    resultado = run_session(cena(1.0, bag_id_apos=901), PLANO, Config())

    assert len(tipos(resultado, EventKind.BAG_APPEARED)) == 1
    religacoes = [e for e in tipos(resultado, EventKind.TRACK_RELINKED) if e.bag is not None]
    assert len(religacoes) == 1
    assert religacoes[0].evidence["from_track"] == 901


def test_bagagem_que_nao_volta_resolve_com_quem_esteve_nela():
    """Quem responde é quem esteve ao alcance DURANTE a ausência, não quem
    está mais perto quando o timeout estoura — a essa altura o ladrão já saiu
    de perto e quem sobrou é um inocente qualquer, ou ninguém."""
    resultado = run_session(some_e_nao_volta(), PLANO, Config())

    (furto,) = tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER)
    assert furto.subject == ESTRANHO


def test_a_decisao_espera_o_timeout():
    """Antes deste trabalho o evento saía aos ~32.2s, 0.2s depois do sumiço.
    Agora espera `max_occlusion_s`: é a diferença entre decidir com informação
    e decidir sem ela."""
    config = Config()
    resultado = run_session(some_e_nao_volta(), PLANO, config)

    (furto,) = tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER)
    assert furto.t_start == pytest.approx(32.0 + config.custody.max_occlusion_s, abs=1.0)


def test_varios_candidatos_suprimem_em_vez_de_acusar():
    """Regra P3: incerteza suprime, nunca gera. Uma bagagem que some atrás de
    duas pessoas não produz dois suspeitos nem escolhe um a esmo."""
    resultado = run_session(multidao(), PLANO, Config())

    assert tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER) == []
    assert len(tipos(resultado, EventKind.BAG_AMBIGUOUS)) == 1
