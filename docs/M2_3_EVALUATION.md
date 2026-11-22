# M2.3 — Evaluation: `evaluate` and `strokes_eval.json` 1.0

Status (2026-11-22): **association, miss causes, verdicts of unassociated events, time
rules and `strokes_eval.json` 1.0 done (PR 2 of the M2.3 preparation)**. No detector exists
yet: the command is tested on synthetic files only and on real data it has only been run
to check that it works (no number from it is a result). Reports by type / view / label
quality, the per-wrist offset distribution (nearest-rank, P7) and the false-candidate
review sheet are PR 3, not started.

Code: `engine/tennis_cv/evaluation/` — `association.py` (graph, pure), `clip_time.py`
(time of a frame, frame states, windows), `evaluate.py` (rules, dataset), `eval_artifact.py`
(models of `strokes_eval.json`). JSON Schema:
`packages/contracts/schema/strokes_eval_v1.schema.json`. Rules come from the approved
proposal "M2.3: strumento di valutazione e schema strokes.json" (ADR-021, ADR-022, P1–P7,
P3 bis, P2 revised on 2026-11-22). `compare` (M2.1) is unchanged.

```bash
uv run python scripts/eval_strokes.py evaluate MANIFEST LABELS --out strokes_eval.json \
    [--annotated annotated.csv] [--results-dir DIR] [--radii 0.1,0.2,0.3,0.5] \
    [--targets forehand,backhand] [--outside-search-s 2.0] [--no-input-hashes]
```

Strokes files: `DIR/<clip_id>.strokes.json` or `strokes.json` in each `pose_dir` (as
`compare`). `annotated.csv` next to the labels is used when present. Clips without a
strokes file are listed in `clips_without_results`, never dropped silently. The console
prints counts per clip and radius as `n/total`; the data is in the JSON.

## 1. Inputs and checks (errors stop the run, with a stable code)

| Check | Error code |
| --- | --- |
| Label with an unknown clip; label outside every annotated interval of its clip; interval past the end of the clip | `dataset_format_invalid` |
| `pose.json` incomplete or inconsistent (fps, stride, indices, a missing sampled frame, `timestamp_s`), checked once per clip before any label | `pose_integrity_error` |
| sha256 of `pose.json` / `timemap.json` differs from `strokes.json` (skipped with `--no-input-hashes`, which is recorded as a warning) | `strokes_inputs_stale` |
| `params.signals.timebase` or the visibility thresholds in `params.signals.smoothing` not recorded | `strokes_params_invalid` |
| `time_basis` or map status in the file differ from `build_timebase` run with the file's parameters | `strokes_time_basis_mismatch` |
| A wrist peak not on a time-base sample | `strokes_peak_not_a_sample` |
| Seconds written in the file differ from `t(f)` by more than the 4-decimal rounding | `strokes_time_mismatch` |
| Label (or minimal-format event) out of the clip's domain | `label_frame_out_of_domain` |

## 2. Time (P3, P3 bis)

`t(f)` = time of the source image shown in proxy frame `f` (consistent `timemap.json`), or
`f / fps` (basis `proxy`, `unverified_time`). Labels, peaks and events are placed from their
frames, never from written seconds; nothing is interpolated. Each label gets exactly one
`frame_state`, first that applies: `not_sampled_stride`, `repeated_frame` (with the kept
frame and whether it has a pose), `no_pose_frame`, `sample`. Only `sample` has
`kinematic_sample: true`; the others are attributes, not errors and not miss causes.
Domain: with a map `0 <= f < proxy_frame_count`; without one `0 <= f <= last pose frame`.

## 3. Association per radius (ADR-021, P1)

Each radius is computed from scratch. Target graph: accepted and uncertain events vs
target impacts (player A, `--targets` types). An event with a representative wrist is an
instant; an undetermined one is its window (distance 0 inside, else to the nearer edge).
Edge if `distance <= r + 1e-9 s`. Connected components (union-find), ordered by the earliest
member time, ids `t001`… / `o001`…: they never depend on input order.

| Component | Outcome |
| --- | --- |
| 1 impact + 1 event | `intercepted` (`intercepted_uncertain` if the event is uncertain), with signed offsets of every peak (s and proxy frames) and of the event instant |
| 1 impact | `missed`, with a cause (section 4) |
| 1 event | unassociated event (section 5) |
| several events or impacts | `ambiguous` with `several_events` / `several_labels`; nothing assigned |

Every edge is saved (`target_edges`, `other_edges`); groups and outcomes can be rebuilt
from them (tested). `transitions` lists every impact whose outcome changes between
consecutive radii; nothing is corrected. Each impact also has
`irregular_intervals_in_window` (long / short / invalid intervals overlapping `t ± r`).

## 4. Miss causes (fixed precedence)

1. `no_pose`: no time-base sample with a detected pose within `t ± r`.
2. `both_wrists_unreliable`: no GOOD or LOW sample of either wrist within `t ± r`
   (`classify_quality` with the thresholds recorded in the file).
3. `event_rejected`: a rejected event within r (ids, distances, reason codes).
4. `peak_discarded`: a discarded peak within r (ids, signed offsets, reasons).
5. `event_outside_radius`: nearest **free** event (accepted or uncertain, alone in its target
   component) within the search limit (2 s, a diagnosis limit, not a threshold). The same
   free event nearest to several misses is cited for all of them (`shared_with`), assigned
   to none. Intercepted or ambiguous events are never used.
6. `no_peak`.

Lists for 3–5 are recorded even when an earlier cause wins.

## 5. Unassociated events (P2 revised)

Second graph, same rules: unassociated events vs every non-target label (other types of A,
`none`, every label of another player). Then, per event:

| Position (interval times from `t(start)`, `t(end)`) | Verdict | Cause |
| --- | --- | --- |
| `inside` one interval, more than r from both edges, interval exhaustive | `judged` | `non_target_stroke`, `not_a_stroke` (`none`), `other_player` (possible tracking error), `unlabelled`, or `ambiguous` |
| `inside`, interval not exhaustive | `not_judgeable_coverage` | none; `missing_types`, `missing_players` |
| `near_boundary`, `outside_annotated`, `no_intervals` | `not_judged_position` | none |

Exhaustive = all five stroke types and player A (or `all_visible`), whatever the other
people (`coverage()` of `annotated.py`). Every unassociated event is listed with the nearest
target impact (signed), its component in both graphs and its interval line.

## 6. Radius check (P4)

D = minimum gap between consecutive labels of the clip (all labels, any type or player),
with the two lines; W = actual maximum span `b - a` of graph events, with the event id.
Per radius: `ok` if `2r + W < D`, else `not_interpretable` (results kept and marked);
fewer than two labels: `not_applicable` (`fewer_than_two_labels`).

## 7. Choices made where the proposal was silent (to review)

- **Minimal M2.1 format.** `classified` → `accepted`; each event is an instant at
  `peak_frame`; no peaks, so `peak_discarded` never applies; no parameters are recorded, so
  the engine defaults of `TimeBaseParams` / `SmoothingParams` are used and marked
  `params_used.source: defaults`. Its frames must be in the domain but need not be samples.
- **Parameters of a 1.x file.** `params.signals.timebase` must hold exactly the
  `TimeBaseParams` fields and `params.signals.smoothing` the two visibility thresholds;
  otherwise `strokes_params_invalid` (never a silent default).
- **Position of a window event.** "Inside" needs the whole event (instant or window) in one
  interval with more than r to both edges; an event touching two intervals is
  `near_boundary`.
- **Another player's label** in the second graph gives `other_player`, read as a possible
  tracking error (P2 revised), not as an unlabelled stroke.
- **One output for the whole dataset**, with one block per clip; `created_at` is the only
  field ignored by `same_eval`. Inputs are referenced by file name and sha256 (manifest,
  labels, annotated, each strokes file).
- **Real data.** Run once on `img5098_full` with an empty minimal file only to check that
  the command reads the real inputs and writes a valid file.
