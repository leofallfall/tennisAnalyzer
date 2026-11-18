"""Ball interfaces.

Status: NOT IMPLEMENTED. A tennis ball is a few pixels wide and heavily blurred in
smartphone footage; detection will be attempted only where feasible and must
report non-observability rather than guess.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tennis_cv.common import Reliability, StrictModel
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.video.frames import Frame


class BallDetection(StrictModel):
    frame_index: int
    timestamp_s: float
    center: tuple[float, float] | None = None  # normalised; None when not observed
    radius_px: float | None = None
    reliability: Reliability


class BallDetector(ABC):
    @abstractmethod
    def detect(self, frame: Frame) -> BallDetection: ...


class NotImplementedBallDetector(BallDetector):
    def detect(self, frame: Frame) -> BallDetection:
        raise NotYetImplementedError("Ball detection is not implemented yet.")
