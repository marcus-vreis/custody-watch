"""Calibração de câmera em arquivo, com resíduo de reprojeção.

O PETS2007 distribuía calibração e o CAVIAR não — foi por isso que caímos numa
escala global com cerca de 30% de erro. Material gravado por nós não precisa
disso: quatro pontos medidos com fita métrica no chão resolvem a homografia.

**O resíduo é obrigatório.** Medição feita às pressas produz homografia errada
em silêncio, e aí todo limiar em metros passa a mentir junto, sem sintoma.
Acima de `MAX_RESIDUAL_M` o carregador recusa.

O limite de 0,25m vem de comparação com o que já existe: a escala global do
CAVIAR carrega ~30% de erro, o que num cenário de oito metros passa de dois
metros. Uma calibração pior que 0,25m não compra nada sobre isso, então
recusar é mais honesto que aceitar e fingir precisão.

A recusa olha o **pior** ponto, não a média. Média dilui: com doze pontos, um
canto medido 2m fora dá média de 17cm e passa no limite — que é exatamente o
erro silencioso que este módulo existe para pegar. "Nenhum ponto medido erra
mais de 25cm" é um contrato mais forte, e não precisa de uma segunda constante
arbitrária para valer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .ground_plane import GroundPlane
from .types import Point

MAX_RESIDUAL_M = 0.25

MAX_UNIFORM_SCALE_RATIO = 2.0
"""Acima disto a escala uniforme não descreve mais a cena.

A razão entre a altura de uma pessoa no primeiro plano e no fundo **é** o
fator de erro da escala entre os dois planos. Colocada no meio, uma escala
uniforme erra pela raiz dessa razão para cada lado: a 2.0 já são 41% em cada
direção, e nenhum limiar em metros sobrevive a isso sem dizer outra coisa do
que promete.

Medido: o CAVIAR dá 3.27, e o vídeo de portão de embarque dá 11.28. Nenhuma
das duas cenas que este projeto já viu é descritível por escala uniforme, e é
por isso que o aviso existe em vez de um número que finge funcionar.
"""


@dataclass(frozen=True)
class Calibration:
    camera: str
    note: str
    plane: GroundPlane
    residual_m: float
    """Erro médio. Serve para relatar a qualidade da medição."""
    worst_residual_m: float
    """Erro do pior ponto. É por ele que a calibração é aceita ou recusada."""


def reprojection_errors(
    plane: GroundPlane,
    pixels: Sequence[tuple[float, float]],
    world: Sequence[Point],
) -> list[float]:
    """Erro, em metros, de cada ponto medido contra o reprojetado."""
    return [
        plane.project(px, py).distance_to(alvo)
        for (px, py), alvo in zip(pixels, world, strict=True)
    ]


def reprojection_residual(
    plane: GroundPlane,
    pixels: Sequence[tuple[float, float]],
    world: Sequence[Point],
) -> float:
    """Erro médio, em metros, entre o ponto medido e o reprojetado."""
    erros = reprojection_errors(plane, pixels, world)
    return sum(erros) / len(erros) if erros else 0.0


def perspective_ratio(person_heights: Sequence[float]) -> float:
    """Quanto a mesma pessoa muda de tamanho entre o fundo e o primeiro plano.

    Pessoa é a régua disponível: altura humana varia pouco, então a variação
    de altura em pixels dentro de um mesmo quadro é perspectiva, não gente
    diferente.

    p95 sobre p05, e não máximo sobre mínimo. Uma caixa truncada na borda do
    quadro, ou duas pessoas fundidas numa detecção só, produz altura absurda e
    sequestraria a razão inteira — o aviso passaria a descrever o detector.

    Devolve 1.0 para amostra vazia: sem medida não há alerta a dar.
    """
    alturas = sorted(h for h in person_heights if h > 0.0)
    if not alturas:
        return 1.0

    fundo = alturas[int(0.05 * len(alturas))]
    frente = alturas[min(int(0.95 * len(alturas)), len(alturas) - 1)]
    return frente / fundo if fundo > 0.0 else 1.0


def load_calibration(path: Path | str) -> Calibration:
    # O caminho vem da linha de comando. Resolver antes de abrir tira o
    # relativo e o `..` do meio, e a checagem de arquivo comum transforma
    # "apontei para a pasta errada" numa frase em vez de um traceback de IO
    # saindo de dentro de uma função que promete validar a medição do chão.
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(
            f"{path}: arquivo de calibração não encontrado. Esperado um JSON "
            f"com 'camera', 'note' e ao menos 4 correspondências medidas no chão"
        )

    data = json.loads(path.read_text(encoding="utf-8"))

    camera = str(data.get("camera", "")).strip()
    if not camera:
        raise ValueError(f"{path}: campo 'camera' obrigatório e não vazio")

    note = str(data.get("note", "")).strip()
    if not note:
        raise ValueError(
            f"{path}: campo 'note' obrigatório — registre como o chão foi medido, "
            f"porque daqui a seis meses ninguém lembra"
        )

    correspondencias = data.get("correspondences", [])
    if len(correspondencias) < 4:
        raise ValueError(f"{path}: homografia exige ao menos 4 correspondências")

    pixels = [(float(c["pixel"][0]), float(c["pixel"][1])) for c in correspondencias]
    mundo = [Point(float(c["world"][0]), float(c["world"][1])) for c in correspondencias]

    plane = GroundPlane.from_correspondences(pixels, [(p.x, p.y) for p in mundo])
    erros = reprojection_errors(plane, pixels, mundo)
    media = sum(erros) / len(erros)
    pior = max(erros)

    if pior > MAX_RESIDUAL_M:
        indice = erros.index(pior)
        raise ValueError(
            f"{path}: resíduo de reprojeção de {pior:.2f}m no ponto {indice} "
            f"(pixel {pixels[indice]}) excede o limite de {MAX_RESIDUAL_M}m; "
            f"média de {media:.2f}m. A medição do chão provavelmente está errada, "
            f"e aceitar faria todo limiar em metros mentir em silêncio."
        )

    return Calibration(
        camera=camera, note=note, plane=plane, residual_m=media, worst_residual_m=pior
    )


def plane_from(calibration: Path | str | None, metres_per_pixel: float | None) -> GroundPlane:
    """O plano do chão, da fonte que o chamador escolheu — e só de uma delas.

    Não existe default silencioso. Todo limiar deste sistema é em metros, e um
    plano chutado faz todos eles mentirem sem sintoma nenhum: nada quebra, os
    números só passam a descrever outra cena. Quem roda decide de onde vem a
    escala, e a decisão fica no comando.

    Dar as duas também é erro. Adivinhar qual vale seria escolher em silêncio
    a origem de toda distância da sessão.
    """
    if calibration is not None and metres_per_pixel is not None:
        raise ValueError("passe apenas uma das duas: calibração medida ou escala uniforme")

    if calibration is not None:
        return load_calibration(calibration).plane

    if metres_per_pixel is None:
        raise ValueError(
            "sem plano do chão: passe uma calibração medida, ou "
            "metres_per_pixel para usar escala uniforme. Para estimar a escala, "
            "meça em pixels a altura de uma pessoa no quadro e divida 1.70 por "
            "ela — uma pessoa com 200px dá cerca de 0.0085"
        )

    return GroundPlane.uniform(metres_per_pixel)
