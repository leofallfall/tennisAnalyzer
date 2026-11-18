# A — Current Architecture Audit

Commit audited: `2195fc4`. Benchmarks: [`benchmarks/2026-11-18-baseline.md`](benchmarks/2026-11-18-baseline.md).

How each figure is labelled:

- **[M]** measured
- **[D]** derived arithmetically from measurements
- **[E]** estimate, with its assumption stated
- **[U]** unmeasured

## 1. What exists today

```
Browser (React/Vite)
   │  multipart POST /videos (whole file through the API)
   ▼
FastAPI (apps/api) ── SQLite (WAL) ── local filesystem storage/
   │  inserts processing_jobs row
   ▼
Worker (worker/) polls SQLite every 1 s, atomically claims a job
   │
   ▼
tennis_cv.pipeline.run_video_processing  (one process, one job at a time)
   1. ffprobe → metadata → validation
   2. full-resolution CFR proxy  (x264 veryfast CRF18, long side ≤1920)
   3. sequential decode of the proxy (cv2)
        ├─ quality sample every 0.5 s (blur/brightness/contrast)
        ├─ MediaPipe PoseLandmarker (full, VIDEO mode) on EVERY frame
        └─ overlay frame drawn → MJPEG temp file
   4. MJPEG → H.264 overlay transcode
   5. pose.json (all frames, all 33 landmarks), report.json
   ▼
Browser plays proxy.mp4 or overlay.mp4; VideoPlayer can also draw
the skeleton client-side from pose.json on a <canvas>.
```

What is built and what is not:

- **Not implemented yet** (interfaces only, raise `NotYetImplementedError`): stroke detection, FH/BH classification, racket, ball, biomechanical metrics per stroke, comparison, and any Claude call.
- **The architecture is sound for local development.** The layers are separated correctly: the engine is a pure library, the API owns persistence, the worker owns long jobs. The job queue is durable and claimed atomically, and errors are typed.
- **The problems below are cost and robustness problems,** not structural ones.

## 2. Expensive operations

| # | Operation | Where it runs now | CPU per footage-minute | Peak RAM | Wall time (4 vCPU) | Needed on every frame? | Optimise / sample / skip | Where it should run |
|---|---|---|---|---|---|---|---|---|
| 1 | Upload of the original through the API (`save_upload`, SHA-256 while streaming) | API process | small; SHA-256 ≈ 0.3 cpu-s per 100 MB **[E]** | streaming, low | bound by the network: 100 MB at 20 Mbit/s ≈ 40 s; a 60-min file (~6 GB) ≈ 40 min **[D]** | n/a | Move off the API: presigned multipart upload straight to object storage. On Railway an HTTP request is capped at 15 min, so a 60-min upload through the API **cannot succeed** on mobile bandwidth. | Phone → bucket directly |
| 2 | ffprobe metadata | worker | 0.08 s per file **[M]** | trivial | 0.08 s | no | keep | worker (also cheap enough on the API) |
| 3 | Full-resolution CFR proxy, CRF 18 | worker (ffmpeg) | **91 cpu-s [M]** | 402 MB **[M]** | 27.7 s **[M]** | it re-encodes every frame | 720p CRF 23 costs 51 cpu-s and is 4.6× smaller **[M]**. Keep *one* CFR timeline (project rule) but choose the cheaper encode after the accuracy check in G. Use a 1 s GOP so analysis windows can seek cheaply. | worker |
| 4 | Sequential decode of every proxy frame | worker | ≈ 24 cpu-s **[D]**; 19–24 cpu-s **[M]** for raw decode | ~80–120 MB | ≈ 6.7 ms/frame **[M]** | only inside stroke windows | A 10 fps scan still costs a full decode for H.264 (20 cpu-s **[M]**), so the saving must come from what runs *after* decode | worker |
| 5 | MediaPipe pose, full model, every frame | worker (Python, effectively one core) | **≈ 113 cpu-s [D]** (31.4 ms × 3600) | ~350–430 MB process **[M]** | ≈ 113 s | **no.** Rallies are a fraction of a session; FH/BH technique needs full temporal resolution only around contact | Scan at 10 fps to find the player and candidate strokes; dense full-fps pose only in ~1.5 s windows | worker, in N parallel processes |
| 6 | Overlay rendering (draw + MJPEG + H.264 transcode) | worker | **≈ 98 cpu-s [D]** plus part of the ffmpeg children | — | ≈ 98 s | **no.** It duplicates what `VideoPlayer` can already draw from `pose.json` | **Remove from the default pipeline.** Render the skeleton in the browser; keep server overlay only as an opt-in for export | browser (canvas) |
| 7 | Frame quality sampling (blur/brightness/contrast) | worker | negligible | — | < 1 s | no; 2 per second is fine | keep; also compute on the scan frames | worker |
| 8 | `pose.json`: every frame, 33 landmarks, as JSON | worker → storage → browser | serialisation small | whole session held in memory | write 0.1 s **[M]** | no | 10.5 MB per footage-minute **[M]**, so a 60-min session ≈ 630 MB, held in memory and sent to the browser. Store per-window, compact binary or quantised, and send the browser only the window it shows. | worker writes, API serves windows |
| 9 | Waiting worker polling SQLite every 1 s | worker | ~0.1 % of one core **[M]** | 97 MB **[M]** | — | — | Fine locally. On Railway it keeps an always-on service and can't share the API's SQLite volume (see §4). | — |
| 10 | Claude interpretation | not implemented | — | — | — | — | Design in `C_` and `E_` | Claude API, called by the worker |

How the 264 s per footage-minute breaks down **[M/D]**:

| Stage | Seconds | Share |
|---|---|---|
| Proxy | 27 | 10 % |
| Decode | 24 | 9 % |
| Pose | 113 | 43 % |
| Overlay | 98 | 37 % |
| Other | 2 | 1 % |

**Two thirds of the work is either removable (overlay) or applied to frames that will never be analysed (pose outside strokes).**

## 3. Parallelism

- The 1/2/4-core runs (328 / 274 / 264 s) show the pipeline is **serial** (Python loop + MediaPipe). Only ffmpeg scales with cores.
- Billing caveat: Railway bills *used* vCPU-seconds, not allocated ones. A wider allocation costs nothing extra, but it also buys no speed unless the work is split.
- Remedy: split the session into time chunks (scan) and stroke windows (dense pose), and process them in a `ProcessPoolExecutor` with one MediaPipe instance per process (~350–430 MB each **[M]**).
- At 4 processes, expect a near-linear speedup on the pose stages **[E]**: the work is CPU-bound, with no shared state between windows.

## 4. Deployment blockers found in the code (for a future Railway deployment)

1. **Uploads through the API.** The 15-minute HTTP cap and mobile networks make long uploads fail, and the original is written to the API container's disk.
2. **Shared SQLite and local filesystem.**
   - The worker and API open the same SQLite file and `storage/` directory.
   - On Railway a volume attaches to **one** service, so two services cannot share them. Hobby volumes are also capped at **5 GB**, smaller than one 60-min original (~3.5–6.8 GB **[D]**).
   - Fix:
     - Objects go to an S3-compatible bucket.
     - The worker reaches the job queue through the API over HTTP, or both use Railway Postgres.
     - SQLite and the filesystem stay as the local adapters.
3. **Worker is always on.** Polling keeps a continuously billed service. Idle cost is small (≈ $1/month, see D), but a service that only wakes on demand is cleaner.
4. **No resumable or retriable upload.** A dropped connection on a phone restarts from zero.
5. **No retention policy.** Originals at ~60–113 MB per footage-minute **[M]** are kept forever.
6. **Format handling is already correct.** The iPhone HEVC/`.mov` and VFR → CFR path (ffprobe + proxy) works; HEVC decode costs ~25 cpu-s per minute **[M]**.

## 5. What to keep (working code that should not be replaced)

These are measured working or tested:

- engine/API/worker separation
- `processing_jobs` table and atomic claim
- typed errors
- `ffprobe` metadata and validation
- CFR timeline convention
- MediaPipe estimator behind `PoseEstimator`
- `Measurement` model
- client-side skeleton canvas
- the test suite (66 tests)

The roadmap in F changes these by *adding* adapters and stages, not by rewriting them.

## 6. Phase-2 findings still open

From the 2026-11-18 verification:

- Naive UTC datetimes are shown 1 h early in Italy.
- `probe_failed` details leak an absolute server path.
- MediaPipe follows only the most prominent person, a risk with the opponent on court.
- Accuracy on real tennis footage is unvalidated (M1 item 11).

These are prerequisites in the roadmap.
