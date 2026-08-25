import json

import pytest

from custody_watch.calibration import (
    MAX_RESIDUAL_M,
    load_calibration,
    reprojection_residual,
)
from custody_watch.types import Point

QUADRADO = [
    {"pixel": [0, 0], "world": [0.0, 0.0]},
    {"pixel": [100, 0], "world": [1.0, 0.0]},
    {"pixel": [100, 100], "world": [1.0, 1.0]},
    {"pixel": [0, 100], "world": [0.0, 1.0]},
]


def escrever(tmp_path, **campos):
    payload = {
        "camera": "ensaio-01",
        "note": "retangulo de 1x1m marcado com fita, medido em 2026-08-24",
        "correspondences": QUADRADO,
    }
    payload.update(campos)
    caminho = tmp_path / "cal.json"
    caminho.write_text(json.dumps(payload), encoding="utf-8")
    return caminho


def test_carrega_e_projeta(tmp_path):
    calibracao = load_calibration(escrever(tmp_path))

    projetado = calibracao.plane.project(50.0, 50.0)
    assert projetado.x == pytest.approx(0.5, abs=1e-6)
    assert projetado.y == pytest.approx(0.5, abs=1e-6)


def test_camera_e_nota_sao_obrigatorias(tmp_path):
    """Daqui a seis meses ninguem lembra de como o chao foi medido."""
    with pytest.raises(ValueError, match="note"):
        load_calibration(escrever(tmp_path, note="  "))

    with pytest.raises(ValueError, match="camera"):
        load_calibration(escrever(tmp_path, camera=""))


def test_menos_de_quatro_pontos_e_rejeitado(tmp_path):
    with pytest.raises(ValueError, match="4 correspond"):
        load_calibration(escrever(tmp_path, correspondences=QUADRADO[:3]))


def test_residuo_de_calibracao_exata_e_zero(tmp_path):
    calibracao = load_calibration(escrever(tmp_path))

    assert calibracao.residual_m == pytest.approx(0.0, abs=1e-9)


def test_medicao_ruim_e_recusada(tmp_path):
    """Chao medido as pressas produz homografia errada em silencio, e todo
    limiar em metros passa a mentir junto."""
    torto = [
        {"pixel": [0, 0], "world": [0.0, 0.0]},
        {"pixel": [100, 0], "world": [1.0, 0.0]},
        {"pixel": [100, 100], "world": [1.0, 1.0]},
        {"pixel": [0, 100], "world": [0.0, 1.0]},
        {"pixel": [50, 50], "world": [9.0, 9.0]},
    ]

    with pytest.raises(ValueError, match="resíduo"):
        load_calibration(escrever(tmp_path, correspondences=torto))


def test_residuo_calculado_diretamente():
    from custody_watch.ground_plane import GroundPlane

    pixels = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    mundo = [Point(0.0, 0.0), Point(1.0, 0.0), Point(1.0, 1.0), Point(0.0, 1.0)]

    plano = GroundPlane.from_correspondences(pixels, [(p.x, p.y) for p in mundo])

    assert reprojection_residual(plano, pixels, mundo) == pytest.approx(0.0, abs=1e-9)


def test_limite_e_declarado_e_documentado():
    assert MAX_RESIDUAL_M > 0.0


def test_calibration_carrega_metadados(tmp_path):
    calibracao = load_calibration(escrever(tmp_path))

    assert calibracao.camera == "ensaio-01"
    assert "fita" in calibracao.note


GRADE = [(x, y) for y in range(3) for x in range(4)]


def grade_com_canto_torto(desvio: float):
    """Grade 4x3 onde pixel = mundo * 100, com um ponto medido fora do lugar."""
    correspondencias = []
    for indice, (x, y) in enumerate(GRADE):
        mundo = [float(x), float(y)]
        if indice == 5:
            mundo = [x + desvio, y + desvio]
        correspondencias.append({"pixel": [x * 100, y * 100], "world": mundo})
    return correspondencias


def test_um_ponto_torto_entre_muitos_bons_e_recusado(tmp_path):
    """A recusa olha o pior ponto, nao a media, e este e o caso que decide.

    Doze pontos, um deles medido 0.8m fora. Ele reprojeta 0.97m errado, mas a
    media dos doze da 0.18m e passaria folgado no limite de 0.25m. Media dilui
    exatamente o erro que este modulo existe para pegar.
    """
    with pytest.raises(ValueError, match="resíduo"):
        load_calibration(escrever(tmp_path, correspondences=grade_com_canto_torto(0.8)))


def test_a_media_sozinha_teria_aceitado(tmp_path):
    """Prova de que o teste acima nao passa por acidente: a media do mesmo
    conjunto fica sob o limite."""
    from custody_watch.ground_plane import GroundPlane

    correspondencias = grade_com_canto_torto(0.8)
    pixels = [(float(c["pixel"][0]), float(c["pixel"][1])) for c in correspondencias]
    mundo = [Point(float(c["world"][0]), float(c["world"][1])) for c in correspondencias]
    plano = GroundPlane.from_correspondences(pixels, [(p.x, p.y) for p in mundo])

    assert reprojection_residual(plano, pixels, mundo) < MAX_RESIDUAL_M


def test_erro_nomeia_o_ponto_culpado(tmp_path):
    """Recusar sem dizer qual ponto obriga a remedir o chao inteiro."""
    with pytest.raises(ValueError, match=r"ponto 5"):
        load_calibration(escrever(tmp_path, correspondences=grade_com_canto_torto(0.8)))


def test_calibracao_exata_reporta_pior_ponto_zero(tmp_path):
    calibracao = load_calibration(escrever(tmp_path))

    assert calibracao.worst_residual_m == pytest.approx(0.0, abs=1e-9)
