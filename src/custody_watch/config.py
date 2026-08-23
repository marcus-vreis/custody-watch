"""Configuração com guarda-corpo (spec §3).

Tornar limiares ajustáveis por JSON reabre todos os ataques que três rodadas
de revisão adversarial fecharam. `late_join_extent_m: 0.5` num arquivo de
config devolve a posse da bagagem a dois cúmplices, silenciosamente, sem
quebrar teste nenhum — porque os testes usam os defaults.

Daí `SAFE_BOUNDS`: cada limiar perigoso tem faixa e uma razão escrita. Sair
da faixa exige `unsafe_override` com justificativa, e a mensagem de erro cita
o ataque que o limite previne. Erro de config é documentação.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

MIN_JUSTIFICATION_CHARS = 15


class UnsafeValueError(ValueError):
    """Valor fora da faixa segura, sem override justificado."""


@dataclass(frozen=True)
class Bounds:
    minimum: float
    maximum: float
    reason: str


@dataclass(frozen=True)
class PartyConfig:
    proximity_m: float = 2.0
    late_join_extent_m: float = 5.0
    min_extent_m: float = 0.5
    min_overlap_samples: int = 3
    min_overlap_s: float = 2.0
    max_gap_s: float = 2.0
    time_tolerance_s: float = 0.5


@dataclass(frozen=True)
class CustodyConfig:
    unattended_distance_m: float = 3.0
    unattended_time_s: float = 25.0


@dataclass(frozen=True)
class RegistryConfig:
    moved_threshold_m: float = 0.5
    ambiguity_radius_m: float = 1.0


@dataclass(frozen=True)
class FlagConfig:
    tau_s: float = 900.0
    weight_n1: float = 1.0
    weight_n2: float = 3.0
    weight_n3: float = 10.0


@dataclass(frozen=True)
class AlertConfig:
    clip_margin_s: float = 10.0
    operator_hourly_budget: int = 25


@dataclass(frozen=True)
class Config:
    party: PartyConfig = PartyConfig()
    custody: CustodyConfig = CustodyConfig()
    registry: RegistryConfig = RegistryConfig()
    flags: FlagConfig = FlagConfig()
    alerts: AlertConfig = AlertConfig()


SAFE_BOUNDS: dict[str, Bounds] = {
    "party.proximity_m": Bounds(
        1.0,
        3.0,
        "acima de 3m, pessoas em mesas vizinhas da praça de alimentação são "
        "tratadas como o mesmo grupo",
    ),
    "party.late_join_extent_m": Bounds(
        3.0,
        15.0,
        "abaixo de 3m, dois cúmplices obtêm posse perambulando ao lado da "
        "vítima sem ir a lugar algum",
    ),
    "party.min_extent_m": Bounds(
        0.3,
        2.0,
        "abaixo de 0.3m o ruído do detector satisfaz a guarda, e duas pessoas "
        "sentadas passam a co-mover",
    ),
    "party.time_tolerance_s": Bounds(
        0.1,
        2.0,
        "acima de 2s, observações de instantes não relacionados são casadas, "
        "e seguir alguém a oito metros vira andar junto",
    ),
    "party.min_overlap_s": Bounds(
        1.0,
        30.0,
        "abaixo de 1s, um cruzamento momentâneo conta como co-movimento sustentado",
    ),
    "party.max_gap_s": Bounds(
        0.5,
        10.0,
        "acima de 10s, três fotografias esparsas contam como sobreposição contínua",
    ),
    "custody.unattended_distance_m": Bounds(
        1.0,
        10.0,
        "acima de 10m o dono nunca é considerado ausente; abaixo de 1m qualquer "
        "passo dele dispara desacompanhamento",
    ),
    "custody.unattended_time_s": Bounds(
        5.0,
        300.0,
        "abaixo de 5s, ir ao balcão vira bagagem desacompanhada; acima de 300s "
        "o furto termina antes do alerta",
    ),
    "registry.moved_threshold_m": Bounds(
        0.2,
        2.0,
        "abaixo de 0.2m o jitter do detector é lido como a bagagem sendo carregada",
    ),
    "flags.tau_s": Bounds(
        60.0,
        7200.0,
        "acima de 7200s o decaimento não decai, e tempo de permanência no "
        "aeroporto vira suspeição acumulada",
    ),
}

_SECTIONS = {field.name: field.type for field in fields(Config)}


def _resolve(key: str, raw: Any) -> float | int:
    """Extrai o valor e valida contra a faixa segura, se houver."""
    override: str | None = None
    if isinstance(raw, dict):
        if "value" not in raw:
            raise ValueError(f"{key}: objeto de config precisa da chave 'value'")
        desconhecidas = set(raw) - {"value", "unsafe_override"}
        if desconhecidas:
            raise ValueError(f"{key}: campo desconhecido {sorted(desconhecidas)}")
        override = raw.get("unsafe_override")
        value = raw["value"]
    else:
        value = raw

    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{key}: valor deve ser numérico, recebido {value!r}")

    bounds = SAFE_BOUNDS.get(key)
    if bounds is None or bounds.minimum <= value <= bounds.maximum:
        return value

    if override is None:
        raise UnsafeValueError(
            f"{key} = {value} está fora da faixa segura "
            f"[{bounds.minimum}, {bounds.maximum}].\n"
            f"Motivo do limite: {bounds.reason}.\n"
            f"Para assumir o risco, use "
            f'{{"value": {value}, "unsafe_override": "<justificativa>"}}.'
        )

    if len(override.strip()) < MIN_JUSTIFICATION_CHARS:
        raise UnsafeValueError(
            f"{key} = {value}: unsafe_override exige justificativa de ao menos "
            f"{MIN_JUSTIFICATION_CHARS} caracteres. O campo existe para deixar "
            f"rastro de quem afrouxou o limite e por quê."
        )

    return value


def load_config(path: Path | str) -> Config:
    """Carrega config de JSON, mesclando com os defaults.

    Seção ou campo desconhecido é erro, não aviso: um typo silencioso vira
    config ignorada, que vira comportamento surpresa em produção.
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError(f"{path}: raiz do JSON deve ser um objeto")

    config = Config()
    for section_name, section_data in data.items():
        if section_name not in _SECTIONS:
            raise ValueError(
                f"{path}: seção desconhecida {section_name!r}; esperadas {sorted(_SECTIONS)}"
            )
        if not isinstance(section_data, dict):
            raise ValueError(f"{path}: seção {section_name!r} deve ser um objeto")

        current = getattr(config, section_name)
        known = {field.name for field in fields(current)}
        updates: dict[str, float | int] = {}

        for field_name, raw in section_data.items():
            if field_name not in known:
                raise ValueError(
                    f"{path}: campo desconhecido {section_name}.{field_name!r}; "
                    f"esperados {sorted(known)}"
                )
            updates[field_name] = _resolve(f"{section_name}.{field_name}", raw)

        config = replace(config, **{section_name: replace(current, **updates)})

    return config
