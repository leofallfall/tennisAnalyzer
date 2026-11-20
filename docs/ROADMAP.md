# Roadmap

## Current status (2026-11-20)

**M0 Foundation: complete.** Monorepo, docs, web app, API, worker, engine interfaces,
first pose experiment, tests. **M1 is in progress**: all functional pieces exist and
work on synthetic and real (non-tennis) footage; validation on real amateur tennis
footage has **not** been done yet.

**M1.10 technical fixes: complete** (2026-11-19, `docs/M1_10_TECHNICAL_FIXES.md`): UTC
timestamps, no server paths in API responses, real MediaPipe runtime check, recent errors
in System Status, proxy → original time map, smaller/compressed `pose.json`, dominant hand
and backhand persisted.

**M2.0 stroke-recognition design: approved** (2026-11-19, decisions D1–D11, summarised in
ADR-017). **M2.1 dataset and labels: done for the personal prototype** (2026-11-20,
`docs/M2_1_DATASET.md` §9): 6 clips, 35 labelled impacts, **one player** (the developer).
**Personal-first priority** (ADR-018): the prototype is developed and checked on the
developer's own tennis first; validation on other players comes later and is not claimed.
**M2.2 (signal processing only) approved** on 2026-11-20 and **in progress** after the
PR #1/#2 merges: intervention 1 (time base, `docs/M2_2_SIGNALS.md` §2) merged (PR #3);
intervention 2 (smoothing of positions, §4) under review; derivatives and the other
signals are not implemented yet (each intervention waits for review).

**Personal AI analysis (Claude Code, no API): plumbing done** (2026-11-18, ADR-013,
`docs/AI_ANALYSIS.md`). Evidence Packs, the `/analizza-sessione` skill, validated import and
the UI flow work end to end. They only produce technical feedback once M2 provides strokes.

## Milestones

### M0 — Foundation ✅
Repository, tooling, docs, initial app skeletons.

### M1 — Video processing prototype (in progress)
Acceptance criteria:
1. ✅ A video can be uploaded.
2. ✅ Metadata can be extracted (rotation, VFR, fps, resolution).
3. ✅ The video can be decoded.
4. ✅ Frames are extracted with correct timestamps (tested).
5. ✅ Pose estimation runs on selected frames (stride).
6. ✅ Joint coordinates exported in structured form (`pose.json`).
7. ✅ Skeleton overlay video generated.
8. ✅ Processing errors reported as structured errors.
9. ✅ Basic video quality information available.
10. ✅ Frontend displays the uploaded video and its processing status.
10b. ✅ **M1.10 — technical fixes before validation** (see `docs/M1_10_TECHNICAL_FIXES.md`).
11. ◐ **Validated on real amateur smartphone tennis clips: carried out as M2.1** (D1).
    Done for one player (6 clips, 35 impacts, `docs/M2_1_DATASET.md` §9); the broader scope
    below stays open and is not a blocker for the personal prototype (ADR-018). Original scope:
    ≥ 20 real amateur smartphone tennis clips covering side/behind
    views, 30/60/120/240 fps, portrait/landscape, indoor/outdoor, left/right-handed.
    Record detection rate, visual skeleton accuracy on wrists/elbows during the swing,
    processing time. Calibrate quality thresholds from this set. Also record, per clip:
    - the player to follow (manual annotation) and every moment where the skeleton
      appears to switch to another person (`num_poses=1` limit; no automatic detection);
    - declared dominant hand / backhand;
    - `time_mapping` summary (duplicated/dropped frames, max error) for VFR and
      120/240 fps clips, and a spot check of a few proxy frames against the original;
    - `pose.json` size and browser load time.

### M2 — Stroke detection (design approved 2026-11-19, ADR-017)
Candidate stroke events from pose kinematics (hitting wrist relative to the torso, shoulder
rotation as confirmation); forehand/backhand from body side and swing direction with the
declared dominant hand; explicit `uncertain`/`unknown` outcomes with reasons; a re-runnable
step reading `pose.json` and writing `strokes.json`. Groundstrokes only.
Acceptance target: precision/recall ≥ 0.9 on annotated rallies from suitable views, **to
be confirmed or revised after the M2.1 data** (D11).

| Step | Goal | Status |
| --- | --- | --- |
| M2.1 | Dataset, labels, `eval_strokes.py`, input visibility report (also M1 item 11) | ✅ single-player dataset (ADR-018) |
| M2.2 | Kinematic signals (body frame, gap-aware derivatives on `timemap.json` source times, repeated/skipped frames, smoothing, NumPy peaks) | in progress: #1 time base merged; #2 smoothing under review (2026-11-20) |
| M2.3 | Candidate events + `strokes.json`, CLI on existing analyses, evaluated on the dataset | not started |
| M2.4 | Forehand/backhand with levels and uncertainty reasons | not started |
| M2.5 | Player selection by tap (verification first), identity jumps; `num_poses` > 1 if M2.1 data require it | not started |
| M2.6 | Worker step, API artifact, contracts, UI list, capabilities | not started |
| M2.7 | (conditional) 10 fps scan + dense pose around candidates, only if measured cost requires it | conditional |
| M2.8 | (conditional) other pose estimator (e.g. rtmlib) behind `PoseEstimator`, only past an agreed error threshold | conditional |

### M3 — Single stroke deep analysis
Phase segmentation with user-adjustable boundaries; first validated 2D metrics
(elbow angle, knee flexion, contact height/distance); evidence frames; multi-angle sync.

### M4 — Session report
Aggregation with observable denominators; issue identification and ranking; filters;
issue → stroke navigation.

### M5 — Serve
Serve detection, phases (stance, toss, trophy, drop, contact, follow-through), metrics.

### M6 — Racket and ball (experimental)
Evaluate racket keypoint detection and ball detection feasibility at 60–240 fps;
evaluate RTMPose/MMPose vs MediaPipe on tennis footage (wrist accuracy, speed).

### M7 — AI Coach and improvement paths
Structured LLM interpretation per AI_GUIDELINES; drills; improvement paths; coach notes.

### M8 — Comparison and progress
Comparability checks, normalisation, phase-aligned comparison, professional references
(licensed content only), longitudinal progress with recording-condition caveats.

## Open technical items (tracked, not scheduled inside M1.11)

| Item | Priority | Milestone / gate |
| --- | --- | --- |
| Explicit player selection (tap on the player, multi-person tracking) | High | M2.5, designed from M2.1 evidence; needed before M2 results are trusted |
| `TimeInterval` docstring says "source timeline" while engine times are on the proxy timeline (D10) | Medium | M2.3, together with the `strokes.json` model |
| Data retention, deletion and storage policy (endpoints + documented policy, backups) | High | **Gate: before the app is reachable online** (any cloud deployment) |
| Alignment of `AI_GUIDELINES.md` with the actual AI result format and validation | High | **Gate: before AI analysis is integrated** for real technical feedback (when M2 strokes feed it; at the latest M7) |
| Worker heartbeat / "worker running" indicator, stale-job recovery by timeout | Medium | Before online deployment (cloud worker); useful but not required for M1.11 |
| Filesystem protection on Windows (`O_NOFOLLOW`/`dir_fd` unavailable: `safe_files.py` falls back to weaker checks) | Medium | Before supporting Windows installations; document until then |
| Concurrency of AI imports (simultaneous imports, duplicate detection beyond identical files) | Medium | Before multi-user or online use; at the latest M7 |
| Windowed/columnar `pose.json` loading in the browser | Low | M3 (deep stroke analysis), if M1.11 shows load problems earlier |

## Dependencies

M1 → M2 → M3 → M4; M3 → M5; M1 → M6 (parallel research); M4 + M3 → M7; M3 + M4 → M8.

## Priorities

Reliability of ingestion and pose > stroke detection > metrics > AI interpretation >
comparison/progress. Each M2 step starts only after explicit approval (M2.2 approved
2026-11-20; M2.3 and later not yet). Personal-first (ADR-018): results are first judged on
the developer's own tennis; the engine stays player-independent (no body-specific constants,
per-player hand/backhand, personal data separate from algorithm parameters).
