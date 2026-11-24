"""M2.3 stroke detector, intervention I2: wrist peaks grouped into candidate events.

Experimental. Writes ``strokes.json`` 1.1 from ``pose.json`` (+ ``timemap.json``). Uses the
M2.2 signals and the I1 peaks unchanged; no labels, no player configuration (both wrists
are treated the same way). Rules and parameters: docs/M2_3_EVENTS.md section 9.

Steps (fixed order)
-------------------
1. **Peaks** (I1, ``strokes.peaks``): every local maximum of both wrists. A peak is
   *discarded* when it has no torso scale (``peak_no_torso_scale``) or a prominence below
   ``min_prominence_torso_per_s`` (``peak_below_min_prominence``, the initial provisional
   2.0). Internal and run-edge peaks follow the same rule but stay identifiable
   (``prominence.category``). V1 review 1 (ADR-029): a peak is also discarded when the
   tracking cannot be trusted around it, i.e. in most samples of its quality window the
   skeleton is mostly outside the image (``peak_out_of_frame``) or its torso is far smaller
   than usual in the clip, a skeleton on someone or something else
   (``peak_tracking_jump``). Those stretches become *not assessable* (step 6).
2. **Grouping** (chain): kept peaks of both wrists in time order; a new event starts when the
   gap to the previous peak is more than G. One gesture is one event, never one per peak.
3. **Representative wrist**: the wrist of the most prominent *internal* peak of the event
   (ties: higher speed, then earlier, then left). Its peak gives the reference time. With
   only run-edge peaks the wrist is ``undetermined`` (one-sided prominences are not
   comparable) and the event is a window.
4. **Regions**: torso, left wrist, right wrist, legs, judged on the samples within
   ``half_window_s`` (M2.2 fit half-window) of the event window.
5. **Decision** (I2: no height thresholds, observability only): ``uncertain`` when the
   representative wrist is undetermined, the chain is longer than ``max_single_span_s``
   (possible merge of two strokes, never split automatically), no peak of the event has a
   mostly GOOD quality window, the representative peak's window has an irregular interval,
   or a region used is not observable; otherwise ``accepted``. Height thresholds (ADR-027)
   reject or make uncertain; V1 review 1 (ADR-029): of the non-rejected events closer than
   ``min_event_gap_s`` only the strongest is kept, the others are rejected as duplicates
   (``reject_near_stronger_event``).
6. **Coverage**: stretches of at least ``coverage_min_duration_s`` where no stroke could
   have been found: neither wrist has a speed value, the torso scale is missing, or the
   tracking cannot be trusted (out of frame / tracking jump, plus the window of every peak
   discarded for that). Insufficient evidence, not absence of strokes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from tennis_cv import __version__
from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.body import Side
from tennis_cv.signals.scale import ScaleParams, ScaleState, TorsoScale, torso_scale
from tennis_cv.signals.smoothing import (
    KeypointQuality,
    SmoothingParams,
    SmoothState,
    extract_keypoint,
)
from tennis_cv.signals.timebase import (
    IntervalStatus,
    MapStatus,
    TimeBase,
    TimeBaseParams,
    TimeBasis,
    build_timebase,
    check_timemap,
)
from tennis_cv.signals.velocity import KeypointVelocity, VelocityState, keypoint_velocity
from tennis_cv.strokes.artifact import _TOL as TIME_TOLERANCE_S
from tennis_cv.strokes.artifact import (
    DECIMALS,
    DECISION_RULE,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    CandidateEvent,
    Coverage,
    CoverageStretch,
    EventReliability,
    EventStatus,
    EventTime,
    Evidence,
    EvidenceState,
    Grouping,
    GroupLink,
    Inputs,
    PeakCategoryValue,
    PeakProminence,
    PeakStatus,
    PoseInput,
    Producer,
    ProducerStatus,
    Provenance,
    QualityWindow,
    Region,
    RegionName,
    RegionReason,
    Regions,
    RegionState,
    RepresentativeWrist,
    RepresentativeWristValue,
    Rule,
    RuleKind,
    SampleCounts,
    Speed,
    StrokesFile,
    TimemapInput,
    TraceOutcome,
    TraceStep,
    Wrist,
    WristEvidence,
    WristPeak,
    file_sha256,
    params_sha256,
)
from tennis_cv.strokes.peaks import (
    SIGNAL,
    WRIST_KEYPOINT,
    CandidatePeak,
    PeakCategory,
    keypoint_present,
    wrist_peaks,
)
from tennis_cv.video.timemap import TimeMap

BoolArray = npt.NDArray[np.bool_]


def _r(x: float) -> float:
    """Times are rounded to the written precision once, so the in-memory document and the
    written one satisfy the same invariants (links <= G, events > G apart)."""
    return round(float(x), DECIMALS)


def _t(p: _Peak) -> float:
    return _r(p.src.sample.t_s)


ALGORITHM = "tpa.stroke_detector"
ALGORITHM_VERSION = "0.3.0-v1r1"
TORSO_KEYPOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
_SHOULDERS = ("left_shoulder", "right_shoulder")
_HIPS = ("left_hip", "right_hip")
LEG_KEYPOINTS = ("left_knee", "right_knee", "left_ankle", "right_ankle")
_IRREGULAR = (IntervalStatus.LONG, IntervalStatus.SHORT, IntervalStatus.INVALID)
_TO_SMOOTH = {
    VelocityState.VALID: SmoothState.SMOOTHED,
    VelocityState.EDGE: SmoothState.EDGE,
    VelocityState.UNRELIABLE: SmoothState.UNRELIABLE,
    VelocityState.TOO_SHORT: SmoothState.TOO_SHORT,
}

# ----------------------------------------------------------------------------- rules

R_NO_SCALE = "peak_no_torso_scale"
R_BELOW_MIN = "peak_below_min_prominence"
R_GROUPING = "grouping_chain"
R_REP = "representative_wrist"
R_REP_ONLY_EDGE = "rep_only_run_edge_peaks"
R_UNDETERMINED = "uncertain_wrist_undetermined"
R_MERGE = "uncertain_possible_merge"
R_LOW = "uncertain_no_mostly_good_peak"
R_IRREGULAR = "uncertain_irregular_interval"
R_NOT_OBSERVABLE = "uncertain_region_not_observable"
R_PROXY = "info_time_basis_proxy"
R_HEIGHT_REJECT = "reject_low_relative_height"
R_HEIGHT_UNCERTAIN = "uncertain_low_relative_height"
R_NO_CLIP_REF = "uncertain_no_clip_reference"
R_OUT_OF_FRAME = "peak_out_of_frame"
R_TRACKING_JUMP = "peak_tracking_jump"
R_DUPLICATE = "reject_near_stronger_event"

RULES: tuple[Rule, ...] = (
    Rule(code=R_NO_SCALE, kind=RuleKind.DISCARD_PEAK,
         description="No torso scale at the peak: the prominence cannot be compared with "
                     "the minimum (torso lengths/s)"),
    Rule(code=R_BELOW_MIN, kind=RuleKind.DISCARD_PEAK,
         description="Prominence below detector.min_prominence_torso_per_s (initial "
                     "provisional value, not validated); internal and run-edge alike"),
    Rule(code=R_TRACKING_JUMP, kind=RuleKind.DISCARD_PEAK,
         description="In most samples of the peak quality window the torso is shorter than "
                     "tracking.min_torso_ratio times the clip median torso: the skeleton is "
                     "on someone or something else (provisional)"),
    Rule(code=R_OUT_OF_FRAME, kind=RuleKind.DISCARD_PEAK,
         description="In most samples of the peak quality window at least "
                     "tracking.out_of_frame_min_keypoints keypoints are outside the image: "
                     "the player is (partly) out of frame (provisional)"),
    Rule(code=R_GROUPING, kind=RuleKind.INFO,
         description="Kept peaks of both wrists chained in time order while consecutive "
                     "peaks are at most G apart"),
    Rule(code=R_REP, kind=RuleKind.REPRESENTATIVE_WRIST,
         description="Wrist of the most prominent internal peak (ties: speed, earlier, left)"),
    Rule(code=R_REP_ONLY_EDGE, kind=RuleKind.REPRESENTATIVE_WRIST,
         description="Only run-edge peaks (one-sided prominence): wrist not determined, the "
                     "event is a window"),
    Rule(code=R_HEIGHT_REJECT, kind=RuleKind.REJECT,
         description="Height peak (representative, or highest when the wrist is "
                     "undetermined) below decision.t_reject times the clip reference "
                     "(rel_to_clip); provisional, chosen on Development A/B"),
    Rule(code=R_HEIGHT_UNCERTAIN, kind=RuleKind.UNCERTAIN,
         description="Height peak below decision.t_accept times the clip reference "
                     "(rel_to_clip); provisional, chosen on Development A/B"),
    Rule(code=R_NO_CLIP_REF, kind=RuleKind.UNCERTAIN,
         description="Too few kept peaks in the clip for a reference height: the event "
                     "height cannot be compared"),
    Rule(code=R_UNDETERMINED, kind=RuleKind.UNCERTAIN,
         description="Representative wrist undetermined: no reference time"),
    Rule(code=R_MERGE, kind=RuleKind.UNCERTAIN,
         description="Chain longer than grouping.max_single_span_s: it may join two "
                     "strokes (never split automatically)"),
    Rule(code=R_LOW, kind=RuleKind.UNCERTAIN,
         description="No peak of the event has a quality window that is mostly GOOD "
                     "(GOOD samples more than half of the window)"),
    Rule(code=R_IRREGULAR, kind=RuleKind.UNCERTAIN,
         description="Representative peak: an irregular time interval in its quality window"),
    Rule(code=R_NOT_OBSERVABLE, kind=RuleKind.UNCERTAIN,
         description="A region used by the event is not observable"),
    Rule(code=R_DUPLICATE, kind=RuleKind.REJECT,
         description="Another non-rejected event less than grouping.min_event_gap_s away is "
                     "stronger (accepted before uncertain, determined wrist, then higher "
                     "rel_to_clip): this one is a duplicate of the same stroke (provisional)"),
    Rule(code=R_PROXY, kind=RuleKind.INFO,
         description="No usable time map: times are proxy times, not verified"),
    Rule(code=DECISION_RULE, kind=RuleKind.DECISION,
         description="rejected if the height reject or the duplicate rule failed; otherwise "
                     "uncertain if an uncertain rule failed; otherwise accepted"),
)  # fmt: skip


@dataclass(frozen=True)
class DetectorParams:
    """Every value is a starting value chosen on Development A/B, not validated."""

    min_prominence_torso_per_s: float = 2.0
    g_s: float = 0.3
    max_single_span_s: float = 1.0
    coverage_min_duration_s: float = 0.3
    rel_percentile: float = 90.0
    min_kept_peaks: int = 10
    t_reject: float = 0.45
    t_accept: float = 0.7
    min_event_gap_s: float = 1.0
    out_of_frame_min_keypoints: int = 9
    out_of_frame_margin: float = 0.01
    min_torso_ratio: float = 0.4
    smoothing: SmoothingParams = field(default_factory=SmoothingParams)
    scale: ScaleParams = field(default_factory=ScaleParams)
    timebase: TimeBaseParams = field(default_factory=TimeBaseParams)

    def __post_init__(self) -> None:
        for name in (
            "min_prominence_torso_per_s",
            "g_s",
            "max_single_span_s",
            "coverage_min_duration_s",
            "rel_percentile",
            "t_reject",
            "t_accept",
            "min_event_gap_s",
            "min_torso_ratio",
        ):
            v = getattr(self, name)
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be finite and > 0")

        if self.rel_percentile > 100:
            raise ValueError("rel_percentile must be <= 100")
        if self.min_kept_peaks < 1:
            raise ValueError("min_kept_peaks must be >= 1")
        if not 1 <= self.out_of_frame_min_keypoints <= 33:
            raise ValueError("out_of_frame_min_keypoints must be in [1, 33]")
        if not 0 <= self.out_of_frame_margin < 0.5:
            raise ValueError("out_of_frame_margin must be in [0, 0.5)")
        if self.min_torso_ratio >= 1:
            raise ValueError("min_torso_ratio must be < 1")
        if self.t_reject > self.t_accept:
            raise ValueError("t_reject must be <= t_accept")

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": {
                "min_prominence_torso_per_s": self.min_prominence_torso_per_s,
                "peak_signal": SIGNAL,
            },
            "grouping": {
                "g_s": self.g_s,
                "max_single_span_s": self.max_single_span_s,
                "method": "chain",
                "min_event_gap_s": self.min_event_gap_s,
            },
            "tracking": {
                "out_of_frame_min_keypoints": self.out_of_frame_min_keypoints,
                "out_of_frame_margin": self.out_of_frame_margin,
                "min_torso_ratio": self.min_torso_ratio,
                "torso_ratio_reference": "median GOOD torso length of the clip",
            },
            "coverage": {"min_duration_s": self.coverage_min_duration_s},
            "decision": {
                "rel_to_clip": "speed / percentile of kept peak speeds of the clip",
                "rel_percentile": self.rel_percentile,
                "min_kept_peaks": self.min_kept_peaks,
                "t_reject": self.t_reject,
                "t_accept": self.t_accept,
            },
            "signals": {
                "smoothing": asdict(self.smoothing),
                "scale": asdict(self.scale),
                "timebase": asdict(self.timebase),
            },
        }


# ----------------------------------------------------------------------------- samples


@dataclass(frozen=True)
class _Samples:
    """Per time-base sample: quality of a region (worst keypoint) and whether a keypoint
    is absent."""

    quality: tuple[KeypointQuality, ...]
    missing: BoolArray


_ORDER = {KeypointQuality.UNRELIABLE: 0, KeypointQuality.LOW: 1, KeypointQuality.GOOD: 2}


def _region_samples(
    poses: PoseSequence, tb: TimeBase, names: Sequence[str], params: SmoothingParams
) -> _Samples:
    quals = [extract_keypoint(poses, tb, n, params).quality for n in names]
    missing = np.zeros(tb.proxy_frames.size, dtype=bool)
    for n in names:
        missing |= ~keypoint_present(poses, tb, n)
    worst = tuple(min(qs, key=lambda q: _ORDER[q]) for qs in zip(*quals, strict=True))
    return _Samples(quality=worst, missing=missing)


def _region(s: _Samples, tb: TimeBase, lo: int, hi: int) -> Region:
    q = s.quality[lo : hi + 1]
    good = sum(x is KeypointQuality.GOOD for x in q)
    low = sum(x is KeypointQuality.LOW for x in q)
    unreliable = len(q) - good - low
    missing = int(s.missing[lo : hi + 1].sum())
    irregular = any(tb.interval_status[k] in _IRREGULAR for k in range(max(lo, 1), hi + 1))
    reasons: list[RegionReason] = []
    if missing:
        reasons.append(RegionReason.NO_POSE)
    if low or unreliable > missing:
        reasons.append(RegionReason.BELOW_THRESHOLD)
    if irregular:
        reasons.append(RegionReason.IRREGULAR_INTERVAL)
    if good + low == 0:
        state = RegionState.NOT_OBSERVABLE
    elif good == len(q) and not irregular:
        state = RegionState.GOOD
    else:
        state = RegionState.LOW_BUT_USABLE
    return Region(
        state=state,
        reasons=reasons,
        samples=SampleCounts(good=good, low_but_usable=low, unreliable=unreliable, missing=missing),
    )


# ----------------------------------------------------------------------------- tracking


@dataclass(frozen=True)
class _Tracking:
    """Per time-base sample: tracking that cannot be trusted (V1 review 1, ADR-029)."""

    out_of_frame: BoolArray  # >= out_of_frame_min_keypoints keypoints outside the image
    jump: BoolArray  # torso < min_torso_ratio x clip median: skeleton on something else
    torso_ref_px: float | None  # clip median of the GOOD torso lengths

    @property
    def bad(self) -> BoolArray:
        return self.out_of_frame | self.jump


def _tracking(
    poses: PoseSequence, tb: TimeBase, scale: TorsoScale, params: DetectorParams
) -> _Tracking:
    """Out of frame: normalised coordinates outside ``[margin, 1 - margin]`` (MediaPipe
    extrapolates keypoints it cannot see). Jump: torso length (mid-shoulders to mid-hips,
    whatever the visibility) below ``min_torso_ratio`` times the median of the GOOD
    measurements of the clip; no reference (no GOOD measurement) -> never a jump."""
    by_index = {f.frame_index: f for f in poses.frames}
    n = tb.proxy_frames.size
    out = np.zeros(n, dtype=bool)
    torso = np.full(n, np.nan)
    lo_m, hi_m = params.out_of_frame_margin, 1.0 - params.out_of_frame_margin
    w, h = poses.image_width, poses.image_height
    for i, fi in enumerate(tb.proxy_frames.tolist()):
        f = by_index[fi]
        if not f.detected:
            continue
        kp = {k.name: k for k in f.keypoints}
        outside = sum(1 for k in f.keypoints if not (lo_m < k.x < hi_m and lo_m < k.y < hi_m))
        out[i] = outside >= params.out_of_frame_min_keypoints
        if all(n_ in kp for n_ in TORSO_KEYPOINTS):
            sx = (kp[_SHOULDERS[0]].x + kp[_SHOULDERS[1]].x) / 2 * w
            sy = (kp[_SHOULDERS[0]].y + kp[_SHOULDERS[1]].y) / 2 * h
            hx = (kp[_HIPS[0]].x + kp[_HIPS[1]].x) / 2 * w
            hy = (kp[_HIPS[0]].y + kp[_HIPS[1]].y) / 2 * h
            torso[i] = math.hypot(sx - hx, sy - hy)
    good = scale.measured_px[np.isfinite(scale.measured_px)]
    ref = float(np.median(good)) if good.size else None
    if ref is None:
        jump = np.zeros(n, dtype=bool)
    else:
        with np.errstate(invalid="ignore"):
            jump = np.isfinite(torso) & (torso < params.min_torso_ratio * ref)
    return _Tracking(out_of_frame=out, jump=jump, torso_ref_px=ref)


def _window(tb: TimeBase, t_s: float, half_s: float) -> tuple[int, int]:
    t = tb.times_s
    lo = int(np.searchsorted(t, t_s - half_s - 1e-9, side="left"))
    hi = int(np.searchsorted(t, t_s + half_s + 1e-9, side="right")) - 1
    return lo, max(lo, hi)


def _mostly(mask: BoolArray, lo: int, hi: int) -> bool:
    return bool(mask[lo : hi + 1].sum() * 2 > hi - lo + 1)


# ----------------------------------------------------------------------------- detector


@dataclass(frozen=True)
class _Peak:
    id: str
    src: CandidatePeak
    kept: bool
    reasons: tuple[str, ...]


def _rel(p: _Peak, clip_ref: float | None) -> float | None:
    v = p.src.speed_torso_per_s
    return None if v is None or clip_ref is None else v / clip_ref


def _wrist_peak(p: _Peak, event_id: str | None, clip_ref: float | None) -> WristPeak:
    c = p.src
    torso = c.speed_torso_per_s
    prom = c.prominence_torso_per_s
    qw = c.quality_window
    return WristPeak(
        id=p.id,
        wrist=Wrist(c.wrist),
        proxy_frame=c.sample.proxy_frame,
        t_s=_r(c.sample.t_s),
        speed=Speed(
            value=torso if torso is not None else c.speed_px_per_s,
            unit="torso_lengths_per_s" if torso is not None else "px_per_s",
            rel_to_clip=_rel(p, clip_ref),
        ),
        provenance=Provenance(
            signal=SIGNAL,
            keypoint=c.keypoint,
            landmark_index=c.landmark_index,
            velocity_state=c.velocity_state,
            smooth_state=_TO_SMOOTH[c.velocity_state],
            scale_state=c.scale_state,
        ),
        quality_window=QualityWindow(
            half_window_s=c.half_window_s,
            good=qw.good,
            low_but_usable=qw.low_but_usable,
            unreliable=qw.unreliable,
            missing=qw.missing,
            irregular_intervals=qw.irregular_intervals,
            non_finite=qw.non_finite,
            low_visibility=qw.low_visibility,
        ),
        status=PeakStatus.GROUPED if event_id is not None else PeakStatus.DISCARDED,
        reasons=list(p.reasons),
        event_id=event_id,
        prominence=PeakProminence(
            value=prom if prom is not None else c.prominence_px_per_s,
            unit="torso_lengths_per_s" if prom is not None else "px_per_s",
            category=PeakCategoryValue(c.category.value),
            lower_bound=c.prominence_is_lower_bound,
        ),
    )


def _chain(kept: list[_Peak], g_s: float) -> list[list[_Peak]]:
    chains: list[list[_Peak]] = []
    for p in kept:
        if chains and _t(p) - _t(chains[-1][-1]) <= g_s + TIME_TOLERANCE_S:
            chains[-1].append(p)
        else:
            chains.append([p])
    return chains


def _representative(chain: list[_Peak]) -> _Peak | None:
    internal = [p for p in chain if p.src.category is PeakCategory.INTERNAL]
    if not internal:
        return None
    return max(
        internal,
        key=lambda p: (
            p.src.prominence_torso_per_s or 0.0,
            p.src.speed_px_per_s,
            -p.src.sample.t_s,
            p.src.wrist == "left",
        ),
    )


def _good_share(p: _Peak) -> float:
    qw = p.src.quality_window
    total = qw.good + qw.low_but_usable + qw.unreliable
    return qw.good / total if total else 0.0


def _step(n: int, rule: str, subject: str, outcome: TraceOutcome, **kw: Any) -> TraceStep:
    param = kw.pop("param", None)
    return TraceStep(step=n, rule=rule, subject=subject, inputs=kw, param=param, outcome=outcome)


def _event(
    eid: str,
    chain: list[_Peak],
    params: DetectorParams,
    tb: TimeBase,
    regions_s: dict[RegionName, _Samples],
    half_window_s: float,
    clip_ref: float | None,
    duplicate_of: str | None = None,
) -> CandidateEvent:
    t = tb.times_s
    first, last = chain[0].src, chain[-1].src
    span = _r(_t(chain[-1]) - _t(chain[0]))
    lo = int(np.searchsorted(t, first.sample.t_s - half_window_s - 1e-9, side="left"))
    hi = int(np.searchsorted(t, last.sample.t_s + half_window_s + 1e-9, side="right")) - 1
    regions = {name: _region(s, tb, lo, hi) for name, s in regions_s.items()}
    sides = {p.src.wrist for p in chain}
    used = [RegionName.TORSO] + [
        RegionName.LEFT_WRIST if w == "left" else RegionName.RIGHT_WRIST
        for w in ("left", "right")
        if w in sides
    ]
    rank = {RegionState.NOT_OBSERVABLE: 0, RegionState.LOW_BUT_USABLE: 1, RegionState.GOOD: 2}
    worst = min(rank[regions[r].state] for r in used)
    state = next(s for s, v in rank.items() if v == worst)
    limited = [r for r in used if rank[regions[r].state] == worst] if worst < 2 else []

    rep = _representative(chain)
    merge = span > params.max_single_span_s + TIME_TOLERANCE_S
    trace: list[TraceStep] = [
        _step(1, R_GROUPING, eid, TraceOutcome.PASS, param="grouping.g_s",
              peaks=len(chain), span_s=round(span, 4)),
    ]  # fmt: skip
    reasons: list[str] = []
    rep_reasons: list[str] = []
    if rep is None:
        rep_value = RepresentativeWristValue.UNDETERMINED
        rep_reasons.append(R_REP_ONLY_EDGE)
        trace.append(_step(2, R_REP_ONLY_EDGE, eid, TraceOutcome.FAIL, internal_peaks=0))
    else:
        rep_value = RepresentativeWristValue(rep.src.wrist)
        trace.append(
            _step(2, R_REP, rep.id, TraceOutcome.PASS, wrist=rep.src.wrist,
                  prominence=round(rep.src.prominence_torso_per_s or 0.0, 4))
        )  # fmt: skip

    def check(rule: str, failed: bool | None, subject: str, **kw: Any) -> None:
        if failed is None:
            outcome = TraceOutcome.NOT_APPLICABLE
        else:
            outcome = TraceOutcome.FAIL if failed else TraceOutcome.PASS
            if failed:
                reasons.append(rule)
        trace.append(_step(len(trace) + 1, rule, subject, outcome, **kw))

    height = rep if rep is not None else max(chain, key=lambda p: p.src.speed_px_per_s)
    rel = _rel(height, clip_ref)
    if rel is None:
        check(R_NO_CLIP_REF, True, height.id, param="decision.min_kept_peaks")
        check(R_HEIGHT_REJECT, None, height.id)
        check(R_HEIGHT_UNCERTAIN, None, height.id)
    else:
        check(R_NO_CLIP_REF, False, height.id, param="decision.min_kept_peaks")
        check(R_HEIGHT_REJECT, rel < params.t_reject, height.id, rel_to_clip=round(rel, 4),
              param="decision.t_reject")  # fmt: skip
        check(R_HEIGHT_UNCERTAIN, rel < params.t_accept, height.id, rel_to_clip=round(rel, 4),
              param="decision.t_accept")  # fmt: skip
    check(R_UNDETERMINED, rep is None, eid)
    check(R_MERGE, merge, eid, span_s=span, param="grouping.max_single_span_s")
    best_good = max(_good_share(p) for p in chain)
    check(R_LOW, not best_good > 0.5, eid, best_good_share=round(best_good, 4))
    if rep is None:
        check(R_IRREGULAR, None, eid)
    else:
        qw = rep.src.quality_window
        check(R_IRREGULAR, qw.irregular_intervals > 0, rep.id,
              irregular_intervals=qw.irregular_intervals)  # fmt: skip
    check(R_NOT_OBSERVABLE, state is RegionState.NOT_OBSERVABLE, eid, reliability=state.value)
    if R_HEIGHT_REJECT in reasons:
        check(R_DUPLICATE, None, eid)
    else:
        check(R_DUPLICATE, duplicate_of is not None, eid, stronger_event=duplicate_of,
              param="grouping.min_event_gap_s")  # fmt: skip
    if R_HEIGHT_REJECT in reasons or R_DUPLICATE in reasons:
        status = EventStatus.REJECTED
    else:
        status = EventStatus.UNCERTAIN if reasons else EventStatus.ACCEPTED
    if tb.basis is TimeBasis.PROXY:
        reasons.append(R_PROXY)
    trace.append(
        _step(len(trace) + 1, DECISION_RULE, eid, TraceOutcome(status.value),
              reasons=[r for r in reasons if r != R_PROXY])
    )  # fmt: skip

    by_wrist = {w: [p.id for p in chain if p.src.wrist == w] for w in ("left", "right")}
    return CandidateEvent(
        id=eid,
        status=status,
        reasons=reasons,
        grouping=Grouping(
            method="chain",
            g_s=params.g_s,
            peak_ids=[p.id for p in chain],
            links=[
                GroupLink(**{"from": a.id, "to": b.id, "dt_s": _r(_t(b) - _t(a))})
                for a, b in pairwise(chain)
            ],
            span_s=span,
            possible_merge=merge,
        ),
        evidence=Evidence(
            left=WristEvidence(
                state=EvidenceState.PEAKS if by_wrist["left"] else EvidenceState.NO_PEAK,
                peak_ids=by_wrist["left"],
            ),
            right=WristEvidence(
                state=EvidenceState.PEAKS if by_wrist["right"] else EvidenceState.NO_PEAK,
                peak_ids=by_wrist["right"],
            ),
        ),
        representative_wrist=RepresentativeWrist(value=rep_value, reasons=rep_reasons),
        time=EventTime(
            ref_proxy_frame=None if rep is None else rep.src.sample.proxy_frame,
            ref_t_s=None if rep is None else _t(rep),
            window_proxy_frames=(
                min(p.src.sample.proxy_frame for p in chain),
                max(p.src.sample.proxy_frame for p in chain),
            ),
            window_t_s=(_t(chain[0]), _t(chain[-1])),
        ),
        regions=Regions(
            torso=regions[RegionName.TORSO],
            left_wrist=regions[RegionName.LEFT_WRIST],
            right_wrist=regions[RegionName.RIGHT_WRIST],
            legs=regions[RegionName.LEGS],
        ),
        reliability=EventReliability(state=state, regions_used=used, limited_by=limited),
        trace=trace,
    )


def _event_time(e: CandidateEvent) -> float:
    """Time shown in the app: the reference time, else the window start."""
    return e.time.ref_t_s if e.time.ref_t_s is not None else e.time.window_t_s[0]


def _strength(
    e: CandidateEvent, peaks: dict[str, _Peak], clip_ref: float | None
) -> tuple[bool, bool, float, float]:
    """Accepted before uncertain, determined wrist before undetermined, then the highest
    relative speed of the height peak, then earlier."""
    own = [peaks[pid] for pid in e.grouping.peak_ids]
    rep = next(
        (p for p in own if p.src.sample.proxy_frame == e.time.ref_proxy_frame
         and p.src.wrist == e.representative_wrist.value.value),
        None,
    )  # fmt: skip
    height = rep if rep is not None else max(own, key=lambda p: p.src.speed_px_per_s)
    rel = _rel(height, clip_ref)
    return (
        e.status is EventStatus.ACCEPTED,
        e.time.ref_t_s is not None,
        rel if rel is not None else -1.0,
        -_event_time(e),
    )


def _duplicates(
    events: list[CandidateEvent],
    peaks: dict[str, _Peak],
    clip_ref: float | None,
    min_gap_s: float,
) -> dict[str, str]:
    """Event id -> id of the stronger kept event less than ``min_gap_s`` away (greedy, by
    strength). Rejected events neither win nor lose."""
    live = [e for e in events if e.status is not EventStatus.REJECTED]
    order = sorted(live, key=lambda e: _strength(e, peaks, clip_ref), reverse=True)
    kept: list[CandidateEvent] = []
    out: dict[str, str] = {}
    for e in order:
        t = _event_time(e)
        near = [k for k in kept if abs(_event_time(k) - t) < min_gap_s - TIME_TOLERANCE_S]
        if near:
            out[e.id] = min(near, key=lambda k: abs(_event_time(k) - t)).id
        else:
            kept.append(e)
    return out


def _coverage(
    tb: TimeBase,
    vels: dict[Side, KeypointVelocity],
    present: dict[Side, BoolArray],
    scale: TorsoScale,
    torso: _Samples,
    min_duration_s: float,
    untracked: BoolArray | None = None,
) -> Coverage:
    n = tb.times_s.size
    no_wrist = np.ones(n, dtype=bool)
    for v in vels.values():
        no_wrist &= ~np.isfinite(v.speed_2d_px_per_s)
    no_scale = np.array([s is not ScaleState.OK for s in scale.state], dtype=bool)
    if untracked is None:
        untracked = np.zeros(n, dtype=bool)
    bad = no_wrist | no_scale | untracked
    stretches: list[CoverageStretch] = []
    i = 0
    while i < n:
        if not bad[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and bad[j + 1]:
            j += 1
        duration = float(tb.times_s[j] - tb.times_s[i]) + tb.nominal_interval_s
        if duration >= min_duration_s - 1e-9:
            regions: list[RegionName] = []
            reasons: set[RegionReason] = set()
            if no_wrist[i : j + 1].any():
                regions += [RegionName.LEFT_WRIST, RegionName.RIGHT_WRIST]
                for side, v in vels.items():
                    for k in range(i, j + 1):
                        if not no_wrist[k]:
                            continue
                        if not present[side][k]:
                            reasons.add(RegionReason.NO_POSE)
                        elif v.quality[k] is KeypointQuality.UNRELIABLE:
                            reasons.add(RegionReason.BELOW_THRESHOLD)
                        else:
                            reasons.add(RegionReason.BROKEN_RUN)  # usable, run too short
            if no_scale[i : j + 1].any():
                regions.insert(0, RegionName.TORSO)
                for k in range(i, j + 1):
                    if no_scale[k]:
                        miss = bool(torso.missing[k])
                        reasons.add(RegionReason.NO_POSE if miss else RegionReason.BELOW_THRESHOLD)
            if untracked[i : j + 1].any():
                # skeleton out of frame or on something else: no region can be trusted
                regions = [RegionName.TORSO, RegionName.LEFT_WRIST, RegionName.RIGHT_WRIST]
                reasons.add(RegionReason.OUT_OF_FRAME)
            stretches.append(
                CoverageStretch(
                    start_proxy_frame=int(tb.proxy_frames[i]),
                    end_proxy_frame=int(tb.proxy_frames[j]),
                    start_t_s=_r(tb.times_s[i]),
                    end_t_s=_r(tb.times_s[j]),
                    regions=regions,
                    reasons=[r for r in RegionReason if r in reasons],
                )
            )
        i = j + 1
    return Coverage(min_duration_s=min_duration_s, not_assessable=stretches)


def truncate_inputs(
    poses: PoseSequence,
    timemap: TimeMap | None,
    last_proxy_frame: int | None,
    first_proxy_frame: int | None = None,
) -> tuple[PoseSequence, TimeMap | None]:
    """Inputs as if the video ended at ``last_proxy_frame`` and/or started at
    ``first_proxy_frame`` (both inclusive; ``None`` = no cut on that side).

    Start: pose frames before ``first_proxy_frame`` are dropped; the time map stays whole
    (it is indexed by proxy frame, so every kept frame keeps its time and the map stays
    consistent), and the time base, signals and peaks start at the first kept frame.

    Pose frames after it are dropped. A consistent time map is cut to the same proxy
    frames and to the source frames they show (``proxy_to_source`` prefix, source times up
    to the last source frame used, counts updated), so it stays consistent with the cut
    poses and gives every kept frame the time it has in the whole file. Summary statistics
    of the whole file (duplicated / dropped frames, errors) are not used by the time base
    and are left as they are. An inconsistent map is passed unchanged: it stays unusable
    and the time basis stays proxy, as on the whole file."""
    for name, v in (
        ("last_proxy_frame", last_proxy_frame),
        ("first_proxy_frame", first_proxy_frame),
    ):
        if v is not None and v < 0:
            raise ValueError(f"{name} must be >= 0")
    lo = 0 if first_proxy_frame is None else first_proxy_frame
    hi = math.inf if last_proxy_frame is None else last_proxy_frame
    frames = [f for f in poses.frames if lo <= f.frame_index <= hi]
    if not frames:
        raise ValueError(f"no pose frame between frames {first_proxy_frame} and {last_proxy_frame}")
    cut = poses.model_copy(update={"frames": frames})
    if last_proxy_frame is None or timemap is None or check_timemap(poses, timemap):
        return cut, timemap
    n_proxy = min(frames[-1].frame_index + poses.frame_stride, len(timemap.proxy_to_source))
    mapping = timemap.proxy_to_source[:n_proxy]
    n_source = max(mapping) + 1
    summary = timemap.summary.model_copy(
        update={"proxy_frame_count": n_proxy, "source_frame_count": n_source}
    )
    cut_map = timemap.model_copy(
        update={
            "summary": summary,
            "proxy_to_source": mapping,
            "source_times_s": timemap.source_times_s[:n_source],
        }
    )
    return cut, cut_map


def detect(
    poses: PoseSequence,
    *,
    pose_file: str,
    pose_sha256: str,
    timemap: TimeMap | None = None,
    timemap_file: str | None = None,
    timemap_sha256: str | None = None,
    params: DetectorParams | None = None,
    now: datetime | None = None,
    last_proxy_frame: int | None = None,
    first_proxy_frame: int | None = None,
) -> StrokesFile:
    """Run the detector on one pose sequence; the result passes every invariant.

    ``last_proxy_frame`` (data splits, e.g. Development B = img5098_full 0-4499): the
    inputs are cut at that frame (inclusive) *before* any computation
    (:func:`truncate_inputs`), so time base, smoothing, velocity, torso scale, peaks,
    prominences, quality windows, regions and coverage never see a later frame. Frame
    numbers and times stay those of the original file; the input hashes and
    ``inputs.pose.frames`` describe the whole files; the limit is recorded in
    ``params.input``. Without a limit nothing changes. ``first_proxy_frame`` cuts the
    start the same way (Control = img5098_full 4500-9470): no earlier frame is used."""
    params = params or DetectorParams()
    all_frames = len(poses.frames)
    if last_proxy_frame is not None or first_proxy_frame is not None:
        poses, timemap = truncate_inputs(poses, timemap, last_proxy_frame, first_proxy_frame)
    tb = build_timebase(poses, timemap, params.timebase)
    scale = torso_scale(poses, tb, params.scale, params.smoothing)
    vels: dict[Side, KeypointVelocity] = {}
    present: dict[Side, BoolArray] = {}
    cands: list[CandidatePeak] = []
    half_window_s = 0.0
    for side in ("left", "right"):
        name = WRIST_KEYPOINT[side]
        vels[side] = keypoint_velocity(poses, tb, name, scale, params.smoothing)
        present[side] = keypoint_present(poses, tb, name)
        wp = wrist_peaks(vels[side], side, present[side])
        cands += wp.peaks
        half_window_s = wp.half_window_s
    cands.sort(key=lambda c: (c.sample.t_s, c.wrist))
    trk = _tracking(poses, tb, scale, params)
    # Untrusted stretches; the window of every peak discarded for tracking is added so that
    # the stretch it was in is always shown as not assessable (at least min_duration_s).
    untracked = trk.bad.copy()
    mark_half_s = max(half_window_s, params.coverage_min_duration_s / 2)
    peaks: list[_Peak] = []
    for k, c in enumerate(cands, start=1):
        prom = c.prominence_torso_per_s
        lo, hi = _window(tb, c.sample.t_s, c.half_window_s)
        if prom is None:
            reasons: tuple[str, ...] = (R_NO_SCALE,)
        elif prom < params.min_prominence_torso_per_s:
            reasons = (R_BELOW_MIN,)
        elif _mostly(trk.jump, lo, hi):
            reasons = (R_TRACKING_JUMP,)
        elif _mostly(trk.out_of_frame, lo, hi):
            reasons = (R_OUT_OF_FRAME,)
        else:
            reasons = ()
        if reasons and reasons[0] in (R_TRACKING_JUMP, R_OUT_OF_FRAME):
            mlo, mhi = _window(tb, c.sample.t_s, mark_half_s)
            untracked[mlo : mhi + 1] = True
        peaks.append(_Peak(id=f"p{k:05d}", src=c, kept=not reasons, reasons=reasons))

    regions_s = {
        RegionName.TORSO: _region_samples(poses, tb, TORSO_KEYPOINTS, params.smoothing),
        RegionName.LEFT_WRIST: _region_samples(poses, tb, ("left_wrist",), params.smoothing),
        RegionName.RIGHT_WRIST: _region_samples(poses, tb, ("right_wrist",), params.smoothing),
        RegionName.LEGS: _region_samples(poses, tb, LEG_KEYPOINTS, params.smoothing),
    }
    events: list[CandidateEvent] = []
    owner: dict[str, str] = {}
    if half_window_s <= 0:  # no peak at all: use the M2.2 fit half-window directly
        w = params.smoothing.window_samples(tb.nominal_interval_s)
        half_window_s = (w // 2) * tb.nominal_interval_s
    # Reference height as calibrated in ADR-027: the peaks that pass the prominence rule,
    # tracking discards included, so the height thresholds keep their meaning (ADR-029).
    ref_peaks = [p for p in peaks if p.kept or p.reasons[0] in (R_TRACKING_JUMP, R_OUT_OF_FRAME)]
    kept_speeds = [
        p.src.speed_torso_per_s for p in ref_peaks if p.src.speed_torso_per_s is not None
    ]
    clip_ref = (
        float(np.percentile(kept_speeds, params.rel_percentile))
        if len(kept_speeds) >= params.min_kept_peaks
        else None
    )
    chains = _chain([p for p in peaks if p.kept], params.g_s)
    for k, chain in enumerate(chains, start=1):
        eid = f"e{k:04d}"
        events.append(_event(eid, chain, params, tb, regions_s, half_window_s, clip_ref))
        owner.update({p.id: eid for p in chain})
    by_id = {p.id: p for p in peaks}
    dupes = _duplicates(events, by_id, clip_ref, params.min_event_gap_s)
    events = [
        _event(e.id, chain, params, tb, regions_s, half_window_s, clip_ref, dupes[e.id])
        if e.id in dupes
        else e
        for e, chain in zip(events, chains, strict=True)
    ]

    if tb.basis is TimeBasis.SOURCE:
        tm_input = TimemapInput(file=timemap_file, sha256=timemap_sha256, status=tb.map_status)
    elif tb.map_status is MapStatus.MISSING:
        tm_input = TimemapInput(status=MapStatus.MISSING)
    else:
        tm_input = TimemapInput(file=timemap_file, sha256=timemap_sha256, status=tb.map_status)
    p_dict = params.as_dict()
    p_dict["input"] = {"last_proxy_frame": last_proxy_frame}
    if first_proxy_frame is not None:  # key only when used: earlier files stay identical
        p_dict["input"]["first_proxy_frame"] = first_proxy_frame
    return StrokesFile(
        schema_=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        created_at=now or datetime.now(UTC),
        producer=Producer(
            algorithm=ALGORITHM,
            algorithm_version=ALGORITHM_VERSION,
            status=ProducerStatus.EXPERIMENTAL,
            engine_version=__version__,
        ),
        inputs=Inputs(
            pose=PoseInput(
                file=pose_file,
                sha256=pose_sha256,
                fps=poses.fps,
                frames=all_frames,
                frame_stride=poses.frame_stride,
            ),
            timemap=tm_input,
        ),
        time_basis=tb.basis,
        params=p_dict,
        params_sha256=params_sha256(p_dict),
        rules=list(RULES),
        wrist_peaks=[_wrist_peak(p, owner.get(p.id), clip_ref) for p in peaks],
        events=events,
        coverage=_coverage(
            tb,
            vels,
            present,
            scale,
            regions_s[RegionName.TORSO],
            params.coverage_min_duration_s,
            untracked,
        ),
    )


POSE_FILE = "pose.json"
TIMEMAP_FILE = "timemap.json"


def detect_dir(
    pose_dir: str | Path,
    params: DetectorParams | None = None,
    now: datetime | None = None,
    last_proxy_frame: int | None = None,
    first_proxy_frame: int | None = None,
) -> StrokesFile:
    """Detector on ``pose_dir/pose.json`` (+ ``timemap.json`` when present)."""
    d = Path(pose_dir)
    pose_path = d / POSE_FILE
    poses = PoseSequence.model_validate_json(pose_path.read_bytes())
    tm_path = d / TIMEMAP_FILE
    timemap = TimeMap.model_validate_json(tm_path.read_bytes()) if tm_path.is_file() else None
    return detect(
        poses,
        pose_file=POSE_FILE,
        pose_sha256=file_sha256(pose_path),
        timemap=timemap,
        timemap_file=TIMEMAP_FILE if timemap is not None else None,
        timemap_sha256=file_sha256(tm_path) if timemap is not None else None,
        params=params,
        now=now,
        last_proxy_frame=last_proxy_frame,
        first_proxy_frame=first_proxy_frame,
    )
