"""M2.2 #6: complementary body signals and player configuration.
Synthetic geometry with known answers; nothing here is a biomechanical claim."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import numpy as np
import pytest

from tennis_cv.evaluation.dataset import Backhand, Hand
from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.signals import (
    BodySignals,
    PlayerConfig,
    ScaleState,
    SignalState,
    body_signals,
    build_timebase,
)

FPS = 30.0
W_IMG, H_IMG = 720, 1280
RIGHT_2H = PlayerConfig(Hand.RIGHT, Backhand.TWO_HANDED)
LEFT_2H = PlayerConfig(Hand.LEFT, Backhand.TWO_HANDED)

# player seen from behind, square: left keypoints on the image left. Torso = 200 px.
BASE = {
    "left_shoulder": (320.0, 500.0),
    "right_shoulder": (400.0, 500.0),
    "left_hip": (330.0, 700.0),
    "right_hip": (390.0, 700.0),
    "left_wrist": (300.0, 650.0),
    "right_wrist": (480.0, 600.0),
}

Points = dict[str, tuple[float, float]]
Side = Literal["left", "right"]


def _poses(
    n: int = 60,
    points: Callable[[int], Points] | None = None,
    vis: Callable[[int, str], float] | None = None,
    mirror: bool = False,
) -> PoseSequence:
    frames = []
    for k in range(n):
        pts = points(k) if points else BASE
        kps = []
        for name, (px, py) in pts.items():
            if mirror:  # mirror the image and swap anatomical sides (left-handed twin)
                px = W_IMG - px
                name = name.replace("left", "@").replace("right", "left").replace("@", "right")
            v = vis(k, name) if vis else 0.9
            kps.append(Keypoint(name=name, x=px / W_IMG, y=py / H_IMG, visibility=v, presence=v))
        frames.append(PoseFrame(frame_index=k, timestamp_s=k / FPS, detected=True, keypoints=kps))
    return PoseSequence(
        estimator="t", model="t", image_width=W_IMG, image_height=H_IMG, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip


def _signals(poses: PoseSequence, player: PlayerConfig = RIGHT_2H) -> BodySignals:
    return body_signals(poses, build_timebase(poses), player)


def test_static_geometry_units_and_signs() -> None:
    s = _signals(_poses())
    c = 30  # a centred sample
    assert s.scale.state[c] is ScaleState.OK and s.scale.length_px[c] == pytest.approx(200.0)
    assert s.shoulder_line_deg.value[c] == pytest.approx(0.0, abs=1e-9)
    assert s.hip_line_deg.value[c] == pytest.approx(0.0, abs=1e-9)
    assert s.shoulder_line_deg.value_torso is None  # angles have no torso value
    assert s.shoulder_width.value[c] == pytest.approx(80.0)
    assert s.shoulder_width.value_torso is not None
    assert s.shoulder_width.value_torso[c] == pytest.approx(0.4)
    assert s.hip_width.value_torso is not None and s.hip_width.value_torso[c] == pytest.approx(0.3)
    assert s.hands_distance.value[c] == pytest.approx(np.hypot(180.0, 50.0))
    # wrists relative to the mid-hip point (360, 700), image axes, y up
    lat, ver = s.wrist_lateral, s.wrist_vertical
    assert lat["right"].value[c] == pytest.approx(120.0)
    assert ver["right"].value[c] == pytest.approx(100.0)
    assert lat["left"].value_torso is not None and ver["left"].value_torso is not None
    assert lat["left"].value_torso[c] == pytest.approx(-0.3)
    assert ver["left"].value_torso[c] == pytest.approx(0.25)
    for sig in s.all():
        assert sig.state[c] is SignalState.VALID and sig.state[0] is SignalState.EDGE


def test_angle_convention_tilt_front_view_and_wrap() -> None:
    def tilted(_: int) -> Points:  # right shoulder 40 px higher than the left
        return {**BASE, "right_shoulder": (400.0, 460.0)}

    s = _signals(_poses(points=tilted))
    assert s.shoulder_line_deg.value[30] == pytest.approx(np.degrees(np.arctan2(40, 80)))

    def front(_: int) -> Points:  # seen from the front: sides swap in the image
        return {
            **BASE,
            "left_shoulder": (400.0, 500.0),
            "right_shoulder": (320.0, 500.0),
            "left_hip": (390.0, 700.0),
            "right_hip": (330.0, 700.0),
        }

    s = _signals(_poses(points=front))
    assert s.shoulder_line_deg.value[30] == pytest.approx(180.0)  # never -180
    assert s.hip_line_deg.value[30] == pytest.approx(180.0)


def test_rotation_follows_smoothed_positions_and_width_shrinks() -> None:
    """Shoulders turning towards the camera axis: the right shoulder moves left at constant
    speed, so the projected width shrinks linearly (exact for the quadratic fit)."""

    def turning(k: int) -> Points:
        return {**BASE, "right_shoulder": (400.0 - 2.0 * k, 500.0 - 1.0 * k)}

    s = _signals(_poses(points=turning))
    k = np.arange(60)
    dx, dy_up = 80.0 - 2.0 * k, 1.0 * k
    np.testing.assert_allclose(s.shoulder_width.value, np.hypot(dx, dy_up), atol=1e-6)
    np.testing.assert_allclose(
        s.shoulder_line_deg.value, np.degrees(np.arctan2(dy_up, dx)), atol=1e-6
    )
    assert s.shoulder_line_deg.value[45] > 90  # right shoulder now image-left of the left


def test_mirror_left_handed_player() -> None:
    """Mirrored image + swapped sides = a left-handed twin: the left -> right direction
    (dx, dy) becomes (dx, -dy), so line angles change sign; lateral positions change sign,
    vertical ones and distances stay, roles follow."""

    def tilted(_: int) -> Points:
        return {**BASE, "right_shoulder": (400.0, 470.0), "left_hip": (330.0, 690.0)}

    right = _signals(_poses(points=tilted), RIGHT_2H)
    left = _signals(_poses(points=tilted, mirror=True), LEFT_2H)
    c = 30
    for a, b in (
        (right.shoulder_line_deg, left.shoulder_line_deg),
        (right.hip_line_deg, left.hip_line_deg),
    ):
        assert abs(a.value[c]) > 5
        assert b.value[c] == pytest.approx(-a.value[c])
    assert left.hands_distance.value[c] == pytest.approx(right.hands_distance.value[c])
    assert left.scale.length_px[c] == pytest.approx(right.scale.length_px[c])
    swapped: tuple[tuple[Side, Side], ...] = (("right", "left"), ("left", "right"))
    for r_side, l_side in swapped:
        assert left.wrist_lateral[l_side].value[c] == pytest.approx(
            -right.wrist_lateral[r_side].value[c]
        )
        assert left.wrist_vertical[l_side].value[c] == pytest.approx(
            right.wrist_vertical[r_side].value[c]
        )
    assert RIGHT_2H.dominant_side == "right" and LEFT_2H.dominant_side == "left"
    assert RIGHT_2H.reference_side("forehand") == "right"
    assert RIGHT_2H.reference_side("backhand") == "left"
    assert LEFT_2H.reference_side("forehand") == "left"
    assert LEFT_2H.reference_side("backhand") == "right"
    # the same dominant wrist has the same values in both players
    r_dom = right.wrist_vertical[RIGHT_2H.dominant_side or "right"].value[c]
    l_dom = left.wrist_vertical[LEFT_2H.dominant_side or "left"].value[c]
    assert r_dom == pytest.approx(l_dom)


def test_player_config_roles_and_unknown() -> None:
    one = PlayerConfig(Hand.RIGHT, Backhand.ONE_HANDED)
    assert one.reference_side("backhand") == "right" and one.non_dominant_side == "left"
    unknown_hand = PlayerConfig(Hand.UNKNOWN, Backhand.TWO_HANDED)
    assert unknown_hand.dominant_side is None and unknown_hand.reference_side("forehand") is None
    unknown_bh = PlayerConfig(Hand.RIGHT, Backhand.UNKNOWN)
    assert unknown_bh.reference_side("backhand") is None
    assert unknown_bh.reference_side("forehand") == "right"
    # the configuration names roles only: values do not depend on it
    a, b = _signals(_poses(), RIGHT_2H), _signals(_poses(), unknown_hand)
    for x, y in zip(a.all(), b.all(), strict=True):
        np.testing.assert_array_equal(x.value, y.value)
        assert x.state == y.state


def test_hands_distance_uses_good_samples_only() -> None:
    """LOW right wrist on frames 20-24: hands distance withheld wherever a fit used one,
    while the wrist position keeps a value, marked by low_in_window."""

    def vis(k: int, name: str) -> float:
        return 0.45 if name == "right_wrist" and 20 <= k < 25 else 0.9

    s = _signals(_poses(vis=vis))
    hd, lat = s.hands_distance, s.wrist_lateral["right"]
    assert hd.good_only and not lat.good_only
    for i in range(60):
        if s.positions["right_wrist"].x.low_in_window[i] > 0:
            assert hd.state[i] is SignalState.LOW_EXCLUDED and np.isnan(hd.value[i])
            assert lat.state[i] in (SignalState.VALID, SignalState.EDGE)
            assert np.isfinite(lat.value[i]) and lat.low_in_window[i] > 0
        else:
            assert np.isfinite(hd.value[i])
    assert hd.count(SignalState.LOW_EXCLUDED) == 11  # frames 17-27 (window of 7)
    assert np.isfinite(hd.value[16]) and np.isfinite(hd.value[28])


def test_states_combine_over_the_keypoints_used() -> None:
    """Unreliable hips on frames 20-24 remove the wrist positions there (they use the
    mid-hip point) but not the hands distance or the shoulder line."""

    def vis(k: int, name: str) -> float:
        return 0.2 if name.endswith("_hip") and 20 <= k < 25 else 0.9

    s = _signals(_poses(vis=vis))
    lat = s.wrist_lateral["left"]
    assert all(st is SignalState.UNRELIABLE for st in lat.state[20:25])
    assert np.all(np.isnan(lat.value[20:25]))
    assert lat.state[19] is SignalState.EDGE and lat.state[25] is SignalState.EDGE
    assert s.hip_width.state[22] is SignalState.UNRELIABLE
    assert s.hands_distance.state[22] is SignalState.VALID
    assert s.shoulder_line_deg.state[22] is SignalState.VALID

    def short(k: int, name: str) -> float:  # 5-sample hip run between holes: too short
        return 0.2 if name == "left_hip" and (k in (14, 20)) else 0.9

    s = _signals(_poses(vis=short))
    assert all(st is SignalState.TOO_SHORT for st in s.hip_line_deg.state[15:20])
    assert s.hip_line_deg.state[14] is SignalState.UNRELIABLE


def test_missing_scale_keeps_pixel_values_and_states() -> None:
    def vis(_: int, name: str) -> float:  # hips LOW: usable, but never GOOD for the scale
        return 0.45 if name.endswith("_hip") else 0.9

    s = _signals(_poses(vis=vis))
    assert all(st is ScaleState.INSUFFICIENT for st in s.scale.state)
    w = s.shoulder_width
    assert w.scale_state == s.scale.state
    assert w.value_torso is not None and np.all(np.isnan(w.value_torso))
    assert w.value[30] == pytest.approx(80.0) and w.state[30] is SignalState.VALID
    lat = s.wrist_lateral["right"]
    assert lat.value[30] == pytest.approx(120.0) and lat.state[30] is SignalState.VALID
    assert lat.low_in_window[30] == 14  # 7 LOW samples for each hip
    assert np.isnan(s.wrist_velocity["right"].speed_2d_torso_per_s[30])
