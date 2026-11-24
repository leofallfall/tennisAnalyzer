"""Personal V1 view of a ``strokes.json`` for the app (ADR-025, ADR-027, ADR-028).

Read-only: states and reasons come from ``tennis_cv.strokes.states``; no decision changes.
Times are put on the proxy timeline (``frame / fps``), the one the player shows, also when
the file's ``time_basis`` is ``source``.
"""

from __future__ import annotations

from pathlib import Path

from tennis_api.schemas import NotAssessableOut, StrokeEventOut, StrokesOut
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


def strokes_view(path: Path, analysis_id: str, video_id: str) -> StrokesOut:
    doc = load_strokes(path)
    fps = doc.inputs.pose.fps

    def t(frame: int) -> float:
        return round(frame / fps, 3)

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
    )
