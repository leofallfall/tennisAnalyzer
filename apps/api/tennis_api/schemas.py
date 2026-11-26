"""Pydantic request/response schemas (the API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tennis_api.models import (
    AnalysisMode,
    AnalysisStatus,
    BackhandStyle,
    DominantHand,
    StrokeFilter,
    VideoRelation,
)
from tennis_cv.capabilities import Capability
from tennis_cv.comparison.base import ViewAngle
from tennis_cv.strokes.base import StrokeType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(ApiModel):
    status: str
    version: str


class ToolStatus(ApiModel):
    name: str
    available: bool
    version: str | None = None
    detail: str | None = Field(
        default=None, description="Why the tool is unavailable and how to fix it"
    )


class SystemInfo(ApiModel):
    api_version: str
    engine_version: str
    python_version: str
    platform: str
    database: str
    storage_writable: bool = Field(
        description="Whether the API can write to its storage directory. The path itself "
        "is not exposed (M1.10)."
    )
    tools: list[ToolStatus]
    pose_model: ToolStatus
    pose_runtime: ToolStatus = Field(
        description="MediaPipe really loads (native libraries + model), not just imports"
    )
    capabilities: list[Capability]


class VideoOut(ApiModel):
    id: str
    original_filename: str
    content_type: str | None
    size_bytes: int
    sha256: str
    metadata: dict[str, Any] = Field(validation_alias="video_metadata")
    validation: dict[str, Any]
    created_at: datetime
    file_url: str = ""

    @model_validator(mode="after")
    def _url(self) -> VideoOut:
        self.file_url = f"/api/videos/{self.id}/file"
        return self


class AnalysisVideoIn(ApiModel):
    video_id: str
    camera_view: ViewAngle = ViewAngle.UNKNOWN


class AnalysisCreate(ApiModel):
    mode: AnalysisMode
    title: str | None = Field(default=None, max_length=256)
    videos: list[AnalysisVideoIn] = Field(min_length=1, max_length=8)
    video_relation: VideoRelation | None = Field(
        default=None,
        description="Required when more than one video is attached: same stroke from "
        "different angles vs. different strokes. Never inferred.",
    )
    stroke_filter: StrokeFilter = StrokeFilter.BOTH
    focus: str | None = Field(default=None, max_length=2000)
    dominant_hand: DominantHand | None = Field(
        default=None,
        description="Declared by the user (e.g. from the player profile). Null when not "
        "provided; never inferred.",
    )
    backhand: BackhandStyle | None = Field(
        default=None, description="Declared by the user. Null when not provided."
    )

    @model_validator(mode="after")
    def _relation(self) -> AnalysisCreate:
        ids = [v.video_id for v in self.videos]
        if len(set(ids)) != len(ids):
            raise ValueError("The same video is attached twice")
        if len(self.videos) == 1:
            if self.video_relation not in (None, VideoRelation.SINGLE):
                raise ValueError("A single video must use video_relation 'single'")
            self.video_relation = VideoRelation.SINGLE
        else:
            if self.video_relation in (None, VideoRelation.SINGLE):
                raise ValueError(
                    "With several videos, video_relation must be "
                    "'same_stroke_multi_angle' or 'different_strokes'"
                )
            if self.mode is AnalysisMode.SESSION:
                raise ValueError("Session analysis accepts exactly one video for now")
        return self


class AnalysisVideoOut(ApiModel):
    video_id: str
    position: int
    camera_view: str
    original_filename: str
    file_url: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    strokes_url: str | None = Field(
        default=None, description="V1 strokes view; set only when strokes.json exists"
    )
    stroke_detection: dict[str, Any] | None = Field(
        default=None,
        description="Summary of the last stroke detection run (status, counts, error)",
    )


class ErrorInfo(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AnalysisStatusOut(ApiModel):
    id: str
    status: AnalysisStatus
    stage: str | None
    progress: float
    error: ErrorInfo | None = None
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class AnalysisOut(AnalysisStatusOut):
    mode: AnalysisMode
    title: str
    video_relation: VideoRelation
    stroke_filter: StrokeFilter
    focus: str | None
    dominant_hand: DominantHand | None = None
    backhand: BackhandStyle | None = None
    created_at: datetime
    videos: list[AnalysisVideoOut]
    results: dict[str, Any] | None = Field(
        default=None,
        description="Per-video processing reports keyed by video id (ingestion, quality, "
        "pose) and, under 'strokes', the experimental stroke detection summary per video. "
        "No biomechanical analysis yet.",
    )


class AnalysisSummaryOut(AnalysisStatusOut):
    mode: AnalysisMode
    title: str
    video_count: int
    created_at: datetime


class SingleStrokeAiRequest(ApiModel):
    """Pick the stroke for a Single Stroke AI analysis.

    Either ``stroke_id`` (a stroke listed in a session Evidence Pack) or an interval on
    one video. Without both, a single-stroke upload is analysed as a whole clip.
    """

    stroke_id: str | None = None
    video_id: str | None = None
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    declared_stroke_type: StrokeType | None = Field(
        default=None, description="Stroke type stated by the user (never inferred)"
    )
    source_ai_analysis_id: str | None = None
    source_import_id: str | None = Field(
        default=None, description="Imported version the issue comes from (default: latest)"
    )
    issue_id: str | None = None

    @model_validator(mode="after")
    def _choice(self) -> SingleStrokeAiRequest:
        if self.stroke_id and (self.start_s is not None or self.end_s is not None):
            raise ValueError("Give either stroke_id or start_s/end_s, not both")
        if (self.start_s is None) != (self.end_s is None):
            raise ValueError("start_s and end_s must be given together")
        if self.start_s is not None and self.end_s is not None and self.end_s <= self.start_s:
            raise ValueError("end_s must be greater than start_s")
        if bool(self.source_ai_analysis_id) != bool(self.issue_id):
            raise ValueError("source_ai_analysis_id and issue_id must be given together")
        return self


class AiHandoffOut(ApiModel):
    adapter: str
    interactive: bool
    pack_path: str
    command: str
    result_path: str
    steps: list[str]


class AiValidationProblemOut(ApiModel):
    code: str
    message: str
    location: str | None = None


class AiImportOut(ApiModel):
    """One validated, imported AI result (append-only history)."""

    id: str
    version: int
    imported_at: datetime
    origin: str = Field(description="import | legacy_migration")
    adapter: str
    model: str | None = Field(description="Self-reported by the analyst; not verified")
    pack_id: str
    result_sha256: str | None
    result: dict[str, Any]
    validation_warnings: list[AiValidationProblemOut]


class AiAnalysisOut(ApiModel):
    id: str
    analysis_id: str
    kind: str
    pack_id: str
    pack_path: str = Field(description="Path to use with Claude Code (relative to repo root)")
    adapter: str
    status: str
    target: dict[str, Any] | None
    data_status: dict[str, Any]
    pack_status: str = Field(
        description="verified | modified | missing | legacy (created before integrity "
        "protection: cannot be imported, prepare a new pack)"
    )
    result: dict[str, Any] | None = Field(
        description="Latest imported AnalysisResult (same as imports[-1].result)"
    )
    model: str | None = Field(description="Latest import's self-reported model")
    validation_errors: list[AiValidationProblemOut]
    validation_warnings: list[AiValidationProblemOut]
    import_attempts: int
    created_at: datetime
    imported_at: datetime | None
    last_rejected_at: datetime | None = None
    imports: list[AiImportOut] = Field(
        default_factory=list, description="Every imported version, oldest first"
    )
    handoff: AiHandoffOut | None = None
    images: list[dict[str, Any]] = Field(default_factory=list)


class RegionVisibilityOut(ApiModel):
    """How well one body region was seen around the event (detector's own judgement).
    Separate from the event state and its reasons."""

    region: str = Field(description="torso | left_wrist | right_wrist | legs")
    label: str
    state: str = Field(description="good | low_but_usable | not_observable")
    state_label: str
    used: bool = Field(description="Region used by the event (reliability.regions_used)")
    reasons: list[str] = Field(description="Why not good, in words")


class StrokeEventOut(ApiModel):
    """One detector event as a personal V1 state (ADR-025, ADR-027). No score."""

    id: str
    state: str = Field(description="flagged | to_check | discarded")
    status: str = Field(description="Detector status: accepted | uncertain | rejected")
    label: str = Field(description="State in words (Italian)")
    time_s: float | None = Field(
        description="Reference time on the proxy timeline (frame / fps); null when the "
        "wrist is undetermined (use the window)"
    )
    frame: int | None
    window_start_s: float
    window_end_s: float
    reasons: list[str] = Field(description="Reasons in words, rule order")
    reason_codes: list[str]
    regions: list[RegionVisibilityOut] = Field(
        description="Four regions, fixed order: torso, left wrist, right wrist, legs"
    )
    review: ReviewEntryOut | None = Field(
        default=None, description="The user's correction of this event, if any (V1 step 5)"
    )


class ReviewEntryOut(ApiModel):
    """One user correction (strokes_review.json). The user's frame is the truth."""

    id: str
    kind: str = Field(description="stroke | not_stroke")
    action: str = Field(description="confirmed | moved | deleted | added | detached")
    action_label: str
    frame: int
    time_s: float = Field(description="Proxy timeline (frame / fps)")
    stroke_type: str | None = Field(description="forehand | backhand | other | null")
    stroke_type_label: str | None
    visibility: str | None = Field(description="Impact as seen by the user: full|partial|none")
    visibility_label: str | None
    reason: str | None = Field(description="Why a deleted event is not a stroke")
    reason_label: str | None
    note: str
    event_id: str | None = Field(description="Detector event it was made on (null = added)")
    event_frame: int | None


class ReviewedStretchOut(ApiModel):
    id: str
    start_frame: int
    end_frame: int
    start_s: float
    end_s: float


class ReviewEntryIn(BaseModel):
    """Create a correction. With ``event_id`` it applies to that detector event (confirm,
    move, delete); without it, a stroke the detector missed."""

    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(default=None, max_length=64)
    kind: Literal["stroke", "not_stroke"] = "stroke"
    frame: int | None = Field(
        default=None, ge=0, description="Default: the event's frame; required to add"
    )
    stroke_type: Literal["forehand", "backhand", "other"] | None = None
    visibility: Literal["full", "partial", "none"] | None = None
    reason: Literal["duplicate", "not_a_stroke", "other_person", "out_of_frame", "other"] | None = (
        None
    )
    note: str = Field(default="", max_length=500)


class ReviewEntryUpdate(BaseModel):
    """Replace the editable fields of a correction (the event link never changes)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["stroke", "not_stroke"]
    frame: int = Field(ge=0)
    stroke_type: Literal["forehand", "backhand", "other"] | None = None
    visibility: Literal["full", "partial", "none"] | None = None
    reason: Literal["duplicate", "not_a_stroke", "other_person", "out_of_frame", "other"] | None = (
        None
    )
    note: str = Field(default="", max_length=500)


class ReviewedStretchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0, description="Included; swapped if before start_frame")


class NotAssessableOut(ApiModel):
    """Insufficient evidence to detect strokes here, NOT absence of strokes."""

    state: str = "not_assessable"
    label: str
    start_s: float = Field(description="Proxy timeline (frame / fps)")
    end_s: float
    start_frame: int
    end_frame: int
    regions: list[str]
    reason: str


class StrokesOut(ApiModel):
    analysis_id: str
    video_id: str
    algorithm: str
    algorithm_version: str
    experimental: bool
    fps: float
    provisional_params: dict[str, Any] = Field(
        description="Provisional values chosen on Development A/B, not validated"
    )
    note: str
    events: list[StrokeEventOut]
    not_assessable: list[NotAssessableOut]
    frame_count: int = Field(description="Proxy frames in pose.json (valid frames 0..n-1)")
    my_strokes: list[ReviewEntryOut] = Field(
        default_factory=list,
        description="Corrections not shown on a detector event: strokes added by the user "
        "and corrections whose event changed after a new detection (detached)",
    )
    reviewed_stretches: list[ReviewedStretchOut] = Field(default_factory=list)
