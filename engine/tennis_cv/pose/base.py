"""Pose estimator interface. Alternative backends (RTMPose/MMPose) implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType

from tennis_cv.pose.models import PoseFrame
from tennis_cv.video.frames import Frame


class PoseEstimator(ABC):
    name: str
    model: str
    limitations: list[str] = []

    @abstractmethod
    def estimate(self, frame: Frame) -> PoseFrame:
        """Estimate the pose of the most prominent person in ``frame``.

        Frames must be passed in increasing timestamp order (tracking estimators
        rely on temporal continuity). When no person is found the result has
        ``detected=False`` and no keypoints.
        """

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release native resources."""

    def __enter__(self) -> PoseEstimator:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
