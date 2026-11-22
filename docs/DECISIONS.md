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

### ADR-013 Personal AI analysis through Claude Code (no API) behind an `AIClient` port
**Context:** Leonardo wants Claude's interpretation during development without paid API
calls, using Claude Code on his own subscription, and a later switch to the Anthropic API
without rewrites. The audit (docs/audit, step F 3.1) already planned an `AiClient` port.
**Decision:**
- The app builds a model-independent **Evidence Pack** (small JSON files plus ≤ 768 px frames, with SHA-256 integrity).
- The user runs `/analizza-sessione <pack>` interactively in Claude Code, which writes only `analysis_result.json`.
- The app imports that file after strict validation: schema plus cross-checks against the pack.
- `AIClient` has `ClaudeCodeAdapter` (returns instructions; executes nothing) and an `AnthropicAPIAdapter` stub.
- Results go to a new `ai_analyses` table, never into `Analysis.results`.
- The AI layer lives in `apps/api/tennis_api/ai/`, not the engine, so the engine stays LLM-free (ADR-002).
- Packs are built synchronously in the API: a few frames, seconds of work. This is a small exception to "API = light" that stays as long as packs stay small.

**Alternatives rejected:**
- Automating Claude Code or reusing subscription tokens as an API: not an officially supported use, and the user excluded it.
- A local LLM: excluded by the user.
- Building packs in the worker: more moving parts for a few seconds of work.

**Consequences:**
- Until stroke detection exists (M2), real packs carry no strokes, and validation forces "insufficient evidence" session results.
- Single-stroke analysis works on user-selected intervals with 2D angle time series.

### ADR-014 Evidence Pack integrity anchored in the database; append-only AI import history
**Context:** The Personal AI Analysis audit (2026-11-18) proved two critical flaws.
- C1: unchecked manifest paths let the image endpoint serve `/etc/hostname`.
- C2: the only hashes lived inside the pack, so editing a file *and* its hash in
  `manifest.json` imported a forged issue.

It also found (I2) that a new import overwrote the previous one, and (I3) that the pack
carried the analysis title, the free-text focus and the original file name.
**Decision:**
- All pack file access goes through `ai/safe_files.py`:
  - paths are strict relative names;
  - files are opened component by component with `O_NOFOLLOW`, so no symlink is followed;
  - each file must be a regular file;
  - bytes are read once, then hashed and parsed from the same buffer.
- At creation the API stores the SHA-256 of `manifest.json` in `ai_analyses`; it is
  computed from the in-memory manifest, never read back from disk.
- On import, validation:
  - checks the manifest against the stored hash, and the pack id, analysis id, kind and
    folder against the DB row;
  - requires the full file set for the pack kind;
  - rejects symlinks and any unlisted file.
- Packs created before this change (no stored hash) are **legacy**: they are not
  imported, and the user prepares a new pack. Results already imported are kept.
- Every valid import adds an `ai_analysis_imports` row (version n+1).
  - An identical file is refused (409).
  - Invalid attempts only update the latest-attempt fields.
- Packs no longer include title, focus or original file names.
- Schema changes are applied by a small additive migration runner (`tennis_api/migrations.py`) run by the API at startup.
**Alternatives:**
- An HMAC signature of the pack: it needs a secret key and adds nothing while the database already sits outside Claude Code's write scope.
- Read-only file permissions: a deterrent only.
- Alembic: a new dependency for two additive steps. Revisit for renames, drops or PostgreSQL.
- Overwriting with a confirmation flag: it loses history.
**Consequences:**
- Any change to a pack after creation blocks its import, even a harmless one; the fix is to prepare a new pack.
- The CLI `ai_pack.py validate` reads the anchor from the database when it can. If it cannot, it says the manifest hash was not checked.
- Security still depends on the database not being writable by the analyst. Claude Code runs with the user's permissions, so this is protection against accidental or unreviewed changes, not against a deliberately malicious local process.


### ADR-015 One frame-rate rule for every proxy, and a reconstructed proxy → original map (M1.10)
**Context:** the proxy (ADR-006) resampled VFR inputs with the `fps` filter and left CFR
inputs to `-fps_mode cfr`, and FFmpeg aligned the output on the container start (an earlier
audio track added duplicated frames). Nothing recorded which original frame each proxy
frame shows.
**Decision:** every proxy is built with `setpts=PTS-STARTPTS,fps=<rate>:eof_action=pass`
(CFR inputs keep their nominal rate, VFR inputs get their average rate). `timemap.py`
reconstructs the `fps` filter's selection rule from the source packet PTS and stores
`timemap.json` plus a summary in the report, explicitly flagged `exact_time_sync: false`.
**Alternatives:**
- Emulate `-fps_mode cfr` as well: two rules to reproduce, the vsync one depending on
  internal thresholds and lead-in handling (measured to differ by one frame on a delayed
  start).
- Read the selection back from FFmpeg (`showinfo` checksums before/after `fps`): exact but
  needs parsing stderr of the encode and ambiguous for identical frames.
- PTS-based timestamps end to end: exact, but every consumer changes (ADR-006).
**Consequences:** proxy time 0 is always the first video frame; CFR proxies are unchanged
except when the container start differs from the video start. Ground-truth tests encode
each frame's index in its pixels and check the map against the real proxy. Real phone
files are validated in M1.11.

### ADR-016 API response hygiene: UTC, no server paths, compressed JSON (M1.10)
**Context:** SQLite returned naive datetimes, serialised without offset and shown 1–2 h
early in Italy; error details, reports and `system/info` exposed absolute paths and
tracebacks; `pose.json` was sent uncompressed.
**Decision:** `UTCDateTime` column type (UTC on write, tagged UTC on read: fixes old rows
without migration); engine errors name files by base name and scrub tool stderr, the API
passes error bodies, analysis status/results and served reports through
`redaction.redact` (known server roots → `<storage>`, `<models>`, `<app>`, `<home>`),
tracebacks stay in the log with an `error_id`; FastAPI's bundled `GZipMiddleware` for JSON.
**Alternatives:** storing ISO strings instead of datetimes (schema change); a database
data rewrite to strip old paths (irreversible, and new rows would still need the same
rule); hiding all error details (loses useful diagnostics such as ffprobe's reason);
Brotli (new dependency).
**Consequences:** redaction is a safety net based on known roots: a path outside them in a
third-party message would not be shortened (engine errors no longer produce such paths).
The AI hand-off command intentionally keeps the pack path the user types.

### ADR-017 Stroke recognition (M2): pose-kinematics rules first, measured before built
**Context:** M2 must find stroke events, the player involved, forehand/backhand, a time and
an interval, with explicit uncertainty. The code had pose only (33 MediaPipe 2D landmarks
per frame, `num_poses=1`), no kinematics and no labelled real footage. The M2.0 design
(2026-11-19) was approved with decisions D1–D11.
**Decision:**
- D1: M2.1 (dataset, labels, evaluation tooling) also fulfils M1 item 11.
- D2: the MVP works on the full-rate `pose.json` already produced; the 10 fps scan with
  dense pose around candidates is postponed (conditional M2.7).
- D3: no SciPy; peaks and smoothing are written in NumPy.
- D4: the event time is the hitting-wrist speed peak; `estimated_contact_s` stays `null`
  until the peak-to-impact offset is measured on labels.
- D5: confidence as levels (`high`/`low`/`none`) with reason codes; numeric confidence
  stays `null` until calibrated.
- D6: no forehand/backhand without a declared dominant hand.
- D7: MVP scope is groundstrokes; serve, smash and volleys are `unknown` or out of scope.
- D8: player selection in two steps (tap as verification, then `num_poses` > 1 if data
  require it); another pose estimator (e.g. rtmlib) only past an error threshold agreed
  after M2.1.
- D9: new `strokes.json` artifact; `StrokeClassifier` receives a typed context instead of
  `dominant_hand: str` (done in M2.3/M2.4, not before).
- D10: `TimeInterval` is documented on the proxy timeline; source times are reported too
  when `timemap.json` exists.
- D11: the ROADMAP target (precision/recall ≥ 0.9) is confirmed or revised after M2.1.
**Alternatives:** racket or ball detection (generic detectors fail on motion blur; no
labelled data); a trained model (GBDT/1D-CNN) from the start (no labelled data to train or
evaluate); a 10 fps scan first (cheaper, but fewer samples around the wrist peak and more
MediaPipe tracking loss, with no measurement yet).
**Consequences:** nothing is built before the input is measured on real clips. The
evaluation tooling (`tennis_cv.evaluation`) lives in the engine as a pure library used only
by `scripts/eval_strokes.py`, so it is typed and tested like the rest but never runs in
production. Labels use the proxy frame index printed on `overlay.mp4`, so no annotation tool
and no time conversion are needed.

### ADR-018 Personal-first development and validation (2026-11-20)
**Context:** M2.1 produced a first real dataset of one player (the developer): 6 clips, 35
impacts, one indoor venue, 30 fps. A representative dataset (other players, venues, frame
rates, ≥ 5 dev and ≥ 5 test clips from different sessions) would take months to collect.
**Decision:**
- Priority: build a tool that works reliably on the developer's own tennis first. The
  current M2.1 dataset is enough to start M2.2. More of his sessions, and friends' clips when
  available, are added as extra checks, not as a blocker.
- Every report states the limit: one player; visibility results reflect his body and his
  recording conditions; generalisation to other players is not shown; no result is a
  general validation of the system.
- The software stays player-independent: dominant hand and backhand are per-player inputs;
  measurements are normalised to body proportions when technically possible; detection rules
  contain no coordinates or physical traits of a specific body; personal data stay separate
  from algorithm parameters; new players and datasets can be added without rewriting the
  engine. No calibration system or multi-user features are added for this.
- Signal processing must not treat proxy frames as equally spaced: sample times come from
  `timemap.json` (once the map is exact, see `M2_1_DATASET.md` §9), repeated proxy frames are
  removed or marked, and a derivative over an
  irregular or unreliable interval is reported as unreliable or non-observable rather than
  as motion (timing artefacts are not biomechanics).
**Alternatives:** collect 10–20 sessions including other players before M2.2 (more general
evidence, months of delay for a personal tool); tune the engine to this player only (faster
first results, but a rewrite for anyone else).
**Consequences:** thresholds tuned in M2.2–M2.4 may encode this player's style and must be
re-checked when other players' clips arrive. Accuracy figures, when they exist, are labelled
as single-player. M1 item 11 stays partly open.

### ADR-019 Remaining M2.2 direction and M2.3/M2.4 constraints (2026-11-20)
**Context:** M2.2 #1 (time base) and #2 (smoothing) showed: timing artefacts are solved
(regenerated maps); MediaPipe VIDEO tracking keeps the peak frame but not the amplitude
(0.4–1.7×); with visibility ≥ 0.5 as a hard switch, wrists have a value on only 19–72 % of
samples (M2.1: of 12 checked right wrists below 0.5, one was on the hand; see
`M2_2_SIGNALS.md` §4.3).
**Decision (approved by the developer):**
- Visibility: two quality levels, *good* and *low but usable*, instead of one switch at 0.5.
  Raw coordinates, visibility, quality and smoothing state are always kept. Unreliable
  samples still break runs (no interpolation across holes). No weighting or adaptive
  algorithms until their need is shown.
- Scale: robust torso length from reliable measurements, stabilised over time; no
  normalised value when the torso is not observable; pixel coordinates always kept.
- Velocity: first derivative of the same local polynomial fit, on real times. Described as
  *apparent 2D (projected) speed*, used for timing and comparisons within one video, not
  as the physical speed of a body segment. No accelerations for now.
- Complementary signals: shoulder line, hip line, distance between hands, wrist position
  relative to the torso. Dominant hand and backhand type are player configuration.
- Evidence levels are always kept apart: synthetic tests, behaviour observed on the 13
  videos / 35 impacts, and validation. Results on one player are not a generalisation.
- M2.2 stays bounded: only the signals M2.3 needs; choices that cannot be validated yet are
  documented, not engineered further.
- M2.3 (after explicit approval, exit criterion proposed first): stroke candidates from
  speed peaks plus complementary signals, thresholds relative to the video, peak grouping,
  confidence and evidence; a peak is not automatically a stroke; abstention allowed.
- M2.4: forehand/backhand depends on player configuration and camera view; `unknown` allowed.
- Peak finding moves from M2.2 to M2.3, where it is used.
**Alternatives:** keep 0.5 as a hard switch (simpler, loses most wrist samples around
backhands); finite differences of smoothed positions for speed (inconsistent at run edges);
absolute speed thresholds (not supported by the VIDEO/IMAGE comparison).
**Consequences:** the remaining M2.2 interventions are proposed one by one and reviewed;
M2.3 does not start without approval.
**Addendum, intervention 6 (2026-11-22, under review):** wrist position relative to the
mid-hip point in image axes (lateral and vertical separate); line angles as the direction
left → right in (−180°, 180°], y up, not unwrapped; GOOD-only = no LOW sample in the fit of
either wrist; states combined over the keypoints used, scale state apart; `PlayerConfig`
reuses the manifest enums and only names roles. Alternatives: axes attached to the trunk
(change with every bend), mid-shoulder origin (moves with shoulder tilt during the swing),
undirected line angle modulo 180° (no wrap, but loses which side is where), GOOD-only from
raw instead of smoothed positions (noisier, different from every other signal).

### ADR-020 Session Analysis as entry point; single-stroke analysis on demand (2026-11-20)
**Context:** the product's main experience starts from a whole session: recurring technical
issues per forehand / backhand / both, personal reference executions, and access to the
strokes behind each issue. The roadmap had single-stroke analysis (M3) before the session
report (M4). Pose is already computed on every frame (D2), so computation is not what
separates the two analyses. Design discussed in the developer's project (architecture
review, revision 2), approved by the developer.
**Decision (A1–A11):**
- M3 and M4 swap: Session Analysis (M3) is the first product result; single-stroke deep
  analysis (M4) follows and runs on demand.
- Session indicators are computed on every stroke and anchored to the stroke event, not to
  phases.
- Initial issues are relative to the player (inconsistency, deviation from the usual range,
  forehand/backhand differences); no unvalidated absolute biomechanical thresholds.
- "Personal reference execution" replaces "positive execution": confirmable or discardable
  by the user; it does not mean a successful or technically correct stroke.
- Indicators are validated (M3.1) before any issue is generated. Availability, geometric
  correctness, stability and view compatibility are assessed separately; having a value on
  most strokes is not validation. The four indicators proposed (lateral impact distance,
  impact height, shoulder-turn range, finish completeness) are candidates only.
- Versioned chain of JSON artifacts (`strokes.json` → `stroke_features.json` →
  `session_findings.json` → per-stroke detail), each re-runnable from the previous one
  without re-running pose.
- M2.5 moves before M3, limited to marking and excluding strokes that may belong to
  another person (abstention and traceability; no identity recognition).
- M2.3 additions: peak-to-impact offset checked on the 35 labels; the peak is a candidate
  event, not proof of impact; observability per body region with explicit reasons; a
  stroke's overall reliability never exceeds that of its measurements.
- Four result levels are always kept apart: personal variation, deviation from the usual
  range, possible technical issue, technical error backed by a validated measurement.
- An issue concentrated in one part of a session is not excluded for that reason; its
  distribution over the session is shown.
- Statistical rules (minimum 8 evaluable strokes per type, frequency intervals, minimum 3
  evidence strokes, distribution over the session, 80 % availability) are provisional
  parameters to tune on videos, not final rules.
**Alternatives:** keep single stroke first (deep analysis without knowing which strokes
matter); a "light" per-stroke session pass (cannot support recurring-issue claims);
unsupervised anomaly detection (on current data it would mostly find tracking errors).
**Consequences:** still proposals to validate: the indicator list, the
`session_findings.json` structure, the issue catalogue, "common" meaning each stroke type
passes separately, extending the AI pack models, a separate file for manual corrections,
no new database tables before M8, on-demand per-stroke detail.

### ADR-021 M2.3 exit criterion (2026-11-20)
**Context:** M2.3 must be judged on 35 labelled impacts of one player (34 forehand/backhand
targets) without fixing accuracy targets or time tolerances before seeing results. The
existing comparison tool matches nearest-first, ignores rejected events and does not know
which part of each clip is labelled (3 of 6 clips only in the first minute).
**Decision (approved by the developer):**
- Two stages: yes/no checks and a complete, reproducible report first; numeric judgements
  decided by the developer after seeing results and recorded as provisional values.
- Five dimensions assessed separately: impact coverage (intercepted, intercepted-uncertain,
  ambiguous, missed with a cause); temporal precision (signed peak-to-impact offset per
  wrist peak, full distribution by type, view, wrist and label visibility; estimated
  frames shown apart); false candidates (judged only inside labelled intervals, categories
  preparation, recovery/finish, movement between strokes, tracking error, unlabelled real
  stroke, not judgeable); regional observability (torso, left wrist, right wrist, legs,
  each with state and reasons, independent of each other); repeatability and traceability
  (identical output on re-run, full decision trace for every event including rejected
  ones, effect of video-relative thresholds, tuning clips recorded, parameters frozen).
- Wrist peaks are evidence; candidate events (possible strokes) are what is counted. Peaks
  of both wrists within a grouping window G are chained into one event; evidence of both
  wrists is kept; the representative wrist may stay "not determined", the event then has a
  time window. G is chosen from data and must satisfy 2·r_max + G < minimum label gap.
- Association by connected components of the event–label graph within radius r: one event
  and one impact = intercepted; several events or impacts = ambiguous, reported and never
  assigned. Radii 0.1 / 0.2 / 0.3 / 0.5 s are each computed from scratch. The minimum gap
  between consecutive labels over the whole sample is 82 frames (2.73 s, img4893), hence
  G < 1.73 s; the check is recomputed on every label set.
- Blind re-annotation of two clips is optional but recommended and measures intra-annotator
  variability, not absolute label error.
- A confirmation video is mandatory before M3.1: new session, behind view, never used for
  tuning, annotated end to end (serves, pauses, movements between strokes, opponent),
  evaluated with frozen parameters.
**Alternatives:** a fixed tolerance (0.25 s) and nearest-neighbour matching (hides
ambiguity, threshold chosen before data); precision/recall targets now (35 impacts cannot
support them).
**Consequences:** before implementation, `strokes.json` (wrist peaks, events, regions,
decision trace) and the evaluation tool (labelled intervals, connected-component matching,
rejected events, independent radii, false-candidate review sheet) must be specified. The
results are a development check on one player, not a validation. M2.3 starts only after
M2.2 and an explicit go.
