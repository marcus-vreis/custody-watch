# Contributing

## Setup

There is no need for a system Python — `uv` provides it.

```bash
uv python install 3.12
uv sync --extra dev
```

Install the git hooks — this is not optional, CI runs the same checks:

```bash
uv run pre-commit install --install-hooks
```

Verify:

```bash
uv run pytest -v
uv run pre-commit run --all-files
```

## What the hooks enforce

Beyond the usual formatting and whitespace hygiene, three checks exist for reasons specific to this project:

**The test suite runs on every commit.** It takes under a second, so there is no reason to defer it. Three bugs in this repository's history passed human review and were caught by tooling; the suite is the cheapest tool available.

**Face recognition imports are rejected.** `insightface`, `face_recognition`, `deepface`, `facenet`, `dlib`, `mediapipe`. This is a permanent scope decision, not a v1 limitation — see below. A rule that depends on someone having read this file is not a rule.

**Commit messages may not carry AI co-authorship trailers.** Human co-authors are fine and expected; `Co-Authored-By: Claude`, Copilot, and equivalents are rejected.

If a hook blocks something it should not, fix the hook in `.pre-commit-config.yaml` and say why in the commit message. Do not reach for `--no-verify` — CI runs the same checks and will catch it anyway.

## Workflow

One task per branch, one task per PR. Tests are written before implementation and observed failing first — a test that has never failed has not been shown to test anything.

```bash
git switch -c task/<short-name>
# write the failing test
uv run pytest tests/test_<name>.py -v   # expect failure
# implement
uv run pytest tests/test_<name>.py -v   # expect pass
uv run ruff check src tests
```

## Design invariants

Four rules carry this system. A change that violates one is a bug even if every test passes — and if a test appears to require violating one, the test is wrong.

**P1 — Ownership only flows through strong bonds.** `Party.owns()` returns true only for `Bond.STRONG`. If group membership were granted by proximity alone, someone could sit near a traveller for three minutes and become the bag's legitimate owner. Weak bonds attenuate a flag; they never clear it.

**P2 — A stationary bag is a spatial anchor, not a re-identification problem.** Identical suitcases defeat appearance matching, which is exactly what makes trackers swap IDs under occlusion. Position is identity.

**P3 — Uncertainty suppresses alerts, never generates them.** `BagState.AMBIGUA` is a first-class state. When association is uncertain, mark and stay silent. Guessing corrupts the ownership map and manufactures a theft that never happened.

**P4 — Flags are relational, never attributive.** A flag describes a person's relationship to a bag over time. Never a static property of a person. "Carries no luggage" fires on a quarter of an airport, carries no information, and systematically marks airport workers — it was rejected for those reasons and equivalents will be too.

## Conventions

- **Metres, never pixels.** Every threshold is in ground-plane metres. Pixel distances vary with depth and are meaningless as thresholds.
- **Portuguese for operator-facing strings**, English for code, comments, and docs.
- Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
- One responsibility per module. If a file needs "and" to describe it, split it.

## Out of scope, permanently

Face recognition. Identification of people against any database. Automated decisions without human review. These are design decisions, not v1 limitations — proposals to add them will be declined.
