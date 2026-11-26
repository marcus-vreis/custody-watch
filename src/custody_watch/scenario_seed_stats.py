"""Agregação do benchmark de cenários sobre seeds: taxa média de acerto e IC.

Uma célula é (variante, princípio, fps, nível de ruído). Dentro dela, cada
seed dá uma taxa de acerto -- corretas / execuções dos cenários daquele
princípio naquela taxa de quadro --, e o intervalo de confiança é calculado
sobre essas taxas por seed, não sobre as execuções soltas: as execuções de uma
mesma seed compartilham o gerador e não são independentes entre si.

**Método**: t de Student bilateral sobre as taxas por seed (gl = n - 1),
limitado a [0, 1]. Com poucas seeds o bootstrap percentil subestima a largura
(com 5 seeds ele só reamostra 5 valores), e o t é a escolha conservadora usual
para média de poucas réplicas. Limites ficam registrados em `CI_LIMITS` e vão
para o JSON junto com o resultado.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from scipy import stats

CI_LEVEL = 0.95
CI_METHOD = (
    "t de Student bilateral 95% sobre a taxa de acerto por seed "
    "(média ± t[0,975; n-1] · s/√n), limitado a [0, 1]"
)
CI_LIMITS = (
    "n = 1 seed (inclusive o caso sem ruído, determinístico): sem IC",
    "todas as taxas iguais: IC degenerado [média, média]; quer dizer nenhuma variação "
    "observada entre as seeds, não certeza",
    "a taxa por seed é discreta (k de poucos cenários) e o t supõe normalidade da média; "
    "com poucas seeds o IC é aproximado",
    "a variação coberta é só a do ruído semeado sobre um conjunto FIXO de cenários; "
    "não generaliza para outros cenários",
    "o corte em [0, 1] é truncamento, não correção de cobertura",
)


@dataclass(frozen=True)
class SeedInterval:
    """Média das taxas por seed e IC; `None` nos limites quando n = 1."""

    n_seeds: int
    media: float
    ic_inf: float | None
    ic_sup: float | None


def seed_interval(rates: Sequence[float], level: float = CI_LEVEL) -> SeedInterval:
    """IC t de Student da média de `rates` (uma taxa em [0, 1] por seed)."""
    if not rates:
        raise ValueError("sem taxas: esperado ao menos uma seed")
    for rate in rates:
        if not (math.isfinite(rate) and 0.0 <= rate <= 1.0):
            raise ValueError(f"taxa fora de [0, 1]: {rate!r}")
    if not 0.0 < level < 1.0:
        raise ValueError(f"nível do IC deve estar em (0, 1), recebido {level!r}")

    n = len(rates)
    mean = math.fsum(rates) / n
    if n == 1:
        return SeedInterval(1, mean, None, None)

    sd = math.sqrt(math.fsum((r - mean) ** 2 for r in rates) / (n - 1))
    half = float(stats.t.ppf(0.5 + level / 2, n - 1)) * sd / math.sqrt(n)
    return SeedInterval(n, mean, max(0.0, mean - half), min(1.0, mean + half))


@dataclass(frozen=True)
class RunOutcome:
    """Uma execução pontuada: quem, em que célula, com que seed, acertou ou não."""

    variante: str
    principio: str
    fps: float
    ruido: str
    seed: int | None
    correta: bool


CellKey = tuple[str, str, float, str]


def _per_seed(outcomes: Iterable[RunOutcome]) -> dict[CellKey, dict[int | None, list[bool]]]:
    cells: dict[CellKey, dict[int | None, list[bool]]] = {}
    for o in outcomes:
        key = (o.variante, o.principio, o.fps, o.ruido)
        cells.setdefault(key, {}).setdefault(o.seed, []).append(o.correta)
    return cells


def aggregate_over_seeds(outcomes: Iterable[RunOutcome]) -> list[dict[str, Any]]:
    """Uma linha por célula, ordenada, com taxas por seed, média e IC."""
    rows = []
    for key, by_seed in sorted(_per_seed(outcomes).items()):
        if None in by_seed and len(by_seed) > 1:
            raise ValueError(f"célula {key} mistura execução sem seed com seeds")
        seeds = sorted(by_seed, key=lambda s: -1 if s is None else s)
        rates = [sum(by_seed[s]) / len(by_seed[s]) for s in seeds]
        interval = seed_interval(rates)
        variante, principio, fps, ruido = key
        rows.append(
            {
                "variante": variante,
                "principio": principio,
                "fps": fps,
                "ruido": ruido,
                "seeds": seeds,
                "execucoes_por_seed": [len(by_seed[s]) for s in seeds],
                "corretas_por_seed": [sum(by_seed[s]) for s in seeds],
                "taxa_por_seed": rates,
                "n_seeds": interval.n_seeds,
                "media": interval.media,
                "ic_inf": interval.ic_inf,
                "ic_sup": interval.ic_sup,
            }
        )
    return rows


def ci_description() -> dict[str, Any]:
    """Metadado do método, para o JSON de saída."""
    return {"metodo": CI_METHOD, "nivel": CI_LEVEL, "unidade": "seed", "limites": list(CI_LIMITS)}


__all__ = [
    "CI_LEVEL",
    "CI_LIMITS",
    "CI_METHOD",
    "RunOutcome",
    "SeedInterval",
    "aggregate_over_seeds",
    "ci_description",
    "seed_interval",
]
