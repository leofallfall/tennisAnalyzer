"""Pose signal processing (M2.2). Pure library: not used by the pipeline or the worker.

Intervention 1: :mod:`~tennis_cv.signals.timebase`, the time base of the pose samples
(source vs proxy times, repeated frames, irregular intervals, map checks).
Interventions 2-3: :mod:`~tennis_cv.signals.smoothing`, keypoint quality (good / low but
usable / unreliable) and local polynomial smoothing on real times within runs.
Intervention 4: :mod:`~tennis_cv.signals.scale`, robust torso length (2D scale).
Intervention 5: :mod:`~tennis_cv.signals.velocity`, apparent 2D speed from the derivative
of the same local fit.
Intervention 6: :mod:`~tennis_cv.signals.body`, shoulder / hip lines, widths, distance
between the hands, wrist position relative to the hips, and the player configuration;
:func:`body_signals` returns every signal with its state. Accelerations are not
implemented; peak finding belongs to M2.3.
See docs/M2_2_SIGNALS.md.
"""

from tennis_cv.signals.body import (
    BODY_KEYPOINTS,
    BodySignal,
    BodySignals,
    PlayerConfig,
    SignalState,
    body_signals,
    combine_states,
    distance_px,
    line_angle_deg,
    signals_from_positions,
)
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
    "BODY_KEYPOINTS",
    "BodySignal",
    "BodySignals",
    "IntervalStatus",
    "KeypointQuality",
    "KeypointVelocity",
    "MapStatus",
    "PlayerConfig",
    "RawKeypointSignal",
    "ScaleParams",
    "ScaleState",
    "SignalState",
    "SmoothState",
    "SmoothedKeypoint",
    "SmoothedSignal",
    "SmoothingParams",
    "TimeBase",
    "TimeBaseParams",
    "TimeBasis",
    "TorsoScale",
    "VelocityState",
    "body_signals",
    "build_timebase",
    "check_timemap",
    "classify_intervals",
    "classify_quality",
    "combine_states",
    "distance_px",
    "extract_keypoint",
    "find_runs",
    "keypoint_velocity",
    "line_angle_deg",
    "signals_from_positions",
    "smooth_keypoint",
    "smooth_series",
    "stabilise",
    "torso_measurements",
    "torso_scale",
    "velocity_from_smoothed",
]
