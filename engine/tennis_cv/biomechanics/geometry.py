"""2D geometry on pose keypoints. Values are *projected* (image-plane) quantities."""

from __future__ import annotations

import math

from tennis_cv.biomechanics.measurement import Measurement
from tennis_cv.common import Observability
from tennis_cv.pose.models import PoseFrame

METHOD_VERSION = "projected_joint_angle/v1"


def projected_angle_deg(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float | None:
    """Angle ABC in degrees (0-180) in the image plane; None if degenerate."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return None
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def projected_joint_angle(
    pose: PoseFrame,
    joints: tuple[str, str, str],
    *,
    metric_id: str,
    image_size: tuple[int, int],
    min_visibility: float = 0.6,
    stroke_id: str | None = None,
) -> Measurement:
    """Measure angle at ``joints[1]`` with explicit observability.

    ``image_size`` is (width, height): normalised coordinates must be rescaled to
    pixels first, otherwise non-square frames distort angles.
    """
    w, h = image_size
    base = {
        "metric_id": metric_id,
        "unit": "deg",
        "frame_index": pose.frame_index,
        "stroke_id": stroke_id,
        "body_part": joints[1],
        "method": f"{METHOD_VERSION}: 2D angle on pixel-scaled pose keypoints",
        "limitations": [
            "2D projection: out-of-plane motion changes the apparent angle.",
            "Confidence is the minimum landmark visibility score, not a calibrated accuracy.",
        ],
    }
    if not pose.detected:
        return Measurement(value=None, observability=Observability.NOT_OBSERVABLE, **base)
    by_name = {k.name: k for k in pose.keypoints}
    kps = [by_name.get(j) for j in joints]
    if any(k is None for k in kps):
        return Measurement(value=None, observability=Observability.NOT_OBSERVABLE, **base)
    vis = [k.visibility if k and k.visibility is not None else 0.0 for k in kps]
    min_vis = min(vis)
    if min_vis < min_visibility:
        return Measurement(value=None, observability=Observability.NOT_OBSERVABLE, **base)
    pts = [(k.x * w, k.y * h) for k in kps if k is not None]
    angle = projected_angle_deg(pts[0], pts[1], pts[2])
    if angle is None:
        return Measurement(value=None, observability=Observability.NOT_OBSERVABLE, **base)
    return Measurement(
        value=round(angle, 2),
        confidence=round(min_vis, 3),
        observability=Observability.OBSERVABLE,
        **base,
    )
