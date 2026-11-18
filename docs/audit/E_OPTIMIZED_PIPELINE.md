# E — Optimised Processing Pipeline

Labels: **[M]** measured (see [`benchmarks/2026-11-18-baseline.md`](benchmarks/2026-11-18-baseline.md)), **[D]** derived, **[E]** estimate, **[U]** unmeasured.

Principle: **spend dense computation only where the measurement needs it.** Each stage is cheaper than the next and decides which frames the next stage sees.

## 1. Temporal resolution each measurement actually needs

| Measurement / decision | Required sampling | Why |
|---|---|---|
| Video usable? Camera angle, lighting, player visible | 0.5–1 fps | scene-level, changes slowly |
| Main player identity and court position, rally vs pause segmentation | 5–10 fps | players move < 1 m in 0.1 s; pauses last seconds |
| Candidate stroke detection (wrist speed peak, arm extension) | 10 fps for candidates, then refined | a forward swing lasts ≈ 0.3–0.6 s, so 3–6 samples at 10 fps show a peak; contact time is *refined* in the dense stage |
| Footwork / stance at preparation, split step | 30 fps | events last ~0.1–0.2 s |
| Contact point, wrist/forearm speed, racket-arm angles at contact, lag | **full source rate (60 fps preferred)** in ±0.5–0.75 s around contact | the wrist travels ≈ 15–30 cm between frames at 30 fps in a forehand **[E]**; a 30 fps contact frame can be ±16 ms off |
| Follow-through, finish position, balance after the stroke | 30 fps | slower phase |
| Session statistics (consistency across strokes) | derived from per-stroke measurements | no extra frames |

Conclusions:

- **Full temporal resolution is needed for ~1.5 s per stroke.** At 10 strokes per minute **[E, unmeasured on tennis]**, that is ~25 % of the frames in a continuous rally drill and much less in a real session with pauses.
- **The rest of the session needs at most 10 fps.**
- **Ask the user to film at 60 fps.** Phones do it by default at 1080p, and it doubles the samples around contact. Stay compatible with 30 fps: measurements that need 60 fps carry a lower confidence at 30 fps, per the `Measurement` model.

## 2. Stages

```
S0  Phone/browser        metadata (duration, size, codec from <video>/file), optional trim in/out,
                         camera-setup checklist → no CV on the device
S1  Worker  probe        ffprobe + validation                                 0.1 s           [M]
S2  Worker  timeline     CFR analysis/playback proxy, GOP = 1 s (fast seeking)
                         • skip entirely when source is already CFR + browser-playable H.264 [E]
                         • otherwise 720p CRF23 (51 cpu-s/min) or 1080p (91 cpu-s/min)        [M]
S3  Worker  scan         10 fps, pose LITE in VIDEO mode, per 30-s chunk in parallel
                         → player track, rally segments, quality stats, wrist-speed signal
                         decode 9–20 cpu-s/min [D/M] + pose 600 × ~30 ms ≈ 18 cpu-s/min [D]
S4  Worker  candidates   peaks of wrist speed + arm extension inside rally segments,
                         non-maximum suppression, side-of-body → candidate stroke list
                         negligible CPU [E]
S5  Worker  dense        for each candidate: window [-0.9 s, +0.6 s] around the peak, every frame,
                         pose FULL with fresh tracking state, ROI around the player track
                         ≈ 10 strokes × 90 frames × 31 ms ≈ 28 cpu-s/min + seek-decode 4–8 [D/E]
S6  Worker  classify     FH/BH (+ unknown) from wrist position vs body midline and handedness at
                         contact; reject non-strokes (bounce, walking, pick-up) → confidence
S7  Worker  measure      Measurement objects per stroke (angles, timing, speed proxies) with
                         observability flags; per-stroke quality gates
S8  Worker  evidence     per stroke: compact metrics JSON + 3–4 key frames (prep/contact/finish)
                         as 768-px JPEG crops; per session: aggregate stats + outliers
                         ≈ 1 cpu-s/min [E]; stroke clip = stream copy from proxy GOPs ≈ 0 [E]
S9  Worker  Claude       session call (aggregation)                      [see §4]
S10 On demand Claude     single-stroke deep analysis only when the user picks an issue/stroke
```

## 3. Cost per footage-minute (1080p60 source)

| | Current | Target, fast path (no proxy) | Target, with 720p proxy | Target, with 1080p proxy |
|---|---|---|---|---|
| CPU | 399 cpu-s **[M]** | ≈ 75 cpu-s **[E]** | ≈ 120 cpu-s **[E]** | ≈ 165 cpu-s **[E]** |
| Wall time on 4 vCPU | 264 s **[M]** | ≈ 25 s **[E]** | ≈ 35–45 s **[E]** | ≈ 50–60 s **[E]** |
| Peak RAM | ~0.85 GB **[M]** | ~2 GB with 4 pose processes **[E]** | ~2 GB **[E]** | ~2 GB **[E]** |
| Stored artifacts | 33 MB **[M]** | ~1–2 MB metrics + evidence **[E]** | + 3.2 MB proxy **[M]** | + 14.9 MB proxy **[M]** |

In plain terms:

- A 60-minute session drops from **≈ 4.4 h to ≈ 30–45 min** of wall time.
- CPU drops by **2.4–5×**.

These estimates come from measured per-stage costs, combined with an assumed stroke density of 10 strokes per minute. Both the stroke density and the detection rate on tennis footage are **[U]**, and benchmark plan G closes them.

## 4. Claude evidence selection (what is sent, and what is not)

**Never sent:**

- video
- full `pose.json`
- every frame

**Session call (once per analysis):**

- A system prompt holding the rubric, the `Measurement` semantics, the uncertainty rules from `AI_GUIDELINES.md` and the output JSON schema. It is ~3–4k tokens and **prompt-cached**.
- Aggregated stats per stroke type: n observable, median, IQR, trend over the session.
- A compact per-stroke table: about 15 numbers plus confidence each, ≈ 120–150 tokens per stroke **[E]**.
- Data-quality summary.
- **6–8 key frames:** a representative stroke and the worst outlier for each candidate issue. Each is 768 px on the long edge, ≈ 448 tokens (⌈768/28⌉ × ⌈432/28⌉ = 28 × 16) by the official formula **[D]**.
- Output: a ranked list of issues, each one an observation, the evidence it rests on, an interpretation, a recommendation and a confidence. Each issue links to the stroke ids that show it.

**Single-stroke deep call (only when the user picks an issue or stroke):**

- the same cached system prompt
- the stroke's full time series (pose landmarks subsampled to the joints that matter, at full rate in ±0.3 s of contact)
- metrics and quality
- 8–12 frames at 768 px (≈ 3.6–5.4k tokens)
- optionally 1–2 higher-resolution crops of the contact frame
- the session-level issue that motivated the request

**Longitudinal:** reuse the stored session results (JSON) only. No frames are sent unless the user asks to compare two specific strokes.

**Caching and deduplication:**

- Key every Claude result by `sha256(evidence pack + prompt version + model)`. Re-opening a session or re-asking the same deep analysis costs nothing.

## 5. Model choice per task

Prices are official per million tokens, input / output.

| Task | Model | Why |
|---|---|---|
| Session aggregation and issue ranking | **Claude Sonnet 5.5** ($2 / $10; cache read $0.10), via the **Batch API** (−50 %) when the user is not waiting | strong reasoning over structured data plus images at moderate cost; batch fits "results in a few minutes" |
| Single-stroke deep analysis (the user is waiting) | **Claude Sonnet 5.5** by default. **Claude Opus 5.5** ($4 / $20) as an "expert" option, or when Sonnet's confidence is low. | quality-sensitive, low volume, triggered by the user |
| Cheap text tasks: titles, short summaries, turning results into notifications, checking that output JSON is well formed | **Claude Haiku 5.5** ($0.10 / $0.50 for prompts ≤ 100K tokens) | fast and very cheap |
| Stroke detection or classification | **no LLM.** Deterministic CV plus a classifier. | per-frame work must never go to an LLM |

Model IDs are `claude-sonnet-5-5`, `claude-opus-5-5` and `claude-haiku-5-5`. Keep them in configuration, never hard-coded, because model choice changes faster than the code.

## 6. CV component choices

| Component | Recommendation | Status / evidence |
|---|---|---|
| Pose | **Keep MediaPipe PoseLandmarker** behind `PoseEstimator`: lite for the scan, full for dense windows. Heavy is 2.3× slower **[M]**; use it only if G shows full is not accurate enough at contact. | working, measured |
| Pose alternative | **RTMPose via ONNX Runtime** (`rtmlib`, Apache-2.0) with an RTMDet person detector. It is top-down and multi-person, which solves the "opponent is more prominent" risk, and its published accuracy is higher. Benchmark it in G before adopting it. | **[U]** in this project; speed claims so far come only from its authors |
| Avoid | YOLO-pose from Ultralytics: **AGPL-3.0**, a licence problem for a closed product. MoveNet: single-person, lower accuracy on fast limbs. | — |
| Player selection | Scan stage: follow the person nearest the camera, or the one the user tapped on the first frame (one tap on the phone, which costs nothing) | to implement |
| Racket | Not in M2. Use pose-only wrist and forearm proxies with explicit "racket not observed" flags. A custom racket detector needs a labelled dataset; that is a later milestone. | [U] |
| Ball | Not needed for FH/BH technique. TrackNet-class models need a GPU for practical speed. Defer. | [U] |
| Stroke detection | Heuristic on the 10 fps scan, refined at full rate. Later, a small classifier (GBDT or 1D-CNN on pose sequences) trained on the labelled clips from M1.11. | [U] |
| GPU | **Not required.** Every stage above runs on CPU at the costs shown. A GPU becomes worth considering only for ball tracking or heavy models. | — |

## 7. Device / browser responsibilities

- **Does:**
  - reads metadata
  - captures trim points (in/out) without re-encoding
  - offers a one-tap player selection on a thumbnail
  - shows a camera-placement checklist before recording
  - runs a presigned, resumable multipart upload
  - draws the skeleton on a canvas from per-window pose data
- **Does not:**
  - pose estimation
  - transcoding
  - frame extraction

  Reasons:
  - phone CPU and thermal limits would make the experience vary from phone to phone
  - WebCodecs has only "limited availability": no Firefox on Android, and Safari only from version 26
  - the results would not be reproducible across devices
- **Possible later progressive enhancement:** a WebCodecs thumbnail strip, used to pick trim points faster. It must never be required.
