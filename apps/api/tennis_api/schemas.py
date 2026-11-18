"""Pydantic request/response schemas (the API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tennis_api.models import AnalysisMode, AnalysisStatus, StrokeFilter, VideoRelation
from tennis_cv.capabilities import Capability
from tennis_cv.comparison.base import ViewAngle


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(ApiModel):
    status: str
    version: str


class ToolStatus(ApiModel):
    name: str
    available: bool
    version: str | None = None


class SystemInfo(ApiModel):
    api_version: str
    engine_version: str
    python_version: str
    platform: str
    database: str
    storage_dir: str
    tools: list[ToolStatus]
    pose_model: ToolStatus
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
