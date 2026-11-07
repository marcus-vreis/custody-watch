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
    assert "/assets/map_positions.js" in text
    assert "/assets/camera_inspector.js" in text
    assert "/assets/contact_review.js" in text
    assert "/assets/contact_review_ui.js" in text
    assert 'id="contact-fields" disabled' in text
    assert '<dialog id="inspector" aria-labelledby="inspection-title">' in text
    assert '<input id="diagnostics" type="checkbox">' in text
    with _get(replay_server, "/assets/multicamera.js") as response:
        assert "javascript" in response.headers["Content-Type"]
        assert response.read()
    with _get(replay_server, "/assets/replay_link.js") as response:
        assert "javascript" in response.headers["Content-Type"]
        assert b"replayTarget" in response.read()
    with _get(replay_server, "/assets/map_positions.js") as response:
        assert "javascript" in response.headers["Content-Type"]
        assert b"mapPosition" in response.read()
    with _get(replay_server, "/assets/camera_inspector.js") as response:
        assert "javascript" in response.headers["Content-Type"]
        assert b"inspectionTarget" in response.read()
    for asset in ("contact_review.js", "contact_review_ui.js"):
        with _get(replay_server, "/assets/" + asset) as response:
            assert "javascript" in response.headers["Content-Type"]
            assert response.read()


def test_config_e_estado_tem_relogio_compartilhado(replay_server):
    with _get(replay_server, "/api/config") as response:
        config = json.load(response)
    with _get(replay_server, "/api/state?t=2") as response:
        state = json.load(response)
    assert config["site_id"] == "synthetic-hall"
    assert config["schema_version"] == state["schema_version"] == 2
    track = state["cameras"][0]["tracks"][0]
    assert track["position_m"] is None
    assert track["projected_position_m"] is not None
    assert track["association_eligible"] is False
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


def test_inspecao_aceita_resolucao_da_fonte_sem_mudar_namespace(replay_server):
    with _get(
        replay_server,
        "/api/image?camera_id=cam-a&segment_id=segment-1&frame_index=0&resolution=source",
    ) as response:
        assert response.headers["Content-Type"].startswith("image/svg+xml")
        assert "cam-a" in response.read().decode("utf-8")


@pytest.mark.parametrize(
    "suffix",
    ["&resolution=", "&resolution=source&resolution=preview", "&resolution=original", "&path=x"],
)
def test_inspecao_recusa_parametros_invalidos(replay_server, suffix):
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(
            replay_server,
            "/api/image?camera_id=cam-a&segment_id=segment-1&frame_index=0" + suffix,
        )
    assert caught.value.code == 400


def test_imagem_vinculada_aos_hashes_e_tracks_do_snapshot(replay_server):
    with _get(replay_server, "/api/state?t=0") as response:
        image = json.load(response)["cameras"][0]["image"]
    assert image["tracks"][0]["track_key"]["local_track_id"] == 1
    with _get(replay_server, image["url"] + "&resolution=source") as response:
        assert response.status == 200


@pytest.mark.parametrize("change", ["registry", "sequence", "missing", "duplicate"])
def test_imagem_de_outro_registro_ou_sequencia_nao_herda_metadados(replay_server, change):
    with _get(replay_server, "/api/state?t=0") as response:
        image = json.load(response)["cameras"][0]["image"]
    url = image["url"]
    if change == "registry":
        url = url.replace(image["registry_sha256"], "0" * 64)
    elif change == "sequence":
        url = url.replace(image["sequence_sha256"], "0" * 64)
    elif change == "missing":
        url = url.replace("&sequence_sha256=" + image["sequence_sha256"], "")
    else:
        url += "&registry_sha256=" + image["registry_sha256"]

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(replay_server, url)

    assert caught.value.code == 400


def test_revisao_nao_introduz_endpoint_de_escrita_no_servidor(replay_server):
    request = urllib.request.Request(
        replay_server + "/api/contacts", data=b'{"path":"arquivo.json"}', method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    assert caught.value.code == 501
