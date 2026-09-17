"""A composição entre fila ranqueada, recorte de clipe e página do operador.

Vive separada do `report.py` porque aquele módulo é HTML puro — não importa
cv2 nem PIL, e não deveria passar a importar. Recortar clipe é outra
responsabilidade, e juntar as duas é uma terceira.
"""

import numpy as np
import pytest

from custody_watch.alerts import AlertItem
from custody_watch.events import Event, EventKind, EventLog
from custody_watch.orchestrator import SessionResult
from custody_watch.review import review_items
from custody_watch.tracking import TrackedDetection
from custody_watch.types import FlagLevel

LARGURA, ALTURA = 64, 48


def quadros(n: int = 30, fps: float = 25.0, bag_track: int = 900):
    """Quadros sintéticos no formato que `render_clip` consome."""
    for i in range(n):
        imagem = np.zeros((ALTURA, LARGURA, 3), dtype=np.uint8)
        yield (
            i / fps,
            imagem,
            [
                TrackedDetection(7, "person", (10.0, 10.0, 20.0, 40.0)),
                TrackedDetection(bag_track, "suitcase", (30.0, 30.0, 38.0, 40.0)),
            ],
        )


def alerta(person: int = 7) -> AlertItem:
    return AlertItem(
        person=person,
        score=9.5,
        top_level=FlagLevel.N3,
        clip_start=0.0,
        clip_end=0.8,
        explanations=["Bagagem 900 do grupo 1 foi retirada por pessoa fora do grupo."],
    )


def com_bagagem(bag_id: int) -> EventLog:
    """Um log que menciona a bagagem — é dele que sai o id a destacar."""
    registro = EventLog()
    registro.emit(
        Event(
            kind=EventKind.BAG_OWNED,
            t_start=0.0,
            t_end=0.0,
            subject=None,
            bag=bag_id,
            party=1,
        )
    )
    return registro


def resultado(fila: list[AlertItem], events: EventLog | None = None, **kwargs) -> SessionResult:
    return SessionResult(
        events=events if events is not None else EventLog(),
        queue=fila,
        frames=30,
        duration_s=1.2,
        **kwargs,
    )


def test_fila_vazia_nao_produz_item(tmp_path):
    """Sessão sem alerta é o caso comum e não é erro."""
    itens = review_items(resultado([]), quadros, tmp_path, "cena")

    assert itens == []


def test_cada_alerta_vira_um_item_com_clipe(tmp_path):
    itens = review_items(resultado([alerta()]), quadros, tmp_path, "cena")

    (item,) = itens
    assert item.rank == 1
    assert item.person == 7
    assert item.level == "N3"
    assert item.clip_path is not None
    assert item.clip_path.exists()


def test_o_ranque_segue_a_ordem_da_fila(tmp_path):
    """A fila já chega ordenada pelo `build_queue`. O item só numera."""
    itens = review_items(resultado([alerta(7), alerta(9)]), quadros, tmp_path, "cena")

    assert [i.rank for i in itens] == [1, 2]
    assert [i.person for i in itens] == [7, 9]


def test_o_clipe_recebe_todo_track_que_ja_respondeu_pela_bagagem(tmp_path):
    """Depois de uma readoção sob oclusão os quadros trazem um track novo, mas
    o alerta cita o `bag_id` antigo. Passar só o canônico deixaria o clipe do
    operador sem caixa na bagagem — justamente no caso que a readoção existe
    para tratar."""
    sessao = resultado([alerta()], events=com_bagagem(900), bag_links={901: 900})
    vistos: list[frozenset[int]] = []

    def espiao(frames, request, **kwargs):
        vistos.append(request.bag_ids)
        return None

    review_items(sessao, quadros, tmp_path, "cena", render=espiao)

    assert vistos == [frozenset({900, 901})]


def test_clipe_sem_quadro_na_janela_nao_derruba_o_item(tmp_path):
    """`render_clip` devolve `None` quando a janela cai fora do vídeo — é o
    caso de um alerta perto do fim, e não é erro."""
    tarde = AlertItem(
        person=7,
        score=9.5,
        top_level=FlagLevel.N3,
        clip_start=900.0,
        clip_end=930.0,
        explanations=["fora do vídeo"],
    )

    (item,) = review_items(resultado([tarde]), quadros, tmp_path, "cena")

    assert item.clip_path is None
    assert item.person == 7


def test_cada_sessao_escreve_num_nome_proprio(tmp_path):
    """Duas sessões com a mesma pessoa sinalizada não podem sobrescrever o
    clipe uma da outra."""
    review_items(resultado([alerta()]), quadros, tmp_path, "primeira")
    review_items(resultado([alerta()]), quadros, tmp_path, "segunda")

    gerados = sorted(p.name for p in tmp_path.rglob("*.gif"))
    assert len(gerados) == 2
    assert gerados[0] != gerados[1]


def test_a_bagagem_citada_vem_dos_eventos(tmp_path):
    """Sem evento de bagagem não há o que destacar, e passar um id inventado
    pintaria a caixa errada."""
    vistos: list[frozenset[int]] = []

    def espiao(frames, request, **kwargs):
        vistos.append(request.bag_ids)
        return None

    review_items(resultado([alerta()]), quadros, tmp_path, "cena", render=espiao)

    assert vistos == [frozenset()]


def test_a_pessoa_do_clipe_inclui_os_tracks_religados(tmp_path):
    """Mesma razão da bagagem, do lado das pessoas: o alerta cita o id
    canônico e os quadros trazem os brutos."""
    sessao = resultado([alerta(7)], links={42: 7})
    vistos: list[frozenset[int]] = []

    def espiao(frames, request, **kwargs):
        vistos.append(request.person_ids)
        return None

    review_items(sessao, quadros, tmp_path, "cena", render=espiao)

    assert vistos == [frozenset({7, 42})]


def test_janela_invertida_falha_alto(tmp_path):
    invertida = AlertItem(
        person=7,
        score=1.0,
        top_level=FlagLevel.N2,
        clip_start=5.0,
        clip_end=1.0,
        explanations=[],
    )

    sessao = resultado([invertida])

    with pytest.raises(ValueError, match="janela invertida"):
        review_items(sessao, quadros, tmp_path, "cena")
