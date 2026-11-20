"""Body scale for pose signals: robust torso length (M2.2, intervention 4).

Pixel distances depend on how far the player is from the camera. Later signals are
expressed in *torso lengths* so that they are comparable within a video and across
videos, without body-specific constants. This is a 2D scale normalisation of the image,
not a 3D measurement.

Rules
-----
* **Measurement.** ``torso length = |mid-shoulder - mid-hip|`` in pixels, from the raw
  positions of the four keypoints, only on samples where all four are ``GOOD``
  (``LOW`` and ``UNRELIABLE`` samples are not used for the scale).
* **Stabilisation.** Rolling median of those measurements over ``window_s`` (1 s) of
  real time, centred on each sample and limited to its time-base segment. A median over
  ~1 s removes per-frame noise, isolated wrong keypoints and the short shortening of the
  projected torso during a stroke (bending, rotation: ~0.3 s), while it follows changes of
  distance from the camera that take longer than about half a window (a linear change
  is followed without lag).
* **No value without data.** If the window holds fewer than ``min_fraction`` of the
  samples it should hold (or no measurement), the scale is missing (``INSUFFICIENT``) and
  every value normalised with it is NaN.
* **Spread.** ``relative_spread`` = median absolute deviation / median in the window: a
  high value means the projected torso changed a lot inside the window (posture, a wrong
  keypoint, a person switch), i.e. a less trustworthy scale. It is reported, not used to
  reject samples (no validated threshold).

Pixel coordinates are never replaced: normalised values are computed on request.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.smoothing import (
    KeypointQuality,
    RawKeypointSignal,
    SmoothingParams,
    extract_keypoint,
)
from tennis_cv.signals.timebase import TimeBase

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

TORSO_KEYPOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")


class ScaleState(StrEnum):
    OK = "ok"
    INSUFFICIENT = "insufficient"  # too few GOOD torso measurements in the window


@dataclass(frozen=True)
class ScaleParams:
    """General parameters (not player-specific), starting values, not validated."""

    window_s: float = 1.0
    min_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not (math.isfinite(self.window_s) and self.window_s > 0):
            raise ValueError("window_s must be finite and > 0")
        if not 0 < self.min_fraction <= 1:
            raise ValueError("min_fraction must be in (0, 1]")


@dataclass(frozen=True)
class TorsoScale:
    """Per sample of the time base.

    ``measured_px``: instantaneous torso length (NaN unless all four keypoints GOOD).
    ``length_px``: stabilised torso length (NaN when ``state`` is INSUFFICIENT).
    ``measures_in_window``: GOOD measurements used by the median.
    ``relative_spread``: MAD / median of those measurements (NaN when no value).
    """

    timebase: TimeBase
    measured_px: FloatArray
    length_px: FloatArray
    state: tuple[ScaleState, ...]
    measures_in_window: IntArray
    relative_spread: FloatArray
    params: ScaleParams

    def to_torso_units(self, values_px: FloatArray) -> FloatArray:
        """Pixel lengths / distances -> torso lengths; NaN where the scale is missing.
        ``values_px`` is not modified."""
        if values_px.shape != self.length_px.shape:
            raise ValueError("values must have one entry per time-base sample")
        return values_px / self.length_px

    def count(self, state: ScaleState) -> int:
        return sum(s is state for s in self.state)


def torso_measurements(
    left_shoulder: RawKeypointSignal,
    right_shoulder: RawKeypointSignal,
    left_hip: RawKeypointSignal,
    right_hip: RawKeypointSignal,
) -> FloatArray:
    """Instantaneous torso length in pixels; NaN unless all four samples are GOOD."""
    parts = (left_shoulder, right_shoulder, left_hip, right_hip)
    good = np.ones(left_shoulder.x_px.size, dtype=bool)
    for p in parts:
        good &= np.array([q is KeypointQuality.GOOD for q in p.quality], dtype=bool)
    sx = (left_shoulder.x_px + right_shoulder.x_px) / 2
    sy = (left_shoulder.y_px + right_shoulder.y_px) / 2
    hx = (left_hip.x_px + right_hip.x_px) / 2
    hy = (left_hip.y_px + right_hip.y_px) / 2
    length = np.hypot(sx - hx, sy - hy)
    length[~good | ~np.isfinite(length) | (length <= 0)] = np.nan
    return length


def stabilise(
    timebase: TimeBase, measured_px: FloatArray, params: ScaleParams | None = None
) -> TorsoScale:
    """Rolling median of ``measured_px`` in real time, within segments (module rules)."""
    params = params or ScaleParams()
    t = timebase.times_s
    n = t.size
    if measured_px.size != n:
        raise ValueError("one measurement per time-base sample is required")
    half = params.window_s / 2
    expected = params.window_s / timebase.nominal_interval_s + 1
    min_measures = max(1, math.ceil(params.min_fraction * expected))
    length = np.full(n, np.nan)
    spread = np.full(n, np.nan)
    count = np.zeros(n, dtype=np.int64)
    states = [ScaleState.INSUFFICIENT] * n
    seg = timebase.segment
    bounds = np.flatnonzero(np.diff(seg)) + 1
    for s0, s1 in zip(np.r_[0, bounds], np.r_[bounds, n], strict=True):
        ts = t[s0:s1]
        ms = measured_px[s0:s1]
        lo = np.searchsorted(ts, ts - half - 1e-9, side="left")
        hi = np.searchsorted(ts, ts + half + 1e-9, side="right")
        for k in range(ts.size):
            window = ms[lo[k] : hi[k]]
            window = window[np.isfinite(window)]
            count[s0 + k] = window.size
            if window.size < min_measures:
                continue
            med = float(np.median(window))
            length[s0 + k] = med
            spread[s0 + k] = float(np.median(np.abs(window - med))) / med
            states[s0 + k] = ScaleState.OK
    return TorsoScale(
        timebase=timebase,
        measured_px=measured_px.copy(),
        length_px=length,
        state=tuple(states),
        measures_in_window=count,
        relative_spread=spread,
        params=params,
    )


def torso_scale(
    poses: PoseSequence,
    timebase: TimeBase,
    params: ScaleParams | None = None,
    quality: SmoothingParams | None = None,
) -> TorsoScale:
    """Torso scale of ``poses`` on ``timebase``; ``quality`` sets what GOOD means."""
    ls, rs, lh, rh = (extract_keypoint(poses, timebase, k, quality) for k in TORSO_KEYPOINTS)
    return stabilise(timebase, torso_measurements(ls, rs, lh, rh), params)
