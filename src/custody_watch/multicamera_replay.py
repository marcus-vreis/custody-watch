"""Ordena quadros gravados pelo relógio comum, sem associar identidades."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from heapq import merge

from .camera_registry import CameraRecording, CameraRegistry, finite_number
from .recorded_predictions import RecordedFrame


@dataclass(frozen=True)
class SynchronizedFrame:
    camera: CameraRecording
    frame: RecordedFrame
    t: float
    time_interval_s: tuple[float, float] | None


def _frames(camera: CameraRecording) -> Iterator[SynchronizedFrame]:
    for frame in camera.recording.frames:
        yield SynchronizedFrame(
            camera, frame, camera.clock.global_time(frame.t), camera.clock.interval(frame.t)
        )


def iter_synchronized(
    registry: CameraRegistry, *, start_s: float | None = None, end_s: float | None = None
) -> Iterator[SynchronizedFrame]:
    """Janela inclusiva no tempo nominal; não infere quadros ou simultaneidade.

    Empates usam camera_id e frame_index. A incerteza é metadado declarado e não
    desloca timestamps, preenche lacunas ou comprova associação entre vistas.
    """
    start = None if start_s is None else finite_number(start_s, "start_s")
    end = None if end_s is None else finite_number(end_s, "end_s")
    if start is not None and end is not None and end < start:
        raise ValueError("end_s: precisa ser maior ou igual a start_s")
    frames = merge(
        *(_frames(camera) for camera in registry.cameras),
        key=lambda item: (item.t, item.camera.camera_id, item.frame.frame_index),
    )
    for item in frames:
        if end is not None and item.t > end:
            break
        if start is None or item.t >= start:
            yield item


def frame_to_dict(item: SynchronizedFrame) -> dict:
    """Separa projeção diagnóstica de posição com contato no chão comprovado.

    O cache v1 não declara visibilidade dos pés. Mesmo em oracle, uma bbox
    visível pode terminar em uma oclusão. Nenhuma posição desses caches está
    qualificada para associação entre vistas.
    """
    camera, frame = item.camera, item.frame
    tracks = []
    for detection in frame.detections:
        projection = None
        status = "no_common_frame"
        if camera.projection_frame_id is not None:
            point = camera.recording.plane.foot_point(detection.bbox)
            projection = [finite_number(v, "position_m projetada") for v in (point.x, point.y)]
            status = "image_border" if detection.touches_bottom else "ground_contact_unknown"
        tracks.append(
            {
                "track_key": {
                    "camera_id": camera.camera_id,
                    "segment_id": camera.segment_id,
                    "local_track_id": detection.track_id,
                },
                "cls": detection.cls,
                "bbox": list(detection.bbox),
                "position_m": None,
                "projected_position_m": projection,
                "position_status": status,
                "association_eligible": False,
                "touches_bottom": detection.touches_bottom,
            }
        )
    manifest = camera.recording.manifest
    return {
        "schema_version": 2,
        "camera_id": camera.camera_id,
        "floor_id": camera.floor_id,
        "segment_id": camera.segment_id,
        "projection_frame_id": camera.projection_frame_id,
        "sequence_id": manifest["sequence_id"],
        "mode": manifest["mode"],
        "source_kind": manifest["source"]["kind"],
        "provenance": manifest["predictions"]["provenance"],
        "geometry_kind": manifest["geometry"]["kind"],
        "frame_index": frame.frame_index,
        "media_t": frame.t,
        "t": item.t,
        "time_interval_s": None if item.time_interval_s is None else list(item.time_interval_s),
        "status": frame.status,
        "tracks": tracks,
    }
