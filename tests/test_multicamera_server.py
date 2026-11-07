import json
import threading
import urllib.error
import urllib.request

import pytest

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_media import FrameImages
from custody_watch.multicamera_server import ReplayServer
from custody_watch.multicamera_view import ReplayView
from tests.multicamera_fixture import make_registry


@pytest.fixture
def replay_server(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    server = ReplayServer(ReplayView(registry), FrameImages(registry), port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def _get(base, route):
    return urllib.request.urlopen(base + route, timeout=5)


def test_pagina_usa_assets_locais_e_nao_codigo_inline(replay_server):
    with _get(replay_server, "/") as response:
        text = response.read().decode("utf-8")
        assert response.headers["Content-Type"].startswith("text/html")
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "/assets/multicamera.js" in text
    assert "/assets/multicamera.css" in text
    assert "/assets/replay_link.js" in text
    with _get(replay_server, "/assets/multicamera.js") as response:
        assert "javascript" in response.headers["Content-Type"]
        assert response.read()
    with _get(replay_server, "/assets/replay_link.js") as response:
        assert "javascript" in response.headers["Content-Type"]
        assert b"replayTarget" in response.read()


def test_config_e_estado_tem_relogio_compartilhado(replay_server):
    with _get(replay_server, "/api/config") as response:
        config = json.load(response)
    with _get(replay_server, "/api/state?t=2") as response:
        state = json.load(response)
    assert config["site_id"] == "synthetic-hall"
    assert state["cameras"][1]["status"] == "missing"
    assert state["cameras"][1]["image"]["historical"] is True
    assert state["cameras"][1]["image"]["t"] == 1


def test_imagem_qualificada_e_svg_sintetico(replay_server):
    with _get(
        replay_server, "/api/image?camera_id=cam-a&segment_id=segment-1&frame_index=0"
    ) as response:
        assert response.headers["Content-Type"].startswith("image/svg+xml")
        assert response.headers["Cache-Control"] == "no-store"
        assert "Esquema sintético" in response.read().decode("utf-8")


@pytest.mark.parametrize(
    "route",
    [
        "/api/state",
        "/api/state?t=nan",
        "/api/state?t=1&t=2",
        "/api/state?t=1&extra=2",
        "/api/image?camera_id=cam-a&segment_id=outro&frame_index=0",
        "/api/image?camera_id=cam-a&segment_id=segment-1&frame_index=nan",
    ],
)
def test_query_invalida_nao_derruba_servidor(replay_server, route):
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(replay_server, route)
    assert caught.value.code == 400
    with _get(replay_server, "/api/state?t=0") as response:
        assert response.status == 200


def test_nao_serve_arquivos_do_workspace(replay_server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(replay_server, "/assets/../camera_registry.py")
    assert caught.value.code == 404
