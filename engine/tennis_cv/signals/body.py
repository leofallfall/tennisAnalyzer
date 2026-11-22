"""Complementary body signals and player configuration (M2.2, intervention 6).

Every signal is computed **per sample from the smoothed positions** of intervention 2-3
(same runs, windows and states), on the image plane. Nothing is smoothed again, nothing is
interpolated, and pixel values are always kept next to the normalised ones.

Signals
-------
* ``shoulder_line_deg`` / ``hip_line_deg``: direction of the line from the **left** to the
  **right** keypoint (anatomical sides as labelled by MediaPipe), in degrees in (-180, 180],
  measured in the image with the y axis pointing **up**: 0 = right keypoint to the image
  right of the left one (a player seen from behind, square to the camera), 180 = to the
  image left (seen from the front), +90 = right keypoint above the left one. It is a
  projected 2D angle: it mixes tilt and rotation of the body with the camera view and is
  not a 3D rotation. The value wraps at +-180; it is not unwrapped and not differentiated.
* ``shoulder_width`` / ``hip_width``: apparent distance between the two keypoints (px and
  torso lengths). It shrinks when the line turns towards the camera axis.
* ``hands_distance``: distance between the two wrists (px and torso lengths). **GOOD
  samples only** (rule from the review of #3): the value is withheld (``low_excluded``)
  whenever the fit of either wrist used a ``LOW`` sample.
* ``<side>_wrist_lateral`` / ``<side>_wrist_vertical``: position of each wrist relative to
  the **mid-hip point**, in **image axes**: lateral = image x (positive = image right),
  vertical = image y pointing up (positive = above the mid-hip point). Lateral and
  vertical are separate signals. Image axes are used instead of axes attached to the
  torso so that bending or tilting the trunk does not change the reference; the image
  side that is "towards the dominant hand" depends on the view and is not resolved here.
  ``LOW`` samples are allowed and marked by ``low_in_window``, as for velocities.

Torso units = stabilised torso length (:mod:`~tennis_cv.signals.scale`); a normalised value
is NaN where the scale is missing (``scale_state``), independently of the signal ``state``.

States (``SignalState``), combined over all keypoints a signal uses
-------------------------------------------------------------------
``unreliable`` (any keypoint unreliable) > ``too_short`` (any run too short) >
``low_excluded`` (GOOD-only signal, a LOW sample in a window) > ``edge`` (any window shifted
at a run edge) > ``valid``. Only ``valid`` and ``edge`` have a value. ``low_in_window`` is
the sum over the keypoints used.

Player configuration
--------------------
:class:`PlayerConfig` (dominant hand, backhand type) is an explicit input, never assumed.
It does not change any value: it names the dominant and non-dominant wrists and the wrist
of reference for each stroke type (convention of the M2.1 labels: dominant wrist for
forehands and one-handed backhands, non-dominant wrist for two-handed backhands, the one
seen best on Leonardo's clips). ``unknown`` gives no role (None).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import numpy as np
import numpy.typing as npt

from tennis_cv.evaluation.dataset import Backhand, Hand
from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.scale import ScaleParams, ScaleState, TorsoScale, torso_scale
from tennis_cv.signals.smoothing import (
    SmoothedKeypoint,
    SmoothingParams,
    SmoothState,
    extract_keypoint,
    smooth_keypoint,
)
from tennis_cv.signals.timebase import TimeBase
from tennis_cv.signals.velocity import KeypointVelocity, velocity_from_smoothed

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

Side = Literal["left", "right"]
StrokeSide = Literal["forehand", "backhand"]

BODY_KEYPOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_wrist",
    "right_wrist",
)


@dataclass(frozen=True)
class PlayerConfig:
    """Declared by the user, never inferred. Names roles; changes no value."""

    dominant_hand: Hand
    backhand: Backhand

    @property
    def dominant_side(self) -> Side | None:
        if self.dominant_hand is Hand.RIGHT:
            return "right"
        if self.dominant_hand is Hand.LEFT:
            return "left"
        return None

    @property
    def non_dominant_side(self) -> Side | None:
        side = self.dominant_side
        if side is None:
            return None
        return "left" if side == "right" else "right"

    def reference_side(self, stroke: StrokeSide) -> Side | None:
        """Wrist of reference for a stroke type (module docstring); None if unknown."""
        if stroke == "forehand":
            return self.dominant_side
        if self.backhand is Backhand.ONE_HANDED:
            return self.dominant_side
        if self.backhand is Backhand.TWO_HANDED:
            return self.non_dominant_side
        return None


class SignalState(StrEnum):
    VALID = "valid"
    EDGE = "edge"
    LOW_EXCLUDED = "low_excluded"  # GOOD-only signal and a LOW sample was used: no value
    UNRELIABLE = "unreliable"
    TOO_SHORT = "too_short"


@dataclass(frozen=True)
class BodySignal:
    """One signal per time-base sample.

    ``value`` is in ``unit`` (``deg`` or ``px``); ``value_torso`` is the same value in torso
    lengths for ``px`` signals (NaN where the scale is missing) and None for angles.
    ``state`` never depends on the scale; ``scale_state`` is reported apart."""

    name: str
    unit: Literal["deg", "px"]
    keypoints: tuple[str, ...]
    good_only: bool
    value: FloatArray
    value_torso: FloatArray | None
    state: tuple[SignalState, ...]
    low_in_window: IntArray
    scale_state: tuple[ScaleState, ...] | None

    def count(self, state: SignalState) -> int:
        return sum(s is state for s in self.state)


@dataclass(frozen=True)
class BodySignals:
    """All M2.2 signals of one pose sequence, each with its state."""

    timebase: TimeBase
    player: PlayerConfig
    scale: TorsoScale
    positions: dict[str, SmoothedKeypoint]
    shoulder_line_deg: BodySignal
    shoulder_width: BodySignal
    hip_line_deg: BodySignal
    hip_width: BodySignal
    hands_distance: BodySignal
    wrist_lateral: dict[Side, BodySignal]
    wrist_vertical: dict[Side, BodySignal]
    wrist_velocity: dict[Side, KeypointVelocity]

    def all(self) -> tuple[BodySignal, ...]:
        return (
            self.shoulder_line_deg,
            self.shoulder_width,
            self.hip_line_deg,
            self.hip_width,
            self.hands_distance,
            self.wrist_lateral["left"],
            self.wrist_vertical["left"],
            self.wrist_lateral["right"],
            self.wrist_vertical["right"],
        )


def combine_states(
    parts: tuple[SmoothedKeypoint, ...], good_only: bool = False
) -> tuple[tuple[SignalState, ...], IntArray]:
    """Combined state and LOW count of a signal that uses ``parts`` (module rules)."""
    n = parts[0].x.smoothed.size
    low = np.zeros(n, dtype=np.int64)
    for p in parts:
        if p.x.state != p.y.state:  # same runs and windows by construction
            raise ValueError(f"x and y smoothing states differ for {p.raw.name}")
        low += p.x.low_in_window
    states: list[SignalState] = []
    for i in range(n):
        s = {p.x.state[i] for p in parts}
        if SmoothState.UNRELIABLE in s:
            states.append(SignalState.UNRELIABLE)
        elif SmoothState.TOO_SHORT in s:
            states.append(SignalState.TOO_SHORT)
        elif good_only and low[i] > 0:
            states.append(SignalState.LOW_EXCLUDED)
        elif SmoothState.EDGE in s:
            states.append(SignalState.EDGE)
        else:
            states.append(SignalState.VALID)
    return tuple(states), low


def _has_value(states: tuple[SignalState, ...]) -> npt.NDArray[np.bool_]:
    return np.array([s in (SignalState.VALID, SignalState.EDGE) for s in states], dtype=bool)


def _signal(
    name: str,
    unit: Literal["deg", "px"],
    parts: tuple[SmoothedKeypoint, ...],
    raw_value: FloatArray,
    scale: TorsoScale | None,
    good_only: bool = False,
) -> BodySignal:
    states, low = combine_states(parts, good_only)
    value = np.where(_has_value(states), raw_value, np.nan)
    torso = None
    if unit == "px":
        torso = scale.to_torso_units(value) if scale is not None else np.full(value.shape, np.nan)
    return BodySignal(
        name=name,
        unit=unit,
        keypoints=tuple(p.raw.name for p in parts),
        good_only=good_only,
        value=value,
        value_torso=torso,
        state=states,
        low_in_window=low,
        scale_state=scale.state if scale is not None else None,
    )


def line_angle_deg(left: SmoothedKeypoint, right: SmoothedKeypoint) -> FloatArray:
    """Direction of left -> right in degrees, image y up, in (-180, 180]."""
    dx = right.x.smoothed - left.x.smoothed
    dy_up = -(right.y.smoothed - left.y.smoothed)
    deg = np.degrees(np.arctan2(dy_up, dx))
    return np.where(deg == -180.0, 180.0, deg)


def distance_px(a: SmoothedKeypoint, b: SmoothedKeypoint) -> FloatArray:
    return np.hypot(b.x.smoothed - a.x.smoothed, b.y.smoothed - a.y.smoothed)


def signals_from_positions(
    positions: dict[str, SmoothedKeypoint],
    player: PlayerConfig,
    scale: TorsoScale,
) -> BodySignals:
    """All signals from already smoothed positions of :data:`BODY_KEYPOINTS`."""
    missing = [k for k in BODY_KEYPOINTS if k not in positions]
    if missing:
        raise ValueError(f"missing keypoints: {missing}")
    p = positions
    if any(p[k].x.smoothed.shape != scale.length_px.shape for k in BODY_KEYPOINTS):
        raise ValueError("torso scale and positions must share the time base")
    ls, rs, lh, rh = p["left_shoulder"], p["right_shoulder"], p["left_hip"], p["right_hip"]
    lw, rw = p["left_wrist"], p["right_wrist"]
    hip_x = (lh.x.smoothed + rh.x.smoothed) / 2
    hip_y = (lh.y.smoothed + rh.y.smoothed) / 2
    lateral: dict[Side, BodySignal] = {}
    vertical: dict[Side, BodySignal] = {}
    sides: tuple[tuple[Side, SmoothedKeypoint], ...] = (("left", lw), ("right", rw))
    for side, w in sides:
        parts = (w, lh, rh)
        lateral[side] = _signal(f"{side}_wrist_lateral", "px", parts, w.x.smoothed - hip_x, scale)
        vertical[side] = _signal(f"{side}_wrist_vertical", "px", parts, hip_y - w.y.smoothed, scale)
    return BodySignals(
        timebase=scale.timebase,
        player=player,
        scale=scale,
        positions=dict(positions),
        shoulder_line_deg=_signal(
            "shoulder_line_deg", "deg", (ls, rs), line_angle_deg(ls, rs), None
        ),
        shoulder_width=_signal("shoulder_width", "px", (ls, rs), distance_px(ls, rs), scale),
        hip_line_deg=_signal("hip_line_deg", "deg", (lh, rh), line_angle_deg(lh, rh), None),
        hip_width=_signal("hip_width", "px", (lh, rh), distance_px(lh, rh), scale),
        hands_distance=_signal(
            "hands_distance", "px", (lw, rw), distance_px(lw, rw), scale, good_only=True
        ),
        wrist_lateral=lateral,
        wrist_vertical=vertical,
        wrist_velocity={
            "left": velocity_from_smoothed(lw, scale),
            "right": velocity_from_smoothed(rw, scale),
        },
    )


def body_signals(
    poses: PoseSequence,
    timebase: TimeBase,
    player: PlayerConfig,
    params: SmoothingParams | None = None,
    scale_params: ScaleParams | None = None,
) -> BodySignals:
    """Extract, smooth and combine everything M2.2 provides for one pose sequence."""
    positions = {
        k: smooth_keypoint(extract_keypoint(poses, timebase, k, params), params)
        for k in BODY_KEYPOINTS
    }
    scale = torso_scale(poses, timebase, scale_params, params)
    return signals_from_positions(positions, player, scale)
