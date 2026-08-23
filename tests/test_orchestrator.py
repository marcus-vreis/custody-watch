import numpy as np

from custody_watch.events import EventKind
from custody_watch.ground_plane import GroundPlane
from custody_watch.orchestrator import run_session
from custody_watch.tracking import TrackedDetection

PLANO = GroundPlane(np.eye(3))


def pessoa(track_id: int, x: float) -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls="person", bbox=(x - 0.5, 0.0, x + 0.5, 1.0))


def mala(track_id: int, x: float) -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls="suitcase", bbox=(x - 0.2, 0.0, x + 0.2, 0.4))


def cena(*frames: list[TrackedDetection]):
    return ((float(i), frame) for i, frame in enumerate(frames))


def test_cada_track_novo_forma_grupo_de_um():
    """Começar separado e exigir evidência para unir é a escolha conservadora."""
    resultado = run_session(cena([pessoa(1, 0.0), pessoa(2, 10.0)]), PLANO)

    formados = resultado.events.of_kind(EventKind.PARTY_FORMED)
    assert [e.evidence["members"] for e in formados] == [[1], [2]]


def test_track_ja_conhecido_nao_forma_grupo_de_novo():
    resultado = run_session(cena([pessoa(1, 0.0)], [pessoa(1, 1.0)], [pessoa(1, 2.0)]), PLANO)

    assert len(resultado.events.of_kind(EventKind.PARTY_FORMED)) == 1


def test_back_tracing_atribui_o_dono_de_quem_depositou():
    resultado = run_session(cena([pessoa(1, 0.0)], [pessoa(1, 0.0), mala(9, 0.5)]), PLANO)

    posse = resultado.events.of_kind(EventKind.BAG_OWNED)
    assert len(posse) == 1
    assert posse[0].bag == 9


def test_bagagem_longe_de_todos_fica_orfa():
    """Ninguém dentro do raio de busca significa dono desconhecido, não chute."""
    resultado = run_session(cena([pessoa(1, 0.0)], [pessoa(1, 0.0), mala(9, 50.0)]), PLANO)

    assert resultado.events.of_kind(EventKind.BAG_OWNED) == []


def test_retirada_pelo_dono_nao_gera_alerta():
    quadros = [[pessoa(1, 0.0), mala(9, 0.5)]] * 3 + [[pessoa(1, 0.5)]] * 8
    resultado = run_session(cena(*quadros), PLANO)

    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_OWNER)) == 1
    assert resultado.queue == []


def test_retirada_por_estranho_gera_alerta_na_fila():
    quadros = [[pessoa(1, 0.0), mala(9, 0.5)]] * 3 + [[pessoa(7, 0.5)]] * 8
    resultado = run_session(cena(*quadros), PLANO)

    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)) == 1
    assert len(resultado.queue) == 1
    assert resultado.queue[0].person == 7


def test_ausencia_curta_nao_conta_como_retirada():
    """Bagagem some por um frame e volta: oclusão, não furto."""
    quadros = [[pessoa(1, 0.0), mala(9, 0.5)]] * 3 + [[pessoa(1, 0.0)]] * 2
    quadros += [[pessoa(1, 0.0), mala(9, 0.5)]] * 3

    resultado = run_session(cena(*quadros), PLANO, missing_frames_before_removal=5)

    assert resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER) == []
    assert resultado.events.of_kind(EventKind.BAG_REMOVED_BY_OWNER) == []


def test_retirada_e_resolvida_uma_vez_so():
    """Estado terminal não retransiciona, mesmo com a bagagem ausente por muitos frames."""
    quadros = [[pessoa(1, 0.0), mala(9, 0.5)]] * 3 + [[pessoa(7, 0.5)]] * 30
    resultado = run_session(cena(*quadros), PLANO)

    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)) == 1


def test_resultado_reporta_frames_e_duracao():
    resultado = run_session(cena([pessoa(1, 0.0)], [pessoa(1, 1.0)], [pessoa(1, 2.0)]), PLANO)

    assert resultado.frames == 3
    assert resultado.duration_s == 2.0
