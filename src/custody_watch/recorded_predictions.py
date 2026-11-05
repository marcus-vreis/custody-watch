"""Contrato versionado de predições rastreadas para replay, sem detector.

Valida todos os arquivos e quadros antes de entregar observações à lógica.
O manifesto identifica o vídeo; ele pode estar offline, mas cache, anotações,
configuração e calibração precisam estar disponíveis e bater com seus hashes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .annotations import GroundTruthEvent, load_annotations
from .calibration import load_calibration
from .config import Config, load_config
from .events import EventKind
from .ground_plane import GroundPlane
from .reid import Appearance
from .tracking import TrackedDetection
from .types import BAG_CLASSES, PERSON_CLASS

SCHEMA_VERSION = 1
EVALUATED_KINDS = frozenset(
    {
        EventKind.BAG_UNATTENDED,
        EventKind.BAG_REMOVED_BY_OWNER,
        EventKind.BAG_REMOVED_BY_STRANGER,
    }
)


def _object(value: Any, label: str, required: set[str], optional=()) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: esperado objeto JSON")
    missing = required - value.keys()
    unknown = value.keys() - required - set(optional)
    if missing or unknown:
        raise ValueError(
            f"{label}: campos ausentes {sorted(missing)}; desconhecidos {sorted(unknown)}"
        )
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: esperado texto não vazio")
    return value


def _number(value: Any, label: str, minimum=0.0, positive=False) -> float:
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value < minimum
        or (positive and value == minimum)
    ):
        raise ValueError(f"{label}: esperado número finito {'>' if positive else '>='} {minimum}")
    return float(value)


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}: esperado inteiro não negativo")
    return value


def _version(value: Any, label: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError(f"{label}.schema_version: versão não suportada {value!r}")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON: chave duplicada {key!r}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"JSON: número não finito {value}")


def _json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _reference(base: Path, value: Any, label: str, offline=False, extra=()) -> Path | None:
    ref = _object(value, label, {"path", "sha256", "size_bytes"} | set(extra))
    if not isinstance(ref["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", ref["sha256"]):
        raise ValueError(f"{label}: hash sha256 inválido")
    size = _integer(ref["size_bytes"], f"{label}.size_bytes")
    if offline and ref["path"] is None:
        return None
    relative = _text(ref["path"], f"{label}.path")
    if Path(relative).is_absolute() or PureWindowsPath(relative).drive or relative.startswith("\\"):
        raise ValueError(f"{label}: use path relativo ao manifesto")
    path = (base / relative).resolve()
    if not path.is_file():
        raise ValueError(f"{label}: arquivo não encontrado")
    if path.stat().st_size != size:
        raise ValueError(f"{label}: tamanho diverge do manifesto")
    if file_digest(path) != ref["sha256"]:
        raise ValueError(f"{label}: hash diverge do manifesto")
    return path


@dataclass(frozen=True)
class RecordedFrame:
    frame_index: int
    t: float
    status: str
    detections: tuple[TrackedDetection, ...]


@dataclass(frozen=True)
class RecordedSequence:
    manifest: dict
    manifest_sha256: str
    source_verified: bool
    frames: tuple[RecordedFrame, ...]
    truth: tuple[GroundTruthEvent, ...]
    config: Config
    plane: GroundPlane

    @property
    def frame_counts(self) -> dict[str, int]:
        observed = [frame for frame in self.frames if frame.status == "observed"]
        expected = self.manifest["clock"]["frame_count"]
        return {
            "expected": expected,
            "observed": len(observed),
            "empty": sum(not frame.detections for frame in observed),
            "missing": expected - len(observed),
        }

    def observations(self) -> Iterator[tuple[float, list[TrackedDetection]]]:
        # Frame ausente não é uma nova observação vazia, nem caixa repetida.
        for frame in self.frames:
            if frame.status == "observed":
                yield frame.t, list(frame.detections)


def _detection(value: Any, classes: list[str]) -> TrackedDetection:
    det = _object(value, "detecção", {"track_id", "cls", "bbox"}, {"touches_bottom", "appearance"})
    track_id = _integer(det["track_id"], "track_id")
    if det["cls"] not in classes:
        raise ValueError(f"classe incompatível com o manifesto: {det['cls']!r}")
    if not isinstance(det["bbox"], list) or len(det["bbox"]) != 4:
        raise ValueError("bbox: esperadas quatro coordenadas xyxy")
    bbox = tuple(_number(v, "bbox", minimum=-math.inf) for v in det["bbox"])
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError("bbox: largura e altura precisam ser positivas")
    bottom = det.get("touches_bottom", False)
    if type(bottom) is not bool:
        raise ValueError("touches_bottom: esperado booleano")
    appearance = None
    if det.get("appearance") is not None:
        signature = det["appearance"]
        if not isinstance(signature, list):
            raise ValueError("appearance: esperada lista de valores")
        appearance = Appearance(tuple(_number(v, "appearance") for v in signature))
    return TrackedDetection(track_id, det["cls"], bbox, appearance, bottom)


def _frames(path: Path, clock: dict, classes: list[str]) -> tuple[RecordedFrame, ...]:
    result = []
    previous_index, previous_t = -1, -math.inf
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = _object(
                    _json(line),
                    "quadro",
                    {
                        "schema_version",
                        "frame_index",
                        "t",
                        "status",
                        "detections",
                    },
                )
                _version(row["schema_version"], "quadro")
                index = _integer(row["frame_index"], "frame_index")
                if index <= previous_index or index >= clock["frame_count"]:
                    raise ValueError("frame_index: repetido, fora de ordem ou fora da sequência")
                t = _number(row["t"], "timestamp")
                if t <= previous_t or not clock["start_s"] <= t <= clock["end_s"]:
                    raise ValueError("timestamp: fora de ordem ou fora do intervalo da mídia")
                if row["status"] not in ("observed", "missing"):
                    raise ValueError("status: esperado observed ou missing")
                if not isinstance(row["detections"], list):
                    raise ValueError("detections: esperada lista")
                if row["status"] == "missing" and row["detections"]:
                    raise ValueError("missing: frame ausente não pode carregar detecções")
                detections = tuple(_detection(det, classes) for det in row["detections"])
                if len({det.track_id for det in detections}) != len(detections):
                    raise ValueError("track_id: duplicado no mesmo quadro")
                result.append(RecordedFrame(index, t, row["status"], detections))
                previous_index, previous_t = index, t
            except (ValueError, TypeError) as error:
                raise ValueError(f"predictions linha {line_number}: {error}") from error
    return tuple(result)


def _truth(path: Path, sequence: str, clock: dict) -> tuple[GroundTruthEvent, ...]:
    data = _object(_json(path.read_text(encoding="utf-8")), "annotations", {"session", "events"})
    if data["session"] != sequence:
        raise ValueError("annotations.session diverge de sequence_id")
    if not isinstance(data["events"], list):
        raise ValueError("annotations.events: esperada lista")
    for value in data["events"]:
        event = _object(
            value, "anotação", {"kind", "t"}, {"t_end", "truncated", "bag", "subject", "note"}
        )
        EventKind(event["kind"])
        t = _number(event["t"], "anotação.t")
        end = event.get("t_end")
        if not clock["start_s"] <= t <= clock["end_s"]:
            raise ValueError("anotação.t fora do intervalo da mídia")
        if end is not None and not t <= _number(end, "anotação.t_end") <= clock["end_s"]:
            raise ValueError("anotação.t_end fora do intervalo")
        if type(event.get("truncated", False)) is not bool:
            raise ValueError("anotação.truncated: esperado booleano")
        for name in ("bag", "subject"):
            if event.get(name) is not None:
                _integer(event[name], f"anotação.{name}")
    return tuple(load_annotations(path))


def _calibrated_plane(path: Path) -> GroundPlane:
    data = _object(
        _json(path.read_text(encoding="utf-8")),
        "calibration",
        {"camera", "note", "correspondences"},
    )
    if not isinstance(data["correspondences"], list):
        raise ValueError("calibration.correspondences: esperada lista")
    for sample in data["correspondences"]:
        _object(sample, "calibration.correspondence", {"pixel", "world"})
        for name in ("pixel", "world"):
            coordinates = sample[name]
            if not isinstance(coordinates, list) or len(coordinates) != 2:
                raise ValueError(f"calibration.{name}: esperado par de coordenadas")
            for value in coordinates:
                _number(value, f"calibration.{name}", minimum=-math.inf)
    plane = load_calibration(path).plane
    for sample in data["correspondences"]:
        point = plane.project(*sample["pixel"])
        _number(point.x, "calibration.projected_x", minimum=-math.inf)
        _number(point.y, "calibration.projected_y", minimum=-math.inf)
    return plane


def load_recording(path: Path | str) -> RecordedSequence:
    path = Path(path).resolve()
    data = _object(
        _json(path.read_text(encoding="utf-8")),
        "manifest",
        {
            "schema_version",
            "sequence_id",
            "mode",
            "source",
            "predictions",
            "annotations",
            "config",
            "geometry",
            "clock",
            "classes",
            "producer",
            "evaluation",
        },
    )
    _version(data["schema_version"], "manifest")
    _text(data["sequence_id"], "sequence_id")
    if data["mode"] not in ("predictions", "oracle"):
        raise ValueError("mode: esperado predictions ou oracle")
    source = _object(data["source"], "source", {"kind", "origin", "redistribution", "file"})
    if source["kind"] not in ("video", "synthetic"):
        raise ValueError("source.kind: esperado video ou synthetic")
    for field in ("origin", "redistribution"):
        _text(source[field], f"source.{field}")
    source_path = _reference(path.parent, source["file"], "source.file", offline=True)
    cache = _reference(path.parent, data["predictions"], "predictions", extra={"provenance"})
    annotation_path = _reference(path.parent, data["annotations"], "annotations")
    config_path = _reference(path.parent, data["config"], "config")
    provenance = data["predictions"]["provenance"]
    expected = (
        "ground_truth"
        if data["mode"] == "oracle"
        else ("synthetic" if source["kind"] == "synthetic" else "detector")
    )
    if provenance != expected:
        raise ValueError("predictions.provenance incompatível: ground_truth exige mode oracle")
    classes = data["classes"]
    if (
        not isinstance(classes, list)
        or not classes
        or any(
            not isinstance(cls, str) or cls not in BAG_CLASSES | {PERSON_CLASS} for cls in classes
        )
        or len(set(classes)) != len(classes)
    ):
        raise ValueError("classes: lista de classes compatíveis, sem duplicatas")
    clock = _object(data["clock"], "clock", {"fps", "start_s", "end_s", "frame_count"})
    _number(clock["fps"], "clock.fps", positive=True)
    _number(clock["start_s"], "clock.start_s")
    _number(clock["end_s"], "clock.end_s", minimum=clock["start_s"], positive=True)
    if _integer(clock["frame_count"], "clock.frame_count") == 0:
        raise ValueError("clock.frame_count: precisa ser positivo")
    producer = _object(
        data["producer"],
        "producer",
        {
            "code_revision",
            "detector",
            "tracker",
            "weights_sha256",
            "environment",
            "command",
            "seeds",
        },
    )
    for field in ("code_revision", "detector", "tracker"):
        _text(producer[field], f"producer.{field}")
    weights = producer["weights_sha256"]
    if weights is not None and (
        not isinstance(weights, str) or not re.fullmatch("[a-f0-9]{64}", weights)
    ):
        raise ValueError("producer.weights_sha256: hash inválido")
    if not isinstance(producer["environment"], dict) or not producer["environment"]:
        raise ValueError("producer.environment: esperado objeto não vazio")
    for key, value in producer["environment"].items():
        _text(value, f"environment.{key}")
    if not isinstance(producer["command"], list) or not producer["command"]:
        raise ValueError("producer.command: esperada lista não vazia")
    for part in producer["command"]:
        _text(part, "producer.command")
    if not isinstance(producer["seeds"], dict):
        raise ValueError("producer.seeds: esperado objeto")
    for name, seed in producer["seeds"].items():
        _integer(seed, f"seed.{name}")
    evaluation = _object(
        data["evaluation"],
        "evaluation",
        {
            "kinds",
            "lag_window_s",
            "slack_before_s",
            "target_rfa",
        },
    )
    kinds = evaluation["kinds"]
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(
            not isinstance(kind, str) or kind not in {item.value for item in EVALUATED_KINDS}
            for kind in kinds
        )
        or len(set(kinds)) != len(kinds)
    ):
        raise ValueError("evaluation.kinds: tipos de custódia válidos, sem duplicatas")
    for name in ("lag_window_s", "slack_before_s", "target_rfa"):
        _number(evaluation[name], f"evaluation.{name}")
    geometry = data["geometry"]
    if not isinstance(geometry, dict):
        raise ValueError("geometry: esperado objeto")
    if geometry.get("kind") == "uniform_scale":
        _object(geometry, "geometry", {"kind", "metres_per_pixel", "limitation"})
        _text(geometry["limitation"], "geometry.limitation")
        plane = GroundPlane.uniform(_number(geometry["metres_per_pixel"], "escala", positive=True))
    elif geometry.get("kind") == "calibration":
        _object(geometry, "geometry", {"kind", "file"})
        calibration_path = _reference(path.parent, geometry["file"], "calibration")
        plane = _calibrated_plane(calibration_path)
    else:
        raise ValueError("geometry.kind: esperado uniform_scale ou calibration")
    # Os leitores legados são chamados só após rejeitar NaN e chaves duplicadas.
    _json(config_path.read_text(encoding="utf-8"))
    config = load_config(config_path)
    truth = _truth(annotation_path, data["sequence_id"], clock)
    frames = _frames(cache, clock, classes)
    return RecordedSequence(
        data, file_digest(path), source_path is not None, frames, truth, config, plane
    )
