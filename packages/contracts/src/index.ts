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
  artifacts: Record<string, string>
  timings: StepTiming[]
  total_duration_ms: number
  not_performed: string[]
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
  artifacts: Partial<Record<'proxy' | 'pose' | 'overlay' | 'report', string>>
}

export interface AnalysisResults {
  kind: string
  videos: Record<string, ProcessingReport>
  note: string
}

export interface Analysis extends AnalysisStatusInfo {
  mode: AnalysisMode
  title: string
  video_relation: VideoRelation
  stroke_filter: StrokeFilter
  focus: string | null
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
}

export interface SystemInfo {
  api_version: string
  engine_version: string
  python_version: string
  platform: string
  database: string
  storage_dir: string
  tools: ToolStatus[]
  pose_model: ToolStatus
  capabilities: Capability[]
}
