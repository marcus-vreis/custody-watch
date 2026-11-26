# Privacy

This document describes what the software does with data. It is not legal
advice and does not establish or guarantee compliance with LGPD, GDPR or any
other law for a given deployment. Lawfulness, notices, retention periods and
access control are decisions for whoever deploys it.

## Purpose

custody-watch ranks luggage custody changes in surveillance video for a human
operator. The question it asks is whether the person leaving with a bag is
from the group that set it down, not who a person is.

## What it does not do

- No face recognition, no biometrics, no identity database. A pre-commit hook
  (`sem-reconhecimento-facial` in `.pre-commit-config.yaml`) rejects imports of
  `insightface`, `face_recognition`, `deepface`, `facenet`, `dlib` and
  `mediapipe` under `src/`. This is a static import check, not a runtime
  guarantee about what a deployer adds.
- No automated accusation. The output is a ranked queue for a human; the
  project's stated scope excludes automated decisions without human review
  (`CONTRIBUTING.md`).
- Flags describe relations between people and bags, not personal attributes.

## Appearance matching (re-ID)

To reconnect a track that fragments during one run, `src/custody_watch/reid.py`
computes a 48-dimension colour histogram (HSV, upper and lower body halves) per
person crop. It is not a face or body-shape embedding. Its docstrings state
that it is scoped to a session and discarded when processing ends. The
signatures are held in process memory by `TrackLinker`, and the event journal
does not write them (README, "Incremental journal and retention").

## What goes to disk

| Data | When | Notes |
|---|---|---|
| Event journal (`.jsonl` parts) | Only with `--journal-dir` | Event records: numeric track and bag IDs, timestamps, evidence numbers, operator-pointing marks. No frames, appearance descriptors, camera URLs or personal paths. Clocks are local, not UTC. |
| Terminal bag archive (SQLite) | Only with `--archive-terminal-bags`, which requires `--journal-dir` | Snapshots of bags in a terminal state, plus track aliases; one new file per segment. |
| Annotated GIF clips | When `review.py` / `build_report.py` run | Written under a `clipes/` folder. They are cut from the video, so they contain whatever the footage shows. |
| Review page (HTML) | When the report scripts run | Embeds the selected clips as base64. Size budget defaults to 15,000,000 bytes. |

Without `--journal-dir`, live events stay in RAM, limited to the most recent
`--event-tail` events (default 1000); the live command says that disk
recording is disabled.

## Retention

The software deletes nothing. Journal sessions are never overwritten or
pruned, the terminal archive only grows, and no disk purge policy exists:
retention is unlimited until the operator applies one. Rotation only splits the
journal into parts (`--journal-part-records`, default 10000 records); it is not
a retention limit. Source footage, clips and reports are yours to protect and
delete.

## Network exposure

The live screen streams annotated video. See `SECURITY.md`: without the
`CUSTODY_WATCH_TOKEN` access token it has no authentication, and it refuses to
listen off loopback without that token and TLS.
