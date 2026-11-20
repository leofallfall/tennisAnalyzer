"""Pose signal processing (M2.2). Pure library: not used by the pipeline or the worker.

Intervention 1: :mod:`~tennis_cv.signals.timebase`, the time base of the pose samples
(source vs proxy times, repeated frames, irregular intervals, map checks).
Intervention 2: :mod:`~tennis_cv.signals.smoothing`, local polynomial smoothing on real
times within runs of reliable samples. Derivatives and other signals are not implemented.
See docs/M2_2_SIGNALS.md.
"""

from tennis_cv.signals.smoothing import (
    RawKeypointSignal,
    SmoothedKeypoint,
    SmoothedSignal,
    SmoothingParams,
    SmoothState,
    extract_keypoint,
    find_runs,
    smooth_keypoint,
    smooth_series,
)
from tennis_cv.signals.timebase import (
    IntervalStatus,
    MapStatus,
    TimeBase,
    TimeBaseParams,
    TimeBasis,
    build_timebase,
    check_timemap,
    classify_intervals,
)

__all__ = [
    "IntervalStatus",
    "MapStatus",
    "RawKeypointSignal",
    "SmoothState",
    "SmoothedKeypoint",
    "SmoothedSignal",
    "SmoothingParams",
    "TimeBase",
    "TimeBaseParams",
    "TimeBasis",
    "build_timebase",
    "check_timemap",
    "classify_intervals",
    "extract_keypoint",
    "find_runs",
    "smooth_keypoint",
    "smooth_series",
]
