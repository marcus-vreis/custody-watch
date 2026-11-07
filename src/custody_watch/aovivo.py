"""O laço ao vivo: de onde vêm os quadros, quem os detecta, para onde vão.

Arquivo e câmera entram pelo mesmo laço, mas marcam o tempo de jeitos
diferentes, e a diferença é o que decide se os limiares valem:

- **Arquivo** tem relógio próprio. O instante é o índice do quadro sobre o
  fps, e nenhum quadro se perde -- se o processador for lento, a tela anda
  em câmera lenta, mas a lógica vê exatamente o que veria em lote. Para
  caber num processador fraco, `passo` pula quadros sem encolher o tempo.
- **Câmera** não espera ninguém. Processar todo quadro de uma câmera mais
  rápida que o detector é acumular atraso sem limite: em uma hora de turno o
  operador estaria vendo o passado. `CameraAoVivo` lê numa thread própria e
  entrega sempre o quadro mais novo, e o instante vem do relógio -- quadro
  perdido é tempo que passou, e contar quadros encolheria cada limiar em
  segundos na proporção dos que se perderam.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

from .calibration import uniform_scale_warning
from .orchestrator import LiveSession
from .painel import Painel, desenha, estado
from .source_health import SourceHealth
from .tracking import TrackedDetection
from .types import PERSON_CLASS
from .video import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_TRACKER,
    DEFAULT_WEIGHTS,
    parse_result,
    raw_frames,
)

Detector = Callable[[np.ndarray], list[TrackedDetection]]
Quadros = Iterable[tuple[float, np.ndarray]]

AMOSTRA_MIN_ESCALA = 20
"""Alturas de pessoa antes de opinar sobre a escala. Com menos, o p05 e o p95
de `perspective_ratio` são a mesma meia dúzia de caixas."""

AMOSTRA_MAX_ESCALA = 5000
"""Depois disto a estimativa não muda mais, e guardar alturas para sempre numa
câmera que roda por dias seria um vazamento."""

VERIFICA_ESCALA_A_CADA = 25


def processa(
    quadros: Quadros,
    detecta: Detector,
    sessao: LiveSession,
    painel: Painel,
    parar: threading.Event | None = None,
    escala_uniforme: bool = False,
    source_health: SourceHealth | None = None,
    capture_time: Callable[[float], float] | None = None,
) -> None:
    """Detecta, alimenta a sessão e publica no painel, quadro a quadro.

    `escala_uniforme` liga o aviso de perspectiva. Na página de revisão ele
    sai no terminal depois do fim; ao vivo não há fim, então o aviso vai para
    a tela, onde está quem precisa lê-lo.
    """
    alturas: list[float] = []
    avisos: list[str] = []

    if source_health is not None and source_health.kind == "live" and capture_time is None:
        raise ValueError("fonte live exige capture_time do leitor de câmera")
    completed = False
    try:
        for t, imagem in quadros:
            if parar is not None and parar.is_set():
                return
            received_at = None
            if source_health is not None:
                received_at = (
                    capture_time(t) if capture_time is not None else source_health.received(t)
                )
            tracked = detecta(imagem)
            sessao.feed(t, tracked)

            if escala_uniforme and len(alturas) < AMOSTRA_MAX_ESCALA:
                alturas.extend(d.bbox[3] - d.bbox[1] for d in tracked if d.cls == PERSON_CLASS)
                if (
                    len(alturas) >= AMOSTRA_MIN_ESCALA
                    and sessao.frames % VERIFICA_ESCALA_A_CADA == 0
                ):
                    aviso = uniform_scale_warning(alturas)
                    avisos = [aviso] if aviso else []

            atual = estado(sessao)
            atual["avisos"] = list(avisos)
            painel.atualiza(desenha(imagem, tracked, sessao), atual)
            if source_health is not None:
                source_health.processed(t, received_at=received_at)
        completed = True
    except KeyboardInterrupt:
        if source_health is not None:
            source_health.finish("stopped", "user_stop")
        raise
    except Exception:
        if source_health is not None:
            source_health.finish("interrupted", "processing_exception")
        raise
    finally:
        if source_health is not None:
            if parar is not None and parar.is_set():
                source_health.finish("stopped", "user_stop")
            elif completed and source_health.kind == "file":
                source_health.finish("completed", "end_of_file")


def quadros_de_arquivo(caminho: Path | str, passo: int = 1) -> Iterator[tuple[float, np.ndarray]]:
    """Um quadro a cada `passo`, com o instante do vídeo e não o da leitura."""
    if passo < 1:
        raise ValueError(f"passo tem que ser ao menos 1, veio {passo}")
    for indice, (t, imagem) in enumerate(raw_frames(caminho)):
        if indice % passo == 0:
            yield t, imagem


class CameraAoVivo:
    """Quadros de uma câmera, sempre o mais novo.

    `captura` é qualquer coisa com `read()` e `release()` no formato do
    `cv2.VideoCapture` -- o que deixa testar a política de descarte sem
    câmera nenhuma.
    """

    ESPERA_S = 0.1
    """De quanto em quanto o consumidor confere se pediram para parar. Uma
    leitura de RTSP pode travar por segundos quando a rede cai, e o Ctrl+C não
    pode esperar por ela."""

    def __init__(
        self,
        captura: Any,
        relogio: Callable[[], float] = time.monotonic,
        parar: threading.Event | None = None,
        source_health: SourceHealth | None = None,
    ) -> None:
        self._captura = captura
        self._relogio = relogio
        self._parar = parar if parar is not None else threading.Event()
        self._source_health = source_health
        self._origin: float | None = None
        self._cond = threading.Condition()
        self._ultimo: tuple[int, float, np.ndarray] | None = None
        self._acabou = False
        self.encerrada = threading.Event()
        self.perdidos = 0
        """Quadros lidos que ninguém processou. É o preço de não atrasar, e
        fica exposto porque um número alto diz que o detector não dá conta da
        câmera."""

    def _le(self) -> None:
        indice = 0
        reason = "read_failed"
        try:
            while not self._parar.is_set():
                ok, imagem = self._captura.read()
                if not ok:
                    return
                agora = self._relogio()
                if self._origin is None:
                    self._origin = agora
                t = agora - self._origin
                if self._source_health is not None:
                    self._source_health.received(t, received_at=agora)
                with self._cond:
                    self._ultimo = (indice, t, imagem)
                    self._cond.notify_all()
                indice += 1
        except Exception:
            # Não publicar str(erro): backends podem incluir uma URL com senha.
            reason = "capture_exception"
        finally:
            if self._source_health is not None:
                self._source_health.finish(
                    "stopped" if self._parar.is_set() else "interrupted",
                    "user_stop" if self._parar.is_set() else reason,
                )
            try:
                self._captura.release()
            except Exception:
                if self._source_health is not None and not self._parar.is_set():
                    self._source_health.finish("interrupted", "capture_exception")
            finally:
                with self._cond:
                    self._acabou = True
                    self._cond.notify_all()
                self.encerrada.set()

    def received_at(self, media_t: float) -> float:
        """O t da câmera é monotônico desde a primeira captura, sem mapa crescente."""
        with self._cond:
            if self._origin is None:
                raise ValueError("nenhum quadro capturado")
            return self._origin + media_t

    def __iter__(self) -> Iterator[tuple[float, np.ndarray]]:
        threading.Thread(target=self._le, daemon=True, name="camera").start()
        entregue = -1

        def novo() -> bool:
            return self._ultimo is not None and self._ultimo[0] > entregue

        def pronto() -> bool:
            return self._acabou or self._parar.is_set() or novo()

        while True:
            with self._cond:
                while not pronto():
                    self._cond.wait(self.ESPERA_S)
                if self._parar.is_set():
                    if self._source_health is not None:
                        self._source_health.finish("stopped", "user_stop")
                    return
                if not novo():
                    return
                indice, t, imagem = self._ultimo

            self.perdidos += indice - entregue - 1
            entregue = indice
            yield t, imagem


def fonte_de(texto: str) -> tuple[int | str, bool]:
    """O que o operador digitou, e se é ao vivo.

    Número é índice de câmera local; qualquer coisa com esquema (`rtsp://`,
    `http://`) é fluxo de rede; o resto é arquivo.
    """
    if texto.isdigit():
        return int(texto), True
    if "://" in texto:
        return texto, True
    return texto, False


def detector_yolo(
    weights: str = DEFAULT_WEIGHTS,
    tracker: str = DEFAULT_TRACKER,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    with_appearance: bool = True,
) -> Detector:
    """O mesmo detector e o mesmo tracker de `VideoSource`, um quadro por vez.

    `persist=True` é o que faz o tracker lembrar do quadro anterior entre uma
    chamada e outra; sem ele cada quadro nasceria com ids novos e nenhuma
    custódia sobreviveria a dois quadros.
    """
    from ultralytics import YOLO  # import tardio: pesado

    modelo = YOLO(weights)

    def detecta(imagem: np.ndarray) -> list[TrackedDetection]:
        resultado = modelo.track(imagem, tracker=tracker, persist=True, verbose=False)[0]
        return parse_result(resultado, min_confidence, with_appearance)

    return detecta


__all__ = [
    "CameraAoVivo",
    "Detector",
    "detector_yolo",
    "fonte_de",
    "processa",
    "quadros_de_arquivo",
]
