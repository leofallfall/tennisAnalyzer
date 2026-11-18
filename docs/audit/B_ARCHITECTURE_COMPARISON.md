# B — Architecture Comparison

Numbers come from [D](D_COST_MODEL.md) and the [baseline benchmarks](benchmarks/2026-11-18-baseline.md). Labels: [M] measured, [E] estimated.

## A — Everything on one app server (Railway Hobby)

The API process (or a thread or subprocess beside it) does the upload, the CV and the Claude calls, with one volume.

| | |
|---|---|
| Cost | Lowest on paper: one service, ≈ $1.6/month idle, the same compute per minute as B |
| Blockers | **Upload through the API** hits the 15-min HTTP cap. **A 5 GB volume** can't hold one 60-min original. **CV and web requests compete** for the same container: during a 4.4 h job **[M → D]**, API latency and memory spike (~0.85–2 GB). One crash takes both down. Redeploying the API kills a running job. |
| Reliability | Poor for long jobs |
| Verdict | **Rejected.** It breaks the constraint "don't make Railway's app server do continuous heavy CV", and its known failure modes have no workaround. |

## B — Light backend plus an on-demand worker

The API (light, always on) issues presigned upload URLs and job records. A separate worker service runs only while there are jobs.

Variants:

- **B1 — Railway worker, always on:** + ≈ $1/month idle **[D]**.
- **B2 — Railway worker, serverless (sleeps when idle):** woken by the API over the private network. ≈ $0 idle **[E]**.
- **B3 — Worker on Modal or Cloud Run Jobs:** essentially $0 at ≤ 2,000 footage-min/month thanks to free credits and tiers. It adds a second provider and egress plumbing.

| | |
|---|---|
| Cost | Target pipeline ≈ $0.0012 per footage-min; 100 analyses/month → Railway bill ≈ $7.7 **[E]** |
| Reliability | Good. A job survives API deploys. Retries are per job. The worker can be sized independently (Hobby allows up to 48 vCPU / 48 GB per service, billed on use). |
| Complexity | Low. This is today's code with a storage adapter and an HTTP job source added. |
| Verdict | **B2 chosen** as the deployment shape. B3 is kept as a documented escape hatch (D §5 has the triggers). |

## C — Partial client processing (WASM, WebCodecs, Web Workers, WebGPU)

Pose on the phone or in the browser (MediaPipe Tasks Web, WebGPU) and upload landmarks instead of video.

| | |
|---|---|
| Cost | Lowest server cost |
| Blockers | Leonardo's constraint: "do not assume the user's smartphone can perform heavy video analysis". Running pose on 60–120 min of 1080p60 heats the phone and drains the battery, and speed varies 5–10× across devices **[E]**. WebCodecs isn't on Firefox for Android and is Safari 26+ only. Results would differ by device, breaking reproducibility and the "honest measurement" rule. The server would still need the video for evidence frames and for re-analysis when models improve. |
| What *does* belong on the client | metadata reading; trim in/out points; one-tap player selection; resumable presigned multipart upload; skeleton rendering on canvas (already implemented in `VideoPlayer`); a camera-setup checklist |
| Verdict | **Only as progressive enhancement for UX.** It is never part of the measurement path. |

## D — Hybrid (recommended)

**Client-side:** UX-only work (C's light half). **Server-side:** a light API plus a serverless worker (B2) running the staged pipeline (E). **Claude API:** interpretation only, fed with selected evidence.

Validated against the code and the benchmarks:

- **Server overlay** is 37 % of current wall time **[M/D]**. The client canvas renders the same skeleton for free, so the server overlay is dropped from the default pipeline.
- **Dense pose** is needed only around strokes (E §1). The target pipeline cuts CPU 2.4–5× and wall time ~6× **[E]**.
- **Infrastructure** is ≈ $5–8/month for up to 100 analyses **[E]**; Claude is the main variable cost.
- **Local development keeps today's adapters:**
  - SQLite instead of Postgres or the HTTP job source
  - filesystem storage instead of the bucket
  - a stub instead of the real Claude client, unless `ANTHROPIC_API_KEY` is set and the user asks for AI

| Criterion | A | B2 | C | **D** |
|---|---|---|---|---|
| Works for 60-min phone uploads | ✗ | ✓ | ~ (on the phone) | ✓ |
| Monthly infra at 100 analyses **[E]** | ~$7–8 but fragile | ~$7.7 | ~$5 | **~$7.7** |
| Wall time for a 60-min session **[E]** | 4.4 h now; ~40 min target, competing with the API | 35–45 min | depends on the device | **35–45 min** |
| Reproducible measurements | ✓ | ✓ | ✗ | ✓ |
| Battery and thermal load on the phone | none | none | high | **low** (upload only) |
| Local development stays simple | ✓ | ✓ | ✗ | ✓ |
| Change from current code | small | moderate | large | **moderate, additive** |
