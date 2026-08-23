"""Religação de tracks fragmentados por aparência efêmera.

## O problema medido

Rodando o orquestrador nos clipes do CAVIAR, dois terços das retiradas
legítimas de bagagem viraram acusação de furto. A causa não é limiar mal
ajustado: o dono deixa a mala, sai de cena, o track morre, e cinco segundos
depois ele volta com um id novo. A camada de lógica recebe dois tracks e não
tem nenhuma informação que os ligue, então trata o dono como estranho.

## O que este módulo NÃO é

Não é reconhecimento facial, que está permanentemente fora de escopo. Não
identifica pessoas, não consulta banco de dados, e não produz nada que
sobreviva ao processamento de um vídeo. A assinatura é um histograma de cor
de 48 dimensões, descartada ao fim da sessão, e serve só para responder "este
track é a continuação daquele?" dentro do mesmo clipe.

## Por que histograma de cor e não embedding profundo

A caixa de pessoa no CAVIAR tem cerca de 14x32 pixels. OSNet espera 256x128 —
ampliar oito vezes entrega interpolação, não informação. Histograma de cor por
metades (torso e pernas) é o que a literatura de re-ID usava antes de haver
resolução para redes profundas, e é o que esses 448 pixels comportam. Em
material de resolução moderna, troque por um embedding aprendido.

## As três guardas, e por que nenhuma basta sozinha

Medido em `LeftBag`, com perfis médios de cinco tracks:

| par | similaridade | temporal | |
|---|---|---|---|
| id3 x id5 | 0,990 | disjuntos | mesma pessoa, deve ligar |
| id1 x id2 | **0,992** | coexistem em 517 frames | pessoas distintas, não pode ligar |

O par de similaridade **mais alta** é justamente o que não pode ser ligado.
Aparência sozinha erraria. Daí:

1. **Disjunção temporal.** Só é candidato o track que terminou antes deste
   começar. Dois tracks simultâneos são duas pessoas, por definição.
2. **Margem, não valor absoluto.** Todas as similaridades ficam acima de 0,87,
   porque o fundo da cena domina o histograma em crops pequenos. O que
   discrimina é a distância para o segundo colocado.
3. **Plausibilidade física.** Reaparecer a trinta metros do ponto de saída
   meio segundo depois não é a mesma pessoa.

## A assimetria de erro que define os limiares

Não ligar quando deveria custa um alarme falso, que o operador descarta em
dez segundos. Ligar quando não deveria faz um ladrão herdar o grupo da vítima,
e o furto real **fica silencioso** — o sistema falha na única coisa que
deveria fazer.

Os dois erros não são simétricos, então os limiares são deliberadamente
conservadores. Na dúvida, não liga: é a regra P3 do projeto aplicada aqui.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from .config import ReidConfig
from .types import Point

HUE_BINS = 12
SAT_BINS = 6
VAL_BINS = 6
BODY_SPLITS = 2
SIGNATURE_SIZE = BODY_SPLITS * (HUE_BINS + SAT_BINS + VAL_BINS)

MIN_CROP_PIXELS = 24


@dataclass(frozen=True)
class Appearance:
    """Assinatura de aparência com escopo de sessão.

    Não é biometria: não identifica uma pessoa, só permite perguntar se dois
    tracks do mesmo vídeo são a continuação um do outro. Descartada ao fim do
    processamento.
    """

    signature: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.signature) != SIGNATURE_SIZE:
            raise ValueError(
                f"assinatura deve ter {SIGNATURE_SIZE} dimensões, tem {len(self.signature)}"
            )


def describe(crop: np.ndarray) -> Appearance | None:
    """Histograma HSV das metades superior e inferior do recorte.

    Separar torso de pernas é o que dá poder discriminativo em resolução
    baixa: uma pessoa de camiseta clara e calça escura tem perfil diferente de
    outra de camiseta escura e calça clara, mesmo com o mesmo histograma
    global.

    Devolve `None` para recortes pequenos demais para carregar informação.
    """
    if crop.ndim != 3 or crop.size < MIN_CROP_PIXELS * 3:
        return None

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    partes: list[np.ndarray] = []

    for metade in np.array_split(hsv, BODY_SPLITS, axis=0):
        if metade.size == 0:
            return None
        for canal, bins, topo in ((0, HUE_BINS, 180), (1, SAT_BINS, 256), (2, VAL_BINS, 256)):
            contagem, _ = np.histogram(metade[:, :, canal], bins=bins, range=(0, topo))
            partes.append(contagem / max(contagem.sum(), 1))

    return Appearance(signature=tuple(float(v) for v in np.concatenate(partes)))


def similarity(a: Appearance, b: Appearance) -> float:
    """Cosseno entre assinaturas. Sempre em [0, 1] — histogramas não têm sinal."""
    va = np.asarray(a.signature)
    vb = np.asarray(b.signature)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def average(appearances: Sequence[Appearance]) -> Appearance:
    """Perfil médio de um track. Um frame isolado é ruidoso demais."""
    if not appearances:
        raise ValueError("não há aparências para promediar")
    media = np.mean([a.signature for a in appearances], axis=0)
    return Appearance(signature=tuple(float(v) for v in media))


@dataclass
class TrackWindow:
    """Janela de vida de um track e o que se sabe da aparência dele."""

    track_id: int
    first_t: float
    last_t: float
    last_position: Point
    samples: list[Appearance]

    def profile(self) -> Appearance | None:
        return average(self.samples) if self.samples else None


@dataclass(frozen=True)
class Resolution:
    """O que o religador concluiu sobre um track.

    `settled` é falso enquanto não há amostras suficientes para um perfil
    confiável. Antes disso o track existe, mas não deve receber posse de
    bagagem — um perfil de um frame só é ruído.
    """

    canonical_id: int
    settled: bool
    linked_from: int | None = None
    similarity: float | None = None
    margin: float | None = None


class TrackLinker:
    """Liga tracks fragmentados dentro de uma sessão.

    Todo o estado é descartado quando o objeto sai de escopo. Nada persiste
    entre vídeos, e nada identifica pessoa alguma fora deste clipe.
    """

    def __init__(self, config: ReidConfig | None = None) -> None:

        self._config = config or ReidConfig()
        self._windows: dict[int, TrackWindow] = {}
        self._canonical: dict[int, int] = {}
        self._pending: dict[int, list[Appearance]] = {}
        self._pending_since: dict[int, float] = {}

    def observe(
        self,
        track_id: int,
        t: float,
        position: Point,
        appearance: Appearance | None,
    ) -> Resolution:
        """Registra uma observação e devolve o id canônico do track."""
        known = self._canonical.get(track_id)
        if known is not None:
            window = self._windows[known]
            window.last_t = t
            window.last_position = position
            if appearance is not None and len(window.samples) < self._config.max_samples:
                window.samples.append(appearance)
            return Resolution(canonical_id=known, settled=True)

        if appearance is None:
            # Sem assinatura nunca haverá religação, então esperar não compra
            # nada: o track vira canônico de si mesmo e segue. Degrada para o
            # comportamento anterior ao re-ID, que é o lado seguro do erro.
            self._canonical[track_id] = track_id
            self._windows[track_id] = TrackWindow(
                track_id=track_id,
                first_t=t,
                last_t=t,
                last_position=position,
                samples=[],
            )
            return Resolution(canonical_id=track_id, settled=True)

        amostras = self._pending.setdefault(track_id, [])
        self._pending_since.setdefault(track_id, t)
        amostras.append(appearance)
        if len(amostras) < self._config.min_samples:
            return Resolution(canonical_id=track_id, settled=False)

        perfil = average(amostras)
        nasceu_em = self._pending_since.pop(track_id)
        alvo, score, margem = self._match(track_id, nasceu_em, t, position, perfil)
        del self._pending[track_id]

        if alvo is None:
            self._canonical[track_id] = track_id
            self._windows[track_id] = TrackWindow(
                track_id=track_id,
                first_t=t,
                last_t=t,
                last_position=position,
                samples=amostras,
            )
            return Resolution(canonical_id=track_id, settled=True)

        self._canonical[track_id] = alvo
        window = self._windows[alvo]
        window.last_t = t
        window.last_position = position
        window.samples.extend(amostras[: self._config.max_samples - len(window.samples)])
        return Resolution(
            canonical_id=alvo,
            settled=True,
            linked_from=alvo,
            similarity=score,
            margin=margem,
        )

    def _match(
        self,
        track_id: int,
        born_t: float,
        t: float,
        position: Point,
        perfil: Appearance,
    ) -> tuple[int | None, float, float]:
        """As três guardas. Devolve (alvo, similaridade, margem).

        A disjunção é medida contra `born_t`, o instante em que o track novo
        apareceu, e não contra `t`, em que ele terminou de acumular perfil.
        Usar `t` deixaria passar um candidato que esteve em cena durante os
        primeiros frames do track novo — dois tracks vivos ao mesmo tempo, que
        é justamente o caso impossível de religar.
        """
        candidatos: list[TrackWindow] = []
        for window in self._windows.values():
            if window.track_id == track_id:
                continue

            gap = born_t - window.last_t
            # Guarda 1: tracks simultâneos são duas pessoas, por definição.
            if gap <= 0.0 or gap > self._config.max_gap_s:
                continue

            # Guarda 3: reaparecer longe demais rápido demais não é a mesma pessoa.
            alcance = self._config.max_speed_ms * gap + self._config.position_slack_m
            if window.last_position.distance_to(position) > alcance:
                continue

            candidatos.append(window)

        if not candidatos:
            return None, 0.0, 0.0

        perfis = [(w.profile(), w.track_id) for w in candidatos]
        scores = sorted(
            ((similarity(perfil, p), tid) for p, tid in perfis if p is not None),
            key=lambda par: par[0],
            reverse=True,
        )
        if not scores:
            return None, 0.0, 0.0
        melhor, alvo = scores[0]
        segundo = scores[1][0] if len(scores) > 1 else 0.0
        margem = melhor - segundo

        if melhor < self._config.min_similarity:
            return None, melhor, margem

        # Guarda 2: o valor absoluto não discrimina porque o fundo da cena
        # domina o histograma. O que discrimina é a distância para o segundo.
        # Empate significa que não se sabe, e não saber suprime a religação.
        if len(scores) > 1 and margem < self._config.min_margin:
            return None, melhor, margem

        return alvo, melhor, margem

    def canonical(self, track_id: int) -> int:
        return self._canonical.get(track_id, track_id)

    def links(self) -> dict[int, int]:
        """Mapa de id bruto para canônico, só onde houve religação."""
        return {raw: canon for raw, canon in self._canonical.items() if raw != canon}
