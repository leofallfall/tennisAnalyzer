# M2.3 — Detector dataset: split, corrections and checks (intervention I0)

Status (2026-11-23): **data preparation only.** No detector code exists: no peak finding,
grouping, representative wrist or decision. The design approved by the developer on
2026-11-23 ("M2.3 — Progettazione dello Stroke Detector", Claude Doc) splits the work into
interventions I0–I4; this document is I0. Every count below describes one player's clips
(ADR-018) and is not a validation.

## 1. Where the files are

Real clips, poses and labels are personal data and stay out of git (`.gitignore`:
`storage/*`, `tests/fixtures/real/`). They live in the developer's project files, in the
layout below; `pose_dir` in each manifest is relative to the manifest, so the folder can be
copied as a whole (for example under `tests/fixtures/real/`).

```
m2_1/pose_outputs/out_<video>/        pose.json, timemap.json, report.json (13 videos)
m2_1/MANIFEST.md, labels.csv          M2.1 dataset (unchanged)
m2_3_labels/                          img5098_full: MANIFEST.md, labels.csv, annotated.csv
m2_3_labels/labels_2026-11-22_originale.csv   labels before the I0 corrections
m2_3_labels/labels_2026-11-23_prima_Q3.csv    after Q2, before Q3
m2_3_dataset/dev_a/                   MANIFEST.md, labels.csv
m2_3_dataset/dev_b/                   MANIFEST.md, labels.csv, annotated.csv
m2_3_dataset/control/                 MANIFEST.md, labels.csv, annotated.csv
m2_3_dataset/evidenze/                frames used for the corrections (section 4)
```

The split files are made only with the existing formats (`MANIFEST.md`, `labels.csv`,
`annotated.csv`) and checked with the existing `eval_strokes.py check`; no tool was added.
Of the 13 processed videos only 7 have labels; the other 6 (IMG_4895_2min, Timeline_1/2/3/5,
Video_Project_2) are not used for any number.

## 2. Split (approved, decision Q1)

| Group | Clips / frames | Forehand | Backhand | Other | Volley | Estimated frame (`partial` / `none`) | Annotated interval | Min label gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Development A | img4893, img4894, img5098_1, img4912_2min, img4912_m3 (whole pose files) | 17 | 12 | 0 | 0 | 10 / 0 | none (coverage unknown) | 82 frames (img4893 674–756) |
| Development B | img5098_full 0–4499 | 11 | 13 | 4 | 0 | 2 / 7 | 0–4499, `all`, A: exhaustive | 72 frames (4128–4200) |
| Control | img5098_full 4500–9470 | 13 | 13 | 4 | 3 | 4 / 8 | 4500–9470, `all`, A: exhaustive | 66 frames (8952–9018) |
| Excluded | img5098 (22 s cut) | — | — | — | — | — | — | — |

Totals: 79 forehand/backhand targets (29 + 24 + 26), 8 `other`, 3 volleys, 0 serves, 0
`none` labels. `manifest.split` is `dev` for Development A and B, `test` for Control; the
`check` warning about fewer than 5 clips per split is expected and is repeated in every
report.

- **img5098 excluded.** Its pose matches img5098_full frames 5169–5848 (median difference of
  nose and wrists 0.0008 in normalised coordinates at offset 5169; next best offsets 5168 and
  5170 give 0.0020 and 0.0022). Its six labels map to full 5195, 5293, 5387, 5484, 5751,
  5845 (full labels 5196, 5294, 5389, *none*, 5747, 5844). Using it anywhere would leak
  Control frames into development.
- **Cut at frame 4500.** No label between 4282 and 4732 (about 15 s of pause). The detector
  will always run on the whole clip (it reads no labels); the split exists only in the
  evaluation, through the two label / interval pairs. An event within r of 4499/4500 is
  `near_boundary`, as for any interval edge.
- **Not independent.** Development B and Control are two portions of **one session**, the
  same recording img5098_full (see 2.1).
- **Radius check.** Smallest D is 66 frames (2.2 s, Control) after the Q3 correction:
  `2r + W < D` needs W < 1.2 s at r = 0.5 s. Before the correction it was 35 frames
  (W < 0.17 s at r = 0.5 s).

### 2.1 What Control measures, and what it does not

- **Development** (A and B) is the only data used to look at signals, choose parameters and
  thresholds (I1–I3), and fill review sheets.
- **Control** (img5098_full 4500–9470) holds strokes never used in development, evaluated
  once with frozen parameters. It measures how the detector behaves on **unseen events of the
  same session** (same day, venue, light, camera position, clothing, player).
- Control is **not** an independent validation and **not** evidence of generalisation: not to
  a new session, other venues or cameras, other players. Its numbers are reported as
  "Control (same session as Development B)", never as a test-set accuracy.
- The independent check remains the ADR-021 confirmation video (new session, never used for
  tuning, annotated end to end), required before M3.1.

## 3. Results of `eval_strokes.py check` (2026-11-23)

```
dev_a    OK: 5 clips, 29 labels, 0 annotated intervals   (warning: no annotated.csv, coverage unknown)
dev_b    OK: 1 clips, 28 labels, 1 annotated intervals   (0-4499: exhaustive)
control  OK: 1 clips, 33 labels, 1 annotated intervals   (4500-9470: exhaustive)
m2_3_labels (whole img5098_full, corrected)  OK: 1 clips, 61 labels, 1 annotated intervals
```

Pose coverage reported by `check`: img4893 9031/10994 frames, img4894 1974/2427, img5098_1
9511/10893, img4912_2min 2873/3603, img4912_m3 1471/1800, img5098_full 7666/9471.

## 4. Label corrections and checks

### Q2 — feed missing from img5098_full: **added**

Evidence: overlay of img5098 (same source frames, offset 5169), frames 300–322 = full
5469–5491 (`evidenze/q2_img5098_full_5469-5491.jpg`). The player drops the ball (300–306),
lets it bounce and plays it from below with a forehand-side swing; the ball is at the strings
around 314–316 (full 5483–5485). It is the same kind of gesture as the other six
"lancio di palla inizio scambio" labels of img5098_full, labelled `other` by the developer's
choice. Added: `img5098_full,5484,other,A,full,,<note>` (frame = img5098 315 + 5169, the
M2.1 frame; `overlay_wrist` left empty: not checked). The original file is kept as
`labels_2026-11-22_originale.csv`; the corrected file carries a comment line with the date.

Lesson for the dataset: an interval declared exhaustive was not exhaustive for `other`. The
evaluation trusts the declaration (P2); false candidates judged `unlabelled` must therefore
be reviewed, never counted automatically as detector errors.

### Q3 — forehand at img5098_full 5161: **moved to 5109** (confirmed by the developer)

Checked on the overlay of the whole video (the 22 s cut starts at 5169), frame numbers
matched with the `#N` printed on the overlay (`evidenze/q3_1…`, `q3_2…`, `q3_3…`):

- 5060–5106: the player runs in from the left with the racket high and leaves through the
  right edge of the image;
- 5108–5112, at the right edge: the racket swings high and a ball streak leaves towards the
  left (5109–5112): a forehand-side stroke played almost out of frame; contact not visible;
- 5114–5132: out of frame;
- 5137–5169: walks back; **at 5161 the player stands in the ready position, racket low, no
  swing**;
- 5175–5197: the forehand labelled at 5196.

Reading: the label belonged to the stroke at the edge, about 50 frames late; there is no
separate stroke at 5161. The developer agreed (2026-11-23). Changed
`img5098_full,5161,forehand,A,partial` → `img5098_full,5109,forehand,A,partial` with a note
(frame estimated: the contact is not visible), in `m2_3_labels/labels.csv` and
`m2_3_dataset/control/labels.csv`; the file before the change is kept. `check` after the
change: Control OK (33 labels), whole img5098_full OK (61 labels). Control's minimum gap goes
from 35 to 66 frames.

The pose alone had suggested lateral movement at 5161 (no fast wrist motion, right wrist
visibility 0.12–0.45); the video confirmed it and also showed the real stroke, which the
pose could not (the player is out of frame there).

## 5. What the files do not contain

- No `none` labels and no explicit "ambiguous" label type; the visibility column (`full`,
  `partial`, `none`) only says how readable the impact was.
- No annotated intervals for Development A: unassociated events there are never judged
  automatically (`no_intervals`). Optional (Q9 of the design): declare intervals after
  checking that those clips contain no unlabelled volleys or serves (img4912_2min has serves).
- No player or session identifier columns: the clips' session membership was settled with
  the developer (2026-11-23) and needs no change in I0.

## 6. Requirements for a possible later ML classifier (documented, not implemented)

No model, no feature and no contract field is added now. Checked against what exists:

| Requirement | Already covered by | Gap | Proposal (needs approval) |
| --- | --- | --- | --- |
| Tell a real stroke from an irrelevant movement without forcing a class | labels (`stroke_type`, including `none`), review sheet categories (`preparation`, `recovery_finish`, `between_strokes`, `tracking_error`, `unlabelled_stroke`, `not_judgeable`) | no way to say "a gesture, type unclear" in `labels.csv` | allow `stroke_type = unknown` in labels (minor change of the label format and validator) |
| Labels tied to a player and a session | `player` column (A/B) is per clip; manifest notes mention sessions in free text | no id columns | none for now: session membership settled with the developer (2026-11-23); revisit only if an ML dataset is built |
| Detected events vs annotated swings not detected | `strokes_eval.json`: `intercepted*`, `missed` with cause, unassociated events | — | none |
| Rebuild examples later (windows, signals) | `strokes.json`: input sha256, all parameters and `params_sha256`, frames and times of every peak and event, decision trace | signals are not stored, they are recomputed from `pose.json` + parameters | keep `pose.json` / `timemap.json` of every evaluated clip (hashes already recorded) |
| Keep every peak of both wrists with its quality | `strokes.json` `wrist_peaks[]` (grouped and discarded, provenance, `quality_window`) | — | none |
| Splits by session and player without leakage | split column in the manifest | the current Control shares its session with development (2.1) | the confirmation video as a new session |

## 7. Next step

I1 (peak finding) starts only after the developer's explicit approval. Q2 and Q3 are closed.
