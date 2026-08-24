"""P_miss @ RFA — métrica padrão do NIST ActEV para esta família de problema.

mAP não serve aqui. O que importa é: quantos eventos reais o sistema perde,
dado um orçamento de falsos alarmes por minuto que o operador aguenta.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoredEvent:
    score: float
    is_true_event: bool


@dataclass(frozen=True)
class OperatingPoint:
    threshold: float
    p_miss: float
    false_alarms: int
    rfa: float  # falsos alarmes por minuto


def p_miss_at_rfa(
    events: Sequence[ScoredEvent],
    video_minutes: float,
    target_rfa: float,
    total_positives: int | None = None,
) -> OperatingPoint:
    """Menor P_miss alcançável sem estourar `target_rfa` falsos alarmes/minuto.

    Varre os limiares candidatos do mais restritivo ao mais permissivo e
    devolve o ponto de operação mais barato que atinge o melhor P_miss.

    `total_positives` existe porque derivar a contagem da própria lista ignora
    o positivo que o sistema **nunca detectou** — ele não está lá para ser
    contado, e o P_miss sai subestimado. Quanto pior o sistema, mais otimista
    o número.

    Quando `None`, deriva como antes: é o caminho das listas sintéticas, onde
    todo positivo está presente por construção. O caminho de ground truth passa
    a contagem real, vinda do arquivo de anotação.
    """
    if video_minutes <= 0:
        raise ValueError("video_minutes deve ser positivo")

    detectados = sum(1 for e in events if e.is_true_event)

    if total_positives is None:
        total_true = detectados
    elif total_positives < detectados:
        raise ValueError(
            f"total_positives ({total_positives}) menor que os {detectados} "
            f"positivos presentes na lista"
        )
    else:
        total_true = total_positives

    if total_true == 0:
        raise ValueError("nenhum evento verdadeiro no ground truth")

    budget = target_rfa * video_minutes
    ordered = sorted(events, key=lambda e: e.score, reverse=True)

    best = OperatingPoint(threshold=float("inf"), p_miss=1.0, false_alarms=0, rfa=0.0)
    hits = 0
    false_alarms = 0

    for event in ordered:
        if event.is_true_event:
            hits += 1
        else:
            false_alarms += 1

        if false_alarms > budget:
            break

        p_miss = 1.0 - hits / total_true
        # Só avança quando o P_miss MELHORA de fato. Sem esta guarda, um falso
        # alarme de score baixo depois do último acerto substituiria um ponto
        # de operação igualmente bom por um mais caro.
        if p_miss < best.p_miss:
            best = OperatingPoint(
                threshold=event.score,
                p_miss=p_miss,
                false_alarms=false_alarms,
                rfa=false_alarms / video_minutes,
            )

    return best
