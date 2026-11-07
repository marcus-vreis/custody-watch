"""Importa recortes anotados de NVIDIA/WILDTRACK como oracle, sem detector."""

from __future__ import annotations

import json
import platform
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .calibration import load_calibration
from .camera_registry import finite_number, load_camera_registry
from .ground_plane import GroundPlane
from .multicamera_media import FrameImages
from .multicamera_view import ReplayView
from .recorded_predictions import file_digest
from .types import Point


@dataclass(frozen=True)
class SampleCamera:
    camera_id: str
    plane: GroundPlane
    position_m: Point
    source_fps: float
    width: int
    height: int


@dataclass(frozen=True)
class AnnotatedPerson:
    track_id: int
    bbox: tuple[float, float, float, float]
    world: Point


def _index(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}: esperado inteiro não negativo")
    return value


def _matrix(value):
    values = np.asarray(value, dtype=object)
    if values.shape != (3, 3):
        raise ValueError("homography: esperada matriz 3x3")
    matrix = np.array([[finite_number(v, "homography") for v in row] for row in values])
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as error:
        raise ValueError("homography: matriz singular") from error
    if not np.isfinite(inverse).all():
        raise ValueError("homography: inversa não finita")
    return inverse


def _field(row, name):
    alternate = name.replace("_", " ")
    if name in row and alternate in row and name != alternate:
        raise ValueError(f"campo ambíguo: {name}")
    return row[name] if name in row else row[alternate]


def read_nvidia_prefix(path: Path, frame_ids: list[int]) -> dict:
    """Extrai somente frames completos de um prefixo; não verifica o JSON integral."""
    text = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    position = len(text) - len(text.lstrip())
    if position >= len(text) or text[position] != "{":
        raise ValueError("prefixo GT: esperado objeto JSON")
    position += 1
    wanted = {str(i) for i in frame_ids}
    found, seen = {}, set()
    try:
        while position < len(text):
            while position < len(text) and text[position] in " \r\n\t,":
                position += 1
            if position == len(text) or text[position] == "}":
                break
            key, position = decoder.raw_decode(text, position)
            if key in seen:
                raise ValueError("frame duplicado no prefixo GT")
            seen.add(key)
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text) or text[position] != ":":
                break
            position += 1
            while position < len(text) and text[position].isspace():
                position += 1
            value, position = decoder.raw_decode(text, position)
            if key in wanted:
                if not isinstance(value, list):
                    raise ValueError("frame GT: esperada lista")
                found[key] = value
            if found.keys() == wanted:
                return found
    except json.JSONDecodeError:
        pass
    raise ValueError("prefixo GT incompleto para os frames solicitados")


def _bbox(values):
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError("bbox: esperadas quatro coordenadas")
    box = tuple(finite_number(v, "bbox") for v in values)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("bbox: área não positiva")
    return box


def nvidia_camera(sensor: dict) -> SampleCamera:
    camera_id = sensor["id"]
    if not isinstance(camera_id, str) or not re.fullmatch(r"Camera_\d{4}", camera_id):
        raise ValueError("id: esperado Camera_NNNN")
    attrs = {item["name"]: item["value"] for item in sensor["attributes"]}
    fps = finite_number(float(attrs["fps"]), "fps da fonte")
    width, height = int(attrs["frameWidth"]), int(attrs["frameHeight"])
    if fps <= 0 or min(width, height) <= 0:
        raise ValueError("dimensões/fps da fonte precisam ser positivos")
    return SampleCamera(
        camera_id,
        GroundPlane(_matrix(sensor["homography"])),
        Point(*(finite_number(sensor["coordinates"][axis], "coordinates") for axis in ("x", "y"))),
        fps,
        width,
        height,
    )


def nvidia_observations(rows: list, camera_id: str) -> list[AnnotatedPerson]:
    people = []
    for row in rows:
        kind = _field(row, "object_type")
        if not isinstance(kind, str):
            raise ValueError("object type: esperado texto")
        if kind.lower() != "person":
            continue
        views = _field(row, "2d_bounding_box_visible")
        if camera_id not in views:
            continue
        location = _field(row, "3d_location")
        if not isinstance(location, list) or len(location) != 3:
            raise ValueError("3d location: esperado vetor de três valores")
        people.append(
            AnnotatedPerson(
                _index(_field(row, "object_id"), "object id"),
                _bbox(views[camera_id]),
                Point(*(finite_number(v, "3d location") for v in location[:2])),
            )
        )
    if len({person.track_id for person in people}) != len(people):
        raise ValueError("object id duplicado")
    return people


def wildtrack_position(position_id: int) -> Point:
    position_id = _index(position_id, "positionID")
    if position_id >= 480 * 1440:
        raise ValueError("positionID fora da grade WILDTRACK")
    return Point(
        (-300 + 2.5 * (position_id % 480)) / 100, (-900 + 2.5 * (position_id // 480)) / 100
    )


def wildtrack_camera(root: Path, camera_id: str) -> SampleCamera:
    if camera_id not in [f"C{i}" for i in range(1, 8)]:
        raise ValueError("camera_id: esperado C1 a C7")
    index = int(camera_id[1:]) - 1
    name = f"CVLab{index + 1}" if index < 4 else f"IDIAP{index - 3}"
    storage = cv2.FileStorage(
        str(root / "calibrations" / "intrinsic_zero" / f"intr_{name}.xml"), cv2.FILE_STORAGE_READ
    )
    try:
        intrinsic = storage.getNode("camera_matrix").mat()
    finally:
        storage.release()
    xml = ET.parse(root / "calibrations" / "extrinsic" / f"extr_{name}.xml").getroot()
    rotation, _ = cv2.Rodrigues(np.array([float(v) for v in xml.findtext("rvec").split()]))
    translation = np.array([float(v) for v in xml.findtext("tvec").split()])
    if intrinsic is None or translation.shape != (3,) or not np.isfinite(translation).all():
        raise ValueError("calibração WILDTRACK inválida")
    # K[R1 R2 t] projeta chão em cm para pixels. A inversa precisa virar metros.
    world_to_pixel = intrinsic @ np.column_stack((rotation[:, :2], translation))
    inverse = np.diag([0.01, 0.01, 1.0]) @ _matrix(world_to_pixel.tolist())
    center = -rotation.T @ translation / 100
    return SampleCamera(
        camera_id, GroundPlane(inverse), Point(float(center[0]), float(center[1])), 10, 1920, 1080
    )


def wildtrack_observations(rows: list, view_index: int) -> list[AnnotatedPerson]:
    people = []
    for row in rows:
        views = [view for view in row["views"] if view["viewNum"] == view_index]
        if len(views) != 1:
            raise ValueError("viewNum ausente ou duplicado")
        view = views[0]
        box = [view[key] for key in ("xmin", "ymin", "xmax", "ymax")]
        if box == [-1, -1, -1, -1]:
            continue
        people.append(
            AnnotatedPerson(
                _index(row["personID"], "personID"),
                _bbox(box),
                wildtrack_position(row["positionID"]),
            )
        )
    if len({person.track_id for person in people}) != len(people):
        raise ValueError("personID duplicado")
    return people


def _write(path: Path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _reference(path: Path):
    return {"path": path.name, "size_bytes": path.stat().st_size, "sha256": file_digest(path)}


def _calibration(camera: SampleCamera) -> dict:
    points = [
        (camera.width * x, camera.height * y)
        for x, y in [(0.25, 0.55), (0.75, 0.55), (0.25, 0.95), (0.75, 0.95)]
    ]
    return {
        "camera": camera.camera_id,
        "note": (
            "Correspondências algébricas derivadas da calibração publicada. "
            "Resíduo verifica conversão numérica; não mede precisão física."
        ),
        "correspondences": [
            {"pixel": list(pixel), "world": [point.x, point.y]}
            for pixel in points
            for point in [camera.plane.project(*pixel)]
        ],
    }


def _stats(values):
    return {
        "count": len(values),
        "median_m": float(np.median(values)) if values else None,
        "p95_m": float(np.quantile(values, 0.95)) if values else None,
        "max_m": max(values, default=None),
    }


def build_sample(
    dataset: str,
    source: Path,
    output: Path,
    frame_ids: list[int],
    camera_ids: list[str],
    *,
    code_revision: str,
    source_revision: str | None = None,
) -> dict:
    if output.exists():
        raise ValueError("output já existe; escolha outro diretório")
    if dataset not in ("nvidia", "wildtrack"):
        raise ValueError("dataset não suportado")
    if len(frame_ids) < 2 or any(type(i) is not int or i < 0 for i in frame_ids):
        raise ValueError("frames: esperados ao menos dois índices não negativos")
    stride = frame_ids[1] - frame_ids[0]
    if stride <= 0 or any(
        b - a != stride for a, b in zip(frame_ids[:-1], frame_ids[1:], strict=True)
    ):
        raise ValueError("frames: a amostragem precisa ser crescente e uniforme")
    if len(camera_ids) < 2 or len(set(camera_ids)) != len(camera_ids):
        raise ValueError("cameras: esperadas ao menos duas câmeras distintas")
    inputs = set()
    if dataset == "nvidia":
        calibration_path = source / "calibration.json"
        sensors = json.loads(calibration_path.read_text(encoding="utf-8"))["sensors"]
        by_id = {item["id"]: item for item in sensors}
        cameras = [nvidia_camera(by_id[c]) for c in camera_ids]
        truth_path = source / "ground_truth.prefix.json.part"
        if truth_path.is_file():
            truth = read_nvidia_prefix(truth_path, frame_ids)
        else:
            truth_path = source / "ground_truth.json"
            truth = json.loads(truth_path.read_text(encoding="utf-8"))
        observations = {
            c.camera_id: [nvidia_observations(truth[str(i)], c.camera_id) for i in frame_ids]
            for c in cameras
        }
        inputs.update([calibration_path, truth_path])
    else:
        cameras = [wildtrack_camera(source, c) for c in camera_ids]
        truth_files = [source / "annotations_positions" / f"{i:08d}.json" for i in frame_ids]
        truth = [json.loads(path.read_text(encoding="utf-8")) for path in truth_files]
        observations = {
            c.camera_id: [wildtrack_observations(rows, int(c.camera_id[1:]) - 1) for rows in truth]
            for c in cameras
        }
        inputs.update(truth_files)
        inputs.update(path for path in (source / "calibrations").rglob("*.xml"))
    if len({camera.source_fps for camera in cameras}) != 1:
        raise ValueError("fps diferentes exigem outro protocolo de sincronização")
    output.mkdir(parents=True)
    registry_cameras, reports, error_rows = [], [], []
    for camera in cameras:
        folder = output / camera.camera_id
        folder.mkdir()
        fps = camera.source_fps / stride
        original_video = source / f"{camera.camera_id}.mp4"
        capture = cv2.VideoCapture(str(original_video)) if dataset == "nvidia" else None
        if capture is not None:
            inputs.add(original_video)
            if (
                not capture.isOpened()
                or abs(capture.get(cv2.CAP_PROP_FPS) - camera.source_fps) > 0.001
            ):
                capture.release()
                raise ValueError("vídeo ausente ou fps diverge da calibração")
        video = folder / "sample.avi"
        writer = cv2.VideoWriter(
            str(video), cv2.VideoWriter_fourcc(*"MJPG"), fps, (camera.width, camera.height)
        )
        try:
            if not writer.isOpened():
                raise ValueError("codec MJPG indisponível")
            for original in frame_ids:
                if capture is None:
                    path = source / "Image_subsets" / camera.camera_id / f"{original:08d}.png"
                    inputs.add(path)
                    image = cv2.imread(str(path))
                else:
                    if not capture.set(cv2.CAP_PROP_POS_FRAMES, original):
                        raise ValueError("decoder não aceitou busca por índice")
                    ok, image = capture.read()
                    position = finite_number(
                        capture.get(cv2.CAP_PROP_POS_FRAMES), "índice do decoder"
                    )
                    if not ok or abs(position - original - 1) > 0.5:
                        raise ValueError("decoder não confirmou o quadro da anotação")
                if image is None or image.shape[:2] != (camera.height, camera.width):
                    raise ValueError("imagem ausente ou dimensões divergentes")
                writer.write(image)
        finally:
            writer.release()
            if capture is not None:
                capture.release()
        rows, all_errors, unclipped_errors = [], [], []
        _write(folder / "calibration.json", _calibration(camera))
        imported_plane = load_calibration(folder / "calibration.json").plane
        for index, people in enumerate(observations[camera.camera_id]):
            detections = []
            for person in people:
                point = imported_plane.foot_point(person.bbox)
                error = finite_number(point.distance_to(person.world), "desvio de projeção")
                all_errors.append(error)
                x1, y1, x2, y2 = person.bbox
                clipped = x1 <= 0 or y1 <= 0 or x2 >= camera.width - 1 or y2 >= camera.height - 1
                if not clipped:
                    unclipped_errors.append(error)
                error_rows.append(
                    {
                        "camera_id": camera.camera_id,
                        "source_frame": frame_ids[index],
                        "annotation_id": person.track_id,
                        "error_m": error,
                        "bbox_at_boundary": clipped,
                    }
                )
                detections.append(
                    {
                        "track_id": person.track_id,
                        "cls": "person",
                        "bbox": list(person.bbox),
                        "touches_bottom": y2 >= camera.height - 1,
                    }
                )
            rows.append(
                {
                    "schema_version": 1,
                    "frame_index": index,
                    "t": index / fps,
                    "status": "observed",
                    "detections": detections,
                }
            )
        cache = folder / "predictions.jsonl"
        cache.write_text(
            "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows),
            encoding="utf-8",
            newline="\n",
        )
        _write(folder / "config.json", {})
        sequence_id = f"{dataset}-{camera.camera_id}"
        _write(folder / "annotations.json", {"session": sequence_id, "events": []})
        manifest = {
            "schema_version": 1,
            "sequence_id": sequence_id,
            "mode": "oracle",
            "source": {
                "kind": "video",
                "origin": (
                    f"{dataset}; câmera {camera.camera_id}; "
                    f"quadros originais {frame_ids}; derivado MJPG"
                ),
                "redistribution": "CC BY 4.0"
                if dataset == "nvidia"
                else (
                    "Não redistribuído; condições de reutilização a confirmar na fonte WILDTRACK"
                ),
                "file": _reference(video),
            },
            "predictions": {**_reference(cache), "provenance": "ground_truth"},
            "config": _reference(folder / "config.json"),
            "annotations": _reference(folder / "annotations.json"),
            "geometry": {"kind": "calibration", "file": _reference(folder / "calibration.json")},
            "clock": {
                "fps": fps,
                "start_s": 0.0,
                "end_s": (len(frame_ids) - 1) / fps,
                "frame_count": len(frame_ids),
            },
            "classes": ["person"],
            "producer": {
                "code_revision": code_revision,
                "detector": "oracle: anotações publicadas; sem detector",
                "tracker": "IDs anotados; sem associação calculada",
                "weights_sha256": None,
                "environment": {
                    "importer_sha256": file_digest(Path(__file__)),
                    "python": platform.python_version(),
                    "opencv": cv2.__version__,
                    "numpy": np.__version__,
                },
                "command": [
                    "import_multicamera_sample",
                    dataset,
                    "--frames",
                    ",".join(map(str, frame_ids)),
                ],
                "seeds": {},
            },
            "evaluation": {
                "kinds": ["bag_unattended"],
                "lag_window_s": 0,
                "slack_before_s": 0,
                "target_rfa": 0,
            },
        }
        manifest_path = folder / "manifest.json"
        _write(manifest_path, manifest)
        registry_cameras.append(
            {
                "camera_id": camera.camera_id,
                "floor_id": "ground",
                "segment_id": "sample-1",
                "position_m": [camera.position_m.x, camera.position_m.y],
                "coverage_m": None,
                "projection_frame_id": f"{dataset}-ground-m",
                "recording": {
                    **_reference(manifest_path),
                    "path": f"{camera.camera_id}/manifest.json",
                },
                "clock": {
                    "offset_s": frame_ids[0] / camera.source_fps,
                    "uncertainty_s": None,
                    "method": "Sincronização declarada pelo dataset; limite de erro não fornecido.",
                },
            }
        )
        reports.append(
            {
                "camera_id": camera.camera_id,
                "observations": len(all_errors),
                "bbox_foot_to_world_annotation": _stats(all_errors),
                "bbox_foot_to_world_annotation_unclipped": _stats(unclipped_errors),
            }
        )
    _write(
        output / "registry.json",
        {
            "schema_version": 1,
            "site_id": f"{dataset}-public-sample",
            "floors": [
                {
                    "floor_id": "ground",
                    "coordinate_frame": {
                        "frame_id": f"{dataset}-ground-m",
                        "unit": "m",
                        "origin": "Origem da calibração publicada",
                        "x_axis": "X do dataset",
                        "y_axis": "Y do dataset",
                    },
                }
            ],
            "cameras": registry_cameras,
        },
    )
    registry = load_camera_registry(output / "registry.json")
    images = FrameImages(registry)
    view = ReplayView(registry)
    for camera in registry.cameras:
        for index in (0, len(frame_ids) - 1):
            images.get(camera.camera_id, "sample-1", index)
    end_t = (len(frame_ids) - 1) / (cameras[0].source_fps / stride) + frame_ids[0] / cameras[
        0
    ].source_fps
    view.snapshot(end_t)
    _write(output / "projection-errors.json", error_rows)
    report = {
        "dataset": dataset,
        "source_revision": source_revision,
        "importer_sha256": file_digest(Path(__file__)),
        "code_revision": code_revision,
        "runs": 1,
        "selection": "Quadros e câmeras explícitos; recorte de conveniência, não aleatório.",
        "mode": "oracle",
        "integration_status": "passed",
        "frames_per_camera": len(frame_ids),
        "source_frame_ids": frame_ids,
        "source_fps": cameras[0].source_fps,
        "sample_fps": cameras[0].source_fps / stride,
        "cameras": reports,
        "detector_validated": False,
        "identity_association_validated": False,
        "custody_validated": False,
        "physical_calibration_accuracy_validated": False,
        "limitations": [
            "Somente pessoas anotadas; nenhuma anotação de posse/bagagem importada.",
            "Desvio entre base da bbox e posição anotada mistura convenções/oclusão "
            "e calibração; não é erro puro da calibração.",
            "Correspondências da calibração foram geradas algebricamente, não medidas no ambiente.",
            "Recorte pequeno em oracle; não mede detecção, tracking aprendido "
            "ou redução de furtos.",
        ],
        "inputs": [
            {
                "path": str(path.relative_to(source)),
                "size_bytes": path.stat().st_size,
                "sha256": file_digest(path),
            }
            for path in sorted(inputs)
        ],
    }
    _write(output / "validation.json", report)
    return report
