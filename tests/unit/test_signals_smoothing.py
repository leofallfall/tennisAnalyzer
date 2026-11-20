"""M2.2 #2: local polynomial smoothing on real times, within runs of reliable samples.
All signals are synthetic with a known truth; nothing here is a biomechanical claim."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.signals import (
    KeypointQuality,
    SmoothedSignal,
    SmoothingParams,
    SmoothState,
    build_timebase,
    classify_quality,
    extract_keypoint,
    smooth_keypoint,
    smooth_series,
)
from tennis_cv.video.timemap import build_timemap

FPS = 30.0
DT = 1 / FPS
W, P = 7, 2  # window and order used with smooth_series directly
F = npt.NDArray[Any]


def _smooth(
    t: F, v: F, segment: list[int] | None = None, reliable: list[bool] | None = None
) -> SmoothedSignal:
    seg = np.zeros(t.size, dtype=np.int64) if segment is None else np.asarray(segment)
    rel = np.ones(t.size, dtype=bool) if reliable is None else np.asarray(reliable)
    return smooth_series(
        np.asarray(t, dtype=np.float64), np.asarray(v, dtype=np.float64), seg, rel, W, P, DT
    )


def test_window_from_duration_and_validation() -> None:
    p = SmoothingParams()
    assert p.window_samples(1 / 30) == 7 and p.window_samples(1 / 24) == 5
    assert SmoothingParams(window_s=0.01).window_samples(1 / 30) == 5  # >= polyorder + 2, odd
    with pytest.raises(ValueError):
        SmoothingParams(window_s=float("nan"))
    with pytest.raises(ValueError):
        smooth_series(np.arange(9.0), np.zeros(9), np.zeros(9, np.int64), np.ones(9, bool), 6, 2, 1)


def test_1_noise_is_attenuated() -> None:
    rng = np.random.default_rng(0)
    t = np.arange(120) * DT
    truth = 100 * np.sin(2 * np.pi * 0.8 * t)
    noisy = truth + rng.normal(0, 3, t.size)
    s = _smooth(t, noisy)
    centre = np.array([x is SmoothState.SMOOTHED for x in s.state])
    err_raw = np.sqrt(np.mean((noisy - truth) ** 2))
    err_c = np.sqrt(np.mean((s.smoothed[centre] - truth[centre]) ** 2))
    err_all = np.sqrt(np.mean((s.smoothed - truth) ** 2))
    assert err_c < 0.7 * err_raw  # theory for 7-point quadratic fit: x0.58
    assert err_all < 0.8 * err_raw
    np.testing.assert_array_equal(s.raw, noisy)  # raw kept separately and unchanged


def test_2_shape_and_main_peak_are_kept() -> None:
    """A 0.1 s-sigma bump (a fast but not instantaneous excursion) keeps its peak frame
    and >= 95 % of its height; a slower one is essentially untouched."""
    t = np.arange(60) * DT
    for sigma, min_ratio in ((0.1, 0.95), (0.2, 0.99)):
        truth = 100 * np.exp(-0.5 * ((t - 1.0) / sigma) ** 2)
        s = _smooth(t, truth)
        assert int(np.nanargmax(s.smoothed)) == int(np.argmax(truth))
        assert np.nanmax(s.smoothed) >= min_ratio * truth.max()
        assert np.nanmax(s.smoothed) <= truth.max() + 1e-9  # no overshoot
    rng = np.random.default_rng(1)
    truth = 100 * np.exp(-0.5 * ((t - 1.0) / 0.1) ** 2)
    s = _smooth(t, truth + rng.normal(0, 3, t.size))
    assert abs(int(np.nanargmax(s.smoothed)) - int(np.argmax(truth))) <= 1


def test_3_segment_edges_have_no_artificial_peaks() -> None:
    t = np.arange(30) * DT
    # polynomials up to the fit order are reproduced exactly, edges included
    quad = 5 + 40 * t - 300 * t**2
    s = _smooth(t, quad)
    np.testing.assert_allclose(s.smoothed, quad, atol=1e-9)
    assert s.state[:3] == (SmoothState.EDGE,) * 3 and s.state[-3:] == (SmoothState.EDGE,) * 3
    assert s.count(SmoothState.SMOOTHED) == 30 - 6
    # noisy ramp: edge values stay within the data range of their window (no overshoot
    # spike) and their error stays bounded
    rng = np.random.default_rng(2)
    worst = 0.0
    for _ in range(200):
        ramp = 200 * t + rng.normal(0, 3, t.size)
        s = _smooth(t, ramp)
        edge = np.array([x is SmoothState.EDGE for x in s.state])
        worst = max(worst, float(np.max(np.abs(s.smoothed[edge] - 200 * t[edge]))))
    assert worst < 4 * 3  # < 4 sigma of the raw noise over 200 trials x 6 edge samples


def test_4_no_contamination_between_segments() -> None:
    t = np.arange(40) * DT
    v = np.sin(t * 3) * 50
    seg = [0] * 20 + [1] * 20
    a = _smooth(t, v, seg)
    v2 = v.copy()
    v2[20:] += 1e4  # a huge jump in the second segment only
    b = _smooth(t, v2, seg)
    np.testing.assert_array_equal(a.smoothed[:20], b.smoothed[:20])
    np.testing.assert_allclose(b.smoothed[20:] - a.smoothed[20:], 1e4, atol=1e-6)
    assert a.state[17:23] == (SmoothState.EDGE,) * 6  # both sides of the boundary
    assert a.run.tolist() == [0] * 20 + [1] * 20


def test_5_too_short_segments_get_no_value() -> None:
    t = np.arange(13) * DT
    v = np.linspace(0, 10, 13)
    s = _smooth(t, v, [0] * 6 + [1] * 7)
    assert s.state[:6] == (SmoothState.TOO_SHORT,) * 6
    assert np.all(np.isnan(s.smoothed[:6]))
    assert np.all(np.isfinite(s.smoothed[6:]))  # exactly one window long: smoothed


def test_6_missing_or_unreliable_samples_are_not_filled() -> None:
    t = np.arange(30) * DT
    v = 10 * np.cos(t * 4)
    rel = [True] * 30
    rel[15] = False
    a = _smooth(t, v, reliable=rel)
    assert a.state[15] is SmoothState.UNRELIABLE and np.isnan(a.smoothed[15])
    assert a.run[15] == -1 and a.run[14] != a.run[16]  # no fit bridges the hole
    assert a.state[12:15] == (SmoothState.EDGE,) * 3 and a.state[16:19] == (SmoothState.EDGE,) * 3
    v2 = v.copy()
    v2[15] = 1e6  # the unreliable raw value has no influence anywhere
    np.testing.assert_array_equal(_smooth(t, v2, reliable=rel).smoothed, a.smoothed)
    # a NaN value marked reliable is treated as unreliable
    v3 = v.copy()
    v3[15] = np.nan
    np.testing.assert_array_equal(_smooth(t, v3).smoothed, a.smoothed)
    # runs too short because of holes get no value
    rel4 = [i % 5 != 0 for i in range(30)]  # runs of 4 samples
    b = _smooth(t, v, reliable=rel4)
    assert not np.any(np.isfinite(b.smoothed))


def test_7_non_uniform_timestamps_are_used() -> None:
    """iPhone-like 31.67 / 35.00 ms intervals: a quadratic in real time is reproduced
    exactly; fitting on a uniform index instead would be biased."""
    steps = np.array([31.666667, 35.0] * 15) / 1000
    t = np.concatenate([[0.0], np.cumsum(steps)])
    quad = 3000 * (t - 0.5) ** 2
    s = _smooth(t, quad)
    np.testing.assert_allclose(s.smoothed, quad, atol=1e-8)
    uniform = _smooth(np.arange(t.size) * DT, quad)
    assert np.nanmax(np.abs(uniform.smoothed - quad)) > 0.1


def _kp(name: str, x: float, y: float, vis: float) -> Keypoint:
    return Keypoint(name=name, x=x, y=y, visibility=vis, presence=vis)


def test_keypoint_on_real_time_base() -> None:
    """End to end on a pose sequence: repeat removed by the time base, undetected and
    low-visibility frames unreliable, raw positions kept as written."""
    steps = [120] * 12 + [240] + [120] * 12  # one real repeat in the proxy
    src = [Fraction(0)]
    for d in steps:
        src.append(src[-1] + Fraction(d, 3600))
    tm = build_timemap(src, FPS)
    n = tm.summary.proxy_frame_count
    frames = []
    for k in range(n):
        vis = 0.3 if k == 20 else 0.9
        frames.append(
            PoseFrame(
                frame_index=k, timestamp_s=k / FPS, detected=k != 5,
                keypoints=[] if k == 5 else [_kp("right_wrist", 0.1 + 0.01 * k, 0.5, vis)],
            )
        )  # fmt: skip
    poses = PoseSequence(
        estimator="t", model="t", image_width=720, image_height=1280, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip
    tb = build_timebase(poses, tm)
    assert tb.repeats_removed == 1 and tb.segment_count == 2
    raw = extract_keypoint(poses, tb, "right_wrist")
    sm = smooth_keypoint(raw)
    k5 = int(np.flatnonzero(tb.proxy_frames == 5)[0])
    k20 = int(np.flatnonzero(tb.proxy_frames == 20)[0])
    assert np.isnan(raw.x_px[k5]) and not raw.reliable[k5]
    assert raw.x_px[k20] == pytest.approx((0.1 + 0.2) * 720) and not raw.reliable[k20]
    assert sm.x.state[k5] is SmoothState.UNRELIABLE and sm.x.state[k20] is SmoothState.UNRELIABLE
    np.testing.assert_array_equal(sm.x.raw, raw.x_px)
    assert sm.x.window_samples == 7
    # every value is either smoothed inside one segment/run or explicitly absent
    for i, st in enumerate(sm.x.state):
        assert np.isfinite(sm.x.smoothed[i]) == (st in (SmoothState.SMOOTHED, SmoothState.EDGE))
    runs = sm.x.run[sm.x.run >= 0]
    for r in np.unique(runs):
        assert np.unique(tb.segment[sm.x.run == r]).size == 1


# --- intervention 3: two quality levels ---

G, LO, UN = KeypointQuality.GOOD, KeypointQuality.LOW, KeypointQuality.UNRELIABLE


def test_quality_levels() -> None:
    x = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, np.nan])
    vis = np.array([0.9, 0.5, 0.49, 0.3, 0.1, np.nan, 0.9])
    two = SmoothingParams(usable_visibility=0.3)
    assert classify_quality(x, x, vis, two) == (G, G, LO, LO, UN, UN, UN)
    # provisional default: usable == good, so no sample is LOW
    assert classify_quality(x, x, vis) == (G, G, UN, UN, UN, UN, UN)
    with pytest.raises(ValueError):
        SmoothingParams(usable_visibility=0.6)


def test_low_samples_join_runs_and_are_counted_unreliable_break() -> None:
    t = np.arange(30) * DT
    v = 20 * np.sin(t * 3)
    low = np.zeros(30, dtype=bool)
    low[10:13] = True
    rel = np.ones(30, dtype=bool)
    rel[20] = False
    s = smooth_series(t, v, np.zeros(30, np.int64), rel, W, P, DT, low)
    assert s.run[:20].tolist() == [0] * 20 and s.run[20] == -1 and s.run[21] == 1
    assert np.all(np.isfinite(s.smoothed[10:13]))  # LOW samples get a value
    assert s.state[11] is SmoothState.SMOOTHED  # and do not create edges
    assert s.low_in_window[11] == 3 and s.low_in_window[5] == 0
    assert s.low_in_window[20] == 0 and s.state[20] is SmoothState.UNRELIABLE
    # same values as treating them as GOOD: LOW changes the label, not the fit
    s_good = smooth_series(t, v, np.zeros(30, np.int64), rel, W, P, DT)
    np.testing.assert_array_equal(s.smoothed, s_good.smoothed)


def test_low_quality_end_to_end_keeps_raw_and_quality() -> None:
    n = 30
    frames = [
        PoseFrame(
            frame_index=k, timestamp_s=k / FPS, detected=True,
            keypoints=[_kp("left_wrist", 0.3 + 0.002 * k, 0.4, 0.4 if 12 <= k < 15 else 0.9)],
        )
        for k in range(n)
    ]  # fmt: skip
    poses = PoseSequence(
        estimator="t", model="t", image_width=720, image_height=1280, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip
    tb = build_timebase(poses)
    two = SmoothingParams(usable_visibility=0.3)
    raw = extract_keypoint(poses, tb, "left_wrist", two)
    assert raw.quality[12:15] == (LO,) * 3 and raw.count(G) == n - 3
    sm = smooth_keypoint(raw, two)
    assert sm.x.run.max() == 0 and np.all(np.isfinite(sm.x.smoothed[12:15]))
    assert sm.x.low_in_window[13] == 3
    # default (usable = good = 0.5): the same samples are unreliable and split the run
    raw_d = extract_keypoint(poses, tb, "left_wrist")
    sm_d = smooth_keypoint(raw_d)
    assert raw_d.quality[12:15] == (UN,) * 3 and sm_d.x.run.max() == 1
    assert np.all(np.isnan(sm_d.x.smoothed[12:15]))
    # raw coordinates and visibility are kept whatever the quality
    np.testing.assert_array_equal(raw.x_px, raw_d.x_px)
    np.testing.assert_array_equal(raw_d.visibility[12:15], [0.4, 0.4, 0.4])
