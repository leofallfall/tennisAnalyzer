"""Map proxy frames back to the original video (M1.10).

The proxy (see ``preprocess``) is made with ``setpts=PTS-STARTPTS,fps=<rate>``. This
module reproduces the selection rule of FFmpeg's ``fps`` filter (``round=near``) from the
source frame timestamps, so that every proxy frame can be traced to the source frame it
shows and to that frame's time in the original file.

What this is and is not
-----------------------
* It is a **reconstruction** of FFmpeg's rule from the container timestamps (packet
  PTS read with ffprobe, no decoding). It is not read back from the encoder. On the
  synthetic test videos (CFR, VFR, non-zero start, early audio, 120/240 fps) it matches
  the frames actually present in the proxy exactly; real smartphone files are validated
  in M1.11.
* The correspondence is **frame-exact, time-approximate**: each proxy frame shows one
  whole source frame, but its proxy time ``k / fps`` differs from that frame's source
  time by up to half a proxy frame interval, and by more where the source has gaps
  (VFR drops), where the same source frame is repeated. ``max_abs_error_ms`` reports the
  worst case for each video.

Rule (``fps`` filter, ``round=near``, ``eof_action=pass``): source frame ``i`` at time
``t_i`` (relative to the first source frame) belongs to output slot
``s_i = floor(t_i * fps + 0.5)``. Proxy frame ``k`` shows the last source frame with
``s_i <= k``; later source frames in the same slot are dropped, empty slots repeat the
previous frame. Frames after the last slot repeat the last source frame.
"""

from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.errors import ProbeError, scrub_paths
from tennis_cv.video.ffmpeg import find_tool, run_tool

METHOD = "ffmpeg_fps_filter_reconstruction"
LIMITATIONS = [
    "Reconstructed from container timestamps with FFmpeg's fps-filter rule; not read back "
    "from the encoder. Validated exactly on synthetic videos only (M1.11: real phones).",
    "Frame-exact, time-approximate: a proxy time differs from the source time of the frame "
    "it shows by up to half a proxy frame interval, more across VFR gaps (see max_abs_error_ms).",
    "Source times are presentation timestamps of the original container after its edit "
    "list; slow-motion playback metadata (e.g. iPhone slo-mo ramps) is ignored.",
]


class TimeMapSummary(StrictModel):
    """Stored in the processing report; the per-frame map is in ``timemap.json``."""

    method: str = METHOD
    exact_time_sync: bool = Field(
        False, description="Always false: the correspondence is frame-exact, time-approximate"
    )
    source_start_pts_s: float = Field(
        description="Presentation time, in the original file, of its first video frame. "
        "Proxy time 0 corresponds to this instant."
    )
    proxy_fps: float
    source_frame_count: int
    proxy_frame_count: int
    duplicated_frames: int = Field(description="Proxy frames repeating the previous source frame")
    dropped_frames: int = Field(description="Source frames that appear in no proxy frame")
    max_abs_error_ms: float = Field(
        description="Worst |proxy time - source time of the frame shown|"
    )
    mean_abs_error_ms: float
    limitations: list[str] = Field(default_factory=lambda: list(LIMITATIONS))


class TimeMap(StrictModel):
    """Proxy frame ``k`` shows source frame ``proxy_to_source[k]`` (presentation order),
    whose time in the original file is ``source_start_pts_s + source_times_s[...]``."""

    summary: TimeMapSummary
    source_times_s: list[float] = Field(
        description="Source frame times relative to the first frame, presentation order"
    )
    proxy_to_source: list[int]

    def source_frame(self, proxy_frame: int) -> int:
        k = min(max(proxy_frame, 0), len(self.proxy_to_source) - 1)
        return self.proxy_to_source[k]

    def source_time(self, proxy_time_s: float) -> float:
        """Absolute time in the original file of the frame shown at ``proxy_time_s``."""
        k = int(math.floor(proxy_time_s * self.summary.proxy_fps + 1e-6))
        return self.summary.source_start_pts_s + self.source_times_s[self.source_frame(k)]


def read_source_frame_times(path: str | Path, *, timeout_s: float = 300) -> list[float]:
    """Presentation timestamps (s) of the first video stream's frames, sorted.

    Uses packet timestamps (no decoding). Packets marked discard by the edit list are
    skipped, as the decoder skips them.
    """
    p = Path(path)
    cmd = [
        find_tool("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time,flags",
        "-of",
        "json",
        str(p),
    ]
    result = run_tool(cmd, timeout_s=timeout_s)
    if result.returncode != 0:
        raise ProbeError(
            "ffprobe could not read the frame timestamps",
            details={"file": p.name, "stderr": scrub_paths(result.stderr.strip(), p)[-2000:]},
        )
    packets = json.loads(result.stdout or "{}").get("packets") or []
    times = sorted(
        float(pk["pts_time"])
        for pk in packets
        if pk.get("pts_time") not in (None, "N/A") and "D" not in str(pk.get("flags", ""))
    )
    if not times:
        raise ProbeError("The video has no timestamped frames", details={"file": p.name})
    return times


def build_timemap(
    source_pts_s: list[float], fps: Fraction | float, proxy_frame_count: int | None = None
) -> TimeMap:
    """Reproduce the fps-filter selection. ``source_pts_s`` must be sorted."""
    if not source_pts_s:
        raise ValueError("source_pts_s is empty")
    rate = float(fps)
    start = source_pts_s[0]
    rel = [t - start for t in source_pts_s]
    slots = [int(math.floor(t * rate + 0.5)) for t in rel]
    natural = slots[-1] + 1
    count = proxy_frame_count if proxy_frame_count and proxy_frame_count > 0 else natural
    mapping: list[int] = []
    i = 0
    for k in range(count):
        while i + 1 < len(slots) and slots[i + 1] <= k:
            i += 1
        mapping.append(i)
    shown = set(mapping)
    errors = [abs(k / rate - rel[src]) * 1000 for k, src in enumerate(mapping)]
    duplicated = sum(1 for k in range(1, count) if mapping[k] == mapping[k - 1])
    summary = TimeMapSummary(
        source_start_pts_s=round(start, 6),
        proxy_fps=round(rate, 6),
        source_frame_count=len(rel),
        proxy_frame_count=count,
        duplicated_frames=duplicated,
        dropped_frames=len(rel) - len(shown),
        max_abs_error_ms=round(max(errors), 3),
        mean_abs_error_ms=round(sum(errors) / len(errors), 3),
    )
    return TimeMap(
        summary=summary,
        source_times_s=[round(t, 6) for t in rel],
        proxy_to_source=mapping,
    )
