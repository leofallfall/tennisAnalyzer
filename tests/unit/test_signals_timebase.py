"""M2.2 #1: time base of pose samples (source vs proxy times, repeats, irregular
intervals, missing or inconsistent maps). Synthetic maps built with the real
``build_timemap``; no video is decoded here."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from tennis_cv.pose.models import PoseFrame, PoseSequence
from tennis_cv.signals import (
    IntervalStatus,
    MapStatus,
    TimeBaseParams,
    TimeBasis,
    build_timebase,
)
from tennis_cv.video.timemap import TimeMap, build_timemap

FPS = 30.0
FST, R, L, S = (
    IntervalStatus.FIRST,
    IntervalStatus.REGULAR,
    IntervalStatus.LONG,
    IntervalStatus.SHORT,
)


def _poses(n: int, fps: float = FPS, stride: int = 1, detected: bool = True) -> PoseSequence:
    frames = [
        PoseFrame(frame_index=k, timestamp_s=k / fps, detected=detected)
        for k in range(0, n, stride)
    ]
    return PoseSequence(
        estimator="test", model="test", image_width=10, image_height=10,
        fps=fps, frame_stride=stride, frames=frames,
    )  # fmt: skip


def _src(steps_3600: list[int], start: int = 0) -> list[Fraction]:
    """Source times from integer steps in 1/3600 s (as the timemap tests do)."""
    t, out = start, [Fraction(start, 3600)]
    for d in steps_3600:
        t += d
        out.append(Fraction(t, 3600))
    return out


def test_regular_map_uses_source_times() -> None:
    src = _src([120] * 9, start=7)  # exactly 30 fps, non-zero start pts
    tb = build_timebase(_poses(10), build_timemap(src, FPS, 10))
    assert tb.basis is TimeBasis.SOURCE and tb.verified and tb.map_status is MapStatus.OK
    assert tb.repeats_removed == 0 and tb.segment_count == 1 and tb.irregular_count() == 0
    assert tb.source_frames is not None and tb.source_frames.tolist() == list(range(10))
    np.testing.assert_allclose(tb.times_s, np.arange(10) / FPS, atol=1e-6)
    assert tb.interval_status == (FST,) + (R,) * 9
    assert np.isnan(tb.interval_s[0])


def test_original_times_differ_from_proxy_times() -> None:
    """iPhone-like 31.67 / 35.00 ms alternation: times are the source ones, not k/fps,
    and the jitter stays regular."""
    src = _src([114, 126] * 15)
    tm = build_timemap(src, FPS)
    tb = build_timebase(_poses(tm.summary.proxy_frame_count), tm)
    assert tb.basis is TimeBasis.SOURCE
    assert tb.source_frames is not None
    np.testing.assert_allclose(
        tb.times_s, [float(src[i] - src[0]) for i in tb.source_frames], atol=1e-6
    )
    assert not np.allclose(tb.times_s, tb.proxy_frames / FPS, atol=1e-4)
    assert tb.irregular_count() == 0 and tb.segment_count == 1


def test_real_repeated_frame_is_removed_and_interval_is_source_interval() -> None:
    # 60 ms gap between source frames 4 and 5 -> proxy slot 6 repeats source frame 4
    src = _src([120] * 4 + [240] + [120] * 4)
    tm = build_timemap(src, FPS)
    assert tm.summary.duplicated_frames == 1
    poses = _poses(tm.summary.proxy_frame_count)
    tb = build_timebase(poses, tm)
    assert tb.repeats_removed == 1
    assert len(tb.proxy_frames) == len(poses.frames) - 1
    assert tb.source_frames is not None and tb.source_frames.tolist() == list(range(10))
    # the repeat is not a zero interval: the gap is reported once, as LONG, at its real size
    k = int(np.flatnonzero(tb.source_frames == 5)[0])
    assert tb.interval_status[k] is L
    assert tb.interval_s[k] == pytest.approx(240 / 3600, abs=1e-6)
    assert tb.segment_count == 2
    assert not np.any(np.diff(tb.times_s) <= 0)


def test_skipped_source_frame_is_judged_by_real_interval() -> None:
    """Source frame 3 shares a proxy slot with frame 4 and never appears. The sample is
    simply missing: the interval is measured on real times (2 -> 4 = 41.7 ms, 1.25 x
    nominal), so it stays regular instead of being flagged by frame counting."""
    src = _src([120, 120, 120, 30, 150, 120, 120])
    tm = build_timemap(src, FPS)
    assert tm.summary.dropped_frames == 1
    tb = build_timebase(_poses(tm.summary.proxy_frame_count), tm)
    assert tb.source_frames is not None and 3 not in tb.source_frames.tolist()
    k = int(np.flatnonzero(tb.source_frames == 4)[0])
    assert tb.interval_s[k] == pytest.approx(150 / 3600, abs=1e-6)
    assert tb.interval_status[k] is R and tb.segment_count == 1


def test_vfr_gap_and_short_interval() -> None:
    src = _src([120] * 3 + [600] + [120] * 3)  # 167 ms hole (e.g. a VFR pause)
    tm = build_timemap(src, FPS)
    tb = build_timebase(_poses(tm.summary.proxy_frame_count), tm)
    assert tb.repeats_removed == 4 and tb.irregular_count() == 1 and tb.segment_count == 2
    # a map of a burst (two source frames 8 ms apart, both shown) gives SHORT
    tm2 = TimeMap.model_validate(
        {
            **tm.model_dump(),
            "source_times_s": [0.0, 0.0333, 0.0413, 0.0747],
            "proxy_to_source": [0, 1, 2, 3],
            "summary": {**tm.summary.model_dump(), "source_frame_count": 4,
                        "proxy_frame_count": 4},
        }
    )  # fmt: skip
    tb2 = build_timebase(_poses(4), tm2)
    assert tb2.interval_status == (FST, R, S, R) and tb2.segment.tolist() == [0, 0, 1, 1]


def test_thresholds_are_configurable() -> None:
    src = _src([120] * 3 + [190] + [120] * 3)  # 1.58 x nominal, no drop/repeat
    tm = build_timemap(src, FPS)
    poses = _poses(tm.summary.proxy_frame_count)
    assert build_timebase(poses, tm).irregular_count() == 1
    assert build_timebase(poses, tm, TimeBaseParams(max_interval_ratio=1.7)).irregular_count() == 0
    with pytest.raises(ValueError):
        TimeBaseParams(max_interval_ratio=0.9)


def test_missing_map_falls_back_to_unverified_proxy_times() -> None:
    tb = build_timebase(_poses(5))
    assert tb.basis is TimeBasis.PROXY and not tb.verified
    assert tb.map_status is MapStatus.MISSING and tb.source_frames is None
    assert tb.repeats_removed == 0  # repeats cannot be seen without a map
    np.testing.assert_allclose(tb.times_s, np.arange(5) / FPS)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"summary.proxy_fps": 25.0}, "proxy_fps"),
        ({"summary.proxy_frame_count": 3, "proxy_to_source": [0, 1, 2]}, "covers 3"),
        ({"proxy_to_source": [0, 1, 1, 0, 2, 3, 4, 5]}, "non-decreasing"),
        ({"proxy_to_source": [0, 1, 2, 3, 4, 5, 6, 9]}, "outside"),
        ({"source_times_s": [0.0, 0.1, 0.05, 0.2, 0.3, 0.4, 0.5, 0.6]}, "strictly increasing"),
        ({"source_times_s": [0.0, 0.1]}, "source_frame_count"),
    ],
)
def test_inconsistent_map_is_ignored_with_reasons(change: dict[str, object], reason: str) -> None:
    tm = build_timemap(_src([120] * 7), FPS)
    data = tm.model_dump()
    for key, value in change.items():
        if key.startswith("summary."):
            data["summary"][key.removeprefix("summary.")] = value
        else:
            data[key] = value
    tb = build_timebase(_poses(8), TimeMap.model_validate(data))
    assert tb.map_status is MapStatus.INCONSISTENT and tb.basis is TimeBasis.PROXY
    assert any(reason in p for p in tb.map_problems), tb.map_problems
    assert not tb.verified


def test_pose_timestamps_not_matching_frame_rate_make_map_unusable() -> None:
    poses = _poses(8)
    poses.frames[3].timestamp_s = 0.5
    tb = build_timebase(poses, build_timemap(_src([120] * 7), FPS))
    assert tb.map_status is MapStatus.INCONSISTENT
    assert any("timestamp_s" in p for p in tb.map_problems)


def test_stride_sets_nominal_interval() -> None:
    src = _src([120] * 19)
    tm = build_timemap(src, FPS)
    tb = build_timebase(_poses(20, stride=2), tm)
    assert tb.nominal_interval_s == pytest.approx(2 / FPS)
    assert tb.proxy_frames.tolist() == list(range(0, 20, 2))
    assert tb.irregular_count() == 0 and tb.segment_count == 1


def test_undetected_frames_are_kept_as_samples() -> None:
    """Detection is handled by later interventions: the time base has every frame."""
    tb = build_timebase(_poses(6, detected=False), build_timemap(_src([120] * 5), FPS))
    assert len(tb.proxy_frames) == 6


def test_rejects_unordered_pose_frames() -> None:
    poses = _poses(4)
    poses.frames.reverse()
    with pytest.raises(ValueError):
        build_timebase(poses)
