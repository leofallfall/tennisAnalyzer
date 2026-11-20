"""Apparent 2D speed of keypoints (M2.2, intervention 5).

The velocity is the **first derivative of the same local polynomial fit** used for the
smoothed positions (:func:`~tennis_cv.signals.smoothing.smooth_series`), evaluated at
each sample on real times. There is no second smoothing and no finite difference between
frames, so velocities inherit the runs, windows and states of the positions: no
derivative crosses a hole, a ``long`` / ``short`` / ``invalid`` interval or a segment
boundary.

What it is, and is not
----------------------
An **apparent, projected 2D speed** in the image plane: the rate of change of a keypoint's
pixel position. It ignores motion towards or away from the camera, carries MediaPipe's
VIDEO-mode filtering (peak timing reliable, amplitude not: 0.4-1.7x vs IMAGE mode), and is
meant for timing and for comparisons within one video. It is **not** the physical speed of
a body segment.

States (``VelocityState``, same as the smoothing state of the position)
-----------------------------------------------------------------------
* ``valid``: centred window.
* ``edge``: window shifted at a run edge. The slope is evaluated off-centre and is much
  noisier than a centred one (docs/M2_2_SIGNALS.md 6); exact only for polynomials up to
  the fit order.
* ``unreliable``: the position sample is not usable -> NaN.
* ``too_short``: run shorter than the window -> NaN.

``LOW`` samples (visibility 0.4-0.5) are inside runs, so they get a velocity like ``GOOD``
ones; their quality stays visible in ``quality`` and every value reports how many ``LOW``
samples its window used (``low_in_window``). Consumers decide whether to accept them.

``speed_2d_torso_per_s`` = ``speed_2d_px_per_s`` / stabilised torso length
(:mod:`~tennis_cv.signals.scale`); NaN where the scale is missing, while the pixel values
stay available and unchanged. No dispersion threshold is applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.scale import ScaleState, TorsoScale
from tennis_cv.signals.smoothing import (
    KeypointQuality,
    SmoothedKeypoint,
    SmoothingParams,
    SmoothState,
    extract_keypoint,
    smooth_keypoint,
)
from tennis_cv.signals.timebase import TimeBase

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


class VelocityState(StrEnum):
    VALID = "valid"
    EDGE = "edge"
    UNRELIABLE = "unreliable"
    TOO_SHORT = "too_short"


_FROM_SMOOTH = {
    SmoothState.SMOOTHED: VelocityState.VALID,
    SmoothState.EDGE: VelocityState.EDGE,
    SmoothState.UNRELIABLE: VelocityState.UNRELIABLE,
    SmoothState.TOO_SHORT: VelocityState.TOO_SHORT,
}


@dataclass(frozen=True)
class KeypointVelocity:
    """Per time-base sample. Pixel values are never replaced by normalised ones."""

    name: str
    vx_px_per_s: FloatArray
    vy_px_per_s: FloatArray
    speed_2d_px_per_s: FloatArray
    speed_2d_torso_per_s: FloatArray  # NaN where the torso scale is missing
    state: tuple[VelocityState, ...]
    quality: tuple[KeypointQuality, ...]  # quality of the position sample itself
    low_in_window: IntArray
    scale_state: tuple[ScaleState, ...] | None  # None when no scale was given
    positions: SmoothedKeypoint

    def count(self, state: VelocityState) -> int:
        return sum(s is state for s in self.state)


def velocity_from_smoothed(
    positions: SmoothedKeypoint, scale: TorsoScale | None = None
) -> KeypointVelocity:
    """Velocity from already smoothed positions (same fit, same states)."""
    if positions.x.state != positions.y.state:  # same runs and windows by construction
        raise ValueError("x and y smoothing states differ")
    vx = positions.x.derivative.copy()
    vy = positions.y.derivative.copy()
    speed = np.hypot(vx, vy)
    if scale is not None:
        if scale.length_px.shape != speed.shape:
            raise ValueError("torso scale and positions must share the time base")
        speed_torso = scale.to_torso_units(speed)
        scale_state: tuple[ScaleState, ...] | None = scale.state
    else:
        speed_torso = np.full(speed.shape, np.nan)
        scale_state = None
    return KeypointVelocity(
        name=positions.raw.name,
        vx_px_per_s=vx,
        vy_px_per_s=vy,
        speed_2d_px_per_s=speed,
        speed_2d_torso_per_s=speed_torso,
        state=tuple(_FROM_SMOOTH[s] for s in positions.x.state),
        quality=positions.raw.quality,
        low_in_window=positions.x.low_in_window.copy(),
        scale_state=scale_state,
        positions=positions,
    )


def keypoint_velocity(
    poses: PoseSequence,
    timebase: TimeBase,
    name: str,
    scale: TorsoScale | None = None,
    params: SmoothingParams | None = None,
) -> KeypointVelocity:
    """Extract, smooth and differentiate keypoint ``name`` in one call."""
    raw = extract_keypoint(poses, timebase, name, params)
    return velocity_from_smoothed(smooth_keypoint(raw, params), scale)
