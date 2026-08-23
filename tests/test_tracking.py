import numpy as np
import pytest

from custody_watch.ground_plane import GroundPlane
from custody_watch.tracking import (
    PlausibilityGate,
    TrackedDetection,
    to_observations,
    video_fps,
)
from custody_watch.types import Observation, Point


def test_converte_para_metros_pela_base_da_bbox():
    plane = GroundPlane(np.diag([0.01, 0.01, 1.0]))
    tracked = [TrackedDetection(track_id=1, cls="person", bbox=(100.0, 0.0, 300.0, 400.0))]

    observations = to_observations(tracked, plane, t=1.5)

    assert len(observations) == 1
    assert observations[0].track_id == 1
    assert observations[0].position.x == pytest.approx(2.0)  # centro x = 200px
    assert observations[0].position.y == pytest.approx(4.0)  # base y = 400px
    assert observations[0].t == 1.5


def test_descarta_deteccao_que_projeta_no_infinito():
    """Um ponto na linha do horizonte não deve derrubar o pipeline inteiro."""
    degenerate = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    tracked = [TrackedDetection(track_id=1, cls="person", bbox=(0.0, 0.0, 2.0, 2.0))]

    assert to_observations(tracked, GroundPlane(degenerate), t=0.0) == []


def test_preserva_track_id_e_classe():
    plane = GroundPlane(np.eye(3))
    tracked = [
        TrackedDetection(track_id=7, cls="person", bbox=(0.0, 0.0, 2.0, 2.0)),
        TrackedDetection(track_id=8, cls="suitcase", bbox=(4.0, 4.0, 6.0, 6.0)),
    ]

    observations = to_observations(tracked, plane, t=0.0)

    assert [(o.track_id, o.cls) for o in observations] == [(7, "person"), (8, "suitcase")]
    assert observations[1].position == Point(5.0, 6.0)


def test_fps_de_arquivo_invalido_falha_alto(tmp_path):
    """Melhor falhar que chutar: fps errado distorce todos os limiares temporais."""
    fake = tmp_path / "nao_e_video.mp4"
    fake.write_bytes(b"isso nao e um video")

    with pytest.raises(ValueError, match="fps"):
        video_fps(fake)


def test_fps_de_arquivo_inexistente_falha_alto(tmp_path):
    with pytest.raises(ValueError, match="fps"):
        video_fps(tmp_path / "nao_existe.mp4")


# --- portao de plausibilidade -------------------------------------------------


def observacao(track_id: int, x: float, t: float) -> Observation:
    return Observation(track_id=track_id, cls="person", position=Point(x, 0.0), t=t)


def test_portao_aceita_caminhada_normal():
    """Mediana medida no CAVIAR: 1,34 m/s."""
    portao = PlausibilityGate()

    assert portao.accept(observacao(1, 0.0, 0.0)) is True
    assert portao.accept(observacao(1, 0.05, 0.04)) is True
    assert portao.rejected == 0


def test_portao_aceita_a_primeira_observacao_de_um_track():
    """Sem referencia anterior nao ha velocidade a julgar."""
    assert PlausibilityGate().accept(observacao(7, 999.0, 5.0)) is True


def test_portao_rejeita_salto_impossivel():
    """O artefato descrito na revisao: 6m em 40ms, 150 m/s."""
    portao = PlausibilityGate()
    portao.accept(observacao(1, 0.0, 0.0))

    assert portao.accept(observacao(1, 6.0, 0.04)) is False
    assert portao.rejected == 1


def test_observacao_rejeitada_nao_vira_a_nova_referencia():
    """Aceita-la deixaria o salto passar em duas etapas.

    A primeira metade seria rejeitada, a segunda pareceria plausivel a partir
    do ponto errado, e a posicao implausivel entraria no historico assim mesmo.
    """
    portao = PlausibilityGate()
    portao.accept(observacao(1, 0.0, 0.0))
    portao.accept(observacao(1, 6.0, 0.04))

    assert portao.accept(observacao(1, 6.1, 0.08)) is False


def test_portao_trata_tracks_de_forma_independente():
    portao = PlausibilityGate()
    portao.accept(observacao(1, 0.0, 0.0))
    portao.accept(observacao(2, 50.0, 0.0))

    assert portao.accept(observacao(1, 0.05, 0.04)) is True
    assert portao.accept(observacao(2, 50.05, 0.04)) is True


def test_filter_devolve_so_o_plausivel():
    portao = PlausibilityGate()
    portao.filter([observacao(1, 0.0, 0.0), observacao(2, 0.0, 0.0)])

    sobreviventes = portao.filter([observacao(1, 0.05, 0.04), observacao(2, 40.0, 0.04)])

    assert [o.track_id for o in sobreviventes] == [1]


def test_limiar_configuravel():
    apertado = PlausibilityGate(max_speed_ms=1.0)
    apertado.accept(observacao(1, 0.0, 0.0))

    assert apertado.accept(observacao(1, 0.1, 0.04)) is False
