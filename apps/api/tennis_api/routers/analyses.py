from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from tennis_api.config import Settings
from tennis_api.deps import DbDep, SettingsDep
from tennis_api.errors import ApiError, not_found
from tennis_api.models import (
    Analysis,
    AnalysisMode,
    AnalysisStatus,
    AnalysisVideo,
    ProcessingJob,
    Video,
)
from tennis_api.redaction import redact
from tennis_api.schemas import (
    AnalysisCreate,
    AnalysisOut,
    AnalysisStatusOut,
    AnalysisSummaryOut,
    AnalysisVideoOut,
    ErrorInfo,
)
from tennis_api.storage import ARTIFACT_NAMES, analysis_video_dir

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

STARTABLE = {AnalysisStatus.CREATED.value, AnalysisStatus.FAILED.value}


def _error(a: Analysis, settings: Settings) -> ErrorInfo | None:
    if not a.error_code:
        return None
    # Rows written before M1.10 may hold absolute paths or a traceback.
    details = dict(a.error_details or {})
    details.pop("traceback", None)
    message, details = redact((a.error_message or "", details), settings)
    return ErrorInfo(code=a.error_code, message=message, details=details)


def _status_fields(a: Analysis, settings: Settings) -> dict[str, object]:
    return {
        "id": a.id,
        "status": a.status,
        "stage": a.stage,
        "progress": a.progress,
        "error": _error(a, settings),
        "started_at": a.started_at,
        "finished_at": a.finished_at,
        "updated_at": a.updated_at,
    }


def _to_out(a: Analysis, settings: Settings) -> AnalysisOut:
    results = a.results or {}
    videos = []
    for link in a.video_links:
        report = (results.get("videos") or {}).get(link.video_id) or {}
        artifacts = {
            key: f"/api/analyses/{a.id}/videos/{link.video_id}/artifacts/{key}"
            for key in report.get("artifacts", {})
        }
        if report:
            artifacts["report"] = f"/api/analyses/{a.id}/videos/{link.video_id}/artifacts/report"
        videos.append(
            AnalysisVideoOut(
                video_id=link.video_id,
                position=link.position,
                camera_view=link.camera_view,
                original_filename=link.video.original_filename,
                file_url=f"/api/videos/{link.video_id}/file",
                artifacts=artifacts,
            )
        )
    return AnalysisOut(
        **_status_fields(a, settings),
        mode=AnalysisMode(a.mode),
        title=a.title,
        video_relation=a.video_relation,
        stroke_filter=a.stroke_filter,
        focus=a.focus,
        created_at=a.created_at,
        videos=videos,
        results=redact(a.results, settings) if a.results is not None else None,
    )


def _get(db: DbDep, analysis_id: str) -> Analysis:
    a = db.get(Analysis, analysis_id)
    if a is None:
        raise not_found("analysis", analysis_id)
    return a


@router.get("", response_model=list[AnalysisSummaryOut])
def list_analyses(db: DbDep, settings: SettingsDep) -> list[AnalysisSummaryOut]:
    rows = db.scalars(select(Analysis).order_by(Analysis.created_at.desc())).all()
    return [
        AnalysisSummaryOut(
            **_status_fields(a, settings),
            mode=AnalysisMode(a.mode),
            title=a.title,
            video_count=len(a.video_links),
            created_at=a.created_at,
        )
        for a in rows
    ]


@router.post("", response_model=AnalysisOut, status_code=201)
def create_analysis(body: AnalysisCreate, db: DbDep, settings: SettingsDep) -> AnalysisOut:
    missing = [v.video_id for v in body.videos if db.get(Video, v.video_id) is None]
    if missing:
        raise ApiError(404, "video_not_found", "Some videos do not exist", {"video_ids": missing})
    first = db.get(Video, body.videos[0].video_id)
    assert first is not None
    default_title = (
        f"{'Session' if body.mode is AnalysisMode.SESSION else 'Single stroke'}: "
        f"{first.original_filename}"
    )
    assert body.video_relation is not None
    analysis = Analysis(
        mode=body.mode.value,
        title=body.title or default_title,
        video_relation=body.video_relation.value,
        stroke_filter=body.stroke_filter.value,
        focus=body.focus,
        status=AnalysisStatus.CREATED.value,
        progress=0.0,
    )
    analysis.video_links = [
        AnalysisVideo(video_id=v.video_id, position=i, camera_view=v.camera_view.value)
        for i, v in enumerate(body.videos)
    ]
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return _to_out(analysis, settings)


@router.get("/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: str, db: DbDep, settings: SettingsDep) -> AnalysisOut:
    return _to_out(_get(db, analysis_id), settings)


@router.get("/{analysis_id}/status", response_model=AnalysisStatusOut)
def get_status(analysis_id: str, db: DbDep, settings: SettingsDep) -> AnalysisStatusOut:
    return AnalysisStatusOut(**_status_fields(_get(db, analysis_id), settings))


@router.post("/{analysis_id}/start", response_model=AnalysisStatusOut, status_code=202)
def start_analysis(analysis_id: str, db: DbDep, settings: SettingsDep) -> AnalysisStatusOut:
    """Queue the analysis for the worker. Returns immediately (202 Accepted)."""
    a = _get(db, analysis_id)
    if a.status not in STARTABLE:
        raise ApiError(
            409,
            "analysis_not_startable",
            f"Analysis is '{a.status}' and cannot be started",
            {"status": a.status, "startable_from": sorted(STARTABLE)},
        )
    a.status = AnalysisStatus.QUEUED.value
    a.stage = "queued"
    a.progress = 0.0
    a.error_code = a.error_message = None
    a.error_details = None
    a.results = None
    a.started_at = a.finished_at = None
    db.add(ProcessingJob(analysis_id=a.id))
    db.commit()
    return AnalysisStatusOut(**_status_fields(a, settings))


@router.get("/{analysis_id}/videos/{video_id}/artifacts/{name}", response_model=None)
def get_artifact(
    analysis_id: str, video_id: str, name: str, db: DbDep, settings: SettingsDep
) -> FileResponse | JSONResponse:
    _get(db, analysis_id)
    filename = ARTIFACT_NAMES.get(name)
    if filename is None:
        raise ApiError(
            404,
            "artifact_unknown",
            f"Unknown artifact '{name}'",
            {"available": sorted(ARTIFACT_NAMES)},
        )
    path = analysis_video_dir(settings.storage_dir, analysis_id, video_id) / filename
    if not path.is_file():
        raise ApiError(404, "artifact_not_found", f"Artifact '{name}' does not exist (yet)")
    if name == "report":
        # Small file; reports written before M1.10 hold absolute paths.
        return JSONResponse(redact(json.loads(path.read_text()), settings))
    media = "application/json" if filename.endswith(".json") else "video/mp4"
    return FileResponse(path, media_type=media)
