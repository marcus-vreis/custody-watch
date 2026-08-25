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
from custody_watch.types import FlagLevel

FPS = 25.0
BAG, DONO, ESTRANHO = 900, 1, 2
PASSANTE, LADRAO = 3, 4

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


def test_bagagem_readotada_fica_estavel_ate_o_fim_do_video():
    """`seen`/`missing` em `observe_bags` têm que ser chaveados por `bag_id`,
    não por `track_id`. Depois da readoção o track novo (901) responde pelo
    `bag_id` antigo (900) — se `seen`/`missing` usassem 901, a bagagem, que
    está perfeitamente visível, nunca apareceria em `seen` (só 900 é
    procurado), então cada frame a conta de `missing` para 900 estoura de
    novo, abre uma oclusão, e o próprio `observe_bags` fecha essa oclusão no
    frame seguinte (porque `get_by_track(901)` resolve para o bag 900 já
    religado) — um ciclo de abre-fecha a cada `missing_frames_before_occluded`
    quadros, pelo resto do vídeo. Estendendo a cena para 80s (bem além de
    `max_occlusion_s`) para dar tempo do ciclo se manifestar."""
    resultado = run_session(cena(1.0, bag_id_apos=901, duracao_s=80.0), PLANO, Config())

    assert len(tipos(resultado, EventKind.BAG_OCCLUDED)) == 1


def test_bagagem_que_nao_volta_resolve_com_quem_esteve_nela():
    """Quem responde é quem esteve ao alcance DURANTE a ausência, não quem
    está mais perto quando o timeout estoura — a essa altura o ladrão já saiu
    de perto e quem sobrou é um inocente qualquer, ou ninguém."""
    resultado = run_session(some_e_nao_volta(), PLANO, Config())

    (furto,) = tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER)
    assert furto.subject == ESTRANHO

    # Carregada, não redundante: `update_attendance` precisa rodar mesmo
    # enquanto a bagagem está oclusa (invisível), contra a âncora congelada.
    # Se ela só rodar para bagagem vista (`if bag.bag_id in seen:`), o
    # cronômetro de 25s desacompanhada congela durante a ausência e este
    # BAG_UNATTENDED, que nasce ANTES do furto nesta mesma cena, some.
    assert len(tipos(resultado, EventKind.BAG_UNATTENDED)) == 1


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


DURACAO_DOIS_EPISODIOS = 40.0 + Config().custody.max_occlusion_s + 15.0


def dois_episodios(duracao_s: float = DURACAO_DOIS_EPISODIOS):
    """Dono larga a bagagem aos 4s e sai. Dois episódios de oclusão, com
    desfechos opostos.

    Aos 10s, `PASSANTE` cruza em frente à bagagem por 0.4s e sai de cena — a
    bagagem volta a ser vista sem incidente, episódio encerrado. Aos 40s,
    `LADRAO` para na frente da bagagem e ela nunca mais reaparece,
    ultrapassando `max_occlusion_s`: é esse segundo episódio que precisa
    resolver, e só com `LADRAO` como candidato.
    """
    for i in range(int(duracao_s * FPS)):
        t = i / FPS
        det = [TrackedDetection(DONO, "person", caixa(dono_x(t), 11.0, 170))]

        if t < 4.0:
            det.append(TrackedDetection(BAG, "suitcase", caixa(6.0 + t, 11.0, 55)))
            yield t, det
            continue

        ocluida = False
        if 10.0 <= t < 10.4:
            det.append(TrackedDetection(PASSANTE, "person", caixa(10.0, 9.8, 170)))
            ocluida = True
        if t >= 40.0:
            det.append(TrackedDetection(LADRAO, "person", caixa(10.0, 9.8, 170)))
            ocluida = True

        if not ocluida:
            det.append(TrackedDetection(BAG, "suitcase", caixa(10.0, 10.0, 55)))

        yield t, det


def test_candidatos_de_oclusao_nao_vazam_entre_episodios():
    """`occlusion_candidates` tem que esvaziar quando uma oclusão termina.

    Sem isso, o `PASSANTE` inocente do primeiro episódio (aos 10s, que só
    ocluiu e foi embora) continuaria na lista quando o segundo episódio (o
    `LADRAO`, aos 40s) estourasse o timeout. Ali `resolve_occlusion_timeout`
    veria dois candidatos onde só há um culpado, e a regra de `len != 1`
    rebaixaria um furto real para AMBIGUA -- a pior direção possível: um vazio
    de estado transformando um caso resolvível em alerta suprimido."""
    resultado = run_session(dois_episodios(), PLANO, Config())

    furtos = tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER)
    assert len(furtos) == 1
    assert furtos[0].subject == LADRAO
    assert tipos(resultado, EventKind.BAG_AMBIGUOUS) == []


BAG_A, BAG_B, NOVO = 910, 911, 920


def duas_perto_ocluidas():
    """Duas bagagens a 0.3m uma da outra -- dentro do limiar de movimento
    default de 0.5m, que é o raio de busca de `adopt_occluded` -- somem
    juntas. Depois de ocluídas as duas, uma detecção com track novo reaparece
    exatamente no meio do caminho entre elas: candidata a ambas, e não há
    informação que desempate."""
    for i in range(75):
        t = i / FPS
        det = []
        if t < 1.0:
            det.append(TrackedDetection(BAG_A, "suitcase", caixa(10.0, 10.0, 55)))
            det.append(TrackedDetection(BAG_B, "suitcase", caixa(10.3, 10.0, 55)))
        elif t < 2.0:
            pass  # janela de ausência: as duas somem ao mesmo tempo
        else:
            det.append(TrackedDetection(NOVO, "suitcase", caixa(10.15, 10.0, 55)))
        yield t, det


def test_readocao_com_mais_de_uma_candidata_marca_ambigua_e_nao_adota():
    """Regra P3 aplicada em `adopt_occluded`: com mais de uma candidata no
    raio, chutar qual é qual corrompe o mapa de posse. A única saída correta
    é marcar as vizinhas como AMBIGUA e não religar o track novo a bagagem
    nenhuma -- as duas metades da mesma decisão, e as duas ficam sem teste
    até este."""
    resultado = run_session(duas_perto_ocluidas(), PLANO, Config())

    religacoes_de_bagagem = [
        e for e in tipos(resultado, EventKind.TRACK_RELINKED) if e.bag is not None
    ]
    assert religacoes_de_bagagem == []
    assert len(tipos(resultado, EventKind.BAG_AMBIGUOUS)) == 1


LADRAO = 3


def cena_com_furto(duracao_s: float = 70.0):
    """Depois da passagem inocente aos 32s, um segundo estranho chega aos 45s
    e leva a bagagem embora aos 50s, EM PLENA VISTA. O detector nunca perde a
    bagagem durante o furto."""
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
            ocluida = 31.5 <= t < 32.5

        mala_x, mala_y = 10.0, 10.0
        if t >= 45.0:
            lx = min(6.0 + (t - 45.0), 10.0) if t < 50.0 else 10.0 + (t - 50.0) * 1.5
            det.append(TrackedDetection(LADRAO, "person", caixa(lx, 10.2, 170)))
            if t >= 50.0:
                mala_x, mala_y = lx, 10.2

        if not ocluida:
            det.append(TrackedDetection(BAG, "suitcase", caixa(mala_x, mala_y, 55)))

        yield t, det


def test_furto_em_plena_vista_e_visto():
    """`has_moved` existia, era testado, e nunca era chamado por `src/`. A
    remoção só era detectada por DESAPARECIMENTO, então uma bagagem carregada
    embora continuava sendo detectada todo frame, nunca entrava no caminho, e
    `observe()` movia a âncora junto com o ladrão."""
    resultado = run_session(cena_com_furto(), PLANO, Config())

    (furto,) = tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER)
    assert furto.subject == LADRAO
    assert furto.t_start == pytest.approx(50.0, abs=1.0)


def test_a_fila_ranqueia_o_ladrao_acima_do_inocente():
    """O defeito que importa. A saída do sistema é uma ordem de prioridade
    para um operador humano, e ela estava invertida: medido, a fila continha
    só o passante inocente, em N3, e o ladrão não aparecia nela.

    O passante continua na fila, e deve continuar: ele passou a menos de 80cm
    da bagagem, e isso é um fato relacional verdadeiro — a regra P4 diz que é
    disso que um flag trata. O que não pode existir é acusação de custódia
    contra ele. Exigir que ele suma da fila seria pedir ao sistema que
    esquecesse uma observação real.
    """
    resultado = run_session(cena_com_furto(), PLANO, Config())

    fila = {item.person: item for item in resultado.queue}

    assert resultado.queue[0].person == LADRAO
    assert fila[LADRAO].top_level is FlagLevel.N3
    assert fila[ESTRANHO].top_level is FlagLevel.N2


def test_ancora_nao_caminha_com_quem_leva():
    """A âncora marca ONDE a custódia foi perdida, e é esse ponto que o
    recorte de clipe mostra ao operador. Se ela seguir a bagagem, o clipe
    aponta para onde o ladrão estava ao sair de cena, não para o furto.

    A bagagem foi largada em (10, 10) e o ladrão sai andando dali.
    """
    resultado = run_session(cena_com_furto(), PLANO, Config())

    (furto,) = tipos(resultado, EventKind.BAG_REMOVED_BY_STRANGER)
    assert furto.evidence["anchor"] == pytest.approx([10.0, 10.0], abs=0.01)


def test_custodia_nao_e_dada_por_restaurada_com_a_bagagem_indo_embora():
    """Medido antes deste conserto: o ladrão carregava a bagagem NA DIREÇÃO do
    dono, a âncora ia junto, a distância caía abaixo dos 3m e o sistema emitia
    BAG_REATTENDED — dava a custódia por restaurada durante o furto."""
    resultado = run_session(cena_com_furto(), PLANO, Config())

    assert tipos(resultado, EventKind.BAG_REATTENDED) == []


def test_carry_away_nao_inunda_flags_por_quadro():
    """Medido antes deste conserto: sem a guarda de estado terminal no topo de
    `carry_away`, `flag_for_removal` rodava a cada quadro enquanto o ladrão se
    afastava com a bagagem em vista -- 738 flags de retirada para um furto de
    ~30s de duração, inflando o score do alerta para 7253 (proporcional à
    duração do furto, não à gravidade dele). Com a guarda, exatamente um flag
    de retirada por furto."""
    resultado = run_session(cena_com_furto(), PLANO, Config())

    flags_de_retirada = [
        f for f in resultado.flags.for_person(LADRAO) if f.kind == "retirada_por_estranho"
    ]
    assert len(flags_de_retirada) == 1
