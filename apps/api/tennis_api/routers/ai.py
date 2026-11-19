"""Personal AI analysis: prepare Evidence Packs and import validated AI results.

The API never calls an LLM. With the default Claude Code adapter the user runs the
analysis interactively and then asks the app to import ``analysis_result.json``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from tennis_api.ai import pack as pk
from tennis_api.ai.builder import (
    PackOptions,
    build_session_pack,
    build_single_stroke_pack,
    manifest_sha256,
)
from tennis_api.ai.client import get_ai_client
from tennis_api.ai.result import AnalysisResult
from tennis_api.ai.sources import session_metadata, video_sources
from tennis_api.ai.validation import (
    PackAnchor,
    ValidationOutcome,
    load_pack,
    read_anchored_manifest,
    read_verified_image,
)
from tennis_api.config import REPO_ROOT, Settings
from tennis_api.deps import DbDep, SettingsDep
from tennis_api.errors import ApiError, not_found
from tennis_api.models import (
    AiAnalysis,
    AiAnalysisImport,
    AiAnalysisStatus,
    AiImportOrigin,
    Analysis,
    AnalysisStatus,
)
from tennis_api.schemas import (
    AiAnalysisOut,
    AiHandoffOut,
    AiImportOut,
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


def _anchor(row: AiAnalysis) -> PackAnchor | None:
    """The integrity anchor recorded at creation; ``None`` for legacy packs."""
    if not row.manifest_sha256:
        return None
    return PackAnchor(
        pack_id=row.pack_id,
        analysis_id=row.analysis_id,
        kind=pk.PackKind(row.kind),
        manifest_sha256=row.manifest_sha256,
    )


def _pack_dir(row: AiAnalysis, settings: Settings) -> Path:
    return settings.storage_dir / row.pack_dir


def _manifest_state(row: AiAnalysis, settings: Settings) -> tuple[str, pk.Manifest | None]:
    anchor = _anchor(row)
    if anchor is None:
        return "legacy", None
    outcome = ValidationOutcome()
    verified = read_anchored_manifest(_pack_dir(row, settings), anchor, outcome)
    if verified is not None:
        return "verified", verified.manifest
    codes = {e.code for e in outcome.errors}
    return ("missing" if codes <= {"pack_manifest_missing", "pack_missing"} else "modified"), None


def _import_out(imp: AiAnalysisImport) -> AiImportOut:
    return AiImportOut(
        id=imp.id,
        version=imp.version,
        imported_at=imp.imported_at,
        origin=imp.origin,
        adapter=imp.adapter,
        model=imp.model,
        pack_id=imp.pack_id,
        result_sha256=imp.result_sha256,
        result=imp.result,
        validation_warnings=[
            AiValidationProblemOut.model_validate(w) for w in imp.validation_warnings or []
        ],
    )


def _to_out(row: AiAnalysis, settings: Settings) -> AiAnalysisOut:
    pack_dir = _pack_dir(row, settings)
    pack_status, manifest = _manifest_state(row, settings)
    handoff = None
    if row.status != AiAnalysisStatus.IMPORTED.value and pack_status == "verified":
        client = get_ai_client(row.adapter)
        if client.interactive:
            handoff = client.request_analysis(
                pack_dir, _display_path(pack_dir), pk.PackKind(row.kind)
            )
    images: list[dict[str, object]] = []
    if manifest is not None:
        images = [
            {**img.model_dump(mode="json"), "url": f"/api/ai-analyses/{row.id}/images/{img.id}"}
            for img in manifest.images
        ]
    imports = [_import_out(i) for i in row.imports]
    latest = imports[-1] if imports else None
    return AiAnalysisOut(
        id=row.id,
        analysis_id=row.analysis_id,
        kind=row.kind,
        pack_id=row.pack_id,
        pack_path=_display_path(pack_dir),
        adapter=row.adapter,
        status=row.status,
        pack_status=pack_status,
        target=row.target,
        data_status=row.data_status,
        result=latest.result if latest else None,
        model=latest.model if latest else None,
        validation_errors=[
            AiValidationProblemOut.model_validate(e) for e in row.validation_errors or []
        ],
        validation_warnings=[
            AiValidationProblemOut.model_validate(w) for w in row.validation_warnings or []
        ],
        import_attempts=row.import_attempts,
        created_at=row.created_at,
        imported_at=latest.imported_at if latest else None,
        last_rejected_at=row.last_rejected_at,
        imports=imports,
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
        # C2: the expected manifest hash lives in the database, outside the pack folder.
        manifest_sha256=manifest_sha256(manifest),
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
    """Look the stroke up in this analysis' session packs (the engine's stroke list).

    Only packs that pass the full integrity check (manifest anchored in the database,
    every file hash) are used: a modified or legacy pack is ignored.
    """
    rows = db.scalars(
        select(AiAnalysis)
        .where(AiAnalysis.analysis_id == analysis_id, AiAnalysis.kind == pk.PackKind.SESSION.value)
        .order_by(AiAnalysis.created_at.desc())
    ).all()
    for row in rows:
        anchor = _anchor(row)
        if anchor is None:
            continue
        loaded = load_pack(_pack_dir(row, settings), ValidationOutcome(), anchor)
        if loaded is None:
            continue
        for s in loaded.strokes.strokes:
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
        if src is None or src.analysis_id != a.id or not src.imports:
            raise ApiError(404, "ai_analysis_not_found", "Source AI analysis not found")
        if body.source_import_id:
            src_import = next((i for i in src.imports if i.id == body.source_import_id), None)
            if src_import is None:
                raise ApiError(404, "ai_import_not_found", "Source AI result version not found")
        else:
            src_import = src.imports[-1]
        result = AnalysisResult.model_validate(src_import.result)
        issue = next((i for i in result.issues if i.id == body.issue_id), None)
        if issue is None:
            raise ApiError(404, "issue_not_found", f"Issue '{body.issue_id}' not found")
        motivating = pk.MotivatingIssue(
            source_ai_analysis_id=src.id,
            source_import_id=src_import.id,
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
    """Validate ``analysis_result.json`` and add it to the import history.

    * Invalid output (including a modified, legacy or foreign pack) is never imported;
      the history is untouched and only the latest-attempt fields change.
    * Each valid import is a new version; earlier versions are never modified.
    * Importing the exact same file again is refused (409): it would not be a new version.
    """
    row = _get(db, ai_id)
    client = get_ai_client(row.adapter)
    outcome = client.collect_result(_pack_dir(row, settings), _anchor(row))
    row.import_attempts += 1
    row.validation_warnings = [w.model_dump() for w in outcome.warnings]
    now = datetime.now(UTC)
    if not outcome.ok:
        row.validation_errors = [e.model_dump() for e in outcome.errors]
        row.last_rejected_at = now
        if not row.imports:
            row.status = AiAnalysisStatus.INVALID.value
        db.commit()
        raise ApiError(
            422,
            "ai_result_invalid",
            "The AI result was not imported because it failed validation",
            {"errors": row.validation_errors, "ai_analysis_id": row.id},
        )
    assert outcome.result is not None and outcome.result_sha256 is not None
    duplicate = next((i for i in row.imports if i.result_sha256 == outcome.result_sha256), None)
    if duplicate is not None:
        db.commit()
        raise ApiError(
            409,
            "ai_result_already_imported",
            f"This exact result is already imported as version {duplicate.version}",
            {"ai_analysis_id": row.id, "version": duplicate.version},
        )
    version = max((i.version for i in row.imports), default=0) + 1
    row.imports.append(
        AiAnalysisImport(
            ai_analysis_id=row.id,
            analysis_id=row.analysis_id,
            version=version,
            kind=row.kind,
            pack_id=row.pack_id,
            manifest_sha256=row.manifest_sha256,
            result_sha256=outcome.result_sha256,
            adapter=row.adapter,
            model=outcome.result.model,
            result=outcome.result.model_dump(mode="json"),
            validation_warnings=row.validation_warnings,
            origin=AiImportOrigin.IMPORT.value,
            imported_at=now,
        )
    )
    row.validation_errors = []
    row.status = AiAnalysisStatus.IMPORTED.value
    db.commit()
    db.refresh(row)
    return _to_out(row, settings)


@router.get("/api/ai-analyses/{ai_id}/images/{image_id}")
def get_pack_image(ai_id: str, image_id: str, db: DbDep, settings: SettingsDep) -> Response:
    """Serve one evidence JPEG of a verified pack (never a file outside the pack)."""
    row = _get(db, ai_id)
    anchor = _anchor(row)
    data = (
        None if anchor is None else read_verified_image(_pack_dir(row, settings), anchor, image_id)
    )
    if data is None:
        raise ApiError(404, "image_not_found", "Image not found")
    return Response(content=data, media_type="image/jpeg")
