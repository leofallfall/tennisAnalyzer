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
   (``prominence.category``).
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
   or a region used is not observable; otherwise ``accepted``. Nothing is ``rejected`` in
   I2 (thresholds come with I3).
6. **Coverage**: stretches of at least ``coverage_min_duration_s`` where no stroke could
   have been found: neither wrist has a speed value, or the torso scale is missing.
   Insufficient evidence, not absence of strokes.
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
ALGORITHM_VERSION = "0.1.0-i2"
TORSO_KEYPOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
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

RULES: tuple[Rule, ...] = (
    Rule(code=R_NO_SCALE, kind=RuleKind.DISCARD_PEAK,
         description="No torso scale at the peak: the prominence cannot be compared with "
                     "the minimum (torso lengths/s)"),
    Rule(code=R_BELOW_MIN, kind=RuleKind.DISCARD_PEAK,
         description="Prominence below detector.min_prominence_torso_per_s (initial "
                     "provisional value, not validated); internal and run-edge alike"),
    Rule(code=R_GROUPING, kind=RuleKind.INFO,
         description="Kept peaks of both wrists chained in time order while consecutive "
                     "peaks are at most G apart"),
    Rule(code=R_REP, kind=RuleKind.REPRESENTATIVE_WRIST,
         description="Wrist of the most prominent internal peak (ties: speed, earlier, left)"),
    Rule(code=R_REP_ONLY_EDGE, kind=RuleKind.REPRESENTATIVE_WRIST,
         description="Only run-edge peaks (one-sided prominence): wrist not determined, the "
                     "event is a window"),
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
    Rule(code=R_PROXY, kind=RuleKind.INFO,
         description="No usable time map: times are proxy times, not verified"),
    Rule(code=DECISION_RULE, kind=RuleKind.DECISION,
         description="accepted unless an uncertain rule failed (I2: no rejection rule yet)"),
)  # fmt: skip


@dataclass(frozen=True)
class DetectorParams:
    """Every value is a starting value chosen on Development A/B, not validated."""

    min_prominence_torso_per_s: float = 2.0
    g_s: float = 0.3
    max_single_span_s: float = 1.0
    coverage_min_duration_s: float = 0.3
    smoothing: SmoothingParams = field(default_factory=SmoothingParams)
    scale: ScaleParams = field(default_factory=ScaleParams)
    timebase: TimeBaseParams = field(default_factory=TimeBaseParams)

    def __post_init__(self) -> None:
        for name in (
            "min_prominence_torso_per_s",
            "g_s",
            "max_single_span_s",
            "coverage_min_duration_s",
        ):
            v = getattr(self, name)
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be finite and > 0")

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
            },
            "coverage": {"min_duration_s": self.coverage_min_duration_s},
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


# ----------------------------------------------------------------------------- detector


@dataclass(frozen=True)
class _Peak:
    id: str
    src: CandidatePeak
    kept: bool
    reasons: tuple[str, ...]


def _wrist_peak(p: _Peak, event_id: str | None) -> WristPeak:
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


def _coverage(
    tb: TimeBase,
    vels: dict[Side, KeypointVelocity],
    present: dict[Side, BoolArray],
    scale: TorsoScale,
    torso: _Samples,
    min_duration_s: float,
) -> Coverage:
    n = tb.times_s.size
    no_wrist = np.ones(n, dtype=bool)
    for v in vels.values():
        no_wrist &= ~np.isfinite(v.speed_2d_px_per_s)
    no_scale = np.array([s is not ScaleState.OK for s in scale.state], dtype=bool)
    bad = no_wrist | no_scale
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
    poses: PoseSequence, timemap: TimeMap | None, last_proxy_frame: int
) -> tuple[PoseSequence, TimeMap | None]:
    """Inputs as if the video ended at ``last_proxy_frame`` (inclusive).

    Pose frames after it are dropped. A consistent time map is cut to the same proxy
    frames and to the source frames they show (``proxy_to_source`` prefix, source times up
    to the last source frame used, counts updated), so it stays consistent with the cut
    poses and gives every kept frame the time it has in the whole file. Summary statistics
    of the whole file (duplicated / dropped frames, errors) are not used by the time base
    and are left as they are. An inconsistent map is passed unchanged: it stays unusable
    and the time basis stays proxy, as on the whole file."""
    if last_proxy_frame < 0:
        raise ValueError("last_proxy_frame must be >= 0")
    frames = [f for f in poses.frames if f.frame_index <= last_proxy_frame]
    if not frames:
        raise ValueError(f"no pose frame at or before frame {last_proxy_frame}")
    cut = poses.model_copy(update={"frames": frames})
    if timemap is None or check_timemap(poses, timemap):
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
) -> StrokesFile:
    """Run the detector on one pose sequence; the result passes every invariant.

    ``last_proxy_frame`` (data splits, e.g. Development B = img5098_full 0-4499): the
    inputs are cut at that frame (inclusive) *before* any computation
    (:func:`truncate_inputs`), so time base, smoothing, velocity, torso scale, peaks,
    prominences, quality windows, regions and coverage never see a later frame. Frame
    numbers and times stay those of the original file; the input hashes and
    ``inputs.pose.frames`` describe the whole files; the limit is recorded in
    ``params.input``. Without a limit nothing changes."""
    params = params or DetectorParams()
    all_frames = len(poses.frames)
    if last_proxy_frame is not None:
        poses, timemap = truncate_inputs(poses, timemap, last_proxy_frame)
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
    peaks: list[_Peak] = []
    for k, c in enumerate(cands, start=1):
        prom = c.prominence_torso_per_s
        if prom is None:
            reasons: tuple[str, ...] = (R_NO_SCALE,)
        elif prom < params.min_prominence_torso_per_s:
            reasons = (R_BELOW_MIN,)
        else:
            reasons = ()
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
    for k, chain in enumerate(_chain([p for p in peaks if p.kept], params.g_s), start=1):
        eid = f"e{k:04d}"
        events.append(_event(eid, chain, params, tb, regions_s, half_window_s))
        owner.update({p.id: eid for p in chain})

    if tb.basis is TimeBasis.SOURCE:
        tm_input = TimemapInput(file=timemap_file, sha256=timemap_sha256, status=tb.map_status)
    elif tb.map_status is MapStatus.MISSING:
        tm_input = TimemapInput(status=MapStatus.MISSING)
    else:
        tm_input = TimemapInput(file=timemap_file, sha256=timemap_sha256, status=tb.map_status)
    p_dict = params.as_dict()
    p_dict["input"] = {"last_proxy_frame": last_proxy_frame}
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
        wrist_peaks=[_wrist_peak(p, owner.get(p.id)) for p in peaks],
        events=events,
        coverage=_coverage(
            tb,
            vels,
            present,
            scale,
            regions_s[RegionName.TORSO],
            params.coverage_min_duration_s,
        ),
    )


POSE_FILE = "pose.json"
TIMEMAP_FILE = "timemap.json"


def detect_dir(
    pose_dir: str | Path,
    params: DetectorParams | None = None,
    now: datetime | None = None,
    last_proxy_frame: int | None = None,
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
    )
