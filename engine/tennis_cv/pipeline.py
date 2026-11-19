"""Milestone 1 video-processing prototype pipeline.

Steps: probe -> validate -> preprocess (proxy) -> decode frames -> quality metrics
-> pose estimation -> pose export -> overlay video -> report.

This pipeline performs NO stroke or biomechanical analysis. Its purpose is to
validate ingestion and pose estimation on real amateur footage.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from tennis_cv import __version__
from tennis_cv.common import StrictModel
from tennis_cv.errors import TennisCVError, VideoValidationError
from tennis_cv.pose.base import PoseEstimator
from tennis_cv.pose.models import PoseFrame, PoseSequence, PoseSummary, summarize
from tennis_cv.pose.overlay import OverlayWriter, annotate_frame_info, draw_skeleton
from tennis_cv.quality.assessment import QualityReport, assess_quality
from tennis_cv.quality.metrics import FrameQuality, measure_frame
from tennis_cv.video.frames import iter_frames
from tennis_cv.video.metadata import VideoMetadata, probe_video
from tennis_cv.video.preprocess import (
    PreprocessOptions,
    PreprocessResult,
    preprocess_video,
    proxy_frame_rate,
)
from tennis_cv.video.timemap import TimeMapSummary, build_timemap, read_source_frame_times
from tennis_cv.video.validation import ValidationResult, validate_metadata

ProgressCallback = Callable[[str, float], None]

PROXY_NAME = "proxy.mp4"
POSE_NAME = "pose.json"
OVERLAY_NAME = "overlay.mp4"
REPORT_NAME = "report.json"
TIMEMAP_NAME = "timemap.json"

log = logging.getLogger(__name__)


class PipelineOptions(StrictModel):
    pose_stride: int = Field(1, ge=1, description="Run pose on every N-th frame")
    max_pose_frames: int | None = Field(None, ge=1)
    quality_sample_every_s: float = Field(0.5, gt=0)
    render_overlay: bool = True
    preprocess: PreprocessOptions = Field(default_factory=PreprocessOptions)


class StepTiming(StrictModel):
    step: str
    duration_ms: float


class ProcessingReport(StrictModel):
    engine_version: str = __version__
    source_metadata: VideoMetadata
    validation: ValidationResult
    preprocess: PreprocessResult
    quality: QualityReport
    pose_estimator: str
    pose_model: str
    pose_summary: PoseSummary
    time_mapping: TimeMapSummary | None = Field(
        default=None,
        description="Proxy -> original frame correspondence (frame-exact, time-approximate); "
        "per-frame map in timemap.json. None when it could not be built.",
    )
    artifacts: dict[str, str]
    timings: list[StepTiming]
    total_duration_ms: float
    not_performed: list[str] = Field(
        default_factory=lambda: [
            "stroke_detection",
            "stroke_classification",
            "phase_segmentation",
            "racket_tracking",
            "ball_detection",
            "biomechanical_metrics",
            "technical_issue_identification",
        ],
        description="Analyses deliberately not performed because they are not implemented",
    )


def _noop(_step: str, _progress: float) -> None:
    return None


def run_video_processing(
    source: str | Path,
    output_dir: str | Path,
    estimator: PoseEstimator,
    *,
    options: PipelineOptions | None = None,
    progress: ProgressCallback = _noop,
) -> ProcessingReport:
    opts = options or PipelineOptions()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    timings: list[StepTiming] = []
    t_total = time.perf_counter()

    def timed(step: str, t0: float) -> None:
        timings.append(StepTiming(step=step, duration_ms=(time.perf_counter() - t0) * 1000))

    progress("probing", 0.02)
    t0 = time.perf_counter()
    meta = probe_video(source)
    validation = validate_metadata(meta)
    timed("probe_and_validate", t0)
    if not validation.is_valid:
        raise VideoValidationError(
            "Video failed validation",
            details={"issues": [i.model_dump() for i in validation.errors]},
        )

    progress("preprocessing", 0.05)
    t0 = time.perf_counter()
    prep = preprocess_video(source, out / PROXY_NAME, source_metadata=meta, options=opts.preprocess)
    timed("preprocess", t0)

    t0 = time.perf_counter()
    time_mapping = _write_timemap(source, meta, prep, out)
    timed("time_mapping", t0)
    proxy_meta = prep.metadata
    fps = proxy_meta.effective_fps or 30.0
    quality_every = max(1, int(round(opts.quality_sample_every_s * fps)))
    total_frames = proxy_meta.frame_count or 0

    progress("pose_estimation", 0.1)
    t0 = time.perf_counter()
    pose_frames: list[PoseFrame] = []
    frame_quality: list[FrameQuality] = []
    writer = (
        OverlayWriter(
            out / OVERLAY_NAME, fps, (proxy_meta.display_width, proxy_meta.display_height)
        )
        if opts.render_overlay
        else None
    )
    pose_time = 0.0
    last_pose: PoseFrame | None = None
    try:
        for frame in iter_frames(prep.output_path):
            if frame.index % quality_every == 0:
                frame_quality.append(measure_frame(frame.image, frame.index, frame.timestamp_s))
            analysed = frame.index % opts.pose_stride == 0 and (
                opts.max_pose_frames is None or len(pose_frames) < opts.max_pose_frames
            )
            if analysed:
                tp = time.perf_counter()
                last_pose = estimator.estimate(frame)
                pose_time += time.perf_counter() - tp
                pose_frames.append(last_pose)
            if writer is not None:
                img = frame.image
                if analysed and last_pose is not None:
                    img = draw_skeleton(img, last_pose)
                    label = "pose" if last_pose.detected else "no pose detected"
                else:
                    label = "not analysed"
                annotate_frame_info(img, f"#{frame.index}  {frame.timestamp_s:7.3f}s  {label}")
                writer.write(img)
            if total_frames and frame.index % 15 == 0:
                progress("pose_estimation", 0.1 + 0.8 * min(1.0, frame.index / total_frames))
    except BaseException:
        if writer is not None:
            writer.abort()
        raise
    overlay_path = writer.finish() if writer is not None else None
    timed("decode_quality_pose_overlay", t0)

    seq = PoseSequence(
        estimator=estimator.name,
        model=estimator.model,
        image_width=proxy_meta.display_width,
        image_height=proxy_meta.display_height,
        fps=fps,
        frame_stride=opts.pose_stride,
        frames=pose_frames,
        limitations=list(getattr(estimator, "limitations", [])),
    )
    pose_summary = summarize(seq, processing_time_ms=pose_time * 1000)

    progress("writing_results", 0.95)
    t0 = time.perf_counter()
    (out / POSE_NAME).write_text(seq.model_dump_json())
    quality = assess_quality(
        proxy_meta, frame_quality, pose_detection_rate=pose_summary.detection_rate
    )
    # Resolution/fps must describe the *source*, not the downscaled proxy.
    quality.resolution = assess_quality(meta, []).resolution
    quality.frame_rate = assess_quality(meta, []).frame_rate
    timed("write_results", t0)

    artifacts = {"proxy": PROXY_NAME, "pose": POSE_NAME}
    if overlay_path is not None:
        artifacts["overlay"] = OVERLAY_NAME
    if time_mapping is not None:
        artifacts["timemap"] = TIMEMAP_NAME
    # The report reaches API clients: file references are relative, never absolute paths
    # (the source is named by its base name, the proxy relative to ``output_dir``).
    public_meta = meta.model_copy(update={"path": Path(meta.path).name})
    public_prep = prep.model_copy(
        update={
            "output_path": PROXY_NAME,
            "metadata": prep.metadata.model_copy(update={"path": PROXY_NAME}),
        }
    )
    report = ProcessingReport(
        source_metadata=public_meta,
        validation=validation,
        preprocess=public_prep,
        quality=quality,
        pose_estimator=estimator.name,
        pose_model=estimator.model,
        pose_summary=pose_summary,
        time_mapping=time_mapping,
        artifacts=artifacts,
        timings=timings,
        total_duration_ms=(time.perf_counter() - t_total) * 1000,
    )
    (out / REPORT_NAME).write_text(json.dumps(report.model_dump(mode="json"), indent=2))
    progress("completed", 1.0)
    return report


def _write_timemap(
    source: str | Path, meta: VideoMetadata, prep: PreprocessResult, out: Path
) -> TimeMapSummary | None:
    """Build and save the proxy -> original map. Not fatal: the proxy timeline is
    self-consistent without it; only the link back to the original is missing."""
    rate = proxy_frame_rate(meta)
    if rate is None:
        return None
    try:
        times = read_source_frame_times(source)
    except TennisCVError as exc:
        log.warning("Time map not built: %s", exc.message)
        return None
    timemap = build_timemap(times, rate, prep.metadata.frame_count)
    (out / TIMEMAP_NAME).write_text(timemap.model_dump_json())
    return timemap.summary
