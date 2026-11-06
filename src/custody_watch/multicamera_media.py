"""Imagens qualificadas por câmera, segmento e quadro, somente de fontes locais."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

import cv2

from .camera_registry import CameraRecording, CameraRegistry, finite_number
from .recorded_predictions import file_digest


@dataclass(frozen=True)
class FrameImage:
    content_type: str
    data: bytes


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class FrameImages:
    def __init__(self, registry: CameraRegistry):
        self._cameras = {camera.camera_id: camera for camera in registry.cameras}
        self._frames = {
            camera.camera_id: {frame.frame_index: frame for frame in camera.recording.frames}
            for camera in registry.cameras
        }
        self._sources = {}
        for camera in registry.cameras:
            manifest = camera.recording.manifest
            if (
                manifest["source"]["kind"] == "video"
                and camera.recording.source_verified
                and camera.recording_path is not None
            ):
                path = (camera.recording_path.parent / manifest["source"]["file"]["path"]).resolve()
                expected = manifest["source"]["file"]
                if (
                    path.stat().st_size != expected["size_bytes"]
                    or file_digest(path) != expected["sha256"]
                ):
                    raise ValueError("integridade do vídeo diverge da gravação")
                self._sources[camera.camera_id] = path, _signature(path)

    def get(self, camera_id: str, segment_id: str, frame_index: int) -> FrameImage:
        camera = self._cameras.get(camera_id)
        if camera is None or camera.segment_id != segment_id:
            raise ValueError("câmera ou segmento desconhecido")
        if type(frame_index) is not int:
            raise ValueError("frame_index: esperado inteiro")
        frame = self._frames[camera_id].get(frame_index)
        if frame is None or frame.status != "observed":
            raise ValueError("quadro observado não encontrado")
        if camera.recording.manifest["source"]["kind"] == "synthetic":
            return FrameImage("image/svg+xml", self._schematic(camera, frame))
        source = self._sources.get(camera_id)
        if source is None:
            raise ValueError("vídeo local indisponível")
        path, signature = source
        if _signature(path) != signature:
            raise ValueError("vídeo alterado após o carregamento")
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened() or not capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index):
                raise ValueError("não foi possível posicionar o vídeo local")
            ok, image = capture.read()
            if not ok or image is None:
                raise ValueError("não foi possível ler o quadro local")
            position = finite_number(capture.get(cv2.CAP_PROP_POS_FRAMES), "índice do decoder")
            if abs(position - frame_index - 1) > 0.5:
                raise ValueError("decoder não confirmou o índice do quadro")
            for detection in frame.detections:
                x1, y1, x2, y2 = (int(value) for value in detection.bbox)
                color = (202, 213, 107) if detection.cls == "person" else (130, 187, 243)
                cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    image,
                    str(detection.track_id),
                    (x1, max(16, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                    cv2.LINE_AA,
                )
            height, width = image.shape[:2]
            scale = min(1, 1280 / width, 720 / height)
            if scale < 1:
                image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))))
            ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                raise ValueError("falha na codificação JPEG")
            if _signature(path) != signature:
                raise ValueError("vídeo alterado durante a leitura")
            return FrameImage("image/jpeg", encoded.tobytes())
        finally:
            capture.release()

    @staticmethod
    def _schematic(camera: CameraRecording, frame) -> bytes:
        detections = [det for item in camera.recording.frames for det in item.detections]
        left = min(0, *(det.bbox[0] for det in detections)) if detections else 0
        top = min(0, *(det.bbox[1] for det in detections)) if detections else 0
        width = (
            finite_number(max(400, *(d.bbox[2] for d in detections)) - left, "largura do esquema")
            if detections
            else 400
        )
        height = (
            finite_number(max(240, *(d.bbox[3] for d in detections)) - top, "altura do esquema")
            if detections
            else 240
        )
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left} {top} {width} {height}">',
            '<defs><pattern id="grid" width="25" height="25" patternUnits="userSpaceOnUse">'
            '<path d="M 25 0 L 0 0 0 25" fill="none" stroke="#243344" stroke-width=".6"/>'
            "</pattern></defs>",
            f'<rect x="{left}" y="{top}" width="{width}" height="{height}" fill="#101a26"/>',
            f'<rect x="{left}" y="{top}" width="{width}" height="{height}" fill="url(#grid)"/>',
        ]
        for det in frame.detections:
            x1, y1, x2, y2 = det.bbox
            color = "#6bd5c2" if det.cls == "person" else "#f3bb82"
            label = {
                "person": "Pessoa",
                "suitcase": "Mala",
                "backpack": "Mochila",
                "handbag": "Bolsa",
            }[det.cls]
            parts.extend(
                [
                    f'<rect x="{x1}" y="{y1}" width="{finite_number(x2 - x1, "largura")}" '
                    f'height="{finite_number(y2 - y1, "altura")}" fill="{color}" fill-opacity=".1" '
                    f'stroke="{color}" stroke-width="2"/>',
                    f'<text x="{x1 + 5}" y="{y1 + 18}" fill="{color}" font-size="12" '
                    f'font-family="sans-serif">{label} #{det.track_id}</text>',
                ]
            )
        parts.append(
            f'<text x="{left + 10}" y="{top + height - 12}" fill="#9aabba" '
            f'font-size="11" font-family="sans-serif">Esquema sintético · '
            f"{escape(camera.camera_id)} · mídia {frame.t:g}s</text></svg>"
        )
        return "".join(parts).encode("utf-8")
