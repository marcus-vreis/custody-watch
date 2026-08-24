import json

import pytest

from custody_watch.annotations import (
    GroundTruthEvent,
    load_annotations,
    match_events,
    save_annotations,
)
from custody_watch.events import Event, EventKind, EventLog


def verdade(t: float, kind: EventKind = EventKind.BAG_UNATTENDED, bag: int = 1):
    return GroundTruthEvent(kind=kind, t=t, bag=bag, subject=None, note="teste")


def detectado(t: float, kind: EventKind = EventKind.BAG_UNATTENDED, bag: int = 1):
    return Event(kind=kind, t_start=t, t_end=t, subject=None, bag=bag, party=None)


def log(*eventos) -> EventLog:
    registro = EventLog()
    for e in eventos:
        registro.emit(e)
    return registro


def test_round_trip_de_arquivo(tmp_path):
    eventos = [verdade(10.0), verdade(50.0, EventKind.BAG_REMOVED_BY_STRANGER, bag=2)]
    caminho = tmp_path / "a.json"

    save_annotations(eventos, caminho, session="ensaio-01")

    assert load_annotations(caminho) == eventos


def test_arquivo_exige_sessao_e_nota(tmp_path):
    """Daqui a seis meses ninguem lembra de que gravacao veio o arquivo."""
    caminho = tmp_path / "a.json"
    caminho.write_text(json.dumps({"events": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="session"):
        load_annotations(caminho)


def test_kind_desconhecido_falha_alto(tmp_path):
    caminho = tmp_path / "a.json"
    caminho.write_text(
        json.dumps({"session": "x", "events": [{"kind": "nao_existe", "t": 1.0}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_annotations(caminho)


# --- casamento com janela assimetrica -----------------------------------------


def test_deteccao_atrasada_dentro_da_janela_casa():
    """BAG_UNATTENDED dispara `unattended_time_s` DEPOIS do abandono fisico.

    Com o padrao de 25s, a anotacao diz 20s e o sistema emite aos 45s. Janela
    simetrica de poucos segundos marcaria tudo como perdido e espurio.
    """
    resultado = match_events(log(detectado(45.0)), [verdade(20.0)], lag_window_s=27.0)

    assert len(resultado.matched) == 1
    assert resultado.missed == []
    assert resultado.spurious == []


def test_deteccao_alem_da_janela_nao_casa():
    resultado = match_events(log(detectado(90.0)), [verdade(20.0)], lag_window_s=27.0)

    assert resultado.matched == []
    assert len(resultado.missed) == 1
    assert len(resultado.spurious) == 1


def test_deteccao_antes_do_fato_nao_casa():
    """Detectar abandono antes de a bagagem ser abandonada e incoerente."""
    resultado = match_events(log(detectado(10.0)), [verdade(20.0)], lag_window_s=27.0)

    assert resultado.matched == []
    assert len(resultado.spurious) == 1


def test_folga_pequena_antes_cobre_impressao_de_anotacao():
    resultado = match_events(
        log(detectado(19.5)), [verdade(20.0)], slack_before_s=1.0, lag_window_s=27.0
    )

    assert len(resultado.matched) == 1


def test_cada_anotado_casa_no_maximo_uma_vez():
    """Sem isso, emitir dez eventos em torno do instante certo daria dez acertos."""
    resultado = match_events(
        log(detectado(45.0), detectado(46.0), detectado(47.0)),
        [verdade(20.0)],
        lag_window_s=27.0,
    )

    assert len(resultado.matched) == 1
    assert len(resultado.spurious) == 2


def test_casa_pelo_mais_proximo_dentro_da_janela():
    resultado = match_events(
        log(detectado(46.0), detectado(44.5)), [verdade(20.0)], lag_window_s=27.0
    )

    _, escolhido = resultado.matched[0]
    assert escolhido.t_start == 44.5


def test_tipos_diferentes_nao_casam():
    resultado = match_events(
        log(detectado(45.0, EventKind.BAG_REMOVED_BY_STRANGER)),
        [verdade(20.0, EventKind.BAG_UNATTENDED)],
        lag_window_s=27.0,
    )

    assert resultado.matched == []


def test_filtra_por_tipo_de_interesse():
    """Eventos fora do escopo da avaliacao nao contam como espurios."""
    resultado = match_events(
        log(detectado(45.0), detectado(50.0, EventKind.PARTY_FORMED)),
        [verdade(20.0)],
        lag_window_s=27.0,
        kinds={EventKind.BAG_UNATTENDED},
    )

    assert len(resultado.matched) == 1
    assert resultado.spurious == []


def test_conta_positivos_totais():
    resultado = match_events(
        log(detectado(45.0)), [verdade(20.0), verdade(200.0)], lag_window_s=27.0
    )

    assert resultado.total_positives == 2
    assert len(resultado.missed) == 1


def test_sem_anotacao_e_sem_kinds_falha_alto():
    """Gravacao de controle, em que nada e furtado, e onde o falso alarme se
    mede. Derivar os tipos de uma lista vazia daria zero espurios em silencio,
    escondendo exatamente a metade da metrica que aquele material serve para
    medir."""
    with pytest.raises(ValueError, match="kinds"):
        match_events(log(detectado(45.0)), [], lag_window_s=27.0)


def test_gravacao_de_controle_conta_espurios_com_kinds_explicito():
    resultado = match_events(
        log(detectado(45.0)), [], lag_window_s=27.0, kinds={EventKind.BAG_UNATTENDED}
    )

    assert resultado.total_positives == 0
    assert len(resultado.spurious) == 1


def test_round_trip_preserva_fim_do_estado(tmp_path):
    """BAG_UNATTENDED e um estado, nao um instante: sem a duracao nao da para
    julgar se o sistema deveria te-lo emitido."""
    eventos = [GroundTruthEvent(kind=EventKind.BAG_UNATTENDED, t=20.0, t_end=95.0, note="x")]
    caminho = tmp_path / "a.json"

    save_annotations(eventos, caminho, session="s")

    assert load_annotations(caminho)[0].t_end == 95.0


def test_estado_sem_fim_carrega_como_none(tmp_path):
    """None nao e zero: significa que o estado ainda valia quando o material
    acabou, e a duracao e um limite inferior."""
    caminho = tmp_path / "a.json"
    caminho.write_text(
        json.dumps({"session": "s", "events": [{"kind": "bag_unattended", "t": 20.0}]}),
        encoding="utf-8",
    )

    assert load_annotations(caminho)[0].t_end is None


def estado(t: float, t_end: float | None, truncated: bool = False) -> GroundTruthEvent:
    return GroundTruthEvent(kind=EventKind.BAG_UNATTENDED, t=t, t_end=t_end, truncated=truncated)


def test_estado_longo_o_bastante_e_positivo():
    assert estado(20.0, 60.0).sustained_for(25.0) is True


def test_estado_curto_que_acabou_de_verdade_nao_e_instancia():
    """O dono voltou aos 30s. Um abandono de 10s nao e o evento que o sistema
    afirma detectar com limiar de 25s, entao nao e positivo perdido."""
    assert estado(20.0, 30.0).sustained_for(25.0) is False


def test_estado_curto_e_truncado_nao_tem_resposta():
    """A camera parou aos 30s. O abandono pode ter durado uma hora. Contar como
    perdido inventaria um P_miss ruim a partir do comprimento do clipe."""
    assert estado(20.0, 30.0, truncated=True).sustained_for(25.0) is None


def test_truncado_que_ja_passou_do_limiar_e_positivo():
    """O que veio depois do corte nao muda o que ja aconteceu antes dele."""
    assert estado(20.0, 60.0, truncated=True).sustained_for(25.0) is True


def test_round_trip_preserva_truncamento(tmp_path):
    caminho = tmp_path / "a.json"
    save_annotations([estado(20.0, 30.0, truncated=True)], caminho, session="s")

    assert load_annotations(caminho) == [estado(20.0, 30.0, truncated=True)]
