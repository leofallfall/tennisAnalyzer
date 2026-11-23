"""Wrist speed peaks (M2.3, intervention I1).

Every local maximum of the apparent 2D speed of **both** wrists, found separately inside
each continuous run of the M2.2 signals, with its prominence and the quality of the
keypoint around it. Nothing is filtered, grouped, classified or written to
``strokes.json``: the minimum prominence, grouping and decisions are I2/I3 and are chosen
by the developer on Development data. Definitions in docs/M2_3_PEAKS.md.

Signal
------
Maxima and bases are searched on ``speed_2d_px_per_s`` (M2.2 velocity: derivative of the
local quadratic fit, 0.2 s). Values in torso lengths per second are that pixel value
divided by the stabilised torso length **at the peak sample**, so they coincide with
``speed_2d_torso_per_s`` there. Searching on pixels keeps the step changes of the 1 s
median torso length from creating or moving maxima; a peak without a torso scale is kept,
with ``None`` torso values (M2.2: a missing value is a valid result).

Runs
----
A run is the M2.2 run: consecutive usable (GOOD or LOW) samples of one time-base segment.
No search crosses a run boundary (a hole, an unreliable sample, a long / short / invalid
interval). Runs shorter than the fit window have no velocity and give no peak; they are
counted.

Local maximum (plateau rule)
----------------------------
Inside a run, a *plateau* is a maximal stretch of consecutive samples with exactly equal
speed (usually one sample). A plateau is a local maximum when each neighbour that exists
inside the run is strictly lower. The peak sample is the middle of the plateau; with an
even number of samples, the left one of the two middle samples.

* Run edge: a plateau touching the first (or last) sample of its run has no neighbour on
  that side; it is a maximum when the neighbour on the other side is strictly lower, and
  is marked ``run_edge``. A plateau covering the whole run is not a maximum (no variation).
* Close peaks: never merged, never separated by a minimum distance. Each maximum is
  reported; the dip between two close maxima is what lowers the prominence of the smaller
  one.

Prominence (topographic, bounded by the run)
--------------------------------------------
For a peak of height ``h`` (plateau ``a..b``), on each side walk away from the plateau
until the first sample **strictly higher** than ``h`` or the run edge. The side's base is
the lowest sample walked (ties: the one closest to the peak). Prominence =
``h - max(base heights)`` over the sides that exist.

* ``censored`` side: the walk reached the run edge without finding a higher sample. The
  run may hide a lower dip beyond its edge, so the prominence is a **lower bound** of the
  prominence the uncut signal would have.
* ``run_edge`` peak: one side has no samples and is ignored; the prominence is one-sided
  (``ProminenceSides.ONE_SIDED``) and not comparable with a two-sided one.

Quality counts (two windows, both from M2.2)
--------------------------------------------
* ``fit_window``: the exact samples of the M2.2 fit that produced the peak's speed
  (``window_samples`` samples, start ``min(max(i - half, run start), run end - W)``).
  Inside a run by construction, so only GOOD and LOW samples.
* ``quality_window``: samples with ``|t - t_peak| <= half_window_s``, where
  ``half_window_s = (window_samples // 2) * nominal_interval_s`` is the M2.2 fit
  half-window (0.1 s at 30 fps). It may cross the run edge and counts GOOD, LOW,
  UNRELIABLE (keypoint present, visibility below the usable threshold or missing),
  MISSING (no pose or no keypoint) and irregular intervals (long / short / invalid)
  overlapping the window. It matches the ``quality_window`` of ``strokes.json``.
* ``base_span``: GOOD and LOW samples between the two bases (the data the prominence used).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from tennis_cv.pose.landmarks import LANDMARK_INDEX
from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.body import Side
from tennis_cv.signals.scale import ScaleState, TorsoScale
from tennis_cv.signals.smoothing import KeypointQuality, SmoothingParams
from tennis_cv.signals.timebase import IntervalStatus, TimeBase
from tennis_cv.signals.velocity import KeypointVelocity, VelocityState, keypoint_velocity

FloatArray = npt.NDArray[np.float64]

# Candidate minimum prominence chosen by the developer on Development A/B (2026-11-23,
# docs/M2_3_PEAKS.md 6). Frozen for the single Control run; NOT applied in I1 (every peak
# is returned) and not a validated threshold. Run-edge (one-sided) and internal
# (two-sided) peaks are compared with it separately, never pooled.
CANDIDATE_MIN_PROMINENCE_TORSO_PER_S = 2.0

WRIST_KEYPOINT: dict[Side, str] = {"left": "left_wrist", "right": "right_wrist"}
SIGNAL = "speed_2d_px_per_s"
_EPS_S = 1e-9


class RunEdge(StrEnum):
    NONE = "none"
    START = "run_start"  # plateau touches the first sample of its run
    END = "run_end"  # plateau touches the last sample of its run


class PeakCategory(StrEnum):
    """Kept apart in every count (developer's decision, 2026-11-23): their prominences are
    not equivalent."""

    INTERNAL = "internal"  # two-sided prominence
    RUN_EDGE = "run_edge"  # plateau at a run edge: one-sided, censored prominence


class ProminenceSides(StrEnum):
    TWO_SIDED = "two_sided"
    ONE_SIDED = "one_sided"  # run-edge peak: one side has no samples


@dataclass(frozen=True)
class Plateau:
    """Local maximum inside one array (indices relative to it)."""

    first: int
    last: int
    peak: int  # middle sample; left of the two middles when even
    edge: RunEdge


@dataclass(frozen=True)
class Prominence:
    """Indices relative to the array; ``None`` for a side without samples."""

    value: float
    sides: ProminenceSides
    left_base: int | None
    right_base: int | None
    left_censored: bool  # walk reached the array start without a strictly higher sample
    right_censored: bool


@dataclass(frozen=True)
class QualityCounts:
    good: int
    low_but_usable: int
    unreliable: int = 0
    missing: int = 0
    irregular_intervals: int = 0


@dataclass(frozen=True)
class SampleRef:
    """A time-base sample: index, proxy frame, time."""

    index: int
    proxy_frame: int
    t_s: float


@dataclass(frozen=True)
class CandidatePeak:
    """One local maximum of one wrist's speed. Not a stroke, not a decision."""

    wrist: Side
    keypoint: str
    landmark_index: int
    sample: SampleRef
    plateau_first: SampleRef
    plateau_last: SampleRef
    run_id: int
    run_first: SampleRef
    run_last: SampleRef
    run_edge: RunEdge
    speed_px_per_s: float
    speed_torso_per_s: float | None  # None without a torso scale at the peak
    velocity_state: VelocityState  # VALID (centred fit) or EDGE (shifted fit)
    scale_state: ScaleState | None  # None when no scale was given
    torso_length_px: float | None
    prominence_px_per_s: float
    prominence_torso_per_s: float | None  # prominence_px / torso length at the peak
    prominence_sides: ProminenceSides
    left_base: SampleRef | None
    right_base: SampleRef | None
    left_censored: bool
    right_censored: bool
    fit_window: tuple[SampleRef, SampleRef]
    fit_counts: QualityCounts
    half_window_s: float
    quality_window: QualityCounts
    base_span_counts: QualityCounts

    @property
    def category(self) -> PeakCategory:
        return PeakCategory.INTERNAL if self.run_edge is RunEdge.NONE else PeakCategory.RUN_EDGE

    @property
    def prominence_is_lower_bound(self) -> bool:
        return self.prominence_sides is ProminenceSides.TWO_SIDED and (
            self.left_censored or self.right_censored
        )


@dataclass(frozen=True)
class WristPeaks:
    """All peaks of one wrist on one clip, in time order, plus what could not be searched."""

    wrist: Side
    keypoint: str
    peaks: tuple[CandidatePeak, ...]
    runs: int
    runs_too_short: int  # shorter than the fit window: no velocity, no peak
    samples_with_speed: int
    window_samples: int
    half_window_s: float


def plateau_maxima(values: FloatArray) -> list[Plateau]:
    """Local maxima of a finite 1-D array (module rules: plateaus, edges)."""
    v = np.asarray(values, dtype=np.float64)
    n = v.size
    if n and not np.all(np.isfinite(v)):
        raise ValueError("plateau_maxima needs finite values")
    out: list[Plateau] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and v[j + 1] == v[i]:
            j += 1
        has_left, has_right = i > 0, j < n - 1
        if (
            (has_left or has_right)
            and (not has_left or v[i - 1] < v[i])
            and (not has_right or v[j + 1] < v[i])
        ):
            edge = RunEdge.START if not has_left else RunEdge.END if not has_right else RunEdge.NONE
            out.append(Plateau(first=i, last=j, peak=(i + j) // 2, edge=edge))
        i = j + 1
    return out


def _walk(v: FloatArray, start: int, step: int, height: float) -> tuple[int | None, bool]:
    """Lowest sample from ``start`` moving by ``step`` until a strictly higher sample or
    the end. Returns (base index or None if no sample, censored)."""
    n = v.size
    base: int | None = None
    k = start
    while 0 <= k < n:
        if v[k] > height:
            return base, False
        if base is None or v[k] < v[base]:  # strict: keep the one closest to the peak
            base = k
        k += step
    return base, True


def prominence(values: FloatArray, plateau: Plateau) -> Prominence:
    """Topographic prominence of ``plateau`` inside ``values`` (module rules)."""
    v = np.asarray(values, dtype=np.float64)
    h = float(v[plateau.first])
    left, left_cens = _walk(v, plateau.first - 1, -1, h)
    right, right_cens = _walk(v, plateau.last + 1, +1, h)
    bases = [float(v[b]) for b in (left, right) if b is not None]
    if not bases:
        raise ValueError("a plateau covering the whole array is not a maximum")
    sides = ProminenceSides.TWO_SIDED if len(bases) == 2 else ProminenceSides.ONE_SIDED
    return Prominence(
        value=h - max(bases),
        sides=sides,
        left_base=left,
        right_base=right,
        left_censored=left_cens if left is not None else True,
        right_censored=right_cens if right is not None else True,
    )


def _ref(tb: TimeBase, i: int) -> SampleRef:
    return SampleRef(index=i, proxy_frame=int(tb.proxy_frames[i]), t_s=float(tb.times_s[i]))


def _quality_counts(vel: KeypointVelocity, lo: int, hi: int) -> QualityCounts:
    q = vel.quality[lo : hi + 1]
    return QualityCounts(
        good=sum(x is KeypointQuality.GOOD for x in q),
        low_but_usable=sum(x is KeypointQuality.LOW for x in q),
    )


def _symmetric_window(vel: KeypointVelocity, i: int, half_window_s: float) -> QualityCounts:
    raw = vel.positions.raw
    tb = raw.timebase
    t = tb.times_s
    inside = np.flatnonzero(np.abs(t - t[i]) <= half_window_s + _EPS_S)
    lo, hi = int(inside[0]), int(inside[-1])
    good = low = unreliable = missing = 0
    for k in range(lo, hi + 1):
        if not (math.isfinite(raw.x_px[k]) and math.isfinite(raw.y_px[k])):
            missing += 1
        elif vel.quality[k] is KeypointQuality.GOOD:
            good += 1
        elif vel.quality[k] is KeypointQuality.LOW:
            low += 1
        else:
            unreliable += 1
    # interval k joins samples k-1 and k: it overlaps the window when k-1 <= hi and k >= lo
    irregular = sum(
        tb.interval_status[k] in (IntervalStatus.LONG, IntervalStatus.SHORT, IntervalStatus.INVALID)
        for k in range(max(lo, 1), min(hi + 1, t.size - 1) + 1)
    )
    return QualityCounts(good, low, unreliable, missing, irregular)


def wrist_peaks(vel: KeypointVelocity, wrist: Side) -> WristPeaks:
    """Every local maximum of ``vel`` (one wrist), run by run (module rules)."""
    sm = vel.positions.x
    tb = vel.positions.raw.timebase
    w = sm.window_samples
    half = w // 2
    half_window_s = half * tb.nominal_interval_s
    speed = vel.speed_2d_px_per_s
    keypoint = vel.name
    run = sm.run
    peaks: list[CandidatePeak] = []
    runs = runs_short = samples = 0
    for r in np.unique(run[run >= 0]).tolist():
        idx = np.flatnonzero(run == r)
        r0, r1 = int(idx[0]), int(idx[-1])
        runs += 1
        if idx.size < w:
            runs_short += 1
            continue
        values = speed[r0 : r1 + 1]
        samples += values.size
        for pl in plateau_maxima(values):
            pr = prominence(values, pl)
            i = r0 + pl.peak
            length: float | None = None
            scale_state: ScaleState | None = None
            if vel.scale_state is not None:
                scale_state = vel.scale_state[i]
            torso = float(vel.speed_2d_torso_per_s[i])
            if math.isfinite(torso):  # a maximum has speed > 0, so torso > 0
                length = float(speed[i]) / torso
            start = min(max(i - half, r0), r1 + 1 - w)
            lb = None if pr.left_base is None else r0 + pr.left_base
            rb = None if pr.right_base is None else r0 + pr.right_base
            span_lo = lb if lb is not None else r0 + pl.first
            span_hi = rb if rb is not None else r0 + pl.last
            peaks.append(
                CandidatePeak(
                    wrist=wrist,
                    keypoint=keypoint,
                    landmark_index=LANDMARK_INDEX[keypoint],
                    sample=_ref(tb, i),
                    plateau_first=_ref(tb, r0 + pl.first),
                    plateau_last=_ref(tb, r0 + pl.last),
                    run_id=int(r),
                    run_first=_ref(tb, r0),
                    run_last=_ref(tb, r1),
                    run_edge=pl.edge,
                    speed_px_per_s=float(speed[i]),
                    speed_torso_per_s=torso if math.isfinite(torso) else None,
                    velocity_state=vel.state[i],
                    scale_state=scale_state,
                    torso_length_px=length,
                    prominence_px_per_s=pr.value,
                    prominence_torso_per_s=None if length is None else pr.value / length,
                    prominence_sides=pr.sides,
                    left_base=None if lb is None else _ref(tb, lb),
                    right_base=None if rb is None else _ref(tb, rb),
                    left_censored=pr.left_censored,
                    right_censored=pr.right_censored,
                    fit_window=(_ref(tb, start), _ref(tb, start + w - 1)),
                    fit_counts=_quality_counts(vel, start, start + w - 1),
                    half_window_s=half_window_s,
                    quality_window=_symmetric_window(vel, i, half_window_s),
                    base_span_counts=_quality_counts(vel, span_lo, span_hi),
                )
            )
    peaks.sort(key=lambda p: (p.sample.t_s, p.sample.index))
    return WristPeaks(
        wrist=wrist,
        keypoint=keypoint,
        peaks=tuple(peaks),
        runs=runs,
        runs_too_short=runs_short,
        samples_with_speed=samples,
        window_samples=w,
        half_window_s=half_window_s,
    )


def find_wrist_peaks(
    poses: PoseSequence,
    timebase: TimeBase,
    scale: TorsoScale | None = None,
    params: SmoothingParams | None = None,
) -> dict[Side, WristPeaks]:
    """Peaks of both wrists, independently (no lateral assumption: the player's
    configuration is not used here)."""
    out: dict[Side, WristPeaks] = {}
    for side in ("left", "right"):
        vel = keypoint_velocity(poses, timebase, WRIST_KEYPOINT[side], scale, params)
        out[side] = wrist_peaks(vel, side)
    return out
