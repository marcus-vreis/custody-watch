"""Varredura de envelope (#134): seeds explícitas, determinismo e JSON com proveniência.

Os testes não usam o CAVIAR, que não está no repositório: dois cenários
sintéticos com abandono fazem o papel dos clipes.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from custody_watch.config import Config
from custody_watch.scenarios import build_scenarios, identity_plane

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "envelope.py"
_spec = importlib.util.spec_from_file_location("envelope_script", SCRIPT)
envelope = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = envelope  # dataclass resolve anotações pelo módulo registrado
_spec.loader.exec_module(envelope)

PEQUENA = (
    envelope.Varredura("bagagem_falha", "BAGAGEM", "bag", "drop_rate", (0.0, 0.8)),
    envelope.Varredura("pessoa_posicao", "PESSOA", "person", "position_sigma_px", (2.0,)),
)


@pytest.fixture(scope="module")
def clipes():
    cenarios = {s.name: s for s in build_scenarios()}
    return {nome: list(cenarios[nome].frames(3.0)) for nome in ("owner_returns", "stranger_takes")}


def varre(clipes, seeds, varreduras=PEQUENA):
    return envelope.varrer(clipes, identity_plane(), Config(), seeds, varreduras)


def test_mesmas_seeds_mesmo_resultado(clipes):
    primeira = varre(clipes, [3, 7])
    segunda = varre(clipes, [3, 7])

    assert primeira == segunda


def test_execucao_limpa_nao_depende_da_seed(clipes):
    a = varre(clipes, [3])
    b = varre(clipes, [4])
    assert a["execucao_limpa"] == b["execucao_limpa"]
    # O ponto 0% de falha é a execução limpa repetida: tem que bater com a base.
    assert (
        a["varreduras"][0]["pontos"][0]["por_seed"][0]["abandonos"]
        == (a["execucao_limpa"]["abandonos"])
    )


def test_resultado_tem_um_registro_por_seed_e_por_ponto(clipes):
    resultado = varre(clipes, [1, 2, 5])
    assert resultado["execucao_limpa"]["abandonos"] == 2
    assert [v["id"] for v in resultado["varreduras"]] == ["bagagem_falha", "pessoa_posicao"]
    pontos = resultado["varreduras"][0]["pontos"]
    assert [p["valor"] for p in pontos] == [0.0, 0.8]
    for ponto in pontos:
        assert ponto["n_runs"] == 3
        assert [r["seed"] for r in ponto["por_seed"]] == [1, 2, 5]
        assert ponto["retidos_min"] <= ponto["retidos_media"] <= ponto["retidos_max"]
    limpo = pontos[0]
    assert limpo["retidos_media"] == 1.0
    assert limpo["veredito"] == "opera"
    assert limpo["bagagem"]["drop_burst_frames"] == envelope.RAJADA_QUADROS
    assert pontos[1]["bagagem"]["drop_rate"] == 0.8
    assert pontos[1]["pessoa"] == {
        "drop_rate": 0.0,
        "drop_burst_frames": 1,
        "position_sigma_px": 0.0,
        "id_switch_rate": 0.0,
    }


@pytest.mark.parametrize(
    "seeds",
    [[], [1, 1], [-1], [envelope.SEED_MAXIMA + 1], [True], [1.5]],
)
def test_recusa_seeds_invalidas(seeds):
    with pytest.raises(ValueError):
        envelope.valida_seeds(seeds)


@pytest.mark.parametrize(
    "varredura",
    [
        envelope.Varredura("x", "x", "bag", "drop_rate", (1.5,)),
        envelope.Varredura("x", "x", "bag", "drop_rate", (float("nan"),)),
        envelope.Varredura("x", "x", "bag", "position_sigma_px", (-1.0,)),
        envelope.Varredura("x", "x", "bag", "id_switch_rate", (0.1,)),
        envelope.Varredura("x", "x", "carro", "drop_rate", (0.1,)),
        envelope.Varredura("x", "x", "bag", "drop_rate", ()),
    ],
)
def test_recusa_varredura_invalida(clipes, varredura):
    with pytest.raises(ValueError):
        varre(clipes, [1], [varredura])


def test_recusa_sem_clipes():
    with pytest.raises(ValueError):
        envelope.varrer({}, identity_plane(), Config(), [1], PEQUENA)


def test_veredito_poe_silencio_antes_de_falso_alarme():
    assert envelope.veredito(0.4, 0.0, 1.0).startswith("MUDO")
    assert envelope.veredito(0.9, 2.0, 1.0).startswith("ESTOURA")
    assert envelope.veredito(0.9, 0.5, 1.0) == "opera"
    assert envelope.veredito(None, 0.0, 1.0).startswith("SEM BASE")


def test_proveniencia_traz_commit_seeds_runs_e_alcance(tmp_path):
    doc = envelope.proveniencia(["--seeds", "4", "9"], tmp_path, Config(), (4, 9), 12)
    json.dumps(doc)  # serializável
    assert doc["schema_version"] == 1
    assert len(doc["commit"]["sha"]) == 40
    assert isinstance(doc["commit"]["sujo"], bool)
    assert len(doc["codigo_sha256"]) == 64
    assert doc["seeds"] == [4, 9]
    assert doc["n_runs_por_ponto"] == 2
    assert doc["ruido"]["drop_burst_frames"] == 12
    assert "posições sintéticas" in doc["alcance"]
    assert "Não demonstra detector" in doc["alcance"]
    assert "associação calculada" in doc["alcance"]
    assert doc["comando"][-3:] == ["--seeds", "4", "9"]
    # Dataset ausente vira hash nulo, não um hash inventado.
    assert all(h is None for clipe in doc["dataset"]["sha256"].values() for h in clipe.values())


def roda(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.mark.parametrize(
    "args",
    [["--seeds", "1", "1"], ["--seeds", "-3"], ["--seeds", "abc"], ["--varreduras", "nada"]],
)
def test_cli_recusa_entrada_invalida(tmp_path, args):
    execucao = roda(*args, "--data", str(tmp_path))
    assert execucao.returncode == 2, execucao.stderr


def test_cli_recusa_sobrescrever(tmp_path):
    saida = tmp_path / "ja_existe.json"
    saida.write_text("{}", encoding="utf-8")
    execucao = roda("--output", str(saida), "--data", str(tmp_path))
    assert execucao.returncode == 2
    assert "recuso sobrescrever" in execucao.stderr
    assert saida.read_text(encoding="utf-8") == "{}"


def test_cli_sem_dataset_falha_sem_gravar(tmp_path):
    saida = tmp_path / "res.json"
    execucao = roda("--output", str(saida), "--data", str(tmp_path / "nao_existe"))
    assert execucao.returncode == 1
    assert not saida.exists()
