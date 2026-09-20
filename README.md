# custody-watch

Detecting **luggage custody changes** in airport surveillance video — not faces, not "suspicious people".

> **Status:** runs end to end, from an `.mp4` to a ranked review page for a human operator. 397 tests, CI green.
>
> **On a generated theft, it found the thief.** A 40-second gate-area theft made with Veo: the thief is the only top-severity item in the queue, with the right bag and the right owner group. See [The first footage that is not a dataset](#the-first-footage-that-is-not-a-dataset).
>
> **On real staged thefts, it missed all four.** MEVA has four annotated bag thefts on real cameras, and the detector never saw any of the four bags at rest — so no custody ever existed to lose. False accusations on the same footage went to zero. See [Real footage](#real-footage-meva-and-ucf-crime).

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

Run it on a video file. This is the path where both halves run — detector and logic — and the only one that produces a review page from something other than annotation:

```bash
uv run python scripts/watch_video.py scene.mp4 --calibration ground.json
uv run python scripts/watch_video.py scene.mp4 --metres-per-pixel 0.0085
```

**There is no silent default for the ground plane.** One of the two is required: a measured calibration, refused above 25 cm of residual *on its worst point*, or an explicit scale. Every threshold in this system is in metres, and a guessed plane makes all of them lie without a symptom — nothing breaks, the numbers just start describing a different scene.

A uniform scale now says when it cannot describe the scene. If the people in frame differ in height by more than 2×, the view has perspective, the background is compressed past every distance threshold, and the run warns with the measured ratio. Both scenes this project has seen fail that test: CAVIAR at 3.3×, the gate clip at 11.3×.

Or watch it live. `watch_video.py` shows nothing until the file ends, and a camera has no end; this serves the annotated video, the operator's queue and the custody events to a browser while the scene is still happening:

```bash
uv run python scripts/serve.py scene.mp4 --metres-per-pixel 0.006 --passo 2
uv run python scripts/serve.py 0 --calibration ground.json
uv run python scripts/serve.py rtsp://camera/stream --calibration ground.json
```

and open `http://localhost:8765`. A file is processed without loss, on its own clock — `--passo N` skips frames without shrinking time. A camera (index or URL) always hands over the newest frame, timed by the wall clock: processing every frame of a camera faster than the detector would drift further behind for the whole shift. It listens on this machine only. Listening beyond it with `--host` is refused without `--cert` and `--key`: plain HTTP on a network is the camera open to anyone on the same segment, and a warning in the terminal protects no one. On the gate clip, CPU only, `--passo 2` runs at about real time and the thief is the only N3, ranked first, and reaches the screen while the clip is still playing — not after it ends.

**The operator can point at what the detector cannot see.** On the live screen, *Apontar bagagem* turns a click into an anchor at that spot, and *Apontar dono* attaches an owner to it; both are recorded in the event log as `origem: operador`, so a human assertion is never confused with a detection. This exists because of a measurement, not a hunch: across the four MEVA thefts, in the 90 s each bag sits still before it is taken, the detector produces **zero** bag boxes over it — and neither a bigger model (`yolo26x`) nor a 4× zoomed crop changes that zero, even at a confidence of 0.05. A pointed anchor is watched by the same rules as any other: it goes unattended when its owner walks away, and a stranger who touches it is ranked. It buys nothing it cannot see, though — with no bag detections there is no removal to observe, so theft of an invisible bag stays out of reach.

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

Sweep how good perception has to be, one noise axis at a time:

```bash
uv run python scripts/envelope.py
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
| `orchestrator.py` | Wires it together; consumes frames, emits a queue — in batch or one frame at a time |
| `aovivo.py` | The live loop: file on its own clock, camera always on its newest frame |
| `painel.py` | What the live screen shows: queue, custody events, annotated frame, pointed anchors |
| `servidor.py` | Standard-library HTTP: the page, MJPEG video, state as JSON |
| `clips.py` | Annotated GIF cut around the gravest signal |
| `report.py` | The operator's review page |
| `review.py` | Queue, clip and page composed — including the identity translation between them |
| `noise.py` | Detector noise model: drop bursts, position error, ID switches |
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

## Perception: measured, and not where we thought

`YoloDetector` existed, was tested against a fake, and had never once been instantiated. Every result this project produced came from ground-truth boxes. Running it changed what the project thinks it is — twice.

### On CAVIAR, at 384×288

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

On this data, perception dominates everything downstream, and none of the numbers below say anything about whether the system can see.

### At 720p, the same weights

On the 40-second gate clip, the same weights file finds the suitcase in **1,223 frames** with a median box height of **166 px**, and holds it through two ID switches while it is being carried away.

The 0.1% is a property of a 2004 dataset at 384×288, not of the detector. Which means the sentence this README led with — *the detector cannot see luggage* — was true about the material and false about the tool.

### Which axis actually breaks the logic

"How good does perception have to be" is not answered by measuring the detector. It is answered by degrading the input and watching the logic fail, one axis at a time, with the other axis clean — the measured person noise alone destroys the pipeline, so sweeping both at once measures nothing.

The column that decides is **retained**: how many of the true events of a clean run survive. Under noise the system does not cry wolf, it **goes mute**, and a false-alarm rate of zero produced by silence reads exactly like success.

| person detections lost | ownerships (of 4) | events retained | |
|---|---|---|---|
| 0% – 40% | 4.0 | **100%** | operates |
| 80% | 0.8 | 17% | mute |

| bag detections lost | ownerships | events retained | |
|---|---|---|---|
| 0% – 20% | 4.0 | 92 – 100% | operates |
| 40% | 4.0 | 75% | operates |
| 80% | 1.8 | 25% | mute |

Position error is survivable to 8 px on either axis: 96% retained for people, 79% for bags.

**Both halves of perception now break in the same place** — around 40% of detections lost, or 8 px of position error. That symmetry is new. The version of this system that this README described before broke at **5% of person detections lost**, losing 40% of ownership with it, because ownership hung on the single frame in which a bag was first seen.

The bag axis was more robust then and is slightly less so now: a bag has to be *seen* settling before it becomes an anchor, so a dropped frame costs a sample. Going from 96% to 75% retained at 40% bag loss is what buys not being mute at 4 px of bag position error — and position error is the one the measurement says is likely, because the bag error has never been measured at all. One paired detection in 1,686 is not a sample.

That is not the same as "the logic half is fine" — it had four serious defects that no dataset could have shown, and a fifth that only a full-length scene could. See [Defects no dataset could expose](#defects-no-dataset-could-expose).

## The first footage that is not a dataset

Forty seconds of a boarding gate, generated with Veo: a couple arrives with a suitcase, stops, looks at their phones. A man in black enters from the right and walks off with the bag while they keep looking down.

It is not real footage, and it is not evidence about real thefts. It is the first time both halves of this system ran together on something nobody annotated by hand.

**The first run put the owner at the top of the queue,** accused of stealing his own suitcase two seconds after walking into frame, with a random traveller second and the thief eighth. Every traveller who enters pulling a bag was that case — in an airport, nearly everyone.

The cause was one line. Ownership was decided in the single frame where a bag first appeared; people only exist for the logic after their re-ID settles; so the bag was born orphan, and an orphan bag that moves resolves as removal by a stranger.

The fix changed what the registry *is*. It is now **a registry of anchors**: a bag enters only after it has stayed put, because rule P2 says position is identity *precisely because a stationary bag does not teleport*, and a bag in transit has no such property. And ownership is now **the deposit** — whoever was within reach when the bag arrived at the place where it came to rest.

| | before | after |
|---|---|---|
| `bag_appeared` | 20 | **6** |
| `bag_ambiguous` | 3 | **0** |
| the owner | **1st in the queue, N3** | not in the queue at all |
| the thief | 8th, N2 "touched a bag" | **N3, named, with the right bag and the right owner group** |
| review page | 142 MB | 22.6 MB |

The theft comes out as one line of log:

```
34.67  bag_removed_by_stranger  bag=12  subj=533  party=2
```

Two seconds after the bag started moving, across two tracker ID switches that anchor re-adoption recovered at 2 cm and 35 cm.

**What it does not prove.** One staged clip is one sample, and a generated one: no real sensor noise, no compression artefacts, no crowd. The false alarms that remain in that run are all bags in the *background*, which a uniform scale reads as standing still — a calibration failure, not a logic one, and the reason the tool now refuses to stay quiet about it. The theft survives a 5× range of scale error. The noise does not.

## Real footage: MEVA and UCF-Crime

[MEVA](https://mevadata.org/) is the dataset behind the NIST ActEV challenge — the same programme whose `P_miss @ RFA` metric this project uses. It is staged by actors, on real cameras, CC BY 4.0. Of its 2,212 annotated clips, **four contain a bag being stolen and one a bag being abandoned**. [UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/) is 1,900 unfiltered surveillance videos; searching all 23,542 sentences of its [UCA](https://github.com/Xuange923/Surveillance-Video-Understanding) descriptions, one involves a suitcase.

| clip | what happens | did the detector see the bag? | result |
|---|---|---|---|
| UCF-Crime `Stealing057` | someone rifles through a suitcase left in a waiting hall | yes | right owner, unattended at the right time; the man rifling through it gets no flag (#49) |
| MEVA bus 13:15 | bag theft | **no** — cut off by the bottom of the frame | missed |
| MEVA school G421 | bag theft | **no** — dark, against the light | missed |
| MEVA bus 14:55 | bag theft | **no** — dark bag on a dark bench | missed |
| MEVA bus 15:10 | bag theft | **no** | missed |
| MEVA bus 15:15 | abandonment | yes | not recognised as abandonment; the person who left it gets a contact flag at the right moment |

**`P_miss` on real bag thefts: 4 of 4.** Not one of them reached the logic. At minimum confidence, YOLO does see *something* where each bag sits — at 1–9%, confused with a chair, a bench, a laptop. Lowering the threshold would make the chairs luggage too.

### The false accusations were the logic's, and are gone

The same footage produced five false top-severity accusations, and all five were bags passing the rest test without having been put down:

- **cut off by the frame border** — the foot of the box becomes the edge of the image, so the projection never moves. One of these made the thief the owner of the bag he was carrying.
- **too small** — below 40 px a bag's position on the floor is detector noise
- **carried** — a backpack on someone standing still stands still too. Stopping is not depositing.

Each is now a veto on becoming an anchor, answered in pixels before projection (#48). **Five false accusations became zero**, and on the generated clip the thief became the only top-severity item at the scale used all along.

### A better detector sees the bags, and still does not catch the thefts

On the 32 frames before the four thefts, at a matched level of extra detections:

| detector | bag found | thefts where the bag is seen | CPU time per frame |
|---|---|---|---|
| YOLO26s (current) | 1/32 | 1/4 | 0.09 s |
| YOLO26x | up to 11/32 | 2/4 | 0.46 s |
| OWLv2, open vocabulary | 19/32 | 4/4 | 4.30 s |

OWLv2 sees the bags. Plugged into the pipeline it still catches none of the thefts, and adds false accusations — sampled every 2 s it fabricates stillness between samples, and even at 5 frames a second the bag flickers around threshold at rest, vanishes at the moment it is picked up, and jumps too far between frames once carried for anything to connect it back to its anchor. The measurements are in #51.

On this footage the gap is not one component. It is the handover between *seeing a bag sit* and *seeing the same bag leave*, and that handover is where the next work is.

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
LeftBag                       1        1      0        0        1         0         0   +10.6s
LeftBag_AtChair               1        1      0        0        1         0         0   +10.0s
LeftBag_PickedUp              1        1      0        0        1         0         0   +10.0s
LeftBox                       1        0      0        1        0         0         0

P_miss @ RFA 0.5/min : 0.00      positivos medidos: 3      falsos alarmes: 0
```

(Tooling speaks Portuguese; the README does not. `medivel` / `curto` / `incerto` are the three verdicts on an annotated positive — measurable, too short to be an instance at this threshold, or cut off before it could be judged. That third one is the one that matters: without it, "we cannot tell" is silently counted as "the system missed it", which is the easiest way to manufacture a bad `P_miss` out of short material.)

Three positives carry a confidence interval of roughly ±40 pp, so `0.00` is not a claim about the system. What it proves is that the harness runs end to end on real annotation.

The lag column earns its own note: **+10.0 and +10.6 — the threshold in use, plus the time it takes to confirm the bag is at rest**, because the event fires when the state *completes* its duration. A symmetric ±2 s matching window would mark all three as missed *and* spurious at once, producing `P_miss = 1.0` with no relation to the system. That was an argument in the spec; now it is a measurement.

`LeftBag` is the one that carries the extra 0.6 s: it is the only clip where the bag is *placed* on camera rather than already sitting there, so the two seconds the registry spends confirming it is at rest land inside the measured lag. The spurious column is empty for the first time — the one entry it used to carry was a bag that had left the scene with its owner being reported as unattended, which is now suppressed as uncertainty rather than counted as evidence.

### Defects no dataset could expose

The logic half was carrying four chained defects that CAVIAR cannot show, because in none of its clips does anyone stand in front of a stationary bag for long enough.

A passer-by occluding a bag for 0.2 s produced a top-severity theft accusation against them; the bag then died, erasing the genuine abandonment; and a theft committed in plain sight emitted nothing at all, because removal was only ever detected by *disappearance*. The ranked queue — the system's entire output — listed the innocent bystander and never mentioned the thief.

All four were found by a twelve-line synthetic scenario run against `run_session`, and are fixed.

### And a fifth that only a full-length scene could

The four above are about what happens *around* a bag that is already established. The fifth was about how a bag becomes established at all, and neither a synthetic scenario nor an annotated dataset could show it, because both hand the system a bag that is simply there.

Ownership was decided in the single frame where a bag first appeared. The noise sweep found the size of it — **5% person detection failure cost 40% of ownership**, because a 12-frame failure burst means the owner is absent in 39% of frames and that one frame of decision is a coin toss. The generated clip then showed what it costs at the other end: with no owner, an orphan bag that moves is a removal by a stranger, so the man who walked in pulling his own suitcase was the top of the queue.

Both ends come from the same assumption, and fixing it meant stating what the registry is for: **it holds anchors, and ownership is the deposit.** A bag enters after it stays put; whoever was within reach when it arrived owns it. Chasing the owner afterwards would have reopened the attack rule P1 exists to close — a thief who walks up to an unclaimed bag becoming its legitimate owner — so the window that lets the system keep asking is bounded, and the answer it keeps is the one from the moment the bag arrived.

**Real footage with thefts in it now exists in the loop**, and it moved the blocker. Four staged thefts on real cameras were all lost before the logic saw them — see [Real footage](#real-footage-meva-and-ucf-crime).

One requirement for that footage came out of the measurement, and would not have been guessed: the camera has to keep rolling for `max_occlusion_s` **after the bag leaves frame** — 30 s by default — and not merely for `unattended_time_s` after the abandonment. A scene that ends when the bag does produces an unresolvable event rather than a measurable one.

## Stack

Python 3.12 · [uv](https://docs.astral.sh/uv/) · YOLO26 behind a swappable `Detector` interface · OpenCV · Pillow

## Licence

[AGPL-3.0](LICENSE), inherited from Ultralytics. The `Detector` interface exists partly so that swapping to an Apache-2.0 model (RF-DETR, D-FINE) stays a contained change.
