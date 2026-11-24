# Video Pipeline

Implemented in `engine/tennis_cv/pipeline.py` (`run_video_processing`) and invoked by the
worker. Steps marked ⏳ are designed but not implemented.

## 1. Upload (API)

- Allowed extensions: `.mp4 .mov .m4v .avi .mkv .webm`. Others → 415 `unsupported_file_type`.
- Streamed in 1 MiB chunks; SHA-256 computed; size limit `TPA_MAX_UPLOAD_MB` (default 4096) → 413.
- Stored at `storage/videos/<video_id>/original.<ext>` (original never modified).

## 2. Validation

`tennis_cv.video.validation.validate_metadata` returns errors (reject) and warnings
(keep, show to user):

| Code | Severity | Rule |
| --- | --- | --- |
| `unsupported_codec` | error | Codec not in h264/hevc/vp8/vp9/av1/mpeg4/prores/mjpeg |
| `too_short` / `too_long` | error | < 0.5 s or > 60 min |
| `resolution_too_low` | error | Short side < 360 px |
| `resolution_low` | warning | Short side < 720 px |
| `fps_too_low` | error | < 20 fps |
| `fps_low` | warning | < 50 fps |
| `variable_frame_rate` | warning | VFR source (will be resampled) |
| `unknown_duration` / `unknown_fps` | warning / error | Not determinable |
| `file_too_large` | error | Over limit |

Unreadable files → 422 `probe_failed`.

## 3. Metadata extraction

`probe_video` runs `ffprobe -show_format -show_streams -of json`. Handles:
- **Rotation**: display-matrix side data (counter-clockwise) and legacy `rotate` tag,
  normalised to clockwise {0, 90, 180, 270}; display width/height swapped accordingly.
- **VFR**: flagged when `r_frame_rate` and `avg_frame_rate` differ by > 2 %
  (typical of phones: `120/1` nominal vs `29.97` average).
- Missing `nb_frames`: estimated from duration × average fps.

## 4. Preprocessing (proxy)

`preprocess_video` → `proxy.mp4`: rotation applied by FFmpeg auto-rotate; long side capped
at 1920 px (even dimensions); filter chain `setpts=PTS-STARTPTS,fps=<rate>:eof_action=pass`
(since M1.10) where `<rate>` is the average rate rounded to 3 decimals for VFR inputs and the
nominal rate as a rational (e.g. `30000/1001`) for CFR inputs, which are therefore not
resampled; `-fps_mode cfr`; H.264 yuv420p CRF 18 `+faststart`. Audio dropped.

Proxy time 0 is the **first decoded video frame** of the source: a non-zero `start_time` or
an audio track that starts earlier no longer adds duplicated frames at the start (before
M1.10 it did, because FFmpeg aligned the proxy on the container start).

**Trade-off**: resampling VFR to CFR duplicates/drops individual frames. Accepted because it
gives an exact `index / fps` time base, which phase timing and cross-video alignment depend
on. The link back to the original is kept in `timemap.json` (§4.1). Revisit if contact
timing needs sub-frame accuracy (would require PTS-based timestamps end to end).

### 4.1 Time mapping proxy → original (`timemap.json`, M1.10)

**This is a reconstruction, frame-exact but time-approximate, not an exact time
synchronisation** (`exact_time_sync: false` in every map).

- **Algorithm** (`tennis_cv/video/timemap.py`): ffprobe reads the packet PTS of the source
  video stream (no decoding), drops packets flagged *discard* by the edit list, sorts them
  (presentation order) and subtracts the first one. Source frame `i` at relative time `t_i`
  belongs to proxy slot `s_i = floor(t_i × fps + 0.5)` — the rule of FFmpeg's `fps` filter
  (`round=near`), computed exactly from integer PTS × stream `time_base` (rounded
  `pts_time` floats misplace frames lying exactly on a half-slot, frequent on 1/600 s phone
  timescales). Proxy frame `k` shows the **last** source frame with `s_i ≤ k`. Proxy
  frames after the last slot (`eof_action=pass`) repeat the last source frame.
- **Content**: `summary` (also in `report.json` as `time_mapping`), `source_times_s`
  (each source frame's time relative to the first) and `proxy_to_source` (source frame index
  for every proxy frame).
- **Going back to the original**: absolute time in the original file of the frame shown at
  proxy time `T` = `source_start_pts_s + source_times_s[proxy_to_source[floor(T × fps)]]`
  (`TimeMap.source_time`). `source_start_pts_s` is the presentation time of the first video
  frame in the original container (it handles a non-zero `start_time`).
- **Duplicated and dropped frames**: `duplicated_frames` counts proxy frames repeating the
  previous source frame (gaps, lower source rate); `dropped_frames` counts source frames
  shown by no proxy frame (several in one slot, higher source rate).
- **Expected precision**: frame identity is exact *if FFmpeg applies the rule as
  reconstructed*: verified on synthetic videos whose frames carry their own index in the
  pixels (CFR 30 fps, VFR 30→120 fps, `start_time` 2.5 s, audio 0.5 s earlier, 240 fps CFR,
  jittery 120 fps with missing frames: 0 mismatches). Time error |proxy time − source time
  of the frame shown| is ≤ half a proxy interval where the source is dense, larger across
  gaps; each map reports `max_abs_error_ms` and `mean_abs_error_ms`.
- **Limits**: not validated on real smartphone files yet (M1.11). Real VFR/high-frame-rate
  phones can have irregular timestamps, edit lists and slow-motion metadata (e.g. iPhone
  slo-mo playback ramps), which are ignored: times are the container's presentation times.
  VFR resampling to the average rate means a 240 fps burst inside a 30 fps clip is decimated
  (many `dropped_frames`). Tie cases exactly at half a slot depend on floating-point
  rounding. If the map cannot be built (ffprobe error, invalid ffprobe output, timeout or
  OS error) the analysis still completes: `time_mapping` is `null`, there is no `timemap`
  artifact and a warning is logged (tested for each case). The web UI does not read
  `time_mapping` yet, so a missing map is not shown to the user; nothing in the app uses
  proxy time as original time (player and Evidence Pack state the proxy timeline).

## 5. Frame extraction

`iter_frames(path, stride, start_s, end_s, max_frames)` decodes sequentially with OpenCV
(`grab` for skipped frames, `retrieve` for used ones). Timestamps are `index / fps` on the
proxy. Seeking is avoided because long-GOP smartphone video seeks imprecisely.

## 6. Quality assessment

Sampled every 0.5 s: sharpness (variance of Laplacian at 640 px width), mean luma,
contrast, clipped fractions. Combined with source resolution/fps and pose detection
rate into ratings (good / acceptable / poor / unknown) and a preliminary observability.
**Thresholds are uncalibrated heuristics** and are reported as such in the API.

## 7. Pose estimation

MediaPipe Pose Landmarker (Tasks API, `VIDEO` running mode, `num_poses=1`, model
`pose_landmarker_full`) on every `pose_stride`-th frame (`TPA_POSE_STRIDE`, default 1).
Output `pose.json` (`PoseSequence`): 33 normalised keypoints per frame with visibility and
presence; frames without a person have `detected=false`. Measured on CPU (4 vCPU):
≈ 20 ms/frame for 576×768.

**Stored precision (M1.10)**: applied only when `pose.json` is written
(`pose/models.py: for_storage`); the estimator, the pose summary and the overlay use full
precision. x/y 5 decimals (0.02 px on a 1920 px side), z 4, visibility/presence 3 decimals
**rounded down** (a stored score is never above the estimator's, so the `≥ 0.6`
observability gate cannot pass because of rounding); timestamps exact (`frame / fps`).
This is numeric storage precision, not the physical accuracy of MediaPipe, which errs by
several pixels. Measurements computed later from `pose.json` (Evidence Pack) use the stored
values: projected joint angles change by < 0.05° (tested). Size, 1 min at 30 fps of synthetic keypoints: ≈ 9.4 MB → ≈ 5.7 MB on disk;
the API gzip-compresses JSON (≈ 1.1 MB transferred). The browser still loads the whole file:
windowed/columnar loading is on the roadmap. Real pose data still has to be measured (M1.11).

**One person only (`num_poses=1`)**: MediaPipe follows the most prominent person. With an
opponent or another person closer to the camera the skeleton can switch to them **without
any warning**. There is no automatic detection of subject switches (deliberately: an
unvalidated heuristic would give false positives and negatives). During M1.11 the player
to follow is annotated manually for each clip and every visible switch is recorded; explicit
player selection stays on the roadmap.

**Runtime check (M1.10)**: on Linux MediaPipe needs `libegl1 libgles2`. `import mediapipe`
works without them; the failure appears when the landmarker is created. That failure is
now the structured error `pose_runtime_unavailable` (with the install hint), and System
Status reports `pose_runtime` by actually creating a landmarker.

## 8. Overlay

`overlay.mp4`: proxy frames with skeleton (left = blue, right = orange, low visibility =
grey) and a label `#frame  time  pose|no pose detected|not analysed`. Written as MJPEG then
transcoded to H.264 for browser playback.

## 9. Scene detection ⏳

Split sessions into segments with constant camera geometry (hard cuts, camera moved,
player changed side). Required before aggregating metrics across a session.

## 10. Stroke detection and analysis (experimental, personal V1 step 3, ADR-028)

After the pose of each video the worker runs the experimental M2.3 detector
(`tennis_cv.strokes.detector.detect_dir`, parameters unchanged, docs/M2_3_EVENTS.md) on
`pose.json` + `timemap.json` and writes `strokes.json` (1.1) next to them
(`worker/tennis_worker/jobs/stroke_detection.py`). A detector failure never fails the
analysis: the pose results stay, and `results.strokes[<video_id>]` records `status`
(`completed` / `failed` / `not_available`), event counts per status and the number of
not-assessable stretches (counts, never rates).

Analyses processed before this step: `POST /api/analyses/{id}/strokes` queues a
`stroke_detection` job that runs the same detector on the `pose.json` already on disk (no
new pose estimation; about 4 s for 5 minutes of video). Only `results.strokes` changes.

`GET /api/analyses/{id}/videos/{video_id}/strokes` is the read-only V1 view: states and
reasons in words from `tennis_cv.strokes.states` (*colpo segnalato* / *da verificare* /
*scartato* / *non valutabile*), with times on the **proxy timeline** (`frame / fps`) also when
`strokes.json` uses source times, so the player seeks to the exact frame. Discarded events
are hidden in the web app unless asked for; not-assessable stretches are labelled as
insufficient evidence, never as absence of strokes.

Not yet: forehand/backhand classification, per-stroke quality gating beyond the states,
phase segmentation, metrics, aggregation.

## 11. Storage layout

```
storage/
  tpa.sqlite3
  videos/<video_id>/original.<ext>
  analyses/<analysis_id>/videos/<video_id>/
      proxy.mp4  pose.json  overlay.mp4  report.json  timemap.json  strokes.json
```

`report.json` (`ProcessingReport`) also lists `not_performed` analyses explicitly. File
references inside it are relative: the source by base name (`original.mp4`), the proxy
relative to its folder (`proxy.mp4`).

## 12. Errors and privacy (M1.10)

Errors are structured (`{code, message, details}`) and never contain server paths:
engine errors name files by base name and scrub ffmpeg/ffprobe stderr; the ffmpeg command
and Python tracebacks go to the server log only (`internal_error` carries an `error_id` to
find them). `tennis_api/redaction.py` replaces known server directories (`<storage>`,
`<models>`, `<app>`, `<home>`) in every error body, analysis status/result and served
`report.json`, which also covers rows written before M1.10. System Status lists the last
5 failed analyses. **Limit:** redaction recognises only the absolute paths of the known
roots; a path outside them in a third-party message (e.g. `/var/tmp/x/original.mp4`) is
returned unchanged (tested). A relative `TPA_STORAGE_DIR` is resolved to its absolute path
first, so plain words such as "storage" are never replaced. Known exception: the AI hand-off command shows the Evidence Pack path the
user has to type in Claude Code (absolute only when storage is outside the repository).
