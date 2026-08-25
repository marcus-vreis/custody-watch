import json

import pytest

from custody_watch.events import Event, EventKind, EventLog


def make_event(kind: EventKind = EventKind.BAG_UNATTENDED, t_start: float = 10.0) -> Event:
    return Event(
        kind=kind,
        t_start=t_start,
        t_end=t_start + 5.0,
        subject=7,
        bag=100,
        party=1,
        evidence={"distance_m": 4.2, "elapsed_s": 26.0},
    )


def test_evento_e_um_intervalo_nao_um_instante():
    """A unidade do sistema é a janela temporal, não o frame."""
    event = make_event(t_start=10.0)

    assert event.t_end > event.t_start
    assert event.duration_s == pytest.approx(5.0)


def test_intervalo_invertido_e_rejeitado():
    with pytest.raises(ValueError, match="t_end"):
        Event(
            kind=EventKind.BAG_UNATTENDED,
            t_start=10.0,
            t_end=5.0,
            subject=1,
            bag=1,
            party=None,
            evidence={},
        )


def test_instante_pontual_e_permitido():
    event = Event(
        kind=EventKind.BAG_APPEARED,
        t_start=3.0,
        t_end=3.0,
        subject=None,
        bag=5,
        party=None,
        evidence={},
    )
    assert event.duration_s == 0.0


def test_round_trip_json():
    original = make_event()
    assert Event.from_dict(original.to_dict()) == original


def test_to_dict_e_serializavel_em_json():
    payload = json.dumps(make_event().to_dict())
    assert json.loads(payload)["kind"] == EventKind.BAG_UNATTENDED.value


def test_kind_desconhecido_na_desserializacao_falha_alto():
    payload = make_event().to_dict()
    payload["kind"] = "evento_que_nao_existe"

    with pytest.raises(ValueError):
        Event.from_dict(payload)


def test_evidencia_carrega_os_numeros_da_decisao():
    """Sem os números, o operador não consegue contestar o alerta."""
    event = make_event()

    assert event.evidence["distance_m"] == 4.2
    assert event.evidence["elapsed_s"] == 26.0


def test_log_preserva_ordem_de_emissao():
    log = EventLog()
    log.emit(make_event(t_start=30.0))
    log.emit(make_event(t_start=10.0))

    assert [e.t_start for e in log] == [30.0, 10.0]


def test_log_filtra_por_tipo():
    log = EventLog()
    log.emit(make_event(EventKind.BAG_UNATTENDED))
    log.emit(make_event(EventKind.BAG_REMOVED_BY_STRANGER))

    encontrados = log.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)
    assert [e.kind for e in encontrados] == [EventKind.BAG_REMOVED_BY_STRANGER]


def test_log_round_trip_jsonl(tmp_path):
    """Replay e auditoria dependem disto."""
    log = EventLog()
    log.emit(make_event(EventKind.BAG_UNATTENDED, t_start=10.0))
    log.emit(make_event(EventKind.BAG_REMOVED_BY_STRANGER, t_start=40.0))

    caminho = tmp_path / "eventos.jsonl"
    log.to_jsonl(caminho)

    assert list(EventLog.from_jsonl(caminho)) == list(log)


def test_jsonl_tem_uma_linha_por_evento(tmp_path):
    """Formato append-only e streamável, não um array JSON gigante."""
    log = EventLog()
    for i in range(3):
        log.emit(make_event(t_start=float(i)))

    caminho = tmp_path / "eventos.jsonl"
    log.to_jsonl(caminho)

    linhas = caminho.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 3
    assert all(json.loads(linha)["kind"] for linha in linhas)


def test_log_vazio_gera_arquivo_vazio(tmp_path):
    caminho = tmp_path / "vazio.jsonl"
    EventLog().to_jsonl(caminho)

    assert caminho.read_text(encoding="utf-8") == ""
    assert list(EventLog.from_jsonl(caminho)) == []


def test_jsonl_ignora_linhas_em_branco(tmp_path):
    caminho = tmp_path / "com_brancos.jsonl"
    evento = json.dumps(make_event().to_dict())
    caminho.write_text(f"\n{evento}\n\n", encoding="utf-8")

    assert len(list(EventLog.from_jsonl(caminho))) == 1


def test_len_do_log():
    log = EventLog()
    log.emit(make_event())
    log.emit(make_event())

    assert len(log) == 2


def test_bag_occluded_e_intervalo_serializavel():
    """A oclusao e um intervalo, nao um instante: o que interessa e quanto
    tempo a bagagem ficou invisivel e quem esteve nela nesse periodo."""
    evento = Event(
        kind=EventKind.BAG_OCCLUDED,
        t_start=10.0,
        t_end=12.5,
        subject=None,
        bag=7,
        party=1,
        evidence={"duration_s": 2.5, "candidates": [3]},
    )

    assert evento.duration_s == 2.5
    assert Event.from_dict(evento.to_dict()) == evento
