"""Porta de entrada única: despacha para os scripts em ``scripts/``.

Não importa cv2/ultralytics/torch: ``--help`` precisa ser instantâneo. Os
scripts rodam via ``runpy`` com o ``sys.argv`` original deles, então a ajuda
de cada subcomando é a do próprio script.
"""

import runpy
import sys
from importlib import metadata
from pathlib import Path

# Em instalação por wheel este diretório não existe; ver _executa.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
DEMO_REGISTRY = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "multicamera"
    / "two-cameras"
    / "registry.json"
)

# nome -> (script, descrição)
SUBCOMANDOS: dict[str, tuple[str, str]] = {
    "watch": ("watch_video.py", "analisa um vídeo e mostra a custódia de bagagem"),
    "serve": ("serve.py", "painel web para uma fonte de vídeo"),
    "replay": ("replay_multicamera.py", "reproduz o cadastro multicâmera offline e exporta resumo"),
    "lab": ("serve_multicamera.py", "laboratório multicâmera visual (recebe o cadastro)"),
    "evaluate": ("evaluate_recorded.py", "avalia predições gravadas contra o gabarito"),
    "demo": ("serve_multicamera.py", "laboratório multicâmera com o fixture sintético, sem GPU"),
    "webhook": ("export_webhook.py", "entrega eventos do journal a um webhook (processo à parte)"),
}


def _versao() -> str:
    try:
        return metadata.version("custody-watch")
    except metadata.PackageNotFoundError:
        return "desconhecida (pacote não instalado)"


def _ajuda() -> str:
    linhas = ["uso: custody-watch <subcomando> [argumentos do script]", "", "subcomandos:"]
    linhas += [f"  {nome:<9} {desc}" for nome, (_, desc) in SUBCOMANDOS.items()]
    linhas += ["", "Use 'custody-watch <subcomando> --help' para a ajuda do script."]
    return "\n".join(linhas)


def _erro(mensagem: str) -> int:
    print(f"custody-watch: {mensagem}", file=sys.stderr)
    return 2


def _codigo_de_saida(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(exc.code, file=sys.stderr)
    return 1


def _executa(script: Path, argumentos: list[str]) -> int:
    if not script.is_file():
        return _erro(
            f"script não encontrado: {script}. A pasta scripts/ só existe em um "
            "checkout do repositório (não vem na instalação por wheel)."
        )
    anterior = sys.argv
    sys.argv = [str(script), *argumentos]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return _codigo_de_saida(exc)
    finally:
        sys.argv = anterior
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_ajuda())
        return 0
    if args[0] == "--version":
        print(f"custody-watch {_versao()}")
        return 0
    nome, resto = args[0], args[1:]
    if nome not in SUBCOMANDOS:
        return _erro(f"subcomando desconhecido: {nome!r}\n\n{_ajuda()}")
    if nome == "demo":
        if not DEMO_REGISTRY.is_file():
            return _erro(f"demo indisponível: fixture ausente em {DEMO_REGISTRY}")
        resto = [str(DEMO_REGISTRY), *resto]
    return _executa(SCRIPTS_DIR / SUBCOMANDOS[nome][0], resto)
