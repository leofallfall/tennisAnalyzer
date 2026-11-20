"""Time base for pose signals (M2.2, intervention 1).

Turns the frames of a ``pose.json`` into **samples** with an explicit time, before any
signal is computed. Velocities and accelerations (later interventions) divide by these
times, so a wrong time would look like a real movement.

Rules
-----
* **Time of a sample.** With a usable ``timemap.json`` the time is the presentation time
  of the *source* frame the proxy frame shows (``source_times_s``, relative to the first
  source frame; proxy time 0 is that same frame). Without one, the proxy time
  ``frame_index / fps`` is used and the whole base is marked ``unverified``.
* **Repeated frames.** Proxy frames that show the same source frame as the previous kept
  sample are dropped (only the first is kept): they carry no new image, so they carry no
  new information about the movement. This removes repeats; it does **not** remove the
  temporal filtering MediaPipe applies to every frame in VIDEO mode, which cannot be
  undone from ``pose.json`` (see docs/M2_2_SIGNALS.md).
* **Irregular intervals.** Every interval is compared with the nominal one
  (``frame_stride / fps``) using the real times above. Too long (a VFR pause, a hole
  left by repeats, a missing stride sample) or too short (a burst) intervals end a
  *segment*; later derivatives must stay inside one segment. A source frame the proxy
  skipped is just a missing sample: it ends a segment only if the real interval between
  the samples around it is irregular.
* **Map checks.** A map is used only if it is consistent with ``pose.json``: same proxy
  frame count and rate, pose ``timestamp_s`` finite and equal to ``frame_index / fps``,
  every proxy frame mapped, source indices non-decreasing and in range, source times
  finite and strictly increasing. Otherwise it is ignored (``inconsistent``), with the
  reasons. Without a map, pose timestamp problems are still listed in ``map_problems``.
* **Rejected inputs.** ``fps`` not finite or <= 0, ``frame_stride`` < 1, negative or
  unordered ``frame_index`` raise ``ValueError``: no time base can be built from them.
* **Old maps.** A map written before the exact-slot fix (commit 8df7dd3) passes these
  checks and cannot be told apart from a correct one: regenerate old maps.

This module never reads files and never changes artifacts; it only interprets them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

import numpy as np
import numpy.typing as npt

from tennis_cv.pose.models import PoseSequence
from tennis_cv.video.timemap import TimeMap

IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]


class TimeBasis(StrEnum):
    SOURCE = "source"  # times of the original file's frames (from timemap.json)
    PROXY = "proxy"  # frame_index / fps on the proxy timeline


class MapStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    INCONSISTENT = "inconsistent"


class IntervalStatus(StrEnum):
    FIRST = "first"  # first sample: no interval before it
    REGULAR = "regular"
    LONG = "long"  # real interval longer than max_interval_ratio x nominal
    SHORT = "short"  # real interval shorter than min_interval_ratio x nominal
    INVALID = "invalid"  # interval not finite (NaN / inf): unusable, never regular


@dataclass(frozen=True)
class TimeBaseParams:
    """General parameters (not player-specific). Defaults are starting values, not
    validated: the iPhone originals of M2.1 alternate 31.67 / 35.00 ms around 33.33 ms
    (ratios 0.95-1.05), a skipped frame gives 2.0."""

    max_interval_ratio: float = 1.5
    min_interval_ratio: float = 0.5

    def __post_init__(self) -> None:
        if not 0 < self.min_interval_ratio < 1 < self.max_interval_ratio:
            raise ValueError("need 0 < min_interval_ratio < 1 < max_interval_ratio")


@dataclass(frozen=True)
class TimeBase:
    """One entry per kept sample, in time order.

    ``proxy_frames``: frame index in the proxy / ``pose.json`` (to look up the pose).
    ``source_frames``: source frame index, ``None`` when no usable map.
    ``times_s``: sample time in seconds on ``basis``.
    ``interval_s`` / ``interval_status``: interval from the previous sample (NaN / FIRST
    for the first one). ``segment``: id of the run of regular intervals the sample
    belongs to; derivatives must not cross a segment boundary.
    ``repeats_removed``: proxy frames dropped because they repeat a source frame.
    """

    basis: TimeBasis
    map_status: MapStatus
    map_problems: tuple[str, ...]
    nominal_interval_s: float
    proxy_frames: IntArray
    source_frames: IntArray | None
    times_s: FloatArray
    interval_s: FloatArray
    interval_status: tuple[IntervalStatus, ...]
    segment: IntArray
    repeats_removed: int

    @property
    def verified(self) -> bool:
        """True when times come from a consistent source map (not a guarantee that the
        map is exact; see the module docstring)."""
        return self.basis is TimeBasis.SOURCE

    @property
    def segment_count(self) -> int:
        return int(self.segment.max()) + 1 if self.segment.size else 0

    def irregular_count(self) -> int:
        return sum(s in _IRREGULAR for s in self.interval_status)


_IRREGULAR = (IntervalStatus.LONG, IntervalStatus.SHORT, IntervalStatus.INVALID)


def _pose_timestamp_problems(poses: PoseSequence) -> list[str]:
    bad = [
        f.frame_index
        for f in poses.frames
        if not math.isfinite(f.timestamp_s)
        or abs(f.timestamp_s - f.frame_index / poses.fps) > 0.25 / poses.fps
    ]
    if not bad:
        return []
    return [
        f"pose timestamp_s not finite or != frame_index/fps ({len(bad)} frames, first {bad[0]})"
    ]


def check_timemap(poses: PoseSequence, timemap: TimeMap) -> list[str]:
    """Reasons why ``timemap`` cannot be used with ``poses`` (empty list = consistent)."""
    problems: list[str] = []
    s = timemap.summary
    mapping = timemap.proxy_to_source
    times = timemap.source_times_s
    if not poses.frames:
        return ["pose.json has no frames"]
    if poses.frames[0].frame_index < 0:
        problems.append("pose.json has negative frame_index")
    last = poses.frames[-1].frame_index
    problems += _pose_timestamp_problems(poses)
    if not math.isfinite(s.proxy_fps) or abs(s.proxy_fps - poses.fps) > 1e-3 * max(poses.fps, 1.0):
        problems.append(f"map proxy_fps {s.proxy_fps} differs from pose fps {poses.fps}")
    if len(mapping) != s.proxy_frame_count:
        problems.append("proxy_to_source length differs from proxy_frame_count")
    # pose.json samples every frame_stride-th proxy frame from 0: its last frame index
    # must be one of the last frame_stride frames of the map (stride 1: same length)
    if not last < s.proxy_frame_count <= last + poses.frame_stride:
        problems.append(
            f"map has {s.proxy_frame_count} proxy frames, pose.json has {len(poses.frames)} "
            f"(last frame {last}, stride {poses.frame_stride})"
        )
    if not all(math.isfinite(t) for t in times):
        problems.append("source_times_s contains NaN or infinite values")
    if len(times) != s.source_frame_count:
        problems.append("source_times_s length differs from source_frame_count")
    if mapping and (min(mapping) < 0 or max(mapping) >= len(times)):
        problems.append("proxy_to_source points outside source_times_s")
    if any(b < a for a, b in pairwise(mapping)):
        problems.append("proxy_to_source is not non-decreasing")
    if any(b <= a for a, b in pairwise(times)):
        problems.append("source_times_s is not strictly increasing")
    return problems


def classify_intervals(
    times_s: FloatArray, nominal_s: float, params: TimeBaseParams | None = None
) -> tuple[FloatArray, tuple[IntervalStatus, ...], IntArray]:
    """Interval from the previous sample, its status and the segment id of each sample.

    A non-finite interval (NaN / inf time) is ``INVALID``, never ``REGULAR``; like ``LONG``
    and ``SHORT`` it starts a new segment."""
    params = params or TimeBaseParams()
    interval = np.full(times_s.size, np.nan)
    interval[1:] = np.diff(times_s)
    statuses: list[IntervalStatus] = []
    segment = np.zeros(times_s.size, dtype=np.int64)
    seg = 0
    for i in range(times_s.size):
        if i == 0:
            st = IntervalStatus.FIRST
        else:
            ratio = interval[i] / nominal_s
            if not math.isfinite(ratio):
                st = IntervalStatus.INVALID
            elif ratio > params.max_interval_ratio:
                st = IntervalStatus.LONG
            elif ratio < params.min_interval_ratio:
                st = IntervalStatus.SHORT
            else:
                st = IntervalStatus.REGULAR
            if st is not IntervalStatus.REGULAR:
                seg += 1
        statuses.append(st)
        segment[i] = seg
    return interval, tuple(statuses), segment


def build_timebase(
    poses: PoseSequence,
    timemap: TimeMap | None = None,
    params: TimeBaseParams | None = None,
) -> TimeBase:
    """Build the sample time base of ``poses`` (see module docstring)."""
    params = params or TimeBaseParams()
    if not (math.isfinite(poses.fps) and poses.fps > 0) or poses.frame_stride < 1:
        raise ValueError("pose.json needs a finite fps > 0 and frame_stride >= 1")
    frame_idx = np.array([f.frame_index for f in poses.frames], dtype=np.int64)
    if frame_idx.size and frame_idx[0] < 0:
        raise ValueError("pose.json frame indices must not be negative")
    if frame_idx.size and np.any(np.diff(frame_idx) <= 0):
        raise ValueError("pose.json frame indices must be strictly increasing")
    nominal = poses.frame_stride / poses.fps

    status = MapStatus.MISSING
    problems: tuple[str, ...] = ()
    if timemap is not None:
        found = check_timemap(poses, timemap)
        status = MapStatus.INCONSISTENT if found else MapStatus.OK
        problems = tuple(found)
    else:
        problems = tuple(_pose_timestamp_problems(poses))

    source_frames: IntArray | None = None
    repeats = 0
    if status is MapStatus.OK and timemap is not None:
        mapping = np.asarray(timemap.proxy_to_source, dtype=np.int64)
        src_all = mapping[frame_idx]
        keep = np.ones(frame_idx.size, dtype=bool)
        keep[1:] = src_all[1:] != src_all[:-1]
        repeats = int((~keep).sum())
        frame_idx = frame_idx[keep]
        source_frames = src_all[keep]
        src_times = np.asarray(timemap.source_times_s, dtype=np.float64)
        times = src_times[source_frames]
        basis = TimeBasis.SOURCE
    else:
        times = frame_idx.astype(np.float64) / poses.fps
        basis = TimeBasis.PROXY

    interval, statuses, segment = classify_intervals(times, nominal, params)
    return TimeBase(
        basis=basis,
        map_status=status,
        map_problems=problems,
        nominal_interval_s=nominal,
        proxy_frames=frame_idx,
        source_frames=source_frames,
        times_s=times,
        interval_s=interval,
        interval_status=statuses,
        segment=segment,
        repeats_removed=repeats,
    )
