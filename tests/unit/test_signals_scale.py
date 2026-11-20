"""M2.2 #4: robust torso scale. Synthetic poses with a known torso length."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.signals import (
    MapStatus,
    ScaleParams,
    ScaleState,
    TimeBasis,
    build_timebase,
    stabilise,
    torso_scale,
)
from tennis_cv.signals.timebase import TimeBase

FPS = 30.0
W_PX, H_PX = 720, 1280


def _poses(
    n: int,
    torso_px: Callable[[int], float],
    vis: Callable[[int, str], float] = lambda k, name: 0.9,
    noise_px: float = 0.0,
    seed: int = 0,
) -> PoseSequence:
    """Shoulders 80 px apart at y0, hips 60 px apart torso_px(k) lower."""
    rng = np.random.default_rng(seed)
    frames = []
    for k in range(n):
        y0, cx = 500.0, 360.0
        pts = {
            "left_shoulder": (cx + 40, y0),
            "right_shoulder": (cx - 40, y0),
            "left_hip": (cx + 30, y0 + torso_px(k)),
            "right_hip": (cx - 30, y0 + torso_px(k)),
        }
        kps = []
        for name, (x, y) in pts.items():
            x, y = x + rng.normal(0, noise_px), y + rng.normal(0, noise_px)
            v = vis(k, name)
            kps.append(Keypoint(name=name, x=x / W_PX, y=y / H_PX, visibility=v, presence=v))
        frames.append(PoseFrame(frame_index=k, timestamp_s=k / FPS, detected=True, keypoints=kps))
    return PoseSequence(
        estimator="t", model="t", image_width=W_PX, image_height=H_PX, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip


def test_noise_and_outliers_are_removed() -> None:
    rng = np.random.default_rng(3)
    wrong = set(rng.choice(150, 15, replace=False).tolist())  # 10 % wrong hips
    poses = _poses(150, lambda k: 400.0 * (0.6 if k in wrong else 1.0), noise_px=3.0)
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    ok = np.array([s is ScaleState.OK for s in sc.state])
    assert ok.all()
    assert np.max(np.abs(sc.length_px - 400) / 400) < 0.02
    assert np.max(np.abs(sc.measured_px - 400) / 400) > 0.3  # raw measurements kept
    assert np.nanmedian(sc.relative_spread) < 0.02


def test_real_distance_change_is_followed() -> None:
    """Player walking towards the camera: torso 200 -> 300 px in 3 s. The median of a
    linear change is its central value, so the scale follows it without lag."""
    poses = _poses(91, lambda k: 200.0 + 100.0 * k / 90)
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    inner = slice(15, 76)  # full windows
    np.testing.assert_allclose(sc.length_px[inner], sc.measured_px[inner], rtol=1e-6)
    assert sc.length_px[5] < sc.length_px[45] < sc.length_px[85]


def test_missing_torso_gives_no_scale_and_no_normalised_value() -> None:
    # hips not visible from 1.0 to 3.0 s
    poses = _poses(
        120,
        lambda k: 400.0,
        vis=lambda k, name: 0.1 if "hip" in name and 30 <= k < 90 else 0.9,
    )
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    assert np.all(np.isnan(sc.measured_px[30:90]))
    assert all(s is ScaleState.INSUFFICIENT for s in sc.state[38:82])
    assert all(s is ScaleState.OK for s in sc.state[:22])
    px = np.full(120, 80.0)
    torso_units = sc.to_torso_units(px)
    assert np.all(np.isnan(torso_units[38:82]))
    assert torso_units[0] == pytest.approx(0.2)
    np.testing.assert_array_equal(px, 80.0)  # pixels untouched


def test_only_good_samples_measure_the_torso() -> None:
    # LOW hips (0.45) carry a wrong length: they must not enter the scale
    poses = _poses(
        60,
        lambda k: 250.0 if k % 2 else 400.0,
        vis=lambda k, name: 0.45 if "hip" in name and k % 2 else 0.9,
    )
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    assert np.all(np.isnan(sc.measured_px[1::2]))
    np.testing.assert_allclose(sc.length_px[np.isfinite(sc.length_px)], 400.0)


def test_window_does_not_cross_segments() -> None:
    t = np.arange(60) / FPS
    seg = np.r_[np.zeros(30, np.int64), np.ones(30, np.int64)]
    tb = TimeBase(
        basis=TimeBasis.PROXY,
        map_status=MapStatus.MISSING,
        map_problems=(), nominal_interval_s=1 / FPS, proxy_frames=np.arange(60),
        source_frames=None, times_s=t, interval_s=np.r_[np.nan, np.diff(t)],
        interval_status=(), segment=seg, repeats_removed=0,
    )  # fmt: skip
    measured = np.r_[np.full(30, 300.0), np.full(30, 500.0)]
    sc = stabilise(tb, measured)
    np.testing.assert_array_equal(sc.length_px[:30], 300.0)
    np.testing.assert_array_equal(sc.length_px[30:], 500.0)


def test_params_validation_and_shape_check() -> None:
    with pytest.raises(ValueError):
        ScaleParams(window_s=0)
    with pytest.raises(ValueError):
        ScaleParams(min_fraction=0)
    poses = _poses(10, lambda k: 300.0)
    sc = torso_scale(poses, build_timebase(poses))
    with pytest.raises(ValueError):
        sc.to_torso_units(np.zeros(3))
