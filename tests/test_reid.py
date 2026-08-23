import numpy as np
import pytest

from custody_watch.config import ReidConfig
from custody_watch.reid import (
    SIGNATURE_SIZE,
    Appearance,
    TrackLinker,
    average,
    describe,
    similarity,
)
from custody_watch.types import Point


def assinatura(pico: int, altura: float = 10.0) -> Appearance:
    """Assinatura sintética com um pico numa dimensão, resto plano."""
    vetor = [1.0] * SIGNATURE_SIZE
    vetor[pico] = altura
    return Appearance(signature=tuple(vetor))


def mistura(a: Appearance, b: Appearance, peso: float) -> Appearance:
    va, vb = np.asarray(a.signature), np.asarray(b.signature)
    return Appearance(signature=tuple(float(v) for v in va * (1 - peso) + vb * peso))


# --- assinatura ---------------------------------------------------------------


def test_assinatura_de_tamanho_errado_e_rejeitada():
    with pytest.raises(ValueError, match="48 dimensões"):
        Appearance(signature=(1.0, 2.0))


def test_describe_produz_assinatura_do_tamanho_certo():
    recorte = np.full((32, 14, 3), 128, dtype=np.uint8)

    resultado = describe(recorte)

    assert resultado is not None
    assert len(resultado.signature) == SIGNATURE_SIZE


def test_describe_recusa_recorte_minusculo():
    """Sem pixels suficientes não há informação, e chutar seria pior."""
    assert describe(np.zeros((2, 2, 3), dtype=np.uint8)) is None


def test_describe_separa_torso_de_pernas():
    """Camiseta clara com calça escura difere do inverso, mesmo com o mesmo
    histograma global."""
    de_cima = np.zeros((32, 14, 3), dtype=np.uint8)
    de_cima[:16] = 220
    invertido = np.zeros((32, 14, 3), dtype=np.uint8)
    invertido[16:] = 220

    assert similarity(describe(de_cima), describe(invertido)) < 0.9


def test_similaridade_de_identicos_e_um():
    assert similarity(assinatura(0), assinatura(0)) == pytest.approx(1.0)


def test_media_de_lista_vazia_falha_alto():
    with pytest.raises(ValueError, match="não há aparências"):
        average([])


# --- religação: as três guardas -----------------------------------------------


def test_sem_aparencia_o_track_e_canonico_de_si_mesmo():
    """Degrada para o comportamento anterior ao re-ID, que é o lado seguro."""
    linker = TrackLinker()

    resultado = linker.observe(1, t=0.0, position=Point(0.0, 0.0), appearance=None)

    assert resultado.canonical_id == 1
    assert resultado.settled is True
    assert resultado.linked_from is None


def test_track_nao_estabiliza_antes_do_minimo_de_amostras():
    """Perfil de um frame só é ruído, e ruído não deve receber posse."""
    linker = TrackLinker(ReidConfig(min_samples=3))

    primeiro = linker.observe(1, 0.0, Point(0.0, 0.0), assinatura(0))
    segundo = linker.observe(1, 0.1, Point(0.0, 0.0), assinatura(0))
    terceiro = linker.observe(1, 0.2, Point(0.0, 0.0), assinatura(0))

    assert primeiro.settled is False
    assert segundo.settled is False
    assert terceiro.settled is True


def alimentar(linker, track_id, t0, ponto, aparencia, n=2, dt=0.04):
    resultado = None
    for i in range(n):
        resultado = linker.observe(track_id, t0 + i * dt, ponto, aparencia)
    return resultado


def test_religa_quando_a_evidencia_e_clara():
    linker = TrackLinker(ReidConfig(min_samples=2))
    alimentar(linker, 1, 0.0, Point(0.0, 0.0), assinatura(0))

    resultado = alimentar(linker, 9, 5.0, Point(1.0, 0.0), assinatura(0))

    assert resultado.canonical_id == 1
    assert resultado.linked_from == 1
    assert linker.links() == {9: 1}


def test_tracks_que_coexistem_nunca_sao_religados():
    """Dois tracks vivos ao mesmo tempo são duas pessoas, por definição.

    Este é o caso que aparência sozinha erraria: medido no CAVIAR, o par de
    similaridade mais alta (0,992) era justamente duas pessoas distintas em
    cena simultaneamente.
    """
    linker = TrackLinker(ReidConfig(min_samples=2))

    for i in range(6):
        t = i * 0.04
        linker.observe(1, t, Point(0.0, 0.0), assinatura(0))
        linker.observe(9, t, Point(1.0, 0.0), assinatura(0))

    assert linker.links() == {}


def test_candidato_vivo_no_nascimento_do_track_novo_nao_religa():
    """A disjunção é medida contra o nascimento, não contra a estabilização.

    Um candidato que sumiu depois que o track novo apareceu esteve em cena
    junto com ele. Comparar contra o instante de estabilização deixaria isso
    passar, porque o track novo leva `min_samples` frames para se firmar.
    """
    linker = TrackLinker(ReidConfig(min_samples=5))

    # O track 1 precisa estar estabilizado para ser candidato.
    for i in range(5):
        linker.observe(1, i * 0.04, Point(0.0, 0.0), assinatura(0))
    assert linker.canonical(1) == 1

    # O track 9 nasce em t=0.20, com o track 1 ainda em cena ate t=0.28,
    # e so estabiliza em t=0.36. Medir a lacuna contra 0.36 daria +0.08s e
    # deixaria religar; contra o nascimento da -0.08s e recusa.
    linker.observe(9, 0.20, Point(1.0, 0.0), assinatura(0))
    linker.observe(1, 0.24, Point(0.0, 0.0), assinatura(0))
    linker.observe(9, 0.24, Point(1.0, 0.0), assinatura(0))
    linker.observe(1, 0.28, Point(0.0, 0.0), assinatura(0))
    for t in (0.28, 0.32, 0.36):
        linker.observe(9, t, Point(1.0, 0.0), assinatura(0))

    assert linker.links() == {}


def test_lacuna_longa_demais_recusa():
    """Depois de minutos a pessoa teve tempo de trocar de roupa."""
    linker = TrackLinker(ReidConfig(min_samples=2, max_gap_s=10.0))
    alimentar(linker, 1, 0.0, Point(0.0, 0.0), assinatura(0))

    resultado = alimentar(linker, 9, 60.0, Point(1.0, 0.0), assinatura(0))

    assert resultado.canonical_id == 9
    assert resultado.linked_from is None


def test_reaparecer_longe_demais_rapido_demais_recusa():
    """Trinta metros em meio segundo não é a mesma pessoa."""
    linker = TrackLinker(ReidConfig(min_samples=2, max_speed_ms=1.0, position_slack_m=0.5))
    alimentar(linker, 1, 0.0, Point(0.0, 0.0), assinatura(0))

    resultado = alimentar(linker, 9, 0.5, Point(30.0, 0.0), assinatura(0))

    assert resultado.linked_from is None


def test_empate_entre_candidatos_suprime_a_religacao():
    """Regra P3 aplicada aqui: não saber qual é suprime a religação.

    Ligar ao candidato errado é pior que não ligar — o ladrão herdaria o grupo
    da vítima e o furto real ficaria silencioso.
    """
    linker = TrackLinker(ReidConfig(min_samples=2, min_similarity=0.5, min_margin=0.05))
    alvo = assinatura(0)
    quase = mistura(assinatura(0), assinatura(1), 0.5)

    alimentar(linker, 1, 0.0, Point(0.0, 0.0), alvo)
    alimentar(linker, 2, 0.0, Point(0.0, 0.0), quase)

    resultado = alimentar(linker, 9, 5.0, Point(0.5, 0.0), mistura(alvo, quase, 0.5))

    assert resultado.linked_from is None


def test_similaridade_abaixo_do_piso_recusa():
    linker = TrackLinker(ReidConfig(min_samples=2, min_similarity=0.99))
    alimentar(linker, 1, 0.0, Point(0.0, 0.0), assinatura(0))

    resultado = alimentar(linker, 9, 5.0, Point(1.0, 0.0), assinatura(5))

    assert resultado.linked_from is None


def test_track_ja_canonico_continua_estabilizado():
    linker = TrackLinker(ReidConfig(min_samples=2))
    alimentar(linker, 1, 0.0, Point(0.0, 0.0), assinatura(0))

    resultado = linker.observe(1, 10.0, Point(2.0, 0.0), assinatura(0))

    assert resultado.settled is True
    assert resultado.canonical_id == 1
    assert linker.canonical(1) == 1


def test_canonical_de_track_desconhecido_devolve_ele_mesmo():
    assert TrackLinker().canonical(404) == 404
