# Product Requirements

Priority levels: **P0** = Milestone 1 (foundation), **P1** = MVP, **P2** = after MVP,
**P3** = later. Status reflects the code in this repository.

## 1. Video ingestion (P0)

| ID     | Requirement                                                                   | Status |
| ------ | ----------------------------------------------------------------------------- | ------ |
| VID-1  | Upload MP4/MOV/M4V/AVI/MKV/WebM videos via the web app                         | Done   |
| VID-2  | Validate container, codec, duration, resolution, frame rate, size             | Done   |
| VID-3  | Extract metadata (resolution, rotation, fps, VFR, codec, duration, frames)    | Done   |
| VID-4  | Normalise orientation, VFR and encoding into a browser-playable CFR proxy     | Done   |
| VID-5  | Extract frames with correct timestamps                                         | Done   |
| VID-6  | Report processing errors as structured, user-readable errors                   | Done   |
| VID-7  | Basic quality assessment (resolution, fps, blur, lighting, visibility)         | Done (heuristic) |
| VID-8  | Detect scene/camera changes in session videos                                  | P1, not started |

## 2. Session Analysis (P1)

1. Detect **all** candidate strokes; no fixed maximum.
2. Classify forehand / backhand (serve P2).
3. Evaluate quality and observability per stroke; exclude or flag unreliable strokes.
4. Run preliminary analysis on every stroke above the quality threshold.
5. Aggregate **all valid** measurements into session statistics.
6. Identify, rank and present recurring technical issues.
7. Each issue includes: description, occurrences, evaluable strokes, frequency,
   confidence, technical significance, supporting executions, timestamps, evidence
   frames and observability limitations.
8. Denominators are the number of strokes **where the specific metric is observable**.
9. Categorise: recurring issue, isolated anomaly, potentially significant problem,
   positive reference execution.
10. Filter by forehand / backhand / both.
11. User selects one or more issues and opens their supporting strokes in Single Stroke
    Analysis. AI may recommend which executions to inspect; the user decides.
12. No fixed number of "representative clips".

## 3. Single Stroke Analysis (P1)

1. Start from: a stroke selected in a session; an upload containing one stroke; a manual
   time interval; multiple camera angles of the same stroke; a technical question.
2. Multiple videos must be explicitly declared as **same stroke / different angles** or
   **different strokes**. Never inferred, never confused. *(API + UI: Done)*
3. Frame-by-frame playback, timeline, phase segmentation with adjustable boundaries.
   *(Playback/stepping: Done; segmentation: not started)*
4. Skeleton overlay, joint positions and trajectories. *(Per-frame skeleton + joint table: Done)*
5. Racket tracking and trajectory visualisation. *(P1/P2, not started)*
6. Biomechanical measurements, keyframes, evidence, issue descriptions.
7. Phases: preparation, backswing/loading, forward acceleration, contact,
   follow-through/recovery; adaptable to forehand and backhand.
8. Compare two or more executions (including three or more).

## 4. Biomechanical analysis (P1 → P3)

Body (pose, joint angles, trajectories, orientation, sequencing, kinetic chain, balance,
lower body loading, hip/trunk rotation, shoulder, elbow, arm extension, wrist where
observable); racket (detection, orientation, position, trajectory, preparation, swing,
contact, follow-through); ball and contact (detection/trajectory where feasible,
contact timing, height, distance to body, racket-ball relation); movement conditions
(stationary/moving, positions, angles, distances). Start with reliable 2D measurements;
see [BIOMECHANICS.md](BIOMECHANICS.md).

## 5. Professional comparison (P2)

Manual selection of professional reference; AI-recommended references; side-by-side
video, phase, skeleton and trajectory comparison; highlight meaningful differences;
**comparability check before any conclusion**; copyright-aware ingestion (only content
the project is licensed to use; no assumption that downloads/redistribution are allowed).

## 6. AI Coach (P2)

Access to player profile, dominant hand, historical sessions, stroke analyses, recurring
issues, improvement paths, observations, goals and coach notes. Explains conclusions with
evidence, suggests specific drills. Multiple concurrent improvement paths, open until the
user closes them; no mandatory duration, calendar or reminders. External coach notes are
hypotheses, not truth. No medical diagnosis, injury prediction or physical training plans.
See [AI_GUIDELINES.md](AI_GUIDELINES.md).

## 7. Longitudinal progress (P2)

Compare sessions over time using only observable and comparable measurements. Show
improved aspects, persistent issues, issues that became more frequent, which changes are
supported by sufficient evidence, and which may be caused by recording conditions.

## 8. User experience requirements

- Serious, minimal, technical visual design; excellent readability; no gamification.
- Responsive: desktop, tablet, mobile. *(Done)*
- Navigation: Dashboard, My Analyses, Progress, Comparisons, Improvement Paths,
  Player Profile. *(Done)*
- Placeholder data is always explicitly labelled as example data. *(Done)*
- Long processing is asynchronous with visible status and progress. *(Done)*
- Errors are specific and actionable (code + message). *(Done)*
- Confidence and observability are visible wherever a measurement is shown.
