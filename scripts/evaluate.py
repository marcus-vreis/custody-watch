#!/usr/bin/env python3
"""Calcula P_miss @ RFA sobre anotação real.

Fecha o caminho entre "rodei o sistema" e "tenho o número". No dia seguinte à
gravação encenada, isso passa a ser um comando em vez de uma semana de
ferramental.

O trabalho principal aqui não é a métrica — é decidir **quais positivos
anotados podem ser medidos**. `BAG_UNATTENDED` é um estado que o sistema só
emite depois de `unattended_time_s`. Um abandono anotado que durou menos que
isso não é instância do evento, e um que foi cortado pelo fim do material não
tem resposta. Jogar os dois na conta como positivos perdidos produz um P_miss
que mede o comprimento do clipe, não o sistema.

    uv run python scripts/evaluate.py
    uv run python scripts/evaluate.py --config caminho/para/config.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from custody_watch.annotations import GroundTruthEvent, load_annotations, match_events
from custody_watch.caviar import SCENARIOS, estimate_metres_per_pixel, ground_plane, load_clip
from custody_watch.config import Config, load_config
from custody_watch.events import EventKind
from custody_watch.metrics import ScoredEvent, p_miss_at_rfa
from custody_watch.orchestrator import run_session

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
ANOTACOES = RAIZ / "data" / "annotations" / "caviar"
RFA_ALVO = 0.5
INTERESSE = {EventKind.BAG_UNATTENDED}


def _particiona(
    verdade: list[GroundTruthEvent], limiar: float
) -> tuple[list[GroundTruthEvent], list[GroundTruthEvent], list[GroundTruthEvent]]:
    """Separa os anotados em medíveis, curtos demais e incertos."""
    medivel, curto, incerto = [], [], []
    for evento in verdade:
        veredito = evento.sustained_for(limiar)
        {True: medivel, False: curto, None: incerto}[veredito].append(evento)
    return medivel, curto, incerto


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="config JSON; sem ela usa os defaults. Passa por SAFE_BOUNDS.",
    )
    args = parser.parse_args()

    if not ANOTACOES.exists():
        print(f"anotacoes ausentes em {ANOTACOES}", file=sys.stderr)
        print("rode primeiro: uv run python scripts/annotate_caviar.py", file=sys.stderr)
        return 1

    config = load_config(args.config) if args.config else Config()
    plane = ground_plane(estimate_metres_per_pixel(DATA))
    limiar = config.custody.unattended_time_s
    # O atraso do evento e funcao do limiar em uso, entao vem daqui.
    janela = limiar + 2.0

    print(f"limiar de desacompanhamento {limiar:.1f}s")
    print(f"janela de casamento [-1.0s, +{janela:.1f}s], RFA alvo {RFA_ALVO}/min\n")

    cabecalho = (
        f"{'clipe':<22}{'anotados':>9}{'medivel':>9}{'curto':>7}{'incerto':>9}"
        f"{'acertos':>9}{'perdidos':>10}{'espurios':>10}   atraso"
    )
    print(cabecalho)
    print("-" * len(cabecalho))

    marcados: list[ScoredEvent] = []
    positivos = 0
    minutos = 0.0
    perdidos: list[str] = []
    espurios: list[str] = []
    descartados: list[str] = []
    maior_abandono = 0.0

    for scenario in SCENARIOS:
        verdade = load_annotations(ANOTACOES / f"{scenario}.json")
        resultado = run_session(load_clip(DATA, scenario, with_appearance=True), plane, config)
        minutos += resultado.duration_s / 60.0

        medivel, curto, incerto = _particiona(verdade, limiar)
        for evento in curto:
            duracao = (evento.t_end or evento.t) - evento.t
            descartados.append(f"{scenario} @ {evento.t:.1f}s durou {duracao:.1f}s, curto demais")
        for evento in incerto:
            duracao = (evento.t_end or evento.t) - evento.t
            descartados.append(
                f"{scenario} @ {evento.t:.1f}s durou ao menos {duracao:.1f}s, observacao cortada"
            )
        maior_abandono = max(
            [maior_abandono] + [(e.t_end or e.t) - e.t for e in verdade if e.t_end is not None]
        )

        casamento = match_events(resultado.events, medivel, lag_window_s=janela, kinds=INTERESSE)
        positivos += casamento.total_positives

        marcados.extend(ScoredEvent(score=1.0, is_true_event=True) for _ in casamento.matched)
        marcados.extend(ScoredEvent(score=1.0, is_true_event=False) for _ in casamento.spurious)
        perdidos.extend(f"{scenario} @ {a.t:.1f}s" for a in casamento.missed)
        espurios.extend(f"{scenario} @ {e.t_start:.1f}s" for e in casamento.spurious)

        atraso = " ".join(f"{e.t_start - a.t:+.1f}s" for a, e in casamento.matched)
        print(
            f"{scenario:<22}{len(verdade):>9}{len(medivel):>9}{len(curto):>7}{len(incerto):>9}"
            f"{len(casamento.matched):>9}{len(casamento.missed):>10}"
            f"{len(casamento.spurious):>10}   {atraso}"
        )

    print()
    if descartados:
        print("anotados fora da conta:")
        for item in descartados:
            print(f"  {item}")
        print()

    if positivos == 0:
        print(f"RECUSANDO calcular P_miss: nenhum dos anotados e medivel a {limiar:.1f}s.")
        print(f"O maior abandono anotado dura {maior_abandono:.1f}s.")
        print("Um P_miss aqui mediria o comprimento do material, nao o sistema.")
        return 1

    ponto = p_miss_at_rfa(
        marcados, video_minutes=minutos, target_rfa=RFA_ALVO, total_positives=positivos
    )

    print(f"P_miss @ RFA {RFA_ALVO}/min : {ponto.p_miss:.2f}")
    print(f"falsos alarmes            : {ponto.false_alarms} em {minutos:.1f} min")
    print(f"positivos medidos         : {positivos}")

    for titulo, itens in (("perdidos", perdidos), ("espurios", espurios)):
        if itens:
            print(f"\n{titulo}:")
            for item in itens:
                print(f"  {item}")

    print(
        f"\nAVISO: {positivos} positivos dao intervalo de confianca de "
        f"aproximadamente +-40pp. Este numero indica, nao mede."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
