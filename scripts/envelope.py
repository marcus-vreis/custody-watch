#!/usr/bin/env python3
"""Varre a qualidade de percepção e acha onde a lógica deixa de operar.

A pergunta não é "qual é o ruído do nosso detector" — para bagagem não há
amostra que responda, uma detecção pareada em 1.686. A pergunta é **quão bom o
detector precisa ficar**, e é ela que a gravação encenada precisa responder.

## Um eixo por vez

A primeira versão desta varredura fixava o ruído de pessoa no valor medido e
varria só a bagagem. Não funcionou, e o motivo é o resultado: **o ruído de
pessoa medido já destrói o pipeline sozinho.** Com 69,5% de falha, metade das
bagagens nunca ganha dono, e bagagem órfã não acumula tempo desacompanhado.
O eixo varrido não tinha o que mostrar, porque o eixo fixo tinha chegado antes.

Então cada eixo é varrido com o outro **limpo**. Isso é irreal de propósito:
o objetivo é o requisito por eixo, não o comportamento num ponto de operação.

## O modo de falha que importa

A primeira versão também mediu a coisa errada. Sob ruído o sistema quase não
inventa furto — ele **fica mudo**. Falso alarme por minuto fica em zero em
quase toda a tabela, e um zero por silêncio lê como sucesso.

Então a coluna que decide é `retidos`: quantos dos eventos verdadeiros da
execução limpa sobrevivem. Falso alarme continua reportado, porque quando ele
aparece importa muito — mas ele não é onde a degradação mora.

## Proveniência (#134)

Os números desta varredura chegaram ao histórico sem seed, sem nº de runs e
sem arquivo de resultado. Agora as seeds são explícitas (`--seeds`) e
`--output` grava JSON com commit, árvore suja, seeds, parâmetros de ruído,
resultado por ponto e por seed, e o alcance. Mesmas seeds, mesmo resultado.

    .venv/Scripts/python.exe scripts/envelope.py --seeds 1 2 3 4 5 --output outputs/envelope.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics as st
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from custody_watch.caviar import (
    SCENARIOS,
    annotated_config,
    estimate_metres_per_pixel,
    ground_plane,
    load_clip,
)
from custody_watch.config import Config, load_config
from custody_watch.events import EventKind
from custody_watch.ground_plane import GroundPlane
from custody_watch.noise import NoiseModel, degrade
from custody_watch.orchestrator import run_session
from custody_watch.tracking import TrackedDetection

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data" / "caviar"
CONFIG = RAIZ / "config" / "caviar.json"

SCHEMA_VERSION = 1
MODO = "CAVIAR anotado + ruído sintético declarado"
ALCANCE = (
    "posições sintéticas: as caixas vêm do gabarito do CAVIAR e o ruído é injetado "
    "por custody_watch.noise; mede a degradação da LÓGICA de custódia sob ruído "
    "declarado. Não demonstra detector, rastreador nem associação calculada, e não "
    "substitui a gravação encenada"
)

TAXAS_DE_FALHA = (0.0, 0.05, 0.10, 0.20, 0.40, 0.80)
ERROS_DE_POSICAO_PX = (0.0, 1.0, 2.0, 4.0, 8.0)
SEEDS_PADRAO = (1, 2, 3, 4, 5, 6, 7, 8)
SEED_MAXIMA = 1_000_000
"""Cada clipe usa `seed * 100 + índice`; o teto mantém isso longe de estouro."""
RAJADA_QUADROS = 12
LIMPO = NoiseModel()
LIMIAR_MUDO = 0.5

Quadro = tuple[float, list[TrackedDetection]]
Clipes = Mapping[str, Sequence[Quadro]]


@dataclass(frozen=True)
class Varredura:
    id: str
    titulo: str
    eixo: str
    campo: str
    valores: tuple[float, ...]


VARREDURAS = (
    Varredura("pessoa_falha", "PESSOA — falha de deteccao", "person", "drop_rate", TAXAS_DE_FALHA),
    Varredura(
        "pessoa_posicao",
        "PESSOA — erro de posicao",
        "person",
        "position_sigma_px",
        ERROS_DE_POSICAO_PX,
    ),
    Varredura("bagagem_falha", "BAGAGEM — falha de deteccao", "bag", "drop_rate", TAXAS_DE_FALHA),
    Varredura(
        "bagagem_posicao",
        "BAGAGEM — erro de posicao",
        "bag",
        "position_sigma_px",
        ERROS_DE_POSICAO_PX,
    ),
)
POR_ID = {v.id: v for v in VARREDURAS}


@dataclass(frozen=True)
class Passada:
    falsos: int
    posses: int
    abandonos: int
    minutos: float


def valida_seeds(seeds: Sequence[int]) -> tuple[int, ...]:
    """Recusa o que tornaria o resultado irreprodutível ou ambíguo."""
    if not seeds:
        raise ValueError("pelo menos uma seed é obrigatória")
    for seed in seeds:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"seed deve ser inteiro, recebido {seed!r}")
        if not 0 <= seed <= SEED_MAXIMA:
            raise ValueError(f"seed deve estar em [0, {SEED_MAXIMA}], recebido {seed}")
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"seeds repetidas contariam a mesma execução duas vezes: {list(seeds)}")
    return tuple(seeds)


def valida_varredura(varredura: Varredura) -> None:
    if varredura.eixo not in ("person", "bag"):
        raise ValueError(f"eixo deve ser 'person' ou 'bag', recebido {varredura.eixo!r}")
    if varredura.campo not in ("drop_rate", "position_sigma_px"):
        raise ValueError(f"campo não varrível: {varredura.campo!r}")
    if not varredura.valores:
        raise ValueError(f"varredura {varredura.id!r} sem valores")
    for valor in varredura.valores:
        valido = 0.0 <= valor <= 1.0 if varredura.campo == "drop_rate" else 0.0 <= valor < 1e6
        if not valido:
            raise ValueError(f"{varredura.campo}={valor!r} fora da faixa em {varredura.id!r}")


def semente_do_clipe(seed: int, indice: int) -> int:
    """O esquema de d2abbea, mantido para os números continuarem comparáveis."""
    return seed * 100 + indice


def uma_passada(
    clipes: Clipes,
    person: NoiseModel,
    bag: NoiseModel,
    seed: int,
    plane: GroundPlane,
    config: Config,
) -> Passada:
    falsos = posses = abandonos = 0
    minutos = 0.0
    for indice, nome in enumerate(clipes):
        sujo = degrade(
            iter(clipes[nome]), person=person, bag=bag, seed=semente_do_clipe(seed, indice)
        )
        resultado = run_session(sujo, plane, config)
        falsos += len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER))
        posses += len(resultado.events.of_kind(EventKind.BAG_OWNED))
        abandonos += len(resultado.events.of_kind(EventKind.BAG_UNATTENDED))
        minutos += resultado.duration_s / 60.0
    return Passada(falsos, posses, abandonos, minutos)


def veredito(retido: float | None, falsos_min: float, orcamento: float) -> str:
    """Silêncio vem primeiro: com o sistema mudo, zero falso alarme não é
    aprovação, é ausência de qualquer coisa para aprovar."""
    if retido is None:
        return "SEM BASE: execucao limpa sem abandonos"
    if retido < LIMIAR_MUDO:
        return "MUDO: perde metade dos eventos"
    if falsos_min > orcamento:
        return "ESTOURA o orcamento"
    return "opera"


def modelos_do_ponto(
    varredura: Varredura, valor: float, rajada: int
) -> tuple[NoiseModel, NoiseModel]:
    modelo = replace(LIMPO, **{varredura.campo: valor, "drop_burst_frames": rajada})
    return (
        modelo if varredura.eixo == "person" else LIMPO,
        modelo if varredura.eixo == "bag" else LIMPO,
    )


def celula(
    clipes: Clipes,
    person: NoiseModel,
    bag: NoiseModel,
    seeds: Sequence[int],
    plane: GroundPlane,
    config: Config,
    base: int,
    orcamento: float,
) -> dict[str, Any]:
    """Um ponto da varredura: todas as seeds no mesmo ponto de operação."""
    runs = []
    for seed in seeds:
        p = uma_passada(clipes, person, bag, seed, plane, config)
        runs.append(
            {
                "seed": seed,
                "falsos": p.falsos,
                "posses": p.posses,
                "abandonos": p.abandonos,
                "minutos": p.minutos,
                "falsos_por_min": p.falsos / p.minutos if p.minutos else 0.0,
                "retidos": p.abandonos / base if base else None,
            }
        )
    por_min = [r["falsos_por_min"] for r in runs]
    retidos = [r["retidos"] for r in runs]
    retido = None if base == 0 else st.mean(retidos)
    media = st.mean(por_min)
    return {
        "n_runs": len(runs),
        "falsos_por_min_media": media,
        "falsos_por_min_desvio_pop": st.pstdev(por_min),
        "posses_media": st.mean(r["posses"] for r in runs),
        "retidos_media": retido,
        "retidos_min": None if base == 0 else min(retidos),
        "retidos_max": None if base == 0 else max(retidos),
        "veredito": veredito(retido, media, orcamento),
        "por_seed": runs,
    }


def varrer(
    clipes: Clipes,
    plane: GroundPlane,
    config: Config,
    seeds: Sequence[int],
    varreduras: Sequence[Varredura] = VARREDURAS,
    rajada: int = RAJADA_QUADROS,
) -> dict[str, Any]:
    """O resultado inteiro, sem proveniência: só depende das entradas."""
    seeds = valida_seeds(seeds)
    for varredura in varreduras:
        valida_varredura(varredura)
    if rajada < 1:
        raise ValueError(f"rajada deve ser >= 1 quadro, recebido {rajada}")
    if not clipes:
        raise ValueError("nenhum clipe para varrer")

    orcamento = config.alerts.operator_hourly_budget / 60.0
    # Modelo limpo não consome o gerador: a seed não muda a execução limpa.
    limpa = uma_passada(clipes, LIMPO, LIMPO, seeds[0], plane, config)
    resultado: dict[str, Any] = {
        "execucao_limpa": asdict(limpa),
        "orcamento_falsos_por_min": orcamento,
        "varreduras": [],
    }
    for varredura in varreduras:
        pontos = []
        for valor in varredura.valores:
            person, bag = modelos_do_ponto(varredura, valor, rajada)
            ponto = celula(clipes, person, bag, seeds, plane, config, limpa.abandonos, orcamento)
            pontos.append(
                {"valor": valor, "pessoa": asdict(person), "bagagem": asdict(bag), **ponto}
            )
            print(_linha(varredura, valor, ponto), flush=True)
        resultado["varreduras"].append(
            {
                "id": varredura.id,
                "titulo": varredura.titulo,
                "eixo": varredura.eixo,
                "campo": varredura.campo,
                "pontos": pontos,
            }
        )
    return resultado


def _linha(varredura: Varredura, valor: float, ponto: dict[str, Any]) -> str:
    retido = ponto["retidos_media"]
    retido_txt = "   n/a" if retido is None else f"{retido:>6.0%}"
    return (
        f"{varredura.id:<16}{valor:>6.2f}  falsos/min {ponto['falsos_por_min_media']:.2f}"
        f" +- {ponto['falsos_por_min_desvio_pop']:.2f}  posses {ponto['posses_media']:.1f}"
        f"  retidos {retido_txt}  {ponto['veredito']}"
    )


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(RAIZ), *args], text=True, stderr=subprocess.DEVNULL, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256_arquivo(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for bloco in iter(lambda: handle.read(1 << 20), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _sha256_codigo() -> str:
    """Hash do pacote e deste script: com a árvore suja, o commit sozinho não
    identifica o que rodou."""
    digest = hashlib.sha256()
    for path in [*sorted((RAIZ / "src" / "custody_watch").glob("*.py")), Path(__file__)]:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def proveniencia(
    argv: Sequence[str], data_root: Path, config: Config, seeds: Sequence[int], rajada: int
) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    dataset = {
        nome: {
            arquivo: _sha256_arquivo(data_root / nome / arquivo)
            for arquivo in (xml, f"{nome}_jpg.tar.gz")
        }
        for nome, xml in SCENARIOS.items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "modo": MODO,
        "alcance": ALCANCE,
        "gerado_utc": datetime.now(UTC).isoformat(),
        "comando": [sys.executable, str(Path(__file__).relative_to(RAIZ)), *argv],
        "commit": {
            "sha": _git("rev-parse", "HEAD"),
            "sujo": None if status is None else bool(status),
        },
        "codigo_sha256": _sha256_codigo(),
        "ambiente": {
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "opencv-python": version("opencv-python"),
        },
        "dataset": {"clipes": list(SCENARIOS), "sha256": dataset},
        "config_sha256": hashlib.sha256(
            json.dumps(asdict(config), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "unattended_time_s": config.custody.unattended_time_s,
        "operator_hourly_budget": config.alerts.operator_hourly_budget,
        "seeds": list(seeds),
        "n_runs_por_ponto": len(seeds),
        "semente_por_clipe": "seed * 100 + indice do clipe (ordem de SCENARIOS)",
        "ruido": {
            "modelo_limpo": asdict(LIMPO),
            "drop_burst_frames": rajada,
            "nota": "cada eixo varrido com o outro limpo; id_switch_rate fixo em 0",
            "limiar_mudo_retidos": LIMIAR_MUDO,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS_PADRAO))
    parser.add_argument("--data", type=Path, default=DATA, help="raiz do CAVIAR baixado")
    parser.add_argument("--output", type=Path, help="JSON de resultado; recusa sobrescrever")
    parser.add_argument(
        "--varreduras", nargs="+", choices=list(POR_ID), default=list(POR_ID), metavar="ID"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        seeds = valida_seeds(args.seeds)
    except ValueError as erro:
        parser.error(str(erro))
    if len(set(args.varreduras)) != len(args.varreduras):
        parser.error(f"varreduras repetidas: {args.varreduras}")
    if args.output is not None and args.output.exists():
        print(f"recuso sobrescrever {args.output}", file=sys.stderr)
        return 2
    if not args.data.exists():
        print(f"dataset ausente em {args.data}", file=sys.stderr)
        print("rode primeiro: scripts/download_caviar.py", file=sys.stderr)
        return 1

    # O ruído é sintético por cima de caixa anotada, então o piso de
    # resolução do detector não se aplica -- ele mediria o tamanho do CAVIAR.
    config = annotated_config(load_config(CONFIG))
    plane = ground_plane(estimate_metres_per_pixel(args.data))
    # Materializa uma vez: decodificar JPEG a cada passada custa mais que a sessão.
    clipes = {nome: list(load_clip(args.data, nome, with_appearance=True)) for nome in SCENARIOS}
    varreduras = [POR_ID[i] for i in args.varreduras]
    print(f"seeds {list(seeds)}  |  {len(seeds)} runs por ponto  |  rajada {RAJADA_QUADROS}")
    resultado = varrer(clipes, plane, config, seeds, varreduras)
    print(ALCANCE)

    if args.output is not None:
        documento = {
            **proveniencia(argv, args.data, config, seeds, RAJADA_QUADROS),
            "resultados": resultado,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(documento, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"gravado em {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
