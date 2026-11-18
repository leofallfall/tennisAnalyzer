"""Personal AI analysis: prepare Evidence Packs and import validated AI results.

The API never calls an LLM. With the default Claude Code adapter the user runs the
analysis interactively and then asks the app to import ``analysis_result.json``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select

from tennis_api.ai import pack as pk
from tennis_api.ai.builder import (
    PackOptions,
    build_session_pack,
    build_single_stroke_pack,
)
from tennis_api.ai.client import get_ai_client
from tennis_api.ai.result import AnalysisResult
from tennis_api.ai.sources import session_metadata, video_sources
from tennis_api.config import REPO_ROOT, Settings
from tennis_api.deps import DbDep, SettingsDep
from tennis_api.errors import ApiError, not_found
from tennis_api.models import AiAnalysis, AiAnalysisStatus, Analysis, AnalysisStatus
from tennis_api.schemas import (
    AiAnalysisOut,
    AiHandoffOut,
    AiValidationProblemOut,
    SingleStrokeAiRequest,
)
from tennis_cv.common import TimeInterval

router = APIRouter(tags=["ai"])


def _display_path(path: Path) -> str:
    """Path the user types in Claude Code: relative to the repo root when possible."""
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _to_out(row: AiAnalysis, settings: Settings) -> AiAnalysisOut:
    pack_dir = settings.storage_dir / row.pack_dir
    handoff = None
    if row.status != AiAnalysisStatus.IMPORTED.value:
        client = get_ai_client(row.adapter)
        if client.interactive:
            handoff = client.request_analysis(
                pack_dir, _display_path(pack_dir), pk.PackKind(row.kind)
            )
    images: list[dict[str, object]] = []
    manifest_path = pack_dir / pk.MANIFEST
    if manifest_path.is_file():
        manifest = pk.Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        images = [
            {**img.model_dump(mode="json"), "url": f"/api/ai-analyses/{row.id}/images/{img.id}"}
            for img in manifest.images
        ]
    return AiAnalysisOut(
        id=row.id,
        analysis_id=row.analysis_id,
        kind=row.kind,
        pack_id=row.pack_id,
        pack_path=_display_path(pack_dir),
        adapter=row.adapter,
        status=row.status,
        target=row.target,
        data_status=row.data_status,
        result=row.result,
        model=row.model,
        validation_errors=[
            AiValidationProblemOut.model_validate(e) for e in row.validation_errors or []
        ],
        validation_warnings=[
            AiValidationProblemOut.model_validate(w) for w in row.validation_warnings or []
        ],
        import_attempts=row.import_attempts,
        created_at=row.created_at,
        imported_at=row.imported_at,
        handoff=AiHandoffOut.model_validate(handoff.model_dump()) if handoff else None,
        images=images,
    )


def _completed_analysis(db: DbDep, analysis_id: str) -> Analysis:
    a = db.get(Analysis, analysis_id)
    if a is None:
        raise not_found("analysis", analysis_id)
    if a.status != AnalysisStatus.COMPLETED.value:
        raise ApiError(
            409,
            "cv_not_completed",
            "Video processing must be completed before preparing an AI analysis",
            {"status": a.status},
        )
    return a


def _save(
    db: DbDep, settings: Settings, a: Analysis, manifest: pk.Manifest, pack_dir: Path
) -> AiAnalysis:
    row = AiAnalysis(
        analysis_id=a.id,
        kind=manifest.kind.value,
        pack_id=manifest.pack_id,
        pack_dir=pack_dir.relative_to(settings.storage_dir).as_posix(),
        adapter=settings.ai_adapter,
        status=AiAnalysisStatus.AWAITING_RESULT.value,
        target=manifest.target.model_dump(mode="json") if manifest.target else None,
        data_status={k: v.value for k, v in manifest.data_status.items()},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/api/analyses/{analysis_id}/ai", response_model=list[AiAnalysisOut])
def list_ai_analyses(analysis_id: str, db: DbDep, settings: SettingsDep) -> list[AiAnalysisOut]:
    if db.get(Analysis, analysis_id) is None:
        raise not_found("analysis", analysis_id)
    rows = db.scalars(
        select(AiAnalysis)
        .where(AiAnalysis.analysis_id == analysis_id)
        .order_by(AiAnalysis.created_at.desc())
    ).all()
    return [_to_out(r, settings) for r in rows]


@router.post(
    "/api/analyses/{analysis_id}/ai/session", response_model=AiAnalysisOut, status_code=201
)
def prepare_session(analysis_id: str, db: DbDep, settings: SettingsDep) -> AiAnalysisOut:
    a = _completed_analysis(db, analysis_id)
    get_ai_client(settings.ai_adapter)  # fail early on a bad configuration
    videos = video_sources(a, settings)
    meta = session_metadata(a).model_copy(update={"videos": [v.info for v in videos]})
    # Stroke detection does not exist yet (M2): the pack declares it as not implemented.
    manifest, pack_dir = build_session_pack(
        settings.ai_dir / a.id,
        meta,
        videos,
        stroke_data=None,
        options=PackOptions(language=settings.ai_language),
    )
    return _to_out(_save(db, settings, a, manifest, pack_dir), settings)


def _find_stroke(
    db: DbDep, settings: Settings, analysis_id: str, stroke_id: str
) -> pk.StrokeRecord | None:
    """Look the stroke up in this analysis' session packs (the engine's stroke list)."""
    rows = db.scalars(
        select(AiAnalysis).where(
            AiAnalysis.analysis_id == analysis_id, AiAnalysis.kind == pk.PackKind.SESSION.value
        )
    ).all()
    for row in rows:
        path = settings.storage_dir / row.pack_dir / pk.STROKES
        if path.is_file():
            for s in pk.StrokesFile.model_validate_json(path.read_text()).strokes:
                if s.id == stroke_id:
                    return s
    return None


@router.post(
    "/api/analyses/{analysis_id}/ai/single-stroke", response_model=AiAnalysisOut, status_code=201
)
def prepare_single_stroke(
    analysis_id: str, body: SingleStrokeAiRequest, db: DbDep, settings: SettingsDep
) -> AiAnalysisOut:
    a = _completed_analysis(db, analysis_id)
    get_ai_client(settings.ai_adapter)
    videos = {v.info.video_id: v for v in video_sources(a, settings)}
    motivating = None
    if body.source_ai_analysis_id and body.issue_id:
        src = db.get(AiAnalysis, body.source_ai_analysis_id)
        if src is None or src.analysis_id != a.id or src.result is None:
            raise ApiError(404, "ai_analysis_not_found", "Source AI analysis not found")
        result = AnalysisResult.model_validate(src.result)
        issue = next((i for i in result.issues if i.id == body.issue_id), None)
        if issue is None:
            raise ApiError(404, "issue_not_found", f"Issue '{body.issue_id}' not found")
        motivating = pk.MotivatingIssue(
            source_ai_analysis_id=src.id,
            issue_id=issue.id,
            name=issue.name,
            description=issue.description,
        )

    stroke = None
    interval = None
    if body.stroke_id:
        stroke = _find_stroke(db, settings, a.id, body.stroke_id)
        if stroke is None:
            raise ApiError(
                404,
                "stroke_not_found",
                f"Stroke '{body.stroke_id}' is not in any session Evidence Pack",
            )
        video = videos.get(stroke.video_id)
    else:
        video_id = body.video_id or a.video_links[0].video_id
        video = videos.get(video_id)
        if video is None:
            raise ApiError(404, "video_not_found", f"Video '{video_id}' is not in this analysis")
        if body.start_s is None or body.end_s is None:
            # A single-stroke upload is the stroke: use the whole clip.
            duration = video.info.duration_s or 0.0
            interval = TimeInterval(start_s=0.0, end_s=duration)
        else:
            interval = TimeInterval(start_s=body.start_s, end_s=body.end_s)
    if video is None:
        raise ApiError(404, "video_not_found", "The stroke's video is not in this analysis")
    meta = session_metadata(a).model_copy(update={"videos": [video.info]})
    manifest, pack_dir = build_single_stroke_pack(
        settings.ai_dir / a.id,
        meta,
        video,
        stroke=stroke,
        interval=interval,
        declared_type=body.declared_stroke_type,
        motivating_issue=motivating,
        options=PackOptions(language=settings.ai_language),
    )
    return _to_out(_save(db, settings, a, manifest, pack_dir), settings)


def _get(db: DbDep, ai_id: str) -> AiAnalysis:
    row = db.get(AiAnalysis, ai_id)
    if row is None:
        raise ApiError(404, "ai_analysis_not_found", f"AI analysis '{ai_id}' not found")
    return row


@router.get("/api/ai-analyses/{ai_id}", response_model=AiAnalysisOut)
def get_ai_analysis(ai_id: str, db: DbDep, settings: SettingsDep) -> AiAnalysisOut:
    return _to_out(_get(db, ai_id), settings)


@router.post("/api/ai-analyses/{ai_id}/import", response_model=AiAnalysisOut)
def import_result(ai_id: str, db: DbDep, settings: SettingsDep) -> AiAnalysisOut:
    """Validate ``analysis_result.json`` and store it. Invalid output is never imported."""
    row = _get(db, ai_id)
    client = get_ai_client(row.adapter)
    outcome = client.collect_result(settings.storage_dir / row.pack_dir)
    row.import_attempts += 1
    row.validation_warnings = [w.model_dump() for w in outcome.warnings]
    if not outcome.ok:
        row.validation_errors = [e.model_dump() for e in outcome.errors]
        if row.status != AiAnalysisStatus.IMPORTED.value:
            row.status = AiAnalysisStatus.INVALID.value
        db.commit()
        raise ApiError(
            422,
            "ai_result_invalid",
            "The AI result was not imported because it failed validation",
            {"errors": row.validation_errors, "ai_analysis_id": row.id},
        )
    assert outcome.result is not None
    row.result = outcome.result.model_dump(mode="json")
    row.model = outcome.result.model
    row.validation_errors = []
    row.status = AiAnalysisStatus.IMPORTED.value
    row.imported_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _to_out(row, settings)


@router.get("/api/ai-analyses/{ai_id}/images/{image_id}")
def get_pack_image(ai_id: str, image_id: str, db: DbDep, settings: SettingsDep) -> FileResponse:
    row = _get(db, ai_id)
    pack_dir = settings.storage_dir / row.pack_dir
    manifest = pk.Manifest.model_validate_json((pack_dir / pk.MANIFEST).read_text())
    img = next((i for i in manifest.images if i.id == image_id), None)
    if img is None or not (pack_dir / img.path).is_file():
        raise ApiError(404, "image_not_found", f"Image '{image_id}' not found")
    return FileResponse(pack_dir / img.path, media_type="image/jpeg")
