"""Pydantic request/response schemas (the API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
        description="Per-video processing reports keyed by video id. Contains ingestion, "
        "quality and pose results only; no stroke/biomechanical analysis yet.",
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
