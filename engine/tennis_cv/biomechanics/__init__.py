"""Biomechanical metric definitions, measurement records and evidence layers.

Implemented: the metric catalogue, the Measurement/Observation/Interpretation/
Recommendation contracts, and 2D projected joint-angle geometry.
Not implemented: stroke-level metric calculators (need stroke detection first).
"""

from tennis_cv.biomechanics.geometry import projected_angle_deg, projected_joint_angle
from tennis_cv.biomechanics.layers import Interpretation, Observation, Recommendation
from tennis_cv.biomechanics.measurement import Measurement
from tennis_cv.biomechanics.metrics import METRIC_CATALOGUE, MetricDefinition

__all__ = [
    "METRIC_CATALOGUE",
    "Interpretation",
    "Measurement",
    "MetricDefinition",
    "Observation",
    "Recommendation",
    "projected_angle_deg",
    "projected_joint_angle",
]
