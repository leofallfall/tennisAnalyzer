"""Racket interfaces.

Status: NOT IMPLEMENTED. Off-the-shelf detectors (e.g. COCO 'tennis racket')
struggle with motion blur at 30-60 fps; a tennis-specific model or keypoint
approach (head centre, throat, butt cap) will be evaluated experimentally first.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tennis_cv.common import Reliability, StrictModel
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.video.frames import Frame


class RacketDetection(StrictModel):
    frame_index: int
    timestamp_s: float
    head_center: tuple[float, float] | None = None  # normalised coordinates
    handle_end: tuple[float, float] | None = None
    face_angle_deg: float | None = None  # in image plane
    reliability: Reliability


class RacketDetector(ABC):
    @abstractmethod
    def detect(self, frame: Frame) -> RacketDetection: ...


class RacketTracker(ABC):
    @abstractmethod
    def track(self, frames: list[Frame]) -> list[RacketDetection]: ...


class NotImplementedRacketDetector(RacketDetector):
    def detect(self, frame: Frame) -> RacketDetection:
        raise NotYetImplementedError("Racket detection is not implemented yet.")
