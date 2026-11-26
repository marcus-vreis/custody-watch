"""Servidor local do laboratório: assets fixos, snapshots e imagens qualificadas."""

from __future__ import annotations

import json
from collections.abc import Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import cv2

from .multicamera_media import FrameImages
from .multicamera_view import ReplayView
from .servidor import cabecalhos, host_aceito, nomes_aceitos

ASSETS = {
    "/": ("multicamera.html", "text/html; charset=utf-8"),
    "/assets/multicamera.css": ("multicamera.css", "text/css; charset=utf-8"),
    "/assets/multicamera.js": ("multicamera.js", "text/javascript; charset=utf-8"),
    "/assets/replay_link.js": ("replay_link.js", "text/javascript; charset=utf-8"),
    "/assets/map_positions.js": ("map_positions.js", "text/javascript; charset=utf-8"),
    "/assets/camera_inspector.js": ("camera_inspector.js", "text/javascript; charset=utf-8"),
    "/assets/camera_wall.js": ("camera_wall.js", "text/javascript; charset=utf-8"),
    "/assets/contact_review.js": ("contact_review.js", "text/javascript; charset=utf-8"),
    "/assets/contact_review_ui.js": ("contact_review_ui.js", "text/javascript; charset=utf-8"),
    "/assets/review_queue.js": ("review_queue.js", "text/javascript; charset=utf-8"),
    "/assets/review_queue_ui.js": ("review_queue_ui.js", "text/javascript; charset=utf-8"),
}

# Teto do corpo descartado numa recusa de escrita; acima disso responde e fecha sem ler.
REFUSED_BODY_DRAIN_LIMIT = 256 * 1024


def _query(query: str, fields: set[str]) -> dict[str, str]:
    values = parse_qs(query, keep_blank_values=True)
    if values.keys() != fields or any(len(value) != 1 or not value[0] for value in values.values()):
        raise ValueError("parâmetros ausentes, repetidos ou desconhecidos")
    return {key: value[0] for key, value in values.items()}


def _image_fields(query: str, config: dict) -> dict[str, str]:
    provided = parse_qs(query, keep_blank_values=True)
    expected = {"camera_id", "segment_id", "frame_index"}
    if "resolution" in provided:
        expected.add("resolution")
    hashes = {"registry_sha256", "sequence_sha256"}
    if hashes & provided.keys():
        expected.update(hashes)
    fields = _query(query, expected)
    if "registry_sha256" in fields:
        camera = next((c for c in config["cameras"] if c["camera_id"] == fields["camera_id"]), None)
        if (
            fields["registry_sha256"] != config["registry_sha256"]
            or camera is None
            or fields["sequence_sha256"] != camera["sequence_sha256"]
        ):
            raise ValueError("imagem vinculada a outro cadastro ou sequência")
    return fields


class ReplayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        view: ReplayView,
        images: FrameImages,
        *,
        port: int = 8766,
        allowed_hosts: Iterable[str] = (),
    ):
        """`allowed_hosts`: nomes extras que o Host pode trazer, além dos desta máquina."""
        # Validar também o intervalo da interface antes de abrir a porta.
        self.config = view.config()
        self.view = view
        self.images = images
        self._names = nomes_aceitos("127.0.0.1", allowed_hosts)
        super().__init__(("127.0.0.1", port), _Handler)

    def accepts_host(self, authority: str | None) -> bool:
        """Barra DNS rebinding: o navegador manda o nome do atacante no Host."""
        return host_aceito(authority, self._names, porta=self.server_port, porta_padrao=80)


class _Handler(BaseHTTPRequestHandler):
    server: ReplayServer
    # Prazo (s) só para ler o corpo de uma requisição recusada; não afeta as rotas GET.
    refusal_body_timeout = 2.0

    def parse_request(self) -> bool:
        """Ponto único, antes de qualquer rota e método: Host desta máquina ou 421."""
        if not super().parse_request():
            return False
        try:
            urlsplit(self.path)
        except ValueError:
            self.close_connection = True
            self._drain_refused_body()
            if self.request_version == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
            self.send_error(HTTPStatus.BAD_REQUEST, "caminho invalido")
            return False
        hosts = cabecalhos(self.headers, "Host")
        if not hosts:
            self.close_connection = True
            self._drain_refused_body()
            if self.request_version == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
            self.send_error(HTTPStatus.BAD_REQUEST, "pedido sem Host")
            return False
        authority = hosts[0] if len(hosts) == 1 else None
        if self.server.accepts_host(authority):
            return True
        self.close_connection = True
        self._drain_refused_body()
        self.send_error(HTTPStatus.MISDIRECTED_REQUEST, "Host nao permitido")
        return False

    def _send(self, status: int, content_type: str, data: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # Uma busca nova pode cancelar o fetch anterior no navegador.
            return

    def _json(self, status: int, value: dict):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", data)

    def do_GET(self):
        route = urlsplit(self.path)
        try:
            if route.path in ASSETS:
                filename, content_type = ASSETS[route.path]
                self._send(
                    200, content_type, (Path(__file__).parent / "assets" / filename).read_bytes()
                )
            elif route.path == "/healthz":
                _query(route.query, set())
                self._json(200, {"status": "ok"})
            elif route.path == "/api/config":
                _query(route.query, set())
                self._json(200, self.server.config)
            elif route.path == "/api/state":
                fields = _query(route.query, {"t"})
                self._json(200, self.server.view.snapshot(float(fields["t"])))
            elif route.path == "/api/image":
                fields = _image_fields(route.query, self.server.config)
                image = self.server.images.get(
                    fields["camera_id"],
                    fields["segment_id"],
                    int(fields["frame_index"]),
                    resolution=fields.get("resolution", "preview"),
                )
                self._send(200, image.content_type, image.data)
            else:
                self._json(404, {"error": "Recurso não encontrado"})
        except (ValueError, OverflowError):
            self._json(400, {"error": "Requisição inválida ou imagem indisponível"})
        except (OSError, cv2.error):
            self._json(503, {"error": "Falha na leitura do recurso local"})

    def do_HEAD(self):
        self.do_GET()

    def _refuse_write(self):
        """Recusa escrita (501) sem aceitá-la, drenando um corpo pequeno antes de responder."""
        self.close_connection = True
        self._drain_refused_body()
        self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Servidor somente leitura")

    def _drain_refused_body(self):
        # Fechar o socket com bytes não lidos faz o Windows enviar RST, e o cliente pode
        # perder a resposta de recusa. Corpo inválido, grande demais ou que não chega no
        # prazo é descartado sem leitura adicional: a resposta sai e a conexão fecha.
        announced = self.headers.get("Content-Length", "0").strip()
        if not (announced.isascii() and announced.isdigit()):
            return
        remaining = int(announced)
        if remaining > REFUSED_BODY_DRAIN_LIMIT:
            return
        try:
            self.connection.settimeout(self.refusal_body_timeout)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 8192))
                if not chunk:
                    return
                remaining -= len(chunk)
        except OSError:
            return  # inclui TimeoutError: corpo anunciado que não chega

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse_write

    def log_message(self, *_args):
        # Não registrar query strings ou caminhos das fontes na saída HTTP.
        pass
