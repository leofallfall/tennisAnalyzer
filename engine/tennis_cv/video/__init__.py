"""Video ingestion: probing, validation, preprocessing and frame extraction."""

from tennis_cv.video.frames import Frame, iter_frames
from tennis_cv.video.metadata import VideoMetadata, parse_ffprobe_output, probe_video
from tennis_cv.video.preprocess import PreprocessOptions, PreprocessResult, preprocess_video
from tennis_cv.video.validation import (
    ValidationIssue,
    ValidationLimits,
    ValidationResult,
    validate_metadata,
)

__all__ = [
    "Frame",
    "PreprocessOptions",
    "PreprocessResult",
    "ValidationIssue",
    "ValidationLimits",
    "ValidationResult",
    "VideoMetadata",
    "iter_frames",
    "parse_ffprobe_output",
    "preprocess_video",
    "probe_video",
    "validate_metadata",
]
