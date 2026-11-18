# F — Implementation Roadmap

Rules for every step:

- Each step is small, has a test or benchmark as its acceptance check, and leaves `./scripts/check.sh` green.
- Each step keeps the local workflow free of cloud dependencies.
- Nothing here replaces working code without the reason given in A.
- These steps slot into the existing `docs/ROADMAP.md` milestones. They change *how* M1–M4 are built, not *what* the product does.
- **Nothing in this roadmap has been implemented.** This document is a proposal for review.

## Step 0 — Fixes and validation already due (M1)

| # | Task | Acceptance check |
|---|---|---|
| 0.1 | Store UTC-aware datetimes, or serialise with `Z`, so Italy doesn't show times 1 h early | API test on a timezone-aware value; UI shows local time |
| 0.2 | Remove absolute paths from `probe_failed` details | error test |
| 0.3 | **M1.11:** collect ≥ 20 real amateur clips (Leonardo), run G's tennis benchmark on them, and label strokes by hand on ≥ 5 clips | report with detection rate, stroke count per minute, and landmark sanity checks |

*0.3 blocks the decisions in steps 2 and 4.* All the estimates in D and E depend on its numbers.

## Step 1 — Cheap wins in the current pipeline (no new architecture)

| # | Task | Acceptance check |
|---|---|---|
| 1.1 | `render_overlay` default → `False`. The web player draws the skeleton from pose data (already implemented); server overlay becomes an opt-in export. | benchmark: wall per footage-minute drops by ~35 % **[E from M]**; UI test shows the skeleton |
| 1.2 | Proxy encode: GOP = 1 s (`-g fps`) for cheap seeking. Make resolution and CRF options, *defaults unchanged* until 0.3 compares accuracy at 720p vs 1080p. | proxy test; seek benchmark |
| 1.3 | Compact pose storage: per-window or chunked files, float16 or rounded values, only the 33 landmarks plus visibility. The API serves a time range. | size per footage-minute ≪ 10.5 MB; the player loads windows on demand |
| 1.4 | Retention setting for originals (default 30 days) plus a cleanup command | unit test on expiry |

## Step 2 — Staged pipeline (E)

Each sub-step adds a stage behind the existing `run_video_processing` entry point, with a `pipeline_version` flag, so the old path stays available for comparison.

| # | Task | Acceptance check |
|---|---|---|
| 2.1 | Scan stage: 10 fps, lite model, chunked; outputs a player track, rally segments and a wrist-speed signal | unit tests on synthetic signals; benchmark scan cpu-s per minute |
| 2.2 | Candidate stroke detector (M2 heuristic): peaks plus NMS | precision and recall vs the hand labels from 0.3 (target set with Leonardo) |
| 2.3 | Dense window stage: full model, fresh tracker per window, ROI, `ProcessPoolExecutor` with N workers | per-window landmarks equal or better than the stride-1 baseline on the same frames; wall-time speedup on 4 vCPU |
| 2.4 | FH/BH classifier (heuristic first) with an `unknown` class | accuracy on the labelled clips; `unknown` never forced |
| 2.5 | Evidence builder: per-stroke metrics JSON plus 3–4 key frames; session aggregate | snapshot tests; token count via the count-tokens endpoint (free) once an API key is available |

## Step 3 — Claude interpretation (M3/M4)

| # | Task | Acceptance check |
|---|---|---|
| 3.1 | `AiClient` port, with a stub adapter for tests and an Anthropic adapter enabled only by env var plus an explicit user action | tests run without a key; no network in CI |
| 3.2 | Session call (Sonnet 5.5, structured JSON output, cached system prompt; Batch API when the user isn't waiting) | schema validation; observation, interpretation and recommendation kept separate; cost logged from `usage` |
| 3.3 | Deep single-stroke call on user selection (Sonnet 5.5; Opus 5.5 behind an option) | latency and cost logged; result cached by evidence hash |
| 3.4 | Small eval: 10 sessions, rubric graded by Leonardo, Sonnet vs Opus and medium vs high effort | decision recorded in `DECISIONS.md` |

## Step 4 — Cloud adapters (only when deployment is wanted)

| # | Task | Acceptance check |
|---|---|---|
| 4.1 | `ObjectStore` port: FS adapter (current) + S3 adapter (Railway bucket); presigned URLs | contract tests run against both adapters (S3 against a local MinIO only in an optional test job) |
| 4.2 | Presigned multipart upload in the web app with resume and per-part retry; the API's small-file POST stays for local use | e2e test: interrupted upload resumes |
| 4.3 | `JobSource` port: SQLite (current) + HTTP client; internal claim, heartbeat and complete endpoints in the API, with a shared token | worker tests run against both adapters |
| 4.4 | Railway deployment: API service with a volume (SQLite) or Postgres, a serverless worker, a bucket; wake-up on job creation | first real bill and runtime compared with D; numbers recorded in `benchmarks/` |

## Step 5 — Revisit decisions with real data

- Model choice: RTMPose vs MediaPipe, per G.
- 720p vs 1080p timeline.
- Retention window.
- Worker platform, per D §5 triggers.
- Each decision goes in `DECISIONS.md`.
