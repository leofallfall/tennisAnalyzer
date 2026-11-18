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
at 1920 px (even dimensions); VFR resampled to its average rate with `fps=` filter;
`-fps_mode cfr`; H.264 yuv420p CRF 18 `+faststart`. Audio dropped.

**Trade-off**: resampling VFR to CFR may duplicate/drop individual frames (error ≤ half a
frame interval). Accepted because it gives an exact `index / fps` time base, which
phase timing and cross-video alignment depend on. Revisit if contact timing needs
sub-frame accuracy (would require PTS-based timestamps end to end).

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

## 8. Overlay

`overlay.mp4`: proxy frames with skeleton (left = blue, right = orange, low visibility =
grey) and a label `#frame  time  pose|no pose detected|not analysed`. Written as MJPEG then
transcoded to H.264 for browser playback.

## 9. Scene detection ⏳

Split sessions into segments with constant camera geometry (hard cuts, camera moved,
player changed side). Required before aggregating metrics across a session.

## 10. Stroke detection and analysis ⏳

Candidate strokes from wrist speed peaks and body rotation in the pose sequence
(and later racket/ball), classification by hitting side relative to dominant hand,
per-stroke quality gating, phase segmentation, metric calculation, aggregation.

## 11. Storage layout

```
storage/
  tpa.sqlite3
  videos/<video_id>/original.<ext>
  analyses/<analysis_id>/videos/<video_id>/
      proxy.mp4  pose.json  overlay.mp4  report.json
```

`report.json` (`ProcessingReport`) also lists `not_performed` analyses explicitly.
