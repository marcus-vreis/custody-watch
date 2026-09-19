"""O servidor da tela ao vivo: página, vídeo MJPEG e estado em JSON."""

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from custody_watch.painel import Painel
from custody_watch.servidor import Servidor


@pytest.fixture
def no_ar():
    painel = Painel()
    servidor = Servidor(painel, "127.0.0.1", 0)
    thread = threading.Thread(target=servidor.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield painel, servidor, f"http://127.0.0.1:{servidor.porta}"
    servidor.encerra()
    thread.join(5.0)


def _get(url: str):
    return urllib.request.urlopen(url, timeout=5.0)


def test_pagina_aponta_para_o_video_e_para_o_estado(no_ar):
    _, _, base = no_ar

    with _get(base + "/") as resposta:
        corpo = resposta.read().decode("utf-8")

    assert resposta.headers["Content-Type"].startswith("text/html")
    assert "/video" in corpo
    assert "/estado" in corpo


def test_estado_sai_em_json_e_nunca_de_cache(no_ar):
    painel, _, base = no_ar
    painel.atualiza(np.zeros((24, 32, 3), np.uint8), {"t": 12.5, "fila": []})

    with _get(base + "/estado") as resposta:
        corpo = json.loads(resposta.read())

    assert corpo == {"t": 12.5, "fila": []}
    assert resposta.headers["Cache-Control"] == "no-store"


def test_quadro_avulso_antes_do_primeiro_quadro_e_indisponivel(no_ar):
    _, _, base = no_ar

    with pytest.raises(urllib.error.HTTPError) as erro:
        _get(base + "/quadro.jpg")

    assert erro.value.code == 503


def test_quadro_avulso_e_jpeg(no_ar):
    painel, _, base = no_ar
    painel.atualiza(np.zeros((24, 32, 3), np.uint8), {})

    with _get(base + "/quadro.jpg") as resposta:
        assert resposta.headers["Content-Type"] == "image/jpeg"
        assert resposta.read().startswith(b"\xff\xd8")


def test_video_e_mjpeg(no_ar):
    painel, _, base = no_ar
    painel.atualiza(np.zeros((24, 32, 3), np.uint8), {})

    with _get(base + "/video") as resposta:
        tipo = resposta.headers["Content-Type"]
        fronteira = resposta.readline()
        cabecalhos = {}
        while linha := resposta.readline().strip():
            chave, valor = linha.decode("ascii").split(":", 1)
            cabecalhos[chave.lower()] = valor.strip()
        jpeg = resposta.read(int(cabecalhos["content-length"]))

    assert tipo.startswith("multipart/x-mixed-replace")
    assert "boundary=quadro" in tipo
    assert fronteira == b"--quadro\r\n"
    assert cabecalhos["content-type"] == "image/jpeg"
    assert jpeg.startswith(b"\xff\xd8")


def test_caminho_desconhecido_e_404(no_ar):
    _, _, base = no_ar

    with pytest.raises(urllib.error.HTTPError) as erro:
        _get(base + "/../../etc/passwd")

    assert erro.value.code == 404


def test_encerrar_fecha_os_videos_abertos(no_ar):
    """MJPEG é uma resposta que não termina. Sem isto, cada aba aberta
    seguraria o processo vivo depois do Ctrl+C."""
    painel, servidor, base = no_ar
    painel.atualiza(np.zeros((24, 32, 3), np.uint8), {})
    resposta = _get(base + "/video")
    resposta.read(100)

    servidor.encerra()

    resto = resposta.read()
    resposta.close()
    assert isinstance(resto, bytes)


def test_servidor_so_escuta_na_maquina_por_padrao():
    """É imagem de câmera de aeroporto. Abrir para a rede é decisão de quem
    roda, nunca um default."""
    servidor = Servidor(Painel(), porta=0)
    try:
        assert servidor.server_address[0] == "127.0.0.1"
    finally:
        servidor.server_close()
