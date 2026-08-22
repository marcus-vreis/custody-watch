import numpy as np
import pytest

from custody_watch.ground_plane import GroundPlane
from custody_watch.tracking import TrackedDetection, to_observations, video_fps
from custody_watch.types import Point


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
