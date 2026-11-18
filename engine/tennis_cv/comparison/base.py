"""Comparison interfaces.

Status: NOT IMPLEMENTED. Before comparing two executions the system must assess
whether they are comparable (camera angle, distance, stroke type, movement
condition). The camera metadata model is defined now because it is persisted
with every video from the start.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.pose.models import PoseSequence


class ViewAngle(StrEnum):
    SIDE_FOREHAND = "side_forehand"  # camera on the player's forehand side
    SIDE_BACKHAND = "side_backhand"
    FRONT = "front"  # facing the player (across the net)
    BEHIND = "behind"  # baseline view from behind
    DIAGONAL = "diagonal"
    UNKNOWN = "unknown"


class CameraView(StrictModel):
    view_angle: ViewAngle = ViewAngle.UNKNOWN
    camera_height: str | None = Field(default=None, description="e.g. 'waist', 'head', 'above'")
    approx_distance_m: float | None = Field(default=None, ge=0)
    is_static: bool | None = None
    declared_by_user: bool = True


class CompatibilityAssessment(StrictModel):
    comparable: bool
    reasons: list[str] = Field(default_factory=list)
    comparable_metrics: list[str] = Field(default_factory=list)


class PoseNormalizer(ABC):
    """Removes scale/translation (and later perspective) differences between executions."""

    @abstractmethod
    def normalize(self, poses: PoseSequence) -> PoseSequence: ...


class ExecutionComparator(ABC):
    @abstractmethod
    def assess_compatibility(
        self, a_view: CameraView, b_view: CameraView
    ) -> CompatibilityAssessment: ...


class NotImplementedComparator(ExecutionComparator):
    def assess_compatibility(
        self, a_view: CameraView, b_view: CameraView
    ) -> CompatibilityAssessment:
        raise NotYetImplementedError("Execution comparison is not implemented yet.")
