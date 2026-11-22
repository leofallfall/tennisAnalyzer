# Architecture

## System overview

```
┌────────────────────┐   HTTP/JSON    ┌──────────────────────┐
│  apps/web          │ ─────────────▶ │  apps/api (FastAPI)  │
│  React + Vite + TS │ ◀───────────── │  tennis_api          │
└────────────────────┘  video files   └──────────┬───────────┘
                                                  │ SQLAlchemy
                                       ┌──────────▼───────────┐
                                       │ SQLite (WAL)         │  videos, analyses,
                                       │ storage/tpa.sqlite3  │  analysis_videos,
                                       └──────────▲───────────┘  processing_jobs
                                                  │ poll + atomic claim
                                       ┌──────────┴───────────┐
                                       │ worker (tennis_worker)│
                                       │ separate OS process  │
                                       └──────────┬───────────┘
                                                  │ Python calls
                                       ┌──────────▼───────────┐
                                       │ engine (tennis_cv)   │  FFmpeg, OpenCV,
                                       │ pure library         │  MediaPipe
                                       └──────────────────────┘
        storage/  videos/<id>/original.*     analyses/<id>/videos/<vid>/{proxy.mp4,
                                                pose.json, overlay.mp4, report.json}
```

## Modules and responsibilities

| Module | Path | Responsibility | Must not |
| --- | --- | --- | --- |
| Web | `apps/web` | UI, playback, frame stepping, client-side skeleton rendering | Compute measurements |
| API | `apps/api/tennis_api` | Upload, validation, persistence, job lifecycle, serving artifacts | Run long CV work in a request |
| Worker | `worker/tennis_worker` | Claim queued jobs, run engine pipelines, persist results/status | Expose HTTP |
| Engine | `engine/tennis_cv` | Video, quality, pose, (future) strokes, racket, ball, biomechanics, comparison | Know about HTTP, DB or LLMs |
| Contracts | `packages/contracts` | JSON Schemas (generated) + TS types mirroring Pydantic | Contain logic |
| AI layer | `apps/api/tennis_api/ai` (experimental, see `AI_ANALYSIS.md`) | Build Evidence Packs, validate and import AI interpretations (`AIClient`: Claude Code now, Anthropic API later) | Call an LLM from the app in personal mode, produce or alter measurements |

### Engine sub-modules

| Package | Status | Notes |
| --- | --- | --- |
| `video` | implemented | ffprobe metadata (rotation, VFR), validation, CFR proxy, frame iterator |
| `quality` | experimental | Laplacian sharpness, luma stats, pose detection rate → observability |
| `pose` | experimental | `PoseEstimator` ABC, MediaPipe Pose Landmarker (VIDEO mode), overlay |
| `biomechanics` | experimental | Metric catalogue, `Measurement`, evidence layers, 2D projected angles |
| `detection` | interface | Scene/camera change, player detection |
| `strokes` | interface + format | Detection, classification, phase segmentation (interfaces only); `artifact.py`: `strokes.json` 1.0 models, versioned reader, invariants, canonical writer (docs/M2_3_EVENTS.md). No detector |
| `racket`, `ball` | interface | Detection/tracking |
| `comparison` | interface | `CameraView`, compatibility, normalisation |
| `evaluation` | implemented (offline) | M2.1 dataset formats, annotated intervals and their coverage (`annotated.csv`), label/event matching (M2.1 `compare`), M2.3 association by connected components with miss causes and `strokes_eval.json` 1.0, reports and false-candidate review sheet (docs/M2_3_EVALUATION.md), input visibility report; used by `scripts/eval_strokes.py` only |
| `signals` | partial (offline, M2.2) | Time base of pose samples (`timebase.py`: source/proxy times, repeated frames, irregular intervals, map checks); keypoint quality levels and local polynomial smoothing on real times within runs (`smoothing.py`); robust torso scale (`scale.py`); apparent 2D speed from the derivative of the same fit (`velocity.py`); shoulder/hip lines, widths, hands distance, wrist vs hips and `PlayerConfig` (`body.py`). No accelerations. Not used by the pipeline |
| `pipeline.py` | implemented | Milestone 1 pipeline orchestrating the above |
| `capabilities.py` | implemented | Honest module status, exposed at `/api/system/info` |

## Data flow (Milestone 1)

1. `POST /api/videos/upload` streams the file to `storage/videos/<id>/original.<ext>`,
   hashes it, runs `probe_video` + `validate_metadata`. Invalid → deleted + 422.
2. `POST /api/analyses` links one or more videos (with camera view and explicit
   `video_relation`).
3. `POST /api/analyses/{id}/start` sets status `queued` and inserts a `processing_jobs`
   row; returns 202 immediately.
4. Worker claims the job (`UPDATE ... WHERE status='queued'`, rowcount check), runs
   `run_video_processing` per video, updating `stage`/`progress` (throttled to 2 Hz).
5. Results JSON (one `ProcessingReport` per video) is stored on the analysis; artifacts
   on disk (`proxy.mp4`, `pose.json`, `overlay.mp4`, `report.json`, `timemap.json`).
   Failures store `error_code`, `error_message`, `error_details`; the API redacts server
   paths from all of them (`tennis_api/redaction.py`, M1.10) and System Status lists the
   latest failures.
6. Web polls `/status` every 1.5 s while busy, then loads the analysis, pose JSON and
   proxy, and renders the skeleton client-side synced with `requestVideoFrameCallback`.

## Time base

The proxy is constant frame rate, so frame *i* has timestamp `i / fps`. All engine
timestamps refer to the proxy timeline (starting at the first decoded video frame). VFR
sources are resampled to their average rate; `timemap.json` maps every proxy frame back to
the source frame it shows (frame-exact, time-approximate). Details and limits in
[VIDEO_PIPELINE.md](VIDEO_PIPELINE.md) §4.1.

Wall-clock timestamps (created/started/finished) are UTC in the database and in the API;
the browser converts them to local time.

## Local-first design

- Single machine, no external services, no accounts. SQLite + filesystem.
- Background work in a separate process with a DB-backed queue (no Redis/Celery).
- All paths stored relative to `TPA_STORAGE_DIR`.
- Model files downloaded once into `models/`.

## Cloud migration strategy

| Concern | Local now | Cloud later | Change required |
| --- | --- | --- | --- |
| Database | SQLite | PostgreSQL | `TPA_DATABASE_URL` + driver; add Alembic migrations |
| Files | local `storage/` | S3-compatible object store | Storage interface behind `storage.py` (relative keys already) |
| Queue | `processing_jobs` table | Same table with `SELECT ... FOR UPDATE SKIP LOCKED`, or a managed queue | Claim query only |
| Workers | 1 local process | N containers, GPU optional | Heartbeats + stale-job recovery by timeout |
| Auth | none (single user) | OIDC | `User` entity already in the data model |
| Uploads | through API | pre-signed direct upload | New endpoint, same validation step in worker |

## Non-goals now

Microservices, message brokers, Kubernetes, GPU scheduling, real-time streaming.
