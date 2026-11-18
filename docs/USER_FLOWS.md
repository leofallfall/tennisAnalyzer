# User Flows

Legend: ✅ works today · 🧪 layout with example data · ⏳ not built.

## 1. Upload a session ✅

1. Dashboard → **New analysis** → choose **Session Analysis**.
2. Drop/select one video. Optional title.
3. **Upload and start processing**: the file streams to the API, which runs ffprobe and
   validates it. Invalid files are rejected immediately with the list of problems
   (e.g. "Resolution 320x240 is below the minimum").
4. The analysis is created and queued; the user lands on the analysis page.

## 2. Analyse a session ✅ (ingestion) / ⏳ (strokes)

1. The worker picks up the job: probe → validate → proxy → frames → quality → pose →
   overlay. Progress and stage are shown live.
2. On completion: playable proxy, quality ratings with observability, pose summary,
   per-frame skeleton and joint table.
3. ⏳ Stroke detection, classification, per-stroke quality, aggregation.
4. On failure: error code and message, and a **Retry** button.

## 3. Review technical issues 🧪

1. Session report lists issues ranked by significance with occurrences / evaluable
   strokes, confidence, type (recurring, isolated, potentially significant, positive
   reference), evidence timestamps and limitations.
2. Filter Forehand / Backhand / Both.
3. Select one or more issues → open supporting strokes in Single Stroke Analysis.

## 4. Open a specific stroke ⏳

From an issue (or the stroke list), open a stroke: the single-stroke view loads the
stroke's time interval of the session proxy with its phases and measurements.

## 5. Perform deep analysis ✅ (player) / 🧪 (phases, metrics)

1. **New analysis → Single Stroke Analysis**; upload one or more videos.
2. With several videos the user must state: *same stroke from different angles* or
   *different strokes*, and may label each camera view.
3. Optional "technical aspect to investigate" (e.g. contact distance).
4. Frame-by-frame player: ←/→ one frame, Shift+←/→ ten frames, Space play/pause,
   0.1×–1× speed, skeleton toggle, timeline with phase track (example phases today).
5. Joint table for the current frame (real data). Metrics/feedback panels are examples.

## 6. Compare multiple executions ✅ (manual) / ⏳ (analysis)

Comparisons page: select up to three processed analyses and view them side by side,
each with its own frame controls and skeleton. ⏳ Alignment by phase, comparability
check, normalisation, difference highlighting, professional references.

## 7. Start an improvement path ⏳

From an issue or the AI Coach: create a path ("Improve backhand contact distance"),
attach issues, receive drills, link future analyses as evidence. Multiple paths in
parallel; closed only by the user.

## 8. Review progress ⏳

Progress page: per-issue frequency and metric trends across sessions, each change
marked as supported / insufficient evidence / possibly caused by recording conditions.
