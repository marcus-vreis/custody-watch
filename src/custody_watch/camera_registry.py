"""Cadastro versionado de câmeras gravadas no mesmo ambiente.

Compõe manifests de sequência v1, sem mudar seu contrato. Coordenadas de câmera
e cobertura são declarações no referencial do andar; não comprovam visibilidade.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .recorded_predictions import RecordedSequence, file_digest, load_recording
from .types import Point


def _object(value, label: str, fields: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: esperado objeto")
    missing, unknown = fields - value.keys(), value.keys() - fields
    if missing or unknown:
        raise ValueError(
            f"{label}: campos ausentes {sorted(missing)}; desconhecidos {sorted(unknown)}"
        )
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: esperado texto não vazio")
    return value


def _identifier(value, label: str) -> str:
    value = _text(value, label)
    if value != value.strip():
        raise ValueError(f"{label}: ID não pode ter espaços nas extremidades")
    return value


def finite_number(value, label: str) -> float:
    """Recusa coerção de texto/booleano e overflow, inclusive de inteiros JSON."""
    if type(value) not in (int, float):
        raise ValueError(f"{label}: esperado número finito")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{label}: esperado número finito") from error
    if not math.isfinite(result):
        raise ValueError(f"{label}: esperado número finito")
    return result


def _list(value, label: str) -> list:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: esperada lista não vazia")
    return value


def _point(value, label: str) -> Point:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label}: esperado par de coordenadas em metros")
    return Point(*(finite_number(part, label) for part in value))


def _coverage(value) -> tuple[Point, ...] | None:
    if value is None:
        return None
    points = tuple(_point(item, "coverage_m") for item in _list(value, "coverage_m"))
    if len(set(points)) < 3:
        raise ValueError("coverage_m: esperado polígono com pelo menos três vértices distintos")
    area = sum(a.x * b.y - a.y * b.x for a, b in zip(points, (*points[1:], points[0]), strict=True))
    if finite_number(area, "coverage_m.área") == 0:
        raise ValueError("coverage_m: polígono degenerado")
    return points


def _unique(values, label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label}: ID duplicado")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON: chave duplicada {key!r}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"JSON: número não finito {value}")


@dataclass(frozen=True)
class CoordinateFrame:
    frame_id: str
    unit: str
    origin: str
    x_axis: str
    y_axis: str


@dataclass(frozen=True)
class Floor:
    floor_id: str
    coordinate_frame: CoordinateFrame


@dataclass(frozen=True)
class ReplayClock:
    offset_s: float
    uncertainty_s: float | None
    method: str

    def global_time(self, media_t: float) -> float:
        """t_global = t_mídia + offset; não subtrai o início do clip."""
        return finite_number(finite_number(media_t, "media_t") + self.offset_s, "tempo global")

    def interval(self, media_t: float) -> tuple[float, float] | None:
        t = self.global_time(media_t)
        if self.uncertainty_s is None:
            return None
        return (
            finite_number(t - self.uncertainty_s, "tempo global mínimo"),
            finite_number(t + self.uncertainty_s, "tempo global máximo"),
        )


@dataclass(frozen=True)
class CameraRecording:
    camera_id: str
    floor_id: str
    segment_id: str
    position_m: Point | None
    coverage_m: tuple[Point, ...] | None
    projection_frame_id: str | None
    clock: ReplayClock
    recording: RecordedSequence
    recording_path: Path | None = None


@dataclass(frozen=True)
class CameraRegistry:
    site_id: str
    floors: tuple[Floor, ...]
    cameras: tuple[CameraRecording, ...]
    manifest_sha256: str


def _floor(value) -> Floor:
    data = _object(value, "floor", {"floor_id", "coordinate_frame"})
    frame = _object(
        data["coordinate_frame"],
        "coordinate_frame",
        {"frame_id", "unit", "origin", "x_axis", "y_axis"},
    )
    if frame["unit"] != "m":
        raise ValueError("coordinate_frame.unit: esperado m")
    return Floor(
        _identifier(data["floor_id"], "floor_id"),
        CoordinateFrame(
            _identifier(frame["frame_id"], "frame_id"),
            "m",
            *(_text(frame[name], name) for name in ("origin", "x_axis", "y_axis")),
        ),
    )


def _clock(value) -> ReplayClock:
    data = _object(value, "clock", {"offset_s", "uncertainty_s", "method"})
    offset = finite_number(data["offset_s"], "clock.offset_s")
    uncertainty = data["uncertainty_s"]
    if uncertainty is not None:
        uncertainty = finite_number(uncertainty, "clock.uncertainty_s")
        if uncertainty < 0:
            raise ValueError("clock.uncertainty_s: esperado valor não negativo ou null")
    return ReplayClock(offset, uncertainty, _text(data["method"], "clock.method"))


def _recording(base: Path, value) -> RecordedSequence:
    ref = _object(value, "recording", {"path", "size_bytes", "sha256"})
    relative = _text(ref["path"], "recording.path")
    if Path(relative).is_absolute() or PureWindowsPath(relative).drive or relative.startswith("\\"):
        raise ValueError("recording.path: use caminho relativo ao cadastro")
    size = ref["size_bytes"]
    if type(size) is not int or size < 0:
        raise ValueError("recording.size_bytes: esperado inteiro não negativo")
    digest = ref["sha256"]
    if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("recording.sha256: esperado hash SHA-256 hexadecimal")
    path = (base / relative).resolve()
    if not path.is_file():
        raise ValueError("recording: arquivo não encontrado")
    if path.stat().st_size != size:
        raise ValueError("recording: tamanho diverge do cadastro")
    if file_digest(path) != digest:
        raise ValueError("recording: hash diverge do cadastro")
    recording = load_recording(path)
    if recording.manifest_sha256 != digest:
        raise ValueError("recording: hash alterado durante a leitura")
    return recording


def _camera(base: Path, value, floors: dict[str, Floor]) -> CameraRecording:
    data = _object(
        value,
        "camera",
        {
            "camera_id",
            "floor_id",
            "segment_id",
            "position_m",
            "coverage_m",
            "projection_frame_id",
            "clock",
            "recording",
        },
    )
    camera_id = _identifier(data["camera_id"], "camera_id")
    floor_id = _identifier(data["floor_id"], "floor_id")
    if floor_id not in floors:
        raise ValueError("camera.floor_id: andar inexistente")
    projection = data["projection_frame_id"]
    if projection is not None and projection != floors[floor_id].coordinate_frame.frame_id:
        raise ValueError("projection_frame_id: esperado referencial do andar ou null")
    clock = _clock(data["clock"])
    recording = _recording(base, data["recording"])
    # Recusar overflow e perda da ordem temporal antes de publicar qualquer frame.
    for key in ("start_s", "end_s"):
        clock.interval(recording.manifest["clock"][key])
    previous = -math.inf
    for frame in recording.frames:
        current = clock.global_time(frame.t)
        if current <= previous:
            raise ValueError("clock.offset_s: resolução insuficiente no tempo global")
        previous = current
    return CameraRecording(
        camera_id,
        floor_id,
        _identifier(data["segment_id"], "segment_id"),
        None if data["position_m"] is None else _point(data["position_m"], "position_m"),
        _coverage(data["coverage_m"]),
        projection,
        clock,
        recording,
        (base / data["recording"]["path"]).resolve(),
    )


def load_camera_registry(path: Path | str) -> CameraRegistry:
    """Valida cadastro e todas as sequências antes de oferecer o replay."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    data = _object(
        json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant),
        "camera_registry",
        {"schema_version", "site_id", "floors", "cameras"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("camera_registry.schema_version: esperado inteiro 1")
    floors = tuple(_floor(item) for item in _list(data["floors"], "floors"))
    _unique([floor.floor_id for floor in floors], "floor_id")
    _unique([floor.coordinate_frame.frame_id for floor in floors], "frame_id")
    by_id = {floor.floor_id: floor for floor in floors}
    cameras = tuple(_camera(path.parent, item, by_id) for item in _list(data["cameras"], "cameras"))
    _unique([camera.camera_id for camera in cameras], "camera_id")
    return CameraRegistry(
        _identifier(data["site_id"], "site_id"), floors, cameras, hashlib.sha256(raw).hexdigest()
    )
