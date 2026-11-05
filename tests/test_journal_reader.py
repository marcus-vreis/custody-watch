"""Contrato de leitura estrita e recuperação limitada do journal."""

import json
import warnings
from pathlib import Path

import pytest

from custody_watch.events import Event, EventKind
from custody_watch.journal_reader import JournalFormatError, iter_journal


def envelope(sequence=0, kind="segment_start", segment_id="segment_0", **changes):
    payload = (
        Event(EventKind.BAG_APPEARED, 0.0, 1.0, None, 4, None, {"m": 0.5}).to_dict()
        if kind == "event"
        else {"reason": "start" if kind == "segment_start" else "user_stop"}
    )
    result = {
        "schema_version": 1,
        "session_id": "session_0",
        "segment_id": segment_id,
        "sequence": sequence,
        "kind": kind,
        "payload": payload,
    }
    result.update(changes)
    return result


def write_part(directory, number, records, *, tail=b""):
    path = directory / f"{number:06d}.jsonl"
    path.write_bytes(b"".join((json.dumps(record) + "\n").encode() for record in records) + tail)
    return path


@pytest.mark.parametrize(
    "fields",
    [
        {"local_monotonic_s": True},
        {"local_monotonic_s": float("nan")},
        {"gap_start_s": "100"},
        {"gap_end_s": 110.0},
        {"gap_start_s": 110.0, "gap_end_s": 100.0},
    ],
)
def test_lifecycle_recusa_relogio_ou_lacuna_invalidos(tmp_path, fields):
    write_part(tmp_path, 0, [envelope(payload={"reason": "start", **fields})])
    with pytest.raises(JournalFormatError):
        list(iter_journal(tmp_path))


def test_multiplas_partes_continuam_segmento_e_permitem_reconexao(tmp_path):
    records = [
        envelope(),
        envelope(1, "event"),
        envelope(2, "segment_interrupted", payload={"reason": "read_failed"}),
        envelope(3, segment_id="segment_1", payload={"reason": "reconnected"}),
        envelope(4, "event", "segment_1"),
        envelope(5, "segment_end", "segment_1"),
    ]
    write_part(tmp_path, 0, records[:1])
    write_part(tmp_path, 1, records[1:4])
    write_part(tmp_path, 2, records[4:])
    diagnostics = []
    assert list(iter_journal(str(tmp_path), diagnostics=diagnostics)) == records
    assert diagnostics == []


def test_writer_ainda_ativo_retorna_prefixo_e_diagnostico(tmp_path):
    records = [envelope(), envelope(1, "event")]
    write_part(tmp_path, 0, records)
    diagnostics = []
    assert list(iter_journal(tmp_path, diagnostics=diagnostics)) == records
    assert len(diagnostics) == 1
    assert "segment_0" in diagnostics[0]


@pytest.mark.parametrize("tail", [b'{"schema', b"{}", b"\xff"])
def test_fragmento_final_sem_lf_e_ignorado_mesmo_se_json_valido(tmp_path, tail):
    records = [envelope(), envelope(1, "segment_end")]
    write_part(tmp_path, 0, records, tail=tail)
    diagnostics = []
    assert list(iter_journal(tmp_path, diagnostics=diagnostics)) == records
    assert len(diagnostics) == 1
    assert "000000.jsonl:3" in diagnostics[0]
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:3"):
        list(iter_journal(tmp_path, recover_truncated_tail=False))


def test_fragmento_sem_lf_em_parte_anterior_falha(tmp_path):
    write_part(tmp_path, 0, [envelope()], tail=b"{}")
    write_part(tmp_path, 1, [envelope(1, "segment_end")])
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:2"):
        list(iter_journal(tmp_path))


@pytest.mark.parametrize("line", [b"{invalido}\n", b"\n", b"\xff\n"])
def test_corrupcao_com_lf_nao_e_descartada(tmp_path, line):
    write_part(tmp_path, 0, [envelope()], tail=line)
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:2"):
        list(iter_journal(tmp_path))


def test_corrupcao_no_meio_interrompe_apos_prefixo_valido(tmp_path):
    part = write_part(tmp_path, 0, [envelope()], tail=b"{invalido}\n")
    with part.open("ab") as handle:
        handle.write((json.dumps(envelope(1, "segment_end")) + "\n").encode())
    reader = iter_journal(tmp_path)
    assert next(reader) == envelope()
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:2"):
        next(reader)


@pytest.mark.parametrize(
    "records",
    [
        [envelope(), envelope(0, "event")],
        [envelope(), envelope(2, "event")],
        [envelope(1)],
        [envelope(), envelope(1, "event", session_id="different")],
        [envelope(0, "event")],
        [envelope(), envelope(1, "segment_end"), envelope(2, "event")],
        [envelope(), envelope(1, "segment_end"), envelope(2)],
        [envelope(), envelope(1, segment_id="other")],
        [envelope(), envelope(1, "event", segment_id="other")],
        [envelope(), envelope(1, "segment_end", segment_id="other")],
    ],
)
def test_estado_sequencia_sessao_e_segmentos_invalidos(tmp_path, records):
    write_part(tmp_path, 0, records)
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:\d+"):
        list(iter_journal(tmp_path))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"schema_version": 1.0},
        {"sequence": True},
        {"sequence": -1},
        {"session_id": "com espaço"},
        {"segment_id": "x" * 65},
        {"session_id": None},
        {"kind": []},
        {"kind": "unknown"},
        {"payload": []},
        {"payload": {"reason": "read_failed"}},
        {"payload": {"reason": "start", "extra": 1}},
        {"extra": 1},
    ],
)
def test_envelope_e_lifecycle_exigem_contrato(tmp_path, changes):
    write_part(tmp_path, 0, [envelope(**changes)])
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:1"):
        list(iter_journal(tmp_path))


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "desconhecido"),
        ("kind", []),
        ("t_start", -1),
        ("t_start", True),
        ("t_start", "0"),
        ("t_end", -1),
        ("t_end", float("nan")),
        ("t_end", float("inf")),
        ("subject", True),
        ("bag", True),
        ("party", 1.5),
        ("evidence", []),
        ("evidence", {"nested": [float("inf")]}),
    ],
)
def test_evento_valida_tipos_tempos_ids_e_evidencia(tmp_path, field, value):
    event_record = envelope(1, "event")
    event_record["payload"][field] = value
    write_part(tmp_path, 0, [envelope(), event_record])
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:2"):
        list(iter_journal(tmp_path))


@pytest.mark.parametrize(
    "missing", ["kind", "t_start", "t_end", "subject", "bag", "party", "evidence"]
)
def test_evento_exige_todos_os_campos(tmp_path, missing):
    event_record = envelope(1, "event")
    del event_record["payload"][missing]
    write_part(tmp_path, 0, [envelope(), event_record])
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:2"):
        list(iter_journal(tmp_path))


def test_bag_apontada_pelo_operador_preserva_id_negativo(tmp_path):
    event_record = envelope(1, "event")
    event_record["payload"]["bag"] = -1
    event_record["payload"]["evidence"] = {"origem": "operador"}
    records = [envelope(), event_record, envelope(2, "segment_end")]
    write_part(tmp_path, 0, records)
    assert list(iter_journal(tmp_path)) == records


@pytest.mark.parametrize(
    "line",
    [
        '{"schema_version":1,"schema_version":1}',
        json.dumps(envelope()).replace('"reason": "start"', '"reason":"start","reason":"start"'),
        json.dumps(envelope()).replace('"reason": "start"', '"reason":1e999'),
    ],
)
def test_chaves_duplicadas_e_overflow_json_falham(tmp_path, line):
    (tmp_path / "000000.jsonl").write_text(line + "\n", encoding="utf-8")
    with pytest.raises(JournalFormatError, match=r"000000.jsonl:1"):
        list(iter_journal(tmp_path))


@pytest.mark.parametrize("numbers", [[], [1], [0, 2]])
def test_partes_ausentes_ou_com_lacunas_falham(tmp_path, numbers):
    for number in numbers:
        write_part(tmp_path, number, [])
    with pytest.raises(JournalFormatError):
        list(iter_journal(tmp_path))


def test_ultima_parte_renomeada_e_rejeitada(tmp_path):
    write_part(tmp_path, 0, [envelope()])
    renamed_part = write_part(tmp_path, 1, [envelope(1, "segment_end")])
    renamed_part.rename(tmp_path / "parte_final.jsonl")
    with pytest.raises(JournalFormatError, match=r"parte_final.jsonl"):
        list(iter_journal(tmp_path))


def test_arquivos_que_nao_sao_jsonl_podem_ser_ignorados(tmp_path):
    records = [envelope(), envelope(1, "segment_end")]
    write_part(tmp_path, 0, records)
    (tmp_path / "README.txt").write_text("nota local", encoding="utf-8")
    assert list(iter_journal(tmp_path)) == records


def test_fragmento_sem_coletor_emite_warning_com_mesmo_diagnostico(tmp_path):
    records = [envelope(), envelope(1, "segment_end")]
    write_part(tmp_path, 0, records, tail=b"{fragmento")
    diagnostics = []
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert list(iter_journal(tmp_path, diagnostics=diagnostics)) == records
    assert emitted == []
    with pytest.warns(UserWarning, match=r"000000.jsonl:3") as recovered:
        assert list(iter_journal(tmp_path)) == records
    assert len(recovered) == 1
    assert str(recovered[0].message) == diagnostics[0]


def test_leitura_incremental_nao_materializa_arquivos(tmp_path, monkeypatch):
    records = [envelope(), envelope(1, "segment_end")]
    write_part(tmp_path, 0, records)

    def forbidden(*args, **kwargs):
        raise AssertionError("Leitura integral proibida")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    reader = iter_journal(tmp_path)
    assert next(reader) == records[0]
    assert list(reader) == records[1:]
