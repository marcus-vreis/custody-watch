"""Ground truth por evento, e casamento com o que o sistema emitiu.

Anotar caixa a caixa noventa minutos de vídeo é inviável à mão, e não é o que
a métrica pede. `P_miss @ RFA` opera sobre eventos: "aos 47s a bagagem 3 foi
retirada por quem não era do grupo dono".

`GroundTruthEvent.kind` reusa `EventKind`, para que o vocabulário de anotação
e o de emissão sejam o mesmo. Tradução no meio é onde erros silenciosos moram.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .events import Event, EventKind, EventLog

DEFAULT_SLACK_BEFORE_S = 1.0


@dataclass(frozen=True)
class GroundTruthEvent:
    """Um evento anotado à mão, ou derivado de anotação existente.

    `t_end` existe porque nem todo evento é instantâneo. `BAG_UNATTENDED` é um
    **estado**, e o sistema só o emite depois de `unattended_time_s` de duração
    — então saber quando o estado começou não basta para julgar se o sistema
    deveria tê-lo emitido. Sem a duração, um abandono de 5s e um de 5 minutos
    são indistinguíveis na anotação, e o primeiro entra na conta como positivo
    perdido quando na verdade nunca foi instância do evento.

    `truncated` distingue as duas razões pelas quais um estado para de valer.
    Se o dono voltou, o estado acabou de verdade e `t_end - t` é a duração. Se
    a observação acabou, `t_end - t` é apenas um limite inferior. A diferença
    separa "não aconteceu" de "não dá para saber" — e sem ela a segunda entra
    na conta como positivo perdido, que é a forma mais fácil de inventar um
    P_miss ruim.
    """

    kind: EventKind
    t: float
    t_end: float | None = None
    truncated: bool = False
    bag: int | None = None
    subject: int | None = None
    note: str = ""

    def sustained_for(self, seconds: float) -> bool | None:
        """O estado durou ao menos `seconds`? `None` quando não dá para saber.

        Um estado truncado que já passou do limiar é positivo apesar do corte:
        o que veio depois não muda o que já aconteceu. Truncado e abaixo do
        limiar é a única combinação sem resposta.
        """
        if self.t_end is None:
            return None
        if self.t_end - self.t >= seconds:
            return True
        return None if self.truncated else False


@dataclass(frozen=True)
class MatchResult:
    matched: list[tuple[GroundTruthEvent, Event]] = field(default_factory=list)
    missed: list[GroundTruthEvent] = field(default_factory=list)
    spurious: list[Event] = field(default_factory=list)

    @property
    def total_positives(self) -> int:
        return len(self.matched) + len(self.missed)


def save_annotations(events: list[GroundTruthEvent], path: Path | str, session: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session": session,
        "events": [
            {
                "kind": e.kind.value,
                "t": e.t,
                "t_end": e.t_end,
                "truncated": e.truncated,
                "bag": e.bag,
                "subject": e.subject,
                "note": e.note,
            }
            for e in events
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_annotations(path: Path | str) -> list[GroundTruthEvent]:
    """Carrega anotação. Exige `session` — daqui a seis meses ninguém lembra de
    que gravação o arquivo veio."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("session"):
        raise ValueError(f"{path}: campo 'session' obrigatório e não vazio")

    return [
        GroundTruthEvent(
            kind=EventKind(item["kind"]),
            t=float(item["t"]),
            t_end=None if item.get("t_end") is None else float(item["t_end"]),
            truncated=bool(item.get("truncated", False)),
            bag=item.get("bag"),
            subject=item.get("subject"),
            note=item.get("note", ""),
        )
        for item in data.get("events", [])
    ]


def match_events(
    detected: EventLog,
    truth: list[GroundTruthEvent],
    lag_window_s: float,
    slack_before_s: float = DEFAULT_SLACK_BEFORE_S,
    kinds: set[EventKind] | None = None,
) -> MatchResult:
    """Casa evento detectado com anotado, em janela assimétrica.

    A assimetria não é refinamento. `BAG_UNATTENDED` dispara
    `unattended_time_s` **depois** do abandono físico: com o padrão de 25s, a
    anotação diz 20s e o sistema emite aos 45s. Uma janela simétrica de poucos
    segundos marcaria toda detecção como espúria e todo positivo como perdido,
    produzindo `P_miss = 1.0` sem relação com o sistema.

    `lag_window_s` vem do chamador porque o atraso é função do limiar em uso.
    Fixá-lo aqui acoplaria o casador à config e esconderia a dependência.
    """
    if kinds is None and not truth:
        raise ValueError(
            "sem anotação positiva não há como derivar os tipos de interesse; "
            "passe `kinds` explicitamente. Uma gravação de controle, em que nada "
            "é furtado, é justamente onde o falso alarme se mede — derivar daria "
            "zero espúrios em silêncio"
        )

    interesse = kinds if kinds is not None else {e.kind for e in truth}
    candidatos = [e for e in detected if e.kind in interesse]

    matched: list[tuple[GroundTruthEvent, Event]] = []
    missed: list[GroundTruthEvent] = []
    usados: set[int] = set()

    for anotado in sorted(truth, key=lambda e: e.t):
        inicio = anotado.t - slack_before_s
        fim = anotado.t + lag_window_s

        elegiveis = [
            (abs(e.t_start - anotado.t), i, e)
            for i, e in enumerate(candidatos)
            if i not in usados and e.kind is anotado.kind and inicio <= e.t_start <= fim
        ]

        if not elegiveis:
            missed.append(anotado)
            continue

        _, indice, escolhido = min(elegiveis, key=lambda item: item[0])
        usados.add(indice)
        matched.append((anotado, escolhido))

    spurious = [e for i, e in enumerate(candidatos) if i not in usados]
    return MatchResult(matched=matched, missed=missed, spurious=spurious)
