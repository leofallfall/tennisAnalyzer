"""Schema of the AI interpretation result (``analysis_result.json``).

This is the single contract between any AI adapter (Claude Code today, the Anthropic API
later) and the application. It is model-independent: the adapter only changes *who*
writes the file, never its shape or how it is validated.

Structural rules live here (types, ranges, internal consistency). Rules that need the
Evidence Pack (do the stroke ids exist? are the cited angles in the data?) live in
:mod:`tennis_api.ai.validation`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from tennis_cv.biomechanics.layers import IssueNature
from tennis_cv.common import StrictModel, TimeInterval
from tennis_cv.strokes.base import PhaseName

RESULT_SCHEMA_VERSION = "1.0"
RESULT_FILENAME = "analysis_result.json"
WHOLE_INTERVAL = "whole_interval"


class ResultKind(StrEnum):
    SESSION = "session"
    SINGLE_STROKE = "single_stroke"


class IssueStrokeType(StrEnum):
    FOREHAND = "forehand"
    BACKHAND = "backhand"
    BOTH = "both"
    UNKNOWN = "unknown"


class Relevance(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceKind(StrEnum):
    IMAGE = "image"
    MEASUREMENT = "measurement"
    STROKE = "stroke"
    AGGREGATE = "aggregate"
    DATA_QUALITY = "data_quality"
    TIMESERIES = "timeseries"


class EvidenceRef(StrictModel):
    kind: EvidenceKind
    ref: str = Field(
        min_length=1,
        description="Image id, measurement id, stroke id, metric id (aggregate) or file "
        "section the claim rests on. Must exist in the Evidence Pack.",
    )
    note: str | None = None


class Exercise(StrictModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1, description="Setup and execution")
    cue: str | None = Field(default=None, description="One short focus cue")
    dosage: str | None = Field(default=None, description="Sets / repetitions / duration")
    how_to_verify: str | None = Field(
        default=None, description="What to film next time and which metric should change"
    )


class Issue(StrictModel):
    id: str = Field(min_length=1)
    priority: int = Field(ge=1, description="1 = most important. Unique per result.")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1, description="Technical description of the problem")
    stroke_type: IssueStrokeType
    nature: IssueNature
    occurrences: int = Field(ge=0)
    evaluable_strokes: int = Field(
        ge=0, description="Denominator: strokes where the underlying metric is observable"
    )
    percentage: float = Field(ge=0, le=100)
    relevance: Relevance
    confidence: float = Field(ge=0, le=1)
    stroke_ids: list[str] = Field(description="Strokes showing the issue (len = occurrences)")
    timestamps_s: list[float] = Field(
        default_factory=list, description="Representative times on the CFR proxy timeline"
    )
    observations: list[str] = Field(
        min_length=1, description="Facts restated from the pack data. No new numbers."
    )
    evidence: list[EvidenceRef] = Field(min_length=1)
    biomechanical_explanation: str = Field(
        min_length=1, description="Interpretation. Clearly a hypothesis when not certain."
    )
    alternative_explanations: list[str] = Field(default_factory=list)
    recommended_exercises: list[Exercise] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Issue:
        if self.occurrences > self.evaluable_strokes:
            raise ValueError("occurrences cannot exceed evaluable_strokes")
        if self.evaluable_strokes == 0:
            if self.percentage != 0:
                raise ValueError("percentage must be 0 when evaluable_strokes is 0")
        else:
            expected = 100 * self.occurrences / self.evaluable_strokes
            if abs(expected - self.percentage) > 0.6:
                raise ValueError(
                    f"percentage {self.percentage} does not match "
                    f"{self.occurrences}/{self.evaluable_strokes} = {expected:.1f}"
                )
        if len(self.stroke_ids) != self.occurrences:
            raise ValueError("stroke_ids must list exactly one id per occurrence")
        if len(set(self.stroke_ids)) != len(self.stroke_ids):
            raise ValueError("stroke_ids must be unique")
        if (
            self.confidence < 0.8
            and self.nature is IssueNature.TECHNICAL_ERROR
            and not self.alternative_explanations
        ):
            raise ValueError("medium/low-confidence technical errors need alternative_explanations")
        return self


class PositiveExecution(StrictModel):
    stroke_id: str = Field(min_length=1)
    stroke_type: IssueStrokeType
    timestamp_s: float | None = Field(default=None, ge=0)
    description: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)


class Recommendation(StrictModel):
    priority: int = Field(ge=1)
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    addresses_issue_ids: list[str] = Field(min_length=1)
    exercises: list[Exercise] = Field(default_factory=list)


class EvaluatedStrokes(StrictModel):
    detected: int = Field(ge=0, description="Strokes present in the Evidence Pack")
    evaluable: int = Field(ge=0, description="Strokes with enough observable data to judge")
    forehand: int = Field(ge=0)
    backhand: int = Field(ge=0)
    unknown: int = Field(ge=0)

    @model_validator(mode="after")
    def _sums(self) -> EvaluatedStrokes:
        if self.evaluable > self.detected:
            raise ValueError("evaluable cannot exceed detected")
        if self.forehand + self.backhand + self.unknown != self.detected:
            raise ValueError("forehand + backhand + unknown must equal detected")
        return self


class StrokeTypeAnalysis(StrictModel):
    stroke_type: Literal["forehand", "backhand"]
    detected: int = Field(ge=0)
    evaluable: int = Field(ge=0)
    summary: str = Field(
        min_length=1, description="Separate assessment; say so when there is no data"
    )
    issue_ids: list[str] = Field(default_factory=list)


class PhaseAnalysis(StrictModel):
    phase: PhaseName | Literal["whole_interval"]
    observed: list[str] = Field(
        default_factory=list, description="Only what the data shows, with references"
    )
    hypotheses: list[str] = Field(
        default_factory=list, description="Interpretations that the data does not prove"
    )
    not_observable: list[str] = Field(
        default_factory=list, description="What could not be assessed in this phase and why"
    )
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ResultTarget(StrictModel):
    """Single-stroke analyses only: which stroke/interval was analysed."""

    video_id: str
    stroke_id: str
    interval: TimeInterval


class AnalysisResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: ResultKind
    session_id: str = Field(min_length=1, description="Analysis id the pack was built for")
    pack_id: str = Field(min_length=1, description="Copied from manifest.json")
    analysis_version: str = Field(
        min_length=1, description="Version of the instructions used, from manifest.json"
    )
    adapter: str = Field(min_length=1, description="e.g. 'claude_code'")
    model: str = Field(min_length=1, description="Model that produced the analysis")
    generated_at: datetime
    language: str = Field(default="it", min_length=2)
    summary: str = Field(min_length=1)
    evaluated_strokes: EvaluatedStrokes
    by_stroke_type: list[StrokeTypeAnalysis] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    positive_executions: list[PositiveExecution] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, description="Overall confidence of the analysis")
    insufficient_evidence: bool = Field(
        description="True when the pack does not support technical conclusions"
    )
    target: ResultTarget | None = None
    phases: list[PhaseAnalysis] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> AnalysisResult:
        ids = [i.id for i in self.issues]
        if len(set(ids)) != len(ids):
            raise ValueError("issue ids must be unique")
        priorities = [i.priority for i in self.issues]
        if len(set(priorities)) != len(priorities):
            raise ValueError("issue priorities must be unique")
        known = set(ids)
        for rec in self.recommendations:
            missing = set(rec.addresses_issue_ids) - known
            if missing:
                raise ValueError(f"recommendation addresses unknown issues: {sorted(missing)}")
        for sta in self.by_stroke_type:
            missing = set(sta.issue_ids) - known
            if missing:
                raise ValueError(f"by_stroke_type references unknown issues: {sorted(missing)}")
        if self.kind is ResultKind.SINGLE_STROKE and self.target is None:
            raise ValueError("single_stroke results need a target")
        if self.kind is ResultKind.SESSION and (self.target is not None or self.phases):
            raise ValueError("session results have no target or phases")
        if self.insufficient_evidence and not self.limitations:
            raise ValueError("explain in limitations why the evidence is insufficient")
        return self
