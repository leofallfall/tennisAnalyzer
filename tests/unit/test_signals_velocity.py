"""M2.2 #5: apparent 2D speed = first derivative of the same local polynomial fit.
Synthetic signals with a known derivative; nothing here is a biomechanical claim."""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.signals import (
    KeypointQuality,
    ScaleState,
    SmoothedSignal,
    SmoothState,
    VelocityState,
    build_timebase,
    keypoint_velocity,
    smooth_series,
    torso_scale,
)

FPS = 30.0
DT = 1 / FPS
W, P = 7, 2


def _fit(
    t: npt.NDArray[Any], v: npt.NDArray[Any], seg: Any = None, rel: Any = None
) -> SmoothedSignal:
    n = t.size
    seg = np.zeros(n, np.int64) if seg is None else np.asarray(seg, np.int64)
    rel = np.ones(n, bool) if rel is None else np.asarray(rel, bool)
    return smooth_series(np.asarray(t, np.float64), np.asarray(v, np.float64), seg, rel, W, P, DT)


def test_constant_velocity_is_exact_everywhere() -> None:
    t = np.arange(40) * DT
    s = _fit(t, 12.0 + 250.0 * t)  # 250 px/s
    np.testing.assert_allclose(s.derivative, 250.0, atol=1e-8)  # edges included


def test_constant_acceleration_of_position_gives_linear_velocity() -> None:
    t = np.arange(40) * DT
    s = _fit(t, 5.0 + 100.0 * t + 0.5 * 900.0 * t**2)  # a = 900 px/s^2
    np.testing.assert_allclose(s.derivative, 100.0 + 900.0 * t, atol=1e-7)


def test_derivative_is_the_slope_of_the_same_fit() -> None:
    rng = np.random.default_rng(4)
    t = np.cumsum(rng.uniform(0.03, 0.037, 40))
    v = rng.normal(0, 10, 40)
    s = _fit(t, v)
    for i in (0, 2, 3, 20, 37, 39):  # edge and centred samples
        start = min(max(i - 3, 0), 40 - W)
        c = np.polyfit(t[start : start + W] - t[i], v[start : start + W], P)
        assert s.smoothed[i] == pytest.approx(c[-1], abs=1e-9)
        assert s.derivative[i] == pytest.approx(c[-2], abs=1e-7)


def test_noise_on_velocity_and_edge_amplification() -> None:
    """White noise of 1 px on the position: centred slopes have a known, moderate noise;
    edge slopes are much noisier, which is why they are marked EDGE."""
    rng = np.random.default_rng(5)
    t = np.arange(30) * DT
    d = np.array([_fit(t, rng.normal(0, 1, 30)).derivative for _ in range(4000)])
    std = d.std(axis=0)
    # centred 7-point slope: sigma / (dt * sqrt(28)) = 5.67 px/s for sigma = 1 px
    assert std[15] == pytest.approx(1 / (DT * np.sqrt(28)), rel=0.05)
    assert std[0] > 3 * std[15]  # first sample of a run: off-centre slope (x3.6)
    finite_diff = np.diff(rng.normal(0, 1, (4000, 2)), axis=1)[:, 0] / DT
    assert std[15] < 0.2 * finite_diff.std()  # far less noisy than a frame difference


def test_non_uniform_timestamps() -> None:
    steps = np.array([31.666667, 35.0] * 15) / 1000
    t = np.concatenate([[0.0], np.cumsum(steps)])
    s = _fit(t, 2000 * (t - 0.5) ** 2)
    np.testing.assert_allclose(s.derivative, 4000 * (t - 0.5), atol=1e-6)
    uniform = _fit(np.arange(t.size) * DT, 2000 * (t - 0.5) ** 2)
    assert np.nanmax(np.abs(uniform.derivative - 4000 * (t - 0.5))) > 1.0


def test_holes_and_segment_boundaries_are_not_crossed() -> None:
    t = np.arange(40) * DT
    v = 300.0 * t
    v[20:] += 500.0  # a jump at a segment boundary: no speed spike may appear
    s = _fit(t, v, seg=[0] * 20 + [1] * 20)
    np.testing.assert_allclose(s.derivative, 300.0, atol=1e-8)
    rel = np.ones(40, bool)
    rel[10] = False
    v2 = 300.0 * t
    v2[10] = 1e6  # unreliable sample: no velocity there, no influence elsewhere
    s2 = _fit(t, v2, rel=rel)
    assert np.isnan(s2.derivative[10]) and s2.state[10] is SmoothState.UNRELIABLE
    np.testing.assert_allclose(np.delete(s2.derivative, 10), 300.0, atol=1e-8)
    s3 = _fit(t[:6], v[:6])  # run shorter than the window
    assert np.all(np.isnan(s3.derivative))


def _poses(
    n: int, hip_vis: float = 0.9, wrist_vis: float = 0.9, wrist_seq: Any = None
) -> PoseSequence:
    frames = []
    for k in range(n):
        x = 100.0 + 6.0 * k  # 6 px per frame = 180 px/s at 30 fps
        pts = {
            "left_shoulder": (400.0, 500.0, 0.9),
            "right_shoulder": (320.0, 500.0, 0.9),
            "left_hip": (390.0, 700.0, hip_vis),
            "right_hip": (330.0, 700.0, hip_vis),
            "right_wrist": (x, 600.0, wrist_vis if 20 <= k < 25 else 0.9),
        }
        if wrist_seq is not None:
            pts["right_wrist"] = (x, 600.0, float(wrist_seq[k]))
        kps = [
            Keypoint(name=nm, x=px / 720, y=py / 1280, visibility=v, presence=v)
            for nm, (px, py, v) in pts.items()
        ]
        frames.append(PoseFrame(frame_index=k, timestamp_s=k / FPS, detected=True, keypoints=kps))
    return PoseSequence(
        estimator="t", model="t", image_width=720, image_height=1280, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip


def test_units_states_and_torso_normalisation() -> None:
    poses = _poses(60)
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    vel = keypoint_velocity(poses, tb, "right_wrist", sc)
    np.testing.assert_allclose(vel.vx_px_per_s, 180.0, atol=1e-6)
    np.testing.assert_allclose(vel.vy_px_per_s, 0.0, atol=1e-6)
    np.testing.assert_allclose(vel.speed_2d_px_per_s, 180.0, atol=1e-6)
    np.testing.assert_allclose(vel.speed_2d_torso_per_s, 180.0 / 200.0, atol=1e-6)
    assert vel.state[:3] == (VelocityState.EDGE,) * 3
    assert vel.state[3] is VelocityState.VALID


def test_low_samples_get_marked_velocities_unreliable_get_none() -> None:
    poses = _poses(60, wrist_vis=0.45)  # frames 20-24 LOW
    tb = build_timebase(poses)
    vel = keypoint_velocity(poses, tb, "right_wrist")
    assert vel.quality[20:25] == (KeypointQuality.LOW,) * 5
    assert np.all(np.isfinite(vel.speed_2d_px_per_s[20:25]))
    assert vel.low_in_window[22] == 5 and vel.low_in_window[40] == 0
    poses = _poses(60, wrist_vis=0.2)  # frames 20-24 UNRELIABLE
    vel = keypoint_velocity(poses, build_timebase(poses), "right_wrist")
    assert all(s is VelocityState.UNRELIABLE for s in vel.state[20:25])
    assert np.all(np.isnan(vel.speed_2d_px_per_s[20:25]))
    assert vel.state[19] is VelocityState.EDGE and vel.state[25] is VelocityState.EDGE


def test_missing_scale_keeps_pixel_speed() -> None:
    poses = _poses(60, hip_vis=0.2)  # hips never GOOD: no torso scale
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    assert all(s is ScaleState.INSUFFICIENT for s in sc.state)
    vel = keypoint_velocity(poses, tb, "right_wrist", sc)
    np.testing.assert_allclose(vel.speed_2d_px_per_s, 180.0, atol=1e-6)
    assert np.all(np.isnan(vel.speed_2d_torso_per_s))
    no_scale = keypoint_velocity(poses, tb, "right_wrist")
    assert no_scale.scale_state is None and np.all(np.isnan(no_scale.speed_2d_torso_per_s))
    # the scale never changes the velocity state or the pixel values
    assert vel.state == no_scale.state
    np.testing.assert_array_equal(vel.speed_2d_px_per_s, no_scale.speed_2d_px_per_s)


def test_low_in_window_can_be_rebuilt_from_exposed_fields() -> None:
    """The window of each estimate follows from run, window_samples and the edge rule, so
    the quality of every sample used can be recovered (here: the LOW count)."""
    rng = np.random.default_rng(3)
    vis = rng.choice([0.9, 0.45, 0.2], size=60, p=[0.6, 0.3, 0.1])
    poses = _poses(60, wrist_seq=vis)
    vel = keypoint_velocity(poses, build_timebase(poses), "right_wrist")
    assert set(vel.state) == set(VelocityState)  # all four states occur
    sx = vel.positions.x
    w, half = sx.window_samples, sx.window_samples // 2
    for i, st in enumerate(vel.state):
        if st not in (VelocityState.VALID, VelocityState.EDGE):
            assert vel.low_in_window[i] == 0
            continue
        members = np.flatnonzero(sx.run == sx.run[i])
        r0, r1 = int(members[0]), int(members[-1]) + 1
        start = min(max(i - half, r0), r1 - w)
        assert (start == i - half) == (st is VelocityState.VALID)
        used = vel.quality[start : start + w]
        assert KeypointQuality.UNRELIABLE not in used
        assert vel.low_in_window[i] == used.count(KeypointQuality.LOW)
