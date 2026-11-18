# Tennis Performance Analyzer

A tennis biomechanics and technical stroke-analysis platform for intermediate,
advanced and competitive players. It analyses smartphone videos to produce
**structured, measurable and explainable** technical information, and is explicit about
what cannot be measured reliably.

> **Status: Milestone 1 (video-processing prototype).** Upload, metadata, validation,
> normalisation, frame extraction, quality assessment, MediaPipe pose estimation,
> skeleton overlay and the web app work. Stroke detection, biomechanical metrics, AI
> coaching, comparison and progress are **not implemented**; the UI shows clearly
> labelled example layouts for them. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Repository layout

```
apps/web/            React 19 + Vite + TypeScript + Tailwind 4 (shadcn/ui conventions)
apps/api/            FastAPI backend (package tennis_api): upload, analyses, job lifecycle
worker/              Background worker process (package tennis_worker, jobs/)
engine/tennis_cv/    Computer-vision engine: video, quality, pose, detection, racket,
                     ball, strokes, biomechanics, comparison, pipeline
packages/contracts/  JSON Schemas generated from Pydantic + TypeScript types
tests/               Python unit + integration tests, fixtures documentation
scripts/             dev, checks, model download, pose experiment, contract export
docs/                Product, architecture, pipeline, biomechanics, data model, AI, ADRs
storage/             Local uploads, artifacts and SQLite DB (git-ignored)
models/              Downloaded model files (git-ignored)
```

## Prerequisites

| Tool | Version used | Notes |
| --- | --- | --- |
| Python | 3.12 | `uv` installs it automatically from `.python-version` |
| uv | ≥ 0.4 | https://docs.astral.sh/uv/ |
| Node.js | ≥ 20 (22 tested) | |
| pnpm | 10 | `corepack enable` or `npm i -g pnpm` |
| FFmpeg + ffprobe | ≥ 6 with libx264 | `brew install ffmpeg` / `apt install ffmpeg` / `winget install ffmpeg` |
| Linux only | `libegl1 libgles2` | Required by MediaPipe: `sudo apt install libegl1 libgles2` |

## Install

```bash
uv sync                                     # Python workspace (+ dev tools) into .venv
pnpm install                                # web + contracts
uv run python scripts/download_models.py    # MediaPipe pose model → models/ (~9 MB)
```

## Run

```bash
./scripts/dev.sh
```

starts the API on http://127.0.0.1:8000 (OpenAPI docs at `/docs`), the worker, and the
web app on http://localhost:5173. Or in three terminals:

```bash
uv run tpa-api          # API
uv run tpa-worker       # worker (polls for queued analyses)
pnpm dev:web            # web app (proxies /api to :8000)
```

Then open http://localhost:5173 → **New analysis** → upload a video. The **System status**
link in the sidebar shows which tools/models are available and which modules are
implemented.

### Pose experiment from the command line

```bash
uv run python scripts/pose_experiment.py path/to/video.mp4 [--stride 2] [--max-frames 300]
```

Writes `proxy.mp4`, `pose.json` (structured joint coordinates), `overlay.mp4` (skeleton)
and `report.json` to `storage/experiments/<name>/`, and prints metadata, quality ratings,
detection rate, ms/frame and step timings.

## Test and quality checks

```bash
./scripts/check.sh               # everything below
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict
uv run pytest                    # -m "not slow" skips tests that run the real model
pnpm -r lint && pnpm format:check && pnpm -r typecheck && pnpm -r test
pnpm build                       # production web build
```

Tests that need the MediaPipe model are skipped (with a message) if it is not downloaded.

## API (v0.1)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness |
| GET | `/api/system/info` | Versions, tools, model presence, module capabilities |
| POST | `/api/videos/upload` | Multipart `file`; returns metadata + validation; 415/413/422 on bad input |
| GET | `/api/videos/{id}` | Video record |
| GET | `/api/videos/{id}/file` | Original file (range requests) |
| GET | `/api/analyses` | List |
| POST | `/api/analyses` | Create (`mode`, `videos[{video_id, camera_view}]`, `video_relation`, …) |
| GET | `/api/analyses/{id}` | Analysis with per-video reports and artifact URLs |
| GET | `/api/analyses/{id}/status` | Status, stage, progress, structured error |
| POST | `/api/analyses/{id}/start` | Queue for the worker (202); 409 if already queued/running/completed |
| GET | `/api/analyses/{id}/videos/{vid}/artifacts/{proxy\|pose\|overlay\|report}` | Results |

Errors always have the shape `{"error": {"code", "message", "details"}}`.

## Configuration

See [.env.example](.env.example). Defaults work out of the box.

## Documentation

Start with [CLAUDE.md](CLAUDE.md) (working rules) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
