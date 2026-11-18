# Architecture audit — 2026-11-18

Audit only: **no application code was changed.** Documents:

| Doc | Content |
|---|---|
| [A — Current architecture audit](A_CURRENT_ARCHITECTURE_AUDIT.md) | what runs where today, the cost of each operation, deployment blockers |
| [B — Architecture comparison](B_ARCHITECTURE_COMPARISON.md) | A all-in-one / B worker / C client-side / D hybrid |
| [C — Target architecture](C_TARGET_ARCHITECTURE.md) | diagram, placement of each operation, local vs cloud adapters |
| [D — Cost model](D_COST_MODEL.md) | Railway scenarios 1–8, Claude API, storage and egress, alternative worker hosts |
| [E — Optimised pipeline](E_OPTIMIZED_PIPELINE.md) | staged pipeline, temporal resolution needs, evidence selection, model choice |
| [F — Implementation roadmap](F_IMPLEMENTATION_ROADMAP.md) | small testable steps, mapped onto M1–M4 |
| [G — Benchmark plan](G_BENCHMARK_PLAN.md) | what still needs measuring and how |
| [Baseline benchmarks](benchmarks/2026-11-18-baseline.md) | everything measured, with raw JSONL and scripts |

Labels throughout: **[M]** measured · **[D]** derived from measurements · **[E]** estimate · **[U]** unmeasured.

## Answer to the final question

**What is the most efficient architecture to go from a smartphone upload to a reliable Claude analysis, at low infrastructure cost on a small Railway plan?**

1. **The phone only uploads.** It sends the original straight to a private Railway bucket through presigned, resumable multipart upload, plus metadata, optional trim points and a one-tap player selection. It does no CV.
2. **A light FastAPI service stays always on** (≈ $1.6/month **[D]**). It holds the DB and job queue as today, signs URLs and serves results. It never touches video bytes.
3. **One worker service runs only when there are jobs** (Railway serverless, woken by the API). It runs a **staged CPU pipeline:**
   - CFR timeline
   - a 10 fps lite-pose scan
   - candidate strokes
   - full-rate pose only in ~1.5 s windows, in parallel processes
   - FH/BH classification and measurements
   - a compact evidence pack

   **No server overlay** (the browser draws the skeleton) and **no GPU**.
4. **Claude API only interprets, at two depths:**
   - **Session ranking:** one call per session to **Sonnet 5.5** via Batch, ≈ $0.05 **[E]**.
   - **Deep single-stroke analysis:** **only when the user picks an issue**. Sonnet 5.5 ≈ $0.09, or Opus 5.5 ≈ $0.18 as an expert option **[E]**.
   - **Results** are cached by evidence hash; Haiku 5.5 handles trivial text.
5. **Originals expire** after 7–30 days; derived data is kept.

**Expected outcome:**

| | Today | Target |
|---|---|---|
| Worker CPU per footage-minute | 399 cpu-s **[M]** | ≈ 120 **[E]** |
| A 60-min session, wall time | 4.4 h **[M → D]** | ≈ 35–45 min **[E]** |
| Railway bill, up to ~60 analyses/month | — | the **$5** Hobby minimum |
| Railway bill, 100 analyses/month | — | ≈ $7.7 **[E]** |
| Claude, 100 analyses/month | — | ≈ $23–41 **[E]** |

**Claude, not compute, is the cost to manage.** Railway is a sustainable place for this worker at this scale. Modal and Cloud Run would save at most ~$2–3 a month, so they are not worth a second provider until the D §5 triggers are met.

**Biggest open risk:** the target figures rest on synthetic footage and an assumed density of 10 strokes per minute. Step 0.3 of the roadmap (real clips, M1.11) must run before the staged pipeline is built.
