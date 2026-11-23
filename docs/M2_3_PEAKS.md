# M2.3 — Wrist speed peaks (intervention I1)

Status (2026-11-23): **peak finding implemented and tested on synthetic signals; descriptive
analysis on Development A and B only.** The developer chose 2.0 torso lengths/s as the
**candidate** minimum prominence for I2 (section 6), frozen for the single Control run; it is
not applied in I1 code and is not a validated threshold. No grouping, no event, no `strokes.json` output, no CLI, no pipeline
change. Sections 1–6 were written before Control (img5098_full 4500–9470) was read; Control
was then run **once** on 2026-11-23 with the frozen code and value (section 7) and changed
nothing. Every figure below describes one player's clips (ADR-018) and is **not** a validation.

Code: `engine/tennis_cv/strokes/peaks.py`. Tests: `tests/unit/test_strokes_peaks.py`.

## 1. Signal and runs

- **Signal searched**: `speed_2d_px_per_s` of `left_wrist` and `right_wrist`, i.e. the M2.2
  velocity (first derivative of the local quadratic fit over 0.2 s, 7 samples at 30 fps),
  each wrist independently. The player's configuration is not used to find peaks.
- **Values in torso lengths/s**: the pixel value divided by the stabilised torso length *at
  the peak sample*; at the peak this equals `speed_2d_torso_per_s`. The prominence in torso
  units uses the same factor. Searching on pixels keeps the step changes of the 1 s median
  torso length from creating or moving maxima. A peak without a torso scale is **kept**, with
  `None` torso values (Development A/B: 28 of 4334 peaks).
- **Runs**: the M2.2 runs, i.e. consecutive usable samples (GOOD ≥ 0.5 or LOW 0.4–0.5) of one
  time-base segment. No search crosses a hole, an unreliable sample or a long / short /
  invalid interval. A run shorter than the fit window (7 samples) has no velocity, gives no
  peak and is counted (`runs_too_short`).

## 2. Local maximum

Inside a run:

1. A **plateau** is a maximal stretch of consecutive samples with exactly equal speed (almost
   always a single sample).
2. A plateau is a **local maximum** when every neighbour that exists inside the run is
   **strictly** lower. A shoulder (equal then higher) is not a maximum.
3. The **peak sample** is the middle of the plateau; with an even length, the left one of the
   two middle samples.
4. **Run edges**: a plateau touching the first or last sample of its run has no neighbour on
   that side; it is a maximum when the neighbour on the other side is strictly lower, and is
   marked `run_edge` (`run_start` / `run_end`). A plateau covering the whole run is not a
   maximum.
5. **Close peaks**: never merged and never separated by a minimum distance. Two maxima a
   few samples apart are both reported; the dip between them lowers the prominence of the
   smaller one.

There is no height filter. On a still wrist, rounding alone produces tiny maxima (below
10⁻⁶ px/s in the test); they are reported, and only their prominence (≈ 0) tells them apart (tested).

## 3. Prominence

For a peak of height *h* occupying the plateau *a…b*:

1. Walk left from *a − 1* until the first sample **strictly higher** than *h* or the run
   start; the **left base** is the lowest sample walked (ties: the one closest to the
   peak). The same to the right from *b + 1*.
2. **Prominence = *h* − max(left base, right base)** over the sides that have samples.
3. **Censored side**: the walk reached the run edge without a higher sample. The prominence
   is then a **lower bound** of what an uncut signal would give (more samples can only lower
   a base). The highest peak of every run is censored on both sides by construction.
4. **Run-edge peak**: one side has no samples; the prominence is **one-sided**
   (`ProminenceSides.ONE_SIDED`) and not comparable with a two-sided one (it can be larger
   than the true value). Its fit window is also shifted (`velocity_state = edge`), which
   M2.2 measured as much noisier.

Equal-height samples do not stop the walk (only strictly higher ones do).

**Two categories** (`CandidatePeak.category`, developer's decision 2026-11-23): `internal`
(two-sided prominence) and `run_edge` (one-sided, censored at the run edge). Run-edge peaks
are never dropped; because the two prominences are not equivalent, every count is reported
per category and I2 must handle them separately.

## 4. Quality counts (windows from M2.2)

| Window | Samples | Counts | Purpose |
| --- | --- | --- | --- |
| `fit_window` | the exact 7 samples of the M2.2 fit that produced the peak speed (start `min(max(i − 3, run start), run end − 7)`) | GOOD, LOW (always inside the run) | what the speed value is made of |
| `quality_window` | time-base samples with `|t − t_peak| ≤ half_window_s`, `half_window_s = (7 // 2) × nominal interval` = 0.1 s at 30 fps (the M2.2 fit half-window) | GOOD, LOW, UNRELIABLE exactly as M2.2 `classify_quality` (they add up to the samples in the window); UNRELIABLE split by cause: `missing` (no pose, or keypoint absent), `non_finite` (keypoint present, x or y not finite), `low_visibility` (finite coordinates, visibility < 0.4, missing or not finite); the three causes add up to UNRELIABLE; irregular intervals overlapping the window | what surrounds the peak; may cross the run edge; mapping to `strokes.json` `quality_window` decided in I2 |
| `base_span` | from the left base to the right base | GOOD, LOW | what the prominence was measured on |

For a centred peak (`velocity_state = valid`) at regular 30 fps, the first two windows are the
same 7 samples. The quality window is a window of **real time**: with the iPhone alternation
of 31.67 / 35.00 ms the third sample on one side can be 101.67 ms away and fall outside, so the
window holds 6 or 7 samples depending on the peak sample (tested); the fit window is always 7
samples.

Correction (2026-11-23, developer's review): the first version counted a sample with
non-finite coordinates as MISSING and outside UNRELIABLE, unlike M2.2. Now GOOD / LOW /
UNRELIABLE are M2.2's own classes and the causes are sub-counts. On Development A/B this
changes only the UNRELIABLE count of the quality window (it now includes the `missing`
samples, 132 peaks); no peak, prominence, flag or other count changes. No sample with
present but non-finite coordinates occurs there.

## 5. Descriptive analysis on Development A and B (2026-11-23)

Script and full tables are kept with the data in the developer's project files
(`m2_3_peaks/analisi_i1.py`, `report_i1.py`, `i1_tabelle.md`, `i1_targets.csv`,
`i1_peaks.csv`), not in git (personal data). The detector code runs on the whole
img5098_full pose file; every peak, label and time after frame 4499 is dropped before any
statistic.

Definitions used only for this description (not parameters of the detector):

- **Target**: a forehand or backhand label (Development A 29, Development B 24).
- **Expected wrist**: `PlayerConfig(dominant_hand, backhand).reference_side(stroke)` from
  each clip's manifest (all clips: right-handed, two-handed backhand → right wrist for
  forehands, left wrist for backhands). No laterality is assumed in the code.
- **Territory** of a label: half way to the previous and next label of any type. At the
  open end of Development B it runs to the annotated interval edge (0 or 4499); in
  Development A (no annotated intervals) the closed side's half-gap is mirrored.
- **Offset** = peak time − label time (s, on the time base) and peak frame − label frame.
  No success radius is applied; a target with no kept peak in its territory is reported as
  such.
- **Near / pause** (Development B only): a peak is *near* when within 1.5 s of any label
  (the design's descriptive split), *pause* otherwise. Near time 1.37 min, pause time
  1.13 min.

### 5.1 Peaks per minute without any minimum

About 96–146 peaks per minute per wrist (Development A 106–146, Development B 96). Run-edge
peaks are 24 % of Development A peaks and 31 % of Development B peaks. Runs shorter than the
window are frequent (for example img4893 right wrist: 129 of 226 runs): the wrists drop
below the usable visibility often, mostly during fast motion.

### 5.2 Prominence (torso lengths/s)

| Peaks | n | P10 | P25 | P50 | P75 | P90 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dev A, two-sided | 2912 | 0.04 | 0.17 | 0.77 | 2.12 | 3.94 | 15.38 |
| Dev A, run-edge | 912 | 0.20 | 0.63 | 1.75 | 3.99 | 6.92 | 28.88 |
| Dev B near, two-sided | 232 | 0.04 | 0.18 | 0.89 | 2.54 | 5.13 | 9.60 |
| Dev B near, run-edge | 104 | 0.20 | 0.72 | 2.32 | 4.22 | 6.64 | 11.50 |
| Dev B pause, two-sided | 102 | 0.02 | 0.06 | 0.40 | 1.07 | 2.13 | 4.29 |
| Dev B pause, run-edge | 44 | 0.08 | 0.22 | 0.96 | 3.45 | 5.96 | 11.04 |
| Targets: most prominent expected-wrist peak in the territory | 53 | 4.02 | 5.82 | 7.29 | 9.09 | 10.15 | 16.09 |

- Two-sided peaks in the Development B pauses never exceed 4.29; run-edge peaks in the same
  pauses reach 11.04. Pauses contain real movements (walking, bouncing or picking up balls),
  so the pause distribution is not pure noise.
- The lowest "most prominent expected-wrist peak" among the 53 targets is 2.04
  (img5098_full 3777, backhand, visibility `none`); the next are 2.55 (2876, `none`) and 2.60
  (img4912_m3 1619, `partial`).
- For 19 of the 53 targets the most prominent expected-wrist peak is a run-edge peak, often
  0.4–1.0 s after the label (the wrist leaves the usable range during the follow-through).

### 5.3 Effect of a minimum prominence

Two ways to apply a minimum are shown, because run-edge prominences are one-sided:
**rule A** applies it to every peak; **rule B** applies it to two-sided peaks only and sets
run-edge peaks aside.

| Min | Rule | Dev A peaks kept (of 3852) | Dev B near kept (of 336) | Dev B pause kept (of 146) | Pause peaks/min | Targets with no expected-wrist peak in the territory | Nearest-peak offset of the others: P10 / P50 / P90 (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | A | 3824 | 336 | 146 | 129.0 | 0 | −0.13 / +0.03 / +0.10 |
| 1 | A | 1857 | 181 | 50 | 44.2 | 0 | −0.13 / +0.03 / +0.13 |
| 2 | A | 1189 | 129 | 28 | 24.7 | 0 | −0.22 / +0.03 / +0.13 |
| 2.5 | A | 952 | 104 | 21 | 18.6 | 1 | −0.17 / +0.03 / +0.13 |
| 3 | A | 769 | 83 | 17 | 15.0 | 3 | −0.17 / +0.03 / +0.13 |
| 4 | A | 512 | 58 | 11 | 9.7 | 6 | −0.29 / +0.03 / +0.17 |
| 2 | B | 765 | 71 | 12 | 10.6 | 4 | −0.13 / +0.03 / +0.15 |
| 3 | B | 461 | 43 | 5 | 4.4 | 6 | −0.15 / +0.03 / +0.13 |
| 4.5 | B | 236 | 27 | 0 | 0.0 | 11 | −0.17 / +0.07 / +0.16 |

(Dev A counts at minimum 0 exclude the 28 peaks without a scale.) Targets losing every
expected-wrist peak:

- Rule A, 2.5: img5098_full 3777 (`none`). Rule A, 3: also img5098_full 2876 (`none`),
  img4912_m3 1619 (`partial`). Rule A, 4: also img4894 738, img5098_full 206 (`partial`),
  img5098_full 3054 (`none`).
- Rule B, 2: img5098_full 3191, 3683, 3777, 3958 (all `none`: their only clear peak is a
  run-edge one).

With a minimum of 2 (rule A), 9 targets change their nearest peak: 7 move to another
peak within 0.2 s (a flanking low maximum is removed and the main one stays). Two move
further: img5098_full 2876 (0.00 → −0.57 s) and 3777 (−0.43 → −1.23 s), both with
visibility `none`.

### 5.4 Offsets

With no minimum, every target has an expected-wrist peak in its territory, and the nearest
one is within 0.07 s (2 frames) in half of the targets (|offset| P90 0.26 s). The sign is
mostly positive: the speed peak comes about one frame after the labelled impact (median
+0.03 s). That nearest peak is not always the swing peak. At low minimums it can be a
flanking maximum of the same swing, so offsets are reported for each minimum, not as one
number. The other wrist also peaks close to the impact (|offset| P50 0.07–0.10 s under
rule A), as expected for a two-handed backhand and arm coordination. It does not identify
the stroke side.

### 5.5 Why 19 of 53 targets have their most prominent peak at a run edge

For each of the 19, the run-edge peak, the samples just after (or before) its run and the
nearest internal peak at the candidate minimum were listed (Development A/B only). Three
situations, read from the pose data (not checked on video):

| Situation | Targets | What the pose shows | Internal peak near the impact (min 2.0) |
| --- | --- | --- | --- |
| Visibility fades out of the usable range after (or before) the swing | 10: img4893 756, 853, 945, 1419, 1661; img5098_1 37; img5098_full 764, 1273, 3390, 3584 | the wrist is still detected, its visibility slides just below 0.4 (typically 0.39 → 0.35 → 0.32) while it is still moving fast; the run ends there, 0.4–1.0 s after the label (8 cases, follow-through) or 0.7 s before it (2 cases, preparation) | yes for all 10, at −0.13…+0.13 s |
| Pose lost next to the impact (no pose for 7–64 samples) | 3: img5098_full 3191, 3683, 3958 (all visibility `none`) | the run ends because the pose disappears 0.2–0.5 s before the label | **none**: the run-edge peak is their only evidence |
| The most prominent peak in a wide territory is another movement, 1.2–11.6 s from the label | 6: img5098_1 142, 1345; img4912_2min 1023; img4912_m3 700; img5098_full 1483, 3777 | unrelated motion at a run edge (pose lost in 4 of them) | within 0.10 s for 3 (img5098_1 142, 1345; img4912_2min 1023); farther for 2 (img5098_full 1483 at −0.50 s, img4912_m3 700 at +2.43 s); none for 3777 (its only peak ≥ 2.0 is the run-edge one at −1.23 s) |

So the large one-sided prominences mostly come from the run being cut while the wrist is
still fast: the last sample before the cut is a "maximum" only because the run ends, and its
speed comes from a shifted (edge) fit. Their prominence measures the drop on one side only.
In 13 of the 19 an internal peak lies within 0.13 s of the impact; in 3 (`none`
visibility, pose lost) the run-edge peak is all there is. Why the visibility fades in the
follow-through (motion blur, the arm crossing the body seen from behind, or both) cannot be
told from the pose and was not checked on the video.

### 5.6 Internal and run-edge peaks at the candidate minimum (2.0)

| Category | Dev A kept / total | Dev B near kept / total (per min) | Dev B pause kept / total (per min) |
| --- | --- | --- | --- |
| internal | 765 / 2916 | 71 / 232 (51.9) | 12 / 102 (10.6) |
| run edge | 424 / 936 | 58 / 104 (42.4) | 16 / 44 (14.1) |

Nearest kept peak of the expected wrist for the 53 targets:

| Category | Targets with a peak | Without | Offset P10 / P50 / P90 (s) | abs offset P50 / P90 (s) |
| --- | --- | --- | --- | --- |
| internal | 49 | 4 (img5098_full 3191, 3683, 3777, 3958, all `none`) | −0.13 / +0.03 / +0.15 | 0.10 / 0.26 |
| run edge | 35 | 18 | −0.67 / +0.43 / +1.35 | 0.67 / 1.41 |

Internal peaks sit at the impact (median one frame after it); run-edge peaks mostly follow
it by about half a second. In the Development B pauses, run-edge peaks above the minimum are
more frequent than internal ones (14.1 vs 10.6 per minute).

## 6. Minimum prominence: candidate 2.0 (decided by the developer, 2026-11-23)

**Decision:** 2.0 torso lengths/s is the candidate minimum for I2
(`CANDIDATE_MIN_PROMINENCE_TORSO_PER_S`), **not** a validated threshold. It is frozen for the
single Control run and will not be changed on the basis of Control results. Run-edge peaks
are kept and identifiable (`category = run_edge`), counted apart from internal peaks; I2
handles the two categories separately without excluding run-edge peaks in advance.

Why this value (Development A/B):

- It is the largest value tried at which no target loses every expected-wrist peak. The
  margin is thin: the weakest target is at 2.04 (img5098_full 3777, visibility `none`).
- It removes 69 % of Development A peaks, 62 % of the near peaks and 81 % of the pause peaks
  of Development B (pause rate 129 → 24.7 per minute, both categories). It is close to the
  P90 of internal pause peaks (2.13).
- At 3.0 the pause rate falls to 15/min but three `none`/`partial` targets lose every peak.

Remaining peaks in pauses are expected: the minimum prominence only removes small maxima;
telling swings from other movements is the job of grouping and decisions (I2, I3).

## 7. Control: single run with frozen algorithm and parameter

Protocol (Control = img5098_full 4500–9470, same session as Development B, ADR-023):

1. Frozen before the run: the commit of `strokes/peaks.py` and the sha256 of it and of the
   two analysis scripts, recorded with the data (`m2_3_peaks/FROZEN.md`).
2. One run: `analisi_i1.py --control` then `report_i1.py --control`. Only the candidate
   minimum is reported (no scan of other values), internal and run-edge peaks apart, with
   the same definitions as Development (territory, near / pause, offsets).
3. The results are reported as "Control (same session as Development B)" and are not used to
   change the algorithm or the value.

Status: **run once on 2026-11-23** after the developer's go-ahead, with code `f4bdc11` and
the hashes in `m2_3_peaks/FROZEN.md`. Full tables: `m2_3_peaks/i1c_tabelle.md` (project
files, with the personal data). It must not be run again, and nothing was changed on its
results.

### 7.1 Control results (26 forehand/backhand targets, minimum 2.0, rule A)

| Category | Targets with a peak | Without | Offset P10 / P50 / P90 (s) | abs offset P50 / P90 (s) |
| --- | --- | --- | --- | --- |
| internal | 19 | 7 | −0.70 / +0.03 / +0.10 | 0.10 / 0.70 |
| run edge | 20 | 6 | −1.19 / −0.02 / +0.90 | 0.77 / 1.19 |
| either (reference only) | 24 | 2 | −0.82 / +0.00 / +0.10 | 0.10 / 0.82 |

"With a peak" means that a kept peak of the expected wrist exists, at **any** distance from
the label: it does not mean the stroke was found. Example: 8851 counts in "either" through a
run-edge peak 1.40 s before the label. Internal and run-edge peaks are never added into one
rate; the "either" row is for reference only.

Kept peaks per minute (near = within 1.5 s of a label, 1.60 min; pause 1.16 min): internal
49.9 near / 26.8 pause; run edge 44.9 near / 13.8 pause.

### 7.2 Reading and limits

- **Well visible strokes.** 13 of the 14 `full` targets have an internal peak within 0.17 s
  of the label (−0.13 to +0.17 s). The exception is 8851 (forehand, `full`, no internal
  peak, nearest run-edge peak −1.40 s): not explained, recorded as an open case, not
  investigated in I1.
- **Poorly visible strokes.** Of the 12 `none`/`partial` targets, 6 have no internal peak
  (4827, 6954, 7497, 7595, 7717, 8952) and others have one up to 0.87 s away (5593). The
  abs-offset P90 of 0.70 s comes from these. The two targets with no peak at all (7595, 7717)
  are both `none`.
- **Lower than Development.** Internal coverage is 19/26 on Control against 49/53 on
  Development A/B (section 5.6). Control has many `none`/`partial` targets (12 of 26); no
  further breakdown was computed, by protocol.
- **Pause activity, by category (never summed).** Internal peaks kept in pauses: 26.8 per
  minute on Control against 10.6 on Development B. Run-edge peaks kept in pauses: 13.8 per
  minute on Control against 14.1 on Development B. Internal-peak activity in pauses is
  higher on Control; run-edge activity is about the same.
- **Peaks in pauses.** A kept peak is not a stroke: about 27 internal and 14 run-edge peaks
  per minute remain in pauses. Telling swings from other movements is left to the events and
  decisions (I2, I3).
- **Not independent.** Control is the same session as Development B (ADR-023, M2_3_DATASET
  §2.1): unseen strokes of that session, not new conditions, not another player. It is not a
  validation and not evidence of generalisation.
- The minimum 2.0 remains a **candidate** value, not a validated threshold; it is still not
  applied in I1 code.

## 8. Not done in I1

- The candidate minimum is not applied in code (no `discarded` status), no `rel_to_clip` (its
  definition belongs with the relative thresholds of I3).
- No conversion to `WristPeak` / `strokes.json` (I2), no grouping, no CLI, no pipeline change.
- Control run once with the frozen parameters (section 7); not used to change anything.
