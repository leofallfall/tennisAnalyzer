"""Video metadata extraction with ffprobe.

Handles the realities of smartphone footage: rotation stored as metadata,
variable frame rate (VFR), odd containers and missing duration fields.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.errors import ProbeError, scrub_paths
from tennis_cv.video.ffmpeg import find_tool, run_tool

# avg vs nominal frame-rate disagreement above this ratio is treated as VFR.
VFR_TOLERANCE = 0.02


class VideoMetadata(StrictModel):
    """Technical description of a video file, as read by ffprobe."""

    path: str
    container: str | None = None
    file_size_bytes: int | None = None
    duration_s: float | None = Field(default=None, description="None if not determinable")
    codec: str | None = None
    pixel_format: str | None = None
    coded_width: int
    coded_height: int
    rotation_deg: int = Field(0, description="Clockwise rotation required for display")
    display_width: int
    display_height: int
    nominal_fps: float | None = Field(default=None, description="ffprobe r_frame_rate")
    average_fps: float | None = Field(default=None, description="ffprobe avg_frame_rate")
    is_variable_frame_rate: bool = False
    frame_count: int | None = None
    bit_rate: int | None = None
    has_audio: bool = False

    @property
    def orientation(self) -> str:
        if self.display_width == self.display_height:
            return "square"
        return "landscape" if self.display_width > self.display_height else "portrait"

    @property
    def effective_fps(self) -> float | None:
        return self.average_fps or self.nominal_fps


def _parse_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "0"}:
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _parse_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict[str, Any]) -> int:
    """Clockwise display rotation in degrees, normalised to {0, 90, 180, 270}.

    ffprobe exposes rotation either as a legacy ``tags.rotate`` (clockwise) or as a
    display-matrix side data ``rotation`` (counter-clockwise, e.g. -90 means 90 cw).
    """
    raw: float | None = None
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            raw = -float(side["rotation"])
            break
    if raw is None:
        tag = (stream.get("tags") or {}).get("rotate")
        if tag is not None:
            raw = float(tag)
    if raw is None:
        return 0
    return int(round(raw / 90.0)) * 90 % 360


def parse_ffprobe_output(data: dict[str, Any], path: str) -> VideoMetadata:
    """Convert ``ffprobe -show_format -show_streams -of json`` output to metadata."""
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("No video stream found in file", details={"file": Path(path).name})
    fmt = data.get("format") or {}

    width = _parse_int(video.get("width")) or 0
    height = _parse_int(video.get("height")) or 0
    if width <= 0 or height <= 0:
        raise ProbeError("Video stream has no valid dimensions", details={"file": Path(path).name})

    rotation = _rotation(video)
    swap = rotation in (90, 270)
    nominal = _parse_rate(video.get("r_frame_rate"))
    average = _parse_rate(video.get("avg_frame_rate"))
    vfr = bool(nominal and average and abs(nominal - average) / nominal > VFR_TOLERANCE)

    duration = _parse_float(video.get("duration")) or _parse_float(fmt.get("duration"))
    frame_count = _parse_int(video.get("nb_frames"))
    if frame_count is None and duration and average:
        frame_count = int(round(duration * average))

    return VideoMetadata(
        path=path,
        container=fmt.get("format_name"),
        file_size_bytes=_parse_int(fmt.get("size")),
        duration_s=duration,
        codec=video.get("codec_name"),
        pixel_format=video.get("pix_fmt"),
        coded_width=width,
        coded_height=height,
        rotation_deg=rotation,
        display_width=height if swap else width,
        display_height=width if swap else height,
        nominal_fps=nominal,
        average_fps=average,
        is_variable_frame_rate=vfr,
        frame_count=frame_count,
        bit_rate=_parse_int(fmt.get("bit_rate")),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def probe_video(path: str | Path, *, timeout_s: float = 60) -> VideoMetadata:
    """Run ffprobe on ``path`` and return structured metadata."""
    p = Path(path)
    if not p.is_file():
        raise ProbeError(f"File not found: {p.name}", details={"file": p.name})
    cmd = [
        find_tool("ffprobe"),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(p),
    ]
    result = run_tool(cmd, timeout_s=timeout_s)
    if result.returncode != 0:
        raise ProbeError(
            "ffprobe could not read the file as a video",
            details={"file": p.name, "stderr": scrub_paths(result.stderr.strip(), p)[-2000:]},
        )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON", details={"file": p.name}) from exc
    return parse_ffprobe_output(data, str(p))
