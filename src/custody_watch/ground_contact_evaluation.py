"""Mede declarações de contato em replay, sem liberar associação ou custódia."""

from __future__ import annotations

from collections import Counter

import cv2
import numpy as np

from .camera_registry import CameraRegistry, finite_number
from .ground_contacts import GroundContacts
from .multicamera_media import FrameImages
from .multicamera_replay import frame_to_dict, iter_synchronized


def _image_bounds(registry, contacts):
    known = {camera.camera_id: camera for camera in registry.cameras}
    needs_video = any(
        known[c.camera_id].recording.manifest["source"]["kind"] == "video"
        and known[c.camera_id].recording.source_verified
        and known[c.camera_id].recording_path is not None
        for c in contacts.cameras
    )
    images = FrameImages(registry) if needs_video else None
    result = {}
    for declaration in contacts.cameras:
        camera = known[declaration.camera_id]
        if camera.recording.manifest["source"]["kind"] == "synthetic":
            result[camera.camera_id] = "synthetic_declared"
            continue
        if not camera.recording.source_verified or camera.recording_path is None:
            result[camera.camera_id] = "unavailable"
            continue
        frame = next((f for f in camera.recording.frames if f.status == "observed"), None)
        if frame is None:
            result[camera.camera_id] = "no_observed_frame"
            continue
        encoded = images.get(
            camera.camera_id, camera.segment_id, frame.frame_index, resolution="source"
        )
        image = cv2.imdecode(np.frombuffer(encoded.data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("image_size_px: não foi possível verificar o quadro")
        size = image.shape[1], image.shape[0]
        if declaration.image_size_px != size:
            raise ValueError("image_size_px: dimensões divergem do vídeo local")
        result[camera.camera_id] = "verified_source"
    return result


def _observation(item, track, evidence, declaration, bounds):
    camera = item.camera
    point = None if evidence is None else evidence.point_px
    status = "unknown" if evidence is None else evidence.status
    contact_projection = None
    refusal = f"contact_{status}"
    if point is not None:
        refusal = "no_common_frame"
        if camera.projection_frame_id is not None:
            projected = camera.recording.plane.project(*point)
            contact_projection = [
                finite_number(v, "contact_projection_m") for v in (projected.x, projected.y)
            ]
            refusal = None
    boundary = None
    if point is not None:
        width, height = declaration.image_size_px
        boundary = point[0] in (0, width - 1) or point[1] in (0, height - 1)
    return {
        "schema_version": 1,
        "camera_id": camera.camera_id,
        "segment_id": camera.segment_id,
        "sequence_sha256": camera.recording.manifest_sha256,
        "frame_index": item.frame.frame_index,
        "local_track_id": track["track_key"]["local_track_id"],
        "cls": track["cls"],
        "mode": camera.recording.manifest["mode"],
        "cache_provenance": camera.recording.manifest["predictions"]["provenance"],
        "media_t": item.frame.t,
        "t": item.t,
        "evidence_present": evidence is not None,
        "contact_status": status,
        "contact_provenance": None if evidence is None else evidence.provenance,
        "contact_method": None if evidence is None else evidence.method,
        "point_px": None if point is None else list(point),
        "contact_projection_m": contact_projection,
        "bbox_projection_m": track["projected_position_m"],
        "projection_frame_id": camera.projection_frame_id,
        "projection_refusal": refusal,
        "image_bounds": bounds,
        "at_image_boundary": boundary,
        "position_m": None,
        "association_eligible": False,
    }


def evaluate_ground_contacts(registry: CameraRegistry, contacts: GroundContacts) -> dict:
    """Um run determinístico do arquivo; não mede detector nem certifica a evidência."""
    if contacts.registry_sha256 != registry.manifest_sha256:
        raise ValueError("registry_sha256: evidência de outro cadastro")
    bounds = _image_bounds(registry, contacts)
    declarations = {c.camera_id: c for c in contacts.cameras}
    evidence = {
        (c.camera_id, c.segment_id, o.frame_index, o.local_track_id): o
        for c in contacts.cameras
        for o in c.observations
    }
    rows = []
    for item in iter_synchronized(registry):
        camera_id, segment_id = item.camera.camera_id, item.camera.segment_id
        for track in frame_to_dict(item)["tracks"]:
            key = (
                camera_id,
                segment_id,
                item.frame.frame_index,
                track["track_key"]["local_track_id"],
            )
            rows.append(
                _observation(
                    item,
                    track,
                    evidence.get(key),
                    declarations.get(camera_id),
                    bounds.get(camera_id, "not_declared"),
                )
            )
    statuses = Counter(row["contact_status"] for row in rows)
    labeled = sum(row["evidence_present"] for row in rows)
    projections = sum(row["contact_projection_m"] is not None for row in rows)
    return {
        "schema_version": 1,
        "evaluation": "ground_contact_declarations",
        "registry_sha256": registry.manifest_sha256,
        "evidence_sha256": contacts.manifest_sha256,
        "runs": 1,
        "seed": None,
        "scope": "Declarações e projeções diagnósticas; sem confiança validada ou associação.",
        "counts": {
            "observations": len(rows),
            "labeled": labeled,
            "unlabeled": len(rows) - labeled,
            "observed_contacts": statuses["observed"],
            "occluded_contacts": statuses["occluded"],
            "unknown_contacts": statuses["unknown"],
            "contact_projections": projections,
            "projection_abstentions": len(rows) - projections,
            "qualified_positions": 0,
            "association_abstentions": len(rows),
        },
        "cameras": [
            {
                "camera_id": c.camera_id,
                "segment_id": c.segment_id,
                "sequence_sha256": c.recording.manifest_sha256,
                "mode": c.recording.manifest["mode"],
                "cache_provenance": c.recording.manifest["predictions"]["provenance"],
                "image_size_px": None
                if c.camera_id not in declarations
                else list(declarations[c.camera_id].image_size_px),
                "image_bounds": bounds.get(c.camera_id, "not_declared"),
            }
            for c in sorted(registry.cameras, key=lambda c: c.camera_id)
        ],
        "observations": rows,
    }
