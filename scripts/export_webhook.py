"""Acompanha uma sessão do journal e entrega cada evento confirmado a um webhook.

Processo separado do pipeline: lê só linhas completas, assina com HMAC-SHA256
(segredo em CUSTODY_WATCH_WEBHOOK_SECRET) e guarda o progresso no cursor.
Entrega at-least-once: o destino deduplica pelo id do evento.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from custody_watch.webhook_export import (
    ExportConfig,
    WebhookConfigError,
    WebhookExporter,
    load_secret,
    validate_destination,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path, help="diretório da sessão (<journal>/<id>)")
    parser.add_argument("--url", required=True, help="destino http(s); https fora de loopback")
    parser.add_argument(
        "--cursor",
        type=Path,
        required=True,
        help="arquivo de progresso, dentro do diretório atual (criado se ausente)",
    )
    parser.add_argument("--once", action="store_true", help="drena o que existe e sai")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="segundos entre passadas")
    parser.add_argument("--timeout", type=float, default=5.0, help="timeout por tentativa (s)")
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--backoff-max", type=float, default=30.0, help="teto do backoff (s)")
    parser.add_argument("--max-line-bytes", type=int, default=65536)
    parser.add_argument(
        "--stats-every",
        type=float,
        default=0.0,
        help="imprime contadores a cada N s (0: só ao sair)",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        validate_destination(args.url)
        config = ExportConfig(
            url=args.url,
            secret=load_secret(os.environ),
            timeout_s=args.timeout,
            max_attempts=args.max_attempts,
            backoff_max_s=args.backoff_max,
            max_line_bytes=args.max_line_bytes,
        )
        if not 0 < args.poll_interval <= 3600 or not 0 <= args.stats_every <= 86400:
            raise WebhookConfigError("--poll-interval ou --stats-every fora da faixa")
        if not args.session_dir.is_dir():
            raise WebhookConfigError("diretório da sessão não encontrado")
        exporter = WebhookExporter(args.session_dir, args.cursor, config)
    except WebhookConfigError as error:
        print(f"export_webhook: {error}", file=sys.stderr)
        return 2

    outcome = "caught_up"
    last_stats = time.monotonic()
    try:
        while True:
            outcome = exporter.run_once()
            if args.once:
                break
            if args.stats_every > 0 and time.monotonic() - last_stats >= args.stats_every:
                print(exporter.stats.to_json_line(), flush=True)
                last_stats = time.monotonic()
            # Parado: espera o teto do backoff antes de tentar o mesmo evento de novo.
            time.sleep(args.backoff_max if outcome == "stalled" else args.poll_interval)
    except KeyboardInterrupt:
        pass
    print(exporter.stats.to_json_line(), flush=True)
    return 1 if outcome == "stalled" else 0


if __name__ == "__main__":
    raise SystemExit(main())
