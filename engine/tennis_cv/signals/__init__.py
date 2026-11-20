"""Pose signal processing (M2.2). Pure library: not used by the pipeline or the worker.

Intervention 1: :mod:`~tennis_cv.signals.timebase`, the time base of the pose samples
(source vs proxy times, repeated frames, irregular intervals, map checks).
Interventions 2-3: :mod:`~tennis_cv.signals.smoothing`, keypoint quality (good / low but
usable / unreliable) and local polynomial smoothing on real times within runs.
Intervention 4: :mod:`~tennis_cv.signals.scale`, robust torso length (2D scale).
Intervention 5: :mod:`~tennis_cv.signals.velocity`, apparent 2D speed from the derivative
of the same local fit. Accelerations and other signals are not implemented.
See docs/M2_2_SIGNALS.md.
"""

from tennis_cv.signals.scale import (
    ScaleParams,
    ScaleState,
    TorsoScale,
    stabilise,
    torso_measurements,
    torso_scale,
)
from tennis_cv.signals.smoothing import (
    KeypointQuality,
    RawKeypointSignal,
    SmoothedKeypoint,
    SmoothedSignal,
    SmoothingParams,
    SmoothState,
    classify_quality,
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
from tennis_cv.signals.velocity import (
    KeypointVelocity,
    VelocityState,
    keypoint_velocity,
    velocity_from_smoothed,
)

__all__ = [
    "IntervalStatus",
    "KeypointQuality",
    "KeypointVelocity",
    "MapStatus",
    "RawKeypointSignal",
    "ScaleParams",
    "ScaleState",
    "SmoothState",
    "SmoothedKeypoint",
    "SmoothedSignal",
    "SmoothingParams",
    "TimeBase",
    "TimeBaseParams",
    "TimeBasis",
    "TorsoScale",
    "VelocityState",
    "build_timebase",
    "check_timemap",
    "classify_intervals",
    "classify_quality",
    "extract_keypoint",
    "find_runs",
    "keypoint_velocity",
    "smooth_keypoint",
    "smooth_series",
    "stabilise",
    "torso_measurements",
    "torso_scale",
    "velocity_from_smoothed",
]
