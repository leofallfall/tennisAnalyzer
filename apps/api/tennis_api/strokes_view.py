"""Personal V1 view of a ``strokes.json`` for the app (ADR-025, ADR-027, ADR-028).

Read-only: states and reasons come from ``tennis_cv.strokes.states``; no decision changes.
Times are put on the proxy timeline (``frame / fps``), the one the player shows, also when
the file's ``time_basis`` is ``source``.
"""

from __future__ import annotations

from pathlib import Path

from tennis_api.schemas import (
    NotAssessableOut,
    RegionVisibilityOut,
    ReviewedStretchOut,
    ReviewEntryOut,
    StrokeEventOut,
    StrokesOut,
)
from tennis_cv.strokes import review as rv
from tennis_cv.strokes.artifact import ProducerStatus, StrokesFile, load_strokes
from tennis_cv.strokes.states import STATE_LABEL, V1State, event_view, stretch_view

NOTE = (
    "Rilevatore sperimentale: soglie provvisorie, non validate. Nessuna percentuale. "
    "'Non valutabile' = evidenze insufficienti per rilevare colpi, non assenza di colpi."
)


def _params(doc: StrokesFile) -> dict[str, object]:
    p = doc.params
    out: dict[str, object] = {}
    for section, keys in (
        ("decision", ("t_reject", "t_accept")),
        ("grouping", ("g_s", "max_single_span_s")),
        ("detector", ("min_prominence_torso_per_s",)),
    ):
        block = p.get(section)
        if isinstance(block, dict):
            out.update({k: block[k] for k in keys if k in block})
    return out


def frame_count(doc: StrokesFile) -> int:
    """Proxy frames covered by pose.json (valid frames are 0 .. n-1)."""
    p = doc.inputs.pose
    return (p.frames - 1) * p.frame_stride + 1 if p.frames else 0


def _entry_out(entry: rv.ReviewEntry, action: rv.Action, fps: float) -> ReviewEntryOut:
    return ReviewEntryOut(
        id=entry.id,
        kind=entry.kind.value,
        action=action,
        action_label=rv.ACTION_LABEL[action],
        frame=entry.frame,
        time_s=round(entry.frame / fps, 3),
        stroke_type=entry.stroke_type.value if entry.stroke_type else None,
        stroke_type_label=rv.TYPE_LABEL[entry.stroke_type] if entry.stroke_type else None,
        visibility=entry.visibility.value if entry.visibility else None,
        visibility_label=rv.VISIBILITY_LABEL[entry.visibility] if entry.visibility else None,
        reason=entry.reason.value if entry.reason else None,
        reason_label=rv.REASON_LABEL[entry.reason] if entry.reason else None,
        note=entry.note,
        event_id=entry.event.event_id if entry.event else None,
        event_frame=entry.event.event_frame if entry.event else None,
    )


def strokes_view(
    path: Path, analysis_id: str, video_id: str, review: rv.StrokesReview | None = None
) -> StrokesOut:
    """Detector output as V1 states, with the user's corrections (``review``) applied on
    top: each linked correction is attached to its event; the others are listed apart."""
    doc = load_strokes(path)
    fps = doc.inputs.pose.fps
    review = review or rv.empty_review(video_id)

    def t(frame: int) -> float:
        return round(frame / fps, 3)

    by_id = {e.id: e for e in doc.events}
    on_event: dict[str, ReviewEntryOut] = {}
    mine: list[ReviewEntryOut] = []
    for entry in review.entries:
        linked = rv.linked_event(entry, by_id)
        out = _entry_out(entry, rv.action(entry, linked), fps)
        if linked is not None:
            on_event[linked.id] = out
        else:
            mine.append(out)

    events = []
    for e in doc.events:
        v = event_view(e)
        ref = e.time.ref_proxy_frame
        lo, hi = e.time.window_proxy_frames
        events.append(
            StrokeEventOut(
                id=e.id,
                state=v.state.value,
                status=e.status.value,
                label=STATE_LABEL[v.state],
                time_s=t(ref) if ref is not None else None,
                frame=ref,
                window_start_s=t(lo),
                window_end_s=t(hi),
                reasons=list(v.reasons),
                reason_codes=list(e.reasons),
                regions=[
                    RegionVisibilityOut(
                        region=r.region.value,
                        label=r.label,
                        state=r.state.value,
                        state_label=r.state_label,
                        used=r.used,
                        reasons=list(r.reasons),
                    )
                    for r in v.regions
                ],
                review=on_event.get(e.id),
            )
        )
    stretches = []
    for s in doc.coverage.not_assessable if doc.coverage else []:
        sv = stretch_view(s)
        stretches.append(
            NotAssessableOut(
                label=STATE_LABEL[V1State.NOT_ASSESSABLE],
                start_s=t(s.start_proxy_frame),
                end_s=t(s.end_proxy_frame),
                start_frame=s.start_proxy_frame,
                end_frame=s.end_proxy_frame,
                regions=[r.value for r in s.regions],
                reason=sv.reason,
            )
        )
    events.sort(key=lambda x: (x.window_start_s, x.id))
    stretches.sort(key=lambda x: x.start_s)
    return StrokesOut(
        analysis_id=analysis_id,
        video_id=video_id,
        algorithm=doc.producer.algorithm,
        algorithm_version=doc.producer.algorithm_version,
        experimental=doc.producer.status is ProducerStatus.EXPERIMENTAL,
        fps=fps,
        provisional_params=_params(doc),
        note=NOTE,
        events=events,
        not_assessable=stretches,
        frame_count=frame_count(doc),
        my_strokes=sorted(mine, key=lambda m: (m.frame, m.id)),
        reviewed_stretches=[
            ReviewedStretchOut(
                id=s.id,
                start_frame=s.start_frame,
                end_frame=s.end_frame,
                start_s=t(s.start_frame),
                end_s=t(s.end_frame),
            )
            for s in sorted(review.reviewed_stretches, key=lambda s: s.start_frame)
        ],
    )
