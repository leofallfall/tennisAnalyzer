from __future__ import annotations

import pytest
from pydantic import ValidationError

from tennis_cv.biomechanics import (
    METRIC_CATALOGUE,
    Measurement,
    projected_angle_deg,
    projected_joint_angle,
)
from tennis_cv.biomechanics.metrics import MetricStatus
from tennis_cv.common import Observability
from tennis_cv.pose.models import Keypoint, PoseFrame


def _pose(vis: float = 0.9, detected: bool = True) -> PoseFrame:
    # Right angle at the elbow in pixel space for a 200x100 image:
    # shoulder (100,0) elbow (100,50) wrist (150,50)
    kps = [
        Keypoint(name="right_shoulder", x=0.5, y=0.0, visibility=vis),
        Keypoint(name="right_elbow", x=0.5, y=0.5, visibility=vis),
        Keypoint(name="right_wrist", x=0.75, y=0.5, visibility=vis),
    ]
    return PoseFrame(
        frame_index=3, timestamp_s=0.1, detected=detected, keypoints=kps if detected else []
    )


JOINTS = ("right_shoulder", "right_elbow", "right_wrist")


def test_projected_angle() -> None:
    assert projected_angle_deg((1, 0), (0, 0), (0, 1)) == pytest.approx(90)
    assert projected_angle_deg((1, 0), (0, 0), (-1, 0)) == pytest.approx(180)
    assert projected_angle_deg((0, 0), (0, 0), (1, 1)) is None


def test_joint_angle_uses_pixel_aspect_ratio() -> None:
    m = projected_joint_angle(
        _pose(), JOINTS, metric_id="elbow_angle_projected", image_size=(200, 100)
    )
    assert m.value == pytest.approx(90)
    assert m.observability is Observability.OBSERVABLE
    assert m.unit == "deg" and m.frame_index == 3 and m.confidence == pytest.approx(0.9)
    assert m.limitations


def test_low_visibility_is_not_observable_not_guessed() -> None:
    m = projected_joint_angle(_pose(vis=0.2), JOINTS, metric_id="e", image_size=(200, 100))
    assert m.value is None and m.observability is Observability.NOT_OBSERVABLE


def test_no_pose_is_not_observable() -> None:
    m = projected_joint_angle(_pose(detected=False), JOINTS, metric_id="e", image_size=(1, 1))
    assert m.value is None and m.observability is Observability.NOT_OBSERVABLE


def test_measurement_rejects_value_when_not_observable() -> None:
    with pytest.raises(ValidationError):
        Measurement(
            metric_id="x",
            value=1.0,
            unit="deg",
            method="m",
            observability=Observability.NOT_OBSERVABLE,
        )
    with pytest.raises(ValidationError):
        Measurement(
            metric_id="x",
            value=None,
            unit="deg",
            method="m",
            observability=Observability.OBSERVABLE,
        )


def test_catalogue_is_honest_about_status() -> None:
    assert all(m.status is not MetricStatus.VALIDATED for m in METRIC_CATALOGUE.values())
    implemented = {k for k, m in METRIC_CATALOGUE.items() if m.status is MetricStatus.IMPLEMENTED}
    assert implemented == {"elbow_angle_projected"}
