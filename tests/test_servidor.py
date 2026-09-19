"""O servidor da tela ao vivo: página, vídeo MJPEG e estado em JSON."""

import json
import shutil
import ssl
import subprocess
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from custody_watch.painel import Painel
from custody_watch.servidor import Servidor, contexto_tls


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


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.0.10", "::", "camera.aeroporto.local"])
def test_abrir_para_a_rede_sem_tls_e_recusado(host):
    """Na rede, HTTP em claro deixa qualquer um no mesmo segmento assistir a
    câmera. Um aviso no terminal não protege ninguém; a recusa protege."""
    with pytest.raises(ValueError, match="--cert"):
        Servidor(Painel(), host, 0)


@pytest.fixture
def certificado(tmp_path):
    """Certificado descartável, gerado na hora -- nenhuma chave no repositório."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl indisponível")
    cert, chave = tmp_path / "cert.pem", tmp_path / "chave.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(chave),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, chave


def test_com_tls_o_estado_sai_cifrado(certificado):
    cert, chave = certificado
    painel = Painel()
    painel.atualiza(np.zeros((24, 32, 3), np.uint8), {"t": 3.0})
    servidor = Servidor(painel, "127.0.0.1", 0, tls=contexto_tls(cert, chave))
    thread = threading.Thread(target=servidor.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        cliente = ssl.create_default_context(cafile=str(cert))
        url = f"https://127.0.0.1:{servidor.porta}/estado"
        with urllib.request.urlopen(url, timeout=5.0, context=cliente) as resposta:
            assert json.loads(resposta.read()) == {"t": 3.0}

        with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
            urllib.request.urlopen(f"http://127.0.0.1:{servidor.porta}/estado", timeout=5.0)
    finally:
        servidor.encerra()
        thread.join(5.0)


def test_conexao_sem_tls_na_porta_cifrada_nao_vira_traceback(certificado, capsys):
    """Uma aba esquecida em http:// consulta o estado duas vezes por segundo.
    Cada tentativa virava um traceback inteiro no terminal do operador; quem
    conectou errado já vê o erro do lado dele."""
    cert, chave = certificado
    servidor = Servidor(Painel(), "127.0.0.1", 0, tls=contexto_tls(cert, chave))
    thread = threading.Thread(target=servidor.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        for _ in range(3):
            with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
                urllib.request.urlopen(f"http://127.0.0.1:{servidor.porta}/estado", timeout=5.0)
    finally:
        servidor.encerra()
        thread.join(5.0)

    assert "Traceback" not in capsys.readouterr().err
