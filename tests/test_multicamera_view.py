import json

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_view import ReplayView
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import reference, rewrite_cache, write_json


def _change(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)


def test_config_descreve_cameras_planta_e_origem_sem_expor_caminhos(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    config = view.config()

    assert config["site_id"] == "synthetic-hall"
    assert config["position_method"] == "unverified_bbox_bottom_projection"
    assert config["timeline"] == {"start_s": -1, "end_s": 4, "step_s": 0.05}
    assert config["floors"][0]["coordinate_frame"]["unit"] == "m"
    assert config["cameras"][0]["position_m"] == [-1, 0]
    assert config["cameras"][0]["media_kind"] == "schematic"
    assert config["cameras"][1]["clock"]["uncertainty_s"] == 0.05
    assert "path" not in json.dumps(config)
    assert str(tmp_path) not in json.dumps(config)


def test_snapshot_inicio_separa_ids_locais_sem_fusao(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    a, b = view.snapshot(0)["cameras"]

    assert a["status"] == b["status"] == "observed"
    assert a["tracks"][0]["track_key"] != b["tracks"][0]["track_key"]
    assert a["tracks"][0]["projected_position_m"] == pytest.approx([1.5, 2])
    assert b["tracks"][0]["projected_position_m"] == pytest.approx([1.5, 2])
    assert a["tracks"][0]["position_m"] is None
    assert b["tracks"][0]["position_m"] is None
    assert b["image"]["media_t"] == 1
    assert b["image"]["t"] == 0
    assert b["time_interval_s"] == [-0.05, 0.05]


def test_frame_vazio_remove_tracks_atuais_mas_preserva_ultima_observacao(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    camera = view.snapshot(1)["cameras"][1]

    assert camera["status"] == "observed"
    assert camera["tracks"] == []
    assert camera["image"]["frame_index"] == 1
    assert camera["image"]["historical"] is False
    assert camera["last_tracks"][0]["t"] == 0
    assert camera["last_tracks"][0]["age_s"] == 1


def test_missing_mostra_imagem_historica_com_relogio_proprio(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    a, b = view.snapshot(2.5)["cameras"]

    assert a["status"] == "observed"
    assert b["status"] == "missing"
    assert b["frame_index"] == 2
    assert b["frame_t"] == 2
    assert b["tracks"] == []
    assert b["image"]["historical"] is True
    assert b["image"]["t"] == 1
    assert b["image"]["age_s"] == 1.5
    assert "frame_index=1" in b["image"]["url"]
    assert "segment_id=segment-1" in b["image"]["url"]


def test_busca_para_tras_nao_carrega_tracks_do_futuro(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    expected = view.snapshot(0)
    view.snapshot(4)
    assert view.snapshot(0) == expected
    assert all(camera["last_tracks"] == [] for camera in view.snapshot(-1)["cameras"])


def test_fim_preserva_ultima_imagem_mas_nao_tracks_atuais(tmp_path):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    camera = view.snapshot(4)["cameras"][0]

    assert camera["status"] == "completed"
    assert camera["tracks"] == []
    assert camera["image"]["t"] == 3
    assert camera["image"]["historical"] is True


def test_cameras_com_inicios_distintos_nao_reutilizam_dados(tmp_path):
    path = make_registry(tmp_path)
    _change(path, lambda data: data["cameras"][0]["clock"].update(offset_s=2))
    view = ReplayView(load_camera_registry(path))

    camera = view.snapshot(0)["cameras"][0]

    assert camera["status"] == "not_started"
    assert camera["image"] is None
    assert camera["tracks"] == []
    assert camera["last_tracks"] == []


def test_linha_omitida_vira_stale_sem_fabricar_frame(tmp_path):
    path = make_registry(tmp_path)
    folder = tmp_path / "cam-a"
    rows = [json.loads(line) for line in (folder / "predictions.jsonl").read_text().splitlines()]
    del rows[1]
    rewrite_cache(folder, rows)
    _change(
        path,
        lambda data: data["cameras"][0]["recording"].update(
            {**reference(folder / "manifest.json"), "path": "cam-a/manifest.json"}
        ),
    )
    view = ReplayView(load_camera_registry(path))

    camera = view.snapshot(1.1)["cameras"][0]

    assert camera["status"] == "stale"
    assert camera["image"]["frame_index"] == 0
    assert camera["image"]["historical"] is True
    assert camera["image"]["age_s"] == 1.1
    assert camera["tracks"] == []


def test_sem_calibracao_comum_nao_inventa_posicao_na_planta(tmp_path):
    path = make_registry(tmp_path)
    _change(
        path,
        lambda data: data["cameras"][0].update(
            position_m=None, coverage_m=None, projection_frame_id=None
        ),
    )
    view = ReplayView(load_camera_registry(path))

    assert view.config()["cameras"][0]["coverage_m"] is None
    assert view.snapshot(0)["cameras"][0]["tracks"][0]["position_m"] is None


def test_cache_sem_quadros_nao_finge_captura(tmp_path):
    path = make_registry(tmp_path)
    folder = tmp_path / "cam-a"
    rewrite_cache(folder, [])
    _change(
        path,
        lambda data: data["cameras"][0]["recording"].update(
            {**reference(folder / "manifest.json"), "path": "cam-a/manifest.json"}
        ),
    )
    view = ReplayView(load_camera_registry(path))

    camera = view.snapshot(0)["cameras"][0]

    assert camera["status"] == "unavailable"
    assert camera["image"] is None
    assert camera["tracks"] == camera["last_tracks"] == []


def test_video_offline_mantem_cache_mas_nao_anuncia_imagem(tmp_path):
    path = make_registry(tmp_path)
    manifest = tmp_path / "cam-a" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["source"]["kind"] = "video"
    data["source"]["file"]["path"] = None
    data["predictions"]["provenance"] = "detector"
    write_json(manifest, data)
    _change(
        path,
        lambda group: group["cameras"][0]["recording"].update(
            {**reference(manifest), "path": "cam-a/manifest.json"}
        ),
    )
    view = ReplayView(load_camera_registry(path))

    assert view.config()["cameras"][0]["media_kind"] == "unavailable"
    assert view.snapshot(0)["cameras"][0]["image"] is None
    assert len(view.snapshot(0)["cameras"][0]["tracks"]) == 2


@pytest.mark.parametrize("t", [True, "1", float("nan"), float("inf")])
def test_snapshot_recusa_tempo_invalido(tmp_path, t):
    view = ReplayView(load_camera_registry(make_registry(tmp_path)))

    with pytest.raises(ValueError):
        view.snapshot(t)


def test_imagem_carrega_hashes_e_tracks_somente_do_quadro_exibido(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    view = ReplayView(registry)
    config = view.config()
    current = view.snapshot(0)["cameras"][1]
    historical = view.snapshot(2)["cameras"][1]

    assert config["registry_sha256"] == registry.manifest_sha256
    assert config["cameras"][1]["sequence_sha256"] == registry.cameras[1].recording.manifest_sha256
    assert current["image"]["tracks"] == current["tracks"]
    assert current["image"]["registry_sha256"] == registry.manifest_sha256
    assert current["image"]["sequence_sha256"] == registry.cameras[1].recording.manifest_sha256
    assert f"registry_sha256={registry.manifest_sha256}" in current["image"]["url"]
    assert historical["image"]["frame_index"] == 1
    assert historical["image"]["tracks"] == []
    assert historical["last_tracks"]
    assert view.snapshot(0)["cameras"][1]["image"]["tracks"] == current["tracks"]
