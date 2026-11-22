# M2.3 — Candidate events: the `strokes.json` 1.0 format

Status (2026-11-22): **format, versioned reader, invariant checks and canonical writer
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
- Peak detection, grouping, region states and decisions: the M2.3 detector (not started).
