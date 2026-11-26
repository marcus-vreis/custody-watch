#!/usr/bin/env python3
"""Benchmark sintético de custódia: cenários com gabarito × fps, sistema e baseline.

Cada cenário de `custody_watch.scenarios` roda no sistema (`run_session`) e
na baseline ingênua (`scenario_baseline`), nas mesmas taxas de quadro, e é
pontuado contra o gabarito por `scenario_scoring`. Com `--noise`, roda de
novo sob cada nível de ruído declarado (`--noise-levels`, via
`custody_watch.noise.degrade`) com seeds explícitas, e agrega a taxa de acerto
por seed com intervalo de confiança (`custody_watch.scenario_seed_stats`).

**Alcance**: mede a LÓGICA de custódia com posições por construção, no plano
identidade. Não demonstra detector, rastreador nem desempenho em vídeo real.

Resultado ruim não vira código de saída: o script sai 0 sempre que conseguiu
medir e gravar; só erro de execução sai diferente de 0.

    .venv\\Scripts\\python.exe scripts/benchmark_scenarios.py --output outputs/cenarios.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from custody_watch.config import Config
from custody_watch.noise import NoiseModel, degrade
from custody_watch.orchestrator import run_session
from custody_watch.scenario_baseline import run_baseline
from custody_watch.scenario_scoring import (
    ALL_CATEGORIES,
    Category,
    Expectation,
    Score,
    Verdict,
    score,
    system_emissions,
)
from custody_watch.scenario_seed_stats import (
    CI_METHOD,
    RunOutcome,
    aggregate_over_seeds,
    ci_description,
)
from custody_watch.scenarios import MIN_FPS, Scenario, build_scenarios, identity_plane
from custody_watch.tracking import TrackedDetection

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 2
"""2: ruído por nível (`ruido.niveis`), `ruido` em cada execução e agregado
por seed com IC (`agregados.por_seed`)."""
DEFAULT_FPS = (3.0, 10.0, 25.0)
DEFAULT_SEEDS = (0, 1, 2)
MODE = "sintetico: posições geradas, sem detector"
SCOPE = (
    "mede a LÓGICA de custódia com posições por construção no plano identidade; "
    "não demonstra detector, rastreador nem desempenho em vídeo real"
)
BASELINE_SCOPE = frozenset({Category.ACCUSATION, Category.UNATTENDED})
CORRECT = frozenset({Verdict.CORRECT, Verdict.SILENT_CORRECT})

SYNTHETIC_PERSON_NOISE = NoiseModel(
    drop_rate=0.02, drop_burst_frames=3, position_sigma_px=0.05, id_switch_rate=0.0
)
SYNTHETIC_BAG_NOISE = NoiseModel(
    drop_rate=0.01, drop_burst_frames=3, position_sigma_px=0.03, id_switch_rate=0.0
)
CLEAN_LEVEL = "sem_ruido"
NOISE_LEVELS: dict[str, tuple[NoiseModel, NoiseModel]] = {
    "leve": (SYNTHETIC_PERSON_NOISE, SYNTHETIC_BAG_NOISE),
    "moderado": (
        NoiseModel(drop_rate=0.05, drop_burst_frames=5, position_sigma_px=0.15),
        NoiseModel(drop_rate=0.03, drop_burst_frames=5, position_sigma_px=0.08),
    ),
    "forte": (
        NoiseModel(drop_rate=0.10, drop_burst_frames=8, position_sigma_px=0.30),
        NoiseModel(drop_rate=0.05, drop_burst_frames=8, position_sigma_px=0.15),
    ),
}
"""Níveis (pessoa, bagagem) declarados, em ordem crescente. Não são medidos:
servem para ver como o acerto cai com o ruído, não para afirmar operação real."""
NOISE_NOTE = (
    "ruído declarado, não medido: no plano identidade o pixel é metro, então "
    "position_sigma_px é desvio em metros; rajada em quadros (dura mais a 3 fps "
    "que a 25); sem troca de id, porque o gabarito é por track"
)
REACH_NOTE = "posições sintéticas; não demonstra detector nem associação calculada"
SOURCES = (
    "src/custody_watch/scenarios.py",
    "src/custody_watch/scenarios_base.py",
    "src/custody_watch/scenarios_groups.py",
    "src/custody_watch/scenarios_anchors.py",
    "src/custody_watch/scenario_model.py",
    "src/custody_watch/scenario_premises.py",
    "src/custody_watch/scenario_motion.py",
    "src/custody_watch/scenario_scoring.py",
    "src/custody_watch/scenario_baseline.py",
    "src/custody_watch/scenario_seed_stats.py",
    "src/custody_watch/noise.py",
    "scripts/benchmark_scenarios.py",
)
"""Hash de cada fonte que decide o resultado: com a árvore suja, o commit
sozinho não identifica o que rodou."""


def _fps(text: str) -> float:
    value = float(text)
    if math.isnan(value) or value < MIN_FPS:
        raise argparse.ArgumentTypeError(f"fps deve ser ≥ {MIN_FPS}, recebido {text}")
    return value


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def provenance(config: Config, args: argparse.Namespace) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    config_json = json.dumps(asdict(config), sort_keys=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "modo": MODE,
        "alcance": SCOPE,
        "gerado_utc": datetime.now(UTC).isoformat(),
        "comando": [sys.executable, *sys.argv],
        "commit": {
            "sha": _git("rev-parse", "HEAD"),
            "sujo": None if status is None else bool(status),
        },
        "config_sha256": hashlib.sha256(config_json.encode("utf-8")).hexdigest(),
        "config": asdict(config),
        "fps": list(args.fps),
        "nota_alcance": REACH_NOTE,
        "seeds": list(args.seeds) if args.noise else [],
        "ruido": (
            {
                "niveis": {
                    name: {
                        "pessoa": asdict(NOISE_LEVELS[name][0]),
                        "bagagem": asdict(NOISE_LEVELS[name][1]),
                    }
                    for name in args.noise_levels
                },
                "nota": NOISE_NOTE,
            }
            if args.noise
            else None
        ),
        "intervalo_confianca": ci_description(),
        "pontuacao": {
            "sistema": sorted(c.value for c in ALL_CATEGORIES),
            "baseline": sorted(c.value for c in BASELINE_SCOPE),
            "nota": "a baseline não emite sinal de pessoa (N1/N2); esses itens não a pontuam",
        },
        "fontes_sha256": {name: _sha256(ROOT / name) for name in SOURCES},
    }


def _frames(
    scn: Scenario, fps: float, seed: int | None, level: str = CLEAN_LEVEL
) -> Iterator[tuple[float, list[TrackedDetection]]]:
    clean = scn.frames(fps)
    if seed is None:
        return clean
    person, bag = NOISE_LEVELS[level]
    return degrade(clean, person=person, bag=bag, seed=seed)


def run_case(
    scn: Scenario, config: Config, fps: float, seed: int | None, level: str = CLEAN_LEVEL
) -> tuple[Score, Score]:
    """Uma execução: o mesmo fluxo de quadros para o sistema e para a baseline."""
    plane = identity_plane()
    result = run_session(_frames(scn, fps, seed, level), plane, config)
    system = score(scn.expected, system_emissions(result.flags, result.events))
    baseline = score(
        scn.expected, run_baseline(_frames(scn, fps, seed, level), plane, config), BASELINE_SCOPE
    )
    return system, baseline


def _conditions(seeds: Sequence[int], levels: Sequence[str]) -> list[tuple[str, int | None]]:
    """Sem ruído primeiro (sem seed), depois cada nível com cada seed."""
    return [(CLEAN_LEVEL, None)] + [(lv, s) for lv in levels for s in seeds]


def _expected_dict(expected: Expectation) -> dict[str, Any]:
    return {
        "nunca_acusar": sorted(expected.never_accuse),
        "devidos": [
            {
                "categoria": d.category.value,
                "alvo": d.target,
                "referencia_t": d.reference_t,
                "tolerancia_s": d.tolerance_s,
                "descricao": d.descricao,
            }
            for d in expected.due
        ],
        "silencio": expected.silence,
    }


def evaluate(
    scenarios: Sequence[Scenario],
    config: Config,
    fps_list: Sequence[float],
    seeds: Sequence[int],
    levels: Sequence[str] = ("leve",),
) -> list[dict[str, Any]]:
    out = []
    for scn in scenarios:
        premissas = scn.premissas(config)
        runs = []
        for level, seed in _conditions(seeds, levels):
            for fps in fps_list:
                system, baseline = run_case(scn, config, fps, seed, level)
                runs.append(
                    {
                        "fps": fps,
                        "ruido": level,
                        "seed": seed,
                        "sistema": system.to_dict(),
                        "baseline": baseline.to_dict(),
                    }
                )
        clean = [r for r in runs if r["seed"] is None]
        out.append(
            {
                "name": scn.name,
                "descricao": scn.descricao,
                "invariant": scn.invariant,
                "kind": scn.kind,
                "papeis": scn.papeis,
                "marcos": dict(scn.marks),
                "duracao_s": scn.duration_s,
                "gabarito": _expected_dict(scn.expected),
                "premissas": [asdict(p) for p in premissas],
                "premissas_validas": all(p.valida for p in premissas),
                "execucoes": runs,
                "estavel_entre_fps": {
                    who: len({r[who]["veredito"] for r in clean}) == 1
                    for who in ("sistema", "baseline")
                },
            }
        )
    return out


def outcomes(results: Sequence[dict[str, Any]]) -> list[RunOutcome]:
    """Cada execução pontuada, por invariante e também no balde `total`."""
    out = []
    for item in results:
        for run in item["execucoes"]:
            for who in ("sistema", "baseline"):
                correct = run[who]["veredito"] in CORRECT
                for bucket in (item["invariant"], "total"):
                    out.append(
                        RunOutcome(who, bucket, run["fps"], run["ruido"], run["seed"], correct)
                    )
    return out


def aggregate(results: Sequence[dict[str, Any]], level: str) -> dict[str, Any]:
    """Contagem de vereditos por invariante num nível de ruído, por quem foi pontuado."""
    groups: dict[str, dict[str, Counter]] = {}
    for item in results:
        for run in item["execucoes"]:
            if run["ruido"] != level:
                continue
            for who in ("sistema", "baseline"):
                key = item["invariant"]
                for bucket in (key, "total"):
                    counter = groups.setdefault(bucket, {}).setdefault(who, Counter())
                    counter[run[who]["veredito"]] += 1
    return {
        bucket: {
            who: {
                "execucoes": sum(c.values()),
                "corretas": sum(c[v.value] for v in CORRECT),
                "vereditos": dict(sorted(c.items())),
            }
            for who, c in by_who.items()
        }
        for bucket, by_who in sorted(groups.items())
    }


def _cell(runs: Sequence[dict[str, Any]], who: str) -> str:
    verdicts = [(r["fps"], r[who]["veredito"]) for r in runs]
    if len({v for _, v in verdicts}) == 1:
        return verdicts[0][1]
    return " ".join(f"{fps:g}:{v}" for fps, v in verdicts)


def table(results: Sequence[dict[str, Any]], fps_list: Sequence[float]) -> str:
    taxas = "/".join(f"{f:g}" for f in fps_list)
    lines = [
        f"MODO: {MODE}",
        f"ALCANCE: {SCOPE}",
        "",
        f"{'cenário':30s} {'inv':5s} {'tipo':9s} {'sistema @' + taxas + ' fps':32s} "
        f"{'baseline @' + taxas + ' fps':32s} premissas",
    ]
    for item in results:
        clean = [r for r in item["execucoes"] if r["seed"] is None]
        ok = "ok" if item["premissas_validas"] else "FALHAM"
        lines.append(
            f"{item['name']:30s} {item['invariant']:5s} {item['kind']:9s} "
            f"{_cell(clean, 'sistema'):32s} {_cell(clean, 'baseline'):32s} {ok}"
        )
    return "\n".join(lines)


def _summary(aggregates: dict[str, Any], label: str) -> list[str]:
    lines = ["", f"corretas por invariante ({label}):"]
    for bucket, by_who in aggregates.items():
        cells = "  ".join(
            f"{who} {data['corretas']}/{data['execucoes']}" for who, data in by_who.items()
        )
        lines.append(f"  {bucket:5s} {cells}")
    return lines


def _seed_summary(rows: Sequence[dict[str, Any]]) -> list[str]:
    lines = ["", f"taxa média de acerto sobre seeds, IC: {CI_METHOD}:"]
    for r in rows:
        if r["principio"] != "total":
            continue
        ic = "sem IC" if r["ic_inf"] is None else f"[{r['ic_inf']:.3f}, {r['ic_sup']:.3f}]"
        lines.append(
            f"  {r['variante']:8s} {r['fps']:>4g} fps {r['ruido']:9s} "
            f"n={r['n_seeds']:<2d} média {r['media']:.3f} {ic}"
        )
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, required=True, help="JSON novo; nunca sobrescreve")
    parser.add_argument("--fps", type=_fps, nargs="+", default=list(DEFAULT_FPS))
    parser.add_argument("--noise", action="store_true", help="roda também sob ruído declarado")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument(
        "--noise-levels",
        nargs="+",
        choices=list(NOISE_LEVELS),
        default=["leve"],
        help="níveis declarados usados com --noise (default: leve)",
    )
    parser.add_argument("--scenarios", nargs="+", help="subconjunto por nome (default: todos)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"{args.output} já existe; resultados anteriores são preservados")

    config = Config()
    scenarios = build_scenarios(config)
    if args.scenarios:
        known = {s.name for s in scenarios}
        unknown = sorted(set(args.scenarios) - known)
        if unknown:
            parser.error(f"cenários desconhecidos: {unknown}; conhecidos: {sorted(known)}")
        scenarios = tuple(s for s in scenarios if s.name in args.scenarios)

    if len(set(args.seeds)) != len(args.seeds) or len(set(args.noise_levels)) != len(
        args.noise_levels
    ):
        parser.error("seeds e níveis de ruído não podem se repetir")
    seeds = list(args.seeds) if args.noise else []
    levels = list(args.noise_levels) if args.noise else []
    results = evaluate(scenarios, config, args.fps, seeds, levels)
    runs = len(results) * len(args.fps) * (1 + len(seeds) * len(levels))
    document = provenance(config, args) | {
        "execucoes": {"cenarios": len(results), "por_sistema": runs, "total": 2 * runs},
        "cenarios": results,
        "agregados": {
            "limpo": aggregate(results, CLEAN_LEVEL),
            "ruido": {lv: aggregate(results, lv) for lv in levels} if seeds else None,
            "por_seed": aggregate_over_seeds(outcomes(results)),
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    report = [table(results, args.fps), *_summary(document["agregados"]["limpo"], "limpo")]
    for lv in levels:
        report += _summary(document["agregados"]["ruido"][lv], f"ruído {lv}, seeds {seeds}")
    if seeds:
        report += _seed_summary(document["agregados"]["por_seed"])
    report += ["", f"{runs} execuções por sistema; JSON em {args.output}"]
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
