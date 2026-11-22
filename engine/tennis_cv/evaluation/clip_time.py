"""Time of annotated frames and input checks around them (M2.3 evaluation, rules P3 / P3 bis).

One rule converts a proxy frame ``f`` into seconds, the same one the signals use
(:func:`tennis_cv.signals.timebase.build_timebase`), with no interpolation:

* basis ``source`` (consistent ``timemap.json``): ``t(f) = source_times_s[proxy_to_source[f]]``,
  the time of the source image shown in that frame;
* basis ``proxy``: ``t(f) = f / fps``, marked ``unverified_time``.

Each annotated frame gets exactly one state, from the first check that applies:

1. integrity of ``pose.json`` (checked once per clip, before any label): error
   ``pose_integrity_error``;
2. frame out of domain: error ``label_frame_out_of_domain``;
3. ``not_sampled_stride``; 4. ``repeated_frame`` (source basis only); 5. ``no_pose_frame``;
6. a sample with a pose (no attribute).

States 3-5 are not errors and not miss causes: the label keeps its time ``t(f)`` and gets
``kinematic_sample = False``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

import numpy as np

from tennis_cv.errors import TennisCVError
from tennis_cv.pose.models import PoseSequence
from tennis_cv.signals.smoothing import KeypointQuality, SmoothingParams, extract_keypoint
from tennis_cv.signals.timebase import (
    IntervalStatus,
    MapStatus,
    TimeBase,
    TimeBaseParams,
    TimeBasis,
    build_timebase,
)
from tennis_cv.video.timemap import TimeMap

WRISTS: tuple[str, str] = ("left_wrist", "right_wrist")
_IRREGULAR = (IntervalStatus.LONG, IntervalStatus.SHORT, IntervalStatus.INVALID)


class PoseIntegrityError(TennisCVError):
    """pose.json is incomplete or inconsistent with its declared metadata."""

    code = "pose_integrity_error"


class LabelFrameOutOfDomainError(TennisCVError):
    """An annotated frame is outside the frames the clip's inputs describe."""

    code = "label_frame_out_of_domain"


class FrameState(StrEnum):
    SAMPLE = "sample"  # sampled, kept by the time base, pose detected
    NOT_SAMPLED_STRIDE = "not_sampled_stride"
    REPEATED_FRAME = "repeated_frame"
    NO_POSE_FRAME = "no_pose_frame"


@dataclass(frozen=True)
class FrameInfo:
    frame: int
    t_s: float
    state: FrameState
    kept_frame: int | None
    """For ``repeated_frame``: the kept sample showing the same source image."""
    kept_frame_detected: bool | None
    nearest_sample_dt_s: float | None
    """Distance in seconds from the nearest kept sample (None without samples)."""

    @property
    def kinematic_sample(self) -> bool:
        return self.state is FrameState.SAMPLE


def check_pose_integrity(poses: PoseSequence) -> list[str]:
    """Problems that make ``pose.json`` unusable for frame states (empty list = ok)."""
    problems: list[str] = []
    if not (math.isfinite(poses.fps) and poses.fps > 0):
        problems.append(f"fps {poses.fps} is not finite and > 0")
    if poses.frame_stride < 1:
        problems.append(f"frame_stride {poses.frame_stride} < 1")
    if problems:
        return problems
    idx = [f.frame_index for f in poses.frames]
    if not idx:
        return ["pose.json has no frames"]
    if any(b <= a for a, b in pairwise(idx)):
        problems.append("frame_index values are not strictly increasing")
    bad_stride = [i for i in idx if i < 0 or i % poses.frame_stride]
    if bad_stride:
        problems.append(
            f"{len(bad_stride)} frame_index values are negative or not multiples of "
            f"frame_stride {poses.frame_stride} (first {bad_stride[0]})"
        )
    expected = set(range(0, max(idx) + 1, poses.frame_stride))
    missing = sorted(expected - set(idx))
    if missing:
        problems.append(
            f"{len(missing)} sampled frames missing between 0 and {max(idx)} (first {missing[0]})"
        )
    bad_ts = [
        f.frame_index
        for f in poses.frames
        if not math.isfinite(f.timestamp_s)
        or abs(f.timestamp_s - f.frame_index / poses.fps) > 0.25 / poses.fps
    ]
    if bad_ts:
        problems.append(
            f"timestamp_s differs from frame_index / fps in {len(bad_ts)} frames "
            f"(first {bad_ts[0]})"
        )
    return problems


class ClipTime:
    """Time base of one clip plus the queries the evaluation needs."""

    def __init__(
        self,
        clip_id: str,
        poses: PoseSequence,
        timemap: TimeMap | None,
        timebase_params: TimeBaseParams,
        smoothing_params: SmoothingParams,
    ) -> None:
        problems = check_pose_integrity(poses)
        if problems:
            raise PoseIntegrityError(
                f"pose.json of clip {clip_id} is not consistent",
                details={"problems": [f"clip {clip_id}: {p}" for p in problems]},
            )
        self.clip_id = clip_id
        self.poses = poses
        self.timemap = timemap
        self.base: TimeBase = build_timebase(poses, timemap, timebase_params)
        self.smoothing_params = smoothing_params
        self._detected = {f.frame_index: f.detected for f in poses.frames}
        self._kept = {int(f): i for i, f in enumerate(self.base.proxy_frames.tolist())}
        self._last_frame = poses.frames[-1].frame_index
        self._wrists = {
            w: extract_keypoint(poses, self.base, w, smoothing_params).quality for w in WRISTS
        }

    @property
    def basis(self) -> TimeBasis:
        return self.base.basis

    @property
    def map_status(self) -> MapStatus:
        return self.base.map_status

    @property
    def unverified_time(self) -> bool:
        return self.base.basis is TimeBasis.PROXY

    def in_domain(self, frame: int) -> bool:
        if self.base.basis is TimeBasis.SOURCE and self.timemap is not None:
            return 0 <= frame < self.timemap.summary.proxy_frame_count
        return 0 <= frame <= self._last_frame

    def time(self, frame: int) -> float:
        """``t(f)``; raises if ``f`` is out of domain."""
        if not self.in_domain(frame):
            raise LabelFrameOutOfDomainError(
                f"frame {frame} is outside clip {self.clip_id}",
                details={"problems": [self._domain_text(frame)]},
            )
        if self.base.basis is TimeBasis.SOURCE and self.timemap is not None:
            return float(self.timemap.source_times_s[self.timemap.proxy_to_source[frame]])
        return frame / self.poses.fps

    def _domain_text(self, frame: int) -> str:
        if self.base.basis is TimeBasis.SOURCE and self.timemap is not None:
            n = self.timemap.summary.proxy_frame_count
            return f"clip {self.clip_id}: frame {frame} not in 0..{n - 1} (timemap)"
        return f"clip {self.clip_id}: frame {frame} not in 0..{self._last_frame} (pose.json)"

    def is_sample(self, frame: int) -> bool:
        """Kept by the time base (sampled and not a repeat)."""
        return frame in self._kept

    def frame_info(self, frame: int) -> FrameInfo:
        """State of an annotated frame, by the fixed precedence of the module docstring."""
        t = self.time(frame)  # check 2 (check 1 ran in __init__)
        times = self.base.times_s
        nearest = float(np.min(np.abs(times - t))) if times.size else None
        if frame % self.poses.frame_stride:
            return FrameInfo(frame, t, FrameState.NOT_SAMPLED_STRIDE, None, None, nearest)
        if frame not in self._kept:
            # sampled but dropped: it repeats the source image of the previous kept sample
            kept = int(self.base.proxy_frames[self.base.proxy_frames <= frame].max())
            return FrameInfo(
                frame, t, FrameState.REPEATED_FRAME, kept, self._detected[kept], nearest
            )
        if not self._detected[frame]:
            return FrameInfo(frame, t, FrameState.NO_POSE_FRAME, None, None, nearest)
        return FrameInfo(frame, t, FrameState.SAMPLE, None, None, nearest)

    def _window(self, t_s: float, half_s: float, tol: float) -> list[int]:
        times = self.base.times_s
        return [i for i in range(times.size) if abs(float(times[i]) - t_s) <= half_s + tol]

    def any_pose_in_window(self, t_s: float, half_s: float, tol: float) -> bool:
        frames = self.base.proxy_frames
        return any(self._detected[int(frames[i])] for i in self._window(t_s, half_s, tol))

    def any_wrist_usable_in_window(self, t_s: float, half_s: float, tol: float) -> bool:
        """A GOOD or LOW sample of either wrist (thresholds of ``smoothing_params``)."""
        return any(
            self._wrists[w][i] is not KeypointQuality.UNRELIABLE
            for i in self._window(t_s, half_s, tol)
            for w in WRISTS
        )

    def irregular_intervals_in_window(self, t_s: float, half_s: float, tol: float) -> int:
        """Irregular intervals (long / short / invalid) overlapping ``t +- half``; each one
        is also a segment boundary."""
        times = self.base.times_s
        lo, hi = t_s - half_s - tol, t_s + half_s + tol
        count = 0
        for i in range(1, times.size):
            if self.base.interval_status[i] not in _IRREGULAR:
                continue
            a, b = float(times[i - 1]), float(times[i])
            if not (math.isfinite(a) and math.isfinite(b)) or (a <= hi and b >= lo):
                count += 1
        return count
