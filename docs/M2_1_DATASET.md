# M2.1 — Real-clip dataset, labels and input evaluation

Status: **done for the personal prototype** (2026-11-20): tooling implemented and run on a
first real dataset of **one player** (section 9). M2.1 also fulfils M1 item 11 (decision D1
of the M2.0 design, approved 2026-11-19), with the single-player limit of ADR-018.

M2.1 builds no stroke detector and no forehand/backhand classifier. It provides:

1. a labelling method that needs no annotation tool (a local labelling page was added on
   2026-11-22, section 4b);
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
Verified on real iPhone footage on 2026-11-19/20 (section 9): overlay `#N`, proxy and
`pose.json` frame N agree.

In practice (2026-11-19/20) the player gave approximate impact times while watching the
original (`seconds.frames`, e.g. `19.07` = 19 s + 7 frames) and the impact frame was then
located on the proxy and confirmed by the player. The given times were usually 0–2 frames
before the impact, twice 6 and 14 frames (still in the backswing): always locate the frame
on the video.

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

### 4a. `annotated.csv`: annotated intervals (added for M2.3)

Says **which part** of each clip was labelled and **what** was labelled there exhaustively.
It sits next to `labels.csv`; `check` reads it automatically from there, or from
`--annotated PATH`. Code: `engine/tennis_cv/evaluation/annotated.py`.

```csv
clip_id,start_frame,end_frame,labelled_types,players,notes
img4893,0,1799,forehand;backhand,A,primo minuto: solo dritti e rovesci di A
conf01,0,5399,all,all_visible,video di conferma annotato per intero
```

| Column | Values | Meaning |
| --- | --- | --- |
| `clip_id` | a manifest `clip_id` | |
| `start_frame`, `end_frame` | integers ≥ 0, `start ≤ end` | proxy frames (`#N`), **both included** |
| `labelled_types` | `;`-separated subset of `forehand`, `backhand`, `serve`, `volley`, `other`, or `all` | types labelled exhaustively in the interval. `none` is never a coverage type: non-stroke movements cannot all be labelled |
| `players` | `all_visible`, or `;`-separated capital letters (`A`, `A;B`) | players labelled exhaustively; `all_visible` = every person visible in the frame |
| `notes` | text | |

Rules checked by `eval_strokes.py check` (errors with file and line):

* several rows per clip are allowed, but intervals of the same clip may not overlap;
* unknown clip, `end_frame` past the last proxy frame, `end_frame < start_frame` → error;
* when a clip has intervals, a label outside all of them → error; a clip without intervals
  → warning (coverage unknown);
* a label inside an interval that does not declare its type or player → warning (allowed,
  but it does not make the interval exhaustive).

**Exhaustive interval** (rule P2 of the M2.3 evaluation proposal, revised on 2026-11-22,
function `coverage()` in `annotated.py`): `labelled_types` covers all five stroke types
**and** the analysed player is covered (`players` lists `A`, or `all_visible`). Other
players are not required: the pose estimator tracks a single skeleton, so another
player's stroke can produce a detector event only if the skeleton jumps onto that person,
and that is a tracking error (`overlay_wrist = other_person`, or a jump in the pose), not
an unlabelled stroke. The first version required `all_visible` whenever the manifest said
`other_people = yes`; Leonardo replaced it after the first real labelling session. Only
exhaustive intervals let M2.3 judge unmatched detector events automatically; everything
else goes to manual review. `check` prints, per interval, `exhaustive` or what is missing.
With the 2026-11-19/20 labels (forehands and backhands of A only) no interval can be
exhaustive.

### 4b. Labelling page (`scripts/labeler/labeler.html`)

A single HTML file with no dependencies and no network access: open it with a double click
in Chrome or Edge. It replaces reading `#N` in VLC and typing rows by hand.

1. Choose the clip's `overlay.mp4` (or `proxy.mp4`), write the manifest `clip_id` (the page
   asks to confirm it at every new video) and the proxy fps (from `report.json` /
   `eval_strokes.py list`).
   On the overlay, the burned-in `#N` must equal the frame shown by the page; if it does
   not, the fps is wrong.
2. Step with `←`/`→` (`Shift` = 10 frames). On the impact frame press `D` forehand, `R`
   backhand, `S` serve, `V` volley, `O` other, `N` none. `G` switches player A → B → C;
   `1`/`2`/`3` set the visibility of the next labels; `Canc` deletes the labels on the
   current frame. `overlay_wrist` and notes are edited in the table.
3. Choose the coverage (types, players), press `I` on the first frame of the interval and
   `E` on the last one.
4. Download `labels.csv` and `annotated.csv` and run `eval_strokes.py check`.

Work is saved in the browser (`localStorage`) after every change and survives a reload;
existing CSV files can be imported to continue them. Frames use the same rule as the web
player (seek to the middle of the frame, read with `floor`); the last frame is clamped
because Chromium reports `currentTime = duration` there. Verified on 2026-11-22 in headless
Chromium on synthetic H.264 videos with the frame number encoded in the image (30 and
29.97 fps, 300 frames): 103 steps and jumps each, the decoded frame always equalled the
page's frame. The page's own checks mirror the Python ones but are only a help;
`eval_strokes.py check` is the reference. Browsers may not play HEVC originals: use the
proxy or overlay (H.264).

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
these fields are used (others are ignored). **This minimal format is not `strokes.json` 1.0**
(docs/M2_3_EVENTS.md, ADR-022): 1.0 files have no `peak_frame`; the `evaluate` command
planned for M2.3 reads both formats, `compare` stays as it is:

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
- **Single player** (ADR-018): the first dataset contains only the developer's own strokes,
  in one indoor venue, at 30 fps. Its results describe that player and those recording
  conditions; generalisation to other players is not shown.

## 9. First real dataset (2026-11-19/20) — single player

Personal-first priority (ADR-018): this dataset is enough to start the personal prototype
(M2.2) and is **not** a general validation of the system.

| Property | Value |
| --- | --- |
| Player | one (A), right-handed, two-handed backhand |
| Venue / light | one indoor dome, artificial light |
| Recording | iPhone originals, 720×1280 portrait, 30 fps (29.97–30) |
| Sessions | 3 (two from behind, one match from the forehand side) |
| Clips (`clip_id`) | 6: 4 `behind` (28 impacts), 2 `side_forehand` cuts of one match (7 impacts) |
| Impacts | 35: 19 forehand, 15 backhand, 1 `other` (feed to the partner in a warm-up) |
| Split | all `unassigned`: fewer than 5 clips per split, so no dev/test numbers |

Input visibility (window ±0.3 s, counting threshold 0.5; `n/of` over these clips only):

| | Forehand (19) | Backhand (15) |
| --- | --- | --- |
| Right (hitting) wrist score ≥ 0.5 at impact | 19/19 | 3/15 |
| Right wrist unbroken over the window | 16/19 | 0/15 |
| Left wrist score ≥ 0.5 at impact | 9/19 | 12/15 |
| Left wrist unbroken over the window | 3/19 | 9/15 |
| Shoulders and hips ≥ 0.5 at impact | 19/19 | 15/15 |

Human check of the drawn right wrist (`overlay_wrist`, 30 of 35 checked): score ≥ 0.5 →
18 on the hand, 0 elsewhere; score < 0.5 → 1 on the hand, 8 elsewhere, 3 not drawn. All 11
wrong or missing cases are backhands.

What this means for later milestones (observations on this player, not validated rules):

- Two-handed backhand seen from behind: the right hand is hidden behind the body; the
  **left** wrist is the usable reference. The player judged it "fairly precise" on the
  overlays, but it was not checked impact by impact.
- Side view: only the stroke the player opens towards is observable. From the forehand side
  both arms are hidden on backhands (3/3 not drawn): arm metrics there are non-observable.
- Shoulders and hips were usable at every impact.
- **Timing (corrected 2026-11-20).** The iPhone originals have container timestamps on a
  1/600 s timescale that alternate between 31.67 and 35.00 ms (±1.67 ms around 33.33 ms):
  the source has no gaps. `timemap.json` reports about 270–320 repeated and as many skipped
  proxy frames in IMG_4893, IMG_5098 and IMG_5098_1, but **these are errors of the
  reconstruction, not of the proxy**. Comparing proxy frames with the original frames
  (IMG_4893, proxy frames 8950–10050) the proxy advances one source frame at a time except at
  about 9 places, while the map points one source frame too late (≤ 33 ms) on 317 of those
  1100 frames. Recomputing the same rule with exact integer timestamps matches 1095/1100
  frames and gives 4 repeats in the whole clip instead of 319. Cause: `build_timemap` rounds
  the 6-decimal `pts_time` as a float, and on this timescale many frames sit exactly half a
  proxy frame from a slot boundary, where float error picks the wrong side. Until the map is
  computed exactly, the `repeated_proxy_frames` counts of these clips (1 of 35 windows) and
  their `duplicated_frames`/`dropped_frames` summaries are not reliable. An earlier version
  of this section, and a comparison of poses on "repeated" frames built on it, were wrong.

Other problems found: the overlay frame number is small and low-contrast on bright
backgrounds (pipeline change, not in M2.1); edited exports (DaVinci, 24 fps, pillarboxed)
had pose in only 59–64 % of frames from the side, so originals are preferred; files over
400 MB had to be cut before transfer (`ffmpeg -ss … -t …`).
