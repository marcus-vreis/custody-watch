# custody-watch

Detecting **luggage custody changes** in airport surveillance video — not faces, not "suspicious people".

> **Status:** runs end to end. 335 tests, CI green. The pipeline consumes video, tracks who owns which bag, and produces a ranked review page for a human operator.
>
> **It has never seen a real theft.** No public dataset with one is still online — see [Evaluation](#evaluation).
>
> **And on the only footage available, the detector cannot see the bags at all:** 1 hit in 1,686 annotated suitcases. Everything the logic layer does well, it does on ground-truth boxes. See [Perception](#perception-the-half-that-was-never-measured).

## What this is

Luggage theft in airport waiting areas is not an identity problem. It is an **object custody** problem. The question is not *"who is this person"* — it is:

> Is the person walking away with the bag the same one who set it down?

That reframing is the whole project. It means the system needs no facial recognition, no identity database, and no biometric storage — only the ability to track which bags belong to which travelling group.

## What this is not

- **Not a face recognition system.** Permanently out of scope, and enforced by a pre-commit hook that rejects `insightface`, `deepface`, `dlib` and equivalents.
- **Not a theft detector.** The base rate makes a binary theft alarm mathematically useless — roughly 1 real theft per 30,000 custody events. Even a 0.1% false positive rate yields ~3% precision.
- **Not autonomous.** It ranks clips for a human operator who is already watching the cameras. It never decides, never accuses, never identifies.

The actual goal: **reduce ~600,000 daily custody events to a few hundred ranked clips worth an operator's time.**

## Approach

```
VideoSource ─► Detector ─► GroundPlane ─► Tracking ─► TrackLinker (re-ID)
                                                            │
                                  BagRegistry ◄─────────────┤
                                        │                   ▼
                                        └──► CustodyFSM ◄── PartyManager
                                                   │
                                        FlagEngine ─► AlertQueue ─► clips + review page
```

The seam that matters is in the middle. Everything left of `GroundPlane` speaks pixels; everything right of it speaks metres and seconds. The logic half consumes only `(x, y, t)`, so it cannot tell whether those numbers came from a real camera or a synthetic trajectory — which is what makes the two halves measurable separately, and why the numbers below are reported as two, never one.

Four principles carry the design:

1. **Ownership only flows through strong bonds.** If group membership were granted by proximity alone, a thief would sit next to you for three minutes and become the legitimate owner. Arriving together and walking together count; sitting nearby does not.
2. **A stationary bag is a spatial anchor, not a re-ID problem.** Identical black suitcases defeat appearance matching. Position is identity.
3. **Uncertainty suppresses alerts, never generates them.** There is a first-class `AMBIGUOUS` state.
4. **Flags are relational, never attributive.** "Approached bags from three different groups in eight minutes" is evidence. "Carries no luggage" is profiling — it fires on 20-30% of an airport and systematically marks airport workers.

## Running it

No system Python needed; `uv` provides one.

```bash
uv sync --extra dev
uv run pre-commit install --install-hooks
```

Fetch the dataset (~192 MB) and measure:

```bash
uv run python scripts/download_caviar.py
uv run python scripts/run_caviar.py
```

Measure the detector against the same ground truth — this is the number that governs everything else:

```bash
uv run python scripts/detector_baseline.py
```

Derive event annotations from the CAVIAR XML, then score against them:

```bash
uv run python scripts/annotate_caviar.py
uv run python scripts/evaluate.py
```

`evaluate.py` exits non-zero and refuses to print a `P_miss` when no annotated positive is measurable at the configured threshold — see [Evaluation](#evaluation). To score at a threshold this dataset can actually express:

```bash
uv run python scripts/evaluate.py --config config/caviar.json
```

Build the operator's review page, with annotated clips:

```bash
uv run python scripts/build_report.py
```

## Modules

| Module | Responsibility |
|---|---|
| `types.py` | Shared vocabulary. All positions in ground-plane metres, never pixels |
| `config.py` | Thresholds from JSON, each dangerous one fenced with a written reason |
| `ground_plane.py` | Homography; projects the *base* of a box, where the object meets the floor |
| `calibration.py` | Ground calibration from a file; refuses a fit whose worst point misses by more than 25 cm |
| `detectors/` | Swappable `Detector` protocol; YOLO26 adapter |
| `video.py` | Decodes a file once and yields frame, timestamp and tracks together |
| `tracking.py` | Tracks to observations; reads real fps rather than assuming one |
| `reid.py` | Relinks fragmented tracks by ephemeral appearance, session-scoped |
| `party.py` | Group formation, strong vs weak bonds |
| `bag_registry.py` | Spatial anchors, `AMBIGUOUS` state |
| `custody.py` | Attendance and removal state machine |
| `flags.py` | Relational flags, exponential decay |
| `alerts.py` | Ranked queue, clip window, explanation |
| `events.py` | Events as serialisable intervals, JSONL |
| `orchestrator.py` | Wires it together; consumes frames, emits a queue |
| `clips.py` | Annotated GIF cut around the gravest signal |
| `report.py` | The operator's review page |
| `metrics.py` | P_miss @ RFA |
| `annotations.py` | Ground truth as events, and asymmetric matching against what was emitted |
| `caviar.py` | Dataset reader |

## Auditability

Every decision emits an event carrying the numbers that produced it. A session replays from its log, without the video:

```json
{"kind":"bag_owned","t_start":10.0,"bag":100,"party":1,"evidence":{"party":1}}
{"kind":"bag_unattended","t_start":50.0,"bag":100,"party":1,"evidence":{"distance_m":20.0,"elapsed_s":30.0}}
{"kind":"bag_removed_by_stranger","t_start":58.0,"subject":99,"bag":100,"party":1,"evidence":{"carrier":99,"owner_party":1}}
```

This is why thresholds live in `config.py` behind declared safe ranges: making them tunable reopens every attack three rounds of adversarial review closed, so a value outside its range is rejected with the reason it exists.

## Perception: the half that was never measured

`YoloDetector` existed, was tested against a fake, and had never once been instantiated. Every result this project produced came from ground-truth boxes. Running it changed what the project thinks it is:

```
             annotated   found   recall
person            5493    1677    30.5%
suitcase          1686       1     0.1%
```

**One hit in 1,686.** At 384×288 the detector is effectively blind to luggage, and loses 70% of the people too.

The useful part is that recall tracks box height, cleanly:

| clip | box height | person recall |
|---|---|---|
| LeftBag_PickedUp | 39 px | **63.8%** |
| LeftBag | 33 px | 25.7% |
| LeftBox | 27 px | 15.8% |
| LeftBag_AtChair | 26 px | 23.6% |

Luggage sits between 9 and 22 px and returns 0% in three of the four clips.

That confirms by measurement a requirement that had only been derived geometrically: a 55 cm suitcase needs about 40 px, which works out to 73 px/m. Measured, 39 px gives 64% and anything under 33 px falls below 26%. Two independent routes, same number.

The consequence is blunt: **on this data, perception dominates everything downstream, and none of the numbers below say anything about whether the system can see.**

That is not the same as "the logic half is fine" — it had four serious defects, and this dataset could not have shown any of them. See [Defects no dataset could expose](#defects-no-dataset-could-expose).

## Evaluation

Not mAP. **P_miss @ RFA** (miss probability at a given false-alarm rate per minute), the NIST ActEV standard for this problem family, plus ranking quality — where the true event lands in the queue.

**P_miss for theft has no measured value, because no positive event of that class exists to miss.**

PETS2007 was the intended benchmark: it had a labelled attended-luggage-removal scenario and shipped camera calibration. As of August 2026 every host is gone — `cvg.reading.ac.uk` does not resolve, the Reading FTP mirrors refuse connection, `pets2006.net` and `pets2007.net` are dead, and the Wayback snapshot only archives the HTML, not the `ftp://` payloads.

[CAVIAR](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/) is what remains, and it annotates the bag as its own tracked object. It contains **no theft**: in every clip the person who retrieves the bag is the one who left it, with the track fragmenting in between. It also has no camera calibration, and the standard pedestrian-height fit does not converge on that wide-angle overhead lens — so distances come from a single global scale and carry roughly 30% error.

What that does allow measuring is how often the system invents a theft where none happened. Over 3.2 minutes with zero real thefts:

```
false alarms:  0 without re-ID  ->  0 with re-ID
per minute:    0.00             ->  0.00
```

That was `0.63 → 0.31` before the occlusion work, and the improvement is real — but **the zero is not as good as it looks, and the caveat is the point.** The legitimate `bag_removed_by_owner` events stopped firing too, because both clips that had one end inside the 30 s occlusion timeout. Part of that zero is correct suppression; part of it is material too short to resolve anything. Reported together, or not at all.

### The benchmark cannot express the protocol

The 3 m / 25 s thresholds come from the PETS2007 protocol. Derived from CAVIAR's own XML, the longest abandonment in the entire dataset lasts **13.3 seconds**:

```
LeftBag            39.2s -> 52.5s  (13.3s, someone came back)
LeftBag_AtChair    21.5s -> 33.2s  (11.7s, someone came back)
LeftBag_PickedUp   27.4s -> 38.5s  (11.1s, someone came back)
LeftBox            29.8s -> 34.5s  ( 4.6s, annotation ends)
```

None of the four is an instance of the event at 25 s. Emitting nothing is the **correct** behaviour, and a `P_miss` computed there would measure clip length, not the system. So `evaluate.py` refuses:

```
RECUSANDO calcular P_miss: nenhum dos anotados e medivel a 25.0s.
O maior abandono anotado dura 13.3s.
```

Exits 1. The refusal *is* the result — it is the measure of how far this dataset is from the question.

Whether a dataset can express a protocol's positive class is itself a function of the protocol's parameters, and nobody reports it.

### What the harness does prove

At 10 s, a threshold CAVIAR can express:

```
clipe                  anotados  medivel  curto  incerto  acertos  perdidos  espurios   atraso
LeftBag                       1        1      0        0        1         0         0   +10.0s
LeftBag_AtChair               1        1      0        0        1         0         0    +9.8s
LeftBag_PickedUp              1        1      0        0        1         0         1   +10.0s
LeftBox                       1        0      0        1        0         0         0

P_miss @ RFA 0.5/min : 0.00      positivos medidos: 3
```

(Tooling speaks Portuguese; the README does not. `medivel` / `curto` / `incerto` are the three verdicts on an annotated positive — measurable, too short to be an instance at this threshold, or cut off before it could be judged. That third one is the one that matters: without it, "we cannot tell" is silently counted as "the system missed it", which is the easiest way to manufacture a bad `P_miss` out of short material.)

Three positives carry a confidence interval of roughly ±40 pp, so `0.00` is not a claim about the system. What it proves is that the harness runs end to end on real annotation.

The lag column earns its own note: **+10.0, +9.8, +10.0 — exactly the threshold in use**, because the event fires when the state *completes* its duration. A symmetric ±2 s matching window would mark all three as missed *and* spurious at once, producing `P_miss = 1.0` with no relation to the system. That was an argument in the spec; now it is a measurement.

### Defects no dataset could expose

The logic half was carrying four chained defects that CAVIAR cannot show, because in none of its clips does anyone stand in front of a stationary bag for long enough.

A passer-by occluding a bag for 0.2 s produced a top-severity theft accusation against them; the bag then died, erasing the genuine abandonment; and a theft committed in plain sight emitted nothing at all, because removal was only ever detected by *disappearance*. The ranked queue — the system's entire output — listed the innocent bystander and never mentioned the thief.

All four were found by a twelve-line synthetic scenario run against `run_session`, and are fixed. But the fix is verified by synthetic trajectories: they prove the logic decides correctly, not that perception sees.

**Closing the gap needs staged footage with real thefts in it.** That is the blocker, and it is not a code problem.

One requirement for that footage came out of the measurement, and would not have been guessed: the camera has to keep rolling for `max_occlusion_s` **after the bag leaves frame** — 30 s by default — and not merely for `unattended_time_s` after the abandonment. A scene that ends when the bag does produces an unresolvable event rather than a measurable one.

## Stack

Python 3.12 · [uv](https://docs.astral.sh/uv/) · YOLO26 behind a swappable `Detector` interface · OpenCV · Pillow

## Licence

[AGPL-3.0](LICENSE), inherited from Ultralytics. The `Detector` interface exists partly so that swapping to an Apache-2.0 model (RF-DETR, D-FINE) stays a contained change.
