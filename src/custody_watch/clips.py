"""Recorte de clipe anotado para revisão humana.

`AlertItem` carrega `clip_start` e `clip_end` desde o v1, mas eram apenas
números — nenhum vídeo era recortado, e sem vídeo o operador não tem o que
revisar. Este módulo fecha essa lacuna.

## Por que anotado, e não o recorte cru

Um clipe de vinte segundos de um saguão cheio, sem indicar **qual** pessoa e
**qual** bagagem, obriga o operador a procurar o evento antes de julgá-lo. Isso
destrói a economia que justifica o sistema: o cálculo de viabilidade assume
revisão em torno de trinta segundos por item, e procurar sozinho já consome
mais que isso.

As caixas destacadas não são enfeite — são o que transforma "assista a este
trecho" em "olhe para isto".

## Por que GIF

MP4 depende de codec instalado, e `cv2.VideoWriter` falha de formas diferentes
em cada máquina. GIF é autocontido, embute em HTML como data URI sem host
externo, e a resolução do CAVIAR (384x288) torna o custo aceitável.

A taxa de saída e a paleta são reduzidas de propósito. O operador precisa ver o que
aconteceu, não cada quadro: vinte segundos a 25 fps são quinhentos quadros, e a
2 fps com 32 cores o clipe cai de 21 MB para 1,7 MB, que é o que permite
várias revisões na mesma página.

## Por que existe um teto de largura

Aquele 1,7 MB foi medido a 384x288. O custo de um GIF é por pixel, então o
mesmo recorte a 1280x720 custa oito vezes mais: medido no primeiro vídeo real,
treze itens fizeram uma página de 142 MB, alto demais para abrir e muito além
do teto de 16 MB de um artifact. O teto reduz o quadro antes de desenhar, e a
caixa destacada acompanha — uma pessoa de 290px no quadro original ainda tem
109px, que é de sobra para reconhecer o que aconteceu.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .tracking import TrackedDetection

DEFAULT_OUTPUT_FPS = 2.0
DEFAULT_SCALE = 1
DEFAULT_COLORS = 32
DEFAULT_MAX_WIDTH = 480
"""Largura máxima do clipe, em pixels. Material do tamanho do CAVIAR passa
intacto; um quadro de câmera de verdade é reduzido antes de desenhar."""
COR_SUSPEITO = (255, 64, 64)
COR_BAGAGEM = (255, 196, 0)
COR_NEUTRA = (120, 200, 255)


@dataclass(frozen=True)
class ClipRequest:
    start_s: float
    end_s: float
    person_ids: frozenset[int]
    bag_ids: frozenset[int]
    """Todo track bruto que pode responder pela bagagem sinalizada dentro da
    janela do clipe -- o canônico e qualquer um religado a ele por
    `adopt_occluded`. Uma readoção sob oclusão faz o vídeo trazer um
    `track_id` diferente do `bag_id` citado no evento; um único inteiro aqui
    perderia a caixa assim que a janela cruzasse uma readoção."""
    output: Path


def _fator(largura: int, escala: int, max_width: int) -> float:
    """Quanto o quadro muda de tamanho. O teto vence a ampliação.

    `escala` existe para material pequeno demais para se enxergar; o teto,
    para material grande demais para caber na página. Um só fator porque a
    caixa destacada precisa acompanhar o quadro, e dois números aqui seriam
    duas chances de ela ficar para trás.
    """
    if largura * escala <= max_width:
        return float(escala)
    return max_width / largura


def _desenhar(
    imagem: Image.Image,
    boxes: Iterable[TrackedDetection],
    person_ids: frozenset[int],
    bag_ids: frozenset[int],
    fator: float,
) -> Image.Image:
    # NEAREST ampliando mantém o pixel nítido; reduzindo, ele joga linha fora,
    # e uma bagagem estreita pode simplesmente sumir do clipe.
    filtro = Image.NEAREST if fator >= 1.0 else Image.LANCZOS
    tela = imagem.resize((round(imagem.width * fator), round(imagem.height * fator)), filtro)
    caneta = ImageDraw.Draw(tela)

    for box in boxes:
        x0, y0, x1, y1 = (v * fator for v in box.bbox)

        if box.track_id in person_ids:
            cor, rotulo, espessura = COR_SUSPEITO, "pessoa sinalizada", 3
        elif box.track_id in bag_ids:
            cor, rotulo, espessura = COR_BAGAGEM, "bagagem", 3
        else:
            cor, rotulo, espessura = COR_NEUTRA, "", 1

        caneta.rectangle([x0, y0, x1, y1], outline=cor, width=espessura)
        if rotulo:
            caneta.text((x0, max(0.0, y0 - 11)), rotulo, fill=cor)

    return tela


def render_clip(
    frames: Iterator[tuple[float, np.ndarray, list[TrackedDetection]]],
    request: ClipRequest,
    output_fps: float = DEFAULT_OUTPUT_FPS,
    scale: int = DEFAULT_SCALE,
    colors: int = DEFAULT_COLORS,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> Path | None:
    """Recorta a janela pedida e grava um GIF anotado.

    `frames` traz imagens em BGR, como o OpenCV entrega. Devolve `None` quando
    a janela não contém quadro nenhum — janela vazia não é erro, é um alerta
    perto do fim do vídeo.
    """
    if request.end_s < request.start_s:
        raise ValueError(f"janela invertida: {request.start_s} a {request.end_s}")

    passo = 1.0 / output_fps
    proximo = request.start_s
    recortados: list[Image.Image] = []

    for t, imagem, boxes in frames:
        if t < request.start_s:
            continue
        if t > request.end_s:
            break
        if t + 1e-9 < proximo:
            continue

        proximo = t + passo
        rgb = Image.fromarray(imagem[:, :, ::-1])
        fator = _fator(rgb.width, scale, max_width)
        recortados.append(_desenhar(rgb, boxes, request.person_ids, request.bag_ids, fator))

    if not recortados:
        return None

    # A página do operador embute os clipes como data URI e tem teto de
    # tamanho. Sem quantizar, vinte segundos de saguão passam de 20 MB e uma
    # única revisão estoura a página inteira.
    paleta = [q.quantize(colors=colors, method=Image.MEDIANCUT) for q in recortados]

    request.output.parent.mkdir(parents=True, exist_ok=True)
    paleta[0].save(
        request.output,
        save_all=True,
        append_images=paleta[1:],
        duration=int(1000 / output_fps),
        loop=0,
        optimize=True,
    )
    return request.output
