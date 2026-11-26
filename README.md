# custody-watch

[![checks](https://github.com/marcus-vreis/custody-watch/actions/workflows/checks.yml/badge.svg)](https://github.com/marcus-vreis/custody-watch/actions/workflows/checks.yml) [![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE) ![Python >=3.11](https://img.shields.io/badge/python-%E2%89%A53.11-blue)

custody-watch is research software that ranks luggage custody changes in airport surveillance video for a human operator to review — not faces, not "suspicious people".

> **Status:** runs end to end, from an `.mp4` to a ranked review page for a human operator. Local CPU verification on Windows (2026-11-25): `pytest` 1700 passed, 1 skipped; Node tests 91/91. Not verified on GPU or on a physical camera.
>
> **On a generated theft, it found the thief.** A 40-second gate-area theft made with Veo: the thief is the only top-severity item in the queue, with the right bag and the right owner group. See [The first footage that is not a dataset](EVALUATION_HISTORY.md#the-first-footage-that-is-not-a-dataset).
>
> **On real staged thefts, it missed all four.** MEVA has four annotated bag thefts on real cameras, and the detector never saw any of the four bags at rest — so no custody ever existed to lose. False accusations on the same footage went to zero. See [Real footage](EVALUATION_HISTORY.md#real-footage-meva-and-ucf-crime).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/camera-wall-dark.jpg">
  <img src="assets/screenshots/camera-wall-light.jpg" alt="Multi-camera replay lab: two synthetic cameras side by side with a person and a bag outlined, a synchronized timeline above, and a floor plan on the right.">
</picture>

*The multi-camera replay lab on the synthetic two-camera fixture — no weights, video or GPU. Boxes come from the fixture, not from a detector.*

## Quickstart

From a checkout of this repository, with [uv](https://docs.astral.sh/uv/) installed (it provides Python 3.11 or newer). The demo needs no weights, video, dataset or GPU: it replays a synthetic two-camera fixture whose boxes are ground truth, so it shows the interface and the data flow, not detection.

```bash
git clone https://github.com/marcus-vreis/custody-watch.git
cd custody-watch
uv sync
uv run python -m custody_watch demo
```

Then open `http://127.0.0.1:8766` (extra arguments after `demo` pass through to `scripts/serve_multicamera.py`, for example `--port`). `uv sync` installs the project's declared dependencies, including Ultralytics and its PyTorch requirement, so the first install is large even though the demo itself loads no detector. A fresh-clone install has not been re-verified for this README. Other subcommands (`watch`, `serve`, `replay`, `lab`, `evaluate`) are listed in [Running it](#running-it); analysing a real video additionally needs detector weights and a ground-plane calibration.

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

One entry point wraps the scripts below (from a checkout; arguments pass
through untouched, so `serve --help` shows the script's own help):

```powershell
.venv\Scripts\python.exe -m custody_watch demo   # synthetic lab, no weights, video or GPU
```

| Subcommand | Script |
| --- | --- |
| `watch` | `scripts/watch_video.py` |
| `serve` | `scripts/serve.py` |
| `replay` | `scripts/replay_multicamera.py` |
| `lab` | `scripts/serve_multicamera.py` |
| `evaluate` | `scripts/evaluate_recorded.py` |
| `demo` | `lab` on `tests/fixtures/multicamera/two-cameras/registry.json` |

The web panel currently handles one source. An offline camera registry and
synchronized prediction replay now provide the first part of a virtual
multi-camera lab, without weights, video downloads or a GPU:

```powershell
.venv\Scripts\python.exe scripts/replay_multicamera.py tests/fixtures/multicamera/two-cameras/registry.json --output outputs/multicamera-demo
```

Use a new output directory. The example exports `summary.json` and
`frames.jsonl` from synthetic observations, with camera/segment-qualified IDs
and explicit timing uncertainty. Open the visual lab with:

```powershell
.venv\Scripts\python.exe scripts/serve_multicamera.py tests/fixtures/multicamera/two-cameras/registry.json
```

Browse to `http://127.0.0.1:8766` (or set `--port`). The server checks the `Host`
header (DNS-rebinding defence) and answers `421` for any name other than
`localhost`, `127.0.0.1` and `[::1]` on its own port; behind a local tunnel or
proxy, add a name with `--allowed-host NAME` (repeatable, name only, no port).
The local-only interface has
a camera mosaic, a floor-plan view, camera selection, playback/pause, shared
seeking and speed controls. Synthetic fixtures render schematic views, clearly
labelled as having no video. Locally available video sources serve decoded JPEG
frames; decoding was tested with generated MJPG footage, without a detector or
GPU. Missing/stale/completed cameras retain explicitly historical images, with
their own timestamps and ages. Cross-camera identity and custody remain planned.
Links can select an instant and camera with `?t=2.5&camera=Camera_0019` on the
NVIDIA sample. The camera's floor is selected and time is clamped to the replay.
See the [multi-camera contract and roadmap](MULTICAMERA.md) for data sources,
coordinate conventions and incremental acceptance criteria.
Public NVIDIA and WILDTRACK clips have now been imported and replayed in oracle
mode. The [validation record](PUBLIC_DATASET_VALIDATION.md) documents a ground
projection failure under occlusion. MTMMC is outside the active personal-project plan.
These runs do not validate detection, cross-camera identity or luggage custody.

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

and open `http://localhost:8765`. A file is processed without loss, on its own clock — `--passo N` skips frames without shrinking time. A camera (index or URL) always hands over the newest frame, timed by the wall clock: processing every frame of a camera faster than the detector would drift further behind for the whole shift. It listens on this machine only. Listening beyond it with `--host` is refused without `--cert` and `--key`: plain HTTP on a network is the camera open to anyone on the same segment, and a warning in the terminal protects no one. The `Host` header is checked too (`421` otherwise): with `--host 0.0.0.0` only IP literals pass, so to reach the screen by a network name add `--allowed-host NAME` (repeatable, name only, no port). On the gate clip, CPU only, `--passo 2` runs at about real time and the thief is the only N3, ranked first, and reaches the screen while the clip is still playing — not after it ends.

`GET /healthz` is a liveness probe for monitoring: it answers `200` with `{"status":"ok"}` when the process is serving, passes the same `Host` check as the other routes, and exposes no video, people, bags, configuration or source state (use `/estado` for the source). The replay laboratory answers the same route.

The screen separates **server connected** from **receiving images**. Its source
banner distinguishes initialization, fresh reception, no recent frames,
interrupted capture, completed file and requested shutdown. The last image
remains visible with an explicit historical marker after interruption or file
completion. HTTP still answering does not make an old image live.

`--source-timeout` sets the positive, finite number of seconds without a local
capture before the source is marked stale (default 5 s, an operational setting,
not a universal camera requirement). Source age is recomputed on each state
request, including while a capture read is blocked. A camera receiving new,
visually identical frames stays healthy. The screen also shows the age of the
capture used for the displayed image, so fresh reception cannot hide slow
processing. These are local monotonic measurements; they do not measure delay
upstream of the capture backend. Source health never creates custody flags.
Application URL source labels omit credentials, the URL path and query strings.

Reconnection is opt-in and requires the incremental journal and an existing local
weights file (no automatic download during a retry):

```powershell
.venv\Scripts\python.exe scripts/serve.py 0 --calibration ground.json --weights C:/models/yolo26n.pt --journal-dir outputs/journal --reconnect-attempts 3
```

`--reconnect-backoff` starts the exponential wait (default 1 s), capped by
`--reconnect-max-backoff` (30 s). The retry budget applies to each interruption;
publishing a frame from the new segment renews it. Cancellation interrupts capture
waiting and backoff. `--reconnect-close-timeout` (1 s) bounds waiting for the old
capture thread to release its resource. If native open/read/release is still
blocked, the supervisor fails visibly and opens no replacement. It cannot forcibly
cancel native backend calls. Exhaustion, permanent errors declared by the factory,
processing failures and journal failures remain visible. OpenCV authentication
failures are not classified as permanent automatically; they consume the bounded
retry budget. Finite files are never reopened.

A reception gap longer than the source timeout ends the association, even if the
same connection resumes. Each recovered segment creates a new detector/tracker
and `LiveSession`; raw IDs, groups and ownership do not cross the gap. A new bag
observation needs new ownership evidence. The old segment stays in the journal,
with its reason, local monotonic timestamp and observed gap bounds. Event times
are local to their segment. No event is inferred inside the unobserved interval.
Tracker reconstruction is sequential: the installed Ultralytics 8.4.126 source
uses a global tracker ID counter, so concurrent reset is unsafe. Backend load time
is reported as `recovery.detector_load_s`; it has not been measured with real
weights here. This source inspection and synthetic CPU tests do not establish
physical RTSP or GPU behavior.

The recovery screen uses a JPEG qualified by segment at each state refresh
(about 2 Hz), so a click cannot adopt metadata from a different MJPEG connection.
Pointing requests include that segment ID and are refused after a segment change
or while the image is historical. The server continues serving the final state
after retries fail; Ctrl+C closes it.

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

`evaluate.py` exits non-zero and refuses to print a `P_miss` when no annotated positive is measurable at the configured threshold — see [Evaluation](EVALUATION_HISTORY.md#evaluation). To score at a threshold this dataset can actually express:

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

The review HTML has a total default budget of **15,000,000 UTF-8 bytes**, shared
by all sessions. Configure it with `--report-max-bytes` in `build_report.py` or
`watch_video.py`, or `max_bytes` in `render_report`/`write_report`. The budget
includes text, styles and the base64 expansion of embedded GIFs.

When clips do not fit, lower-priority clips are omitted first: smaller ranks
take precedence, with input session/item order breaking ties. Media forms a
priority prefix; if the first clip is too large, no lower-ranked clip replaces
it. All queue entries, timestamps, scores and explanations stay in the HTML,
with explicit placeholders and a notice about omitted media. Source GIFs are
not modified or deleted. A small report embeds all clips as before.

Only selected GIFs are read, with bounded reads and a size-change check. If
text and placeholders alone cannot fit, report generation refuses rather than
truncating decisions. Validation happens before writing the destination, so
a budget refusal preserves a previous report. This is a page-size budget,
not a budget for video processing, clip generation or peak Python memory.

### Replaying recorded predictions in CPU

`evaluate_recorded.py` runs the logic over a frozen detection/tracking cache.
It does not instantiate a detector, decode video, download weights or use a GPU.
The bundled example is an original synthetic trajectory fixture under CC0-1.0;
it is a software check, not a result on real footage:

```powershell
.venv\Scripts\python.exe scripts/evaluate_recorded.py tests/fixtures/recorded/synthetic-deposit/manifest.json --output outputs/replay-synthetic-01
```

The output directory must be new. It contains `summary.json`, `events.jsonl`
and `metrics.csv`; earlier evaluations are never overwritten. Exit codes are
`0` when at least one requested class has a measurable operating point, `1` for
invalid inputs or an I/O failure, and `2` when no requested class can be scored
(no measurable positives, or unresolved predictions under censored annotation).
The last case still exports the counts and false alarms, with `P_miss` absent
and a refusal reason. A negative control cannot establish a miss probability.

Start with the example manifest when preparing a real sequence. Schema version 1
requires all of the following:

| Field | Contract |
|---|---|
| `sequence_id`, `mode` | Annotation `session` must equal the sequence ID. `mode` is `predictions` or `oracle`. |
| `source` | `kind` (`video` or `synthetic`), origin, redistribution conditions and source file identity. |
| `predictions` | Cache file identity and `provenance`: `detector` for real predictions, `synthetic` for synthetic traces, `ground_truth` only in `oracle` mode. |
| `annotations`, `config` | File identities for event annotations and the existing config JSON. |
| `geometry` | `uniform_scale` with explicit `metres_per_pixel` and a written limitation, or `calibration` with a measured calibration file identity. The existing residual check still applies. |
| `clock` | Original `fps`, observation bounds `start_s`/`end_s`, and total source `frame_count`, before any skipping. Timestamps use the same seconds as annotations. |
| `classes` | Unique subset of `person`, `suitcase`, `backpack`, `handbag`; every cached detection must belong to it. |
| `producer` | Code revision, detector and tracker names **with versions**, weights SHA-256 (or null when not applicable), environment versions, original command as an argument list, and named integer seeds (empty if deterministic). |
| `evaluation` | Explicit event `kinds`, `lag_window_s`, `slack_before_s`, and per-class false-alarm budget `target_rfa` in events/minute. |

A file identity is `{ "path": "relative/file.json", "size_bytes": 123,
"sha256": "64 lowercase hexadecimal digits" }`. Paths are relative to the
manifest. The source video's `path` may be null for offline replay; its size and
hash remain required metadata, and the report then says that the source file
was **not verified**. Every other referenced file is required and verified
before the logic runs. Hashes identify bytes, not the accuracy of annotations
or the truthfulness of a declared provenance.

Each line of the UTF-8 cache is one frame record:

```json
{"schema_version":1,"frame_index":0,"t":0.0,"status":"observed","detections":[{"track_id":9,"cls":"suitcase","bbox":[210,150,250,200],"touches_bottom":false}]}
```

Boxes use image pixels in `xyxy` order; projection happens during replay.
Optional `appearance` is the existing session-scoped HSV signature as a flat
list, not a new embedding format. Preserve it, along with `touches_bottom`,
when the original run used it: omitting it changes the inputs to re-ID.
Caches containing real appearance signatures belong in private local research
storage with an explicit retention policy, never in the public fixture.

An observed frame with `detections: []` means the detector found nothing.
`status: "missing"` requires an empty list and means no frame was observed.
Missing frame indices are also counted against `frame_count`. Neither is
filled with repeated boxes or ground truth. Only observed frames feed the
session; their original timestamps are retained. Replay freezes tracker output:
it does not simulate rerunning the tracker at a different input frame rate.

Use the existing event-annotation format (`session`, `events`) with original
timestamps. `bag_unattended` positives are eligible only after the configured
duration; short completed states and censored states are counted separately.
Unmatched predictions in an unresolved censored interval are reported as
unscored, not asserted to be false. When such predictions exist, the class's
false-alarm rate and operating point are refused: dropping unknown predictions
must not manufacture a zero false-alarm result. Positive eligibility never
depends on whether the detector managed to see the object.

The summary preserves input identities, resolved configuration, producer
metadata, evaluator revision/dirty state when available, a fingerprint of the
logic source, runtime versions, emitted events and annotations. It reports
per-class matches, misses, spurious events, delays and the full declared
observation duration, including missing frames. No wall-clock run time is used
as the false-alarm denominator.

**Protocol limits:** matching currently uses event kind and the declared
asymmetric time window; annotation `bag`/`subject` IDs are not an identity
accuracy metric. Events have binary score 1, so this evaluator measures event
presence at an all-or-none threshold, not the ranked operator queue. Equal
scores enter a threshold together. Oracle traces are labelled in both console
and exports and must be reported separately from detector results.

Real MEVA/UCF comparisons are not bundled or claimed reproduced here. Obtain
footage and annotations through the [MEVA](https://mevadata.org/) and
[UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/) providers under their
applicable terms, generate tracked predictions separately, and record that
producer's actual versions, parameters, weights hash and command. Keep videos,
real caches, credentials and personal paths out of Git. The CPU evaluator does
not acquire those inputs or infer missing provenance.

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
| `event_journal.py` | Incremental journal, exclusive rotation, segment lifecycle and write acknowledgements |
| `journal_reader.py` | Streaming validation and explicit recovery of an interrupted journal tail |
| `orchestrator.py` | Wires it together; consumes frames, emits a queue — in batch or one frame at a time |
| `aovivo.py` | The live loop: file on its own clock, camera always on its newest frame |
| `source_health.py` | Bounded source health, local capture age and displayed-image age, independent of custody |
| `painel.py` | What the live screen shows: queue, custody events, annotated frame, pointed anchors |
| `servidor.py` | Standard-library HTTP: the page, MJPEG video, state as JSON |
| `clips.py` | Annotated GIF cut around the gravest signal |
| `report.py` | The operator's review page |
| `review.py` | Queue, clip and page composed — including the identity translation between them |
| `noise.py` | Detector noise model: drop bursts, position error, ID switches |
| `metrics.py` | P_miss @ RFA |
| `annotations.py` | Ground truth as events, and asymmetric matching against what was emitted |
| `recorded_predictions.py` | Validated, versioned manifests and tracked-prediction caches for CPU replay |
| `recorded_evaluation.py` | Reproducible event evaluation with input provenance and explicit scoring refusals |
| `caviar.py` | Dataset reader |

## Auditability

### CPU duration and state budgets

The CI smoke in `tests/test_session_lifecycle.py` exercises deterministic ID
turnover, repeated occlusions, frame discard, quiet intervals, interrupted and
blocked capture, recovery and exhausted retries. It instantiates no detector,
camera, network source or GPU. It checks structure and lifecycle, never a host's
FPS or memory tolerance. For a separate opt-in benchmark:

```powershell
.venv\Scripts\python.exe scripts/soak_session.py --frames 12800 --sample-every 400 --seed 7 --profile-memory --output outputs/soak-prefixes
.venv\Scripts\python.exe scripts/soak_session.py --wall-seconds 30 --sample-every 4000 --seed 7 --profile-memory --output outputs/soak-wall
```

The second command targets 30 seconds of actual CPU exercise, stopping after a
frame and then finalizing logs/fault cases. Neither command is a real camera
soak. Outputs must be new directories. `summary.json` records revision, dirty
state, source hashes, environment, command, seed and configuration. Streaming
`snapshots.jsonl` and `curves.csv` expose component counts, cumulative inclusive
stage timings, frame counters, thread counts, heap and process-memory samples.
Ctrl+C closes the journal and writes an interrupted summary referring to the
last complete checkpoint; the journal can contain additional completed records.

`SessionProbe.RETENTION` describes every measured collection and the references
that prevent blind expiration. No retention policy is changed by this harness.
The default threshold of 64 identities is a diagnostic stress budget for a scene
with at most three tracks per frame, not an approved deployment capacity. History
and pending-sample budgets allow their configured windows; events have an explicit
64-item tail. Optional `--max-rss-mib` and `--max-feed-ms` are host-specific
budgets; unavailable RSS cannot silently pass a requested RSS budget. Exit 2
means an exceeded/unverified budget or failed fault check; results remain saved.

Observed Windows CPU results (one run per mode, seed 7, traced allocations on):

| Mode | Available / processed / discarded frames | Simulated seconds | Actual seconds | Result |
|---|---|---:|---:|---|
| Fixed prefix | 12,800 / 12,359 / 441 | 2,559.8 | 1.645 | diagnostic budgets exceeded |
| Actual-duration run | 92,026 / 88,853 / 3,173 | 18,405.0 | 30.208 | diagnostic budgets exceeded |

Sources: [prefix results](benchmarks/cpu-synthetic-2026-11-04/prefixes/summary.json),
[duration results](benchmarks/cpu-synthetic-2026-11-04/wall-30s/summary.json), with
raw snapshots and curves beside each summary. These were measured in a dirty
working tree based on `38832f8`; source hashes qualify the exact harness version.
The actual-duration run ended with 920 bag records, 921 person histories and
889 incomplete re-ID candidates, while only 64 of 3,679 emitted events remained
in the RAM tail. All synthetic captures were released; Python thread count
returned from 1 to 1. The blocked fake correctly failed recovery before the
harness explicitly unblocked it for cleanup. These observations identify work
for #30 and related identity/history retention; they establish no production
availability, detector quality, RTSP reliability or universal throughput.

The first #30 optimization keeps a separate nonterminal index for per-frame
relational flags, removal checks and the panel's anchor list. Terminal entries
leave that index on the next query, while `all()`, ownership, track aliases and
tombstones remain available for audit. Ambiguous bags stay indexed. The
[same-prefix result after indexing](benchmarks/cpu-synthetic-2026-11-04/after-index/summary.json)
ends with 27 indexed references and all 128 historical records retained. A
structural test forbids scanning the historical dictionary in those hot paths;
another compares events, bag states, queue and ownership against full scanning.
This reduces that scan's scope without establishing a total state bound.
Registry retention, incomplete re-ID, inactive histories, flags and groups still
require separate lifecycle policies. #30 remains partially addressed.

#### Opt-in terminal bag archive

The CPU harness can move terminal bag objects and their aliases to a new local
SQLite file, while keeping active and ambiguous custody in RAM:

```powershell
.venv\Scripts\python.exe scripts/soak_session.py --output outputs/archive-new --frames 8000 --seed 7 --archive-terminal-bags
```

`terminal-bags.sqlite` is created exclusively in the new output directory.
`BagRegistry` and `LiveSession` also accept a `terminal_archive=TerminalBagArchive(path)`
keyword; the path must be new and its parent must exist. Each archive belongs
to one registry/association segment. Existing files are never reopened as new
custody, and a different file/version/namespace fails instead of borrowing IDs.
The server also accepts `--archive-terminal-bags`, requiring `--journal-dir`.
For example, using an existing calibration and local weights:

```powershell
.venv\Scripts\python.exe scripts/serve.py 0 --calibration ground.json --weights C:/models/yolo26n.pt --journal-dir outputs/journal --archive-terminal-bags --reconnect-attempts 3
```

The default stays in RAM. The server creates `terminal-SEGMENT_ID.sqlite` inside
the new journal session directory. With reconnection, archive creation is deferred
until an actual capture segment starts; the provisional session does not occupy
that filename. Each subsequent segment gets a new empty file. Old files remain
historical and never restore identity, ownership or ambiguity into the new segment.

The panel reports resident bags and terminal records with confirmed writes.
Telemetry uses an immutable snapshot from the last feed/manual bag insertion,
not a disk scan or a wait on the feed's IO lock. Alias counts are also available
in `/estado` under `journal.bag_retention`. These are confirmed counts from this
registry, not a continuous filesystem availability probe or a total memory bound.
Archive failures become a visible failed status; another feed in that session is
blocked even if the disk becomes available again. Reconnection does not hide
storage/initialization failures: it stops with `archive_failed`, releases capture
and preserves the interrupted journal. Correct the storage problem and explicitly
start a new session; there is no implicit restore or retry of custody.

Only terminal snapshots are persisted, with aliases in the same transaction.
Objects leave RAM after commit succeeds; write failure propagates and retains
the affected objects and aliases. A missing/unreadable file is an error, never
an unknown bag. Old IDs/aliases cannot be redirected to a new custody.
The journal remains the full event history and is not replaced by SQLite.

`get`, `get_by_track`, `links` and `all` retain access to archived records.
Archived bags are **detached historical snapshots**: terminal state, owner,
anchor and timestamps are frozen at archival, and modifying a returned copy
does not update the archive. Observing that ID again cannot emit `BAG_APPEARED`
or reopen ownership. A genuinely new ID at that location remains a new question.
`all()` and unfiltered historical links can materialize history; use `resident()`
or point lookups in instrumentation/hot paths. Explicit historical enumeration
is not a bounded-memory API.

The harness reports resident objects in `counts` and disk rows separately in
`archived_terminal`. It opens/closes SQLite connections per operation, requesting
a 256 KiB page-cache target; this is not a bound on total process/OS memory or disk use.
Disk history grows, and no disk purge policy is implied. Remaining histories,
groups, aliases of active bags and ambiguous custody can still exceed diagnostic
budgets: the option does not make the whole #30 soak pass or define a production SLA.

Paired synthetic result: [terminal retention, 2026-11-15](validation/terminal-retention-final-2026-11-15.json).
One run per mode, same revision/configuration/seed 7 and 8000 input frames:
80 resident bags without the archive versus 17 resident and 63 archived with it;
17 nonterminal bags (including 15 ambiguous) and all 318 event payloads matched.
Both modes still returned `budget_exceeded` for other retained state. In this
single measurement, total instrumented time was 0.43302380001114216 s versus
3.807727099992917 s and RSS was 55873536 versus 56983552 bytes: fewer resident
bag objects did **not** establish lower process memory or a performance gain.
The result identifies the clean code revision, source/output hashes and limits.
Reproduce the command above with and without `--archive-terminal-bags`, using
different new output directories; compare journal records with `kind=event`
by `payload`, excluding per-run segment/session IDs and wall-clock metadata.

`tracemalloc` measures allocations traced after its start, not every native
allocation or VRAM. On Windows, process memory uses
[GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/psapi/process-memory-usage-information)
working set, including shared and private pages; Linux uses `/proc/self/statm`.
Other platforms can report unavailable RSS. Different scopes must not be added
together or interpreted as evidence of GPU memory usage.

### Incremental journal and retention

The live command retains at most `--event-tail 1000` recent events in RAM by
default and says when disk recording is disabled. This is an event-count bound,
not a byte budget or a bound on other session structures. Batch `run_session`
and the legacy `EventLog` default still retain full history. To persist events:

```powershell
.venv\Scripts\python.exe scripts/serve.py scene.mp4 --metres-per-pixel 0.006 --journal-dir outputs/journal --journal-part-records 10000
```

Each run creates a fresh opaque session directory with exclusive numbered
parts (`000000.jsonl`, etc.). Existing sessions are never overwritten or
automatically deleted. Rotation counts all records, including lifecycle
records; it continues the same association segment and sequence. Disk retention
is unlimited until an operator applies a separate policy; this command prunes
no footage, events or checkpoints.

The version 1 envelope has `session_id`, `segment_id`, contiguous `sequence`,
`kind` and `payload`. Segment start, interruption and end are explicit. Event
payloads retain `Event.to_dict`, including negative bag IDs and human
`origem: operador`. The journal adds no frames, appearance descriptors, camera
URLs or personal paths. Source/storage failures never become custody flags.
Lifecycle payloads optionally carry `local_monotonic_s`, `gap_start_s` and
`gap_end_s`; the reader still accepts older version 1 payloads containing only
`reason`. These clocks are local to this process and are not UTC timestamps.

| Confirmation | Meaning |
|---|---|
| `pending_records` | A write/synchronization is in progress; no new acknowledgement is implied. |
| `last_written_sequence`, `written_events` | A complete line was written and flushed out of the Python buffer; it is readable before normal shutdown. |
| `synced_through` | Last sequence for which an explicit OS synchronization request returned successfully. |
| `unconfirmed_events` | Recording was not confirmed under the selected policy. The event may still exist on disk; this does not prove loss. |

Every record is flushed. `--journal-fsync` additionally requests synchronization
for each record, at a potential throughput cost. Without it, confirmed writes
can still be lost on OS/power failure; that loss window has not been measured.
An OS acknowledgement does not establish hardware power-loss immunity, and
JSONL does not make an audit log tamper-proof.

The first write/serialization/synchronization failure stops further journal
writes and appears in the state and screen. Recent events remain in the bounded
RAM tail; unconfirmed events are counted separately. Storage-health queries do
not wait on the disk-writing lock. Historical images cannot accept new
pointing requests after source interruption or completion.

Read this format explicitly and incrementally:

```python
from custody_watch.journal_reader import iter_journal

diagnostics = []
for record in iter_journal("outputs/journal/SESSION_ID", diagnostics=diagnostics):
    print(record["sequence"], record["kind"])
print(diagnostics)
```

Only the final line without LF in the final part can be skipped as a fragment,
with a diagnostic (a warning if no collector is supplied). Strict callers can
set `recover_truncated_tail=False`. Earlier corruption, unknown versions,
duplicate/missing sequences, incompatible segments and invalid part names are
errors. An open segment at EOF is reported as incomplete when diagnostics are
requested. The reader stores part names, seen segment IDs and one decoded
record; metadata memory grows with parts/segments rather than all event content.

Legacy JSONL remains readable through `EventLog.from_jsonl`, separately from
this envelope. Exporting a bounded `EventLog` exports only its retained tail.
Journal replay inspects decisions; it never restores a tracker, person identity,
bag ownership or visual state after restart.

Every decision emits an event carrying the numbers that produced it. Those decisions can be inspected from the log without the video:

```json
{"kind":"bag_owned","t_start":10.0,"bag":100,"party":1,"evidence":{"party":1}}
{"kind":"bag_unattended","t_start":50.0,"bag":100,"party":1,"evidence":{"distance_m":20.0,"elapsed_s":30.0}}
{"kind":"bag_removed_by_stranger","t_start":58.0,"subject":99,"bag":100,"party":1,"evidence":{"carrier":99,"owner_party":1}}
```

This is why thresholds live in `config.py` behind declared safe ranges: making them tunable reopens every attack three rounds of adversarial review closed, so a value outside its range is rejected with the reason it exists.

### Optional webhook export

A separate process can forward journal events to a VMS or operations centre.
It only reads the session directory, so a slow or failing destination never
delays the pipeline, and the journal is the durable queue:

```powershell
$env:CUSTODY_WATCH_WEBHOOK_SECRET = "<at least 32 bytes>"
.venv\Scripts\python.exe scripts/export_webhook.py outputs/journal/SESSION_ID --url https://vms.example.org/hook --cursor outputs/SESSION_ID.webhook-cursor.json
```

- The cursor file must live under the directory the exporter is started from;
  a path that resolves outside it is refused.
- Only complete lines (ending in LF) are sent; the final fragment never is.
  Lines above `--max-line-bytes`, malformed JSON, invalid envelopes and
  replayed sequences are skipped and counted as `invalid_records`.
- Delivery is at least once. The cursor (atomic write) advances only after a
  2xx or a counted discard; a restart may resend the last event. Each body has a
  stable `id` (`<session_id>:<sequence>`, also in `X-Custody-Event-Id`) for
  deduplication.
- Timeouts, connection errors, 5xx, 408 and 429 are retried with capped
  exponential backoff and jitter. When attempts run out, the exporter stops
  without advancing and retries later: nothing is dropped because the
  destination is down. Redirects are not followed and, like 401/403/404/405/
  407/410/421, stop without advancing, because they mean misconfiguration.
  Other 4xx responses discard the event and count it in `discarded`.
- The URL must be http(s), https outside loopback, with no credentials, query
  string or fragment. The secret comes only from `CUSTODY_WATCH_WEBHOOK_SECRET`.
- `X-Custody-Signature: sha256=<hex>` is HMAC-SHA256 over
  `<X-Custody-Timestamp>.<body>`. Receivers should check it in constant time and
  reject stale timestamps (`custody_watch.webhook_export.verify_signature`).
- The body carries only relational fields: event kind, times, track, bag and
  group IDs, and allow-listed evidence numbers. Unknown fields, images and paths
  are never sent. Lifecycle records are not forwarded.
- Counters (`sent`, `discarded`, `attempts`, `retries`, `invalid_records`,
  `stalled`, delivery latency, `backlog_bytes`) are printed as one JSON line on
  exit or every `--stats-every` seconds; they hold no event content.

One exporter follows one session; start another for the next run.

## Results so far

The full record of perception measurements, the generated-footage run, the real-footage runs and the evaluation harness is in [EVALUATION_HISTORY.md](EVALUATION_HISTORY.md), moved there unchanged. In short, with every number cited to its source in that file:

- **Perception limits the system.** On CAVIAR at 384×288 the detector found 1 of 1,686 annotated suitcases; at 720p on the generated gate clip the same weights track the suitcase.
- **Generated gate theft:** the thief is the only top-severity item in the queue. One generated clip is one sample, not evidence about real thefts.
- **Real staged thefts (MEVA):** 4 of 4 missed, because the detector never saw the bag at rest. False top-severity accusations on the same footage went from five to zero.
- **P_miss for theft has no measured value**, because no dataset available to the project contains a measurable positive of that class.

Oracle replays (boxes and IDs from annotation) do not validate a detector, computed association or custody; see [PUBLIC_DATASET_VALIDATION.md](PUBLIC_DATASET_VALIDATION.md) for the multi-camera public-dataset limits.

### Synthetic scenario benchmark

`scripts/benchmark_scenarios.py` runs the custody logic on generated scenarios whose correct answer is known by construction (modules `src/custody_watch/scenario*.py`). It has 11 scenarios, each run at 3, 10 and 25 fps: 33 runs per system, without noise and without a seed. As of 2026-11-25 the system is right in 33 of 33 runs; a naive distance baseline is right in 24 of 33.

This measures the logic on positions the generator produces. It does not demonstrate a detector, computed association or real video, and these counts are not a performance claim for real footage.

In `sit_beside_then_take` the owner sits within reach of the bag while it is taken. The removal rule keeps custody while a strong member of the owning party is within `owner_search_radius_m` of the bag's current position, so the accusation deadline for this scenario follows from that rule and the scene geometry: the time for the bag to leave the owner's reach and move one `moved_threshold_m` step, plus `carry_confirm_s`, plus two frames at the minimum frame rate (4.08 s with the default config). The other accusation deadlines are unchanged.

The output JSON goes to `outputs/`, which is gitignored, so no result file is linked. To reproduce, from a checkout (the output path must be new; the script never overwrites):

```powershell
.venv\Scripts\python.exe scripts/benchmark_scenarios.py --output outputs/scenario-benchmark.json
```

`--fps` (default 3, 10 and 25), `--scenarios` (a subset by name), `--noise` and `--seeds` are optional; the counts above come from the default invocation without `--noise`.

## Stack

Python ≥3.11 · [uv](https://docs.astral.sh/uv/) · YOLO26 behind a swappable `Detector` interface · OpenCV · Pillow

## Licence

[AGPL-3.0](LICENSE), inherited from Ultralytics. The `Detector` interface exists partly so that swapping to an Apache-2.0 model (RF-DETR, D-FINE) stays a contained change.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the HTTP servers' threat model,
[PRIVACY.md](PRIVACY.md) for what the software processes and stores, and
[CITATION.cff](CITATION.cff) for how to cite it.
