# G — Benchmark Plan

Goal: replace every **[E]** and **[U]** figure in D and E with a measurement, and repeat that at each roadmap step to catch regressions.

## Inputs

| Set | Content | Source |
|---|---|---|
| T10 / T30 / T60 | real amateur sessions of 10, 30 and 60 min, phone on a tripod behind or beside the baseline, 1080p60. **Include at least one iPhone HEVC `.mov` and one Android H.264 file.** | Leonardo (also needed for M1.11) |
| L20 | ≥ 20 short clips (M1.11), ≥ 5 of them with hand-labelled stroke times and FH/BH type | Leonardo; labelled with a simple CSV: `video, t_contact_s, type, handedness` |
| Synthetic | the current `src_1080p60.mp4` pedestrian file (regression only, not accuracy) | already in use |

## Metrics per run

| Metric | How |
|---|---|
| Processing time per footage-minute | total wall / footage minutes |
| Effective FPS | frames given to pose / pose wall time, per stage |
| CPU | `RUSAGE_SELF` + `RUSAGE_CHILDREN` cpu-s (see `scripts/runcmd.py`), per stage |
| Peak RAM | max RSS per process; sum across pool processes |
| Pose time | ms/frame mean and p95; detector re-runs (tracking lost) per minute |
| Sampling ratio | frames that got pose / total frames, split into scan and dense |
| Storage | bytes per artifact per footage-minute (original, proxy, pose data, evidence) |
| Stroke detection quality | precision and recall vs labels (±150 ms tolerance); FH/BH accuracy; `unknown` rate |
| Measurement quality | landmark visibility at contact; share of strokes where each metric is observable; std of repeated metrics on near-identical strokes |
| Claude tokens and cost | `usage.input_tokens`, `output_tokens`, cache reads, cost per call (from API responses), latency |
| Cloud reality check (Step 4.4) | Railway usage dashboard: vCPU and GB time for the same T30 run vs local |

## Experiments

1. **Baseline:** current pipeline on T10, T30, T60 (overlay on and off).
2. **Pose model:** MediaPipe lite, full and heavy vs RTMPose (rtmlib/ONNX) on L20, comparing:
   - speed
   - detection of the *correct* player with an opponent in view
   - jitter at contact
3. **Timeline resolution:** 1080p vs 720p CFR proxy, comparing landmark error at contact against the 1080p reference, and the cost difference.
4. **Scan rate:** 5 vs 10 vs 15 fps, measuring candidate-stroke recall.
5. **Dense window size:** [-0.9, +0.6] s vs a wider window; metric observability.
6. **Parallelism:** 1, 2, 4 and 8 pool processes; speedup and RAM.
7. **Decode shortcuts:** whether `-skip_frame nonref` or a lower-resolution decode keeps scan recall.
8. **Claude:**
   - Session call: Sonnet 5.5 vs Opus 5.5, and effort medium vs high, on 10 sessions with Leonardo's rubric score.
   - Deep call: frame count 6 vs 12.

## Harness

- Add `scripts/bench/` (promoted from the reference scripts in `benchmarks/scripts/`, paths made configurable).
- Write one JSONL line per run with git SHA, machine and input hash, and store it under `docs/audit/benchmarks/`.
- **Claude experiments run only with Leonardo's approval** because they spend money. The token count endpoint can size evidence packs for free first.
