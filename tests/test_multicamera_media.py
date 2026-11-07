import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_media import FrameImages
from tests.multicamera_fixture import make_registry
from tests.recorded_fixture import reference, write_json


def make_video_registry(root: Path):
    path = make_registry(root)
    video = root / "cam-a" / "scene.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 1, (400, 240))
    assert writer.isOpened(), "o backend do teste precisa codificar MJPG local"
    try:
        for value in [30, 90, 150, 210]:
            writer.write(np.full((240, 400, 3), value, dtype=np.uint8))
    finally:
        writer.release()
    manifest_path = video.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"].update(kind="video", file=reference(video), origin="Vídeo gerado no teste")
    manifest["predictions"]["provenance"] = "detector"
    write_json(manifest_path, manifest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cameras"][0]["recording"].update(
        {**reference(manifest_path), "path": "cam-a/manifest.json"}
    )
    write_json(path, data)
    return path, video


def test_fixture_tem_esquema_svg_sem_abrir_video(tmp_path):
    images = FrameImages(load_camera_registry(make_registry(tmp_path)))

    image = images.get("cam-a", "segment-1", 0)

    assert image.content_type == "image/svg+xml"
    text = image.data.decode("utf-8")
    assert "Esquema sintético" in text
    assert "cam-a" in text
    assert "<rect" in text
    assert "<script" not in text


def test_frame_vazio_tem_esquema_valido_e_missing_nao_tem_imagem(tmp_path):
    images = FrameImages(load_camera_registry(make_registry(tmp_path)))

    assert images.get("cam-b", "segment-1", 1).data
    with pytest.raises(ValueError, match="observado"):
        images.get("cam-b", "segment-1", 2)


@pytest.mark.parametrize(
    "camera,segment,index",
    [("outra", "segment-1", 0), ("cam-a", "velho", 0), ("cam-a", "segment-1", 100)],
)
def test_imagem_exige_camera_segmento_e_frame_validos(tmp_path, camera, segment, index):
    images = FrameImages(load_camera_registry(make_registry(tmp_path)))

    with pytest.raises(ValueError):
        images.get(camera, segment, index)


def test_video_local_decodifica_o_frame_solicitado_e_libera_recurso(tmp_path):
    path, video = make_video_registry(tmp_path)
    images = FrameImages(load_camera_registry(path))

    first = images.get("cam-a", "segment-1", 0)
    last = images.get("cam-a", "segment-1", 3)
    decoded_first = cv2.imdecode(np.frombuffer(first.data, dtype=np.uint8), cv2.IMREAD_COLOR)
    decoded_last = cv2.imdecode(np.frombuffer(last.data, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert first.content_type == last.content_type == "image/jpeg"
    assert decoded_first.shape == decoded_last.shape == (240, 400, 3)
    assert decoded_first[230, 390, 0] == pytest.approx(30, abs=5)
    assert decoded_last[230, 390, 0] == pytest.approx(210, abs=5)
    # Windows recusa rename se o arquivo continuar aberto pelo decoder.
    video.rename(video.with_name("released.avi"))


def test_video_trocado_depois_do_carregamento_e_recusado(tmp_path):
    path, video = make_video_registry(tmp_path)
    images = FrameImages(load_camera_registry(path))
    video.write_bytes(b"substituido")

    with pytest.raises(ValueError, match="alterado"):
        images.get("cam-a", "segment-1", 0)


def test_video_trocado_antes_de_criar_o_catalogo_nao_herda_verificacao(tmp_path):
    path, video = make_video_registry(tmp_path)
    registry = load_camera_registry(path)
    video.write_bytes(b"arquivo substituido")

    with pytest.raises(ValueError, match="integridade"):
        FrameImages(registry)
