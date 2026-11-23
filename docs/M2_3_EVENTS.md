# M2.3 — Candidate events: the `strokes.json` 1.x format

Status (2026-11-23): **1.1 implemented and written by the experimental I2 detector**
(section 9, ADR-026). Earlier status (2026-11-22): **format, versioned reader, invariant checks and canonical writer
done (PR 1 of the M2.3 preparation, ADR-022). No detector writes this file yet**: the M2.3
detector starts only after M2.2 and an explicit go. The evaluation (connected components
per radius, annotated intervals, miss causes, `strokes_eval.json`) is PR 2, see
docs/M2_3_EVALUATION.md; the reports are PR 3, not started.

Code: `engine/tennis_cv/strokes/artifact.py` (pure library, not used by the pipeline,
worker, API or UI). JSON Schema: `packages/contracts/schema/strokes_v1.schema.json`.
Example (hand-written, invented values, no detector output):
`tests/fixtures/strokes/example_v1_0.json`. Full technical proposal, approved by the
developer on 2026-11-22: "M2.3: strumento di valutazione e schema strokes.json" (Claude Doc).

## 1. Three levels

| Level | Field | Counted? | Content |
| --- | --- | --- | --- |
| Wrist peak | `wrist_peaks[]` | no: evidence | every recorded local maximum of either wrist's apparent 2D speed, `grouped` into an event or `discarded` with reasons; provenance (keypoint, landmark index, velocity / smoothing / scale state from M2.2), quality counts in a window |
| Candidate event | `events[]` | **yes** | a possible stroke: peaks of both wrists chained within the grouping window G |
| Per event | inside `events[]` | — | evidence of each wrist, representative wrist, time, four regions, reliability, reserved M2.4 / M2.5 fields, decision trace |

Nothing in the file comes from labels. The association with annotated impacts is written
by the evaluation tool in a separate `strokes_eval.json` (ADR-022, PR 2), so the hash of
`strokes.json` always identifies one detector run.

## 2. Header

| Field | Meaning |
| --- | --- |
| `schema`, `schema_version` | `"tpa.strokes"`, `"1.0"` |
| `created_at` | the only field ignored by repeatability checks |
| `producer` | algorithm name and version, `status: experimental`, engine version |
| `inputs.pose` | plain file name (no `/`, `\`, `:`, NUL, `.` or `..`: never a path, checked before any file access; the exported JSON Schema `pattern` encodes the same rule, whitespace = exactly Python `str.isspace()` listed as `\uXXXX` ranges, no `\s`; tests run it in Node with and without the `u` flag), sha256 of the bytes, fps, frame count, `frame_stride` |
| `inputs.timemap` | plain file name (same rule) and sha256 (both null when `missing`), `status` as `build_timebase` reports it |
| `inputs.player_config` | reserved for the player configuration of M2.2 #6; null and unused in 1.0 |
| `time_basis` | `source` (consistent map) or `proxy`, same values as `signals.timebase` |
| `params`, `params_sha256` | every parameter used, M2.2 signal parameters included as written; sha256 of the compact key-sorted JSON |
| `rules` | catalogue of every rule and reason code used in the file, trace rules and `decision` included (`code`, `kind`, `description`; kinds `discard_peak`, `reject`, `uncertain`, `representative_wrist`, `check`, `decision`, `info`) |

Rule codes of the detector are defined by M2.3. The schema only requires that each code is
declared in `rules`, so every decision can be read from the file alone.

## 3. Events

- `status`: `accepted`, `uncertain`, `rejected` (detection only; forehand / backhand is
  M2.4 and lives in `classification`, `stroke_type: unknown`, `status: not_attempted`).
- `grouping`: `method: chain`, `g_s` (G), `peak_ids`, `links` between consecutive peaks in
  time with `dt_s`, `span_s`, `possible_merge`. With chaining an event may last longer
  than G.
- `evidence.left` / `evidence.right`: `peaks` with their ids, or `no_peak`.
- `representative_wrist`: `left`, `right` or `undetermined` with reasons. When
  undetermined the event has only a window (`window_proxy_frames`, `window_t_s`) and the
  reference time is null.
- `regions`: `torso`, `left_wrist`, `right_wrist`, `legs`, each `good` /
  `low_but_usable` / `not_observable` with closed reasons (`no_pose`, `below_threshold`,
  `out_of_frame`, `broken_run`, `irregular_interval`) and sample counts. Regions are
  independent.
- `reliability`: `state`, `regions_used`, `limited_by`; never above the worst region used.
- `complementary`: reserved for complementary signals (M2.2 #6), empty until M2.3 decides.
- `player.status: not_checked` (M2.5); `estimated_contact_s: null` until the
  peak-to-impact offset is validated (D4).
- `trace`: numbered steps (`rule`, `subject`, `inputs`, `param` = dotted path into
  `params`, `outcome`), the last one being `decision`.

## 4. The eight invariants

`check_invariants` returns every violation prefixed with its number; `parse_strokes`
raises `StrokesInvariantError` (`strokes_invariant_violated`) when any is broken, and the
writer refuses to write such a document.

1. Every peak belongs to at most one event; `peak.event_id` and `grouping.peak_ids` agree
   both ways; a discarded peak has no event and at least one reason.
2. The event window runs from its first to its last peak (frames and times); `span_s`
   and the per-wrist evidence match the peaks.
3. No double counting: the peaks of an event are chained in time order by links of at
   most G, all events use the same G, and consecutive events are more than G apart (two
   peaks within G can never sit in different events).
4. A determined representative wrist has its reference time on one of its peaks in the
   event; an undetermined one has a null reference and at least one reason.
5. Rejected and uncertain events have at least one reason; every code used in the file
   is declared exactly once in `rules`: event, peak and representative-wrist reasons and
   every `trace[].rule`, `decision` included.
6. `reliability.state` is not above the worst state among `regions_used`; `limited_by` ⊆
   `regions_used`; a `not_observable` region has a reason. A region outside
   `regions_used` takes no part in any check.
7. `time_basis: source` if and only if the map status is `ok` (as `build_timebase`
   decides); a recorded map has its name and hash, a missing one has neither.
8. Trace steps are numbered 1..n and only the last one is `decision`, with the event
   status as outcome.

`params_sha256` is also checked against `params`.

## 5. Versions: backward compatibility only

- `schema_version` = `MAJOR.MINOR`. The reader checks `schema` and `schema_version` on the
  raw JSON before any field, so a version problem is never reported as a field problem.
- A reader for 1.N reads 1.0 … 1.N. A newer minor raises `strokes_schema_too_new`, another
  major or another schema name `strokes_schema_unsupported`.
- Models forbid unknown fields (`extra="forbid"`): nothing is ignored silently. This is
  why compatibility is backward only: an old reader refuses newer files explicitly.
- Minor change: only new optional fields whose default means "not recorded", or new
  values of a closed list. Major change: removed or renamed field, new required field,
  changed meaning or unit.
- Writer and readers live in this repository: a version bump updates in one PR the
  models, the exported JSON Schema, the readers and a test reading a file of the previous
  version. The writer always writes the current version.
- One JSON Schema per major (`strokes_v1`), describing the latest minor.

## 6. Canonical writing and hashes

`dump_strokes` checks the invariants, writes keys sorted, 2-space indentation, rules by
code, peaks by time then id, events by window start then id, times and speeds with 4
decimals (0.1 ms), and a final newline: the same document always gives the same bytes.
`same_output` compares two documents ignoring only `created_at` (repeatability check).
`check_inputs` / `verify_inputs` recompute the sha256 of `pose.json` and `timemap.json`
next to the file and report a stale `strokes.json` (`strokes_inputs_stale`).

```bash
uv run python scripts/eval_strokes.py validate-strokes STROKES [--inputs DIR | --no-inputs]
```

Prints the version, producer, peak and event counts; exits 1 on any schema, invariant or
stale-input problem.

## 7. Not in this format (and where it goes)

- Association with labels, radii, ambiguity, miss causes, the state of each annotated
  frame (`not_sampled_stride`, `repeated_frame`, `no_pose_frame`, with a fixed precedence
  and errors for frames out of domain or an inconsistent `pose.json`): evaluation tool,
  PR 2, `strokes_eval.json` (docs/M2_3_EVALUATION.md).
- TypeScript types in `packages/contracts/src`: M2.6, when the API and UI use the file
  (decision E6). Only the JSON Schema is exported now.
- Peak detection, grouping, region states and decisions: the M2.3 detector (I1 peaks done;
  I2 onwards not started).

## 8. 1.1: `coverage` and peak prominence (ADR-025, implemented with I2)

Events only describe what the detector found. To show *where it could not have found a
stroke*, 1.1 adds one optional top-level field (a minor change, section 5):

```json
"coverage": {
  "min_duration_s": 0.3,
  "not_assessable": [
    {"start_proxy_frame": 1200, "end_proxy_frame": 1290, "start_t_s": 40.0, "end_t_s": 43.0,
     "regions": ["left_wrist", "right_wrist"], "reasons": ["no_pose"]}
  ]
}
```

- *Not assessable* means **insufficient evidence to detect a stroke, not absence of strokes**:
  a stroke may well have happened there. The UI must say so (e.g. "not assessable: wrists not
  visible, strokes here cannot be detected").
- A stretch is *not assessable* when **neither wrist** has a usable M2.2 signal, or the
  **torso scale** is missing (the minimum prominence is in torso lengths/s), for at least
  `min_duration_s` (a parameter, recorded in `params`, not validated).
- `regions` and `reasons` reuse the closed lists of section 3 (`RegionName`,
  `RegionReason`); no new vocabulary.
- `coverage = null` means "not recorded" (every 1.0 file). Stretches are sorted, do not
  overlap and lie inside the video.
- Stretches where only one wrist is missing are not listed: a stroke can still be found on
  the other wrist, and events there carry their own regions. To be revisited only if real
  cases need it.

Also in 1.1 (optional, `null` / absent = not recorded, so 1.0 files round-trip unchanged):

- `wrist_peaks[].prominence`: `value`, `unit` (torso lengths/s, or px/s when the torso
  scale is missing), `category` (`internal` / `run_edge`, ADR-024) and `lower_bound`
  (the I1 walk reached a run edge: the true prominence may be larger).
- `quality_window.non_finite` / `low_visibility`: the I1 split of `unreliable` samples by
  cause (with `missing`, they sum to `unreliable`).
- A 1.0 file must not carry 1.1 fields (invariant check `C:`); coverage stretches must not
  end before they start and must be sorted without overlap.

## 9. The I2 detector (experimental, ADR-026)

Code: `engine/tennis_cv/strokes/detector.py` (pure library). Command line:

```bash
uv run python scripts/eval_strokes.py detect POSE_DIR            # -> POSE_DIR/strokes.json
uv run python scripts/eval_strokes.py detect MANIFEST.md --results-dir DIR \
    [--last-proxy-frame 4499]                                     # -> DIR/<clip_id>.strokes.json
```

Output = **candidate events, not validated strokes**. `producer.status = experimental`,
`algorithm = tpa.stroke_detector`, `algorithm_version = 0.1.0-i2`.

| step | rule (code in `rules`) | parameter (in `params`) |
| --- | --- | --- |
| Peaks | I1 peaks of both wrists, unchanged (docs/M2_3_PEAKS.md) | M2.2 signal params |
| Discard | `peak_no_torso_scale`; `peak_below_min_prominence`: prominence < minimum, internal and run-edge alike (category stays visible) | `detector.min_prominence_torso_per_s` = **2.0, initial provisional** |
| Grouping | `grouping_chain`: kept peaks of both wrists in time order; a new event when the gap to the previous peak is more than G | `grouping.g_s` = **0.3 s, provisional** |
| Representative wrist | `representative_wrist`: the most prominent *internal* peak (ties: speed, earlier, left); `rep_only_run_edge_peaks`: only run-edge peaks → `undetermined`, no reference time | — |
| Regions | torso (shoulders + hips), each wrist, legs (knees + ankles): worst keypoint per sample, over the event window ± the M2.2 fit half-window (0.1 s) | — |
| Uncertain (observability only) | `uncertain_wrist_undetermined`; `uncertain_possible_merge` (span > max single span, never split); `uncertain_no_mostly_good_peak` (no peak with GOOD > half of its window); `uncertain_irregular_interval` (in the representative peak window); `uncertain_region_not_observable` (a region used) | `grouping.max_single_span_s` = **1.0 s, provisional** |
| Decision | `decision`: `accepted` unless an uncertain rule failed. **No `rejected` in I2**: height thresholds come with the three-state rules (V1 step 2) | — |
| Info | `info_time_basis_proxy`: no usable time map, times are proxy times | — |
| Coverage | samples where neither wrist has a speed value or the torso scale is missing, in stretches ≥ `min_duration_s`; regions = wrists and/or torso; reasons `no_pose`, `below_threshold`, `broken_run` (usable samples in a run too short for the fit) | `coverage.min_duration_s` = 0.3 s |

- Region states: `not_observable` when no sample is GOOD or LOW; `good` when every sample is
  GOOD and no interval is irregular; otherwise `low_but_usable`. Sample counts: `missing`
  is a subset of `unreliable`. Reliability = worst region among torso and the wrists that
  have peaks; regions not used (e.g. legs) never change the decision.
- Times written in the file are rounded once to 4 decimals before grouping, so the
  in-memory document and the written one satisfy the same invariants.
- `--last-proxy-frame` (recorded as `params.input.last_proxy_frame`): pose.json and
  timemap.json are cut at that frame **before any computation** (`truncate_inputs`), as
  if the video ended there. Every step that uses samples after the one it computes
  (centred torso-scale median ±0.5 s, M2.2 fit ±3 samples and its VALID/EDGE run state,
  peak detection and prominence walks to the run edge, quality, fit and base windows,
  region windows, coverage runs) therefore stops at the limit: the result does not depend
  on later frames (test `test_last_proxy_frame_result_ignores_later_frames`). Frame
  numbers, times, input hashes and `inputs.pose.frames` stay those of the original files.
  Development B uses 4499, so nothing of the Control part of img5098_full is read.
- G was chosen on Development A/B only (ADR-026): gaps between consecutive kept peaks within
  ±0.5 s of a target label (same gesture) p50 0.17 s, p90 0.27 s, p95 0.33 s, max 0.57 s;
  gap between the peaks of consecutive labels min 0.57 s, p5 0.62 s, p10 0.94 s. Both
  distributions are contaminated (peaks near a label are not all of the stroke) and they
  touch at 0.57 s: G separates most, not all, cases.
