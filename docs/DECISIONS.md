# Architecture Decision Record

Format: decision · context · rationale · alternatives considered. Newest last.

### ADR-001 Monorepo with independent apps
pnpm workspace (`apps/web`, `packages/contracts`) + uv workspace (`engine`, `apps/api`,
`worker`) in one repository. *Rationale*: one place for contracts and docs, atomic
changes across layers, still independently testable packages. *Alternatives*: polyrepo
(overhead for one developer), single Python package (couples CV to HTTP).

### ADR-002 Engine as a pure library
`tennis_cv` has no HTTP, DB or LLM dependencies and returns Pydantic models.
*Rationale*: testable in isolation, reusable from scripts/notebooks/worker, keeps CV
independent of AI interpretation. *Alternative*: CV inside the API — rejected.

### ADR-003 Worker as a separate process with a DB-backed queue
`processing_jobs` table; worker polls and claims atomically (`UPDATE … WHERE status =
'queued'` + rowcount). *Rationale*: simplest reliable local solution; durable across
restarts; API never blocks. *Alternatives*: FastAPI BackgroundTasks (runs in API process,
lost on restart), Celery/RQ + Redis (extra infrastructure), Dramatiq. Revisit when
multiple workers or cloud scaling are needed.

### ADR-004 SQLite (WAL) with SQLAlchemy 2.0, PostgreSQL-ready
WAL + busy timeout allow API and worker processes to share the DB. No SQLite-specific
SQL. Tables are created with `create_all` in M0/M1. *Follow-up*: introduce Alembic before
the first schema change that must preserve user data.

### ADR-005 MediaPipe Pose Landmarker as baseline
Tasks API, VIDEO mode, `full` model, CPU. *Rationale*: pip-installable, CPU real-time,
33 keypoints including heel/foot index, permissive licence. *Alternatives*: RTMPose/
MMPose (likely more accurate on fast motion; heavier install, mmcv build friction) —
to be benchmarked in M6 behind the `PoseEstimator` interface; OpenPose (licence, speed);
YOLO-pose (AGPL licence considerations). Linux requires system `libegl1`/`libgles2`.

### ADR-006 Constant-frame-rate proxy as the analysis time base
All analysis runs on an FFmpeg-normalised proxy (rotation applied, CFR, H.264, ≤1920 px).
*Rationale*: exact `index/fps` timestamps, browser playback, bounded cost. *Trade-off*:
VFR resampling error ≤ ½ frame. *Alternative*: PTS-based timestamps throughout — more
exact but complicates every consumer; reconsider if sub-frame timing is required.

### ADR-007 Client-side skeleton rendering + server overlay video
The web app draws the skeleton from `pose.json` on a canvas synchronised with
`requestVideoFrameCallback`; the worker also renders `overlay.mp4` as a portable
artefact. *Rationale*: interactive toggling/inspection without re-encoding; overlay for
sharing/debugging.

### ADR-008 Contracts: Python is the source of truth
Pydantic models → JSON Schemas (`scripts/export_contracts.py`, checked in CI/tests) →
hand-maintained TS types in `packages/contracts`. *Alternative*: generate TS from schemas
(`json-schema-to-typescript`) — deferred to avoid another dependency while contracts are
small; adopt when they grow.

### ADR-009 Honest capability reporting
Unimplemented modules expose interfaces whose default implementations raise
`NotYetImplementedError`; `capabilities.py` reports module status to the UI; reports list
`not_performed` analyses. Placeholder UI is always labelled "Example layout — not a real
analysis".

### ADR-010 Structure deviations from the initial brief
- Python packages are importable directories inside their project folders:
  `apps/api/tennis_api`, `worker/tennis_worker/jobs` (brief: `worker/jobs`) — needed for
  installable uv workspace members with unambiguous import names.
- Added `engine/tennis_cv/pipeline.py`, `capabilities.py`, `common.py`, `errors.py`.
- `tests/` is shared at the root for all Python packages; web tests sit next to the code
  (Vitest convention).
- `models/` (git-ignored) holds downloaded model files.
- `scripts/` contains dev/check/download/experiment/contract scripts.

### ADR-011 Frontend stack details
React 19, Vite 8, Tailwind CSS 4 (CSS-first `@theme` tokens), shadcn/ui conventions
(`components.json`, CVA + tailwind-merge; components written in-repo so the CLI can add
more later), React Router 7, no data-fetching library yet (small `useAsync` hook).
TypeScript pinned to 6.0.x because `typescript-eslint` 8 does not yet support TS 7.
SciPy and PyTorch are **not** installed yet: nothing uses them (add with the first
feature that does).

### ADR-012 Python 3.12
`.python-version` pins 3.12 (uv installs it if missing) for the widest compatibility with
MediaPipe and future PyTorch wheels; packages declare `>=3.11,<3.14`.
