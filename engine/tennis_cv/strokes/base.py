"""Stroke-level interfaces.

Status: NOT IMPLEMENTED. Stroke detection is deliberately postponed until video
ingestion and pose estimation are validated on real amateur footage (Milestone 1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import Field

from tennis_cv.common import Reliability, StrictModel, TimeInterval
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.pose.models import PoseSequence


class StrokeType(StrEnum):
    FOREHAND = "forehand"
    BACKHAND = "backhand"
    SERVE = "serve"  # next after MVP
    UNKNOWN = "unknown"


class PhaseName(StrEnum):
    PREPARATION = "preparation"
    BACKSWING = "backswing"
    FORWARD_SWING = "forward_swing"
    CONTACT = "contact"
    FOLLOW_THROUGH = "follow_through"


class StrokeCandidate(StrictModel):
    interval: TimeInterval
    stroke_type: StrokeType = StrokeType.UNKNOWN
    type_confidence: float | None = Field(default=None, ge=0, le=1)
    estimated_contact_s: float | None = None
    reliability: Reliability


class StrokePhase(StrictModel):
    name: PhaseName
    interval: TimeInterval
    boundary_method: str
    user_adjusted: bool = False
    reliability: Reliability


class StrokeDetector(ABC):
    @abstractmethod
    def detect(self, poses: PoseSequence) -> list[StrokeCandidate]: ...


class StrokeClassifier(ABC):
    @abstractmethod
    def classify(
        self, candidate: StrokeCandidate, poses: PoseSequence, dominant_hand: str
    ) -> StrokeCandidate: ...


class PhaseSegmenter(ABC):
    @abstractmethod
    def segment(self, candidate: StrokeCandidate, poses: PoseSequence) -> list[StrokePhase]: ...


class NotImplementedStrokeDetector(StrokeDetector):
    def detect(self, poses: PoseSequence) -> list[StrokeCandidate]:
        raise NotYetImplementedError("Stroke detection is not implemented yet.")


class NotImplementedStrokeClassifier(StrokeClassifier):
    def classify(
        self, candidate: StrokeCandidate, poses: PoseSequence, dominant_hand: str
    ) -> StrokeCandidate:
        raise NotYetImplementedError("Forehand/backhand classification is not implemented yet.")


class NotImplementedPhaseSegmenter(PhaseSegmenter):
    def segment(self, candidate: StrokeCandidate, poses: PoseSequence) -> list[StrokePhase]:
        raise NotYetImplementedError("Stroke phase segmentation is not implemented yet.")
