# M2.2 — Signal processing (pose signals)

Status (2026-11-20): **intervention 1 (time base) implemented**; smoothing, derivatives,
body-frame normalisation and the other signals are **not implemented**. Each intervention
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
| no map | `proxy` | `missing` | time = `frame_index / fps`; repeats cannot be seen; base is **unverified** |
| map present but inconsistent | `proxy` | `inconsistent` | map ignored, reasons in `map_problems`; base is **unverified** |

**Samples.** Every `pose.json` frame, detected or not (detection and visibility are the
next intervention's job), minus proxy frames that show the same source frame as the
previous kept sample. The first proxy frame of a repeat is kept.

**Intervals.** `interval_s[i] = times_s[i] - times_s[i-1]` on real times (never an assumed
constant `dt`). Status vs the nominal interval `frame_stride / fps`:

* `regular`: `min_interval_ratio ≤ ratio ≤ max_interval_ratio` (defaults 0.5 / 1.5);
* `long`: VFR pause, hole left by a repeat, missing stride sample;
* `short`: two source frames very close (burst);
* `first`: first sample.

Every `long` / `short` interval starts a new **segment**. Later derivatives and local fits
must stay inside one segment. A source frame the proxy skipped is only a missing sample:
it splits a segment only if the real interval around it is irregular.

**Map checks** (`check_timemap`): `proxy_fps` = pose `fps`; the map covers every pose frame;
`proxy_to_source` length = `proxy_frame_count`, non-decreasing, inside `source_times_s`;
`source_times_s` length = `source_frame_count`, strictly increasing; pose `timestamp_s` =
`frame_index / fps` (±¼ frame).

**Parameters** (`TimeBaseParams`) are general, not player-specific, and **not validated**:
1.5 / 0.5 were chosen because the iPhone originals of M2.1 alternate 31.67 / 35.00 ms around
33.33 ms (ratio 0.95–1.05) while one missing frame gives ≈ 2.

### 2.1 Check on the real M2.1 outputs (13 videos, regenerated maps)

All 13 maps pass the consistency checks (`basis = source`).

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

## 4. Not implemented yet (next interventions, after review)

Visibility/continuity of keypoints per sample, scale normalisation (`speed_2d_torso_per_s`
is a 2D scale normalisation by torso length per second, not a 3D speed), local polynomial
fit on non-uniform times within segments, derivatives with explicit reliability states,
per-player hand/backhand configuration for wrist selection.
