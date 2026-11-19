# M2.1 — Real-clip dataset, labels and input evaluation

Status: **tooling implemented, dataset not yet collected** (2026-11-19). M2.1 also
fulfils M1 item 11 (decision D1 of the M2.0 design, approved 2026-11-19).

M2.1 builds no stroke detector and no forehand/backhand classifier. It provides:

1. a labelling method that needs no annotation tool;
2. two hand-written files describing a local dataset of real clips (`MANIFEST.md`,
   `labels.csv`);
3. `scripts/eval_strokes.py`, which checks the dataset, measures the **pose input**
   around labelled impacts, and compares labels with any `strokes.json` (today only
   hand-written ones);
4. the matching rule and reporting conventions later milestones (M2.3, M2.4) are judged by.

Code: `engine/tennis_cv/evaluation/` (pure library, not used by the pipeline or the worker).

## 1. Where the data lives

Real footage and everything derived from it stay on the labelling computer:

| What | Where | In git? |
| --- | --- | --- |
| Original clips | anywhere; upload them through the app | no |
| Pipeline output per clip | `storage/analyses/<analysis_id>/videos/<video_id>/` (`proxy.mp4`, `overlay.mp4`, `pose.json`, `timemap.json`, `report.json`) | no (`storage/*` ignored) |
| `MANIFEST.md`, `labels.csv` | `tests/fixtures/real/` | no (ignored) |
| Reports | wherever `--out`/`--json` point; keep them out of the repository | no |

`uv run python scripts/eval_strokes.py init` creates templates of both files in
`tests/fixtures/real/`; `uv run python scripts/eval_strokes.py list` prints every processed
video folder with its original file name, proxy frame rate and whether a time map exists.

## 2. Labelling protocol

1. Process the clip in the app with pose stride 1 (the default) and overlay on (default).
2. Open `overlay.mp4` (link "overlay" on the analysis page) in a player that steps frame by
   frame (VLC: key `E`; mpv: `.`). Every overlay frame carries `#<frame> <time>s` in the top
   left corner. That number **is** the proxy frame index used by `pose.json` and by the
   engine; no conversion is needed. The analysis page's own player also shows the frame
   number and steps frame by frame, but the burned-in number on the overlay is the reference
   if the two ever disagree (browser seeking).
3. For every impact (racket meets ball) write one row in `labels.csv`. Also add rows for
   movements a detector could mistake for strokes (shadow swings, ball bounces before a
   serve, picking up balls) with `stroke_type=none`.
4. While on the impact frame, look at the drawn **hitting wrist** and fill `overlay_wrist`.
   This takes a second per row and is the only real-world check of the landmark in the
   whole report.

Verified on 2026-11-19 with synthetic videos (30 fps, 60 fps, VFR 10→30 fps): proxy,
overlay and `pose.json` have the same frame count, and overlay frame N prints `#N` with the
same time as the test pattern's own clock (see `tests/integration/test_eval_strokes_pipeline.py`).
Not yet verified on real phone footage.

## 3. `MANIFEST.md` (definitive format)

Free Markdown, plus **one pipe table** whose header is exactly:

```markdown
| clip_id | file | pose_dir | view | dominant_hand | backhand | other_people | occlusion | split | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| clip01 | IMG_0412.MOV | ../../../storage/analyses/9f.../videos/3c... | behind | right | two_handed | no | none | dev | campo 3, sera |
```

| Column | Values | Notes |
| --- | --- | --- |
| `clip_id` | letters, digits, `_`, `-` (≤ 64) | unique; used in `labels.csv` |
| `file` | text | original file name, informational |
| `pose_dir` | path | folder with `pose.json`; relative to `MANIFEST.md` or absolute |
| `view` | `side_forehand`, `side_backhand`, `front`, `behind`, `diagonal`, `unknown` | same vocabulary as the app's camera view |
| `dominant_hand` | `right`, `left`, `unknown` | declared, never inferred |
| `backhand` | `one_handed`, `two_handed`, `unknown` | declared |
| `other_people` | `yes`, `no` | anyone else in frame for a noticeable time (coach, partner, opponent) |
| `occlusion` | `none`, `partial` | net, fence, frame edge hiding the player at times |
| `split` | `dev`, `test`, `unassigned` | assigned per **clip**, never per stroke |
| `notes` | text | light, court, anything unusual |

Frame rates are **not** declared: the tools read the proxy rate from `pose.json` and
CFR/VFR from `report.json`, so the groups cannot disagree with the data.

## 4. `labels.csv` (definitive format)

```csv
clip_id,proxy_frame,stroke_type,player,visibility,overlay_wrist,notes
clip01,812,forehand,A,full,ok,
clip01,905,backhand,A,partial,off,"racchetta coperta dalla rete"
clip01,1010,none,A,full,,swing a vuoto tra i punti
clip02,455,forehand,B,full,,colpo del compagno
```

| Column | Values | Meaning |
| --- | --- | --- |
| `clip_id` | a manifest `clip_id` | |
| `proxy_frame` | integer ≥ 0 | overlay frame of the impact (`#N`); if the impact is hidden, the closest frame where it happens |
| `stroke_type` | `forehand`, `backhand`, `serve`, `volley`, `other`, `none` | `none` = not a stroke (used to explain false positives) |
| `player` | one capital letter | `A` = the player being analysed; `B`, `C`… = anyone else |
| `visibility` | `full`, `partial`, `none` | how well **you** can see the impact |
| `overlay_wrist` | `ok`, `off`, `missing`, `other_person`, or empty | drawn hitting wrist at that frame: on the real wrist / visibly elsewhere / not drawn / drawn on another person / not checked |
| `notes` | text | quote it if it contains commas |

Blank lines and lines starting with `#` are ignored. The same clip, frame and player may
not appear twice. Errors are reported with file and line; nothing is guessed.

## 5. Input visibility report

```bash
uv run python scripts/eval_strokes.py check tests/fixtures/real/MANIFEST.md tests/fixtures/real/labels.csv
uv run python scripts/eval_strokes.py visibility tests/fixtures/real/MANIFEST.md tests/fixtures/real/labels.csv \
    --out storage/m2_1_visibility.md --json storage/m2_1_visibility.json
```

For each impact labelled for player `A` (all stroke types except `none`) the report looks
at the frames within `±window_s` (default **0.3 s**, `--window-s`) and keeps three things
apart:

| Part | What it says | What it does **not** say |
| --- | --- | --- |
| 1. MediaPipe self-report | pose detected at the impact; landmark `visibility` score ≥ threshold (default **0.5**, `--threshold`) at the impact; median score | where the joint really is. The score is the estimator's own likelihood, not an accuracy. |
| 2. Continuity | the above-threshold frames form an unbroken run over the whole window; both shoulders and hips usable at the impact (future body reference); proxy frames repeating a source frame in the window (VFR, from `timemap.json`) | that velocities or accelerations computed there would be right. These are necessary conditions only: a continuous, high-score wrist can sit on the racket or on another person. |
| 3. Human check | `overlay_wrist` counts, and the cross-table of MediaPipe score (above/below threshold) vs the human check for the hitting wrist | anything about other joints or other frames |

Joints: wrist, elbow, shoulder of the hitting side and of the other side (with
`dominant_hand=unknown`, they are reported as left/right). Biomechanical observability
proper (is the quantity measurable from this view and distance?) is **not** assessed in
M2.1; it belongs to the metric milestones.

Every result is printed as `n/of`, for all impacts and separately by `view`, `proxy_fps`,
`source_frame_rate` (cfr/vfr), `other_people`, `split`, `stroke_type` and the human
`visibility` label. Groups with fewer than 3 clips are marked *small sample*.

## 6. Comparing labels with detected events

```bash
uv run python scripts/eval_strokes.py compare MANIFEST LABELS [--results-dir DIR] \
    [--tolerance-s 0.25] [--targets forehand,backhand] [--out REPORT.md] [--json DATA.json]
```

Events are read from `<pose_dir>/strokes.json`, or `<DIR>/<clip_id>.strokes.json`. Only
these fields are used (others are ignored, so the future M2.3 file is compatible):

```json
{"events": [{"id": "s1", "peak_frame": 812, "stroke_type": "forehand", "status": "classified"}]}
```

`stroke_type`: `forehand`, `backhand`, `unknown` (default); `status`: `classified`
(default), `uncertain`, `rejected` (rejected events are not compared).

Matching rule:

1. Candidate pairs: an event and a label of player `A` whose `stroke_type` is a target,
   with `|peak_frame − proxy_frame| / fps ≤ tolerance_s`.
2. One-to-one assignment, smallest time difference first (ties: earlier label, then earlier
   event). Matched event = hit; unmatched target label = miss (reported with its human
   `visibility`).
3. Remaining events are matched the same way against the remaining labels to name the
   cause of the false positive: `non_target_stroke` (a serve, volley or other stroke of A),
   `other_player`, `not_a_stroke` (a `none` label); otherwise `unlabelled`.

**The tolerance (default ±0.25 s) is a starting proposal from the M2.0 design, not a
validated value.** It must be reported with every result, and revisited once the
labelling noise (section 7) and the peak-to-impact offset of the future detector are known.

Reported per group (same groups as section 5): matched/targets, matched/events, false
positives by cause, misses by visibility, signed median and p90 absolute time error,
forehand/backhand confusion on matched events, same-type/decided (abstentions excluded
from the denominator) and abstentions/matched (an `unknown` is never counted as correct).

## 7. Reporting rules

- **Split by clip.** `dev` clips tune thresholds; `test` clips are only for the final
  numbers. With fewer than **5** clips in either split the report states that there is no
  meaningful split and that the numbers describe these clips only (a reporting convention,
  not a statistical guarantee).
- **No accuracy claims in M2.1.** M2.1 measures the input. Detection and classification
  numbers exist only once M2.3/M2.4 produce events, and the ROADMAP target (precision/recall
  ≥ 0.9) is confirmed or revised after looking at M2.1 data (decision D11).
- **Label noise.** Re-label two or three clips on another day without looking at the
  first file, then compare the two `proxy_frame` columns: the spread is the uncertainty of
  the labels themselves. At 30 fps one frame is 33 ms. (No tool for this yet: a
  spreadsheet is enough at this size.)
- **Denominators always.** No bare percentages; small groups flagged.

## 8. Known limits

- `num_poses=1`: when MediaPipe follows another person, part 1 and 2 describe *that*
  person. Only `overlay_wrist=other_person` reveals it.
- Frames skipped by `pose_stride > 1` count as missing in the window (the check command
  warns). Label with stride 1.
- The overlay is optional in the pipeline (`--no-overlay`); labelling needs it.
- Real phone footage has not been run through these tools yet (no clips were available to
  the development environment on 2026-11-19).
