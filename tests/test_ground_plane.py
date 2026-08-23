import numpy as np
import pytest

from custody_watch.ground_plane import GroundPlane
from custody_watch.types import Point


def test_homografia_identidade_preserva_coordenadas():
    plane = GroundPlane(np.eye(3))
    assert plane.project(3.0, 4.0) == Point(3.0, 4.0)


def test_homografia_de_escala_converte_para_metros():
    """100 px por metro."""
    plane = GroundPlane(np.diag([0.01, 0.01, 1.0]))
    projected = plane.project(500.0, 300.0)
    assert projected.x == pytest.approx(5.0)
    assert projected.y == pytest.approx(3.0)


def test_foot_point_usa_a_base_da_bbox():
    """O objeto toca o chão na base da caixa, não no centro.

    Projetar o centro colocaria a pessoa metros à frente de onde ela está.
    """
    plane = GroundPlane(np.eye(3))
    assert plane.foot_point((10.0, 20.0, 30.0, 80.0)) == Point(20.0, 80.0)


def test_homografia_nao_3x3_e_rejeitada():
    with pytest.raises(ValueError, match="3x3"):
        GroundPlane(np.eye(4))


def test_from_correspondences_exige_quatro_pontos():
    with pytest.raises(ValueError, match="4 correspond"):
        GroundPlane.from_correspondences([(0, 0), (1, 0), (0, 1)], [(0, 0), (1, 0), (0, 1)])


def test_from_correspondences_recupera_escala_conhecida():
    pixels = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    world = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    plane = GroundPlane.from_correspondences(pixels, world)
    recovered = plane.project(50.0, 50.0)
    assert recovered.x == pytest.approx(0.5, abs=1e-6)
    assert recovered.y == pytest.approx(0.5, abs=1e-6)


def test_ponto_no_infinito_e_erro_explicito():
    """Linha do horizonte projeta no infinito — falhar alto, não retornar lixo."""
    degenerate = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    with pytest.raises(ValueError, match="infinito"):
        GroundPlane(degenerate).project(1.0, 1.0)
