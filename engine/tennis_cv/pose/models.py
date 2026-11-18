"""Structured pose output. Coordinates are normalised to the proxy frame size."""

from __future__ import annotations

from pydantic import Field

from tennis_cv.common import StrictModel


class Keypoint(StrictModel):
    name: str
    x: float = Field(description="Normalised x (0=left edge, 1=right edge); may exceed [0,1]")
    y: float = Field(description="Normalised y (0=top, 1=bottom); may exceed [0,1]")
    z: float | None = Field(
        default=None,
        description="Estimator-specific relative depth. NOT a metric depth; do not use "
        "for biomechanical measurement.",
    )
    visibility: float | None = Field(
        default=None, ge=0, le=1, description="Estimator likelihood the joint is visible"
    )
    presence: float | None = Field(
        default=None, ge=0, le=1, description="Estimator likelihood the joint is in frame"
    )


class PoseFrame(StrictModel):
    frame_index: int
    timestamp_s: float
    detected: bool
    keypoints: list[Keypoint] = Field(default_factory=list)


class PoseSequence(StrictModel):
    estimator: str
    model: str
    image_width: int
    image_height: int
    fps: float
    frame_stride: int
    frames: list[PoseFrame]
    limitations: list[str] = Field(default_factory=list)


class PoseSummary(StrictModel):
    frames_analysed: int
    frames_with_pose: int
    detection_rate: float
    mean_keypoint_visibility: dict[str, float | None]
    processing_time_ms: float | None = None
    ms_per_frame: float | None = None


def summarize(seq: PoseSequence, processing_time_ms: float | None = None) -> PoseSummary:
    analysed = len(seq.frames)
    detected = [f for f in seq.frames if f.detected]
    sums: dict[str, list[float]] = {}
    for frame in detected:
        for kp in frame.keypoints:
            if kp.visibility is not None:
                sums.setdefault(kp.name, []).append(kp.visibility)
    names = [kp.name for kp in detected[0].keypoints] if detected else []
    return PoseSummary(
        frames_analysed=analysed,
        frames_with_pose=len(detected),
        detection_rate=(len(detected) / analysed) if analysed else 0.0,
        mean_keypoint_visibility={
            n: (round(sum(sums[n]) / len(sums[n]), 4) if sums.get(n) else None) for n in names
        },
        processing_time_ms=processing_time_ms,
        ms_per_frame=(processing_time_ms / analysed) if processing_time_ms and analysed else None,
    )
