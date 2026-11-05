"""Leitura incremental do journal, sem restaurar estado de custódia."""

from __future__ import annotations

import json
import math
import re
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from custody_watch.events import EventKind

_ENVELOPE_FIELDS = {"schema_version", "session_id", "segment_id", "sequence", "kind", "payload"}
_EVENT_FIELDS = {"kind", "t_start", "t_end", "subject", "bag", "party", "evidence"}
_REASONS = {
    "segment_start": {"start", "reconnected"},
    "segment_end": {"user_stop", "end_of_file", "initialization_failed"},
    "segment_interrupted": {
        "read_failed",
        "capture_exception",
        "processing_exception",
        "source_stale",
    },
}
_KINDS = {*_REASONS, "event"}
_EVENT_KINDS = {kind.value for kind in EventKind}
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PART_NAME = re.compile(r"[0-9]{6}\.jsonl")


class JournalFormatError(ValueError):
    """Corrupção ou incompatibilidade no contrato do journal."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"chave JSON duplicada: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"constante JSON não finita: {value}")


def _check_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("número JSON não finito")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


def _validate_event(payload: dict[str, Any]) -> None:
    if not payload.keys() >= _EVENT_FIELDS:
        raise ValueError("evento sem campos obrigatórios")
    if not isinstance(payload["kind"], str) or payload["kind"] not in _EVENT_KINDS:
        raise ValueError("kind de evento desconhecido")
    for field in ("t_start", "t_end"):
        if type(payload[field]) not in (int, float):
            raise ValueError(f"{field} deve ser numérico")
    if not 0 <= payload["t_start"] <= payload["t_end"]:
        raise ValueError("tempos devem respeitar t_end >= t_start >= 0")
    for field in ("subject", "bag", "party"):
        value = payload[field]
        if value is not None and (type(value) is not int or (field != "bag" and value < 0)):
            raise ValueError(f"identificador inválido: {field}")
    if not isinstance(payload["evidence"], dict):
        raise ValueError("evidence deve ser objeto JSON")


def _validate_envelope(record: Any) -> None:
    if not isinstance(record, dict) or record.keys() != _ENVELOPE_FIELDS:
        raise ValueError("campos do envelope incompatíveis")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("schema_version deve ser o inteiro 1")
    for field in ("session_id", "segment_id"):
        if not isinstance(record[field], str) or _SLUG.fullmatch(record[field]) is None:
            raise ValueError(f"slug inválido: {field}")
    if type(record["sequence"]) is not int or record["sequence"] < 0:
        raise ValueError("sequence deve ser inteiro não negativo")
    kind = record["kind"]
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("kind de registro desconhecido")
    payload = record["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload deve ser objeto JSON")
    if kind == "event":
        _validate_event(payload)
    elif (
        payload.keys() != {"reason"}
        or not isinstance(payload["reason"], str)
        or payload["reason"] not in _REASONS[kind]
    ):
        raise ValueError("reason incompatível com o lifecycle")


def validate_record(record: dict) -> None:
    """Contrato compartilhado por leitor e escritor, sem ordem entre registros."""
    _check_finite(record)
    _validate_envelope(record)


def iter_journal(
    directory: Path | str,
    *,
    diagnostics: list[str] | None = None,
    recover_truncated_tail: bool = True,
) -> Iterator[dict]:
    """Valida envelopes sem carregar todo o conteúdo histórico em memória.

    Captura os nomes das partes no início da iteração. Somente o fragmento
    final sem LF da última parte pode ser ignorado; um segmento aberto no
    EOF mantém o prefixo válido e produz diagnóstico, quando solicitado.
    Sem coletor de diagnósticos, a recuperação de fragmento emite UserWarning.
    """
    directory = Path(directory)
    parts = sorted(path for path in directory.iterdir() if path.suffix.lower() == ".jsonl")
    for part in parts:
        if _PART_NAME.fullmatch(part.name) is None:
            raise JournalFormatError(f"{part.name}: nome de parte inválido")
    if not parts:
        raise JournalFormatError(f"{directory}: nenhuma parte do journal encontrada")
    for index, part in enumerate(parts):
        expected_name = f"{index:06d}.jsonl"
        if part.name != expected_name:
            raise JournalFormatError(f"{part.name}: parte ausente: {expected_name}")

    session_id: str | None = None
    active_segment: str | None = None
    used_segments: set[str] = set()
    expected_sequence = 0
    location = parts[0].name
    for part_index, part in enumerate(parts):
        with part.open("rb") as handle:
            for line_number, line in enumerate(handle, 1):
                location = f"{part.name}:{line_number}"
                if not line.endswith(b"\n"):
                    if recover_truncated_tail and part_index == len(parts) - 1:
                        message = f"{location}: fragmento final sem LF ignorado"
                        if diagnostics is not None:
                            diagnostics.append(message)
                        else:
                            warnings.warn(message, UserWarning, stacklevel=2)
                        continue
                    raise JournalFormatError(f"{location}: linha incompleta sem LF")
                try:
                    record = json.loads(
                        line.decode("utf-8"),
                        object_pairs_hook=_unique_object,
                        parse_constant=_reject_constant,
                    )
                    validate_record(record)
                    if record["sequence"] != expected_sequence:
                        raise ValueError(f"sequence esperada: {expected_sequence}")
                    if session_id is not None and record["session_id"] != session_id:
                        raise ValueError("session_id divergente")
                    segment_id = record["segment_id"]
                    kind = record["kind"]
                    if kind == "segment_start":
                        if active_segment is not None:
                            raise ValueError("segment_start antes do fechamento anterior")
                        if segment_id in used_segments:
                            raise ValueError("segment_id reutilizado")
                        used_segments.add(segment_id)
                        active_segment = segment_id
                    elif active_segment != segment_id:
                        raise ValueError("registro sem segment_start compatível")
                    elif kind in ("segment_end", "segment_interrupted"):
                        active_segment = None
                    session_id = record["session_id"]
                    expected_sequence += 1
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise JournalFormatError(f"{location}: {error}") from error
                yield record
    if active_segment is not None and diagnostics is not None:
        diagnostics.append(f"{location}: segmento incompleto: {active_segment}")
