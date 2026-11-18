"""Unimplemented modules must refuse to produce results rather than fake them."""

from __future__ import annotations

import numpy as np
import pytest

from tennis_cv.ball.base import NotImplementedBallDetector
from tennis_cv.capabilities import CAPABILITIES
from tennis_cv.common import ModuleStatus
from tennis_cv.comparison.base import CameraView, NotImplementedComparator
from tennis_cv.detection.base import NotImplementedSceneDetector
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.pose.models import PoseSequence
from tennis_cv.racket.base import NotImplementedRacketDetector
from tennis_cv.strokes.base import NotImplementedStrokeDetector
from tennis_cv.video.frames import Frame

FRAME = Frame(index=0, timestamp_s=0.0, image=np.zeros((4, 4, 3), np.uint8))
POSES = PoseSequence(
    estimator="x", model="x", image_width=1, image_height=1, fps=30, frame_stride=1, frames=[]
)


@pytest.mark.parametrize(
    "call",
    [
        lambda: NotImplementedSceneDetector().detect("x.mp4"),
        lambda: NotImplementedRacketDetector().detect(FRAME),
        lambda: NotImplementedBallDetector().detect(FRAME),
        lambda: NotImplementedStrokeDetector().detect(POSES),
        lambda: NotImplementedComparator().assess_compatibility(CameraView(), CameraView()),
    ],
)
def test_not_implemented_modules_raise(call: object) -> None:
    with pytest.raises(NotYetImplementedError):
        call()  # type: ignore[operator]


def test_capabilities_mark_unimplemented_modules() -> None:
    status = {c.module: c.status for c in CAPABILITIES}
    for module in ("strokes", "racket", "ball", "comparison", "detection"):
        assert status[module] is ModuleStatus.NOT_IMPLEMENTED
