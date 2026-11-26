# Data Model

Legend: **[M1]** implemented as a database table now; others are designed and will be
added with the milestone that needs them (via Alembic migrations once PostgreSQL-ready
migrations are introduced).

## Entity relationship overview

```
User 1─1 PlayerProfile
User 1─* Video
User 1─* Analysis ─*─* Video          (via AnalysisVideo: position, camera_view)   [M1]
Analysis 1─* ProcessingJob                                                         [M1]
Analysis(session) 1─1 Session 1─* Stroke
Stroke 1─* StrokeView *─1 Video       (same stroke, several camera angles)
StrokeView 1─1 CameraView
Stroke 1─* StrokePhase
Video 1─* PoseFrame                   (per analysed frame of a video)
Stroke 1─* BiomechanicalMetric        (Measurement records)
TechnicalIssue *─* Stroke             (via Evidence)
Evidence *─1 Stroke, *─1 Video, frame range, metric refs
ImprovementPath *─* TechnicalIssue
CoachNote *─1 User, optional → Stroke / Analysis / ImprovementPath
ProfessionalReference 1─* Video (licensed), 1─* Stroke
```

## Entities

### User
`id, email, display_name, created_at`. Single implicit local user in M1.

### PlayerProfile
`user_id, dominant_hand (right|left), backhand (one_handed|two_handed), level,
years_playing, height_cm?, goals, updated_at`. Dominant hand is required to classify
forehand vs backhand. *(M1: stored in browser localStorage only.)*

### Video **[M1]**
`id, original_filename, stored_path (relative), content_type, size_bytes, sha256,
video_metadata (JSON VideoMetadata), validation (JSON), created_at`.
Future: `user_id, recorded_at, source (user|professional_reference), licence`.

### Analysis **[M1]**
`id, mode (session|single_stroke), title, video_relation (single|
same_stroke_multi_angle|different_strokes), stroke_filter, focus, dominant_hand
(right|left|NULL), backhand (one_handed|two_handed|NULL), status (created|queued|
running|completed|failed), stage, progress, error_code, error_message, error_details,
results (JSON), created_at, updated_at, started_at, finished_at`.

`dominant_hand` and `backhand` (M1.10, migration `2026_11_19_analysis_player_handedness`)
are declared by the user when creating the analysis (pre-filled from the browser profile)
and are NULL when not provided: never inferred from video. They are not sent to the AI
Evidence Pack yet. The player profile itself still lives only in the browser.

All timestamps are stored and returned as UTC (`UTCDateTime` column type, M1.10): API
values always carry an offset (`+00:00`), also on SQLite, which keeps no timezone.
`error_details` never holds tracebacks or server paths (M1.10): `internal_error` stores
`{error_id, exception}`; the traceback is in the worker log.

### AnalysisVideo **[M1]**
`analysis_id, video_id, position, camera_view`. Associates several videos with one
analysis. Their meaning is given by `Analysis.video_relation`, which the user must
declare explicitly whenever more than one video is attached.

### AiAnalysis **[implemented, experimental]**
One AI interpretation request, i.e. one Evidence Pack: `analysis_id`, `kind` (session |
single_stroke), `pack_id`, `pack_dir` (relative to storage), `adapter`, `status`
(awaiting_result | imported | invalid), `target`, `data_status`, **`manifest_sha256`**
(SHA-256 of `manifest.json` recorded at creation: the integrity anchor; NULL = legacy pack
created before the anchor existed, which cannot be imported), `validation_errors/warnings`
and `last_rejected_at` (latest import attempt), `import_attempts`, `created_at`.
Legacy columns `result`, `model`, `imported_at` are no longer written (copied to
`AiAnalysisImport` by the 2026-11-18 migration). Never modifies `Analysis.results`.
See `AI_ANALYSIS.md`.

### AiAnalysisImport **[implemented, experimental]**
Append-only history of validated AI results: one row per valid import (`version` 1, 2, …
unique per AiAnalysis), with `ai_analysis_id`, `analysis_id`, `kind`, `pack_id`,
`manifest_sha256`, `result_sha256` (hash of the imported `analysis_result.json`),
`adapter`, `model` (self-reported), `result`, `validation_warnings`, `origin` (import |
legacy_migration), `imported_at`. The app never updates or deletes these rows; an invalid
import attempt does not touch them.

### SchemaMigration
`schema_migrations(id, applied_at)`: in-place migrations applied by the API at startup
(`tennis_api.migrations`; additive only, no Alembic yet).

### ProcessingJob **[M1]**
`id, analysis_id, kind, status, attempts, worker_id, created_at, started_at,
finished_at, error`. Durable work queue consumed by the worker.

### Session
`id, analysis_id, video_id, scene_segments (JSON), stroke_count_candidates,
stroke_count_observable, summary (JSON)`.

### Stroke
`id, session_id?, analysis_id, stroke_type (forehand|backhand|serve|unknown),
type_confidence, interval (start_s,end_s on the primary video), estimated_contact_s,
movement_condition (stationary|moving), quality (JSON), observability, excluded_reason?`.

### StrokesReview **[implemented, personal V1 step 5, ADR-031]**
A file, not a table: `strokes_review.json` (schema `tpa.strokes_review` 1.0,
`packages/contracts/schema/strokes_review_v1.schema.json`) next to `strokes.json` in the
analysis video folder. Never written by the detector or the worker.
`entries[]`: `id, kind (stroke|not_stroke), frame (proxy frame, the user's truth),
stroke_type (forehand|backhand|other|null), visibility (full|partial|none|null: how well
the user sees the impact), reason (duplicate|not_a_stroke|other_person|out_of_frame|other,
only for not_stroke), note, event? {event_id, event_frame, algorithm_version}, updated_at`.
`kind = not_stroke` exists only for a detector event. One correction per event, one stroke
per frame. `reviewed_stretches[]`: `id, start_frame, end_frame (included), updated_at`, never
overlapping. Shown actions: confirmed (same frame as the event), moved, deleted, added
(no event), detached (the event is gone or moved by more than 3 frames after a new
detection). Exported as `labels.csv` (player `A`) + `annotated.csv` (`all`, `A`).

### StrokeView *(multi-angle)*
`stroke_id, video_id, interval_in_video, time_offset_s, camera_view_id`.
**One stroke, many videos**: each angle is a StrokeView pointing at the same Stroke, with
the time offset used to synchronise views. Different strokes are different Stroke rows —
the two cases can never be confused.

### CameraView
`id, view_angle (side_forehand|side_backhand|front|behind|diagonal|unknown),
camera_height, approx_distance_m, is_static, declared_by_user`.

### StrokePhase
`stroke_id, name (preparation|backswing|forward_swing|contact|follow_through),
start_s, end_s, boundary_method, user_adjusted, confidence, observability`.

### PoseFrame
`video_id, analysis_id, frame_index, timestamp_s, detected, estimator, model,
keypoints (JSON: name,x,y,z,visibility,presence)`. *(M1: stored as `pose.json` artifact;
moved to a table or columnar file when queried per stroke.)*

### BiomechanicalMetric (Measurement)
`id, stroke_id, metric_id, value?, unit, start_s?, end_s?, frame_index?, body_part,
confidence?, observability, method, limitations (JSON)`. Value is null iff not observable.

### TechnicalIssue
`id, analysis_id, issue_type, stroke_type, title, description, category (recurring|
isolated|potentially_significant|positive_reference), nature (technical_error|
stylistic_variation|uncertain), occurrences, evaluable_count, frequency, confidence,
significance, limitations, created_by (engine|ai|user)`. `evaluable_count` is the number
of strokes where the underlying metric is observable — the only valid denominator.

### Evidence
`id, issue_id, stroke_id, video_id, start_s, end_s, keyframe_indices, metric_ids, note`.

### ImprovementPath
`id, user_id, title, status (open|closed), created_at, closed_at, issue_ids, drills (JSON),
evidence_analysis_ids`. No due dates by design.

### CoachNote
`id, user_id, author (user|external_coach), text, created_at, links (stroke/analysis/path)`.
Treated as hypotheses by the AI Coach.

### ProfessionalReference
`id, player_name, stroke_type, video_id, licence, source_url, usage_restrictions,
camera_view_id, notes`. Only content with a known licence is ingested.
