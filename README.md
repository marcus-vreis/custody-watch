# custody-watch

Detecting **luggage custody changes** in airport surveillance video — not faces, not "suspicious people".

> **Status:** logic layer complete — 115 tests, no video required to run them. Not yet wired into a pipeline: there is no orchestrator, no hand-to-bag contact detection, and no PETS2007 measurement yet. Progress is tracked in [issues](https://github.com/marcus-vreis/custody-watch/issues). The full design document is kept outside this repository.

## What this is

Luggage theft in airport waiting areas is not an identity problem. It is an **object custody** problem. The question is not *"who is this person"* — it is:

> Is the person walking away with the bag the same one who set it down?

That reframing is the whole project. It means the system needs no facial recognition, no identity database, and no biometric storage — only the ability to track which bags belong to which travelling group.

## What this is not

- **Not a face recognition system.** Permanently out of scope.
- **Not a theft detector.** The base rate makes a binary theft alarm mathematically useless — roughly 1 real theft per 30,000 custody events. Even a 0.1% false positive rate yields ~3% precision.
- **Not autonomous.** It ranks clips for a human operator who is already watching the cameras. It never decides, never accuses, never identifies.

The actual goal: **reduce ~600,000 daily custody events to a few hundred ranked clips worth an operator's time.**

## Approach

```
video ─► Detector ─► GroundPlane ─► PersonTracker ─► BagRegistry
                                                          │
                          PartyManager ─► CustodyFSM ─────┤
                                                          ▼
                                     FlagEngine ─► AlertQueue ─► ranked clips
```

Four principles carry the design:

1. **Ownership only flows through strong bonds.** If group membership were granted by proximity alone, a thief would sit next to you for three minutes and become the legitimate owner. Arriving together and walking together count; sitting nearby does not.
2. **A stationary bag is a spatial anchor, not a re-ID problem.** Identical black suitcases defeat appearance matching. Position is identity.
3. **Uncertainty suppresses alerts, never generates them.** There is a first-class `AMBIGUOUS` state.
4. **Flags are relational, never attributive.** "Approached bags from three different groups in eight minutes" is evidence. "Carries no luggage" is profiling — it fires on 20-30% of an airport and systematically marks airport workers.

## What is built

Everything below the perception layer, testable without a single frame of video:

| Module | Responsibility |
|---|---|
| `types.py` | Shared vocabulary. All positions in ground-plane metres, never pixels |
| `ground_plane.py` | Homography; projects the *base* of a box, where the object meets the floor |
| `detectors/` | Swappable `Detector` protocol; YOLO26 adapter |
| `tracking.py` | Tracks to observations; reads real fps rather than assuming one |
| `party.py` | Group formation, strong vs weak bonds |
| `bag_registry.py` | Spatial anchors, `AMBIGUOUS` state |
| `custody.py` | Attendance and removal state machine |
| `flags.py` | Relational flags, exponential decay |
| `alerts.py` | Ranked queue, clip window, explanation |
| `metrics.py` | P_miss @ RFA |

`party.py` went through three rounds of adversarial review and was rewritten twice. Both times the same class of defect: a movement metric that looked reasonable and quietly handed a thief legitimate ownership. First matching trajectories by list index instead of timestamp — following someone eight metres behind counted as walking together. Then measuring accumulated path length, where pacing in place, or detector noise alone, cleared the threshold. The module now measures trajectory extent, and the test suite pins all three metrics so reverting to either broken one fails.

## Evaluation

Not mAP. **P_miss @ RFA** (miss probability at a given false-alarm rate per minute), the NIST ActEV standard for this problem family, plus ranking quality — where the true event lands in the queue.

Validation on [PETS2007](http://www.cvg.reading.ac.uk/PETS2007/data.html), which contains labelled attended-luggage-removal (theft) scenarios and ships camera calibration.

## Stack

Python 3.11+ · [uv](https://docs.astral.sh/uv/) · YOLO26 behind a swappable `Detector` interface · ByteTrack/BoT-SORT · OpenCV

## Licence

[AGPL-3.0](LICENSE), inherited from Ultralytics. The `Detector` interface exists partly so that swapping to an Apache-2.0 model (RF-DETR, D-FINE) stays a contained change.
