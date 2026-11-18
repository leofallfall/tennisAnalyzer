# D — Cost Model

Labels: **[M]** measured, **[D]** derived from measurements and official prices, **[E]** estimate (assumption stated), **[U]** unmeasured.
Prices checked on 2026-11-18. All amounts in USD.

## 1. Official prices used

### Railway (railway.com/pricing and docs)

| Item | Price / rule |
|---|---|
| Hobby plan | **$5/month subscription, which includes $5 of usage.** Usage above $5 is billed as overage. |
| CPU | $20 per vCPU-month = **$0.00000772 per vCPU-second**. Billed on *measured* usage, not on allocation. |
| RAM | $10 per GB-month = **$0.00000386 per GB-second** (measured usage) |
| Volume | $0.15 per GB-month. Hobby volumes max **5 GB**, attached to **one** service. |
| Bucket (S3-compatible object storage) | **$0.015 per GB-month.** S3 operations free; bucket egress free, including through presigned URLs. Uploads *from a Railway service* into a bucket count as that service's egress. Hobby max 1 TB. |
| Service egress | $0.05/GB |
| Limits | HTTP request max **15 min**. Serverless services sleep 5–10 min after their last outbound packet and wake on an incoming request; the first request after sleep may fail and must be retried. Billing while asleep is not spelled out; we assume compute isn't billed **[E, verify on the first invoice]**. Cron minimum interval is 5 min. |

### Alternatives (for the worker only)

| Provider | Price |
|---|---|
| Modal | CPU $0.0000131 per physical-core-second (1 core = 2 vCPU, so $0.00000655 per vCPU-s); RAM $0.00000222 per GiB-s; **$30/month free credits**; regional multiplier 1.15–1.75× |
| Google Cloud Run Jobs | $0.000018 per vCPU-s; $0.000002 per GiB-s; **free tier 240,000 vCPU-s and 450,000 GiB-s per month** |

### Claude API (per million tokens; input / output)

| Model | Input / output | Cache read |
|---|---|---|
| Sonnet 5.5 | $2 / $10 | $0.10 |
| Opus 5.5 | $4 / $20 | $0.20 |
| Haiku 5.5 | $0.10 / $0.50 (prompts ≤ 100K tokens) | — |

- Batch API: −50 %.
- Image tokens = ⌈w/28⌉ × ⌈h/28⌉. A 768×432 frame costs 448 tokens.

## 2. Unit costs derived from the benchmarks

### Per footage-minute (1080p60 source)

| | CPU | RAM × time | Railway compute cost | Artifacts written |
|---|---|---|---|---|
| **Current pipeline** | 399 vCPU-s **[M]** | ~0.85 GB × 264 s **[M]** | 399 × 0.00000772 + 224 GB-s × 0.00000386 = **$0.0040** **[D]** | 33 MB **[M]** |
| **Target pipeline** (E, 720p proxy) | ~120 vCPU-s **[E]** | ~2 GB × 40 s **[E]** | **$0.0012** **[E]** | ~5 MB **[E]** |

### Original file size per footage-minute

| Source | Size |
|---|---|
| H.264 1080p60 at 15 Mbit/s | 113 MB **[M]** |
| iPhone HEVC 1080p60 | 58 MB **[M]** |

The model uses **~100 MB per minute** for safety.

### Always-on services per month (730 h)

| Service | RAM cost | CPU cost | Total |
|---|---|---|---|
| API (106 MB idle **[M]**; ~150 MB budgeted) | 0.15 GB × 2.63 M s × $0.00000386 = $1.52 **[D]** | ~0.005 vCPU average → $0.10 **[E]** | **≈ $1.6** |
| Worker idle, always on (97 MB, 0.1 % of a core **[M]**) | — | — | **≈ $1.0** **[D]** |
| Worker as a serverless service (sleeps when idle) | — | — | **≈ $0 idle** **[E]** |

## 3. Requested scenarios

### Scenario 1 — light backend always on

- API only (static web on the same service, or a free static host): **≈ $1.6/month**.
- That is inside the $5 included usage, so **the bill is $5**.

### Scenario 2 — backend plus a small worker, both always on

- **≈ $2.6/month** usage **[D]**: still inside the $5 included, so **the bill is $5**.
- With a serverless worker: ≈ $1.6 usage.
- Idle infrastructure is not a cost problem on Railway.

### Scenarios 3–5 — one video (single analysis)

| | 10 min | 30 min | 60 min |
|---|---|---|---|
| CPU, current **[M → D]** | 3,990 vCPU-s | 11,970 vCPU-s | 23,940 vCPU-s |
| CPU, target **[E]** | 1,200 | 3,600 | 7,200 |
| Wall time on 4 vCPU, current **[D]** | **44 min** | **2.2 h** | **4.4 h** |
| Wall time, target **[E]** | 6–8 min | 18–23 min | 35–45 min |
| Peak RAM, current / target | 0.85 / ~2 GB | 0.85 / ~2 GB | 0.85 / ~2 GB |
| Original size (58–113 MB/min **[M]**) | 0.6–1.1 GB | 1.7–3.4 GB | 3.5–6.8 GB |
| Artifacts, current (33 MB/min) / target (~5 MB/min) | 330 / 50 MB | 1.0 GB / 150 MB | 2.0 GB / 300 MB |
| **Railway compute, current** | **$0.040** | **$0.12** | **$0.24** |
| **Railway compute, target** | **$0.012** | **$0.036** | **$0.072** |
| Bucket storage of the original, per month kept | $0.009–0.017 | $0.026–0.05 | $0.05–0.10 |
| Service egress worker → bucket, current / target | $0.017 / $0.003 | $0.05 / $0.008 | $0.10 / $0.015 |
| Upload phone → bucket, and playback bucket → phone | $0 (bucket egress free) | $0 | $0 |
| Claude session call, Sonnet 5.5 batch (§4) | $0.05 | $0.05 | $0.06 |
| **Total for one analysis, target** (excluding deep dives) | **≈ $0.07** | **≈ $0.10** | **≈ $0.15** |

- **The volume trap:** a 60-minute original (3.5–6.8 GB) does not fit on a 5 GB Hobby volume. Originals must go to a bucket.
- **The upload trap:** the API-upload path also fails on the 15-minute HTTP limit at mobile bandwidth (6 GB at 20 Mbit/s ≈ 40 min).

### Scenarios 6–8 — 10 / 50 / 100 analyses per month

Assumptions **[E]**:

- 20 footage-minutes per analysis
- one session call, plus 2 single-stroke deep analyses on Sonnet 5.5
- originals kept 30 days, then deleted
- derived artifacts kept

| Monthly | 10 analyses | 50 analyses | 100 analyses |
|---|---|---|---|
| Footage-minutes | 200 | 1,000 | 2,000 |
| Railway: API always on | $1.6 | $1.6 | $1.6 |
| Railway: worker compute, target | $0.24 | $1.2 | $2.4 |
| *(Railway: worker compute, current pipeline)* | *$0.80* | *$4.0* | *$8.0* |
| Railway: bucket, originals (30-day window, ~100 MB/min) | 20 GB → $0.30 | 100 GB → $1.50 | 200 GB → $3.00 |
| Railway: bucket, derived artifacts (grows ~5 MB/min) | +1 GB/month → ~$0.02 | +5 GB/month → ~$0.08 | +10 GB/month → ~$0.15 |
| Railway: service egress (artifact uploads) | ~$0.05 | ~$0.25 | ~$0.50 |
| **Railway usage total, target** | **≈ $2.2** | **≈ $4.6** | **≈ $7.7** |
| **Railway bill** ($5 including $5 of usage) | **$5** | **$5** | **≈ $7.7** |
| *(Railway bill, current pipeline: always-on worker, 33 MB/min artifacts, bucket)* | *$5 (≈ $4.1 usage)* | *≈ $10* | *≈ $18* |
| **Claude API** (≈ $0.23 per analysis, §4) | **≈ $2.3** | **≈ $11.5** | **≈ $23** |
| Claude API, if deep dives use Opus 5.5 (≈ $0.41 per analysis) | ≈ $4.1 | ≈ $20.5 | ≈ $41 |
| **Total per month** | **≈ $7–9** | **≈ $16–26** | **≈ $31–49** |

**Conclusion: Claude, not Railway, is the main variable cost** from about 30 analyses per month. Under the target pipeline, compute is about 10 % of the bill. Storage of originals is the second-largest Railway line, which makes **retention policy a direct cost lever**. A 7-day window instead of 30 days cuts that line by about 75 %.

## 4. Claude API cost per call [E, built from the token formula and prices]

| Call | Input tokens | Output tokens (including thinking) | Model | Cost |
|---|---|---|---|---|
| Session aggregation | ~24k: system prompt 4k, per-stroke table ≤ 150 strokes × 140 = up to 21k, stats 2k, 8 frames × 448 ≈ 3.6k | ~5k | Sonnet 5.5 | $0.048 + $0.05 = **$0.10**; **batch $0.05** |
| Session aggregation | same | same | Opus 5.5 | $0.20; batch $0.10 |
| Single-stroke deep | ~15k: cached system prompt 4k, time series 6k, 12 frames 5.4k, 2 crops 3k | ~6k | Sonnet 5.5 | **≈ $0.09** |
| Single-stroke deep | same | same | Opus 5.5 | **≈ $0.18** |
| Titles, short summaries | ~3k | ~0.5k | Haiku 5.5 | ≈ $0.0006 |

Notes:

- **Long sessions:** the per-stroke table is capped. Beyond about 150 strokes, send aggregated statistics plus outliers only, so cost does not grow with session length.
- **Prompt caching:** saves real money only when calls arrive within the cache lifetime (5 min by default; 1 h at a higher write price). It benefits deep dives that follow a session call. It barely helps isolated session calls from a few users.
- **Result cache** (keyed by evidence-pack hash): repeats cost $0.
- **Output tokens dominate** because of thinking. Effort is the main cost dial: test `medium` against `high` in G.

## 5. Alternative worker hosting (target pipeline, 2,000 footage-minutes per month)

| Option | Cost per footage-minute | 2,000 min/month | Added complexity |
|---|---|---|---|
| Railway serverless worker | $0.0012 | **$2.4** | none: same project, private network |
| Modal CPU | $0.00097 × region (1.15–1.75) | **$0** inside the $30 free credits (≈ 30k footage-min); ≈ $2–3.4 beyond | a second provider, secrets, egress for the original out of the Railway bucket (bucket egress is free) |
| Google Cloud Run Jobs | $0.0023 | ≈ **$0**: the free tier ≈ 2,000 footage-min/month, at its edge | GCP account, IAM, a container registry |

Verdict:

- **Keep the worker on Railway.** The most another provider could save is ~$2–3/month, and it costs a second platform to operate.
- **Re-evaluate only on these triggers:**
  1. Footage beyond ~20,000 min/month.
  2. Wall-time targets that need fan-out beyond one 8–32 vCPU worker. Modal's parallel functions fit here.
  3. A GPU model becomes necessary, for example ball tracking.
- The worker is a plain container, so moving it later is a deployment change, not a rewrite.

## 6. What is measured and what is not

- **Measured:** stage CPU, wall time and RAM on a 4-vCPU VM; output sizes; idle RSS.
- **Estimated, assumptions stated:**
  - the target pipeline
  - stroke density (10 per minute)
  - Claude token counts
  - Railway vCPU speed equal to the test VM (±30 %)
  - no billing while serverless services sleep
- **Not measured:** real tennis footage of any kind, real Claude responses, and a bill from an actual Railway deployment. Plan G closes all three.
