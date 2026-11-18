"""Evidence Pack data model: what an AI analyst is allowed to see.

The pack is a directory of small JSON files plus a few JPEG frames. It is model- and
adapter-independent. It reuses the engine contracts (``Measurement``, ``StrokePhase``,
``QualityReport``, ``PoseSummary``) instead of defining parallel schemas.

Never in a pack: the video, the full ``pose.json``, credentials, absolute server paths.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from tennis_cv.biomechanics.measurement import Measurement
from tennis_cv.common import Observability, Reliability, StrictModel, TimeInterval
from tennis_cv.pose.models import PoseSummary
from tennis_cv.quality.assessment import QualityReport
from tennis_cv.strokes.base import StrokePhase, StrokeType

PACK_SCHEMA_VERSION = "1.0"
INSTRUCTIONS_VERSION = "ai-instructions/1.0"
MANIFEST = "manifest.json"
SESSION_METADATA = "session_metadata.json"
DATA_QUALITY = "data_quality.json"
STROKES = "strokes.json"
MEASUREMENTS = "measurements.json"
AGGREGATES = "aggregates.json"
ISSUE_CANDIDATES = "issues_candidates.json"
TIMESERIES = "stroke_timeseries.json"
INSTRUCTIONS = "claude_instructions.md"
RESULT_SCHEMA = "result_schema.json"
EVIDENCE_DIR = "evidence"


class PackKind(StrEnum):
    SESSION = "session"
    SINGLE_STROKE = "single_stroke"


class DataStatus(StrEnum):
    AVAILABLE = "available"
    NOT_IMPLEMENTED = "not_implemented"
    EMPTY = "empty"


# --- session_metadata.json -------------------------------------------------------


class VideoInfo(StrictModel):
    video_id: str
    original_filename: str
    camera_view: str = Field(description="Declared by the user; 'unknown' if not declared")
    duration_s: float | None
    average_fps: float | None
    frame_count: int | None
    width: int
    height: int
    orientation: str
    codec: str | None
    container: str | None
    analysis_timeline: str = "CFR proxy: timestamp = frame_index / proxy_fps"
    proxy_fps: float


class SessionMetadata(StrictModel):
    analysis_id: str
    title: str
    mode: str
    created_at: datetime
    stroke_filter: str
    focus: str | None
    video_relation: str
    videos: list[VideoInfo]
    recording_conditions: dict[str, str | None] = Field(
        default_factory=dict, description="Only what the user declared; nothing inferred"
    )


# --- data_quality.json -------------------------------------------------------------


class VideoQuality(StrictModel):
    video_id: str
    quality: QualityReport
    pose_estimator: str
    pose_model: str
    pose: PoseSummary
    validation_issues: list[str] = Field(default_factory=list)


class CheckStatus(StrictModel):
    status: DataStatus
    detail: str


class DataQuality(StrictModel):
    videos: list[VideoQuality]
    player_visibility: str = Field(description="Derived from the pose detection rate only")
    occlusions: CheckStatus
    tracking: CheckStatus
    measurement_observability: CheckStatus
    limitations: list[str]


# --- strokes.json / measurements.json ----------------------------------------------


class StrokeSource(StrEnum):
    DETECTED = "detected"
    USER_INTERVAL = "user_interval"


class StrokeRecord(StrictModel):
    id: str
    video_id: str
    source: StrokeSource = StrokeSource.DETECTED
    interval: TimeInterval
    estimated_contact_s: float | None = None
    stroke_type: StrokeType = StrokeType.UNKNOWN
    type_confidence: float | None = Field(default=None, ge=0, le=1)
    type_declared_by_user: bool = False
    reliability: Reliability
    phases: list[StrokePhase] = Field(default_factory=list)
    evidence_image_ids: list[str] = Field(default_factory=list)


class StrokesFile(StrictModel):
    status: DataStatus
    reason: str | None = None
    strokes: list[StrokeRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status(self) -> StrokesFile:
        if self.status is not DataStatus.AVAILABLE and self.strokes:
            raise ValueError("strokes listed but status is not 'available'")
        return self


class MeasurementRecord(StrictModel):
    id: str
    video_id: str
    measurement: Measurement

    @model_validator(mode="after")
    def _has_stroke(self) -> MeasurementRecord:
        if self.measurement.stroke_id is None:
            raise ValueError("pack measurements must reference a stroke_id")
        return self


class MeasurementsFile(StrictModel):
    status: DataStatus
    reason: str | None = None
    measurements: list[MeasurementRecord] = Field(default_factory=list)


# --- aggregates.json / issues_candidates.json -------------------------------------


class MetricAggregate(StrictModel):
    metric_id: str
    stroke_type: StrokeType
    unit: str
    strokes_with_record: int
    observable: int = Field(description="Denominator for every statistic below")
    not_observable: int
    median: float | None
    q1: float | None
    q3: float | None
    min: float | None
    max: float | None


class Aggregates(StrictModel):
    status: DataStatus
    strokes_by_type: dict[str, int]
    metrics: list[MetricAggregate] = Field(default_factory=list)
    denominator_rule: str = (
        "Every statistic uses only the strokes where that metric is observable "
        "(observable or partially_observable)."
    )


class IssueCandidate(StrictModel):
    id: str
    metric_id: str | None
    stroke_type: StrokeType
    description: str
    rule: str = Field(description="Deterministic engine rule that raised the candidate")
    stroke_ids: list[str]
    occurrences: int = Field(ge=0)
    evaluable: int = Field(ge=0)


class IssueCandidatesFile(StrictModel):
    status: DataStatus
    reason: str | None = None
    candidates: list[IssueCandidate] = Field(default_factory=list)


# --- stroke_timeseries.json (single stroke) ----------------------------------------


class AngleSample(StrictModel):
    value_deg: float | None
    observability: Observability
    confidence: float | None = None


class JointSample(StrictModel):
    x: float
    y: float
    visibility: float | None


class TimeseriesSample(StrictModel):
    frame_index: int
    timestamp_s: float
    pose_detected: bool
    angles: dict[str, AngleSample] = Field(
        description="Key = '<metric_id>:<side>', e.g. 'elbow_angle_projected:right'"
    )
    joints: dict[str, JointSample] = Field(
        default_factory=dict, description="Normalised image coordinates of key joints"
    )


class StrokeTimeseries(StrictModel):
    stroke_id: str
    video_id: str
    fps: float
    frame_stride: int
    interval: TimeInterval
    method: str
    limitations: list[str]
    samples: list[TimeseriesSample]


# --- manifest.json -------------------------------------------------------------------


class ImageRole(StrEnum):
    OVERVIEW = "overview"
    PREPARATION = "preparation"
    CONTACT = "contact"
    FOLLOW_THROUGH = "follow_through"
    SEQUENCE = "sequence"


class ImageRef(StrictModel):
    id: str
    path: str = Field(description="Relative to the pack directory")
    video_id: str
    frame_index: int
    timestamp_s: float
    stroke_id: str | None = None
    role: ImageRole
    skeleton_drawn: bool
    width: int
    height: int


class PackFile(StrictModel):
    path: str
    sha256: str
    bytes: int


class MotivatingIssue(StrictModel):
    """The session-level issue that led the user to request a single-stroke analysis."""

    source_ai_analysis_id: str
    issue_id: str
    name: str
    description: str


class SingleStrokeTarget(StrictModel):
    video_id: str
    stroke_id: str
    source: StrokeSource
    interval: TimeInterval
    motivating_issue: MotivatingIssue | None = None


class Manifest(StrictModel):
    pack_schema_version: Literal["1.0"] = "1.0"
    pack_id: str
    kind: PackKind
    analysis_id: str
    created_at: datetime
    generator: str
    instructions_version: str = INSTRUCTIONS_VERSION
    result_schema_version: str
    result_file: str
    language: str
    data_status: dict[str, DataStatus]
    images: list[ImageRef]
    files: list[PackFile] = Field(description="Integrity list: every file except manifest")
    target: SingleStrokeTarget | None = None
    privacy: str = "Contains no video, no full pose sequence, no credentials and no absolute paths."
