"""Temporal smoothing of pose signals (M2.2, intervention 2).

Local polynomial least-squares fit (the Savitzky-Golay idea) on the **real sample times**
of a :class:`~tennis_cv.signals.timebase.TimeBase`: each sample's polynomial is fitted
in ``t - t_i`` seconds, so non-uniform intervals (iPhone 31.67 / 35.00 ms, repeats
removed) are taken into account instead of assuming a constant frame step.

Rules
-----
* **Runs.** A fit never mixes samples from different time-base segments (no fit across
  a ``long`` / ``short`` / ``invalid`` interval) and never bridges an unreliable sample:
  smoothing works on *runs* = consecutive reliable samples of one segment.
* **No value without data.** An unreliable sample (not detected, keypoint missing,
  visibility below threshold, non-finite coordinate) gets no smoothed value
  (``UNRELIABLE``, NaN); it is not interpolated. A run shorter than the window gets no
  smoothed value either (``TOO_SHORT``, NaN).
* **Edges.** The window always has the same number of samples and stays inside the run.
  Near a run edge it is shifted, not shrunk, and the polynomial is evaluated inside the
  fitted interval (never extrapolated). Those samples are marked ``EDGE``: off-centre
  fits are noisier than centred ones (higher variance at the end of the window).
* **Raw vs smoothed.** Raw values are returned unchanged next to the smoothed ones.

Nothing here computes velocities, accelerations or peaks (later interventions).
Parameters are general (not player-specific) starting values, not validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.timebase import TimeBase

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]


class SmoothState(StrEnum):
    SMOOTHED = "smoothed"  # centred window inside one run
    EDGE = "edge"  # window shifted at a run edge (valid value, noisier)
    UNRELIABLE = "unreliable"  # raw sample not reliable: no value
    TOO_SHORT = "too_short"  # run shorter than the window: no value


@dataclass(frozen=True)
class SmoothingParams:
    """``window_s``: target window duration, converted to an odd number of samples with
    the time base's nominal interval (0.2 s -> 7 samples at 30 fps, 5 at 24 fps), never
    fewer than ``polyorder + 2`` (a fit with no redundancy does not smooth).
    ``min_visibility``: approved starting threshold (0.5), not validated."""

    window_s: float = 0.2
    polyorder: int = 2
    min_visibility: float = 0.5

    def __post_init__(self) -> None:
        if not (math.isfinite(self.window_s) and self.window_s > 0):
            raise ValueError("window_s must be finite and > 0")
        if self.polyorder < 0:
            raise ValueError("polyorder must be >= 0")
        if not 0 <= self.min_visibility <= 1:
            raise ValueError("min_visibility must be in [0, 1]")

    def window_samples(self, nominal_interval_s: float) -> int:
        n = round(self.window_s / nominal_interval_s)
        n = max(n, self.polyorder + 2)
        return n if n % 2 else n + 1


@dataclass(frozen=True)
class RawKeypointSignal:
    """Image-plane position of one keypoint on the samples of a time base.

    Pixels (``x * image_width``, ``y * image_height``) so both axes have the same unit.
    Values are what MediaPipe wrote (NaN only when the pose was not detected or the
    keypoint is absent); ``reliable`` says which samples smoothing may use."""

    name: str
    timebase: TimeBase
    x_px: FloatArray
    y_px: FloatArray
    visibility: FloatArray  # NaN when missing
    reliable: BoolArray


@dataclass(frozen=True)
class SmoothedSignal:
    """One smoothed series. ``raw`` is the input, untouched; ``smoothed`` is NaN wherever
    ``state`` is UNRELIABLE or TOO_SHORT. ``run`` = run id (-1 for unreliable samples)."""

    raw: FloatArray
    smoothed: FloatArray
    state: tuple[SmoothState, ...]
    run: IntArray
    window_samples: int
    polyorder: int

    def count(self, state: SmoothState) -> int:
        return sum(s is state for s in self.state)


def extract_keypoint(
    poses: PoseSequence,
    timebase: TimeBase,
    name: str,
    params: SmoothingParams | None = None,
) -> RawKeypointSignal:
    """Raw position of keypoint ``name`` on the samples of ``timebase``."""
    params = params or SmoothingParams()
    by_index = {f.frame_index: f for f in poses.frames}
    n = timebase.proxy_frames.size
    x = np.full(n, np.nan)
    y = np.full(n, np.nan)
    vis = np.full(n, np.nan)
    for i, frame_index in enumerate(timebase.proxy_frames.tolist()):
        frame = by_index[frame_index]
        if not frame.detected:
            continue
        kp = next((k for k in frame.keypoints if k.name == name), None)
        if kp is None:
            continue
        vis[i] = np.nan if kp.visibility is None else kp.visibility
        x[i] = kp.x * poses.image_width
        y[i] = kp.y * poses.image_height
    reliable = np.isfinite(x) & np.isfinite(y) & (vis >= params.min_visibility)
    return RawKeypointSignal(name, timebase, x, y, vis, reliable)


def find_runs(segment: IntArray, reliable: BoolArray) -> IntArray:
    """Run id per sample: consecutive reliable samples of one segment; -1 if unreliable."""
    run = np.full(segment.size, -1, dtype=np.int64)
    current = -1
    for i in range(segment.size):
        if not reliable[i]:
            continue
        if i == 0 or not reliable[i - 1] or segment[i] != segment[i - 1]:
            current += 1
        run[i] = current
    return run


def smooth_series(
    times_s: FloatArray,
    values: FloatArray,
    segment: IntArray,
    reliable: BoolArray,
    window_samples: int,
    polyorder: int,
    time_scale_s: float,
) -> SmoothedSignal:
    """Local polynomial fit of ``values`` on real ``times_s``, within runs (module rules).

    ``time_scale_s`` (the nominal interval) only conditions the normal equations."""
    if window_samples < polyorder + 2 or window_samples % 2 == 0:
        raise ValueError("window_samples must be odd and >= polyorder + 2")
    n = times_s.size
    if not (values.size == segment.size == reliable.size == n):
        raise ValueError("times, values, segment and reliable must have the same length")
    reliable = reliable & np.isfinite(values) & np.isfinite(times_s)
    run = find_runs(segment, reliable)
    smoothed = np.full(n, np.nan)
    states = [SmoothState.UNRELIABLE] * n
    half = window_samples // 2

    # window start of every sample that can be smoothed (run long enough)
    targets: list[int] = []
    starts: list[int] = []
    run_ids = run[run >= 0]
    if run_ids.size:
        first = {int(r): int(i) for i, r in reversed(list(enumerate(run.tolist()))) if r >= 0}
        lengths = np.bincount(run_ids)
        for i in range(n):
            r = int(run[i])
            if r < 0:
                continue
            r0, length = first[r], int(lengths[r])
            if length < window_samples:
                states[i] = SmoothState.TOO_SHORT
                continue
            start = min(max(i - half, r0), r0 + length - window_samples)
            states[i] = SmoothState.SMOOTHED if start == i - half else SmoothState.EDGE
            targets.append(i)
            starts.append(start)

    if targets:
        tgt = np.asarray(targets)
        win = np.asarray(starts)[:, None] + np.arange(window_samples)[None, :]
        dt = (times_s[win] - times_s[tgt][:, None]) / time_scale_s  # (m, w)
        vander = dt[..., None] ** np.arange(polyorder + 1)  # (m, w, p+1)
        normal = np.einsum("mwi,mwj->mij", vander, vander)
        rhs = np.einsum("mwi,mw->mi", vander, values[win])
        coeffs = np.linalg.solve(normal, rhs[..., None])[..., 0]
        smoothed[tgt] = coeffs[:, 0]  # polynomial value at dt = 0

    return SmoothedSignal(
        raw=values.copy(),
        smoothed=smoothed,
        state=tuple(states),
        run=run,
        window_samples=window_samples,
        polyorder=polyorder,
    )


@dataclass(frozen=True)
class SmoothedKeypoint:
    raw: RawKeypointSignal
    x: SmoothedSignal
    y: SmoothedSignal


def smooth_keypoint(
    signal: RawKeypointSignal, params: SmoothingParams | None = None
) -> SmoothedKeypoint:
    """Smooth x and y of a keypoint independently, with the same runs and windows."""
    params = params or SmoothingParams()
    tb = signal.timebase
    w = params.window_samples(tb.nominal_interval_s)

    def run(values: FloatArray) -> SmoothedSignal:
        return smooth_series(
            tb.times_s, values, tb.segment, signal.reliable, w, params.polyorder,
            tb.nominal_interval_s,
        )  # fmt: skip

    return SmoothedKeypoint(raw=signal, x=run(signal.x_px), y=run(signal.y_px))
