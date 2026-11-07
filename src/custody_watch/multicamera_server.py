"""Servidor local do laboratório: assets fixos, snapshots e imagens qualificadas."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import cv2

from .multicamera_media import FrameImages
from .multicamera_view import ReplayView

ASSETS = {
    "/": ("multicamera.html", "text/html; charset=utf-8"),
    "/assets/multicamera.css": ("multicamera.css", "text/css; charset=utf-8"),
    "/assets/multicamera.js": ("multicamera.js", "text/javascript; charset=utf-8"),
    "/assets/replay_link.js": ("replay_link.js", "text/javascript; charset=utf-8"),
}


def _query(query: str, fields: set[str]) -> dict[str, str]:
    values = parse_qs(query, keep_blank_values=True)
    if values.keys() != fields or any(len(value) != 1 or not value[0] for value in values.values()):
        raise ValueError("parâmetros ausentes, repetidos ou desconhecidos")
    return {key: value[0] for key, value in values.items()}


class ReplayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, view: ReplayView, images: FrameImages, *, port: int = 8766):
        # Validar também o intervalo da interface antes de abrir a porta.
        self.config = view.config()
        self.view = view
        self.images = images
        super().__init__(("127.0.0.1", port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: ReplayServer

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
            elif route.path == "/api/config":
                _query(route.query, set())
                self._json(200, self.server.config)
            elif route.path == "/api/state":
                fields = _query(route.query, {"t"})
                self._json(200, self.server.view.snapshot(float(fields["t"])))
            elif route.path == "/api/image":
                fields = _query(route.query, {"camera_id", "segment_id", "frame_index"})
                image = self.server.images.get(
                    fields["camera_id"], fields["segment_id"], int(fields["frame_index"])
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

    def log_message(self, *_args):
        # Não registrar query strings ou caminhos das fontes na saída HTTP.
        pass
