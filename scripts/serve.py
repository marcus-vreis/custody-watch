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
import math
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

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
from custody_watch.event_journal import JournalWriter
from custody_watch.events import EventLog
from custody_watch.live_recovery import LiveRecovery, RecoveryPolicy
from custody_watch.orchestrator import LiveSession
from custody_watch.painel import Painel
from custody_watch.servidor import (
    HOST_PADRAO,
    PORTA_PADRAO,
    Servidor,
    contexto_tls,
    so_nesta_maquina,
)
from custody_watch.source_health import SourceHealth
from custody_watch.video import DEFAULT_MIN_CONFIDENCE, DEFAULT_WEIGHTS


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout precisa ser número finito positivo") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout precisa ser número finito positivo")
    return timeout


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("esperado inteiro positivo") from error
    if result <= 0:
        raise argparse.ArgumentTypeError("esperado inteiro positivo")
    return result


def _nonnegative_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("esperado inteiro não negativo") from error
    if result < 0:
        raise argparse.ArgumentTypeError("esperado inteiro não negativo")
    return result


def _source_label(value: str) -> str:
    if "://" not in value:
        return value
    try:
        parts = urlsplit(value)
        authority = parts.netloc.rsplit("@", 1)[-1]
        return f"{parts.scheme}://{authority}/…"
    except ValueError:
        return "fonte de rede"


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
    parser.add_argument(
        "--source-timeout",
        type=_positive_timeout,
        default=5.0,
        help="segundos sem nova captura local para marcar a fonte como desatualizada",
    )
    parser.add_argument("--journal-dir", type=Path, help="diretório para sessões novas do journal")
    parser.add_argument(
        "--journal-part-records",
        type=_positive_int,
        default=10000,
        help="máximo de registros por parte; não limita retenção total em disco",
    )
    parser.add_argument(
        "--journal-fsync",
        action="store_true",
        help="solicita sincronização de cada registro; exige --journal-dir",
    )
    parser.add_argument(
        "--event-tail",
        type=_positive_int,
        default=1000,
        help="máximo de eventos recentes em RAM; não apaga o journal",
    )
    parser.add_argument(
        "--reconnect-attempts",
        type=_nonnegative_int,
        default=0,
        help="retries por interrupção; 0 desativa. Exige fonte live e journal",
    )
    parser.add_argument(
        "--reconnect-backoff",
        type=_positive_timeout,
        default=1.0,
        help="espera inicial entre tentativas em segundos",
    )
    parser.add_argument(
        "--reconnect-max-backoff",
        type=_positive_timeout,
        default=30.0,
        help="teto da espera exponencial em segundos",
    )
    parser.add_argument(
        "--reconnect-close-timeout",
        type=_positive_timeout,
        default=1.0,
        help="prazo para confirmar release; sem confirmação não reconecta",
    )
    args = parser.parse_args()
    if args.journal_fsync and args.journal_dir is None:
        parser.error("--journal-fsync exige --journal-dir")
    if args.reconnect_attempts:
        if args.journal_dir is None:
            parser.error("--reconnect-attempts exige --journal-dir")
        if not fonte_de(args.fonte)[1] or args.passo != 1:
            parser.error("reconexão exige fonte live e --passo 1; arquivo nunca reconecta")
    return args


def _session(args, plane, config) -> tuple[LiveSession, JournalWriter | None]:
    journal = (
        JournalWriter(
            args.journal_dir,
            max_records_per_file=args.journal_part_records,
            fsync=args.journal_fsync,
        )
        if args.journal_dir is not None
        else None
    )
    try:
        log = EventLog(
            max_events=args.event_tail, sink=journal.emit if journal is not None else None
        )
        return LiveSession(plane, config, events=log), journal
    except Exception:
        if journal is not None:
            journal.close(reason="initialization_failed")
        raise


def _journal_status(journal: JournalWriter | None, session: LiveSession) -> dict:
    status = journal.snapshot() if journal is not None else {"state": "disabled"}
    return status | {
        "retained_events": len(session.events),
        "total_events": session.events.total_events,
    }


def _recovery_detector(args):
    # Não entregar um Model ao YOLO: ele compartilha __dict__. Uma string
    # absoluta existente cria predictor novo e evita download implícito.
    weights = Path(args.weights).resolve()

    def create():
        if not weights.is_file():
            raise ValueError("reconexão exige pesos locais existentes em --weights")
        return detector_yolo(str(weights), min_confidence=args.min_confidence)

    return create


def _capture_factory(args):
    target, _ = fonte_de(args.fonte)

    def create():
        # A CameraAoVivo é dona do release, inclusive de uma abertura malsucedida.
        return cv2.VideoCapture(target)

    return create


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


def _fonte(args: argparse.Namespace, parar: threading.Event, source_health=None):
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
        captura.release()
        raise ValueError(f"não foi possível abrir a câmera {_source_label(str(alvo))}")
    camera = CameraAoVivo(captura, parar=parar, source_health=source_health)
    return iter(camera), camera


def _anuncia(args: argparse.Namespace, servidor: Servidor, ao_vivo: bool) -> None:
    esquema = "https" if servidor.cifrado else "http"
    endereco = "localhost" if so_nesta_maquina(args.host) else args.host
    print(f"fonte   : {_source_label(args.fonte)} ({'ao vivo' if ao_vivo else 'arquivo'})")
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
    _, ao_vivo = fonte_de(args.fonte)
    health = SourceHealth("live" if ao_vivo else "file", stale_after_s=args.source_timeout)
    journal = None
    servidor = None
    recovery = None
    reconnect = getattr(args, "reconnect_attempts", 0) > 0
    try:
        config = load_config(args.config) if args.config else Config()
        plane = plane_from(args.calibration, args.metres_per_pixel)
        sessao, journal = _session(args, plane, config)
        painel = Painel(
            source_health=health,
            journal_status=lambda: _journal_status(
                journal, recovery.session if recovery and recovery.session else sessao
            ),
            recovery_status=lambda: recovery.snapshot() if recovery else None,
        )
        servidor = _servidor(args, painel, sessao)
        if reconnect:
            if not Path(args.weights).resolve().is_file():
                raise ValueError("reconexão exige pesos locais existentes em --weights")
            recovery = LiveRecovery(
                capture_factory=_capture_factory(args),
                detector_factory=_recovery_detector(args),
                session_factory=lambda log: LiveSession(plane, config, events=log),
                journal=journal,
                painel=painel,
                health=health,
                stop=parar,
                policy=RecoveryPolicy(
                    args.reconnect_attempts,
                    args.reconnect_backoff,
                    args.reconnect_max_backoff,
                    args.reconnect_close_timeout,
                ),
                event_tail=args.event_tail,
                on_session=servidor.set_session,
                escala_uniforme=args.metres_per_pixel is not None,
            )
            servidor.set_session(None)
    except (ValueError, OSError) as erro:  # ssl.SSLError é OSError
        if servidor is not None:
            servidor.server_close()
        if journal is not None:
            journal.close(reason="initialization_failed")
        print(erro, file=sys.stderr)
        return 1
    try:
        quadros, camera = (None, None) if reconnect else _fonte(args, parar, source_health=health)
    except ValueError as erro:
        if journal is not None:
            journal.close(reason="initialization_failed")
        print(erro, file=sys.stderr)
        servidor.server_close()
        return 1

    threading.Thread(target=servidor.serve_forever, daemon=True, name="servidor").start()
    _anuncia(args, servidor, ao_vivo=ao_vivo)

    try:
        if recovery is not None:
            recovery.run()
            print("fonte encerrada; estado de reconexão disponível na tela. Ctrl+C encerra.")
        else:
            processa(
                quadros,
                detector_yolo(args.weights, min_confidence=args.min_confidence),
                sessao,
                painel,
                parar=parar,
                escala_uniforme=args.metres_per_pixel is not None,
                source_health=health,
                capture_time=camera.received_at if camera is not None else None,
            )
        if journal is not None and recovery is None:
            source = health.snapshot()
            interrupted = source["state"] == "interrupted"
            journal.finish_segment(
                interrupted=interrupted, reason=source["reason"] if interrupted else "end_of_file"
            )
        if recovery is None:
            if camera is not None:
                camera.close(reason=health.snapshot()["reason"] or "read_failed")
                if not camera.wait_closed(1.0):
                    print("liberação da captura não confirmada", file=sys.stderr)
            _relata_fim(sessao, camera)
        while True:
            time.sleep(1.0)  # Event.wait sem prazo não acorda com Ctrl+C no Windows
    except KeyboardInterrupt:
        print("\nencerrando...")
    except Exception:
        if journal is not None:
            journal.finish_segment(interrupted=True, reason="processing_exception")
        raise
    finally:
        parar.set()
        if camera is not None:
            camera.close()
            if not camera.wait_closed(1.0):
                print("liberação da captura não confirmada", file=sys.stderr)
        health.finish("stopped", "user_stop")
        try:
            servidor.encerra()
        finally:
            if journal is not None:
                journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
