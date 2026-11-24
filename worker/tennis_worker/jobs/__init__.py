"""Job handlers. Each handler processes one ProcessingJob kind."""

from tennis_worker.jobs.stroke_detection import run_stroke_detection_job
from tennis_worker.jobs.video_processing import run_video_processing_job

JOB_HANDLERS = {
    "video_processing": run_video_processing_job,
    "stroke_detection": run_stroke_detection_job,
}

__all__ = ["JOB_HANDLERS", "run_stroke_detection_job", "run_video_processing_job"]
