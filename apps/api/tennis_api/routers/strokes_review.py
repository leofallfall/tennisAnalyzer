"""V1 step 5: the user's corrections of the detected strokes (ADR-031).

Corrections live in ``strokes_review.json`` next to ``strokes.json``; the detector output is
only read, never changed, and a new detection keeps the corrections. Every change answers
with the whole strokes view, so the UI shows exactly what was saved.
"""

from __future__ import annotations

import io
import threading
import zipfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

from tennis_api.deps import DbDep, SettingsDep
from tennis_api.errors import ApiError, not_found
from tennis_api.models import Analysis, Video
from tennis_api.schemas import (
    ReviewedStretchIn,
    ReviewEntryIn,
    ReviewEntryUpdate,
    StrokesOut,
)
from tennis_api.storage import ARTIFACT_NAMES, analysis_video_dir
from tennis_api.strokes_view import frame_count, strokes_view
from tennis_cv.evaluation.dataset import ImpactVisibility
from tennis_cv.strokes import review as rv
from tennis_cv.strokes.artifact import StrokesFile, load_strokes

router = APIRouter(
    prefix="/api/analyses/{analysis_id}/videos/{video_id}/strokes/review",
    tags=["strokes review"],
)

_LOCK = threading.Lock()
"""One writer at a time (single local user; the lock only guards read-modify-write)."""


def _paths(db: DbDep, settings: SettingsDep, analysis_id: str, video_id: str) -> Path:
    a = db.get(Analysis, analysis_id)
    if a is None:
        raise not_found("analysis", analysis_id)
    if all(link.video_id != video_id for link in a.video_links):
        raise not_found("video", video_id)
    path = (
        analysis_video_dir(settings.storage_dir, analysis_id, video_id) / ARTIFACT_NAMES["strokes"]
    )
    if not path.is_file():
        raise ApiError(404, "strokes_not_available", "No strokes detected for this video yet")
    return path


def _bad(message: str, code: str = "review_invalid") -> ApiError:
    return ApiError(422, code, message)


def _check_frame(doc: StrokesFile, frame: int) -> None:
    n = frame_count(doc)
    if frame >= n:
        raise _bad(f"Frame {frame} is past the end of the video (last frame {n - 1})")


def _entry(
    *,
    entry_id: str,
    kind: str,
    frame: int,
    stroke_type: str | None,
    visibility: str | None,
    reason: str | None,
    note: str,
    event: rv.EventLink | None,
) -> rv.ReviewEntry:
    try:
        return rv.ReviewEntry(
            id=entry_id,
            kind=rv.ReviewKind(kind),
            frame=frame,
            stroke_type=rv.ReviewStrokeType(stroke_type) if stroke_type else None,
            visibility=ImpactVisibility(visibility) if visibility else None,
            reason=rv.NotStrokeReason(reason) if reason else None,
            note=note.strip(),
            event=event,
            updated_at=rv.now(),
        )
    except ValueError as exc:
        raise _bad(str(exc).splitlines()[-1] if str(exc) else "Invalid correction") from exc


def _save(
    strokes_path: Path,
    analysis_id: str,
    video_id: str,
    review: rv.StrokesReview,
    **changes: object,
) -> StrokesOut:
    try:
        new = rv.StrokesReview.model_validate({**review.model_dump(by_alias=True), **changes})
    except ValueError as exc:
        msg = str(exc)
        if "same frame" in msg:
            raise _bad("There is already a stroke on this frame", "review_conflict") from exc
        if "same detector event" in msg:
            raise _bad("This event already has a correction", "review_conflict") from exc
        raise _bad("Invalid correction") from exc
    rv.save_review(strokes_path.with_name(rv.REVIEW_FILE), new)
    return strokes_view(strokes_path, analysis_id, video_id, new)


@router.post("/entries", response_model=StrokesOut, status_code=201)
def add_entry(
    analysis_id: str, video_id: str, body: ReviewEntryIn, db: DbDep, settings: SettingsDep
) -> StrokesOut:
    """Confirm, move or delete a detector event (``event_id``), or add a missed stroke."""
    path = _paths(db, settings, analysis_id, video_id)
    with _LOCK:
        doc = load_strokes(path)
        review = rv.load_review(path.with_name(rv.REVIEW_FILE), video_id)
        link: rv.EventLink | None = None
        if body.event_id is not None:
            ev = next((e for e in doc.events if e.id == body.event_id), None)
            if ev is None:
                raise not_found("event", body.event_id)
            link = rv.EventLink(
                event_id=ev.id,
                event_frame=rv.event_frame(ev),
                algorithm_version=doc.producer.algorithm_version,
            )
            # a detached correction on an id that the new detection reuses is replaced
            stale = [e for e in review.entries if e.event and e.event.event_id == ev.id]
            if any(rv.linked_event(e, {ev.id: ev}) is not None for e in stale):
                raise _bad("This event already has a correction", "review_conflict")
            entries = tuple(e for e in review.entries if e not in stale)
        else:
            if body.kind != "stroke":
                raise _bad("Only a detector event can be deleted")
            if body.frame is None:
                raise _bad("frame is required to add a stroke")
            entries = review.entries
        frame = body.frame if body.frame is not None else link.event_frame  # type: ignore[union-attr]
        _check_frame(doc, frame)
        entry = _entry(
            entry_id=rv.new_id(),
            kind=body.kind,
            frame=frame,
            stroke_type=body.stroke_type,
            visibility=body.visibility,
            reason=body.reason,
            note=body.note,
            event=link,
        )
        return _save(path, analysis_id, video_id, review, entries=(*entries, entry))


@router.put("/entries/{entry_id}", response_model=StrokesOut)
def update_entry(
    analysis_id: str,
    video_id: str,
    entry_id: str,
    body: ReviewEntryUpdate,
    db: DbDep,
    settings: SettingsDep,
) -> StrokesOut:
    path = _paths(db, settings, analysis_id, video_id)
    with _LOCK:
        doc = load_strokes(path)
        review = rv.load_review(path.with_name(rv.REVIEW_FILE), video_id)
        old = next((e for e in review.entries if e.id == entry_id), None)
        if old is None:
            raise not_found("correction", entry_id)
        _check_frame(doc, body.frame)
        new = _entry(
            entry_id=old.id,
            kind=body.kind,
            frame=body.frame,
            stroke_type=body.stroke_type,
            visibility=body.visibility,
            reason=body.reason,
            note=body.note,
            event=old.event,
        )
        entries = tuple(new if e.id == entry_id else e for e in review.entries)
        return _save(path, analysis_id, video_id, review, entries=entries)


@router.delete("/entries/{entry_id}", response_model=StrokesOut)
def delete_entry(
    analysis_id: str, video_id: str, entry_id: str, db: DbDep, settings: SettingsDep
) -> StrokesOut:
    """Undo a correction: the event shows the detector's state again."""
    path = _paths(db, settings, analysis_id, video_id)
    with _LOCK:
        review = rv.load_review(path.with_name(rv.REVIEW_FILE), video_id)
        if all(e.id != entry_id for e in review.entries):
            raise not_found("correction", entry_id)
        entries = tuple(e for e in review.entries if e.id != entry_id)
        return _save(path, analysis_id, video_id, review, entries=entries)


@router.post("/stretches", response_model=StrokesOut, status_code=201)
def add_stretch(
    analysis_id: str, video_id: str, body: ReviewedStretchIn, db: DbDep, settings: SettingsDep
) -> StrokesOut:
    """Mark a stretch as reviewed end to end (merged with stretches it overlaps or touches)."""
    path = _paths(db, settings, analysis_id, video_id)
    with _LOCK:
        doc = load_strokes(path)
        review = rv.load_review(path.with_name(rv.REVIEW_FILE), video_id)
        _check_frame(doc, max(body.start_frame, body.end_frame))
        stretches = rv.add_stretch(review.reviewed_stretches, body.start_frame, body.end_frame)
        return _save(path, analysis_id, video_id, review, reviewed_stretches=stretches)


@router.delete("/stretches/{stretch_id}", response_model=StrokesOut)
def delete_stretch(
    analysis_id: str, video_id: str, stretch_id: str, db: DbDep, settings: SettingsDep
) -> StrokesOut:
    path = _paths(db, settings, analysis_id, video_id)
    with _LOCK:
        review = rv.load_review(path.with_name(rv.REVIEW_FILE), video_id)
        if all(s.id != stretch_id for s in review.reviewed_stretches):
            raise not_found("stretch", stretch_id)
        stretches = tuple(s for s in review.reviewed_stretches if s.id != stretch_id)
        return _save(path, analysis_id, video_id, review, reviewed_stretches=stretches)


def _default_clip_id(db: DbDep, video_id: str) -> str:
    v = db.get(Video, video_id)
    stem = Path(v.original_filename).stem if v and v.original_filename else video_id
    clip = "".join(c if c.isalnum() or c in "_-" else "_" for c in stem).strip("_")
    return (clip or "clip")[:64]


@router.get("/export")
def export_review(
    analysis_id: str,
    video_id: str,
    db: DbDep,
    settings: SettingsDep,
    clip_id: str | None = None,
) -> Response:
    """The corrections as ``labels.csv`` (+ ``annotated.csv`` for reviewed stretches) in
    the format of docs/M2_1_DATASET.md §4, zipped with a short LEGGIMI.txt."""
    path = _paths(db, settings, analysis_id, video_id)
    review = rv.load_review(path.with_name(rv.REVIEW_FILE), video_id)
    clip = clip_id or _default_clip_id(db, video_id)
    exp = rv.export_labels(review, clip)
    readme = [
        f"Correzioni a mano esportate il {date.today().isoformat()}.",
        f"clip_id: {clip}",
        f"Colpi esportati in labels.csv: {exp.exported}",
        f"Colpi non esportati: {len(exp.left_out)} (elencati in testa a labels.csv)",
        "",
        "Per usarli con eval_strokes.py aggiungi una riga al MANIFEST.md con questo clip_id;",
        "pose_dir = la cartella dell'analisi che contiene pose.json:",
        str(path.parent),
        "Tipo 'other' = qualsiasi colpo che non sia dritto o rovescio.",
    ]
    if exp.annotated_csv is None:
        readme.append("Nessun tratto rivisto: niente annotated.csv (copertura sconosciuta).")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("labels.csv", exp.labels_csv)
        if exp.annotated_csv is not None:
            z.writestr("annotated.csv", exp.annotated_csv)
        z.writestr("LEGGIMI.txt", "\n".join(readme) + "\n")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="correzioni_{clip}.zip"'},
    )
