"""Honest declaration of what the engine can do today. Surfaced by GET /api/system/info."""

from __future__ import annotations

from tennis_cv.common import ModuleStatus, StrictModel


class Capability(StrictModel):
    module: str
    status: ModuleStatus
    description: str


CAPABILITIES: list[Capability] = [
    Capability(
        module="video",
        status=ModuleStatus.IMPLEMENTED,
        description="ffprobe metadata, validation, rotation/VFR-normalising proxy, frames",
    ),
    Capability(
        module="quality",
        status=ModuleStatus.EXPERIMENTAL,
        description="Resolution, frame rate, blur, lighting, pose-based visibility (uncalibrated)",
    ),
    Capability(
        module="pose",
        status=ModuleStatus.EXPERIMENTAL,
        description="MediaPipe Pose Landmarker baseline, 33 2D keypoints, skeleton overlay video",
    ),
    Capability(
        module="biomechanics",
        status=ModuleStatus.EXPERIMENTAL,
        description="Metric catalogue and per-frame 2D projected joint angles; no stroke metrics",
    ),
    Capability(
        module="detection",
        status=ModuleStatus.NOT_IMPLEMENTED,
        description="Scene/camera change and player detection",
    ),
    Capability(
        module="strokes",
        status=ModuleStatus.EXPERIMENTAL,
        description="Candidate stroke events from wrist speed peaks with provisional states "
        "(not validated); no forehand/backhand classification or phase segmentation",
    ),
    Capability(
        module="racket",
        status=ModuleStatus.NOT_IMPLEMENTED,
        description="Racket detection/tracking",
    ),
    Capability(module="ball", status=ModuleStatus.NOT_IMPLEMENTED, description="Ball detection"),
    Capability(
        module="ai_interpretation",
        status=ModuleStatus.EXPERIMENTAL,
        description="Evidence Packs + validated import of results written by Claude Code "
        "(apps/api, personal mode). The app makes no LLM calls; no stroke data yet.",
    ),
    Capability(
        module="comparison",
        status=ModuleStatus.NOT_IMPLEMENTED,
        description="Execution comparison, normalisation, camera compatibility",
    ),
]
