"""A camada de ruído: pura, semeada, e testável sem rodar sessão nenhuma."""

import statistics as st
from dataclasses import replace

import pytest

from custody_watch.noise import NoiseModel, degrade
from custody_watch.tracking import TrackedDetection

LIMPO = NoiseModel()


def deteccao(track_id: int, cls: str, x: float = 100.0) -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls=cls, bbox=(x, 0.0, x + 20.0, 50.0))


def quadros(n: int, *deteccoes: TrackedDetection):
    for i in range(n):
        yield i / 25.0, list(deteccoes)


def test_ruido_zero_e_identidade():
    """Sem isso, toda célula da varredura carrega um viés que não foi pedido."""
    entrada = list(quadros(20, deteccao(1, "person"), deteccao(900, "suitcase")))

    saida = list(degrade(iter(entrada), person=LIMPO, bag=LIMPO, seed=7))

    assert saida == entrada


def test_falha_total_apaga_a_classe_certa():
    """O ruído de bagagem não pode mexer nas pessoas: a varredura isola um
    eixo por vez, e se os dois se misturarem a tabela não significa nada."""
    cego = NoiseModel(drop_rate=1.0, drop_burst_frames=1)

    saida = list(
        degrade(
            quadros(20, deteccao(1, "person"), deteccao(900, "suitcase")),
            person=LIMPO,
            bag=cego,
            seed=7,
        )
    )

    classes = {d.cls for _, det in saida for d in det}
    assert classes == {"person"}


def test_taxa_de_falha_pedida_e_a_taxa_obtida():
    """Rajadas de um quadro, para que a taxa seja diretamente comparável."""
    modelo = NoiseModel(drop_rate=0.3, drop_burst_frames=1)

    saida = list(
        degrade(quadros(4000, deteccao(900, "suitcase")), person=LIMPO, bag=modelo, seed=1)
    )

    presentes = sum(1 for _, det in saida if det)
    assert presentes / 4000 == pytest.approx(0.7, abs=0.03)


def test_rajada_alonga_a_ausencia():
    """Detector não pisca um quadro de cada vez: ele perde o objeto enquanto
    ele está pequeno ou ocluído, e volta depois. Uma falha independente por
    quadro produziria buracos de 40ms que a lógica nem sente."""
    curto = NoiseModel(drop_rate=0.02, drop_burst_frames=1)
    longo = NoiseModel(drop_rate=0.02, drop_burst_frames=25)

    def maior_buraco(modelo):
        saida = list(
            degrade(quadros(4000, deteccao(900, "suitcase")), person=LIMPO, bag=modelo, seed=3)
        )
        atual = maximo = 0
        for _, det in saida:
            atual = 0 if det else atual + 1
            maximo = max(maximo, atual)
        return maximo

    assert maior_buraco(longo) > maior_buraco(curto) * 3


def test_erro_de_posicao_tem_o_desvio_pedido():
    modelo = NoiseModel(position_sigma_px=4.0)

    saida = list(
        degrade(quadros(3000, deteccao(900, "suitcase")), person=LIMPO, bag=modelo, seed=5)
    )
    desvios = [d.bbox[0] - 100.0 for _, det in saida for d in det]

    assert st.mean(desvios) == pytest.approx(0.0, abs=0.5)
    assert st.pstdev(desvios) == pytest.approx(4.0, abs=0.5)


def test_erro_de_posicao_move_a_caixa_inteira():
    """Deslocar só uma borda mudaria o tamanho da caixa, e tamanho é o que o
    `ground_plane` usa para achar o pé. O erro tem que transladar."""
    modelo = NoiseModel(position_sigma_px=4.0)

    saida = list(degrade(quadros(50, deteccao(900, "suitcase")), person=LIMPO, bag=modelo, seed=5))
    larguras = {round(d.bbox[2] - d.bbox[0], 6) for _, det in saida for d in det}
    alturas = {round(d.bbox[3] - d.bbox[1], 6) for _, det in saida for d in det}

    assert larguras == {20.0}
    assert alturas == {50.0}


def test_troca_de_id_persiste():
    """Tracker que troca o id não volta atrás no quadro seguinte. Se voltasse,
    a religação de âncora nunca seria exercitada."""
    modelo = NoiseModel(id_switch_rate=1.0)

    saida = list(degrade(quadros(10, deteccao(900, "suitcase")), person=LIMPO, bag=modelo, seed=2))
    ids = [d.track_id for _, det in saida for d in det]

    assert ids[0] != 900
    assert len(set(ids)) == 1


def test_mesma_semente_reproduz():
    """Sem isso não dá para distinguir efeito de limiar de flutuação de ruído,
    e a varredura vira folclore."""
    modelo = NoiseModel(drop_rate=0.2, position_sigma_px=3.0, id_switch_rate=0.01)

    def rodar(seed):
        return [
            (t, tuple(det))
            for t, det in degrade(
                quadros(200, deteccao(900, "suitcase")), person=LIMPO, bag=modelo, seed=seed
            )
        ]

    primeira = rodar(11)
    segunda = rodar(11)
    outra = rodar(12)

    assert primeira == segunda
    assert primeira != outra


def test_aparencia_atravessa_intacta():
    """A assinatura de re-ID pertence à pessoa, não ao detector. Ruído de
    caixa não é ruído de aparência, e misturar os dois mediria outra coisa."""
    marcada = replace(deteccao(1, "person"), appearance=("assinatura",))
    modelo = NoiseModel(position_sigma_px=4.0)

    saida = list(degrade(quadros(10, marcada), person=modelo, bag=LIMPO, seed=4))

    assert all(d.appearance == ("assinatura",) for _, det in saida for d in det)
