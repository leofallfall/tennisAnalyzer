"""ORM entities implemented in Milestone 1. See docs/DATA_MODEL.md for the full model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tennis_api.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class AnalysisMode(StrEnum):
    SESSION = "session"
    SINGLE_STROKE = "single_stroke"


class VideoRelation(StrEnum):
    """How multiple videos of one analysis relate. Must never be guessed."""

    SINGLE = "single"
    SAME_STROKE_MULTI_ANGLE = "same_stroke_multi_angle"
    DIFFERENT_STROKES = "different_strokes"


class StrokeFilter(StrEnum):
    FOREHAND = "forehand"
    BACKHAND = "backhand"
    BOTH = "both"


class AnalysisStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024), doc="Relative to storage_dir")
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    video_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    mode: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    video_relation: Mapped[str] = mapped_column(String(48))
    stroke_filter: Mapped[str] = mapped_column(String(16), default=StrokeFilter.BOTH.value)
    focus: Mapped[str | None] = mapped_column(Text, doc="Technical aspect to investigate")
    status: Mapped[str] = mapped_column(String(16), default=AnalysisStatus.CREATED.value)
    stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    results: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    video_links: Mapped[list[AnalysisVideo]] = relationship(
        back_populates="analysis",
        order_by="AnalysisVideo.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AnalysisVideo(Base):
    """Association analysis<->video. ``camera_view`` distinguishes angles of one stroke."""

    __tablename__ = "analysis_videos"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    camera_view: Mapped[str] = mapped_column(String(32), default="unknown")

    analysis: Mapped[Analysis] = relationship(back_populates="video_links")
    video: Mapped[Video] = relationship(lazy="joined")


class ProcessingJob(Base):
    """Durable job queue row consumed by the worker process (no Redis/Celery)."""

    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="video_processing")
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AiAnalysisStatus(StrEnum):
    AWAITING_RESULT = "awaiting_result"
    IMPORTED = "imported"
    INVALID = "invalid"


class AiAnalysis(Base):
    """An AI interpretation request: one Evidence Pack and, once imported, its result.

    Kept apart from ``Analysis.results`` so the CV results stay immutable.
    """

    __tablename__ = "ai_analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), doc="session | single_stroke")
    pack_id: Mapped[str] = mapped_column(String(64), unique=True)
    pack_dir: Mapped[str] = mapped_column(String(1024), doc="Relative to storage_dir")
    adapter: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default=AiAnalysisStatus.AWAITING_RESULT.value)
    target: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    data_status: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(128))
    validation_errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    validation_warnings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    import_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
