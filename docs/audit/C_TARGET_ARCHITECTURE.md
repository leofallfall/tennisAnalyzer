# C — Recommended Target Architecture (option D, hybrid)

## Diagram

```
                ┌──────────────────────── Phone / browser (React PWA) ────────────────────────┐
                │ 1 pick video → read metadata (duration, codec, size) → optional trim in/out │
                │ 2 tap the player on a thumbnail (optional)                                  │
                │ 3 resumable multipart upload, 8–16 MB parts, retry per part,                │
                │   uploadId kept in IndexedDB                                                │
                │ 7 poll / SSE job status → session issues → pick one → deep analysis         │
                │ 8 play stroke clips; draw the skeleton on <canvas> from per-window pose     │
                └───────▲─────────────┬──────────────────────────────▲───────────────────────┘
       presigned GET    │             │ presigned PUT (parts)        │ JSON (HTTPS)
       (clips/frames)   │             ▼                              │
                ┌───────┴──────────────────────┐        ┌────────────┴────────────────────────┐
                │  Object storage               │        │  API service (FastAPI, always on,   │
                │  local: ./storage (FS)        │        │  ~150 MB)                           │
                │  cloud: Railway Bucket (S3)   │        │  • auth, sessions, presign URLs     │
                │  originals/  (expire 7–30 d)  │        │  • DB: SQLite on a volume (or PG)   │
                │  derived/    (kept)           │        │  • job queue table (as today)       │
                └───────▲──────────────────────┘        │  • internal /jobs/claim, /heartbeat,│
                        │ GET original (free egress)     │    /complete (shared token)         │
                        │ PUT artifacts (svc egress)     │  • wakes the worker over private net│
                ┌───────┴──────────────────────────────┐ └────────────▲────────────────────────┘
                │  Worker service (serverless, sleeps)  │◄────────────┘ HTTP (private network)
                │  tennis_cv staged pipeline (E):       │
                │  probe → CFR timeline → 10 fps scan → │──────► Claude API
                │  candidates → dense windows (N procs) │        Sonnet 5.5 (session, batch)
                │  → classify → measure → evidence      │        Sonnet/Opus 5.5 (deep, on demand)
                │  drains the queue, then goes idle     │        Haiku 5.5 (titles, summaries)
                └───────────────────────────────────────┘
```

## Where each operation runs

| Operation | Runs on | Reason |
|---|---|---|
| metadata, trim, player tap, upload, playback, skeleton drawing | phone/browser | cheap; improves UX; no CV on the device |
| presigning, auth, job records, results API | API (always on) | light; must answer fast |
| probe, transcode, scan, pose, classification, measurement, evidence | worker (on demand) | heavy, long, restartable |
| interpretation, ranking issues, coaching language | Claude API | the only place an LLM runs |
| original and derived files | bucket (cloud) / filesystem (local) | cheap ($0.015/GB-month), free egress |

## Adapters (all exist locally today, or are thin interfaces to add)

| Port | Local adapter (default) | Cloud adapter |
|---|---|---|
| `ObjectStore` | filesystem under `storage/` (current behaviour) | S3-compatible client for the Railway bucket, presigned URLs |
| `JobSource` | SQLite claim (current `processing_jobs` code) | HTTP client to the API's internal claim, heartbeat and complete endpoints |
| `AiClient` | disabled, or a deterministic stub for tests; real only when `ANTHROPIC_API_KEY` is set **and** the user requests AI | Anthropic SDK |
| `UploadFlow` | the current multipart POST to the API stays for local and small files | presigned multipart to the bucket |

No Kubernetes, Redis or Celery. The DB-backed queue is enough for this volume: one job at a time per worker, tens of jobs a day. A second worker replica can claim jobs atomically through the same endpoints if needed.

## Reliability rules

- **Jobs:**
  - Every job is idempotent: its outputs go to `derived/<video_id>/<pipeline_version>/`.
  - The worker heartbeats every 30 s. A job with a stale heartbeat (> 5 min) is re-queued, up to 3 attempts, then marked failed with an error code.
  - Stages checkpoint: chunks and windows already done are skipped on retry.
- **Uploads:**
  - Each upload expires: incomplete multipart uploads are aborted after 24 h, and presigned URLs last 15–60 min.
  - The completed upload is verified by size and checksum before a job is created.
- **Storage access:**
  - The bucket is private.
  - Clients get only short-lived presigned GET URLs for their own objects.
  - The API never streams video bytes, so it uses no service egress and isn't hit by the 15-min HTTP cap.
- **HEVC/iPhone:** decoded server-side by ffmpeg, measured at 25 cpu-s/min **[M]**. Playback uses the CFR H.264 proxy or stroke clips, never the raw HEVC, for browsers that lack it.
- **Waking the worker:**
  - The API sends a wake-up request on job creation; the first request after sleep may fail, so it is retried with backoff.
  - A 5-min cron that pings the worker only when queued jobs exist is the fallback.
