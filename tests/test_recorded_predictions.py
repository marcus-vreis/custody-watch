import json

import pytest

from custody_watch.recorded_predictions import load_recording
from tests.recorded_fixture import make_recording, reference, rewrite_cache, write_json


def test_cache_valido_preserva_caixas_relogio_e_procedencia(tmp_path):
    recording = load_recording(make_recording(tmp_path))

    assert recording.manifest["source"]["kind"] == "synthetic"
    assert recording.frames[5].t == 5.0
    assert recording.frames[5].detections[0].bbox == (1100.0, 0.0, 1200.0, 200.0)
    assert recording.config.custody.unattended_time_s == 25.0
    assert recording.truth[0].t == 5.0


@pytest.mark.parametrize("field", ["predictions", "annotations", "config"])
def test_hash_adulterado_e_recusado_antes_da_sessao(tmp_path, field):
    manifest_path = make_recording(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    (tmp_path / manifest[field]["path"]).write_text("adulterado", encoding="utf-8")

    with pytest.raises(ValueError, match="hash|tamanho"):
        load_recording(manifest_path)


@pytest.mark.parametrize("version", [2, True, "1"])
def test_versao_de_manifest_desconhecida_e_recusada(tmp_path, version):
    path = make_recording(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["schema_version"] = version
    write_json(path, manifest)

    with pytest.raises(ValueError, match="schema_version"):
        load_recording(path)


@pytest.mark.parametrize(
    "mutation,diagnostic",
    [
        (lambda rows: rows[1].update(t=0.0), "timestamp"),
        (lambda rows: rows[1].update(t=1000.0), "timestamp"),
        (lambda rows: rows[1].update(t=float("nan")), "JSON|finito"),
        (lambda rows: rows[1].update(frame_index=0), "frame_index"),
        (lambda rows: rows[1].update(schema_version=2), "schema_version"),
        (lambda rows: rows[1]["detections"][0].update(cls="chair"), "classe"),
        (lambda rows: rows[1]["detections"][0].update(bbox=[2, 0, 1, 4]), "bbox"),
        (lambda rows: rows[1].update(status="missing"), "missing"),
        (lambda rows: rows[1]["detections"].append(rows[1]["detections"][0]), "track_id"),
    ],
)
def test_cache_incompativel_nao_chega_ao_pipeline(tmp_path, mutation, diagnostic):
    path = make_recording(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    mutation(rows)
    rewrite_cache(tmp_path, rows)

    with pytest.raises(ValueError, match=diagnostic):
        load_recording(path)


def test_oracle_nao_pode_ser_declarado_como_predicao(tmp_path):
    path = make_recording(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["predictions"]["provenance"] = "ground_truth"
    write_json(path, manifest)

    with pytest.raises(ValueError, match="oracle"):
        load_recording(path)


def test_anotacao_de_outra_sequencia_e_recusada(tmp_path):
    path = make_recording(tmp_path)
    write_json(tmp_path / "annotations.json", {"session": "outra", "events": []})
    manifest = json.loads(path.read_text())
    manifest["annotations"] = reference(tmp_path / "annotations.json")
    write_json(path, manifest)

    with pytest.raises(ValueError, match="session"):
        load_recording(path)


def test_frame_vazio_e_frame_ausente_continuam_distintos(tmp_path):
    path = make_recording(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    rows[2]["detections"] = []
    rows[3].update(status="missing", detections=[])
    del rows[4]
    rewrite_cache(tmp_path, rows)

    recording = load_recording(path)

    assert recording.frame_counts == {"expected": 46, "observed": 44, "empty": 1, "missing": 2}
    assert len(list(recording.observations())) == 44


def test_alteracao_com_mesmo_tamanho_tambem_falha_no_hash(tmp_path):
    path = make_recording(tmp_path)
    cache = tmp_path / "predictions.jsonl"
    before = cache.read_bytes()
    cache.write_bytes(before.replace(b'"track_id": 9', b'"track_id": 8', 1))

    with pytest.raises(ValueError, match="hash"):
        load_recording(path)


def test_video_offline_tem_identidade_mas_nao_e_declarado_verificado(tmp_path):
    path = make_recording(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["source"]["file"]["path"] = None
    write_json(path, manifest)

    recording = load_recording(path)

    assert recording.source_verified is False
    assert recording.manifest["source"]["file"]["sha256"]


@pytest.mark.parametrize(
    "change,diagnostic",
    [
        (lambda m: m["clock"].update(end_s=0), "clock.end_s"),
        (lambda m: m["evaluation"].update(kinds=[{}]), "evaluation.kinds"),
        (lambda m: m["geometry"].update(metres_per_pixel=0), "escala"),
        (lambda m: m.update(classes=["chair"]), "classes"),
        (lambda m: m["producer"].update(seeds={"noise": True}), "seed"),
    ],
)
def test_manifesto_invalido_da_diagnostico(tmp_path, change, diagnostic):
    path = make_recording(tmp_path)
    manifest = json.loads(path.read_text())
    change(manifest)
    write_json(path, manifest)

    with pytest.raises(ValueError, match=diagnostic):
        load_recording(path)


def test_calibracao_medida_e_validada_e_preservada(tmp_path):
    path = make_recording(tmp_path)
    calibration_path = tmp_path / "calibration.json"
    write_json(
        calibration_path,
        {
            "camera": "synthetic",
            "note": "Pontos sintéticos exatos, sem câmera real.",
            "correspondences": [
                {"pixel": [x, y], "world": [x / 100, y / 100]}
                for x, y in [(0, 0), (100, 0), (0, 100), (100, 100)]
            ],
        },
    )
    manifest = json.loads(path.read_text())
    manifest["geometry"] = {"kind": "calibration", "file": reference(calibration_path)}
    write_json(path, manifest)

    recording = load_recording(path)

    projected = recording.plane.project(50, 50)
    assert projected.x == pytest.approx(0.5)
    assert projected.y == pytest.approx(0.5)


def test_assinatura_de_sessao_e_veto_de_borda_nao_somem_no_replay(tmp_path):
    from custody_watch.reid import SIGNATURE_SIZE

    path = make_recording(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    rows[0]["detections"][0]["appearance"] = [0.1] * SIGNATURE_SIZE
    rows[0]["detections"][1]["touches_bottom"] = True
    rewrite_cache(tmp_path, rows)

    recording = load_recording(path)

    assert recording.frames[0].detections[0].appearance.signature == (0.1,) * SIGNATURE_SIZE
    assert recording.frames[0].detections[1].touches_bottom is True


def test_coordenada_textual_nao_finita_na_calibracao_e_recusada(tmp_path):
    path = make_recording(tmp_path)
    calibration = tmp_path / "calibration.json"
    write_json(
        calibration,
        {
            "camera": "synthetic",
            "note": "Medição inválida de teste.",
            "correspondences": [
                {"pixel": [x, y], "world": ["NaN", y / 100]}
                for x, y in [(0, 0), (100, 0), (0, 100), (100, 100)]
            ],
        },
    )
    manifest = json.loads(path.read_text())
    manifest["geometry"] = {"kind": "calibration", "file": reference(calibration)}
    write_json(path, manifest)

    with pytest.raises(ValueError, match="calibration|finito"):
        load_recording(path)
