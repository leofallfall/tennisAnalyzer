/**
 * TypeScript mirror of the backend contracts.
 *
 * Source of truth: Pydantic models in engine/tennis_cv and apps/api/tennis_api/schemas.py.
 * JSON Schemas exported from Python live in ../schema (scripts/export_contracts.py).
 * Keep these types in sync when the Python models change.
 */

export type Observability =
  'observable' | 'partially_observable' | 'not_observable' | 'not_evaluated'
export type ModuleStatus = 'implemented' | 'experimental' | 'not_implemented'
export type Rating = 'good' | 'acceptable' | 'poor' | 'unknown'

// ---- Engine contracts -------------------------------------------------------

export interface VideoMetadata {
  path: string
  container: string | null
  file_size_bytes: number | null
  duration_s: number | null
  codec: string | null
  pixel_format: string | null
  coded_width: number
  coded_height: number
  rotation_deg: number
  display_width: number
  display_height: number
  nominal_fps: number | null
  average_fps: number | null
  is_variable_frame_rate: boolean
  frame_count: number | null
  bit_rate: number | null
  has_audio: boolean
  orientation?: 'landscape' | 'portrait' | 'square'
}

export interface ValidationIssue {
  code: string
  severity: 'error' | 'warning'
  message: string
}

export interface ValidationResult {
  is_valid: boolean
  issues: ValidationIssue[]
}

export interface Assessment {
  rating: Rating
  value: number | null
  unit: string | null
  detail: string
}

export interface QualityReport {
  resolution: Assessment
  frame_rate: Assessment
  blur: Assessment
  lighting: Assessment
  visibility: Assessment
  frames_sampled: number
  blurry_frame_fraction: number | null
  poorly_lit_frame_fraction: number | null
  observability: Observability
  notes: string[]
  method: string
}

export interface Keypoint {
  name: string
  x: number
  y: number
  z: number | null
  visibility: number | null
  presence: number | null
}

export interface PoseFrame {
  frame_index: number
  timestamp_s: number
  detected: boolean
  keypoints: Keypoint[]
}

export interface PoseSequence {
  estimator: string
  model: string
  image_width: number
  image_height: number
  fps: number
  frame_stride: number
  frames: PoseFrame[]
  limitations: string[]
}

export interface PoseSummary {
  frames_analysed: number
  frames_with_pose: number
  detection_rate: number
  mean_keypoint_visibility: Record<string, number | null>
  processing_time_ms: number | null
  ms_per_frame: number | null
}

export interface PreprocessResult {
  output_path: string
  metadata: VideoMetadata
  applied_rotation_deg: number
  resampled_to_cfr: boolean
  scaled: boolean
  duration_ms: number
}

export interface StepTiming {
  step: string
  duration_ms: number
}

export interface ProcessingReport {
  engine_version: string
  source_metadata: VideoMetadata
  validation: ValidationResult
  preprocess: PreprocessResult
  quality: QualityReport
  pose_estimator: string
  pose_model: string
  pose_summary: PoseSummary
  /** Proxy -> original correspondence (frame-exact, time-approximate). Per-frame map in
   * the `timemap` artifact. Absent in reports made before M1.10. */
  time_mapping?: TimeMapSummary | null
  artifacts: Record<string, string>
  timings: StepTiming[]
  total_duration_ms: number
  not_performed: string[]
}

/** engine/tennis_cv/video/timemap.py — never an exact time synchronisation. */
export interface TimeMapSummary {
  method: string
  exact_time_sync: false
  source_start_pts_s: number
  proxy_fps: number
  source_frame_count: number
  proxy_frame_count: number
  duplicated_frames: number
  dropped_frames: number
  max_abs_error_ms: number
  mean_abs_error_ms: number
  limitations: string[]
}

export interface TimeMap {
  summary: TimeMapSummary
  /** Source frame times relative to the first frame, presentation order. */
  source_times_s: number[]
  /** Proxy frame k shows source frame proxy_to_source[k]. */
  proxy_to_source: number[]
}

export interface TimeInterval {
  start_s: number
  end_s: number
}

/** Every reported number has this shape. value is null when not observable. */
export interface Measurement {
  metric_id: string
  value: number | null
  unit: string
  interval: TimeInterval | null
  frame_index: number | null
  stroke_id: string | null
  body_part: string | null
  confidence: number | null
  observability: Observability
  method: string
  limitations: string[]
}

// ---- API contracts ----------------------------------------------------------

export type AnalysisMode = 'session' | 'single_stroke'
export type AnalysisStatus = 'created' | 'queued' | 'running' | 'completed' | 'failed'
export type VideoRelation = 'single' | 'same_stroke_multi_angle' | 'different_strokes'
export type StrokeFilter = 'forehand' | 'backhand' | 'both'
/** Declared by the user, never inferred (M1.10). null = not provided. */
export type DominantHand = 'right' | 'left'
export type BackhandStyle = 'one_handed' | 'two_handed'
export type ViewAngle =
  'side_forehand' | 'side_backhand' | 'front' | 'behind' | 'diagonal' | 'unknown'

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> }
}

export interface Video {
  id: string
  original_filename: string
  content_type: string | null
  size_bytes: number
  sha256: string
  metadata: VideoMetadata
  validation: ValidationResult
  created_at: string
  file_url: string
}

export interface ErrorInfo {
  code: string
  message: string
  details: Record<string, unknown>
}

export interface AnalysisStatusInfo {
  id: string
  status: AnalysisStatus
  stage: string | null
  progress: number
  error: ErrorInfo | null
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export interface AnalysisVideo {
  video_id: string
  position: number
  camera_view: ViewAngle
  original_filename: string
  file_url: string
  artifacts: Partial<
    Record<'proxy' | 'pose' | 'overlay' | 'report' | 'timemap' | 'strokes', string>
  >
  /** V1 strokes view; null until strokes.json exists (apps/api strokes_view.py). */
  strokes_url?: string | null
  stroke_detection?: StrokeDetectionSummary | null
}

/** worker/tennis_worker/jobs/stroke_detection.py — counts, never rates. */
export interface StrokeDetectionSummary {
  status: 'completed' | 'failed' | 'not_available'
  algorithm_version?: string
  experimental?: boolean
  events?: { accepted: number; uncertain: number; rejected: number }
  not_assessable_stretches?: number
  error?: { code: string; message: string; [k: string]: unknown }
  reason?: string
}

export interface AnalysisResults {
  kind: string
  videos: Record<string, ProcessingReport>
  strokes?: Record<string, StrokeDetectionSummary>
  note: string
}

/** Personal V1 states (engine/tennis_cv/strokes/states.py, ADR-025/027). */
export type StrokeState = 'flagged' | 'to_check' | 'discarded'

export type RegionVisibilityState = 'good' | 'low_but_usable' | 'not_observable'

/** How well one body region was seen around the event; apart from state and reasons. */
export interface RegionVisibility {
  region: 'torso' | 'left_wrist' | 'right_wrist' | 'legs'
  label: string
  state: RegionVisibilityState
  state_label: string
  /** Region used by the event (reliability.regions_used). */
  used: boolean
  reasons: string[]
}

/** apps/api/tennis_api/schemas.py StrokeEventOut. Times on the proxy timeline. */
export interface StrokeEvent {
  id: string
  state: StrokeState
  status: 'accepted' | 'uncertain' | 'rejected'
  label: string
  /** null when the wrist is undetermined: use the window. */
  time_s: number | null
  frame: number | null
  window_start_s: number
  window_end_s: number
  reasons: string[]
  reason_codes: string[]
  /** Four regions, fixed order: torso, left wrist, right wrist, legs. */
  regions: RegionVisibility[]
  /** The user's correction of this event, if any (V1 step 5, ADR-031). */
  review?: ReviewEntry | null
}

export type ReviewStrokeType = 'forehand' | 'backhand' | 'other'
export type ImpactVisibility = 'full' | 'partial' | 'none'
export type NotStrokeReason =
  'duplicate' | 'not_a_stroke' | 'other_person' | 'out_of_frame' | 'other'
export type ReviewAction = 'confirmed' | 'moved' | 'deleted' | 'added' | 'detached'

/** apps/api/tennis_api/schemas.py ReviewEntryOut (strokes_review.json). The user's frame
 * is the truth; the detector output is never changed. */
export interface ReviewEntry {
  id: string
  kind: 'stroke' | 'not_stroke'
  action: ReviewAction
  action_label: string
  frame: number
  time_s: number
  stroke_type: ReviewStrokeType | null
  stroke_type_label: string | null
  visibility: ImpactVisibility | null
  visibility_label: string | null
  reason: NotStrokeReason | null
  reason_label: string | null
  note: string
  /** Detector event it was made on; null = a stroke added by the user. */
  event_id: string | null
  event_frame: number | null
}

export interface ReviewedStretch {
  id: string
  start_frame: number
  end_frame: number
  start_s: number
  end_s: number
}

/** ReviewEntryIn: with event_id = confirm / move / delete that event; without = add. */
export interface ReviewEntryCreate {
  event_id?: string | null
  kind?: 'stroke' | 'not_stroke'
  frame?: number | null
  stroke_type?: ReviewStrokeType | null
  visibility?: ImpactVisibility | null
  reason?: NotStrokeReason | null
  note?: string
}

/** ReviewEntryUpdate: replaces the editable fields (the event link never changes). */
export interface ReviewEntryUpdate {
  kind: 'stroke' | 'not_stroke'
  frame: number
  stroke_type?: ReviewStrokeType | null
  visibility?: ImpactVisibility | null
  reason?: NotStrokeReason | null
  note?: string
}

/** Insufficient evidence to detect strokes, NOT absence of strokes. */
export interface NotAssessableStretch {
  state: 'not_assessable'
  label: string
  start_s: number
  end_s: number
  start_frame: number
  end_frame: number
  regions: string[]
  reason: string
}

export interface StrokesView {
  analysis_id: string
  video_id: string
  algorithm: string
  algorithm_version: string
  experimental: boolean
  fps: number
  provisional_params: Record<string, unknown>
  note: string
  events: StrokeEvent[]
  not_assessable: NotAssessableStretch[]
  /** Proxy frames in pose.json (valid frames 0 .. n-1). */
  frame_count: number
  /** Strokes added by the user, and corrections whose event changed after a new detection. */
  my_strokes: ReviewEntry[]
  reviewed_stretches: ReviewedStretch[]
}

export interface Analysis extends AnalysisStatusInfo {
  mode: AnalysisMode
  title: string
  video_relation: VideoRelation
  stroke_filter: StrokeFilter
  focus: string | null
  dominant_hand: DominantHand | null
  backhand: BackhandStyle | null
  created_at: string
  videos: AnalysisVideo[]
  results: AnalysisResults | null
}

export interface AnalysisSummary extends AnalysisStatusInfo {
  mode: AnalysisMode
  title: string
  video_count: number
  created_at: string
}

export interface AnalysisCreate {
  mode: AnalysisMode
  title?: string
  videos: { video_id: string; camera_view?: ViewAngle }[]
  video_relation?: VideoRelation
  stroke_filter?: StrokeFilter
  focus?: string
  dominant_hand?: DominantHand | null
  backhand?: BackhandStyle | null
}

export interface Capability {
  module: string
  status: ModuleStatus
  description: string
}

export interface ToolStatus {
  name: string
  available: boolean
  version: string | null
  /** Why the tool is unavailable and how to fix it. */
  detail?: string | null
}

export interface SystemInfo {
  api_version: string
  engine_version: string
  python_version: string
  platform: string
  database: string
  /** The storage path itself is not exposed (M1.10). */
  storage_writable: boolean
  tools: ToolStatus[]
  pose_model: ToolStatus
  /** MediaPipe really loads (native libraries + model), not just imports. */
  pose_runtime: ToolStatus
  capabilities: Capability[]
}

// ---- Personal AI analysis (apps/api/tennis_api/ai) -------------------------------
// Mirrors tennis_api.ai.result.AnalysisResult and tennis_api.schemas.AiAnalysisOut.

export type AiAnalysisKind = 'session' | 'single_stroke'
export type AiAnalysisState = 'awaiting_result' | 'imported' | 'invalid'
export type IssueStrokeType = 'forehand' | 'backhand' | 'both' | 'unknown'
export type IssueNature = 'technical_error' | 'stylistic_variation' | 'uncertain'
export type Relevance = 'high' | 'medium' | 'low'
export type EvidenceKind =
  'image' | 'measurement' | 'stroke' | 'aggregate' | 'data_quality' | 'timeseries'

export interface EvidenceRef {
  kind: EvidenceKind
  ref: string
  note?: string | null
}

export interface Exercise {
  name: string
  description: string
  cue?: string | null
  dosage?: string | null
  how_to_verify?: string | null
}

export interface AiIssue {
  id: string
  priority: number
  name: string
  description: string
  stroke_type: IssueStrokeType
  nature: IssueNature
  occurrences: number
  evaluable_strokes: number
  percentage: number
  relevance: Relevance
  confidence: number
  stroke_ids: string[]
  timestamps_s: number[]
  observations: string[]
  evidence: EvidenceRef[]
  biomechanical_explanation: string
  alternative_explanations: string[]
  recommended_exercises: Exercise[]
}

export interface AiPositiveExecution {
  stroke_id: string
  stroke_type: IssueStrokeType
  timestamp_s: number | null
  description: string
  evidence: EvidenceRef[]
}

export interface AiRecommendation {
  priority: number
  title: string
  rationale: string
  addresses_issue_ids: string[]
  exercises: Exercise[]
}

export interface AiPhaseAnalysis {
  phase: string
  observed: string[]
  hypotheses: string[]
  not_observable: string[]
  evidence: EvidenceRef[]
}

export interface AiAnalysisResult {
  schema_version: '1.0'
  kind: AiAnalysisKind
  session_id: string
  pack_id: string
  analysis_version: string
  adapter: string
  model: string
  generated_at: string
  language: string
  summary: string
  evaluated_strokes: {
    detected: number
    evaluable: number
    forehand: number
    backhand: number
    unknown: number
  }
  by_stroke_type: {
    stroke_type: 'forehand' | 'backhand'
    detected: number
    evaluable: number
    summary: string
    issue_ids: string[]
  }[]
  issues: AiIssue[]
  positive_executions: AiPositiveExecution[]
  recommendations: AiRecommendation[]
  limitations: string[]
  confidence: number
  insufficient_evidence: boolean
  target: { video_id: string; stroke_id: string; interval: TimeInterval } | null
  phases: AiPhaseAnalysis[]
}

export interface AiHandoff {
  adapter: string
  interactive: boolean
  pack_path: string
  command: string
  result_path: string
  steps: string[]
}

export interface AiValidationProblem {
  code: string
  message: string
  location: string | null
}

export interface AiEvidenceImage {
  id: string
  path: string
  video_id: string
  frame_index: number
  timestamp_s: number
  stroke_id: string | null
  role: 'overview' | 'preparation' | 'contact' | 'follow_through' | 'sequence'
  skeleton_drawn: boolean
  width: number
  height: number
  url: string
}

/** verified: manifest matches the hash recorded at creation. legacy: created before
 * integrity protection (cannot be imported; prepare a new pack). */
export type AiPackStatus = 'verified' | 'modified' | 'missing' | 'legacy'

/** One imported version of an AI result (append-only history). */
export interface AiImport {
  id: string
  version: number
  imported_at: string
  origin: 'import' | 'legacy_migration'
  adapter: string
  /** Self-reported by the analyst; not verified by the app. */
  model: string | null
  pack_id: string
  result_sha256: string | null
  result: AiAnalysisResult
  validation_warnings: AiValidationProblem[]
}

export interface AiAnalysis {
  id: string
  analysis_id: string
  kind: AiAnalysisKind
  pack_id: string
  pack_path: string
  adapter: string
  status: AiAnalysisState
  pack_status: AiPackStatus
  target: {
    video_id: string
    stroke_id: string
    source: 'detected' | 'user_interval'
    interval: TimeInterval
    motivating_issue: {
      issue_id: string
      name: string
      source_import_id?: string | null
    } | null
  } | null
  data_status: Record<string, 'available' | 'not_implemented' | 'empty'>
  /** Latest imported version (same as imports[imports.length - 1].result). */
  result: AiAnalysisResult | null
  model: string | null
  validation_errors: AiValidationProblem[]
  validation_warnings: AiValidationProblem[]
  import_attempts: number
  created_at: string
  imported_at: string | null
  last_rejected_at: string | null
  /** Every imported version, oldest first. */
  imports: AiImport[]
  handoff: AiHandoff | null
  images: AiEvidenceImage[]
}

export interface SingleStrokeAiRequest {
  stroke_id?: string
  video_id?: string
  start_s?: number
  end_s?: number
  declared_stroke_type?: 'forehand' | 'backhand' | 'unknown'
  source_ai_analysis_id?: string
  source_import_id?: string
  issue_id?: string
}
