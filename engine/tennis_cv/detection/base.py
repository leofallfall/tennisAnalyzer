"""Scene/camera change detection and player localisation interfaces.

Status: NOT IMPLEMENTED. Session videos contain cuts, camera re-positioning and
changes of player side; downstream comparisons must not mix segments with different
camera geometry. Implementations will arrive with the session-analysis milestone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from tennis_cv.common import StrictModel, TimeInterval
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.video.frames import Frame


class SceneSegment(StrictModel):
    """A time range with approximately constant camera position and framing."""

    interval: TimeInterval
    reason_started: str  # e.g. "video_start", "hard_cut", "camera_moved"


class BoundingBox(StrictModel):
    x: float
    y: float
    width: float
    height: float
    confidence: float | None = None


class SceneDetector(ABC):
    @abstractmethod
    def detect(self, video_path: str | Path) -> list[SceneSegment]: ...


class PlayerDetector(ABC):
    @abstractmethod
    def detect(self, frame: Frame) -> list[BoundingBox]: ...


class NotImplementedSceneDetector(SceneDetector):
    def detect(self, video_path: str | Path) -> list[SceneSegment]:
        raise NotYetImplementedError("Scene change detection is not implemented yet.")
