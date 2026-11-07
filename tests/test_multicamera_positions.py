from dataclasses import replace

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_replay import frame_to_dict, iter_synchronized
from custody_watch.multicamera_view import ReplayView
from tests.multicamera_fixture import make_registry


@pytest.mark.parametrize("touches_bottom", [None, False, True])
def test_base_da_bbox_nao_comprova_contato_com_o_chao(tmp_path, touches_bottom):
    registry = load_camera_registry(make_registry(tmp_path))
    item = next(iter_synchronized(registry))
    detection = replace(item.frame.detections[0], touches_bottom=touches_bottom)
    item = replace(item, frame=replace(item.frame, detections=(detection,)))

    row = frame_to_dict(item)
    track = row["tracks"][0]

    assert row["schema_version"] == 2
    assert track["position_m"] is None
    assert track["projected_position_m"] == pytest.approx([1.5, 2])
    assert track["position_status"] == (
        "image_border" if touches_bottom else "ground_contact_unknown"
    )
    assert track["association_eligible"] is False
    assert track["bbox"] == [100, 0, 200, 200]


def test_referencial_ausente_nao_gera_nem_projecao_diagnostica(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    item = next(iter_synchronized(registry))
    item = replace(item, camera=replace(item.camera, projection_frame_id=None))

    track = frame_to_dict(item)["tracks"][0]

    assert track["position_m"] is None
    assert track["projected_position_m"] is None
    assert track["position_status"] == "no_common_frame"
    assert track["association_eligible"] is False


def test_busca_historica_preserva_incerteza_sem_retroagir_posicao(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))
    config = view.config()
    late = view.snapshot(2)["cameras"][1]
    first = view.snapshot(0)["cameras"][1]

    assert config["schema_version"] == 2
    assert config["position_method"] == "unverified_bbox_bottom_projection"
    assert late["status"] == "missing"
    assert late["tracks"] == []
    assert all(track["t"] == 0 for track in late["last_tracks"])
    for track in [*late["last_tracks"], *first["tracks"]]:
        assert track["position_m"] is None
        assert track["association_eligible"] is False
        assert track["projected_position_m"] is not None
