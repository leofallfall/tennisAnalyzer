"""Pose signal processing (M2.2). Pure library: not used by the pipeline or the worker.

Intervention 1 (this package so far): :mod:`~tennis_cv.signals.timebase`, the time base
of the pose samples (source vs proxy times, repeated frames, irregular intervals, map
checks). Smoothing, derivatives and other signals are not implemented yet.
See docs/M2_2_SIGNALS.md.
"""

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
    "TimeBase",
    "TimeBaseParams",
    "TimeBasis",
    "build_timebase",
    "check_timemap",
    "classify_intervals",
]
