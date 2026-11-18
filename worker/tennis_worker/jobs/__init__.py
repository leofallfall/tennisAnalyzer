"""Job handlers. Each handler processes one ProcessingJob kind."""

from tennis_worker.jobs.video_processing import run_video_processing_job

JOB_HANDLERS = {"video_processing": run_video_processing_job}

__all__ = ["JOB_HANDLERS", "run_video_processing_job"]
