#!/usr/bin/env python3
"""Roda o orquestrador no CAVIAR e monta a página de revisão do operador.

Fecha a última lacuna entre o que o sistema decide e o que um humano consegue
julgar: até aqui a fila existia só como lista de dataclasses, e `clip_start` e
`clip_end` eram números sem vídeo por trás.

Todo alerta produzido aqui é falso por construção — o CAVIAR não contém furto.
A página diz isso no cabeçalho, porque mostrar contagem de alerta sem esse
contexto induziria a achar que o sistema encontrou alguma coisa.

    uv run python scripts/build_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from custody_watch.caviar import (
    SCENARIOS,
    estimate_metres_per_pixel,
    ground_plane,
    load_clip,
    load_frames,
)
from custody_watch.clips import ClipRequest, render_clip
from custody_watch.orchestrator import SessionResult, run_session
from custody_watch.report import ReviewItem, SessionReport, write_report

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
SAIDA = RAIZ / "outputs"

AVISO = (
    "Estes clipes vêm do CAVIAR, que não contém furto algum: verificado no ground "
    "truth, quem retira a bagagem é sempre quem a deixou, com o track fragmentado "
    "no meio. Todo item abaixo é, por construção, um alarme falso — é exatamente "
    "isso que a página serve para medir."
)


def _bag_bruto(resultado: SessionResult) -> int | None:
    """Id da bagagem citada nos eventos, para destacar no clipe."""
    for evento in resultado.events:
        if evento.bag is not None:
            return evento.bag
    return None


def _itens(resultado: SessionResult, cenario: str) -> list[ReviewItem]:
    bag = _bag_bruto(resultado)
    # `bag` é o bag_id canônico, mas os frames podem trazer um track diferente
    # depois de uma readoção sob oclusão (Finding 5) -- `raw_bag_ids` traz
    # todo track que já respondeu por ele, para o clipe destacar a caixa
    # certa qualquer que seja o id vigente na janela recortada.
    bag_ids = frozenset(resultado.raw_bag_ids(bag)) if bag is not None else frozenset()
    itens: list[ReviewItem] = []

    for posicao, alerta in enumerate(resultado.queue, start=1):
        destino = SAIDA / "clipes" / f"{cenario}_{alerta.person}.gif"
        caminho = render_clip(
            load_frames(DATA, cenario),
            ClipRequest(
                start_s=alerta.clip_start,
                end_s=alerta.clip_end,
                person_ids=frozenset(resultado.raw_ids(alerta.person)),
                bag_ids=bag_ids,
                output=destino,
            ),
        )
        itens.append(
            ReviewItem(
                rank=posicao,
                person=alerta.person,
                score=alerta.score,
                level=alerta.top_level.name,
                clip_start=alerta.clip_start,
                clip_end=alerta.clip_end,
                explanations=alerta.explanations,
                clip_path=caminho,
            )
        )

    return itens


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        print("rode primeiro: uv run python scripts/download_caviar.py", file=sys.stderr)
        return 1

    plane = ground_plane(estimate_metres_per_pixel(DATA))
    sessoes: list[SessionReport] = []

    for cenario in SCENARIOS:
        resultado = run_session(load_clip(DATA, cenario, with_appearance=True), plane)
        contagem: dict[str, int] = {}
        for evento in resultado.events:
            contagem[evento.kind.value] = contagem.get(evento.kind.value, 0) + 1

        itens = _itens(resultado, cenario)
        print(f"{cenario:<22} {len(itens)} item(ns) na fila")

        sessoes.append(
            SessionReport(
                name=cenario,
                duration_s=resultado.duration_s,
                frames=resultado.frames,
                events=contagem,
                items=itens,
            )
        )

    destino = write_report(
        sessoes,
        SAIDA / "fila_de_revisao.html",
        titulo="Revisão de Custódia",
        aviso=AVISO,
    )
    tamanho = destino.stat().st_size / 1e6
    print(f"\npágina: {destino}  ({tamanho:.1f} MB)")
    if tamanho > 15.0:
        print("AVISO: perto do teto de 16 MB do artifact", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
