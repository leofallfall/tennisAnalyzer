"""Input validation rules for uploaded videos.

Errors make a video unusable; warnings are surfaced to the user because they
reduce what can be measured reliably (they never silently change results).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.video.metadata import VideoMetadata

SUPPORTED_CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "prores", "mjpeg"}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(StrictModel):
    code: str
    severity: Severity
    message: str


class ValidationLimits(StrictModel):
    min_duration_s: float = 0.5
    max_duration_s: float = 60 * 60
    min_short_side_px: int = 360
    recommended_short_side_px: int = 720
    min_fps: float = 20.0
    recommended_fps: float = 50.0
    max_file_size_bytes: int = 4 * 1024**3


class ValidationResult(StrictModel):
    is_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]


def validate_metadata(
    meta: VideoMetadata, limits: ValidationLimits | None = None
) -> ValidationResult:
    lim = limits or ValidationLimits()
    issues: list[ValidationIssue] = []

    def add(code: str, sev: Severity, msg: str) -> None:
        issues.append(ValidationIssue(code=code, severity=sev, message=msg))

    if meta.codec and meta.codec not in SUPPORTED_CODECS:
        add("unsupported_codec", Severity.ERROR, f"Video codec '{meta.codec}' is not supported.")

    if meta.duration_s is None:
        add("unknown_duration", Severity.WARNING, "Video duration could not be determined.")
    elif meta.duration_s < lim.min_duration_s:
        add("too_short", Severity.ERROR, f"Video is shorter than {lim.min_duration_s} s.")
    elif meta.duration_s > lim.max_duration_s:
        add("too_long", Severity.ERROR, f"Video is longer than {lim.max_duration_s / 60:.0f} min.")

    short_side = min(meta.display_width, meta.display_height)
    if short_side < lim.min_short_side_px:
        add(
            "resolution_too_low",
            Severity.ERROR,
            f"Resolution {meta.display_width}x{meta.display_height} is below the "
            f"minimum short side of {lim.min_short_side_px}px.",
        )
    elif short_side < lim.recommended_short_side_px:
        add(
            "resolution_low",
            Severity.WARNING,
            "Resolution below 720p: distal joints (wrist) and the racket may be unreliable.",
        )

    fps = meta.effective_fps
    if fps is None:
        add("unknown_fps", Severity.ERROR, "Frame rate could not be determined.")
    elif fps < lim.min_fps:
        add("fps_too_low", Severity.ERROR, f"Frame rate {fps:.1f} fps is below {lim.min_fps}.")
    elif fps < lim.recommended_fps:
        add(
            "fps_low",
            Severity.WARNING,
            f"Frame rate {fps:.1f} fps: contact timing and fast swing phases will be "
            "temporally coarse (50-60 fps or higher is recommended).",
        )

    if meta.is_variable_frame_rate:
        add(
            "variable_frame_rate",
            Severity.WARNING,
            "Variable frame rate detected; preprocessing converts it to a constant rate.",
        )

    if meta.file_size_bytes is not None and meta.file_size_bytes > lim.max_file_size_bytes:
        add("file_too_large", Severity.ERROR, "File exceeds the maximum allowed size.")

    return ValidationResult(
        is_valid=not any(i.severity is Severity.ERROR for i in issues), issues=issues
    )
