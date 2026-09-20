#!/usr/bin/env python3
"""Roda o sistema num arquivo de vídeo e monta a página de revisão.

**É a primeira vez que percepção e lógica se encostam.** Até aqui os quatro
chamadores de `run_session` consumiam `load_clip` — as caixas anotadas do
CAVIAR. `VideoSource` existia e era usado só por scripts de medição. Todo
número que este projeto publicou saiu de ground truth, nunca de detector.

Também é o primeiro tijolo da plataforma: trocar arquivo por RTSP depois é
trocar de onde vêm os quadros, não reescrever o pipeline.

    uv run python scripts/watch_video.py cena.mp4 --metres-per-pixel 0.0085
    uv run python scripts/watch_video.py cena.mp4 --calibration chao.json

Uma das duas é obrigatória, e não há default: todo limiar deste sistema é em
metros, e um plano do chão chutado faz todos eles mentirem sem sintoma nenhum.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from custody_watch.calibration import plane_from, uniform_scale_warning
from custody_watch.config import Config, load_config
from custody_watch.orchestrator import run_session
from custody_watch.report import SessionReport, write_report
from custody_watch.review import review_items
from custody_watch.tracking import TrackedDetection
from custody_watch.video import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_WEIGHTS,
    VideoSource,
    raw_frames,
)

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "outputs"

AVISO = (
    "Esta página vem de detecção real, não de caixa anotada — é a primeira vez "
    "que a metade de percepção do sistema alimenta a metade de lógica. Medido no "
    "CAVIAR, o detector acha 30,5% das pessoas e 0,1% das bagagens a 384x288, "
    "então uma fila vazia aqui provavelmente diz mais sobre o que ele não viu do "
    "que sobre o que aconteceu na cena."
)


def _para_o_clipe(video: Path, fps: float, caixas: dict[int, list[TrackedDetection]]):
    """Quadros no formato do recorte: instante, imagem, detecções.

    As caixas vêm do cache da passagem que a sessão já fez, e não de uma
    detecção nova. Detectar outra vez custaria o dobro para produzir exatamente
    as mesmas caixas — e produziria outras, porque o tracker é sequencial.
    """
    for indice, (t, imagem) in enumerate(raw_frames(video, fps=fps)):
        yield t, imagem, caixas.get(indice, [])


def _aviso_de_escala(caixas: dict[int, list[TrackedDetection]]) -> str | None:
    return uniform_scale_warning(
        [
            caixa.bbox[3] - caixa.bbox[1]
            for quadro in caixas.values()
            for caixa in quadro
            if caixa.cls == "person"
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="arquivo de vídeo a analisar")
    parser.add_argument(
        "--calibration",
        type=Path,
        help="calibração medida do chão; recusa acima de 25cm de resíduo",
    )
    parser.add_argument(
        "--metres-per-pixel",
        type=float,
        help=(
            "escala uniforme, quando não há calibração. Para estimar: meça em "
            "pixels a altura de uma pessoa no quadro e divida 1.70 por ela"
        ),
    )
    parser.add_argument("--config", type=Path, help="config JSON; sem ela usa os defaults")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS, help="pesos do detector")
    parser.add_argument(
        "--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE, help="corte do detector"
    )
    args = parser.parse_args()

    if not args.video.exists():
        print(f"vídeo não encontrado: {args.video}", file=sys.stderr)
        return 1

    try:
        plane = plane_from(args.calibration, args.metres_per_pixel)
    except ValueError as erro:
        print(erro, file=sys.stderr)
        return 1

    config = load_config(args.config) if args.config else Config()
    nome = args.video.stem

    def fonte() -> VideoSource:
        return VideoSource(
            args.video,
            weights=args.weights,
            min_confidence=args.min_confidence,
            with_appearance=True,
        )

    fps = fonte().fps
    print(f"vídeo   : {args.video}")
    print(f"fps     : {fps}")
    print(f"detector: {args.weights}, confiança mínima {args.min_confidence}")
    print("decodificando e detectando...")
    sys.stdout.flush()

    # As caixas de cada quadro ficam guardadas na passagem da sessão. São
    # kilobytes, contra dezenas de gigabytes se fossem as imagens.
    caixas: dict[int, list[TrackedDetection]] = {}

    def observacoes():
        for indice, quadro in enumerate(fonte()):
            caixas[indice] = quadro.tracked
            yield quadro.t, quadro.tracked

    resultado = run_session(observacoes(), plane, config)

    contagem: dict[str, int] = {}
    for evento in resultado.events:
        contagem[evento.kind.value] = contagem.get(evento.kind.value, 0) + 1

    print(f"\n{resultado.frames} quadros, {resultado.duration_s:.1f}s")
    print(f"eventos : {contagem or 'nenhum'}")
    print(f"fila    : {len(resultado.queue)} item(ns)")

    if args.metres_per_pixel is not None:
        aviso = _aviso_de_escala(caixas)
        if aviso:
            print(f"\n{aviso}", file=sys.stderr)

    if not contagem:
        print(
            "\nNenhum evento. Antes de culpar a lógica, confira o detector:\n"
            "  uv run python scripts/detector_baseline.py",
            file=sys.stderr,
        )

    # Segunda passagem pelo arquivo, agora sem detector: só decodificação, com
    # as caixas vindo do cache acima.
    print("\nrecortando clipes...")
    sys.stdout.flush()
    quadros = partial(_para_o_clipe, args.video, fps, caixas)
    itens = review_items(resultado, quadros, SAIDA, nome)

    destino = write_report(
        [
            SessionReport(
                name=nome,
                duration_s=resultado.duration_s,
                frames=resultado.frames,
                events=contagem,
                items=itens,
            )
        ],
        SAIDA / f"revisao_{nome}.html",
        titulo=f"Revisão de Custódia — {nome}",
        aviso=AVISO,
    )

    tamanho = destino.stat().st_size / 1e6
    print(f"\npágina: {destino}  ({tamanho:.1f} MB)")
    if tamanho > 15.0:
        print("AVISO: perto do teto de 16 MB do artifact", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
