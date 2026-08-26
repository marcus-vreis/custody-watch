#!/usr/bin/env python3
"""Varre a qualidade de percepção e acha onde a lógica deixa de operar.

A pergunta não é "qual é o ruído do nosso detector" — para bagagem não há
amostra que responda, uma detecção pareada em 1.686. A pergunta é **quão bom o
detector precisa ficar** para a camada de lógica caber no orçamento do
operador, e é ela que a gravação encenada precisa responder.

No CAVIAR a verdade é conhecida sem anotação nenhuma: os quatro clipes contêm
**zero furtos**, então qualquer `BAG_REMOVED_BY_STRANGER` é falso por
construção.

    uv run python scripts/envelope.py
"""

from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

from custody_watch.caviar import SCENARIOS, estimate_metres_per_pixel, ground_plane, load_clip
from custody_watch.config import load_config
from custody_watch.events import EventKind
from custody_watch.noise import CAVIAR_PERSON_NOISE, NoiseModel, degrade
from custody_watch.orchestrator import run_session

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
CONFIG = RAIZ / "config" / "caviar.json"

TAXAS_DE_FALHA = (0.0, 0.1, 0.3, 0.5, 0.7, 0.9)
ERROS_DE_POSICAO_PX = (0.0, 2.0, 4.0, 8.0)
SEMENTES = (1, 2, 3, 4, 5)
RAJADA_QUADROS = 12


def _uma_passada(bag: NoiseModel, seed: int, plane, config) -> tuple[int, int, float]:
    """Devolve (falsos furtos, abandonos verdadeiros, minutos)."""
    falsos = verdadeiros = 0
    minutos = 0.0

    for indice, scenario in enumerate(SCENARIOS):
        limpo = load_clip(DATA, scenario, with_appearance=True)
        sujo = degrade(limpo, person=CAVIAR_PERSON_NOISE, bag=bag, seed=seed * 100 + indice)
        resultado = run_session(sujo, plane, config)

        falsos += len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER))
        verdadeiros += len(resultado.events.of_kind(EventKind.BAG_UNATTENDED))
        minutos += resultado.duration_s / 60.0

    return falsos, verdadeiros, minutos


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        print("rode primeiro: uv run python scripts/download_caviar.py", file=sys.stderr)
        return 1

    config = load_config(CONFIG)
    plane = ground_plane(estimate_metres_per_pixel(DATA))
    orcamento = config.alerts.operator_hourly_budget / 60.0

    print(f"ruido de pessoa fixo em {CAVIAR_PERSON_NOISE}")
    print(f"rajada de falha de bagagem: {RAJADA_QUADROS} quadros")
    print(f"limiar de desacompanhamento: {config.custody.unattended_time_s}s")
    print(f"orcamento do operador: {config.alerts.operator_hourly_budget}/h = {orcamento:.2f}/min")
    print(f"{len(SEMENTES)} sementes por celula\n")

    print("Os quatro clipes do CAVIAR contem ZERO furtos: todo furto abaixo e falso.")
    print("A coluna 'abandonos' e a guarda contra vacuidade -- se ela zera, o zero")
    print("de falsos alarmes so significa que o sistema parou de ver qualquer coisa.\n")

    cabecalho = f"{'falha':>7}{'erro':>7}  {'falsos/min':>22}  {'abandonos':>11}  veredito"
    print(cabecalho)
    print("-" * len(cabecalho))

    for taxa in TAXAS_DE_FALHA:
        for erro in ERROS_DE_POSICAO_PX:
            bag = NoiseModel(
                drop_rate=taxa,
                drop_burst_frames=RAJADA_QUADROS,
                position_sigma_px=erro,
            )

            por_minuto: list[float] = []
            abandonos: list[int] = []
            for seed in SEMENTES:
                falsos, verdadeiros, minutos = _uma_passada(bag, seed, plane, config)
                por_minuto.append(falsos / minutos)
                abandonos.append(verdadeiros)

            media = st.mean(por_minuto)
            desvio = st.pstdev(por_minuto)
            vistos = st.mean(abandonos)

            if vistos < 0.5:
                veredito = "VACUO: nao ve mais nada"
            elif media <= orcamento:
                veredito = "dentro do orcamento"
            else:
                veredito = "ESTOURA o orcamento"

            print(
                f"{taxa:>6.0%}{erro:>6.1f}px  {media:>10.2f} +- {desvio:<8.2f}"
                f"{vistos:>11.1f}  {veredito}"
            )
        print()

    print("Isto mede degradacao da logica sob ruido sintetico, com movimento humano")
    print("real por baixo. Nao mede percepcao, e nao substitui a gravacao encenada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
