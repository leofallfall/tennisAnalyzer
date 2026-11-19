"""ORM entities implemented in Milestone 1. See docs/DATA_MODEL.md for the full model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from tennis_api.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime column, also on SQLite.

    SQLite has no timezone type: ``DateTime(timezone=True)`` stores the UTC wall time and
    returns a *naive* datetime, which the API then serialised without an offset and
    browsers read as local time (1-2 h shift in Italy). Values are normalised to UTC on
    write and tagged as UTC on read, so existing rows (always written as UTC) are fixed
    without a migration.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)


class AiAnalysisStatus(StrEnum):
    AWAITING_RESULT = "awaiting_result"
    IMPORTED = "imported"
    INVALID = "invalid"


class AiAnalysis(Base):
    """An AI interpretation request: one Evidence Pack and its import history.

    Kept apart from ``Analysis.results`` so the CV results stay immutable. Every valid
    import is a new ``AiAnalysisImport`` row; nothing here is overwritten by an import.
    ``validation_errors``/``validation_warnings`` describe the latest import attempt.
    ``result``/``model``/``imported_at`` are legacy columns (before import history) and
    are no longer written; existing values were copied to ``ai_analysis_imports``.
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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    imported_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    manifest_sha256: Mapped[str | None] = mapped_column(
        String(64),
        doc="SHA-256 of manifest.json at creation (C2 integrity anchor). NULL = legacy pack.",
    )
    last_rejected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    imports: Mapped[list[AiAnalysisImport]] = relationship(
        back_populates="ai_analysis",
        order_by="AiAnalysisImport.version",
        cascade="all, delete-orphan",
    )


class AiImportOrigin(StrEnum):
    IMPORT = "import"
    LEGACY_MIGRATION = "legacy_migration"


class AiAnalysisImport(Base):
    """One validated AI result. Append-only: rows are never updated or deleted by the app."""

    __tablename__ = "ai_analysis_imports"
    __table_args__ = (UniqueConstraint("ai_analysis_id", "version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ai_analysis_id: Mapped[str] = mapped_column(ForeignKey("ai_analyses.id"), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, doc="1, 2, … per AI analysis")
    kind: Mapped[str] = mapped_column(String(32))
    pack_id: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    result_sha256: Mapped[str | None] = mapped_column(
        String(64), doc="SHA-256 of analysis_result.json as imported (NULL for legacy rows)"
    )
    adapter: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128), doc="Self-reported by the analyst")
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation_warnings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(24), default=AiImportOrigin.IMPORT.value)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    ai_analysis: Mapped[AiAnalysis] = relationship(back_populates="imports")


class SchemaMigration(Base):
    """Applied in-place schema migrations (see ``tennis_api.migrations``)."""

    __tablename__ = "schema_migrations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
