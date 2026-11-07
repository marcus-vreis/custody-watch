"""Abre o laboratório de replay com mosaico e planta, somente em localhost."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from custody_watch.camera_registry import load_camera_registry
from custody_watch.multicamera_media import FrameImages
from custody_watch.multicamera_server import ReplayServer
from custody_watch.multicamera_view import ReplayView


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="cadastro versionado do laboratório")
    parser.add_argument("--port", type=int, default=8766, help="porta local (default: 8766)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port: esperado número entre 0 e 65535")
    try:
        registry = load_camera_registry(args.registry)
        view, images = ReplayView(registry), FrameImages(registry)
        with ReplayServer(view, images, port=args.port) as server:
            print(
                f"Laboratório: http://127.0.0.1:{server.server_port} · Ctrl+C encerra", flush=True
            )
            server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(f"Laboratório recusado: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
