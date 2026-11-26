"""Recusa de métodos de escrita: a resposta não pode se perder em RST no Windows."""

import socket
import threading
import time

import pytest

from custody_watch import multicamera_server
from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_media import FrameImages
from custody_watch.multicamera_server import ReplayServer
from custody_watch.multicamera_view import ReplayView
from tests.multicamera_fixture import make_registry


@pytest.fixture
def servidor(tmp_path):
    registry = load_camera_registry(make_registry(tmp_path))
    server = ReplayServer(ReplayView(registry), FrameImages(registry), port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _ler_cabecalhos(sock: socket.socket) -> bytes:
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        pedaco = sock.recv(4096)
        if not pedaco:
            raise AssertionError(f"conexão fechada antes dos cabeçalhos: {buffer!r}")
        buffer += pedaco
    return buffer


def _escrever_com_corpo(porta: int, metodo: str, corpo: bytes) -> bytes:
    cabecalho = (
        f"{metodo} /api/contacts HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(corpo)}\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", porta), timeout=5) as sock:
        sock.sendall(cabecalho + corpo)
        return _ler_cabecalhos(sock)


def test_recusa_de_escrita_com_corpo_sempre_entrega_a_resposta(servidor):
    corpo = b"x" * 65536  # a 8 KiB não reproduziu em 1000 tentativas; a 64 KiB falha ~3%
    falhas = []
    for i in range(200):
        metodo = ("POST", "PUT", "PATCH", "DELETE")[i % 4]
        try:
            resposta = _escrever_com_corpo(servidor, metodo, corpo)
        except (OSError, AssertionError) as erro:
            falhas.append((i, metodo, repr(erro)))
            continue
        if not resposta.startswith(b"HTTP/1.") or b" 501 " not in resposta.split(b"\r\n")[0]:
            falhas.append((i, metodo, resposta[:60]))
    assert not falhas, f"{len(falhas)}/200 recusas sem resposta completa: {falhas[:3]}"


def test_recusa_fecha_a_conexao_depois_de_responder(servidor):
    resposta = _escrever_com_corpo(servidor, "POST", b"{}").lower()
    assert b"connection: close" in resposta


def test_corpo_anunciado_que_nunca_chega_nao_prende_a_thread(servidor, monkeypatch):
    monkeypatch.setattr(multicamera_server._Handler, "refusal_body_timeout", 0.3)
    cabecalho = (
        f"POST /api/contacts HTTP/1.1\r\nHost: 127.0.0.1:{servidor}\r\nContent-Length: 100\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", servidor), timeout=5) as sock:
        sock.sendall(cabecalho)
        inicio = time.monotonic()
        resposta = _ler_cabecalhos(sock)
    assert b" 501 " in resposta.split(b"\r\n")[0]
    assert time.monotonic() - inicio < 3


def test_content_length_invalido_ou_enorme_responde_sem_ler(servidor):
    for valor in (b"abc", b"-5", b"99999999999"):
        inicio = f"POST /api/contacts HTTP/1.1\r\nHost: 127.0.0.1:{servidor}\r\n"
        cabecalho = inicio.encode() + b"Content-Length: " + valor + b"\r\n\r\n"
        with socket.create_connection(("127.0.0.1", servidor), timeout=5) as sock:
            sock.sendall(cabecalho)
            resposta = _ler_cabecalhos(sock)
        assert b" 501 " in resposta.split(b"\r\n")[0], valor


def _pedido_cru(porta: int, dados: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", porta), timeout=5.0) as sock:
        sock.sendall(dados)
        resposta = b""
        while True:
            parte = sock.recv(4096)
            if not parte:
                break
            resposta += parte
    return resposta


def test_caminho_invalido_devolve_400(servidor):
    resposta = _pedido_cru(
        servidor, f"GET http://[x HTTP/1.1\r\nHost: 127.0.0.1:{servidor}\r\n\r\n".encode()
    )
    assert resposta.startswith(b"HTTP/1.0 400")


def test_pedido_http_0_9_devolve_400(servidor):
    resposta = _pedido_cru(servidor, b"GET /api/state\r\n\r\n")
    assert resposta.startswith(b"HTTP/1.0 400")
