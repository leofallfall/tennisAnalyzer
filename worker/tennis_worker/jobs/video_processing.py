"""Milestone 1 job: ingestion + quality + pose (+ experimental strokes, V1) per video."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from tennis_api.config import Settings
from tennis_api.models import Analysis
from tennis_api.storage import analysis_video_dir
from tennis_cv.pipeline import PipelineOptions, run_video_processing
from tennis_cv.pose.base import PoseEstimator
from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator
from tennis_worker.jobs.stroke_detection import detect_strokes_in_dir

log = logging.getLogger(__name__)

ProgressReporter = Callable[[str, float], None]
EstimatorFactory = Callable[[], PoseEstimator]


def run_video_processing_job(
    analysis: Analysis,
    settings: Settings,
    report_progress: ProgressReporter,
    estimator_factory: EstimatorFactory = MediaPipePoseEstimator,
) -> dict[str, object]:
    """Process each attached video. Returns the ``results`` JSON stored on the analysis.

    Raises engine errors unchanged; the worker loop converts them to a failed status.
    """
    links = list(analysis.video_links)
    reports: dict[str, object] = {}
    strokes: dict[str, object] = {}
    options = PipelineOptions(pose_stride=settings.pose_stride)
    for n, link in enumerate(links):
        source = settings.storage_dir / link.video.stored_path
        out_dir = analysis_video_dir(settings.storage_dir, analysis.id, link.video_id)
        log.info("Processing video %s (%d/%d)", link.video_id, n + 1, len(links))

        def progress(stage: str, frac: float, n: int = n) -> None:
            label = stage if len(links) == 1 else f"{stage} (video {n + 1}/{len(links)})"
            report_progress(label, (n + frac) / len(links))

        started = time.perf_counter()
        # A fresh estimator per video: MediaPipe VIDEO mode tracks across timestamps.
        with estimator_factory() as estimator:
            report = run_video_processing(
                source, out_dir, estimator, options=options, progress=progress
            )
        log.info("Video %s done in %.1fs", link.video_id, time.perf_counter() - started)
        reports[link.video_id] = report.model_dump(mode="json")
        progress("stroke detection", 0.99)
        strokes[link.video_id] = detect_strokes_in_dir(out_dir, settings)
    return {
        "kind": "video_processing",
        "videos": reports,
        "strokes": strokes,
        "note": "Ingestion, quality, pose estimation and experimental stroke detection "
        "(provisional thresholds, not validated). No biomechanical analysis has been performed.",
    }
