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

from collections.abc import Iterator
from dataclasses import dataclass, replace

import numpy as np

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


class _Degrader:
    """Uma passagem de degradação: o gerador, e os tracks vistos até agora.

    A ordem das chamadas ao gerador é parte do contrato — falha, depois id,
    depois posição. Trocar a ordem muda toda saída semeada, e com ela a
    calibração medida de `CAVIAR_PERSON_NOISE`.

    Usa o gerador do numpy e não o do módulo `random`. Não é preferência: é o
    gerador idiomático para ruído numérico, numpy já é dependência do projeto,
    e o `random` da biblioteca padrão é sinalizado por analisadores estáticos
    como inseguro — sinalização correta em contexto de segurança e sem sentido
    aqui, onde o gerador existe justamente para ser **reprodutível**.
    """

    def __init__(self, person: NoiseModel, bag: NoiseModel, seed: int) -> None:
        self._rng = np.random.default_rng(seed)
        self._person = person
        self._bag = bag
        self._tracks: dict[int, _Track] = {}
        self._proximo_id = 10_000

    def _falhou(self, track: _Track, modelo: NoiseModel) -> bool:
        """A detecção some neste quadro?"""
        if track.ausente > 0:
            track.ausente -= 1
            return True

        if modelo.drop_rate > 0.0 and self._rng.random() < modelo.drop_rate:
            # A rajada inclui o quadro do gatilho: o contador guarda só os
            # quadros que faltam depois dele, senão uma rajada de 1 apagaria 2.
            track.ausente = max(1, modelo.drop_burst_frames) - 1
            return True

        return False

    def _identidade(self, track: _Track, modelo: NoiseModel, original: int) -> int:
        """O id que o tracker reporta. Uma troca persiste até o fim."""
        if (
            track.id_atual is None
            and modelo.id_switch_rate > 0.0
            and self._rng.random() < modelo.id_switch_rate
        ):
            self._proximo_id += 1
            track.id_atual = self._proximo_id

        return track.id_atual if track.id_atual is not None else original

    def _posicao(
        self, modelo: NoiseModel, caixa: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """Translada a caixa inteira. Mexer numa borda só mudaria o tamanho,
        e é o tamanho que o `ground_plane` usa para achar o pé."""
        if modelo.position_sigma_px <= 0.0:
            return caixa

        dx = self._rng.normal(0.0, modelo.position_sigma_px)
        dy = self._rng.normal(0.0, modelo.position_sigma_px)
        return (caixa[0] + dx, caixa[1] + dy, caixa[2] + dx, caixa[3] + dy)

    def uma(self, deteccao: TrackedDetection) -> TrackedDetection | None:
        """Degrada uma detecção. `None` quando ela some neste quadro."""
        modelo = self._bag if deteccao.cls in BAG_CLASSES else self._person
        track = self._tracks.setdefault(deteccao.track_id, _Track())

        if self._falhou(track, modelo):
            return None

        return replace(
            deteccao,
            track_id=self._identidade(track, modelo, deteccao.track_id),
            bbox=self._posicao(modelo, deteccao.bbox),
        )


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
    degradador = _Degrader(person, bag, seed)

    for t, deteccoes in frames:
        sujas = (degradador.uma(deteccao) for deteccao in deteccoes)
        yield t, [suja for suja in sujas if suja is not None]


CAVIAR_PERSON_NOISE = NoiseModel(
    drop_rate=0.16,
    drop_burst_frames=12,
    position_sigma_px=2.32,
    id_switch_rate=0.016,
)
"""Ruído de pessoa medido no CAVIAR por `scripts/measure_noise.py`.

Medido: 1.677 de 5.493 pessoas anotadas foram pareadas com IoU ≥ 0,5, o que
dá 69,5% de quadros perdidos em 326 rajadas de 11,7 quadros em média. `0.16`
é a taxa de **entrada** em falha que produz esses 69,5% em estado
estacionário, resolvendo `(1-r)/(1-r+r*B) = 0,305` com `B = 12`. Não é a
fração perdida: pôr 0,695 aqui daria quase 96% de perda.

A rajada tem comprimento **fixo**, e isso é simplificação declarada. A
distribuição real é pesada na cauda — mediana de 2 quadros, p95 de 56, máxima
de 517 — e uma rajada fixa de 12 não produz nem as curtas nem as muito longas.
Para a lógica isso sobre-estima a frequência com que a oclusão é acionada:
toda rajada cruza os 5 quadros de `missing_frames_before_occluded`, quando na
realidade metade delas não cruzaria.

`position_sigma_px` usa o desvio de `dy` (2,32px) e não o de `dx` (1,45px),
que é o conservador e também o certo: `ground_plane.foot_point` projeta a
**base** da caixa, então é a borda de baixo que decide a posição no chão — e é
justamente a mais instável das duas.

Existe porque a varredura precisa de um eixo fixo: o interesse é a bagagem, e
mexer nos dois ao mesmo tempo produziria uma tabela em que nenhuma célula
significa alguma coisa.

Para bagagem não há constante equivalente, e a ausência é o desenho: uma
detecção pareada em 1.686 não estima nada. A bagagem é a variável varrida.
"""

__all__ = ["CAVIAR_PERSON_NOISE", "NoiseModel", "degrade"]
