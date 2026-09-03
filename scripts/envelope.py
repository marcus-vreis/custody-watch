#!/usr/bin/env python3
"""Varre a qualidade de percepção e acha onde a lógica deixa de operar.

A pergunta não é "qual é o ruído do nosso detector" — para bagagem não há
amostra que responda, uma detecção pareada em 1.686. A pergunta é **quão bom o
detector precisa ficar**, e é ela que a gravação encenada precisa responder.

## Um eixo por vez

A primeira versão desta varredura fixava o ruído de pessoa no valor medido e
varria só a bagagem. Não funcionou, e o motivo é o resultado: **o ruído de
pessoa medido já destrói o pipeline sozinho.** Com 69,5% de falha, metade das
bagagens nunca ganha dono, e bagagem órfã não acumula tempo desacompanhado.
O eixo varrido não tinha o que mostrar, porque o eixo fixo tinha chegado antes.

Então cada eixo é varrido com o outro **limpo**. Isso é irreal de propósito:
o objetivo é o requisito por eixo, não o comportamento num ponto de operação.

## O modo de falha que importa

A primeira versão também mediu a coisa errada. Sob ruído o sistema quase não
inventa furto — ele **fica mudo**. Falso alarme por minuto fica em zero em
quase toda a tabela, e um zero por silêncio lê como sucesso.

Então a coluna que decide é `retidos`: quantos dos eventos verdadeiros da
execução limpa sobrevivem. Falso alarme continua reportado, porque quando ele
aparece importa muito — mas ele não é onde a degradação mora.

    uv run python scripts/envelope.py
"""

from __future__ import annotations

import statistics as st
import sys
from dataclasses import replace
from pathlib import Path

from custody_watch.caviar import SCENARIOS, estimate_metres_per_pixel, ground_plane, load_clip
from custody_watch.config import load_config
from custody_watch.events import EventKind
from custody_watch.noise import NoiseModel, degrade
from custody_watch.orchestrator import run_session

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
CONFIG = RAIZ / "config" / "caviar.json"

TAXAS_DE_FALHA = (0.0, 0.05, 0.10, 0.20, 0.40, 0.80)
ERROS_DE_POSICAO_PX = (0.0, 1.0, 2.0, 4.0, 8.0)
SEMENTES = tuple(range(1, 9))
RAJADA_QUADROS = 12
LIMPO = NoiseModel()

VARREDURAS = (
    ("PESSOA — falha de deteccao", "person", "drop_rate", TAXAS_DE_FALHA, "{:.0%}"),
    ("PESSOA — erro de posicao", "person", "position_sigma_px", ERROS_DE_POSICAO_PX, "{:.1f}px"),
    ("BAGAGEM — falha de deteccao", "bag", "drop_rate", TAXAS_DE_FALHA, "{:.0%}"),
    ("BAGAGEM — erro de posicao", "bag", "position_sigma_px", ERROS_DE_POSICAO_PX, "{:.1f}px"),
)

CABECALHO = f"{'valor':>8}  {'falsos/min':>18}{'posses':>9}{'retidos':>10}  veredito"


def _uma_passada(person: NoiseModel, bag: NoiseModel, seed: int, plane, config):
    """Devolve (falsos furtos, posses, abandonos, minutos) sobre os quatro clipes."""
    falsos = posses = abandonos = 0
    minutos = 0.0

    for indice, scenario in enumerate(SCENARIOS):
        sujo = degrade(
            load_clip(DATA, scenario, with_appearance=True),
            person=person,
            bag=bag,
            seed=seed * 100 + indice,
        )
        resultado = run_session(sujo, plane, config)

        falsos += len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER))
        posses += len(resultado.events.of_kind(EventKind.BAG_OWNED))
        abandonos += len(resultado.events.of_kind(EventKind.BAG_UNATTENDED))
        minutos += resultado.duration_s / 60.0

    return falsos, posses, abandonos, minutos


def _celula(person: NoiseModel, bag: NoiseModel, plane, config, base: int):
    """Uma célula da tabela: várias sementes do mesmo ponto de operação."""
    por_minuto: list[float] = []
    posses: list[int] = []
    retidos: list[float] = []

    for seed in SEMENTES:
        falsos, p, a, minutos = _uma_passada(person, bag, seed, plane, config)
        por_minuto.append(falsos / minutos)
        posses.append(p)
        retidos.append(a / base)

    return st.mean(por_minuto), st.pstdev(por_minuto), st.mean(posses), st.mean(retidos)


def _veredito(retido: float, falsos: float, orcamento: float) -> str:
    """Silêncio vem primeiro: com o sistema mudo, zero falso alarme não é
    aprovação, é ausência de qualquer coisa para aprovar."""
    if retido < 0.5:
        return "MUDO: perde metade dos eventos"
    if falsos > orcamento:
        return "ESTOURA o orcamento"
    return "opera"


def _varre(varredura, plane, config, base: int, orcamento: float) -> None:
    titulo, eixo, campo, valores, formato = varredura

    print(f"### {titulo}   (o outro eixo limpo)")
    print(CABECALHO)
    print("-" * len(CABECALHO))

    for valor in valores:
        modelo = replace(LIMPO, **{campo: valor, "drop_burst_frames": RAJADA_QUADROS})
        person = modelo if eixo == "person" else LIMPO
        bag = modelo if eixo == "bag" else LIMPO

        media, desvio, posses, retido = _celula(person, bag, plane, config, base)

        print(
            f"{formato.format(valor):>8}  {media:>8.2f} +- {desvio:<7.2f}"
            f"{posses:>9.1f}{retido:>9.0%}  {_veredito(retido, media, orcamento)}"
        )
        sys.stdout.flush()

    print()


def main() -> int:
    if not DATA.exists():
        print(f"dataset ausente em {DATA}", file=sys.stderr)
        print("rode primeiro: uv run python scripts/download_caviar.py", file=sys.stderr)
        return 1

    config = load_config(CONFIG)
    plane = ground_plane(estimate_metres_per_pixel(DATA))
    orcamento = config.alerts.operator_hourly_budget / 60.0

    _, posses_base, abandonos_base, _ = _uma_passada(LIMPO, LIMPO, 1, plane, config)

    print(f"limiar de desacompanhamento: {config.custody.unattended_time_s}s")
    print(f"orcamento do operador: {config.alerts.operator_hourly_budget}/h = {orcamento:.2f}/min")
    print(f"rajada de falha: {RAJADA_QUADROS} quadros   |   {len(SEMENTES)} sementes por celula")
    print(f"execucao limpa: {posses_base} posses, {abandonos_base} abandonos")
    print()
    print("Os quatro clipes do CAVIAR contem ZERO furtos: todo furto abaixo e falso.")
    print("`retidos` e a fracao dos abandonos da execucao limpa que sobrevive -- e a")
    print("coluna que decide, porque sob ruido o sistema fica mudo, nao grita lobo.")
    print()
    sys.stdout.flush()

    for varredura in VARREDURAS:
        _varre(varredura, plane, config, abandonos_base, orcamento)

    print("Isto mede degradacao da LOGICA sob ruido sintetico, com movimento humano")
    print("real por baixo. Nao mede percepcao, e nao substitui a gravacao encenada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
