#!/usr/bin/env python3
"""Baixa os cenários de bagagem do CAVIAR para `data/caviar/`.

Por que CAVIAR e não PETS2007: o PETS2007 era a escolha original — tinha
quatro câmeras, mais eventos, e distribuía calibração de câmera. Em agosto de
2026 todos os seus hosts estão fora do ar (`cvg.reading.ac.uk` não resolve,
`ftp.cs.rdg.ac.uk` e `ftp.pets.rdg.ac.uk` não conectam, `pets2006.net` e
`pets2007.net` morreram). O Wayback tem o HTML da página, mas os links são
`ftp://` e o Wayback não arquiva FTP — retornam 404. Não há mirror conhecido.

O CAVIAR é o que sobrou com cenário equivalente, e tem duas limitações que
importam: 384x288 de resolução (uma bagagem a distância tem ~15-20px) e
**nenhuma calibração de câmera**, então a homografia de `ground_plane.py`
precisa ser estimada à mão a partir de dimensões conhecidas do saguão.

Baixa MPEG2 e sequência JPEG. As sequências JPEG são o que interessa para
processar: o ground truth é indexado por número de frame, e decodificar MPEG2
introduz ambiguidade de contagem entre decodificadores.

O que o ground truth entrega, medido em `LeftBag_PickedUp/lbpugt.xml`: 1355
frames (54s a 25fps), quatro objetos anotados, e **a bagagem é um objeto
próprio** — id 4, papel `leaving object`, presente dos frames 503 a 1015, que
é exatamente a janela de custódia.

A caixa dessa bagagem tem em média **18x14 pixels**. Isso é 0,2% da área do
frame, abaixo do limiar de "objeto pequeno" do COCO (32x32): nenhum detector
encontra isso de forma confiável. A consequência prática é que o CAVIAR serve
para exercitar a camada de lógica alimentando as caixas do ground truth direto
em `to_observations`, e não serve para avaliar detecção. Detecção precisa de
material gravado em resolução moderna.

    uv run python scripts/download_caviar.py
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1"
DEST = Path(__file__).resolve().parent.parent / "data" / "caviar"

# (diretório do cenário, vídeo, sequência de frames, ground truth)
SCENARIOS = [
    ("LeftBag_PickedUp", "LeftBag_PickedUp.mpg", "LeftBag_PickedUp_jpg.tar.gz", "lbpugt.xml"),
    ("LeftBag", "LeftBag.mpg", "LeftBag_jpg.tar.gz", "lb1gt.xml"),
    ("LeftBag_AtChair", "LeftBag_AtChair.mpg", "LeftBag_AtChair_jpg.tar.gz", "lb2gt.xml"),
    (
        "LeftBag_BehindChair",
        "LeftBag_BehindChair.mpg",
        "LeftBag_BehindChair_jpg.tar.gz",
        "lbbcgt.xml",
    ),
    ("LeftBox", "LeftBox.mpg", "LeftBox_jpg.tar.gz", "lbgt.xml"),
]


def remote_size(url: str) -> int | None:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, ValueError):
        return None


def download(url: str, target: Path) -> str:
    """Baixa se ausente ou incompleto. Devolve uma linha de status."""
    expected = remote_size(url)
    if expected is None:
        return "indisponível (HEAD falhou)"

    if target.exists() and target.stat().st_size == expected:
        return f"já presente ({expected / 1e6:.1f} MB)"

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")

    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as handle:
        received = 0
        while chunk := response.read(1 << 16):
            handle.write(chunk)
            received += len(chunk)

    if received != expected:
        partial.unlink(missing_ok=True)
        return f"INCOMPLETO ({received} de {expected} bytes)"

    partial.replace(target)
    return f"baixado ({expected / 1e6:.1f} MB)"


def main() -> int:
    print(f"destino: {DEST}\n")
    falhas = 0

    for scenario, video, frames, ground_truth in SCENARIOS:
        print(f"[{scenario}]")
        for filename in (video, frames, ground_truth):
            status = download(f"{BASE_URL}/{scenario}/{filename}", DEST / scenario / filename)
            print(f"  {filename:<38} {status}")
            if "INCOMPLETO" in status or "indisponível" in status:
                falhas += 1
        print()

    if falhas:
        print(f"{falhas} arquivo(s) falharam.")
        return 1

    print("Tudo baixado. `data/` está no .gitignore — nada disso vai para o repositório.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
