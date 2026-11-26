"""CLI unificada: despacho para scripts/ sem importar dependências pesadas."""

import subprocess
import sys
from pathlib import Path

import pytest

from custody_watch import cli

RAIZ = Path(__file__).resolve().parents[1]
SUBCOMANDOS = {
    "watch": "watch_video.py",
    "serve": "serve.py",
    "replay": "replay_multicamera.py",
    "lab": "serve_multicamera.py",
    "evaluate": "evaluate_recorded.py",
    "webhook": "export_webhook.py",
}


@pytest.fixture
def chamadas(monkeypatch):
    """Substitui runpy.run_path e registra path, run_name e sys.argv."""
    registro = []

    def falso(path, run_name=None):
        registro.append({"path": Path(path), "run_name": run_name, "argv": list(sys.argv)})
        return {}

    monkeypatch.setattr(cli.runpy, "run_path", falso)
    return registro


def test_ajuda_lista_todos_os_subcomandos(capsys):
    assert cli.main(["--help"]) == 0
    saida = capsys.readouterr().out
    for nome in [*SUBCOMANDOS, "demo"]:
        assert nome in saida


def test_sem_argumentos_lista_subcomandos(capsys):
    assert cli.main([]) == 0
    assert "demo" in capsys.readouterr().out


def test_subcomando_desconhecido_falha_com_mensagem(capsys):
    codigo = cli.main(["inexistente"])
    assert codigo != 0
    erro = capsys.readouterr().err
    assert "inexistente" in erro


@pytest.mark.parametrize(("nome", "script"), SUBCOMANDOS.items())
def test_argumentos_sao_repassados_intactos(chamadas, nome, script):
    assert cli.main([nome, "--port", "9", "entrada.json", "--flag"]) == 0
    (chamada,) = chamadas
    assert chamada["path"] == RAIZ / "scripts" / script
    assert chamada["run_name"] == "__main__"
    assert chamada["argv"] == [str(chamada["path"]), "--port", "9", "entrada.json", "--flag"]


@pytest.mark.parametrize("nome", ["serve", "lab", "demo"])
def test_allowed_host_chega_ao_script(chamadas, nome):
    assert cli.main([nome, "--allowed-host", "a.local", "--allowed-host", "b.local"]) == 0
    (chamada,) = chamadas
    assert chamada["argv"][-4:] == ["--allowed-host", "a.local", "--allowed-host", "b.local"]


def test_help_do_subcomando_vai_para_o_script(chamadas):
    cli.main(["serve", "--help"])
    assert chamadas[0]["argv"][1:] == ["--help"]


def test_sys_argv_restaurado_apos_execucao(chamadas):
    antes = list(sys.argv)
    cli.main(["serve", "--x"])
    assert sys.argv == antes


def test_sys_argv_restaurado_mesmo_com_excecao(monkeypatch):
    def explode(path, run_name=None):
        raise RuntimeError("falha do script")

    monkeypatch.setattr(cli.runpy, "run_path", explode)
    antes = list(sys.argv)
    with pytest.raises(RuntimeError):
        cli.main(["serve"])
    assert sys.argv == antes


def test_system_exit_do_script_vira_codigo_de_retorno(monkeypatch):
    def sai(path, run_name=None):
        raise SystemExit(3)

    monkeypatch.setattr(cli.runpy, "run_path", sai)
    assert cli.main(["serve"]) == 3


def test_system_exit_sem_codigo_vira_zero(monkeypatch):
    def sai(path, run_name=None):
        raise SystemExit

    monkeypatch.setattr(cli.runpy, "run_path", sai)
    assert cli.main(["serve"]) == 0


def test_system_exit_com_mensagem_vira_um(monkeypatch, capsys):
    def sai(path, run_name=None):
        raise SystemExit("motivo")

    monkeypatch.setattr(cli.runpy, "run_path", sai)
    assert cli.main(["serve"]) == 1
    assert "motivo" in capsys.readouterr().err


def test_ausencia_de_scripts_vira_codigo_2_com_mensagem(monkeypatch, tmp_path, chamadas, capsys):
    monkeypatch.setattr(cli, "SCRIPTS_DIR", tmp_path / "nao-existe")
    assert cli.main(["serve"]) == 2
    erro = capsys.readouterr().err
    assert "scripts" in erro
    assert "Traceback" not in erro
    assert chamadas == []


def test_demo_resolve_cadastro_do_fixture_existente(chamadas):
    assert cli.main(["demo", "--port", "9999"]) == 0
    (chamada,) = chamadas
    assert chamada["path"] == RAIZ / "scripts" / "serve_multicamera.py"
    registry = Path(chamada["argv"][1])
    assert registry == RAIZ / "tests" / "fixtures" / "multicamera" / "two-cameras" / "registry.json"
    assert registry.is_file()
    assert chamada["argv"][2:] == ["--port", "9999"]


def test_demo_sem_fixture_vira_codigo_2(monkeypatch, tmp_path, chamadas, capsys):
    monkeypatch.setattr(cli, "DEMO_REGISTRY", tmp_path / "ausente.json")
    assert cli.main(["demo"]) == 2
    assert "demo" in capsys.readouterr().err
    assert chamadas == []


def test_importar_cli_nao_carrega_cv2():
    codigo = (
        "import sys; import custody_watch.cli; "
        "pesados = [m for m in ('cv2', 'ultralytics', 'torch') if m in sys.modules]; "
        "sys.exit(1 if pesados else 0)"
    )
    resultado = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert resultado.returncode == 0, resultado.stderr


def test_integracao_replay_help_real(capsys):
    assert cli.main(["replay", "--help"]) == 0
    saida = capsys.readouterr().out
    assert "usage" in saida.lower()
    assert "registry" in saida.lower()
