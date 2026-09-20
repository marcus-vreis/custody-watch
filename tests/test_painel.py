"""O que a tela ao vivo mostra: a fila, os eventos e o quadro anotado."""

import json

import numpy as np

from custody_watch.config import Config
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel, desenha, estado
from custody_watch.tracking import TrackedDetection
from tests.test_occlusion import PLANO, cena_com_furto


def _sessao_com_furto() -> LiveSession:
    sessao = LiveSession(PLANO, Config())
    for t, tracked in cena_com_furto():
        sessao.feed(t, tracked)
    return sessao


def test_estado_traz_a_fila_ranqueada_com_explicacao():
    """O operador precisa saber quem, com que gravidade, e por quê -- a mesma
    frase que a página de revisão mostra."""
    atual = estado(_sessao_com_furto())

    primeiro = atual["fila"][0]
    assert primeiro["nivel"] == "N3"
    assert primeiro["posicao"] == 1
    assert primeiro["explicacoes"]
    assert "retirada" in " ".join(primeiro["explicacoes"])


def test_estado_e_serializavel_em_json():
    """A tela lê por HTTP. Um valor que não vira JSON derruba a página inteira
    no meio de um turno."""
    json.dumps(estado(_sessao_com_furto()))


def test_estado_traz_eventos_recentes_do_mais_novo_para_o_mais_antigo():
    atual = estado(_sessao_com_furto(), eventos_max=5)

    tempos = [e["t"] for e in atual["eventos"]]
    assert len(tempos) <= 5
    assert tempos == sorted(tempos, reverse=True)


def test_estado_traz_o_relogio_da_sessao():
    sessao = _sessao_com_furto()

    atual = estado(sessao)

    assert atual["t"] == sessao.t
    assert atual["quadros"] == sessao.frames


def test_quadro_anotado_marca_quem_esta_na_fila():
    """Na tela, a cor é o que decide para onde o operador olha primeiro."""
    sessao = _sessao_com_furto()
    ladrao = sessao.queue()[0].person
    quadro = np.zeros((1200, 1600, 3), np.uint8)
    caixa = TrackedDetection(ladrao, "person", (300.0, 300.0, 400.0, 600.0))

    anotado = desenha(quadro, [caixa], sessao)

    assert anotado.shape == quadro.shape
    vermelho = anotado[300, 350]
    assert vermelho[2] > 150
    assert vermelho[1] < 100


def test_painel_guarda_o_ultimo_quadro_e_o_ultimo_estado():
    painel = Painel()
    assert painel.jpeg() is None

    painel.atualiza(np.zeros((40, 60, 3), np.uint8), {"t": 1.0})

    assert painel.jpeg().startswith(b"\xff\xd8")
    assert painel.estado()["t"] == 1.0


def test_eventos_da_tela_sao_so_os_de_custodia():
    """Religar trilha e formar grupo são contabilidade interna. No vídeo do
    portão, 13 dos 20 eventos recentes eram religações de trilha: a lista que
    devia mostrar o furto mostrava ruído."""
    atual = estado(_sessao_com_furto(), eventos_max=1000)

    tipos = {e["tipo"] for e in atual["eventos"]}
    assert "bag_removed_by_stranger" in tipos
    assert all(tipo.startswith("bag_") for tipo in tipos)


def _espessura_da_borda(quadro, x: int, y0: int, y1: int) -> int:
    return sum(1 for y in range(y0, y1) if quadro[y, x].any())


def test_caixa_engrossa_com_a_resolucao():
    """A 1080p, uma borda de três pixels some quando a tela mostra o quadro
    reduzido: no primeiro clipe MEVA na tela não dava para ver caixa nenhuma."""
    sessao = _sessao_com_furto()
    caixa = TrackedDetection(sessao.queue()[0].person, "person", (300.0, 300.0, 400.0, 600.0))

    pequeno = desenha(np.zeros((360, 640, 3), np.uint8), [caixa], sessao)
    grande = desenha(np.zeros((1080, 1920, 3), np.uint8), [caixa], sessao)

    fina = _espessura_da_borda(pequeno, 350, 290, 330)
    grossa = _espessura_da_borda(grande, 350, 290, 330)
    assert fina >= 1
    assert grossa >= 2 * fina


def test_bagagem_apontada_aparece_no_quadro():
    """Ela não tem caixa de detector: sem desenhá-la pelo pixel apontado, o
    operador não tem como saber se o clique dele pegou."""
    sessao = _sessao_com_furto()
    sessao.aponta_bagagem(800.0, 700.0)

    anotado = desenha(np.zeros((1200, 1600, 3), np.uint8), [], sessao)

    vizinhanca = anotado[660:740, 760:840]
    assert vizinhanca.any()
