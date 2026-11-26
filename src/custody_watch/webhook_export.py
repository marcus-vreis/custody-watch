"""Exportador opcional de eventos por webhook, lendo o journal já gravado (#137).

Roda em processo separado e nunca toca o caminho quente: acompanha as partes
de uma sessão do journal (`event_journal.py`) e entrega cada registro
``event`` que já é uma linha completa (terminada em LF, isto é, flushada pelo
escritor). Fragmento final sem LF nunca é enviado.

Semântica at-least-once: o cursor só avança depois de 2xx ou de descarte
contado, então um reinício pode reenviar o último evento. Cada carga leva um
``id`` estável (``<session_id>:<sequence>``) para o destino deduplicar.

Falhas transitórias (timeout, conexão, 5xx, 408, 429) são retentadas com
backoff exponencial limitado e, esgotadas, PARAM a passada sem avançar o
cursor: o journal é a fila durável, e descartar apagaria um alerta só porque o
destino caiu. 4xx de conteúdo descarta e conta, para um evento venenoso não
bloquear os seguintes. Redirecionamento e 4xx que indicam destino mal
configurado (401, 403, 404...) também param sem avançar: descartar ali
apagaria todos os eventos por erro de configuração.

O journal é tratado como entrada não confiável: linha com tamanho limitado,
JSON estrito, envelope validado e carga montada só com campos permitidos (P4).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import random as _random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .journal_reader import validate_record

SECRET_ENV = "CUSTODY_WATCH_WEBHOOK_SECRET"
MIN_SECRET_BYTES = 32
SIGNATURE_HEADER = "X-Custody-Signature"
TIMESTAMP_HEADER = "X-Custody-Timestamp"
EVENT_ID_HEADER = "X-Custody-Event-Id"
PAYLOAD_SCHEMA = "custody-watch.webhook.v1"
SIGNATURE_TOLERANCE_S = 300
RETRYABLE_4XX = frozenset({408, 429})
DESTINATION_ERRORS = frozenset({401, 403, 404, 405, 407, 410, 421})
MAX_CURSOR_BYTES = 4096
_OVERSIZE_CHUNK = 65536

# Evidências conhecidas (custody.py, party.py, bag_registry.py, orchestrator.py).
# Qualquer outra chave, ou valor fora do tipo esperado, não sai do processo.
_EVIDENCE_NUMBERS = frozenset(
    {
        "distance_m",
        "elapsed_s",
        "radius_m",
        "moved_m",
        "nearest_person_m",
        "similarity",
        "margin",
        "extent_member_m",
        "extent_candidate_m",
        "overlap_s",
        "max_separation_m",
    }
)
_EVIDENCE_IDS = frozenset({"carrier", "owner_party", "party", "from_track"})
_EVIDENCE_ID_LISTS = frozenset({"candidate_people", "neighbour_bags", "members"})
_EVIDENCE_LABELS = {"bond": frozenset({"strong", "weak"}), "origem": frozenset({"operador"})}
_EVIDENCE_POINTS = frozenset({"anchor"})
_MAX_ID_LIST = 64


class WebhookConfigError(ValueError):
    """Configuração recusada; a mensagem nunca ecoa URL nem segredo."""


class TransportError(OSError):
    """Falha de rede ou timeout, sem status HTTP."""


Transport = Callable[[str, bytes, Mapping[str, str], float], int]


def validate_destination(url: str) -> str:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise WebhookConfigError("URL do webhook: caractere de controle ou espaço")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebhookConfigError("URL do webhook: porta ou host inválido") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise WebhookConfigError("URL do webhook: esquema deve ser http ou https")
    if "@" in parts.netloc:
        raise WebhookConfigError("URL do webhook: credencial na URL não é aceita; use HMAC")
    if parts.query:
        raise WebhookConfigError("URL do webhook: query string não é aceita")
    if parts.fragment:
        raise WebhookConfigError("URL do webhook: fragmento não é aceito")
    host = parts.hostname
    if not host:
        raise WebhookConfigError("URL do webhook: host ausente")
    if port == 0:
        raise WebhookConfigError("URL do webhook: porta inválida")
    if scheme == "http" and not _is_loopback(host):
        raise WebhookConfigError("URL do webhook: https é obrigatório fora de loopback")
    return url


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_secret(environ: Mapping[str, str]) -> bytes:
    value = environ.get(SECRET_ENV)
    if not value:
        raise WebhookConfigError(f"defina {SECRET_ENV} com o segredo HMAC")
    secret = value.encode("utf-8")
    if len(secret) < MIN_SECRET_BYTES:
        raise WebhookConfigError(f"{SECRET_ENV}: esperado ao menos {MIN_SECRET_BYTES} bytes")
    return secret


def sign_body(secret: bytes, timestamp: int, body: bytes) -> str:
    material = str(timestamp).encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret, material, hashlib.sha256).hexdigest()


def verify_signature(
    secret: bytes,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: float,
    tolerance_s: float = SIGNATURE_TOLERANCE_S,
) -> bool:
    """Referência para o receptor: confere HMAC e janela de replay."""
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        return False
    if abs(now - int(timestamp)) > tolerance_s:
        return False
    return hmac.compare_digest(sign_body(secret, int(timestamp), body), signature)


@dataclass(frozen=True)
class ExportConfig:
    url: str
    secret: bytes = field(repr=False)
    timeout_s: float = 5.0
    max_attempts: int = 5
    backoff_base_s: float = 0.5
    backoff_max_s: float = 30.0
    jitter: float = 0.5
    max_line_bytes: int = 65536

    def __post_init__(self) -> None:
        validate_destination(self.url)
        if not isinstance(self.secret, bytes) or len(self.secret) < MIN_SECRET_BYTES:
            raise WebhookConfigError(f"segredo HMAC: esperado ao menos {MIN_SECRET_BYTES} bytes")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 100:
            raise WebhookConfigError("max_attempts: esperado inteiro entre 1 e 100")
        if type(self.max_line_bytes) is not int or not 1024 <= self.max_line_bytes <= 16_777_216:
            raise WebhookConfigError("max_line_bytes: esperado inteiro entre 1024 e 16777216")
        for name in ("timeout_s", "backoff_base_s", "backoff_max_s"):
            value = getattr(self, name)
            if not isinstance(value, int | float) or not 0 < value <= 3600:
                raise WebhookConfigError(f"{name}: esperado número em (0, 3600]")
        if not isinstance(self.jitter, int | float) or not 0 <= self.jitter <= 1:
            raise WebhookConfigError("jitter: esperado número em [0, 1]")


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _filter_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in evidence.items():
        if key in _EVIDENCE_NUMBERS:
            ok = value is None or _finite_number(value)
        elif key in _EVIDENCE_IDS:
            ok = value is None or type(value) is int
        elif key in _EVIDENCE_ID_LISTS:
            ok = (
                type(value) is list
                and len(value) <= _MAX_ID_LIST
                and all(type(item) is int for item in value)
            )
        elif key in _EVIDENCE_LABELS:
            ok = isinstance(value, str) and value in _EVIDENCE_LABELS[key]
        elif key in _EVIDENCE_POINTS:
            ok = type(value) is list and len(value) == 2 and all(map(_finite_number, value))
        else:
            ok = False
        if ok:
            kept[key] = value
    return kept


def build_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Recebe um registro ``event`` já validado; copia só campos relacionais."""
    event = record["payload"]
    return {
        "schema": PAYLOAD_SCHEMA,
        "id": f"{record['session_id']}:{record['sequence']}",
        "session_id": record["session_id"],
        "segment_id": record["segment_id"],
        "sequence": record["sequence"],
        "event": {
            "kind": event["kind"],
            "t_start": event["t_start"],
            "t_end": event["t_end"],
            "subject": event["subject"],
            "bag": event["bag"],
            "party": event["party"],
            "evidence": _filter_evidence(event["evidence"]),
        },
    }


# --- cursor ------------------------------------------------------------------


@dataclass
class JournalCursor:
    session_id: str
    part: int = 0
    offset: int = 0
    next_sequence: int = 0


def resolve_cursor_path(path: Path | str) -> Path:
    """Caminho real do cursor; recusa o que cai fora do diretório de trabalho.

    A base é fixa (o diretório de onde o exportador foi iniciado) para que um
    argumento de linha de comando não leia nem sobrescreva arquivo alheio.
    """
    base = os.path.realpath(os.getcwd())
    resolved = os.path.realpath(os.path.join(base, path))
    if not os.path.normcase(resolved).startswith(os.path.normcase(base) + os.sep):
        raise WebhookConfigError("cursor fora do diretório de trabalho do exportador")
    return Path(resolved)


def load_cursor(path: Path | str, session_id: str) -> JournalCursor:
    path = Path(path)
    if not path.exists():
        return JournalCursor(session_id)
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_CURSOR_BYTES:
            raise ValueError("cursor grande demais")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.keys() != {
            "version",
            "session_id",
            "part",
            "offset",
            "next_sequence",
        }:
            raise ValueError("campos do cursor")
        if data["version"] != 1 or data["session_id"] != session_id:
            raise ValueError("cursor de outra versão ou sessão")
        numbers = [data["part"], data["offset"], data["next_sequence"]]
        if any(type(n) is not int or n < 0 for n in numbers) or data["part"] > 999999:
            raise ValueError("posição do cursor")
    except (OSError, ValueError) as error:
        raise WebhookConfigError(f"cursor recusado: {type(error).__name__}") from None
    return JournalCursor(session_id, *numbers)


def save_cursor(path: Path | str, cursor: JournalCursor) -> None:
    """Escrita atômica: arquivo temporário, fsync e os.replace."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    content = json.dumps({"version": 1, **asdict(cursor)}, sort_keys=True)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


# --- leitura limitada ----------------------------------------------------------


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("chave JSON duplicada")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("constante JSON não finita")


def _read_line(handle, max_line_bytes: int) -> tuple[bytes | None, int, bool]:
    """(conteúdo ou None se acima do limite, bytes consumidos, terminou com LF)."""
    line = handle.readline(max_line_bytes + 1)
    if line.endswith(b"\n") or len(line) <= max_line_bytes:
        return line, len(line), line.endswith(b"\n")
    consumed = len(line)
    while True:
        chunk = handle.readline(_OVERSIZE_CHUNK)
        consumed += len(chunk)
        if not chunk or chunk.endswith(b"\n"):
            return None, consumed, chunk.endswith(b"\n")


def _parse(line: bytes | None, cursor: JournalCursor) -> dict[str, Any] | None:
    if line is None:
        return None
    try:
        record = json.loads(
            line.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        validate_record(record)
    except (ValueError, TypeError, RecursionError):
        return None
    if record["session_id"] != cursor.session_id or record["sequence"] < cursor.next_sequence:
        return None
    return record


def _iter_part(
    path: Path, part: int, offset: int, rotated: bool, cursor: JournalCursor, max_line_bytes: int
) -> Iterator[tuple[dict[str, Any] | None, int, int]]:
    """Linhas de uma parte a partir de `offset`; para no fragmento sem LF se ela é a última."""
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line, consumed, terminated = _read_line(handle, max_line_bytes)
            if consumed == 0 or not (terminated or rotated):
                return
            offset += consumed
            yield (_parse(line, cursor) if terminated else None), part, offset


def iter_confirmed(
    directory: Path, cursor: JournalCursor, max_line_bytes: int
) -> Iterator[tuple[dict[str, Any] | None, int, int]]:
    """Gera (registro ou None se inválido, parte, offset após a linha).

    Para no primeiro fragmento sem LF da última parte. A existência da parte
    seguinte é conferida ANTES de ler até o EOF: o escritor só cria a próxima
    depois de fechar a atual, então ela já estava completa.
    """
    part, offset = cursor.part, cursor.offset
    while True:
        path = directory / f"{part:06d}.jsonl"
        if not path.is_file():
            return
        rotated = (directory / f"{part + 1:06d}.jsonl").is_file()
        yield from _iter_part(path, part, offset, rotated, cursor, max_line_bytes)
        if not rotated:
            return
        part, offset = part + 1, 0


# --- transporte ---------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # None faz o urllib levantar HTTPError com o 3xx, sem seguir o Location.
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        error.close()
        return error.code
    except (OSError, http.client.HTTPException) as error:
        # Sem str(error): a mensagem de URLError pode carregar o host.
        raise TransportError(type(error).__name__) from None


# --- exportador ---------------------------------------------------------------


@dataclass
class ExportStats:
    sent: int = 0
    discarded: int = 0
    attempts: int = 0
    retries: int = 0
    invalid_records: int = 0
    skipped_records: int = 0
    stalled: int = 0
    last_delivery_s: float | None = None
    max_delivery_s: float | None = None
    backlog_bytes: int | None = None

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class WebhookExporter:
    def __init__(
        self,
        session_dir: Path | str,
        cursor_path: Path | str,
        config: ExportConfig,
        *,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        random: Callable[[], float] = _random.random,
    ) -> None:
        self.session_dir = Path(session_dir)
        self.cursor_path = resolve_cursor_path(cursor_path)
        if self.cursor_path.suffix.lower() == ".jsonl":
            raise WebhookConfigError("cursor não pode usar a extensão .jsonl do journal")
        if not self.cursor_path.parent.is_dir():
            raise WebhookConfigError("diretório do cursor não existe")
        self.config = config
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._random = random
        self.cursor = load_cursor(self.cursor_path, self.session_dir.name)
        self.stats = ExportStats()

    def backoff(self, retry: int) -> float:
        delay = min(self.config.backoff_max_s, self.config.backoff_base_s * 2 ** (retry - 1))
        return delay * (1 - self.config.jitter * self._random())

    def _attempt(self, body: bytes, event_id: str) -> int | None:
        timestamp = int(self._wall_clock())
        headers = {
            "Content-Type": "application/json",
            EVENT_ID_HEADER: event_id,
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign_body(self.config.secret, timestamp, body),
        }
        self.stats.attempts += 1
        try:
            return self._transport(self.config.url, body, headers, self.config.timeout_s)
        except TransportError:
            return None

    def deliver(self, payload: dict[str, Any]) -> str:
        """'delivered', 'discarded' ou 'stalled' (não avança o cursor)."""
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        started = self._monotonic()
        for attempt in range(1, self.config.max_attempts + 1):
            if attempt > 1:
                self.stats.retries += 1
                self._sleep(self.backoff(attempt - 1))
            status = self._attempt(body, payload["id"])
            if status is None or status >= 500 or status in RETRYABLE_4XX or status < 200:
                continue
            if status < 300:
                self._record_latency(started)
                return "delivered"
            if status < 400 or status in DESTINATION_ERRORS:
                return "stalled"
            return "discarded"
        return "stalled"

    def _record_latency(self, started: float) -> None:
        elapsed = self._monotonic() - started
        self.stats.last_delivery_s = elapsed
        self.stats.max_delivery_s = max(elapsed, self.stats.max_delivery_s or 0.0)

    def _advance(self, part: int, offset: int, record: dict[str, Any] | None) -> None:
        self.cursor.part, self.cursor.offset = part, offset
        if record is not None:
            self.cursor.next_sequence = record["sequence"] + 1

    def _export_event(self, record: dict[str, Any]) -> bool:
        """Entrega um evento e conta o desfecho; False se a passada deve parar."""
        outcome = self.deliver(build_payload(record))
        if outcome == "stalled":
            self.stats.stalled += 1
            return False
        if outcome == "discarded":
            self.stats.discarded += 1
        else:
            self.stats.sent += 1
        return True

    def run_once(self) -> str:
        """Drena o que está confirmado: 'caught_up' ou 'stalled'."""
        dirty = False
        lines = iter_confirmed(self.session_dir, self.cursor, self.config.max_line_bytes)
        try:
            for record, part, offset in lines:
                is_event = record is not None and record["kind"] == "event"
                if is_event and not self._export_event(record):
                    if dirty:
                        save_cursor(self.cursor_path, self.cursor)
                    return "stalled"
                if record is None:
                    self.stats.invalid_records += 1
                elif not is_event:
                    self.stats.skipped_records += 1
                self._advance(part, offset, record)
                dirty = not is_event
                if is_event:
                    save_cursor(self.cursor_path, self.cursor)
        finally:
            lines.close()
            self.stats.backlog_bytes = self._backlog_bytes()
        if dirty or not self.cursor_path.exists():
            save_cursor(self.cursor_path, self.cursor)
        return "caught_up"

    def _backlog_bytes(self) -> int:
        total, part = 0, self.cursor.part
        while (path := self.session_dir / f"{part:06d}.jsonl").is_file():
            size = path.stat().st_size
            total += size - (self.cursor.offset if part == self.cursor.part else 0)
            part += 1
        return max(total, 0)
