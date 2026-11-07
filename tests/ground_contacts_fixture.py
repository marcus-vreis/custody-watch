"""Declarações sintéticas de contato, sem inferir confiança de produção."""

from tests.recorded_fixture import write_json


def make_contacts(path, registry):
    cameras = []
    for camera in registry.cameras:
        first = {
            "frame_index": 0,
            "local_track_id": 1,
            "status": "observed",
            "point_px": [150 if camera.camera_id == "cam-a" else 250, 200],
            "provenance": "synthetic",
            "method": "Contato definido pelo gerador da cena plana sintética.",
        }
        observations = [first]
        if camera.camera_id == "cam-a":
            observations += [
                {**first, "local_track_id": 9, "status": "occluded", "point_px": None},
                {**first, "frame_index": 1, "status": "unknown", "point_px": None},
            ]
        cameras.append(
            {
                "camera_id": camera.camera_id,
                "segment_id": camera.segment_id,
                "sequence_sha256": camera.recording.manifest_sha256,
                "image_size_px": [800, 600],
                "observations": observations,
            }
        )
    write_json(
        path, {"schema_version": 1, "registry_sha256": registry.manifest_sha256, "cameras": cameras}
    )
    return path
