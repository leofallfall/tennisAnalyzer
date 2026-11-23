"""M2.3 I1: wrist speed peaks. Synthetic signals with known maxima; nothing here is a
biomechanical claim and nothing validates the detector on real data."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.signals import build_timebase, keypoint_velocity, torso_scale
from tennis_cv.signals.scale import ScaleState
from tennis_cv.signals.smoothing import KeypointQuality
from tennis_cv.signals.velocity import VelocityState
from tennis_cv.strokes.peaks import (
    PeakCategory,
    Plateau,
    ProminenceSides,
    QualityCounts,
    RunEdge,
    find_wrist_peaks,
    keypoint_present,
    plateau_maxima,
    prominence,
    wrist_peaks,
)

FPS = 30.0


def _a(*v: float) -> Any:
    return np.asarray(v, dtype=np.float64)


# ------------------------------------------------------------------ local maxima


def test_single_maximum_and_strict_neighbours() -> None:
    assert plateau_maxima(_a(0, 1, 3, 1, 0)) == [Plateau(2, 2, 2, RunEdge.NONE)]
    assert plateau_maxima(_a(0, 1, 2, 3, 4)) == [Plateau(4, 4, 4, RunEdge.END)]
    assert plateau_maxima(_a(4, 3, 2, 1, 0)) == [Plateau(0, 0, 0, RunEdge.START)]


def test_plateau_peak_is_the_middle_left_when_even() -> None:
    assert plateau_maxima(_a(0, 2, 2, 2, 0)) == [Plateau(1, 3, 2, RunEdge.NONE)]
    assert plateau_maxima(_a(0, 2, 2, 2, 2, 0)) == [Plateau(1, 4, 2, RunEdge.NONE)]


def test_shoulder_plateau_is_not_a_maximum() -> None:
    assert plateau_maxima(_a(0, 2, 2, 3, 0)) == [Plateau(3, 3, 3, RunEdge.NONE)]


def test_flat_array_and_minimum_give_no_maximum() -> None:
    assert plateau_maxima(_a(1, 1, 1, 1)) == []
    assert plateau_maxima(_a(3, 1, 3)) == [
        Plateau(0, 0, 0, RunEdge.START),
        Plateau(2, 2, 2, RunEdge.END),
    ]
    assert plateau_maxima(_a()) == []


def test_non_finite_values_are_refused() -> None:
    with pytest.raises(ValueError):
        plateau_maxima(_a(0, np.nan, 1))


# ------------------------------------------------------------------ prominence


def test_two_close_peaks_are_kept_and_the_dip_sets_the_smaller_prominence() -> None:
    v = _a(0, 5, 4, 6, 0)
    pk = plateau_maxima(v)
    assert [p.peak for p in pk] == [1, 3]
    small, big = (prominence(v, p) for p in pk)
    assert small.value == 1 and small.left_base == 0 and small.right_base == 2
    assert small.left_censored and not small.right_censored  # 6 stops the right walk
    assert big.value == 6 and big.left_censored and big.right_censored  # highest in the run
    assert big.left_base == 0 and big.right_base == 4


def test_base_ties_take_the_sample_closest_to_the_peak() -> None:
    v = _a(0, 9, 1, 3, 1, 9, 0)
    (p,) = [x for x in plateau_maxima(v) if x.peak == 3]
    pr = prominence(v, p)
    assert pr.left_base == 2 and pr.right_base == 4 and pr.value == 2


def test_equal_height_does_not_stop_the_walk() -> None:
    v = _a(0, 4, 1, 4, 2)
    pk = plateau_maxima(v)
    first = prominence(v, pk[0])
    second = prominence(v, pk[1])
    assert first.value == 3 and first.right_base == 2  # right walk passes 4, reaches end
    assert second.value == 2 and second.left_base == 0  # max(0, 2) = 2


def test_run_edge_peak_is_one_sided() -> None:
    v = _a(5, 3, 1, 2)
    (p, q) = plateau_maxima(v)
    pr = prominence(v, p)
    assert p.edge is RunEdge.START and pr.sides is ProminenceSides.ONE_SIDED
    assert pr.left_base is None and pr.right_base == 2 and pr.value == 4
    assert q.edge is RunEdge.END and prominence(v, q).value == 1


def test_plateau_prominence_walks_from_the_plateau_edges() -> None:
    v = _a(1, 4, 4, 4, 0, 5)
    (p, _) = plateau_maxima(v)
    pr = prominence(v, p)
    assert pr.left_base == 0 and pr.right_base == 4 and pr.value == 3


# ------------------------------------------------------------------ on pose signals


def _poses(
    x_of_k: Any,
    vis_of_k: Any = None,
    n: int = 120,
    missing: Any = (),
    wrist: dict[int, Any] | None = None,
) -> PoseSequence:
    """Torso 200 px; right wrist moves along x as given; left wrist fixed.
    ``wrist[k]``: ``None`` = right wrist absent from frame k's pose, or (x, y, visibility)
    in pixels to put instead (non-finite values and ``None`` visibility allowed)."""
    frames = []
    for k in range(n):
        vis: float | None = 0.9 if vis_of_k is None else float(vis_of_k(k))
        pts: dict[str, Any] = {
            "left_shoulder": (400.0, 500.0, 0.9),
            "right_shoulder": (320.0, 500.0, 0.9),
            "left_hip": (390.0, 700.0, 0.9),
            "right_hip": (330.0, 700.0, 0.9),
            "left_wrist": (450.0, 650.0, 0.9),
            "right_wrist": (float(x_of_k(k)), 600.0, vis),
        }
        if wrist and k in wrist:
            if wrist[k] is None:
                del pts["right_wrist"]
            else:
                pts["right_wrist"] = wrist[k]
        kps = [
            Keypoint(name=nm, x=px / 720, y=py / 1280, visibility=v, presence=v)
            for nm, (px, py, v) in pts.items()
        ]
        det = k not in missing
        frames.append(
            PoseFrame(
                frame_index=k, timestamp_s=k / FPS, detected=det, keypoints=kps if det else []
            )
        )
    return PoseSequence(
        estimator="t", model="t", image_width=720, image_height=1280, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip


def _bell_x(center: float, width: float = 4.0, amp: float = 300.0) -> Any:
    """Position = integral of a Gaussian speed bump: speed peaks at ``center`` (frames)."""
    from math import erf, sqrt

    return lambda k: 100.0 + amp * (1 + erf((k - center) / (width * sqrt(2)))) / 2


def _right(poses: PoseSequence, scale: bool = True) -> Any:
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb) if scale else None
    present = keypoint_present(poses, tb, "right_wrist")
    return wrist_peaks(keypoint_velocity(poses, tb, "right_wrist", sc), "right", present)


def test_known_bell_gives_its_peak_frame_and_torso_values() -> None:
    res = _right(_poses(_bell_x(60.0)))
    big = max(res.peaks, key=lambda p: p.speed_px_per_s)
    assert big.sample.proxy_frame == 60
    assert big.velocity_state is VelocityState.VALID
    assert big.run_edge is RunEdge.NONE and big.category is PeakCategory.INTERNAL
    assert big.scale_state is ScaleState.OK
    assert big.torso_length_px == pytest.approx(200.0)
    assert big.speed_torso_per_s == pytest.approx(big.speed_px_per_s / 200.0)
    assert big.prominence_torso_per_s == pytest.approx(big.prominence_px_per_s / 200.0)
    assert big.prominence_sides is ProminenceSides.TWO_SIDED
    assert big.prominence_is_lower_bound  # highest in its run: walks reach the run edges
    assert res.window_samples == 7 and res.half_window_s == pytest.approx(0.1)
    assert big.fit_counts.good == 7 and big.fit_counts.low_but_usable == 0
    assert big.fit_window[0].proxy_frame == 57 and big.fit_window[1].proxy_frame == 63
    assert big.quality_window.good == 7  # +-0.1 s = +-3 samples at 30 fps


def test_two_close_bells_both_reported() -> None:
    a, b = _bell_x(50.0, 3.0, 300.0), _bell_x(62.0, 3.0, 200.0)
    poses = _poses(lambda k: a(k) + b(k) - 100.0)
    frames = sorted(
        p.sample.proxy_frame for p in _right(poses).peaks if p.prominence_px_per_s > 100
    )
    assert frames == [50, 62]


def test_peaks_are_found_per_run_and_never_across_a_hole() -> None:
    poses = _poses(_bell_x(60.0), missing=range(58, 63))  # hole over the true peak
    res = _right(poses)
    assert res.runs == 2
    assert all(p.sample.proxy_frame not in range(58, 63) for p in res.peaks)
    edges = [p for p in res.peaks if p.sample.proxy_frame in (57, 63)]
    assert {p.run_edge for p in edges} == {RunEdge.END, RunEdge.START}  # halves of the bell
    for p in edges:
        assert p.velocity_state is VelocityState.EDGE
        assert p.prominence_sides is ProminenceSides.ONE_SIDED
        assert p.category is PeakCategory.RUN_EDGE
        assert p.quality_window.missing > 0  # the symmetric window crosses the hole


def test_short_run_gives_no_peak_and_is_counted() -> None:
    poses = _poses(_bell_x(60.0), missing=[*range(50, 55), *range(60, 70)])  # run 55-59: 5 < 7
    res = _right(poses)
    assert res.runs == 3 and res.runs_too_short == 1
    assert all(not 55 <= p.sample.proxy_frame <= 59 for p in res.peaks)


def test_without_scale_the_peak_is_kept_with_pixel_values_only() -> None:
    res = _right(_poses(_bell_x(60.0)), scale=False)
    big = max(res.peaks, key=lambda p: p.speed_px_per_s)
    assert big.sample.proxy_frame == 60
    assert big.scale_state is None and big.speed_torso_per_s is None
    assert big.prominence_torso_per_s is None and big.torso_length_px is None


def test_low_samples_are_counted_in_both_windows() -> None:
    res = _right(_poses(_bell_x(60.0), vis_of_k=lambda k: 0.45 if k in (59, 61) else 0.9))
    big = max(res.peaks, key=lambda p: p.speed_px_per_s)
    assert big.fit_counts.low_but_usable == 2 and big.fit_counts.good == 5
    assert big.quality_window.low_but_usable == 2 and big.quality_window.unreliable == 0


def test_unreliable_samples_cut_the_run_and_are_counted() -> None:
    res = _right(_poses(_bell_x(60.0), vis_of_k=lambda k: 0.2 if k == 63 else 0.9))
    near = [p for p in res.peaks if abs(p.sample.proxy_frame - 60) <= 3]
    assert near and all(p.quality_window.unreliable == 1 for p in near)
    assert res.runs == 2


def test_irregular_interval_cuts_the_run_and_is_counted() -> None:
    poses = _poses(_bell_x(60.0))
    poses = poses.model_copy(
        update={"frames": [f for f in poses.frames if f.frame_index != 62]}
    )  # one frame lost: a long interval between 61 and 63
    tb = build_timebase(poses)
    vel = keypoint_velocity(poses, tb, "right_wrist", torso_scale(poses, tb))
    res = wrist_peaks(vel, "right", keypoint_present(poses, tb, "right_wrist"))
    near = [p for p in res.peaks if 58 <= p.sample.proxy_frame <= 63]
    assert near and all(p.quality_window.irregular_intervals == 1 for p in near)


def test_noise_without_motion_gives_peaks_with_small_prominence() -> None:
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 1.0, 120)
    res = _right(_poses(lambda k: 300.0 + noise[k]))
    assert res.peaks  # every local maximum is reported, none filtered
    assert max(p.prominence_px_per_s for p in res.peaks) < 60  # 1 px noise: tens of px/s


def test_both_wrists_independent_and_deterministic() -> None:
    poses = _poses(_bell_x(60.0))
    tb = build_timebase(poses)
    sc = torso_scale(poses, tb)
    a = find_wrist_peaks(poses, tb, sc)
    b = find_wrist_peaks(poses, tb, sc)
    assert a == b
    assert set(a) == {"left", "right"}
    # fixed wrist: speed is zero up to rounding; those tiny maxima are reported too, and
    # only their prominence tells them apart
    assert all(p.prominence_px_per_s < 1e-6 for p in a["left"].peaks)
    times = [p.sample.t_s for p in a["right"].peaks]
    assert times == sorted(times)


# ------------------------------------------------------------------ quality causes (vs M2.2)


def _window_counts(poses: PoseSequence, frame: int) -> tuple[QualityCounts, Any, Any]:
    """quality_window of the peak at ``frame`` and the M2.2 qualities around it."""
    tb = build_timebase(poses)
    vel = keypoint_velocity(poses, tb, "right_wrist", torso_scale(poses, tb))
    res = wrist_peaks(vel, "right", keypoint_present(poses, tb, "right_wrist"))
    (p,) = [p for p in res.peaks if p.sample.proxy_frame == frame]
    return p.quality_window, vel, p


def _check_against_m22(qw: QualityCounts, vel: Any, p: Any) -> None:
    """GOOD / LOW / UNRELIABLE are exactly classify_quality over the same window, and the
    causes add up to UNRELIABLE."""
    t = vel.positions.raw.timebase.times_s
    idx = np.flatnonzero(np.abs(t - p.sample.t_s) <= p.half_window_s + 1e-9)
    qs = [vel.quality[k] for k in idx]
    assert qw.good == qs.count(KeypointQuality.GOOD)
    assert qw.low_but_usable == qs.count(KeypointQuality.LOW)
    assert qw.unreliable == qs.count(KeypointQuality.UNRELIABLE)
    assert qw.good + qw.low_but_usable + qw.unreliable == idx.size
    assert qw.missing + qw.non_finite + qw.low_visibility == qw.unreliable


def test_frame_without_pose_is_unreliable_missing() -> None:
    qw, vel, p = _window_counts(_poses(_bell_x(60.0), missing=[62]), 61)
    assert (qw.unreliable, qw.missing, qw.non_finite, qw.low_visibility) == (1, 1, 0, 0)
    _check_against_m22(qw, vel, p)


def test_keypoint_absent_from_detected_pose_is_unreliable_missing() -> None:
    qw, vel, p = _window_counts(_poses(_bell_x(60.0), wrist={62: None}), 61)
    assert (qw.unreliable, qw.missing, qw.non_finite, qw.low_visibility) == (1, 1, 0, 0)
    _check_against_m22(qw, vel, p)


@pytest.mark.parametrize("xy", [(np.nan, 600.0), (np.inf, 600.0), (400.0, np.nan)])
def test_present_keypoint_with_non_finite_coordinates_is_unreliable_non_finite(
    xy: tuple[float, float],
) -> None:
    qw, vel, p = _window_counts(_poses(_bell_x(60.0), wrist={62: (*xy, 0.9)}), 61)
    assert vel.quality[62] is KeypointQuality.UNRELIABLE  # M2.2 rule, unchanged
    assert (qw.unreliable, qw.missing, qw.non_finite, qw.low_visibility) == (1, 0, 1, 0)
    _check_against_m22(qw, vel, p)


@pytest.mark.parametrize("vis", [0.2, 0.399, None])
def test_valid_coordinates_with_insufficient_quality_are_unreliable_low_visibility(
    vis: float | None,
) -> None:
    x = _bell_x(60.0)
    qw, vel, p = _window_counts(_poses(x, wrist={62: (x(62), 600.0, vis)}), 61)
    assert vel.quality[62] is KeypointQuality.UNRELIABLE
    assert (qw.unreliable, qw.missing, qw.non_finite, qw.low_visibility) == (1, 0, 0, 1)
    _check_against_m22(qw, vel, p)


def test_usable_thresholds_are_the_m22_ones() -> None:
    """0.4 is LOW and 0.5 is GOOD (M2.2 boundaries, inclusive): no cause is counted."""
    x = _bell_x(60.0)
    qw, vel, p = _window_counts(
        _poses(x, wrist={59: (x(59), 600.0, 0.4), 61: (x(61), 600.0, 0.5)}), 60
    )
    assert vel.quality[59] is KeypointQuality.LOW and vel.quality[61] is KeypointQuality.GOOD
    assert (qw.good, qw.low_but_usable, qw.unreliable) == (6, 1, 0)
    _check_against_m22(qw, vel, p)


def test_all_causes_together_add_up_to_m22_unreliable() -> None:
    x = _bell_x(60.0)
    poses = _poses(
        x, missing=[57], wrist={58: None, 62: (np.nan, 600.0, 0.9), 63: (x(63), 600.0, 0.1)}
    )
    tb = build_timebase(poses)
    vel = keypoint_velocity(poses, tb, "right_wrist", torso_scale(poses, tb))
    present = keypoint_present(poses, tb, "right_wrist")
    assert present[57:64].tolist() == [False, False, True, True, True, True, True]
    res = wrist_peaks(vel, "right", present)
    for pk in res.peaks:
        _check_against_m22(pk.quality_window, vel, pk)


def test_present_mask_must_match_the_time_base() -> None:
    poses = _poses(_bell_x(60.0))
    tb = build_timebase(poses)
    vel = keypoint_velocity(poses, tb, "right_wrist")
    with pytest.raises(ValueError):
        wrist_peaks(vel, "right", np.ones(3, dtype=bool))


# ------------------------------------------------------------------ real (iPhone) times


def test_iphone_alternating_intervals_use_real_sample_times() -> None:
    """Source intervals alternate 31.67 / 35.00 ms (M2.2). Positions are defined on the
    real times; the peak is found at the sample whose real time is the true peak time, its
    time is the real one (not frame / fps) and its speed is the slope of the M2.2 quadratic
    fit on the real times of its 7 samples. The timebase parameters are the defaults."""
    from fractions import Fraction
    from math import erf, sqrt

    from tennis_cv.video.timemap import build_timemap

    t, src = 0, [Fraction(0)]
    for d in [114, 126] * 60:  # 1/3600 s units: 31.67 and 35.00 ms
        t += d
        src.append(Fraction(t, 3600))
    tm = build_timemap(src, FPS)
    n = tm.summary.proxy_frame_count
    real = [float(src[tm.proxy_to_source[k]]) for k in range(n)]
    k0 = 61  # odd sample: real time 2.031667 s, proxy time 2.033333 s
    t0, width, amp = real[k0], 0.25, 300.0

    def x(k: int) -> float:
        return 100.0 + amp * (1 + erf((real[k] - t0) / (width * sqrt(2)))) / 2

    poses = _poses(x, n=n)
    tb = build_timebase(poses, tm)
    assert tb.basis.value == "source" and tb.irregular_count() == 0 and tb.segment_count == 1
    gaps = np.round(np.diff(tb.times_s) * 1000, 2)
    assert set(gaps.tolist()) == {31.67, 35.0}  # the alternation reaches the time base
    vel = keypoint_velocity(poses, tb, "right_wrist", torso_scale(poses, tb))
    res = wrist_peaks(vel, "right", keypoint_present(poses, tb, "right_wrist"))
    big = max(res.peaks, key=lambda p: p.speed_px_per_s)
    i = big.sample.index
    assert big.sample.proxy_frame == k0
    assert big.sample.t_s == pytest.approx(t0, abs=1e-6)  # timemap stores microseconds
    assert abs(big.sample.t_s - k0 / FPS) > 1e-3  # not the uniform frame time
    # speed = slope of the quadratic fit on the REAL times of the 7 window samples
    w = slice(big.fit_window[0].index, big.fit_window[1].index + 1)
    tt = tb.times_s[w] - tb.times_s[i]
    cx = np.polyfit(tt, vel.positions.raw.x_px[w], 2)
    assert big.speed_px_per_s == pytest.approx(abs(cx[1]), rel=1e-9)
    # the same fit on uniform times gives a different slope: real times were used
    cu = np.polyfit((np.arange(7) - 3) / FPS, vel.positions.raw.x_px[w], 2)
    assert abs(abs(cu[1]) - big.speed_px_per_s) > 1e-3
    # the fit window is 7 samples; the quality window is +-0.1 s of REAL time: here the
    # third sample on one side is 101.67 ms away (35 + 31.67 + 35) and falls outside
    offs = (tb.times_s - tb.times_s[i]) * 1000
    expected_ms = [-98.33, -66.67, -31.67, 0.0, 35.0, 66.67, 101.67]
    assert np.round(offs[i - 3 : i + 4], 2).tolist() == expected_ms
    assert big.fit_counts.good == 7 and big.quality_window.good == 6
