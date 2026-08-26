"""Degradação de detecção, para medir o envelope de operação da lógica.

Três limiares em produção — `carry_confirm_s`, `max_occlusion_s` e
`missing_frames_before_occluded` — têm faixa declarada mas valor escolhido por
julgamento. Esta camada existe para trocar julgamento por medição: ela degrada
um iterador de detecções limpo e a varredura observa a que ponto a lógica
deixa de operar dentro do orçamento do operador.

**O ruído é injetado em pixel, antes da projeção.** É onde o detector erra de
verdade, e deixa a homografia carregar o erro adiante: nove pixels de tremor no
fundo da cena valem vários metros, na frente valem centímetros. Injetar em
metros distribuiria o erro uniformemente e perderia isso.

O movimento não é simulado. Ele vem do ground truth do CAVIAR — pessoas reais,
gravadas, com timing real. O que este módulo acrescenta é degradação.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass, replace

from .tracking import TrackedDetection
from .types import BAG_CLASSES


@dataclass(frozen=True)
class NoiseModel:
    """O que a percepção erra, nos três eixos que a custódia sente."""

    drop_rate: float = 0.0
    """Probabilidade, por quadro e por track, de a detecção começar a falhar."""

    drop_burst_frames: int = 1
    """Duração da falha, em quadros.

    Detector não pisca um quadro de cada vez: ele perde o objeto enquanto ele
    está pequeno ou ocluído, e recupera depois. Falha independente por quadro
    produziria buracos de 40ms que a lógica de custódia nem sente.
    """

    position_sigma_px: float = 0.0
    """Desvio do erro de posição da caixa, em pixel. Independente por quadro —
    é essa independência que produz o salto de um quadro só, o caso que já
    quebrou `carry_away` uma vez."""

    id_switch_rate: float = 0.0
    """Probabilidade, por quadro e por track, de o tracker reatribuir o id.
    A troca persiste: tracker que troca não volta atrás no quadro seguinte."""


@dataclass
class _Track:
    """Estado de um track sob degradação. Vive só durante a passagem."""

    ausente: int = 0
    id_atual: int | None = None


def degrade(
    frames: Iterator[tuple[float, list[TrackedDetection]]],
    *,
    person: NoiseModel,
    bag: NoiseModel,
    seed: int,
) -> Iterator[tuple[float, list[TrackedDetection]]]:
    """Degrada um iterador de detecções, mantendo a interface do `run_session`.

    Pessoa e bagagem têm modelos separados de propósito: a varredura isola um
    eixo por vez, e um modelo só faria os dois se moverem juntos.
    """
    rng = random.Random(seed)
    estado: dict[int, _Track] = {}
    proximo_id = 10_000

    for t, deteccoes in frames:
        saida: list[TrackedDetection] = []

        for deteccao in deteccoes:
            modelo = bag if deteccao.cls in BAG_CLASSES else person
            track = estado.setdefault(deteccao.track_id, _Track())

            if track.ausente > 0:
                track.ausente -= 1
                continue

            if modelo.drop_rate > 0.0 and rng.random() < modelo.drop_rate:
                # A rajada inclui o quadro do gatilho: o contador guarda só
                # os quadros que faltam depois dele, senão uma rajada de 1
                # quadro apagaria 2.
                track.ausente = max(1, modelo.drop_burst_frames) - 1
                continue

            if (
                track.id_atual is None
                and modelo.id_switch_rate > 0.0
                and rng.random() < modelo.id_switch_rate
            ):
                proximo_id += 1
                track.id_atual = proximo_id

            caixa = deteccao.bbox
            if modelo.position_sigma_px > 0.0:
                dx = rng.gauss(0.0, modelo.position_sigma_px)
                dy = rng.gauss(0.0, modelo.position_sigma_px)
                caixa = (caixa[0] + dx, caixa[1] + dy, caixa[2] + dx, caixa[3] + dy)

            saida.append(
                replace(
                    deteccao,
                    track_id=track.id_atual if track.id_atual is not None else deteccao.track_id,
                    bbox=caixa,
                )
            )

        yield t, saida


__all__ = ["NoiseModel", "degrade"]
