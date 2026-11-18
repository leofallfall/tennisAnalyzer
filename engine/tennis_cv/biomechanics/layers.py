"""The three evidence layers. They are stored separately and never merged.

OBSERVATION     a directly measured/detected fact (references Measurements).
INTERPRETATION  a biomechanical conclusion derived from observations.
RECOMMENDATION  a suggested action, derived from interpretations.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from tennis_cv.biomechanics.measurement import Measurement
from tennis_cv.common import Observability, StrictModel


class IssueNature(StrEnum):
    TECHNICAL_ERROR = "technical_error"
    STYLISTIC_VARIATION = "stylistic_variation"
    UNCERTAIN = "uncertain"


class Observation(StrictModel):
    id: str
    statement: str = Field(description="Factual sentence, no judgement")
    measurements: list[Measurement] = Field(min_length=1)
    evidence_frame_indices: list[int] = Field(default_factory=list)
    observability: Observability


class Interpretation(StrictModel):
    id: str
    statement: str
    based_on_observation_ids: list[str] = Field(min_length=1)
    nature: IssueNature
    confidence: float | None = Field(default=None, ge=0, le=1)
    alternative_explanations: list[str] = Field(default_factory=list)


class Recommendation(StrictModel):
    id: str
    action: str
    drill: str | None = None
    addresses_interpretation_ids: list[str] = Field(min_length=1)
    rationale: str
