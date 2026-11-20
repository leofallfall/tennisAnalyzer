"""Normalise any supported input into an analysis proxy.

The proxy is:
* rotation applied (pixels are upright, no rotation metadata left),
* constant frame rate (VFR inputs are resampled to their average rate, CFR inputs keep
  their nominal rate) through FFmpeg's ``fps`` filter,
* H.264 / yuv420p / faststart MP4 (plays in every browser),
* long side capped (default 1920 px) to bound processing cost.

Because the proxy is CFR, the timestamp of frame ``i`` is exactly ``i / fps``.
All downstream timestamps refer to this proxy timeline, which starts at the
first decoded video frame of the source (``setpts=PTS-STARTPTS``; a non-zero
``start_time`` or an audio track starting earlier no longer adds leading frames).

Since M1.10 the frame-rate conversion always goes through the ``fps`` filter, also
for CFR inputs, so that one documented rule decides which source frame each proxy
frame shows. ``tennis_cv.video.timemap`` reproduces that rule to map proxy frames
back to the original (see docs/VIDEO_PIPELINE.md, "Time mapping").
"""

from __future__ import annotations

import logging
import time
from fractions import Fraction
from pathlib import Path

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.errors import PreprocessError, scrub_paths
from tennis_cv.video.ffmpeg import find_tool, run_tool
from tennis_cv.video.metadata import VideoMetadata, probe_video

log = logging.getLogger(__name__)


class PreprocessOptions(StrictModel):
    max_long_side_px: int = Field(1920, ge=240)
    crf: int = Field(18, ge=0, le=51)
    preset: str = "veryfast"
    timeout_s: float = 60 * 30


class PreprocessResult(StrictModel):
    output_path: str
    metadata: VideoMetadata
    applied_rotation_deg: int
    resampled_to_cfr: bool
    scaled: bool
    duration_ms: float


def _target_fps(meta: VideoMetadata) -> float | None:
    """Resampling rate for VFR inputs (their average rate); ``None`` for CFR inputs."""
    if not meta.is_variable_frame_rate:
        return None
    fps = meta.average_fps or meta.nominal_fps
    return round(fps, 3) if fps else None


def proxy_frame_rate(meta: VideoMetadata) -> Fraction | None:
    """Exact rate given to the ``fps`` filter, or ``None`` when the source has no rate.

    VFR: the average rate rounded to 3 decimals (as before M1.10). CFR: the nominal rate
    as a rational (29.97 -> 30000/1001), so a CFR source is not resampled.
    """
    vfr = _target_fps(meta)
    if vfr:
        return Fraction(str(vfr))
    if meta.nominal_fps:
        return Fraction(meta.nominal_fps).limit_denominator(1001)
    return None


def build_preprocess_command(
    src: str, dst: str, meta: VideoMetadata, opts: PreprocessOptions
) -> list[str]:
    filters: list[str] = []
    long_side = max(meta.display_width, meta.display_height)
    if long_side > opts.max_long_side_px:
        m = opts.max_long_side_px
        if meta.display_width >= meta.display_height:
            filters.append(f"scale=trunc(min(iw\\,{m})/2)*2:-2")
        else:
            filters.append(f"scale=-2:trunc(min(ih\\,{m})/2)*2")
    else:
        # libx264 + yuv420p require even dimensions.
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    # Proxy time 0 = first decoded video frame; one rule (the fps filter) for every input.
    filters.append("setpts=PTS-STARTPTS")
    rate = proxy_frame_rate(meta)
    if rate:
        # eof_action=pass: the last source frame is never lost to a short final duration.
        filters.append(f"fps={rate.numerator}/{rate.denominator}:eof_action=pass")
    return [
        find_tool("ffmpeg"),
        "-y",
        "-v",
        "error",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        ",".join(filters),
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        opts.preset,
        "-crf",
        str(opts.crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        dst,
    ]


def preprocess_video(
    src: str | Path,
    dst: str | Path,
    *,
    source_metadata: VideoMetadata | None = None,
    options: PreprocessOptions | None = None,
) -> PreprocessResult:
    opts = options or PreprocessOptions()
    src_s, dst_p = str(src), Path(dst)
    meta = source_metadata or probe_video(src_s)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_preprocess_command(src_s, str(dst_p), meta, opts)

    started = time.perf_counter()
    result = run_tool(cmd, timeout_s=opts.timeout_s)
    elapsed = (time.perf_counter() - started) * 1000
    if result.returncode != 0 or not dst_p.is_file():
        # The full command holds absolute paths: server log only, never the API.
        log.warning("FFmpeg preprocessing failed: %s", " ".join(cmd))
        raise PreprocessError(
            "FFmpeg preprocessing failed",
            details={"stderr": scrub_paths(result.stderr.strip(), src_s, dst_p)[-2000:]},
        )
    out_meta = probe_video(dst_p)
    return PreprocessResult(
        output_path=str(dst_p),
        metadata=out_meta,
        applied_rotation_deg=meta.rotation_deg,
        resampled_to_cfr=_target_fps(meta) is not None,
        scaled=max(meta.display_width, meta.display_height) > opts.max_long_side_px,
        duration_ms=elapsed,
    )
