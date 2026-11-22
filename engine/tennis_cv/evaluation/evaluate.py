"""Evaluation of candidate events against manual labels (M2.3, PR 2 of the plan).

Reads a ``strokes.json`` per clip (1.x, or the M2.1 minimal format as instant events),
the labels and the annotated intervals, and builds ``strokes_eval.json``
(:mod:`~tennis_cv.evaluation.eval_artifact`). Rules, all from the approved M2.3
proposal (ADR-021, ADR-022, P1-P7, P3 bis):

* **Time.** Labels, peaks and events are placed with ``t(f)`` from their proxy frames
  (:mod:`~tennis_cv.evaluation.clip_time`), never from the seconds written in the file.
  The file's ``time_basis`` and map status must be what ``build_timebase`` gives with the
  file's own parameters; every peak frame must be a sample; the seconds in the file must
  equal ``t(f)`` within the 4-decimal rounding. Any difference is an error.
* **Graph.** For each radius, from scratch: accepted and uncertain events vs target
  impacts (analysed player, ``targets`` types). Rejected events stay out of the graph.
  Connected components decide every outcome; ambiguous groups are never assigned.
* **Misses.** First cause that applies: ``no_pose``, ``both_wrists_unreliable``,
  ``event_rejected``, ``peak_discarded``, ``event_outside_radius`` (nearest *free* event
  within the search limit), ``no_peak``.
* **Unassociated events.** Second graph, same rules, vs every other label. A cause is
  given only inside an exhaustive interval and farther than r from its edges; otherwise
  ``not_judgeable_coverage`` or a position (``near_boundary``, ``outside_annotated``,
  ``no_intervals``). No event is dropped.
* **Radius check.** ``ok`` if ``2r + W < D`` (D = minimum gap between consecutive labels
  of the clip, all labels; W = actual maximum span of graph events); else
  ``not_interpretable``; fewer than two labels: ``not_applicable``. Results stay visible.

No threshold here is validated: radii and the search limit are exploratory parameters.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from pydantic import ValidationError

from tennis_cv import __version__
from tennis_cv.errors import TennisCVError
from tennis_cv.evaluation.annotated import (
    AnnotatedInterval,
    check_against_labels,
    coverage,
    load_annotated,
)
from tennis_cv.evaluation.association import (
    NUMERIC_TOLERANCE_S,
    Association,
    EventNode,
    GroupShape,
    LabelNode,
    associate,
    distance,
    signed_offset,
)
from tennis_cv.evaluation.clip_time import ClipTime, LabelFrameOutOfDomainError
from tennis_cv.evaluation.dataset import (
    ANALYSED_PLAYER,
    ClipData,
    DatasetFormatError,
    Label,
    LabelStrokeType,
    load_clip_data,
    load_labels,
    load_manifest,
)
from tennis_cv.evaluation.eval_artifact import (
    ClipEval,
    EdgeOut,
    EvalInputs,
    EvalParams,
    EvalProducer,
    EventInfo,
    FileRef,
    GapInfo,
    GroupOutcome,
    ImpactOutcome,
    ImpactResult,
    Interception,
    IntervalInfo,
    LabelInfo,
    LabelRole,
    Miss,
    MissCause,
    NearbyEvent,
    NearbyPeak,
    NearestTarget,
    OtherGroup,
    OtherGroupOutcome,
    OutsideRadius,
    ParamsSource,
    ParamsUsed,
    PeakOffset,
    Position,
    RadiusResult,
    RadiusStatus,
    StrokesEvalFile,
    StrokesFormat,
    StrokesRef,
    TargetGroup,
    Transition,
    UnassociatedCause,
    UnassociatedEvent,
    Verdict,
)
from tennis_cv.evaluation.matching import DEFAULT_TARGETS, ResultsFile
from tennis_cv.evaluation.matching import EventStatus as MinimalStatus
from tennis_cv.signals.smoothing import SmoothingParams
from tennis_cv.signals.timebase import TimeBaseParams
from tennis_cv.strokes.artifact import (
    EventStatus,
    PeakStatus,
    RepresentativeWristValue,
    StrokesFile,
    Wrist,
    parse_strokes,
    verify_inputs,
)

DEFAULT_RADII_S: tuple[float, ...] = (0.1, 0.2, 0.3, 0.5)
"""Exploratory radii (ADR-021), not acceptance criteria."""
DEFAULT_OUTSIDE_SEARCH_S = 2.0
"""Search limit of ``event_outside_radius``: diagnosis only, not an evaluation threshold."""
TIME_TOLERANCE_S = 0.5e-4 + 1e-9
"""Seconds written in strokes.json are rounded to 4 decimals: half a unit, plus float noise."""
STROKES_FILE = "strokes.json"


def _is_positive_finite(x: object) -> bool:
    """A real number (not bool), finite and strictly greater than zero."""
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def check_radii(radii: Iterable[float]) -> list[float]:
    """Radii as used by the evaluation: sorted, without duplicates. Every value must be a
    finite number > 0 (NaN, +inf, -inf, 0 and negatives are rejected) and the list must not
    be empty. Called before any computation by every public entry point."""
    values = list(radii)
    if not values:
        raise ValueError("radii: the list is empty, give at least one radius > 0")
    bad = [r for r in values if not _is_positive_finite(r)]
    if bad:
        raise ValueError(
            f"radii: every radius must be a finite number > 0 (got {', '.join(map(repr, bad))})"
        )
    return sorted(set(float(r) for r in values))


def check_outside_search(value: float) -> float:
    """Search limit of ``event_outside_radius``: a finite number > 0. Zero is rejected, as
    before: the limit bounds a search for an event *beyond* the radius, so 0 would make the
    cause unreachable without saying so."""
    if not _is_positive_finite(value):
        raise ValueError(f"outside_search_s: must be a finite number > 0 (got {value!r})")
    return float(value)


class StrokesParamsError(TennisCVError):
    """strokes.json does not record the parameters the evaluation must reuse."""

    code = "strokes_params_invalid"


class StrokesTimeBasisMismatchError(TennisCVError):
    """The time basis or map status in strokes.json differs from the one rebuilt."""

    code = "strokes_time_basis_mismatch"


class StrokesPeakNotASampleError(TennisCVError):
    """A wrist peak sits on a proxy frame that is not a sample of the time base."""

    code = "strokes_peak_not_a_sample"


class StrokesTimeMismatchError(TennisCVError):
    """Seconds written in strokes.json differ from t(f) beyond the rounding."""

    code = "strokes_time_mismatch"


# ------------------------------------------------------------------ loaded events


@dataclass(frozen=True)
class _Peak:
    id: str
    wrist: Wrist
    frame: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class _Event:
    id: str
    status: EventStatus
    ref_frame: int | None
    window_frames: tuple[int, int]
    reasons: tuple[str, ...]
    peaks: tuple[_Peak, ...]

    @property
    def in_graph(self) -> bool:
        return self.status is not EventStatus.REJECTED


@dataclass(frozen=True)
class LoadedStrokes:
    """A strokes file reduced to what the evaluation reads."""

    file: str
    sha256: str
    format: StrokesFormat
    doc: StrokesFile | None
    events: tuple[_Event, ...]
    discarded_peaks: tuple[_Peak, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_events(path: str | Path) -> LoadedStrokes:
    """Read ``strokes.json`` 1.x (key ``schema`` present) or the M2.1 minimal format."""
    p = Path(path)
    if not p.is_file():
        raise DatasetFormatError(
            "Results file not found", details={"problems": [f"{p.name}: file does not exist"]}
        )
    data = p.read_bytes()
    try:
        raw = json.loads(data)
    except ValueError as exc:
        raise DatasetFormatError(
            "Results file is not JSON", details={"problems": [f"{p.name}: {exc}"]}
        ) from exc
    if isinstance(raw, dict) and "schema" in raw:
        return _from_strokes(p.name, _sha256(data), parse_strokes(data))
    try:
        minimal = ResultsFile.model_validate(raw)
    except ValidationError as exc:
        raise DatasetFormatError(
            "Results file is invalid", details={"problems": [f"{p.name}: {exc}"]}
        ) from exc
    status = {
        MinimalStatus.CLASSIFIED: EventStatus.ACCEPTED,
        MinimalStatus.UNCERTAIN: EventStatus.UNCERTAIN,
        MinimalStatus.REJECTED: EventStatus.REJECTED,
    }
    ids = [e.id for e in minimal.events]
    if any(i is not None for i in ids) and len({i for i in ids if i}) != len(ids):
        raise DatasetFormatError(
            "Results file is invalid",
            details={"problems": [f"{p.name}: event ids must be all present and unique"]},
        )
    events = tuple(
        _Event(
            id=e.id or f"m{n:04d}",
            status=status[e.status],
            ref_frame=e.peak_frame,
            window_frames=(e.peak_frame, e.peak_frame),
            reasons=(),
            peaks=(),
        )
        for n, e in enumerate(minimal.events, start=1)
    )
    return LoadedStrokes(p.name, _sha256(data), StrokesFormat.M2_1_MINIMAL, None, events, ())


def _from_strokes(name: str, sha: str, doc: StrokesFile) -> LoadedStrokes:
    peaks = {
        pk.id: _Peak(pk.id, pk.wrist, pk.proxy_frame, tuple(pk.reasons)) for pk in doc.wrist_peaks
    }
    events = tuple(
        _Event(
            id=e.id,
            status=e.status,
            ref_frame=(
                e.time.ref_proxy_frame
                if e.representative_wrist.value is not RepresentativeWristValue.UNDETERMINED
                else None
            ),
            window_frames=e.time.window_proxy_frames,
            reasons=tuple(e.reasons),
            peaks=tuple(peaks[i] for i in e.grouping.peak_ids),
        )
        for e in doc.events
    )
    discarded = tuple(peaks[pk.id] for pk in doc.wrist_peaks if pk.status is PeakStatus.DISCARDED)
    return LoadedStrokes(name, sha, StrokesFormat.STROKES_1, doc, events, discarded)


def _params_from(doc: StrokesFile | None) -> tuple[TimeBaseParams, SmoothingParams, ParamsSource]:
    if doc is None:
        return TimeBaseParams(), SmoothingParams(), ParamsSource.DEFAULTS
    signals = doc.params.get("signals")
    tb = signals.get("timebase") if isinstance(signals, dict) else None
    sm = signals.get("smoothing") if isinstance(signals, dict) else None
    problems: list[str] = []
    tb_fields = {f.name for f in dataclasses.fields(TimeBaseParams)}
    if not isinstance(tb, dict) or set(tb) != tb_fields:
        problems.append(f"params.signals.timebase must have exactly {sorted(tb_fields)}")
    needed = ("good_visibility", "usable_visibility")
    if not isinstance(sm, dict) or any(not isinstance(sm.get(k), int | float) for k in needed):
        problems.append(f"params.signals.smoothing must record {', '.join(needed)}")
    if problems:
        raise StrokesParamsError(
            "strokes.json does not record the signal parameters", details={"problems": problems}
        )
    assert isinstance(tb, dict) and isinstance(sm, dict)
    try:
        tbp = TimeBaseParams(**{k: float(v) for k, v in tb.items()})  # type: ignore[arg-type]
        smp = SmoothingParams(
            good_visibility=float(sm["good_visibility"]),  # type: ignore[arg-type]
            usable_visibility=float(sm["usable_visibility"]),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        raise StrokesParamsError(
            "strokes.json signal parameters are invalid", details={"problems": [str(exc)]}
        ) from exc
    return tbp, smp, ParamsSource.STROKES_JSON


def _check_against_time(loaded: LoadedStrokes, ct: ClipTime) -> None:
    """File vs rebuilt time base: basis, map status, peak samples, written seconds."""
    doc = loaded.doc
    if doc is None:
        for e in loaded.events:
            assert e.ref_frame is not None
            if not ct.in_domain(e.ref_frame):
                raise LabelFrameOutOfDomainError(
                    f"event {e.id} is outside clip {ct.clip_id}",
                    details={"problems": [f"event {e.id}: peak_frame {e.ref_frame}"]},
                )
        return
    if doc.time_basis.value != ct.basis.value or doc.inputs.timemap.status != ct.map_status:
        raise StrokesTimeBasisMismatchError(
            f"strokes.json of clip {ct.clip_id} declares another time basis",
            details={
                "problems": [
                    f"file: time_basis {doc.time_basis.value}, map {doc.inputs.timemap.status}; "
                    f"rebuilt: {ct.basis.value}, map {ct.map_status.value}"
                ]
            },
        )
    not_samples = [p.id for p in doc.wrist_peaks if not ct.is_sample(p.proxy_frame)]
    if not_samples:
        raise StrokesPeakNotASampleError(
            f"peaks of clip {ct.clip_id} are not on time-base samples",
            details={"problems": [f"peaks {', '.join(not_samples)}"]},
        )
    bad: list[str] = []

    def check(what: str, frame: int | None, written: float | None) -> None:
        if frame is None or written is None:
            return
        t = ct.time(frame)
        if abs(t - written) > TIME_TOLERANCE_S:
            bad.append(f"{what}: written {written}, t({frame}) = {t:.6f}")

    for p in doc.wrist_peaks:
        check(f"peak {p.id}", p.proxy_frame, p.t_s)
    for ce in doc.events:
        check(f"event {ce.id} ref", ce.time.ref_proxy_frame, ce.time.ref_t_s)
        for k in (0, 1):
            check(
                f"event {ce.id} window[{k}]", ce.time.window_proxy_frames[k], ce.time.window_t_s[k]
            )
    if bad:
        raise StrokesTimeMismatchError(
            f"strokes.json of clip {ct.clip_id} does not match t(f) of its inputs",
            details={"problems": bad},
        )


# ------------------------------------------------------------------ per clip


@dataclass(frozen=True)
class _Placed:
    event: _Event
    node: EventNode
    window: tuple[float, float]


def _place(e: _Event, ct: ClipTime) -> _Placed:
    a, b = ct.time(e.window_frames[0]), ct.time(e.window_frames[1])
    if e.ref_frame is not None:
        t = ct.time(e.ref_frame)
        return _Placed(e, EventNode(e.id, t, t), (a, b))
    return _Placed(e, EventNode(e.id, a, b), (a, b))


def _edges(assoc: Association) -> list[EdgeOut]:
    return [
        EdgeOut(
            event_id=x.event_id,
            label_line=int(x.label_id),
            distance_s=x.distance_s,
            signed_s=x.signed_s,
        )
        for x in assoc.edges
    ]


def _lines(ids: Iterable[str]) -> list[int]:
    return sorted(int(i) for i in ids)


def _gaps(labels: list[LabelInfo], placed: list[_Placed]) -> GapInfo:
    ordered = sorted(labels, key=lambda lb: (lb.t_s, lb.line))
    gap: float | None = None
    gap_lines: tuple[int, int] | None = None
    for x, y in pairwise(ordered):
        d = y.t_s - x.t_s
        if gap is None or d < gap:
            gap, gap_lines = d, (x.line, y.line)
    span, span_id = 0.0, None
    for p in sorted((p for p in placed if p.event.in_graph), key=lambda p: (p.window, p.event.id)):
        w = p.window[1] - p.window[0]
        if span_id is None or w > span:
            span, span_id = w, p.event.id
    return GapInfo(
        min_label_gap_s=gap, gap_lines=gap_lines, max_event_span_s=span, max_span_event_id=span_id
    )


def _radius_status(r: float, gaps: GapInfo) -> tuple[RadiusStatus, str | None]:
    if gaps.min_label_gap_s is None:
        return RadiusStatus.NOT_APPLICABLE, "fewer_than_two_labels"
    lhs = 2 * r + gaps.max_event_span_s
    if lhs < gaps.min_label_gap_s:
        return RadiusStatus.OK, None
    return (
        RadiusStatus.NOT_INTERPRETABLE,
        f"2r + W = {lhs:.4f} s is not below D = {gaps.min_label_gap_s:.4f} s",
    )


def _position(
    window: tuple[float, float], r: float, intervals: list[tuple[AnnotatedInterval, float, float]]
) -> tuple[Position, AnnotatedInterval | None]:
    if not intervals:
        return Position.NO_INTERVALS, None
    a, b = window
    touching = [iv for iv in intervals if a <= iv[2] and b >= iv[1]]
    if not touching:
        return Position.OUTSIDE_ANNOTATED, None
    iv, start, end = touching[0]
    if (
        len(touching) == 1
        and a - start > r + NUMERIC_TOLERANCE_S
        and end - b > (r + NUMERIC_TOLERANCE_S)
    ):
        return Position.INSIDE, iv
    return Position.NEAR_BOUNDARY, iv


def _miss(
    label: LabelInfo,
    r: float,
    ct: ClipTime,
    placed: list[_Placed],
    loaded: LoadedStrokes,
    free: list[_Placed],
    search_s: float,
) -> Miss:
    tol = NUMERIC_TOLERANCE_S
    t = label.t_s
    rejected = [
        NearbyEvent(
            event_id=p.event.id, distance_s=distance(p.node, t), reasons=list(p.event.reasons)
        )
        for p in placed
        if not p.event.in_graph and distance(p.node, t) <= r + tol
    ]
    discarded = [
        NearbyPeak(
            peak_id=pk.id, wrist=pk.wrist, signed_s=ct.time(pk.frame) - t, reasons=list(pk.reasons)
        )
        for pk in sorted(loaded.discarded_peaks, key=lambda x: (x.frame, x.id))
        if abs(ct.time(pk.frame) - t) <= r + tol
    ]
    near = sorted(
        (p for p in free if distance(p.node, t) <= search_s + tol),
        key=lambda p: (distance(p.node, t), p.event.id),
    )
    nearest = (
        OutsideRadius(
            event_id=near[0].event.id, signed_s=signed_offset(near[0].node, t), shared_with=[]
        )
        if near
        else None
    )
    if not ct.any_pose_in_window(t, r, tol):
        cause = MissCause.NO_POSE
    elif not ct.any_wrist_usable_in_window(t, r, tol):
        cause = MissCause.BOTH_WRISTS_UNRELIABLE
    elif rejected:
        cause = MissCause.EVENT_REJECTED
    elif discarded:
        cause = MissCause.PEAK_DISCARDED
    elif nearest is not None:
        cause = MissCause.EVENT_OUTSIDE_RADIUS
    else:
        cause = MissCause.NO_PEAK
    return Miss(
        cause=cause, rejected_events=rejected, discarded_peaks=discarded, nearest_free_event=nearest
    )


def _interception(e: _Event, label: LabelInfo, ct: ClipTime) -> Interception:
    return Interception(
        event_id=e.id,
        event_status=e.status,
        event_signed_s=None if e.ref_frame is None else ct.time(e.ref_frame) - label.t_s,
        event_signed_frames=None if e.ref_frame is None else e.ref_frame - label.proxy_frame,
        peaks=[
            PeakOffset(
                peak_id=pk.id,
                wrist=pk.wrist,
                signed_s=ct.time(pk.frame) - label.t_s,
                signed_frames=pk.frame - label.proxy_frame,
            )
            for pk in sorted(e.peaks, key=lambda x: (x.frame, x.id))
        ],
    )


def _unassociated_cause(label: LabelInfo) -> UnassociatedCause:
    if label.player != ANALYSED_PLAYER:
        return UnassociatedCause.OTHER_PLAYER
    if label.stroke_type is LabelStrokeType.NONE:
        return UnassociatedCause.NOT_A_STROKE
    return UnassociatedCause.NON_TARGET_STROKE


def _evaluate_radius(
    r: float,
    ct: ClipTime,
    loaded: LoadedStrokes,
    placed: list[_Placed],
    labels: list[LabelInfo],
    intervals: list[tuple[AnnotatedInterval, float, float]],
    gaps: GapInfo,
    search_s: float,
) -> RadiusResult:
    by_line = {lb.line: lb for lb in labels}
    by_id = {p.event.id: p for p in placed}
    graph = [p for p in placed if p.event.in_graph]
    targets = [lb for lb in labels if lb.role is LabelRole.TARGET]
    others = [lb for lb in labels if lb.role is LabelRole.OTHER]
    tassoc = associate(
        [p.node for p in graph], [LabelNode(str(lb.line), lb.t_s) for lb in targets], r, "t"
    )
    tgroups: list[TargetGroup] = []
    for g in tassoc.groups:
        if g.shape is GroupShape.ASSOCIATED:
            st = by_id[g.event_ids[0]].event.status
            outcome = (
                GroupOutcome.INTERCEPTED
                if st is EventStatus.ACCEPTED
                else GroupOutcome.INTERCEPTED_UNCERTAIN
            )
        elif g.shape is GroupShape.LABEL_ONLY:
            outcome = GroupOutcome.MISSED
        elif g.shape is GroupShape.EVENT_ONLY:
            outcome = GroupOutcome.UNASSOCIATED_EVENT
        else:
            outcome = GroupOutcome.AMBIGUOUS
        tgroups.append(
            TargetGroup(
                id=g.id,
                event_ids=list(g.event_ids),
                label_lines=_lines(g.label_ids),
                ambiguity=list(g.ambiguity),
                outcome=outcome,
            )
        )
    free = [by_id[g.event_ids[0]] for g in tassoc.groups if g.shape is GroupShape.EVENT_ONLY]

    impacts: list[ImpactResult] = []
    for lb in sorted(targets, key=lambda x: (x.t_s, x.line)):
        g = tassoc.group_of_label(str(lb.line))
        interception: Interception | None = None
        miss: Miss | None = None
        if g.shape is GroupShape.ASSOCIATED:
            ev = by_id[g.event_ids[0]].event
            outcome_i = (
                ImpactOutcome.INTERCEPTED
                if ev.status is EventStatus.ACCEPTED
                else ImpactOutcome.INTERCEPTED_UNCERTAIN
            )
            interception = _interception(ev, lb, ct)
        elif g.shape is GroupShape.LABEL_ONLY:
            outcome_i = ImpactOutcome.MISSED
            miss = _miss(lb, r, ct, placed, loaded, free, search_s)
        else:
            outcome_i = ImpactOutcome.AMBIGUOUS
        irregular = ct.irregular_intervals_in_window(lb.t_s, r, NUMERIC_TOLERANCE_S)
        impacts.append(
            ImpactResult(
                label_line=lb.line,
                group_id=g.id,
                outcome=outcome_i,
                interception=interception,
                miss=miss,
                irregular_intervals_in_window=irregular,
                irregular_time_in_window=irregular > 0,
            )
        )
    # the same free event nearest to several missed impacts: cite it for all, assign none
    citing: dict[str, list[int]] = {}
    for imp in impacts:
        if imp.miss is not None and imp.miss.nearest_free_event is not None:
            citing.setdefault(imp.miss.nearest_free_event.event_id, []).append(imp.label_line)
    for imp in impacts:
        if imp.miss is not None and imp.miss.nearest_free_event is not None:
            nf = imp.miss.nearest_free_event
            nf.shared_with = sorted(x for x in citing[nf.event_id] if x != imp.label_line)

    oassoc = associate(
        [p.node for p in free], [LabelNode(str(lb.line), lb.t_s) for lb in others], r, "o"
    )
    ogroups = [
        OtherGroup(
            id=g.id,
            event_ids=list(g.event_ids),
            label_lines=_lines(g.label_ids),
            ambiguity=list(g.ambiguity),
            outcome={
                GroupShape.ASSOCIATED: OtherGroupOutcome.ASSOCIATED,
                GroupShape.EVENT_ONLY: OtherGroupOutcome.UNLABELLED,
                GroupShape.LABEL_ONLY: OtherGroupOutcome.LABEL_ONLY,
                GroupShape.AMBIGUOUS: OtherGroupOutcome.AMBIGUOUS,
            }[g.shape],
        )
        for g in oassoc.groups
    ]
    unassociated: list[UnassociatedEvent] = []
    for p in sorted(free, key=lambda p: (p.node.start_s, p.event.id)):
        og = oassoc.group_of_event(p.event.id)
        nearest = min(
            targets,
            key=lambda lb: (distance(p.node, lb.t_s), lb.line),
            default=None,
        )
        position, iv = _position((p.node.start_s, p.node.end_s), r, intervals)
        verdict = Verdict.NOT_JUDGED_POSITION
        cause: UnassociatedCause | None = None
        cause_line: int | None = None
        missing_types: list[LabelStrokeType] = []
        missing_players: str | None = None
        if position is Position.INSIDE and iv is not None:
            cov = coverage(iv)
            if cov.exhaustive:
                verdict = Verdict.JUDGED
                if og.shape is GroupShape.ASSOCIATED:
                    cause_line = int(og.label_ids[0])
                    cause = _unassociated_cause(by_line[cause_line])
                elif og.shape is GroupShape.EVENT_ONLY:
                    cause = UnassociatedCause.UNLABELLED
                else:
                    cause = UnassociatedCause.AMBIGUOUS
            else:
                verdict = Verdict.NOT_JUDGEABLE_COVERAGE
                missing_types = list(cov.missing_types)
                missing_players = cov.missing_players
        unassociated.append(
            UnassociatedEvent(
                event_id=p.event.id,
                target_group_id=tassoc.group_of_event(p.event.id).id,
                other_group_id=og.id,
                nearest_target=(
                    None
                    if nearest is None
                    else NearestTarget(
                        label_line=nearest.line, signed_s=signed_offset(p.node, nearest.t_s)
                    )
                ),
                position=position,
                interval_line=None if iv is None else iv.line,
                verdict=verdict,
                cause=cause,
                cause_label_line=cause_line,
                missing_types=missing_types,
                missing_players=missing_players,
            )
        )
    status, reason = _radius_status(r, gaps)
    return RadiusResult(
        radius_s=r,
        status=status,
        status_reason=reason,
        target_edges=_edges(tassoc),
        target_groups=tgroups,
        other_edges=_edges(oassoc),
        other_groups=ogroups,
        impacts=impacts,
        unassociated_events=unassociated,
    )


def _transitions(radii: list[RadiusResult]) -> list[Transition]:
    out: list[Transition] = []
    for prev, cur in pairwise(radii):
        before = {i.label_line: i.outcome for i in prev.impacts}
        for imp in cur.impacts:
            if before[imp.label_line] is not imp.outcome:
                out.append(
                    Transition(
                        label_line=imp.label_line,
                        from_radius_s=prev.radius_s,
                        from_outcome=before[imp.label_line],
                        to_radius_s=cur.radius_s,
                        to_outcome=imp.outcome,
                    )
                )
    return sorted(out, key=lambda x: (x.label_line, x.from_radius_s))


def evaluate_clip(
    clip: ClipData,
    labels: list[Label],
    intervals: list[AnnotatedInterval],
    loaded: LoadedStrokes,
    *,
    radii: Iterable[float] = DEFAULT_RADII_S,
    targets: frozenset[LabelStrokeType] = DEFAULT_TARGETS,
    outside_search_s: float = DEFAULT_OUTSIDE_SEARCH_S,
) -> ClipEval:
    """Evaluate one clip (labels and intervals of other clips are ignored)."""
    cid = clip.clip.clip_id
    labels = [lb for lb in labels if lb.clip_id == cid]
    intervals = [iv for iv in intervals if iv.clip_id == cid]
    radii_sorted = check_radii(radii)
    outside_search_s = check_outside_search(outside_search_s)
    tbp, smp, source = _params_from(loaded.doc)
    ct = ClipTime(cid, clip.poses, clip.timemap, tbp, smp)
    out_of_domain = [
        f"labels.csv:{lb.line}: frame {lb.proxy_frame}"
        for lb in labels
        if not ct.in_domain(lb.proxy_frame)
    ]
    if out_of_domain:
        raise LabelFrameOutOfDomainError(
            f"labels of clip {cid} are outside the clip", details={"problems": out_of_domain}
        )
    _check_against_time(loaded, ct)

    label_infos: list[LabelInfo] = []
    for lb in sorted(labels, key=lambda x: x.line):
        fi = ct.frame_info(lb.proxy_frame)
        label_infos.append(
            LabelInfo(
                line=lb.line,
                proxy_frame=lb.proxy_frame,
                stroke_type=lb.stroke_type,
                player=lb.player,
                visibility=lb.visibility,
                overlay_wrist=lb.overlay_wrist,
                role=(
                    LabelRole.TARGET
                    if lb.player == ANALYSED_PLAYER and lb.stroke_type in targets
                    else LabelRole.OTHER
                ),
                t_s=fi.t_s,
                frame_state=fi.state,
                kinematic_sample=fi.kinematic_sample,
                kept_frame=fi.kept_frame,
                kept_frame_detected=fi.kept_frame_detected,
                nearest_sample_dt_s=fi.nearest_sample_dt_s,
                estimated_frame=lb.visibility.value == "partial",
            )
        )
    placed = sorted((_place(e, ct) for e in loaded.events), key=lambda p: (p.window, p.event.id))
    timed = [
        (iv, ct.time(iv.start_frame), ct.time(iv.end_frame))
        for iv in sorted(intervals, key=lambda x: x.start_frame)
    ]
    gaps = _gaps(label_infos, placed)
    results = [
        _evaluate_radius(r, ct, loaded, placed, label_infos, timed, gaps, outside_search_s)
        for r in radii_sorted
    ]
    warnings: list[str] = []
    if not intervals:
        warnings.append(f"clip {cid}: no annotated intervals, unassociated events not judged")
    if ct.unverified_time:
        warnings.append(f"clip {cid}: no consistent timemap, proxy times (unverified_time)")
    warnings += [f"clip {cid}: {p}" for p in ct.base.map_problems]
    doc = loaded.doc
    return ClipEval(
        clip_id=cid,
        strokes=StrokesRef(
            file=loaded.file,
            sha256=loaded.sha256,
            format=loaded.format,
            schema_version=None if doc is None else doc.schema_version,
            producer=(
                None
                if doc is None
                else f"{doc.producer.algorithm} {doc.producer.algorithm_version}"
            ),
        ),
        time_basis=ct.basis,
        map_status=ct.map_status,
        unverified_time=ct.unverified_time,
        params_used=ParamsUsed(
            source=source,
            timebase={k: float(v) for k, v in dataclasses.asdict(tbp).items()},
            good_visibility=smp.good_visibility,
            usable_visibility=smp.usable_visibility,
        ),
        intervals=[
            IntervalInfo(
                line=iv.line,
                start_frame=iv.start_frame,
                end_frame=iv.end_frame,
                start_t_s=s,
                end_t_s=e,
                exhaustive=coverage(iv).exhaustive,
                missing_types=list(coverage(iv).missing_types),
                missing_players=coverage(iv).missing_players,
            )
            for iv, s, e in timed
        ],
        labels=label_infos,
        events=[
            EventInfo(
                id=p.event.id,
                status=p.event.status,
                in_graph=p.event.in_graph,
                t_s=None if p.event.ref_frame is None else p.node.start_s,
                window_t_s=p.window,
                window_proxy_frames=p.event.window_frames,
                reasons=list(p.event.reasons),
            )
            for p in placed
        ],
        gaps=gaps,
        radii=results,
        transitions=_transitions(results),
        warnings=warnings,
    )


# ------------------------------------------------------------------ dataset


def _file_ref(path: Path) -> FileRef:
    return FileRef(file=path.name, sha256=_sha256(path.read_bytes()))


def strokes_path(clip: ClipData, results_dir: Path | None) -> Path:
    """``<results_dir>/<clip_id>.strokes.json`` or ``strokes.json`` in the pose folder."""
    if results_dir is not None:
        return results_dir / f"{clip.clip.clip_id}.strokes.json"
    return clip.clip.pose_dir / STROKES_FILE


def _frame_count(c: ClipData) -> int:
    if c.timemap is not None:
        return c.timemap.summary.proxy_frame_count
    return c.frame_count


def evaluate_dataset(
    manifest: Path,
    labels_path: Path,
    annotated: Path | None = None,
    *,
    results_dir: Path | None = None,
    radii: Iterable[float] = DEFAULT_RADII_S,
    targets: frozenset[LabelStrokeType] = DEFAULT_TARGETS,
    outside_search_s: float = DEFAULT_OUTSIDE_SEARCH_S,
    check_input_hashes: bool = True,
    now: datetime | None = None,
) -> StrokesEvalFile:
    """Evaluate every manifest clip that has a strokes file; list the others."""
    radii_list = check_radii(radii)
    outside_search_s = check_outside_search(outside_search_s)
    if not targets:
        raise ValueError("targets must not be empty")
    clips = load_manifest(manifest)
    labels = load_labels(labels_path)
    data = [load_clip_data(c) for c in clips]
    known = {c.clip.clip_id for c in data}
    unknown = [
        f"{labels_path.name}:{lb.line}: unknown clip_id {lb.clip_id!r}"
        for lb in labels
        if lb.clip_id not in known
    ]
    if unknown:
        raise DatasetFormatError("Labels do not match the clips", details={"problems": unknown})
    intervals: list[AnnotatedInterval] = []
    warnings: list[str] = []
    if annotated is not None:
        intervals = load_annotated(annotated)
        problems, warns = check_against_labels(
            intervals,
            labels,
            {c.clip.clip_id: c.clip for c in data},
            {c.clip.clip_id: _frame_count(c) for c in data},
            file_name=annotated.name,
            labels_name=labels_path.name,
        )
        if problems:
            raise DatasetFormatError(
                "Annotated intervals do not match the labels", details={"problems": problems}
            )
        warnings += warns
    else:
        warnings.append("no annotated.csv: coverage unknown, unassociated events not judged")
    results: list[ClipEval] = []
    without: list[str] = []
    for c in data:
        path = strokes_path(c, results_dir)
        if not path.is_file():
            without.append(c.clip.clip_id)
            continue
        loaded = load_events(path)
        if check_input_hashes and loaded.doc is not None:
            verify_inputs(loaded.doc, c.clip.pose_dir)
        results.append(
            evaluate_clip(
                c,
                labels,
                intervals,
                loaded,
                radii=radii_list,
                targets=targets,
                outside_search_s=outside_search_s,
            )
        )
    if without:
        warnings.append(f"no strokes file for clips: {', '.join(without)} (not evaluated)")
    if not check_input_hashes:
        warnings.append("input hashes of strokes.json not checked")
    return StrokesEvalFile(
        created_at=now or datetime.now(UTC),
        producer=EvalProducer(engine_version=__version__),
        inputs=EvalInputs(
            manifest=_file_ref(manifest),
            labels=_file_ref(labels_path),
            annotated=None if annotated is None else _file_ref(annotated),
        ),
        params=EvalParams(
            radii_s=radii_list,
            targets=sorted(targets),
            target_player=ANALYSED_PLAYER,
            numeric_tolerance_s=NUMERIC_TOLERANCE_S,
            time_tolerance_s=TIME_TOLERANCE_S,
            outside_radius_search_s=outside_search_s,
        ),
        clips=results,
        clips_without_results=without,
        warnings=warnings,
    )


def summary_lines(doc: StrokesEvalFile) -> list[str]:
    """Counts per clip and radius, for the console (the data is in the JSON)."""
    out: list[str] = []
    for c in doc.clips:
        n_targets = sum(lb.role is LabelRole.TARGET for lb in c.labels)
        out.append(
            f"{c.clip_id}: {c.strokes.format.value}, basis {c.time_basis.value}, "
            f"{n_targets} target impacts, {sum(e.in_graph for e in c.events)} graph events, "
            f"{sum(not e.in_graph for e in c.events)} rejected"
        )
        for rr in c.radii:
            counts: dict[str, int] = {}
            for imp in rr.impacts:
                counts[imp.outcome.value] = counts.get(imp.outcome.value, 0) + 1
            verdicts: dict[str, int] = {}
            for ue in rr.unassociated_events:
                verdicts[ue.verdict.value] = verdicts.get(ue.verdict.value, 0) + 1
            imp_txt = ", ".join(f"{k} {v}/{n_targets}" for k, v in sorted(counts.items()))
            ue_txt = ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items()))
            out.append(
                f"  r={rr.radius_s:g}s [{rr.status.value}]: {imp_txt or 'no target impacts'}; "
                f"unassociated events {len(rr.unassociated_events)}"
                + (f" ({ue_txt})" if ue_txt else "")
            )
    return out
