import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_media import FrameImages
from custody_watch.public_multicamera import (
    build_sample,
    nvidia_camera,
    nvidia_observations,
    read_nvidia_prefix,
    wildtrack_camera,
    wildtrack_observations,
    wildtrack_position,
)


def _sensor(matrix):
    return {
        "id": "Camera_0000",
        "homography": matrix,
        "coordinates": {"x": 1, "y": 2},
        "attributes": [
            {"name": name, "value": value}
            for name, value in [("fps", "30"), ("frameWidth", "1920"), ("frameHeight", "1080")]
        ],
    }


def test_nvidia_inverte_homografia_mundo_para_imagem():
    camera = nvidia_camera(_sensor([[2, 0, 10], [0, 4, 20], [0, 0, 1]]))

    point = camera.plane.project(14, 32)

    assert (point.x, point.y) == pytest.approx((2, 3))
    assert camera.source_fps == 30
    assert camera.position_m.x == 1


@pytest.mark.parametrize(
    "matrix",
    [np.zeros((3, 3)).tolist(), [[1, 0], [0, 1]], [[1, 0, 0], [0, float("nan"), 0], [0, 0, 1]]],
)
def test_nvidia_recusa_matriz_invalida(matrix):
    with pytest.raises(ValueError):
        nvidia_camera(_sensor(matrix))


def test_nvidia_le_campos_reais_com_espacos_e_filtra_classe():
    rows = [
        {
            "object type": "Person",
            "object id": 7,
            "3d location": [2, 3, 1],
            "2d bounding box visible": {"Camera_0000": [10, 20, 30, 40]},
        },
        {"object type": "Forklift"},
    ]

    observations = nvidia_observations(rows, "Camera_0000")

    assert len(observations) == 1
    assert observations[0].track_id == 7
    assert observations[0].bbox == (10, 20, 30, 40)
    assert observations[0].world.x == 2
    assert nvidia_observations(rows, "Camera_0001") == []


def test_nvidia_nao_escolhe_silenciosamente_entre_aliases_conflitantes():
    row = {"object type": "Person", "object_type": "Forklift"}

    with pytest.raises(ValueError, match="ambíguo"):
        nvidia_observations([row], "Camera_0000")


def test_nvidia_recusa_classe_nao_textual():
    with pytest.raises(ValueError, match="object type"):
        nvidia_observations([{"object type": True}], "Camera_0000")


def test_prefixo_gt_importa_so_frames_completos_solicitados(tmp_path):
    path = tmp_path / "prefix.part"
    path.write_text('{"0": [], "1": [{"object type":"Forklift"}], "2": [', encoding="utf-8")

    assert read_nvidia_prefix(path, [0, 1]) == {"0": [], "1": [{"object type": "Forklift"}]}
    with pytest.raises(ValueError, match="incompleto"):
        read_nvidia_prefix(path, [0, 2])


def test_prefixo_gt_nao_aceita_chaves_repetidas(tmp_path):
    path = tmp_path / "prefix.part"
    path.write_text('{"0": [], "0": [], "1": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicado"):
        read_nvidia_prefix(path, [0, 1])


def test_wildtrack_grade_e_convertida_de_centimetros_para_metros():
    position = wildtrack_position(128 + 368 * 480)

    assert (position.x, position.y) == pytest.approx((0.2, 0.2))


def test_wildtrack_vista_invisivel_nao_gera_caixa():
    rows = [
        {
            "personID": 7,
            "positionID": 128 + 368 * 480,
            "views": [
                {"viewNum": 0, "xmin": -1, "ymin": -1, "xmax": -1, "ymax": -1},
                {"viewNum": 1, "xmin": 1, "ymin": 0, "xmax": 3, "ymax": 2},
            ],
        }
    ]

    assert wildtrack_observations(rows, 0) == []
    assert wildtrack_observations(rows, 1)[0].world.y == pytest.approx(0.2)


def _wildtrack_fixture(root: Path):
    for camera in [1, 2]:
        intrinsic = root / "calibrations" / "intrinsic_zero" / f"intr_CVLab{camera}.xml"
        intrinsic.parent.mkdir(parents=True, exist_ok=True)
        storage = cv2.FileStorage(str(intrinsic), cv2.FILE_STORAGE_WRITE)
        storage.write("camera_matrix", np.diag([100.0, 100.0, 1.0]))
        storage.release()
        extrinsic = root / "calibrations" / "extrinsic" / f"extr_CVLab{camera}.xml"
        extrinsic.parent.mkdir(parents=True, exist_ok=True)
        extrinsic.write_text(
            "<opencv_storage><rvec>0 0 0</rvec><tvec>0 0 1000</tvec></opencv_storage>"
        )
        folder = root / "Image_subsets" / f"C{camera}"
        folder.mkdir(parents=True)
        for frame in [0, 5]:
            assert cv2.imwrite(
                str(folder / f"{frame:08d}.png"), np.full((1080, 1920, 3), 70, np.uint8)
            )
    annotations = root / "annotations_positions"
    annotations.mkdir()
    for frame in [0, 5]:
        (annotations / f"{frame:08d}.json").write_text(
            json.dumps(
                [
                    {
                        "personID": 7,
                        "positionID": 128 + 368 * 480,
                        "views": [
                            {"viewNum": i, "xmin": 1, "ymin": 0, "xmax": 3, "ymax": 2}
                            for i in [0, 1]
                        ],
                    }
                ]
            )
        )


def test_wildtrack_extrinsecos_em_cm_nao_viram_metros_sem_conversao(tmp_path):
    _wildtrack_fixture(tmp_path)
    camera = wildtrack_camera(tmp_path, "C1")

    point = camera.plane.project(20, 30)

    assert (point.x, point.y) == pytest.approx((2, 3))
    assert camera.source_fps == 10


def test_importacao_produz_oracle_reproduzivel_e_video_decodificavel(tmp_path):
    root = tmp_path / "source"
    _wildtrack_fixture(root)
    output = tmp_path / "imported"

    report = build_sample("wildtrack", root, output, [0, 5], ["C1", "C2"], code_revision="test")
    registry = load_camera_registry(output / "registry.json")

    assert report["integration_status"] == "passed"
    assert report["frames_per_camera"] == 2
    assert report["mode"] == "oracle"
    assert report["identity_association_validated"] is False
    assert report["custody_validated"] is False
    assert report["cameras"][0]["bbox_foot_to_world_annotation"]["max_m"] < 0.00001
    assert registry.cameras[0].clock.uncertainty_s is None
    assert registry.cameras[0].recording.manifest["clock"]["fps"] == 2
    assert registry.cameras[0].recording.manifest["predictions"]["provenance"] == "ground_truth"
    assert FrameImages(registry).get("C2", "sample-1", 1).content_type == "image/jpeg"


def test_importador_preserva_output_existente(tmp_path):
    target = tmp_path / "output"
    target.mkdir()
    (target / "marker").write_text("preservar")

    with pytest.raises(ValueError, match="existe"):
        build_sample(
            "wildtrack", tmp_path / "source", target, [0, 5], ["C1", "C2"], code_revision="test"
        )
    assert (target / "marker").read_text() == "preservar"
