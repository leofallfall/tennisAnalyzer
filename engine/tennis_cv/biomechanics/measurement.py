"""The Measurement record: every number the system reports has this shape."""

from __future__ import annotations

from pydantic import Field, model_validator

from tennis_cv.common import Observability, StrictModel, TimeInterval


class Measurement(StrictModel):
    metric_id: str = Field(description="Key in METRIC_CATALOGUE")
    value: float | None = Field(description="None when the metric could not be evaluated")
    unit: str
    interval: TimeInterval | None = None
    frame_index: int | None = None
    stroke_id: str | None = None
    body_part: str | None = Field(default=None, description="Joint/segment or 'racket'/'ball'")
    confidence: float | None = Field(default=None, ge=0, le=1)
    observability: Observability
    method: str = Field(description="How the value was computed (algorithm + version)")
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _value_matches_observability(self) -> Measurement:
        unobservable = {Observability.NOT_OBSERVABLE, Observability.NOT_EVALUATED}
        if self.observability in unobservable and self.value is not None:
            raise ValueError("A non-observable measurement must not carry a value")
        if self.observability not in unobservable and self.value is None:
            raise ValueError("An observable measurement must carry a value")
        return self
