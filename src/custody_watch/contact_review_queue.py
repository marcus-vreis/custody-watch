"""Fila de conveniência para anotação; não gera evidências ou valida confiança."""

from __future__ import annotations

import hashlib
import json

from .camera_registry import CameraRegistry, finite_number
from .multicamera_view import media_kind
from .types import PERSON_CLASS


def _integer(value, label: str, maximum: int, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}: esperado inteiro entre {minimum} e {maximum}")
    return value


def _select_frames(candidates, registry_hash, camera, seed, quota, gap):
    def rank(frame):
        key = [
            seed,
            registry_hash,
            camera.camera_id,
            camera.segment_id,
            camera.recording.manifest_sha256,
            frame["frame_index"],
        ]
        return hashlib.sha256(
            json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode()
        ).digest()

    selected = []
    for frame in sorted(candidates, key=rank):
        if all(abs(frame["global_t"] - other["global_t"]) >= gap for other in selected):
            selected.append(frame)
            if len(selected) == quota:
                break
    return sorted(selected, key=lambda frame: (frame["global_t"], frame["frame_index"]))


def prepare_contact_review_queue(
    registry: CameraRegistry, *, seed: int, frames_per_camera: int = 6, min_gap_s: float = 1.0
) -> dict:
    """Prioriza por hash e recusa vizinhos próximos; não otimiza cobertura amostral.

    A unidade é o quadro por câmera, com todos os IDs de pessoas presentes.
    Espaçamento e seed não implicam independência entre pessoas, vistas ou quadros.
    Integridade da fonte é a verificada no carregamento do cadastro; não decodifica vídeo.
    """
    seed = _integer(seed, "seed", 2**32 - 1)
    quota = _integer(frames_per_camera, "frames_per_camera", 1000, 1)
    gap = finite_number(min_gap_s, "min_gap_s")
    if gap < 0:
        raise ValueError("min_gap_s: esperado intervalo não negativo")
    cameras, excluded = [], []
    for camera in sorted(registry.cameras, key=lambda camera: camera.camera_id):
        kind = media_kind(camera)
        if kind != "video":
            excluded.append({"camera_id": camera.camera_id, "reason": kind})
            continue
        candidates = []
        for frame in camera.recording.frames:
            ids = sorted(
                detection.track_id
                for detection in frame.detections
                if detection.cls == PERSON_CLASS
            )
            if frame.status == "observed" and ids:
                _integer(frame.frame_index, "frame_index do painel", 2**53 - 1)
                for track_id in ids:
                    _integer(track_id, "local_track_id do painel", 2**53 - 1)
                candidates.append(
                    {
                        "frame_index": frame.frame_index,
                        "media_t": frame.t,
                        "global_t": camera.clock.global_time(frame.t),
                        "local_track_ids": ids,
                    }
                )
        frames = _select_frames(candidates, registry.manifest_sha256, camera, seed, quota, gap)
        cameras.append(
            {
                "camera_id": camera.camera_id,
                "segment_id": camera.segment_id,
                "sequence_sha256": camera.recording.manifest_sha256,
                "prediction_provenance": camera.recording.manifest["predictions"]["provenance"],
                "candidate_frames": len(candidates),
                "selected_frames": len(frames),
                "frames": frames,
            }
        )
    if not any(camera["selected_frames"] for camera in cameras):
        raise ValueError("Nenhum quadro observado com pessoas em vídeo local verificado")
    return {
        "schema_version": 1,
        "kind": "ground_contact_review_queue",
        "registry_sha256": registry.manifest_sha256,
        "selection": {
            "algorithm": "sha256_rank_greedy_gap_v1",
            "seed": seed,
            "frames_per_camera": quota,
            "min_gap_s": gap,
            "unit": "camera_frame",
        },
        "cameras": cameras,
        "excluded_cameras": excluded,
    }
