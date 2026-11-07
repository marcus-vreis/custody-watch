"""Declarações versionadas de contato, independentes de confiança ou posse."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .camera_registry import CameraRecording, CameraRegistry, finite_number


def _object(value, label, fields):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: esperado objeto")
    missing, unknown = fields - value.keys(), value.keys() - fields
    if missing or unknown:
        raise ValueError(
            f"{label}: campos ausentes {sorted(missing)}; desconhecidos {sorted(unknown)}"
        )
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label}: esperada lista")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: esperado texto não vazio")
    return value


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}: esperado inteiro não negativo")
    return value


def _hash(value, expected, label):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value) or value != expected:
        raise ValueError(f"{label}: hash incompatível")
    return value


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
class ContactObservation:
    frame_index: int
    local_track_id: int
    status: str
    point_px: tuple[float, float] | None
    provenance: str
    method: str


@dataclass(frozen=True)
class CameraContacts:
    camera_id: str
    segment_id: str
    sequence_sha256: str
    image_size_px: tuple[int, int]
    observations: tuple[ContactObservation, ...]


@dataclass(frozen=True)
class GroundContacts:
    registry_sha256: str
    manifest_sha256: str
    cameras: tuple[CameraContacts, ...]


def _size(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("image_size_px: esperado par de inteiros positivos")
    parts = tuple(_integer(v, "image_size_px") for v in value)
    if any(v == 0 for v in parts):
        raise ValueError("image_size_px: esperado par de inteiros positivos")
    for part in parts:
        finite_number(part, "image_size_px")
    return parts


def _pixel(value, status, size):
    if status != "observed":
        if value is not None:
            raise ValueError("point_px: contato ocluído/desconhecido exige null")
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("point_px: contato observado exige par de coordenadas")
    point = tuple(finite_number(v, "point_px") for v in value)
    if not all(0 <= v < limit for v, limit in zip(point, size, strict=True)):
        raise ValueError("point_px: fora do domínio da imagem")
    return point


def _observation(value, camera: CameraRecording, frames, size):
    data = _object(
        value,
        "observation",
        {"frame_index", "local_track_id", "status", "point_px", "provenance", "method"},
    )
    index = _integer(data["frame_index"], "frame_index")
    frame = frames.get(index)
    if frame is None:
        raise ValueError("frame_index: quadro inexistente")
    if frame.status != "observed":
        raise ValueError("frame_index: esperado quadro observado")
    track_id = _integer(data["local_track_id"], "local_track_id")
    if not any(d.track_id == track_id for d in frame.detections):
        raise ValueError("local_track_id: detecção inexistente no quadro")
    status = _text(data["status"], "status")
    if status not in {"observed", "occluded", "unknown"}:
        raise ValueError("status: esperado observed, occluded ou unknown")
    provenance = _text(data["provenance"], "provenance")
    if provenance not in {"human_review", "synthetic", "model_prediction"}:
        raise ValueError("provenance: esperado human_review, synthetic ou model_prediction")
    manifest = camera.recording.manifest
    if provenance == "synthetic" and (
        manifest["source"]["kind"] != "synthetic"
        or manifest["predictions"]["provenance"] != "synthetic"
    ):
        raise ValueError("provenance synthetic exige fonte/cache sintéticos")
    return ContactObservation(
        index,
        track_id,
        status,
        _pixel(data["point_px"], status, size),
        provenance,
        _text(data["method"], "method"),
    )


def _camera(value, known):
    data = _object(
        value,
        "camera",
        {"camera_id", "segment_id", "sequence_sha256", "image_size_px", "observations"},
    )
    camera_id = _text(data["camera_id"], "camera_id")
    camera = known.get(camera_id)
    if camera is None:
        raise ValueError("camera_id: câmera inexistente")
    if data["segment_id"] != camera.segment_id:
        raise ValueError("segment_id: segmento incompatível")
    digest = _hash(data["sequence_sha256"], camera.recording.manifest_sha256, "sequence_sha256")
    size = _size(data["image_size_px"])
    frames = {frame.frame_index: frame for frame in camera.recording.frames}
    observations = tuple(
        _observation(v, camera, frames, size) for v in _list(data["observations"], "observations")
    )
    keys = [(o.frame_index, o.local_track_id) for o in observations]
    if len(set(keys)) != len(keys):
        raise ValueError("observation: entrada duplicada")
    return CameraContacts(
        camera_id,
        camera.segment_id,
        digest,
        size,
        tuple(sorted(observations, key=lambda o: (o.frame_index, o.local_track_id))),
    )


def load_ground_contacts(path: Path | str, registry: CameraRegistry) -> GroundContacts:
    """Valida hashes e referências sem promover declarações a posições aceitas."""
    raw = Path(path).read_bytes()
    data = _object(
        json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant),
        "ground_contacts",
        {"schema_version", "registry_sha256", "cameras"},
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("ground_contacts.schema_version: esperado inteiro 1")
    digest = _hash(data["registry_sha256"], registry.manifest_sha256, "registry_sha256")
    known = {camera.camera_id: camera for camera in registry.cameras}
    cameras = tuple(_camera(value, known) for value in _list(data["cameras"], "cameras"))
    if len({camera.camera_id for camera in cameras}) != len(cameras):
        raise ValueError("camera: entrada duplicada")
    return GroundContacts(
        digest, hashlib.sha256(raw).hexdigest(), tuple(sorted(cameras, key=lambda c: c.camera_id))
    )
