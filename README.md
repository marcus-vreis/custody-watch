# custody-watch

Detecting **luggage custody changes** in airport surveillance video — not faces, not "suspicious people".

> **Status:** runs end to end. 236 tests, CI green. The pipeline consumes video, tracks who owns which bag, and produces a ranked review page for a human operator.
>
> **It has never seen a real theft.** No public dataset with one is still online — see [Evaluation](#evaluation). Every number below measures how often the system cries wolf, not whether it detects anything.

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
video ─► Detector ─► GroundPlane ─► Tracking ─► TrackLinker (re-ID)
                                                      │
                            BagRegistry ◄─────────────┤
                                  │                   ▼
                                  └──► CustodyFSM ◄── PartyManager
                                             │
                                  FlagEngine ─► AlertQueue ─► clips + review page
```

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
| `detectors/` | Swappable `Detector` protocol; YOLO26 adapter |
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
| `caviar.py` | Dataset reader |

## Auditability

Every decision emits an event carrying the numbers that produced it. A session replays from its log, without the video:

```json
{"kind":"bag_owned","t_start":10.0,"bag":100,"party":1,"evidence":{"party":1}}
{"kind":"bag_unattended","t_start":50.0,"bag":100,"party":1,"evidence":{"distance_m":20.0,"elapsed_s":30.0}}
{"kind":"bag_removed_by_stranger","t_start":58.0,"subject":99,"bag":100,"party":1,"evidence":{"carrier":99,"owner_party":1}}
```

This is why thresholds live in `config.py` behind declared safe ranges: making them tunable reopens every attack three rounds of adversarial review closed, so a value outside its range is rejected with the reason it exists.

## Evaluation

Not mAP. **P_miss @ RFA** (miss probability at a given false-alarm rate per minute), the NIST ActEV standard for this problem family, plus ranking quality — where the true event lands in the queue.

**P_miss has no measured value, because no positive event exists to miss.**

PETS2007 was the intended benchmark: it had a labelled attended-luggage-removal scenario and shipped camera calibration. As of August 2026 every host is gone — `cvg.reading.ac.uk` does not resolve, the Reading FTP mirrors refuse connection, `pets2006.net` and `pets2007.net` are dead, and the Wayback snapshot only archives the HTML, not the `ftp://` payloads.

[CAVIAR](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/) is what remains, and it annotates the bag as its own tracked object. It contains **no theft**: in every clip the person who retrieves the bag is the one who left it, with the track fragmenting in between. It also has no camera calibration, and the standard pedestrian-height fit does not converge on that wide-angle overhead lens — so distances come from a single global scale and carry roughly 30% error.

What that does allow measuring is how often the system invents a theft where none happened:

```
false alarms:  2 without re-ID  ->  1 with re-ID
per minute:    0.63             ->  0.31
```

Over 3.2 minutes of footage with zero real thefts. The remaining alarm is the linker declining to guess between two candidates 0.021 apart — refusing is correct there, because linking to the wrong track would let a thief inherit the victim's group and silence a real theft.

**Closing the gap needs staged footage with real thefts in it.** That is the blocker, and it is not a code problem.

## Stack

Python 3.12 · [uv](https://docs.astral.sh/uv/) · YOLO26 behind a swappable `Detector` interface · OpenCV · Pillow

## Licence

[AGPL-3.0](LICENSE), inherited from Ultralytics. The `Detector` interface exists partly so that swapping to an Apache-2.0 model (RF-DETR, D-FINE) stays a contained change.
