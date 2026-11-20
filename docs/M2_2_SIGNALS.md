# M2.2 — Signal processing (pose signals)

Status (2026-11-20): **intervention 1 (time base) merged; intervention 2 (temporal
smoothing of positions) done; intervention 3 (two quality levels, LOW from 0.4) done; intervention 4 (torso scale) under
review** (next
steps: ADR-019); derivatives (velocities,
accelerations), peaks, body-frame normalisation and the other signals are **not
implemented**. Each intervention
is reviewed before the next one starts. Scope (approved 2026-11-20): signals only, NumPy
only, no change to pipeline / worker / API / UI, no stroke detection (M2.3) and no
forehand/backhand classification (M2.4). The package `engine/tennis_cv/signals/` is a pure
library and is not called by the pipeline.

## 1. Why a time base first

Velocities and accelerations divide displacements by time intervals. If an interval is
wrong (a repeated frame read as "no motion", a hole read as one step) the result looks like
a real movement. Every later signal therefore starts from an explicit list of samples with
a checked time and a per-interval status.

## 2. Time base (`tennis_cv.signals.timebase`)

`build_timebase(poses, timemap=None, params=None) -> TimeBase`

| Input situation | `basis` | `map_status` | Behaviour |
|---|---|---|---|
| consistent `timemap.json` | `source` | `ok` | sample time = `source_times_s` of the source frame shown (original file timeline, 0 = first source frame); proxy repeats removed |
| no map | `proxy` | `missing` | time = `frame_index / fps`; repeats cannot be seen; base is **unverified**; pose `timestamp_s` problems still listed in `map_problems` |
| map present but inconsistent | `proxy` | `inconsistent` | map ignored, reasons in `map_problems`; base is **unverified** |

**Samples.** Every `pose.json` frame, detected or not (detection and visibility are the
next intervention's job), minus proxy frames that show the same source frame as the
previous kept sample. The first proxy frame of a repeat is kept.

**Intervals.** `interval_s[i] = times_s[i] - times_s[i-1]` on real times (never an assumed
constant `dt`). Status vs the nominal interval `frame_stride / fps`:

* `regular`: `min_interval_ratio ≤ ratio ≤ max_interval_ratio` (defaults 0.5 / 1.5);
* `long`: VFR pause, hole left by a repeat, missing stride sample;
* `short`: two source frames very close (burst);
* `invalid`: interval not finite (NaN / inf); never classified `regular`. The checks below
  make it unreachable from validated inputs; it is a last guard in `classify_intervals`;
* `first`: first sample.

Every `long` / `short` / `invalid` interval starts a new **segment**. Later derivatives and local fits
must stay inside one segment. A source frame the proxy skipped is only a missing sample:
it splits a segment only if the real interval around it is irregular.

**Map checks** (`check_timemap`): `proxy_fps` finite and = pose `fps`; the map has exactly
the proxy frames of `pose.json` (stride 1: same count; stride *s*: the last pose frame is
one of the last *s* map frames), so a map of another, longer video is refused;
`proxy_to_source` length = `proxy_frame_count`, non-decreasing, inside `source_times_s`;
`source_times_s` finite, length = `source_frame_count`, strictly increasing; pose
`timestamp_s` finite and = `frame_index / fps` (±¼ frame).

**Rejected inputs** (`ValueError`, no time base): `fps` not finite or ≤ 0,
`frame_stride` < 1, negative or non-increasing `frame_index`.

**Parameters** (`TimeBaseParams`) are general, not player-specific, and **not validated**:
1.5 / 0.5 were chosen because the iPhone originals of M2.1 alternate 31.67 / 35.00 ms around
33.33 ms (ratio 0.95–1.05) while one missing frame gives ≈ 2.

### 2.1 Check on the real M2.1 outputs (13 videos, regenerated maps)

All 13 maps pass the consistency checks (`basis = source`), and `map_problems` is empty
with and without the map. After the review fixes (NaN/inf, negative indices, map length,
timestamps without map) the 39 time bases (13 videos × new map, old map, no map) are
identical to those of the first version.

| Video | Repeats removed | Irregular intervals | Segments | Real intervals |
|---|---|---|---|---|
| IMG_4893 | 4 | 3 long (proxy 9017–9021) | 4 | 31.67–66.67 ms |
| IMG_5098 | 3 | 2 long (8665, 8668) | 3 | 31.67–65.00 ms |
| IMG_5098_1 | 3 | 2 long (9468, 9470) | 3 | 31.67–66.67 ms |
| IMG_4895_2min | 2 | 1 long (3602, real 66.67 ms gap in the original, last frames) | 2 | 31.67–66.67 ms |
| IMG_4894, IMG_4912_2min, IMG_5098_share22s | 1 each | 0 | 1 | 31.67–35.00 ms |
| IMG_4912_min3-4, Video_Project_2 | 0 | 0 | 1 | 33.33 ms |
| Timeline_1/2/3/5 | 0 | 0 | 1 | 41.67 ms |

The long intervals of IMG_4893 / 5098 / 5098_1 are **not** in the originals (their source
intervals are all ≤ 35 ms): there the source timestamps sit exactly on half proxy slots, so
the fps filter repeats one source frame and skips the next a few times in a row (checked
against the decoded proxy in M1.10 §3ter). Those source frames have no pose, so the time
base reports the hole instead of hiding it. None of the 35 labelled impact windows
(±15 frames) contains an irregular interval; one (img5098, frame 676) contains a removed
repeat (proxy frame 679).

With the **old** maps (written before commit 8df7dd3) the same code would report 268–318
long intervals per iPhone video: the checks cannot detect a wrong-but-consistent map, which
is why old maps must be regenerated (see §3).

## 3. Limits that the time base cannot correct

* **MediaPipe temporal tracking.** `pose.json` is produced in VIDEO mode, which smooths
  and tracks landmarks across frames. Removing repeats does not undo that filter; it cannot
  be undone from `pose.json`. Measured on IMG_4894 (1000 frames, VIDEO vs IMAGE mode): VIDEO
  jitter ≈ 2.1 px vs 4.9 px; wrist-speed peaks fall on the same frame for clean forehands
  (±1–2 frames otherwise) but their height differs by 0.4–1.7×. Consequence: speeds will be
  usable for timing and relative comparison within a video, **not** as absolute values, and
  removing repeats does **not** make derivatives reliable by itself.
* **Repeats seen by the tracker.** MediaPipe processed every proxy frame, repeats included,
  so its internal state saw "no motion" for one frame there. The pose of the kept sample is
  the first one, before that effect.
* **Old maps.** A `timemap.json` written before 8df7dd3 is consistent but wrong at
  half-slot ties; nothing in the file tells it apart. Re-run preprocessing (or regenerate
  the map) for videos processed before that commit.
* **Source frames identical in content** (a phone duplicating a frame at capture) have
  different timestamps and cannot be detected from metadata; not handled.
* **Without a map** the base is on proxy times and unverified: repeats and VFR holes are
  invisible; later signals must treat such a base as lower quality.

## 4. Temporal smoothing (`tennis_cv.signals.smoothing`)

Positions only. No velocity, acceleration or peak is computed here.

```python
raw = extract_keypoint(poses, timebase, "right_wrist")  # RawKeypointSignal (pixels)
sm = smooth_keypoint(raw)  # SmoothedKeypoint: raw + x/y SmoothedSignal
```

**Raw signal.** `x_px`, `y_px` = MediaPipe's normalised `x`, `y` × image width / height
(same unit on both axes), exactly as written in `pose.json`; NaN only when the pose was
not detected or the keypoint is absent. Each sample has a quality (§4.3); smoothing uses
the usable ones (`good` or `low_but_usable`). Raw values and visibility are never
overwritten: `SmoothedSignal.raw` is a copy next to `smoothed`.

**Method.** For every sample *i* a polynomial of order `polyorder` (2) is fitted by least
squares to the `window` samples around it, in `t - t_i` seconds from the time base (real,
non-uniform times), and its value at `t_i` is taken. On uniform times this is exactly the
Savitzky–Golay filter; on iPhone timing (31.67 / 35.00 ms) a quadratic in real time is
reproduced exactly, while a fit on the frame index is biased (test 7).
`window` comes from `window_s = 0.2` s and the nominal interval: 7 samples at 30 fps,
5 at 24 fps, always odd and ≥ `polyorder + 2`.

**Runs.** Fits use only *runs*: consecutive reliable samples of one time-base segment. A
run ends at every `long` / `short` / `invalid` interval and at every unreliable sample, so
no fit crosses an anomalous interval or bridges a hole, and nothing from one run can affect
another (test 4: a 10⁴ px jump in the next segment changes nothing).

**Per-sample state** (`SmoothState`):

| State | Value | When |
|---|---|---|
| `smoothed` | yes | centred window inside the run |
| `edge` | yes | first/last `window // 2` samples of a run: window shifted inside the run |
| `unreliable` | NaN | raw sample not reliable; never interpolated |
| `too_short` | NaN | run shorter than `window` |

**Edges.** The window is shifted, not shrunk, and the polynomial is evaluated inside the
fitted interval (no extrapolation), so polynomials up to order 2 are reproduced exactly at
the edges and no overshoot is created. Edge values are noisier: measured noise factor (std
of output / std of white input noise) 0.86 on the first/last sample of a run, 0.53 on the
next two, 0.58 for centred samples (7-point quadratic). Later steps must weigh `edge`
samples accordingly.

**Synthetic checks** (`tests/unit/test_signals_smoothing.py`): noise reduced to ≈ 0.58×
(centre); a Gaussian bump keeps its peak frame and 98.7 % of its height at σ = 0.1 s
(95.6 % at 0.07 s, 88 % at 0.05 s: a very short excursion is flattened, a known
property of any smoothing of this width); runs shorter than the window and unreliable
samples have no value; an unreliable raw value has no influence anywhere.

### 4.1 Behaviour on the 13 M2.1 videos (not a validation)

Run on wrists, shoulders and hips with the regenerated maps (4.4 s for 78 series).
Figures describe what the method does with these inputs; they say nothing about
biomechanical accuracy.

* **Share of samples with a value** (`smoothed` + `edge`): shoulders/hips 57–89 %; wrists
  19–72 % (left wrist usually lowest). The rest is mostly `unreliable` (pose not
  detected, player out of frame or wrist visibility < 0.5); `too_short` is 0.3–3.7 %.
* **Raw − smoothed residual** (centred samples, RMS): shoulders/hips 0.6–5 px, wrists
  2–10 px; 99th percentile up to 39 px on wrists (fast swings and tracking jumps both
  contribute; the method cannot tell them apart).
* **At the 35 labelled impacts** (right wrist for forehands and other strokes, left wrist
  for two-handed backhands): 31 `smoothed`, 1 `edge`, 3 `unreliable` (visibility < 0.5)
  at the impact sample; 23/35 windows of ±10 samples have a value on every sample. With
  the threshold at 0 it would be 34 + 1 and 34/35: the 0.5 threshold is the main source of
  holes around impacts. (Correction: an earlier note said M2.1 "found wrists placed
  correctly with scores of 0.35"; that was **one** case out of 12 checked below 0.5, see
  §4.3.)

### 4.2 Limits

* Smoothing reduces jitter; it does not correct MediaPipe tracking errors (a wrong but
  confident wrist stays wrong) and it adds a second low-pass on top of MediaPipe's own
  VIDEO-mode filtering (§3).
* Window and order are general starting values, not tuned on data; fast short
  excursions (< ~0.07 s) are attenuated.
* `edge` samples are valid but noisier; `too_short` runs are lost rather than smoothed with
  a smaller window (deliberately conservative).
* The visibility thresholds are per keypoint score and unvalidated (§4.3).

### 4.3 Keypoint quality: two levels (intervention 3)

| Quality | Rule | In fits / runs | Smoothed value |
|---|---|---|---|
| `good` | visibility ≥ `good_visibility` (0.5) | yes | yes |
| `low_but_usable` | `usable_visibility` ≤ visibility < `good_visibility` | yes, does **not** break runs | yes, and `low_in_window` counts the LOW samples each fit used |
| `unreliable` | not detected, keypoint or visibility missing, non-finite, or visibility < `usable_visibility` | no, breaks runs | NaN (never interpolated) |

The quality is kept per sample in `RawKeypointSignal.quality`, next to raw coordinates
and visibility; the smoothing state stays in `SmoothedSignal.state`. A LOW sample changes
the label, not the fit: the smoothed values are those obtained treating it as good.

**Choice of `usable_visibility`: provisional 0.4** (single player, not validated).

*Evidence 1, M2.1 impact check* (`overlay_wrist`, drawn hitting right wrist at the impact
frame, 30 of 35 checked):

| Right-wrist score at impact | On the hand | Elsewhere | Not drawn / missing |
|---|---|---|---|
| ≥ 0.5 | 18 | 0 | 0 |
| 0.35–0.5 | 1 (0.353) | 6 (0.383–0.497) | 1 (0.484) |
| < 0.35 | 0 | 2 | 2 |

All 12 below 0.5 are two-handed backhands with the right hand hidden behind the body.

*Evidence 2, targeted check (2026-11-20).* 30 wrists with scores 0.2–0.5 (15 right, 15
left, 5 per band and wrist), from 12 videos, ≥ 0.5 s away from the labelled impacts,
judged by the developer on crops (sheet and script in the project files, not in git):

| Score band | On the hand | Wrong | Hand hidden (not judgeable) |
|---|---|---|---|
| 0.4–0.5 | 7 | 1 (left/right swapped) | 2 |
| 0.3–0.4 | 4 | 2 (one "between the two hands") | 4 |
| 0.2–0.3 | 2 (+1 "right or hidden") | 1 (on the opponent) | 6 |

*Reading.* In ordinary play the 0.4–0.5 band is mostly on the hand (7 of 8 judgeable);
below 0.4 errors and hidden hands grow, and at 0.2–0.3 a keypoint can sit on another
person. The hidden right hand at two-handed backhand impacts is wrong even in 0.4–0.5
(0 of 5 in Evidence 1). Lower scores mostly mean "hand hidden": the position is then a
guess, not an observation. Hence `usable_visibility = 0.4`: LOW samples are kept and
marked, never presented as good. Consequences for later interventions: signals sensitive
to a wrong wrist (distance between hands on two-handed backhands) should require GOOD
samples or report `low_in_window`; the hitting-wrist choice for two-handed backhands stays
the left wrist (M2.1).

With 0.4 instead of 0.5, the hitting wrist at the 35 labelled impacts is unchanged (32
with a value, 3 unreliable) while the ±10-sample windows with a value on every sample go
from 23 to 29 of 35.

**Distribution on the 13 videos** (share of time-base samples; "with value" = `smoothed`
+ `edge`; observed behaviour, not validation; ~23 % of samples have no pose at all, see
shoulders):

| | Good | Low @0.4 (default) | Low @0.2 | With value @0.5 / @0.4 / @0.2 |
|---|---|---|---|---|
| Right wrist | 50.1 % | 5.8 % | 18.3 % | 49.0 / 54.7 / 66.9 % |
| Left wrist | 38.0 % | 7.6 % | 23.9 % | 37.1 / 44.5 / 60.8 % |
| Right shoulder | 77.3 % | 0 % | 0 % | 76.0 % at every threshold |
| Left hip | 77.3 % | 0 % | 0 % | 75.9 % at every threshold |

Per video, right wrist good 41–78 %, left wrist 26–60 %; side-view clips (IMG_4912,
Timeline_1/2) have few low-score right-wrist samples.

## 5. Torso scale (`tennis_cv.signals.scale`, intervention 4)

A **2D scale normalisation** of the image, not a 3D measurement: later distances and
speeds are expressed in *torso lengths* so that they do not depend on the distance from
the camera. No body-specific constant is involved.

```python
sc = torso_scale(poses, timebase)  # TorsoScale
sc.length_px  # stabilised torso length per sample (NaN = missing)
sc.to_torso_units(distance_px)  # NaN where the scale is missing; pixels untouched
```

* **Measurement:** `|mid-shoulder − mid-hip|` in pixels from the raw positions, only when
  all four keypoints are `good` (≥ 0.5). `low_but_usable` and `unreliable` samples do not
  measure the torso. Instantaneous values are kept in `measured_px`.
* **Stabilisation:** rolling median over `window_s` = 1 s of real time, centred, inside
  the time-base segment. It removes per-frame noise, isolated wrong hips/shoulders and the
  brief shortening of the projected torso during a stroke (~0.3 s), and follows changes
  of camera distance slower than about half a second; a linear change is followed with
  no lag (test). Faster changes are smoothed over ~0.5 s. The window is **centred, so it
  uses future samples**: suitable for offline session analysis, **not causal**, not usable
  as is for real-time analysis.
* **Missing:** fewer than `min_fraction` (50 %) of the samples of a *full* window with a
  measurement → `insufficient`, `length_px` NaN, every normalised value NaN. At 30 fps:
  ≥ 16 of 31. Consequences (tested): a segment of 15 samples never gets a scale, one of
  16 gets it on every sample; at a segment edge only half a window (16 samples) is
  available, so one missing measurement there removes the scale on the edge sample.
* **Spread:** `relative_spread` = MAD / median in the window: an indicator of
  **geometric dispersion only**, not an autonomous measure of reliability nor of correct
  player identification. A scale can be stable and belong to the wrong person (§5.1,
  IMG_5098_1). Reported for later use, not used to reject (no validated threshold).

Synthetic checks (`tests/unit/test_signals_scale.py`): ±3 px noise and 10 % wrong hips →
error < 2 %; a 200 → 300 px approach in 3 s is followed exactly; hips hidden for 2 s →
no scale and no normalised value beyond half a window; LOW hips never measure; no mixing
across segments.

### 5.1 Behaviour on the 13 videos (not a validation)

| | Range over the videos |
|---|---|
| Samples with a measurement | 59–89 % (side-view Timeline_1/2: 59–64 %) |
| Samples with a scale | 58–89 % |
| Median torso length | 46–98 px (side and far views smaller) |
| Median relative spread | 0.022–0.032 |
| Samples whose scale changes > 50 % in 1 s | 0–1.8 % per video |

The large 1-s changes are mostly **the pose jumping to another person** (e.g. IMG_5098_1
frame ~1720: 22 px, the far opponent, then 101 px, the player). The scale is then
consistent but for the wrong person. Identity is not handled here (M2.5); the spread is
sometimes, not always, high around such switches. Within the player's own sequences the
spread stays around 2–3 %.

### 5.2 Limits

* Projected torso length changes with posture (bending, trunk rotation, side view): the
  1-s median attenuates this, it does not remove it.
* A wrong but `good` shoulder or hip for longer than half a window moves the scale.
* Person switches are not detected (M2.5).
* Window and `min_fraction` are general starting values, not validated.

## 6. Not implemented yet (next interventions, after review)

Derivatives (velocity, acceleration) with explicit reliability states, scale normalisation
(`speed_2d_torso_per_s` is a 2D scale normalisation by torso length per second, not a 3D
speed), per-player hand/backhand configuration for wrist selection (ADR-019: #5 apparent
2D speed, #6 complementary signals; peaks move to M2.3).
