#!/usr/bin/env python3
"""Roda o sistema ao vivo e serve a tela do operador no navegador.

`watch_video.py` só mostra alguma coisa depois que o vídeo acaba. Numa câmera
não existe fim: esta é a primeira vez que a fila aparece enquanto a cena
acontece.

    uv run python scripts/serve.py gravacao.mp4 --metres-per-pixel 0.006
    uv run python scripts/serve.py 0 --calibration chao.json
    uv run python scripts/serve.py rtsp://camera/stream --calibration chao.json

e abra http://localhost:8765. Arquivo é processado quadro a quadro, sem
perda, com o tempo do próprio vídeo; câmera (índice ou URL) entrega sempre o
quadro mais novo, com o tempo do relógio. A escala é obrigatória pelo mesmo
motivo de `watch_video.py`: todo limiar é em metros.

Fora desta máquina (`--host 0.0.0.0`) só com TLS, `--cert` e `--key`: na
rede, HTTP em claro é a câmera aberta para quem estiver no mesmo segmento.
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
from custody_watch.servidor import (
    HOST_PADRAO,
    PORTA_PADRAO,
    Servidor,
    contexto_tls,
    so_nesta_maquina,
)
from custody_watch.video import DEFAULT_MIN_CONFIDENCE, DEFAULT_WEIGHTS


def _argumentos() -> argparse.Namespace:
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
        help="onde escutar. O padrão é só esta máquina; fora dela exige --cert e --key",
    )
    parser.add_argument("--porta", type=int, default=PORTA_PADRAO)
    parser.add_argument("--cert", type=Path, help="certificado TLS (PEM)")
    parser.add_argument("--key", type=Path, help="chave do certificado TLS (PEM)")
    return parser.parse_args()


def _servidor(args: argparse.Namespace, painel: Painel, sessao: LiveSession) -> Servidor:
    """Validado antes de abrir câmera ou carregar modelo: uma recusa por falta
    de TLS não pode custar trinta segundos de carga para aparecer."""
    if (args.cert is None) != (args.key is None):
        raise ValueError("--cert e --key vão juntos")
    for arquivo in (args.cert, args.key):
        if arquivo is not None and not arquivo.is_file():
            raise ValueError(f"{arquivo}: arquivo TLS não encontrado")
    tls = contexto_tls(args.cert, args.key) if args.cert is not None else None
    return Servidor(painel, args.host, args.porta, tls=tls, sessao=sessao)


def _fonte(args: argparse.Namespace, parar: threading.Event):
    """Os quadros, e a câmera quando é ao vivo -- para contar o que se perdeu."""
    alvo, ao_vivo = fonte_de(args.fonte)
    if not ao_vivo:
        if not Path(alvo).exists():
            raise ValueError(f"vídeo não encontrado: {alvo}")
        return quadros_de_arquivo(alvo, passo=args.passo), None

    if args.passo != 1:
        raise ValueError("--passo só vale para arquivo: câmera já descarta o que não dá tempo")
    captura = cv2.VideoCapture(alvo)
    if not captura.isOpened():
        raise ValueError(f"não foi possível abrir a câmera {alvo}")
    camera = CameraAoVivo(captura, parar=parar)
    return iter(camera), camera


def _anuncia(args: argparse.Namespace, servidor: Servidor, ao_vivo: bool) -> None:
    esquema = "https" if servidor.cifrado else "http"
    endereco = "localhost" if so_nesta_maquina(args.host) else args.host
    print(f"fonte   : {args.fonte} ({'ao vivo' if ao_vivo else 'arquivo'})")
    print(f"detector: {args.weights}, confiança mínima {args.min_confidence}")
    if not so_nesta_maquina(args.host):
        print(f"AVISO: escutando em {args.host} -- a tela fica acessível pela rede, cifrada")
    print(f"\nabra {esquema}://{endereco}:{servidor.porta}   (Ctrl+C encerra)")
    sys.stdout.flush()


def _relata_fim(sessao: LiveSession, camera: CameraAoVivo | None) -> None:
    fila = sessao.queue()
    print(f"\nfonte acabou: {sessao.frames} quadros, {sessao.t:.1f}s, {len(fila)} na fila")
    if camera is not None and camera.perdidos:
        print(f"quadros descartados para não atrasar: {camera.perdidos}")
    print("a tela continua no ar com o estado final. Ctrl+C encerra.")
    sys.stdout.flush()


def main() -> int:
    args = _argumentos()
    parar = threading.Event()
    painel = Painel()
    config = load_config(args.config) if args.config else Config()
    try:
        plane = plane_from(args.calibration, args.metres_per_pixel)
        sessao = LiveSession(plane, config)
        servidor = _servidor(args, painel, sessao)
    except (ValueError, OSError) as erro:  # ssl.SSLError é OSError
        print(erro, file=sys.stderr)
        return 1
    try:
        quadros, camera = _fonte(args, parar)
    except ValueError as erro:
        print(erro, file=sys.stderr)
        servidor.server_close()
        return 1

    threading.Thread(target=servidor.serve_forever, daemon=True, name="servidor").start()
    _anuncia(args, servidor, ao_vivo=camera is not None)

    try:
        processa(
            quadros,
            detector_yolo(args.weights, min_confidence=args.min_confidence),
            sessao,
            painel,
            parar=parar,
            escala_uniforme=args.metres_per_pixel is not None,
        )
        _relata_fim(sessao, camera)
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
