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
COR_SUSPEITO = (255, 64, 64)
COR_BAGAGEM = (255, 196, 0)
COR_NEUTRA = (120, 200, 255)


@dataclass(frozen=True)
class ClipRequest:
    start_s: float
    end_s: float
    person_ids: frozenset[int]
    bag_id: int | None
    output: Path


def _desenhar(
    imagem: Image.Image,
    boxes: Iterable[TrackedDetection],
    person_ids: frozenset[int],
    bag_id: int | None,
    escala: int,
) -> Image.Image:
    tela = imagem.resize((imagem.width * escala, imagem.height * escala), Image.NEAREST)
    caneta = ImageDraw.Draw(tela)

    for box in boxes:
        x0, y0, x1, y1 = (v * escala for v in box.bbox)

        if box.track_id in person_ids:
            cor, rotulo, espessura = COR_SUSPEITO, "pessoa sinalizada", 3
        elif bag_id is not None and box.track_id == bag_id:
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
        recortados.append(_desenhar(rgb, boxes, request.person_ids, request.bag_id, scale))

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
