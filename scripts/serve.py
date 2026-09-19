#!/usr/bin/env python3
"""Roda o sistema ao vivo e serve a tela do operador no navegador.

`watch_video.py` só mostra alguma coisa depois que o vídeo acaba. Numa câmera
não existe fim: esta é a primeira vez que a fila aparece enquanto a cena
acontece.

    uv run python scripts/serve.py gravacao.mp4 --metres-per-pixel 0.006
    uv run python scripts/serve.py 0 --calibration chao.json
    uv run python scripts/serve.py rtsp://camera/stream --calibration chao.json

e abra http://localhost:8765. Arquivo é processado quadro a quadro, sem perda,
com o tempo do próprio vídeo; câmera (índice ou URL) entrega sempre o quadro
mais novo, com o tempo do relógio. A escala é obrigatória pelo mesmo motivo
de `watch_video.py`: todo limiar é em metros.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import cv2

from custody_watch.aovivo import (
    CameraAoVivo,
    detector_yolo,
    fonte_de,
    processa,
    quadros_de_arquivo,
)
from custody_watch.calibration import plane_from
from custody_watch.config import Config, load_config
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel
from custody_watch.servidor import HOST_PADRAO, PORTA_PADRAO, Servidor
from custody_watch.video import DEFAULT_MIN_CONFIDENCE, DEFAULT_WEIGHTS


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("fonte", help="arquivo de vídeo, índice de câmera (0) ou URL rtsp://")
    parser.add_argument("--calibration", type=Path, help="calibração medida do chão")
    parser.add_argument(
        "--metres-per-pixel",
        type=float,
        help="escala uniforme; 1.70 dividido pela altura de uma pessoa em pixels",
    )
    parser.add_argument("--config", type=Path, help="config JSON; sem ela usa os defaults")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS, help="pesos do detector")
    parser.add_argument(
        "--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE, help="corte do detector"
    )
    parser.add_argument(
        "--passo",
        type=int,
        default=1,
        help="só para arquivo: processa um quadro a cada N, sem encolher o tempo",
    )
    parser.add_argument(
        "--host",
        default=HOST_PADRAO,
        help="onde escutar. O padrão é só esta máquina; 0.0.0.0 abre para a rede",
    )
    parser.add_argument("--porta", type=int, default=PORTA_PADRAO)
    args = parser.parse_args()

    try:
        plane = plane_from(args.calibration, args.metres_per_pixel)
    except ValueError as erro:
        print(erro, file=sys.stderr)
        return 1

    alvo, ao_vivo = fonte_de(args.fonte)
    if not ao_vivo and not Path(alvo).exists():
        print(f"vídeo não encontrado: {alvo}", file=sys.stderr)
        return 1
    if ao_vivo and args.passo != 1:
        print(
            "--passo só vale para arquivo: câmera já descarta o que não dá tempo", file=sys.stderr
        )
        return 1

    config = load_config(args.config) if args.config else Config()
    parar = threading.Event()

    if ao_vivo:
        captura = cv2.VideoCapture(alvo)
        if not captura.isOpened():
            print(f"não foi possível abrir a câmera {alvo}", file=sys.stderr)
            return 1
        camera = CameraAoVivo(captura, parar=parar)
        quadros = iter(camera)
    else:
        camera = None
        quadros = quadros_de_arquivo(alvo, passo=args.passo)

    painel = Painel()
    servidor = Servidor(painel, args.host, args.porta)
    threading.Thread(target=servidor.serve_forever, daemon=True, name="servidor").start()

    endereco = "localhost" if args.host in ("127.0.0.1", "localhost") else args.host
    print(f"fonte   : {args.fonte} ({'ao vivo' if ao_vivo else 'arquivo'})")
    print(f"detector: {args.weights}, confiança mínima {args.min_confidence}")
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"AVISO: escutando em {args.host} -- a imagem da câmera fica visível na rede")
    print(f"\nabra http://{endereco}:{servidor.porta}   (Ctrl+C encerra)")
    sys.stdout.flush()

    sessao = LiveSession(plane, config)
    try:
        processa(
            quadros,
            detector_yolo(args.weights, min_confidence=args.min_confidence),
            sessao,
            painel,
            parar=parar,
            escala_uniforme=args.metres_per_pixel is not None,
        )
        fila = sessao.queue()
        print(f"\nfonte acabou: {sessao.frames} quadros, {sessao.t:.1f}s, {len(fila)} na fila")
        if camera is not None and camera.perdidos:
            print(f"quadros descartados para não atrasar: {camera.perdidos}")
        print("a tela continua no ar com o estado final. Ctrl+C encerra.")
        sys.stdout.flush()
        while True:
            time.sleep(1.0)  # Event.wait sem prazo não acorda com Ctrl+C no Windows
    except KeyboardInterrupt:
        print("\nencerrando...")
    finally:
        parar.set()
        servidor.encerra()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
