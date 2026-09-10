"""Da fila ranqueada para os itens que o operador revisa.

Compõe três coisas que moram separadas de propósito: `alerts` decide a ordem,
`clips` recorta o vídeo, e `report` escreve a página. Juntá-las é uma quarta
responsabilidade, e é esta.

Ela vive fora de `report.py` porque aquele módulo é HTML puro — não importa
cv2 nem PIL, e não deveria passar a importar só para recortar um GIF.

O detalhe que justifica o módulo existir é a tradução de identidade. O alerta
cita ids **canônicos**; os quadros do vídeo trazem os **brutos**, que divergem
depois de uma religação de track ou de uma readoção de âncora ocluída. Sem a
tradução, o clipe do operador fica sem caixa exatamente no caso que a
religação existe para tratar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .clips import ClipRequest, render_clip
from .orchestrator import SessionResult
from .report import ReviewItem

Frames = Callable[[], Iterator[tuple[float, Any, list[Any]]]]


def _bagagem_citada(resultado: SessionResult) -> frozenset[int]:
    """Todo track que já respondeu pela bagagem que os eventos mencionam.

    Sem evento de bagagem não há o que destacar, e inventar um id pintaria a
    caixa errada — pior que não pintar nenhuma.
    """
    for evento in resultado.events:
        if evento.bag is not None:
            return frozenset(resultado.raw_bag_ids(evento.bag))
    return frozenset()


def review_items(
    resultado: SessionResult,
    frames: Frames,
    saida: Path,
    sessao: str,
    render: Callable[..., Path | None] = render_clip,
) -> list[ReviewItem]:
    """Recorta um clipe por alerta e devolve os itens já ranqueados.

    `frames` é uma **fábrica**, não um iterador: cada clipe precisa de uma
    passagem própria pelo vídeo, e um iterador já consumido devolveria janela
    vazia para o segundo alerta em diante.

    O nome do arquivo inclui a sessão porque duas sessões com a mesma pessoa
    sinalizada sobrescreveriam o clipe uma da outra.
    """
    bag_ids = _bagagem_citada(resultado)
    itens: list[ReviewItem] = []

    for posicao, alerta in enumerate(resultado.queue, start=1):
        caminho = render(
            frames(),
            ClipRequest(
                start_s=alerta.clip_start,
                end_s=alerta.clip_end,
                person_ids=frozenset(resultado.raw_ids(alerta.person)),
                bag_ids=bag_ids,
                output=saida / "clipes" / f"{sessao}_{alerta.person}.gif",
            ),
        )

        itens.append(
            ReviewItem(
                rank=posicao,
                person=alerta.person,
                score=alerta.score,
                level=alerta.top_level.name,
                clip_start=alerta.clip_start,
                clip_end=alerta.clip_end,
                explanations=alerta.explanations,
                clip_path=caminho,
            )
        )

    return itens


__all__ = ["review_items"]
