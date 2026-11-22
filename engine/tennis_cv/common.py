"""Shared reliability vocabulary used by every engine module."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Observability(StrEnum):
    """Whether a quantity could actually be observed in the footage.

    ``NOT_OBSERVABLE`` is a valid analysis outcome, not a failure.
    """

    OBSERVABLE = "observable"
    PARTIALLY_OBSERVABLE = "partially_observable"
    NOT_OBSERVABLE = "not_observable"
    NOT_EVALUATED = "not_evaluated"


class ModuleStatus(StrEnum):
    IMPLEMENTED = "implemented"
    EXPERIMENTAL = "experimental"
    NOT_IMPLEMENTED = "not_implemented"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class TimeInterval(StrictModel):
    """Closed interval in seconds.

    Engine times are on the CFR proxy timeline (``frame / fps``) unless the artifact that
    holds them declares another basis (e.g. ``time_basis`` in ``strokes.json``).
    """

    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> TimeInterval:
        if self.end_s < self.start_s:
            raise ValueError("end_s must be >= start_s")
        return self


class Reliability(StrictModel):
    """Reliability metadata attached to anything the engine reports."""

    observability: Observability
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="0-1 confidence. None when no calibrated confidence exists.",
    )
    limitations: list[str] = Field(default_factory=list)
