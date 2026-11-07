"""Instrumentação opt-in de sessão sintética; não participa das decisões."""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import wraps

import numpy as np

from .reid import SIGNATURE_SIZE, Appearance
from .tracking import TrackedDetection
from .types import TERMINAL_BAG_STATES, BagState

# Cada chave retornada por counts declara sua retenção. Contadores lifetime
# não são entradas retidas e ficam fora do orçamento de coleções.
RETENTION = {
    "bags": "sessão inteira; preservar posse ativa, terminal e referências de flags (#30)",
    "bag_links": "sessão inteira; aliases apontam para bag_id, não podem ressuscitar terminal",
    "unresolved_index": "índice não terminal; terminal sai na próxima consulta; registro permanece",
    "active_bags": "bags não-terminais, incluindo ambíguas; não expirar custódia cegamente",
    "terminal_bags": "subconjunto de bags; tombstones impedem readoção de custódia resolvida",
    "ambiguous_bags": "subconjunto de bags; incerteza suprime sinais",
    "occlusion_candidates": "por bag; pessoas observadas durante oclusão, usadas na resolução",
    "linker_windows": "sessão inteira; max_gap limita match, não remove a janela",
    "linker_aliases": "sessão inteira; raw_id para canonical_id, histórico/posse referenciam",
    "linker_pending": "até min_samples; track que some antes disso não tem poda temporal",
    "linker_pending_since": "acompanha linker_pending, inclusive candidatos que desapareceram",
    "appearance_samples": "max_samples por janela; pending até min_samples; identidades crescem",
    "history_identities": "sessão inteira; janela só é podada quando aquela identidade reaparece",
    "history_samples": "history_window_s por identidade observada, sem poda global de ausentes",
    "gate_tracks": "sessão inteira; última observação por raw_id, usada em velocidade plausível",
    "flag_people": "sessão inteira; drop pode deixar chave vazia, fila consulta identidades",
    "flags": "decaimento de score não expira registros; drop remove relação pessoa/bag",
    "groups": "sessão inteira; posse de bag aponta para party_id",
    "group_members": "sessão inteira; vínculos fortes/fracos e resolução de dono dependem deles",
    "party_membership": "sessão inteira; pessoa para grupo canônico",
    "missing": "por bag ausente, removido ao reaparecer; depende da custódia",
    "pending_bags": "rest_confirm_s; forget_stale_pending poda candidatos que sumiram",
    "pending_bag_samples": "janela rest_confirm_s por candidata; não é evento nem posse",
    "vetoes": "substituído a cada frame; veto visual ao nascimento de âncora",
    "ambiguous_tracks": "sessão inteira; recusa persistente por track impede bag fantasma",
    "near_since": "pares pessoa/bag; remoção por distância é condicional à observação",
    "proximity_flagged": "deduplicação pessoa/bag da sessão; não expirar sem contrato de replay",
    "contact_flagged": "deduplicação; posse tardia pode limpar contatos do dono",
    "together_since": "pares de pessoas; limpeza por distância exige observação do par",
    "pointed_bags": "teto MAX_APONTADAS; referência humana e pixel da âncora",
    "last_tracked": "apenas último feed; picking humano consulta caixas daquele quadro",
    "events_retained": "max_events se configurado; padrão EventLog completo não tem teto",
    "events_total": "contador lifetime O(1), não coleção",
}


@dataclass(frozen=True)
class SyntheticFrame:
    index: int
    t: float
    tracked: tuple[TrackedDetection, ...]
    dropped: bool
    quiet: bool
    occluded: bool


def synthetic_frames(total: int | None, *, seed: int = 7, step_s: float = 0.2):
    """Ciclos de 100 frames: chegada, depósito, oclusões, saída, silêncio.

    Coordenadas sintéticas em pixels, 1 cm/px. Seed e consumo do RNG não
    dependem do tamanho pedido: prefixos são exatamente os mesmos.
    Um track de aparência incompleto por ciclo exercita pending de re-ID.
    Não é uma simulação validada de percepção, população ou aeroporto.
    """
    rng = np.random.default_rng(seed)
    index = 0
    signature = Appearance((1.0,) + (0.0,) * (SIGNATURE_SIZE - 1))
    while total is None or index < total:
        cycle, phase = divmod(index, 100)
        jitter = float(rng.uniform(-0.1, 0.1))
        x = 200.0 + 20.0 * min(phase, 19) + jitter
        if phase >= 55:
            x += (phase - 54) * 20.0
        person = 10 * cycle + 1
        occluded = phase in range(33, 39) or phase in range(49, 55)
        quiet = phase >= 70
        tracks = []
        if not quiet:
            tracks.append(TrackedDetection(person, "person", (x - 40.0, 200.0, x + 40.0, 370.0)))
            if not occluded:
                tracks.append(
                    TrackedDetection(person + 1, "suitcase", (x + 5.0, 315.0, x + 35.0, 370.0))
                )
        if phase == 0:
            tracks.append(
                TrackedDetection(
                    person + 2, "person", (10.0, 10.0, 30.0, 180.0), appearance=signature
                )
            )
        # Grade virtual em nanossegundos: erros de multiplicação binária não
        # devem decidir se uma janela de 2 s já terminou neste benchmark.
        yield SyntheticFrame(
            index, round(index * step_s, 9), tuple(tracks), index % 29 == 28, quiet, occluded
        )
        index += 1


class SessionProbe:
    """Cronometra métodos da sessão própria e restaura-os no exit.

    Usa internals deliberadamente: alterar esses campos deve quebrar o smoke,
    em vez de ocultar uma coleção nova. Um probe por sessão, sem feed concorrente.
    Timings são inclusivos e cumulativos; não somar feed com suas subetapas.
    Estado do probe é O(número de etapas), não O(frames).
    """

    def __init__(self, session):
        self.session = session
        self._timings = {}
        self._originals = []

    def measure(self, stage, call, *args, **kwargs):
        started = time.perf_counter()
        try:
            return call(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            record = self._timings.setdefault(stage, {"calls": 0, "total_s": 0.0, "max_s": 0.0})
            record["calls"] += 1
            record["total_s"] += elapsed
            record["max_s"] = max(record["max_s"], elapsed)

    def feed(self, t, tracked):
        return self.measure("feed", self.session.feed, t, tracked)

    def __enter__(self):
        state = self.session._s
        stages = [
            (state, name)
            for name in (
                "resolve_people",
                "merge_parties",
                "observe_bags",
                "relational_flags",
                "resolve_removals",
            )
        ] + [(state.gate, "filter")]
        for obj, name in stages:
            original = getattr(obj, name)
            self._originals.append((obj, name, original, name in vars(obj)))
            setattr(obj, name, self._wrap(name, original))
        return self

    def _wrap(self, stage, call):
        @wraps(call)
        def measured(*args, **kwargs):
            return self.measure(stage, call, *args, **kwargs)

        return measured

    def __exit__(self, *_args):
        for obj, name, original, was_local in self._originals:
            if was_local:
                setattr(obj, name, original)
            else:
                delattr(obj, name)
        self._originals.clear()

    def timings(self):
        return {stage: dict(record) for stage, record in self._timings.items()}

    def counts(self):
        with self.session._trava:
            s = self.session._s
            bags = s.registry.all()
            return {
                "bags": len(bags),
                "bag_links": len(s.registry._by_track),
                "unresolved_index": len(s.registry._unresolved),
                "active_bags": sum(b.state not in TERMINAL_BAG_STATES for b in bags),
                "terminal_bags": sum(b.state in TERMINAL_BAG_STATES for b in bags),
                "ambiguous_bags": sum(b.state is BagState.AMBIGUA for b in bags),
                "occlusion_candidates": sum(len(b.occlusion_candidates) for b in bags),
                "linker_windows": len(s.linker._windows),
                "linker_aliases": len(s.linker._canonical),
                "linker_pending": len(s.linker._pending),
                "linker_pending_since": len(s.linker._pending_since),
                "appearance_samples": sum(len(w.samples) for w in s.linker._windows.values())
                + sum(len(samples) for samples in s.linker._pending.values()),
                "history_identities": len(s.history),
                "history_samples": sum(len(h) for h in s.history.values()),
                "gate_tracks": len(s.gate._last),
                "flag_people": len(s.flags._by_person),
                "flags": sum(len(flags) for flags in s.flags._by_person.values()),
                "groups": len(s.parties._parties),
                "group_members": sum(len(p.members) for p in s.parties._parties.values()),
                "party_membership": len(s.parties._party_of),
                "missing": len(s.missing),
                "pending_bags": len(s.pending_bags),
                "pending_bag_samples": sum(len(b.amostras) for b in s.pending_bags.values()),
                "vetoes": len(s.vetoes),
                "ambiguous_tracks": len(s.ambiguous_tracks),
                "near_since": len(s.near_since),
                "proximity_flagged": len(s.proximity_flagged),
                "contact_flagged": len(s.contact_flagged),
                "together_since": len(s.together_since),
                "pointed_bags": len(self.session._apontadas),
                "last_tracked": len(self.session._ultimo_tracked),
                "events_retained": len(s.events),
                "events_total": s.events.total_events,
            }
