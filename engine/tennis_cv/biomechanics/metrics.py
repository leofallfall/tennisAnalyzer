"""Catalogue of initial biomechanical metrics (definitions only).

``status`` must stay ``defined`` until a calculator exists *and* has been validated
against manually annotated footage (see docs/BIOMECHANICS.md).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.comparison.base import ViewAngle


class MetricStatus(StrEnum):
    DEFINED = "defined"
    IMPLEMENTED = "implemented"
    VALIDATED = "validated"


class MetricDefinition(StrictModel):
    id: str
    name: str
    unit: str
    description: str
    required_landmarks: list[str]
    suitable_views: list[ViewAngle]
    requires_phase: str | None = None
    limitations: list[str] = Field(default_factory=list)
    status: MetricStatus = MetricStatus.DEFINED


_SIDE = [ViewAngle.SIDE_FOREHAND, ViewAngle.SIDE_BACKHAND]
_TOP_DOWNISH = [ViewAngle.BEHIND, ViewAngle.FRONT]

METRIC_CATALOGUE: dict[str, MetricDefinition] = {
    m.id: m
    for m in [
        MetricDefinition(
            id="elbow_angle_projected",
            name="Hitting-arm elbow angle (2D projected)",
            unit="deg",
            description="Angle shoulder-elbow-wrist of the hitting arm in the image plane.",
            required_landmarks=["shoulder", "elbow", "wrist"],
            suitable_views=[*_SIDE, ViewAngle.FRONT],
            requires_phase="contact",
            limitations=["Foreshortening when the arm points toward/away from the camera."],
            status=MetricStatus.IMPLEMENTED,
        ),
        MetricDefinition(
            id="knee_flexion_projected",
            name="Knee flexion (2D projected)",
            unit="deg",
            description="180 minus the hip-knee-ankle angle in the image plane.",
            required_landmarks=["hip", "knee", "ankle"],
            suitable_views=_SIDE,
            requires_phase="backswing",
            limitations=["Unreliable from behind/front views."],
        ),
        MetricDefinition(
            id="shoulder_hip_separation_projected",
            name="Shoulder-hip separation (2D proxy)",
            unit="deg",
            description="Difference between shoulder-line and hip-line orientation.",
            required_landmarks=["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
            suitable_views=_TOP_DOWNISH,
            requires_phase="backswing",
            limitations=["True axial rotation is 3D; from a side view this is not measurable."],
        ),
        MetricDefinition(
            id="unit_turn_timing",
            name="Preparation (unit turn) timing",
            unit="s",
            description="Time from opponent-ball bounce/stroke start to completed shoulder turn.",
            required_landmarks=["left_shoulder", "right_shoulder"],
            suitable_views=_SIDE + _TOP_DOWNISH,
            requires_phase="preparation",
            limitations=["Needs stroke and ball-event detection; reference event TBD."],
        ),
        MetricDefinition(
            id="contact_distance_body",
            name="Contact distance from body (normalised)",
            unit="torso_lengths",
            description="Horizontal distance hitting wrist to hip centre at contact.",
            required_landmarks=["wrist", "left_hip", "right_hip", "shoulder"],
            suitable_views=_TOP_DOWNISH,
            requires_phase="contact",
            limitations=["Contact frame must be identified; wrist is not the racket face."],
        ),
        MetricDefinition(
            id="contact_height",
            name="Contact height (normalised)",
            unit="torso_lengths",
            description="Vertical wrist position at contact relative to hip centre.",
            required_landmarks=["wrist", "left_hip", "right_hip", "shoulder"],
            suitable_views=_SIDE + _TOP_DOWNISH,
            requires_phase="contact",
        ),
        MetricDefinition(
            id="base_width",
            name="Stance width (normalised)",
            unit="hip_widths",
            description="Ankle-to-ankle distance relative to hip width.",
            required_landmarks=["left_ankle", "right_ankle", "left_hip", "right_hip"],
            suitable_views=_TOP_DOWNISH,
            requires_phase="contact",
        ),
        MetricDefinition(
            id="head_stability",
            name="Head stability through contact",
            unit="torso_lengths",
            description="Nose displacement in a window around contact.",
            required_landmarks=["nose", "shoulder", "hip"],
            suitable_views=_SIDE + _TOP_DOWNISH,
            requires_phase="contact",
            limitations=["Camera motion must be excluded first."],
        ),
    ]
}
