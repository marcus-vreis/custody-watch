"""O que a tela ao vivo mostra: a fila, os eventos e o quadro anotado.

A página de revisão (`report.py`) é o fim de uma sessão; esta é o meio dela.
Mostra o mesmo conteúdo -- quem, com que gravidade e por quê -- mas enquanto
a câmera ainda está rodando, e sem clipe: ao vivo o operador olha para o
quadro atual, e o clipe é o que ele pede depois.

`Painel` é o único ponto de contato entre a thread que processa quadros e a
que serve HTTP. Guarda só o último quadro e o último estado: a tela sempre
quer o agora, e acumular o passado aqui seria um vazamento numa câmera que
roda por dias.
"""

from __future__ import annotations

import threading
from itertools import islice
from typing import Any

import cv2
import numpy as np

from .events import EventKind
from .orchestrator import LiveSession
from .tracking import TrackedDetection
from .types import FlagLevel

EVENTOS_MAX = 20

EVENTOS_DE_CUSTODIA = frozenset(
    {
        EventKind.BAG_APPEARED,
        EventKind.BAG_OWNED,
        EventKind.BAG_UNATTENDED,
        EventKind.BAG_REATTENDED,
        EventKind.BAG_AMBIGUOUS,
        EventKind.BAG_OCCLUDED,
        EventKind.BAG_REMOVED_BY_OWNER,
        EventKind.BAG_REMOVED_BY_STRANGER,
    }
)
"""O que vai para a lista da tela. Religar trilha e formar grupo são
contabilidade interna: no vídeo do portão, 13 dos 20 eventos recentes eram
religações, e a lista que devia mostrar o furto mostrava ruído. A lista é
explícita de propósito -- evento novo só aparece para o operador quando
alguém decide que ele importa."""
FILA_MAX = 25
QUALIDADE_JPEG = 80

# BGR, que é o que o OpenCV desenha.
VERMELHO = (57, 57, 230)
LARANJA = (97, 162, 244)
AZUL = (228, 202, 72)
CINZA = (150, 150, 150)


def _fila(sessao: LiveSession, fila_max: int) -> list[dict[str, Any]]:
    return [
        {
            "posicao": posicao,
            "pessoa": item.person,
            "nivel": item.top_level.name,
            "score": round(item.score, 2),
            "explicacoes": list(item.explanations),
            "clipe": [round(item.clip_start, 1), round(item.clip_end, 1)],
        }
        for posicao, item in enumerate(sessao.queue()[:fila_max], start=1)
    ]


def _eventos(sessao: LiveSession, eventos_max: int) -> list[dict[str, Any]]:
    """Os últimos de custódia, mostrados do mais novo para o mais antigo."""
    ultimos = islice(
        (e for e in reversed(sessao.events) if e.kind in EVENTOS_DE_CUSTODIA), eventos_max
    )
    return sorted(
        (
            {
                "t": round(e.t_start, 1),
                "tipo": e.kind.value,
                "bagagem": e.bag,
                "pessoa": e.subject,
                "grupo": e.party,
            }
            for e in ultimos
        ),
        key=lambda e: e["t"],
        reverse=True,
    )


def estado(
    sessao: LiveSession, eventos_max: int = EVENTOS_MAX, fila_max: int = FILA_MAX
) -> dict[str, Any]:
    """Tudo que a tela precisa, em tipos que viram JSON sem conversão."""
    return {
        "t": sessao.t,
        "quadros": sessao.frames,
        "ancoras": len(sessao.anchors()),
        "fila": _fila(sessao, fila_max),
        "eventos": _eventos(sessao, eventos_max),
    }


def _cor_e_rotulo(
    det: TrackedDetection, sessao: LiveSession
) -> tuple[tuple[int, int, int], str, int]:
    if det.cls == "person":
        nivel = sessao.person_level(det.track_id)
        pessoa = sessao.canonical_person(det.track_id)
        if nivel is not None and nivel >= FlagLevel.N3:
            return VERMELHO, f"N3 p{pessoa}", 3
        if nivel is not None and nivel >= FlagLevel.N2:
            return LARANJA, f"N2 p{pessoa}", 2
        return CINZA, "", 1

    bagagem = sessao.bag_for_track(det.track_id)
    if bagagem is not None and bagagem in sessao.anchors():
        dono = f" g{bagagem.owner_party}" if bagagem.owner_party is not None else " órfã"
        return AZUL, f"bagagem {bagagem.bag_id}{dono}", 2
    return CINZA, "", 1


def desenha(quadro: np.ndarray, tracked: list[TrackedDetection], sessao: LiveSession) -> np.ndarray:
    """O quadro com as caixas coloridas pelo que a lógica decidiu.

    Vermelho e laranja são N3 e N2; azul é bagagem com custódia em aberto.
    Todo o resto fica cinza e fino: a cor é o que decide para onde o operador
    olha primeiro, e cor demais é o mesmo que nenhuma.
    """
    anotado = quadro.copy()
    for det in tracked:
        cor, rotulo, espessura = _cor_e_rotulo(det, sessao)
        x0, y0, x1, y1 = (int(round(v)) for v in det.bbox)
        cv2.rectangle(anotado, (x0, y0), (x1, y1), cor, espessura)
        if rotulo:
            cv2.putText(
                anotado, rotulo, (x0, max(12, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, cor, 2
            )
    return anotado


class Painel:
    """O último quadro e o último estado, trocados entre duas threads."""

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._jpeg: bytes | None = None
        self._estado: dict[str, Any] = {}

    def atualiza(self, quadro: np.ndarray, estado_atual: dict[str, Any]) -> None:
        ok, codificado = cv2.imencode(".jpg", quadro, [cv2.IMWRITE_JPEG_QUALITY, QUALIDADE_JPEG])
        with self._trava:
            if ok:
                self._jpeg = codificado.tobytes()
            self._estado = estado_atual

    def jpeg(self) -> bytes | None:
        with self._trava:
            return self._jpeg

    def estado(self) -> dict[str, Any]:
        with self._trava:
            return dict(self._estado)


__all__ = ["Painel", "desenha", "estado"]
