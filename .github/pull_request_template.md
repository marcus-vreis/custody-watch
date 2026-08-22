<!--
Keep this PR to one task. A PR that touches three modules is three PRs.
-->

## What changed

<!-- One or two sentences. What a reviewer needs before reading the diff. -->

Closes #

## Verification

<!-- Paste real command output. "Tests pass" without output is not verification. -->

```
$ uv run pytest -v

```

```
$ uv run ruff check src tests

```

## Checklist

- [ ] Test written first, observed failing, then made to pass
- [ ] Full suite passes, not just the new tests
- [ ] `ruff check` clean
- [ ] No new public function left untested
- [ ] Thresholds are in metres, never pixels
- [ ] No design rationale copied into code comments that belongs in the design doc

## Design invariants

Tick only those this PR touches. Untick means "not applicable", not "skipped".

- [ ] **P1** — bag ownership only flows through `Bond.STRONG`
- [ ] **P2** — stationary bags are matched by spatial anchor, never by appearance
- [ ] **P3** — uncertainty suppresses alerts, never generates them
- [ ] **P4** — flags are relational (person × bag × time), never attributive

## Notes for the reviewer

<!-- Anything you are unsure about, or chose deliberately against the obvious option. -->
