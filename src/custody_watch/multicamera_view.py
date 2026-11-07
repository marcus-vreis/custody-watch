"""Snapshots sem estado para buscar para frente ou para trás no laboratório."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict
from urllib.parse import urlencode

from .camera_registry import CameraRecording, CameraRegistry, finite_number
from .multicamera_replay import SynchronizedFrame, frame_to_dict


def media_kind(camera: CameraRecording) -> str:
    if camera.recording.manifest["source"]["kind"] == "synthetic":
        return "schematic"
    return "video" if camera.recording.source_verified else "unavailable"


def _xy(point):
    return None if point is None else [point.x, point.y]


class _CameraTimeline:
    def __init__(self, camera: CameraRecording):
        self.camera = camera
        clock = camera.recording.manifest["clock"]
        self.period = finite_number(1 / clock["fps"], "período do replay")
        self.start = camera.clock.global_time(clock["start_s"])
        self.end = finite_number(
            camera.clock.global_time(clock["end_s"]) + self.period, "fim do replay"
        )
        self.rows = tuple(
            frame_to_dict(
                SynchronizedFrame(
                    camera, frame, camera.clock.global_time(frame.t), camera.clock.interval(frame.t)
                )
            )
            for frame in camera.recording.frames
        )
        self.times = tuple(row["t"] for row in self.rows)
        self.observed = tuple(row for row in self.rows if row["status"] == "observed")
        self.observed_times = tuple(row["t"] for row in self.observed)
        self.track_history: dict[int, tuple[list[float], list[dict]]] = {}
        for row in self.observed:
            for track in row["tracks"]:
                key = track["track_key"]["local_track_id"]
                times, values = self.track_history.setdefault(key, ([], []))
                times.append(row["t"])
                values.append(track)

    def snapshot(self, t: float) -> dict:
        camera = self.camera
        index = bisect_right(self.times, t) - 1
        row = None if index < 0 else self.rows[index]
        if t < self.start:
            status = "not_started"
        elif t >= self.end:
            status = "completed"
        elif row is None:
            status = "unavailable"
        elif row["status"] == "missing":
            status = "missing"
        elif t >= row["t"] + self.period:
            status = "stale"
        else:
            status = "observed"
        last_tracks = []
        if status != "not_started":
            for key in sorted(self.track_history):
                times, tracks = self.track_history[key]
                last = bisect_right(times, t) - 1
                if last >= 0:
                    last_tracks.append(
                        {
                            **tracks[last],
                            "t": times[last],
                            "age_s": finite_number(t - times[last], "idade da observação"),
                        }
                    )
        observed_index = bisect_right(self.observed_times, t) - 1
        image = None
        kind = media_kind(camera)
        if observed_index >= 0 and kind != "unavailable" and status != "not_started":
            observed = self.observed[observed_index]
            query = urlencode(
                {
                    "camera_id": camera.camera_id,
                    "segment_id": camera.segment_id,
                    "frame_index": observed["frame_index"],
                }
            )
            image = {
                "url": f"/api/image?{query}",
                "t": observed["t"],
                "media_t": observed["media_t"],
                "frame_index": observed["frame_index"],
                "age_s": finite_number(t - observed["t"], "idade da imagem"),
                "historical": status != "observed",
                "kind": kind,
            }
        return {
            "camera_id": camera.camera_id,
            "floor_id": camera.floor_id,
            "segment_id": camera.segment_id,
            "status": status,
            "frame_index": None if row is None else row["frame_index"],
            "frame_t": None if row is None else row["t"],
            "time_interval_s": None if row is None else row["time_interval_s"],
            "image": image,
            "tracks": row["tracks"] if status == "observed" else [],
            "last_tracks": last_tracks,
        }


class ReplayView:
    def __init__(self, registry: CameraRegistry):
        self.registry = registry
        self._cameras = tuple(
            _CameraTimeline(camera)
            for camera in sorted(registry.cameras, key=lambda camera: camera.camera_id)
        )

    def config(self) -> dict:
        registry = self.registry
        return {
            "schema_version": 2,
            "site_id": registry.site_id,
            "position_method": "unverified_bbox_bottom_projection",
            "floors": [asdict(floor) for floor in registry.floors],
            "timeline": {
                "start_s": finite_number(
                    min(c.start - c.period for c in self._cameras), "início do replay"
                ),
                "end_s": max(c.end for c in self._cameras),
                "step_s": 0.05,
            },
            "cameras": [
                {
                    "camera_id": c.camera_id,
                    "floor_id": c.floor_id,
                    "segment_id": c.segment_id,
                    "position_m": _xy(c.position_m),
                    "coverage_m": None if c.coverage_m is None else [_xy(p) for p in c.coverage_m],
                    "projection_frame_id": c.projection_frame_id,
                    "clock": asdict(c.clock),
                    "source_kind": c.recording.manifest["source"]["kind"],
                    "mode": c.recording.manifest["mode"],
                    "provenance": c.recording.manifest["predictions"]["provenance"],
                    "media_kind": media_kind(c),
                }
                for c in (item.camera for item in self._cameras)
            ],
        }

    def snapshot(self, t: float) -> dict:
        t = finite_number(t, "tempo do replay")
        return {
            "schema_version": 2,
            "t": t,
            "cameras": [camera.snapshot(t) for camera in self._cameras],
        }
