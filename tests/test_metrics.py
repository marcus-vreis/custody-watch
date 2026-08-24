import pytest

from custody_watch.metrics import OperatingPoint, ScoredEvent, p_miss_at_rfa


def test_deteccao_perfeita_tem_p_miss_zero():
    events = [
        ScoredEvent(score=9.0, is_true_event=True),
        ScoredEvent(score=1.0, is_true_event=False),
    ]
    point = p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.1)

    assert point.p_miss == pytest.approx(0.0)
    assert point.threshold == pytest.approx(9.0)


def test_empate_prefere_o_ponto_mais_barato():
    """Falsos alarmes depois do último acerto não melhoram nada.

    Sem esta guarda, o ponto de operação avançaria para um limiar mais
    permissivo com o mesmo P_miss e mais falsos alarmes — pior de graça.
    """
    events = [
        ScoredEvent(score=9.0, is_true_event=True),
        ScoredEvent(score=5.0, is_true_event=False),
        ScoredEvent(score=1.0, is_true_event=False),
    ]
    point = p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.5)

    assert point.p_miss == pytest.approx(0.0)
    assert point.threshold == pytest.approx(9.0)
    assert point.false_alarms == 0


def test_orcamento_de_falso_alarme_e_respeitado():
    """Com 10 min de vídeo e RFA alvo de 0.1/min, cabe 1 falso alarme."""
    events = [
        ScoredEvent(score=9.0, is_true_event=False),
        ScoredEvent(score=8.0, is_true_event=False),
        ScoredEvent(score=7.0, is_true_event=True),
    ]
    point = p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.1)

    assert point.false_alarms <= 1
    assert point.p_miss == pytest.approx(1.0)


def test_limiar_mais_permissivo_reduz_p_miss():
    events = [
        ScoredEvent(score=9.0, is_true_event=True),
        ScoredEvent(score=8.0, is_true_event=False),
        ScoredEvent(score=7.0, is_true_event=True),
    ]
    apertado = p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.0)
    folgado = p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.2)

    assert apertado.p_miss == pytest.approx(0.5)
    assert folgado.p_miss == pytest.approx(0.0)


def test_sem_evento_verdadeiro_p_miss_e_indefinido():
    events = [ScoredEvent(score=5.0, is_true_event=False)]

    with pytest.raises(ValueError, match="nenhum evento verdadeiro"):
        p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.1)


def test_duracao_invalida_e_rejeitada():
    events = [ScoredEvent(score=5.0, is_true_event=True)]

    with pytest.raises(ValueError, match="video_minutes"):
        p_miss_at_rfa(events, video_minutes=0.0, target_rfa=0.1)


def test_lista_vazia_e_rejeitada():
    with pytest.raises(ValueError, match="nenhum evento verdadeiro"):
        p_miss_at_rfa([], video_minutes=10.0, target_rfa=0.1)


def test_operating_point_reporta_rfa_efetivo():
    events = [
        ScoredEvent(score=9.0, is_true_event=False),
        ScoredEvent(score=8.0, is_true_event=True),
    ]
    point: OperatingPoint = p_miss_at_rfa(events, video_minutes=10.0, target_rfa=0.1)

    assert point.rfa == pytest.approx(0.1)


def test_positivo_nunca_detectado_conta_no_p_miss():
    """O bug que so aparece ao conectar ground truth real.

    Derivar o total de positivos da lista de eventos ignora quem o sistema
    nunca detectou: o positivo perdido nao esta la para ser contado. O P_miss
    sai sistematicamente subestimado, e quanto pior o sistema, mais otimista o
    numero.
    """
    eventos = [ScoredEvent(score=9.0, is_true_event=True)]

    derivado = p_miss_at_rfa(eventos, video_minutes=10.0, target_rfa=0.1)
    real = p_miss_at_rfa(eventos, video_minutes=10.0, target_rfa=0.1, total_positives=4)

    assert derivado.p_miss == pytest.approx(0.0)
    assert real.p_miss == pytest.approx(0.75)


def test_total_positives_menor_que_os_detectados_e_rejeitado():
    eventos = [
        ScoredEvent(score=9.0, is_true_event=True),
        ScoredEvent(score=8.0, is_true_event=True),
    ]

    with pytest.raises(ValueError, match="total_positives"):
        p_miss_at_rfa(eventos, video_minutes=10.0, target_rfa=0.5, total_positives=1)


def test_total_positives_zero_e_rejeitado():
    with pytest.raises(ValueError, match="nenhum evento verdadeiro"):
        p_miss_at_rfa([], video_minutes=10.0, target_rfa=0.1, total_positives=0)
