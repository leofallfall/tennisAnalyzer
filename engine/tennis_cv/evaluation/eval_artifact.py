"""``strokes_eval.json`` 1.0: association of detector events with manual labels (M2.3).

Written by ``eval_strokes.py evaluate``, never by the detector (ADR-022): ``strokes.json``
stays free of label information and is referenced here by clip, event id and sha256.

For every clip and every radius the file keeps **all** event-label edges within the
radius (target graph and second graph), the connected components and the outcomes; the
components and outcomes can be rebuilt from the edges alone. Times are seconds on the
clip's time basis, rounded to 4 decimals (0.1 ms) when written. Nothing in this file is
a threshold or an accepted quality level: radii and the search limit are exploratory
parameters recorded in ``params``.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tennis_cv.evaluation.association import AmbiguityReason
from tennis_cv.evaluation.clip_time import FrameState
from tennis_cv.evaluation.dataset import ImpactVisibility, LabelStrokeType, OverlayWrist
from tennis_cv.signals.timebase import MapStatus, TimeBasis
from tennis_cv.strokes.artifact import EventStatus, Rounded, Wrist

EVAL_SCHEMA_NAME = "tpa.strokes_eval"
EVAL_SCHEMA_VERSION = "1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


class StrokesFormat(StrEnum):
    STROKES_1 = "strokes_1"  # strokes.json 1.x (schema tpa.strokes)
    M2_1_MINIMAL = "m2_1_minimal"  # events[].peak_frame, read as instant events


class ParamsSource(StrEnum):
    STROKES_JSON = "strokes_json"  # params.signals.* of the strokes.json file
    DEFAULTS = "defaults"  # M2.1 minimal format: no params recorded, engine defaults used


class LabelRole(StrEnum):
    TARGET = "target"  # analysed player, stroke type in --targets
    OTHER = "other"  # every other label: second graph only


class ImpactOutcome(StrEnum):
    INTERCEPTED = "intercepted"
    INTERCEPTED_UNCERTAIN = "intercepted_uncertain"
    MISSED = "missed"
    AMBIGUOUS = "ambiguous"


class GroupOutcome(StrEnum):
    INTERCEPTED = "intercepted"
    INTERCEPTED_UNCERTAIN = "intercepted_uncertain"
    MISSED = "missed"
    UNASSOCIATED_EVENT = "unassociated_event"
    AMBIGUOUS = "ambiguous"


class OtherGroupOutcome(StrEnum):
    ASSOCIATED = "associated"
    UNLABELLED = "unlabelled"
    LABEL_ONLY = "label_only"
    AMBIGUOUS = "ambiguous"


class MissCause(StrEnum):
    """Closed list, fixed precedence (first that applies)."""

    NO_POSE = "no_pose"
    BOTH_WRISTS_UNRELIABLE = "both_wrists_unreliable"
    EVENT_REJECTED = "event_rejected"
    PEAK_DISCARDED = "peak_discarded"
    EVENT_OUTSIDE_RADIUS = "event_outside_radius"
    NO_PEAK = "no_peak"


class Position(StrEnum):
    """Where an unassociated event sits with respect to the annotated intervals."""

    INSIDE = "inside"  # inside an interval and farther than r from both edges
    NEAR_BOUNDARY = "near_boundary"  # touches an interval but within r of an edge
    OUTSIDE_ANNOTATED = "outside_annotated"  # outside every interval of the clip
    NO_INTERVALS = "no_intervals"  # the clip has no annotated interval


class Verdict(StrEnum):
    JUDGED = "judged"  # inside an exhaustive interval: automatic cause
    NOT_JUDGEABLE_COVERAGE = "not_judgeable_coverage"  # inside a non-exhaustive interval
    NOT_JUDGED_POSITION = "not_judged_position"  # near_boundary / outside / no intervals


class UnassociatedCause(StrEnum):
    """Automatic cause from the second graph (only with verdict ``judged``)."""

    NON_TARGET_STROKE = "non_target_stroke"  # serve / volley / other / non-target type of A
    NOT_A_STROKE = "not_a_stroke"  # a ``none`` label
    OTHER_PLAYER = "other_player"  # a label of another player: possible tracking error
    UNLABELLED = "unlabelled"  # no label within r
    AMBIGUOUS = "ambiguous"  # several events or labels in the second-graph group


class RadiusStatus(StrEnum):
    OK = "ok"  # 2r + W < D
    NOT_INTERPRETABLE = "not_interpretable"
    NOT_APPLICABLE = "not_applicable"  # fewer than two labels: D undefined


# ------------------------------------------------------------------ header


class EvalProducer(_Model):
    tool: str = "eval_strokes.evaluate"
    engine_version: str


class FileRef(_Model):
    file: str = Field(description="File name only")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvalInputs(_Model):
    manifest: FileRef
    labels: FileRef
    annotated: FileRef | None = Field(description="None when no annotated.csv was given")


class EvalParams(_Model):
    radii_s: list[Rounded] = Field(min_length=1)
    targets: list[LabelStrokeType] = Field(min_length=1)
    target_player: str
    numeric_tolerance_s: float = Field(description="Edge if distance <= r + this")
    time_tolerance_s: float = Field(
        description="Allowed difference between seconds written in strokes.json and t(f)"
    )
    outside_radius_search_s: Rounded = Field(
        description="Search limit of event_outside_radius (diagnosis only, not a threshold)"
    )


# ------------------------------------------------------------------ per clip


class StrokesRef(_Model):
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: StrokesFormat
    schema_version: str | None
    producer: str | None = Field(description="algorithm version, None for the minimal format")


class ParamsUsed(_Model):
    source: ParamsSource
    timebase: dict[str, float]
    good_visibility: float
    usable_visibility: float


class IntervalInfo(_Model):
    line: int
    start_frame: int
    end_frame: int
    start_t_s: Rounded
    end_t_s: Rounded
    exhaustive: bool
    missing_types: list[LabelStrokeType]
    missing_players: str | None


class LabelInfo(_Model):
    line: int = Field(description="Line in labels.csv: the label's id in this file")
    proxy_frame: int
    stroke_type: LabelStrokeType
    player: str
    visibility: ImpactVisibility
    overlay_wrist: OverlayWrist | None
    role: LabelRole
    t_s: Rounded
    frame_state: FrameState
    kinematic_sample: bool
    kept_frame: int | None
    kept_frame_detected: bool | None
    nearest_sample_dt_s: Rounded | None
    estimated_frame: bool = Field(description="visibility = partial: frame estimated by hand")


class EventInfo(_Model):
    id: str
    status: EventStatus
    in_graph: bool = Field(description="accepted / uncertain; rejected events stay out")
    t_s: Rounded | None = Field(description="Instant (representative wrist determined)")
    window_t_s: tuple[Rounded, Rounded]
    window_proxy_frames: tuple[int, int]
    reasons: list[str]


class GapInfo(_Model):
    min_label_gap_s: Rounded | None
    gap_lines: tuple[int, int] | None
    max_event_span_s: Rounded = Field(description="W: actual maximum b - a of graph events")
    max_span_event_id: str | None


class EdgeOut(_Model):
    event_id: str
    label_line: int
    distance_s: Rounded
    signed_s: Rounded = Field(description="event - label")


class GroupOut(_Model):
    id: str
    event_ids: list[str]
    label_lines: list[int]
    ambiguity: list[AmbiguityReason]


class TargetGroup(GroupOut):
    outcome: GroupOutcome


class OtherGroup(GroupOut):
    outcome: OtherGroupOutcome


class PeakOffset(_Model):
    peak_id: str
    wrist: Wrist
    signed_s: Rounded = Field(description="peak - label")
    signed_frames: int = Field(description="peak proxy frame - label proxy frame")


class Interception(_Model):
    event_id: str
    event_status: EventStatus
    event_signed_s: Rounded | None = Field(description="None for a window-only event")
    event_signed_frames: int | None
    peaks: list[PeakOffset]


class NearbyEvent(_Model):
    event_id: str
    distance_s: Rounded
    reasons: list[str]


class NearbyPeak(_Model):
    peak_id: str
    wrist: Wrist
    signed_s: Rounded
    reasons: list[str]


class OutsideRadius(_Model):
    event_id: str
    signed_s: Rounded = Field(description="event - label")
    shared_with: list[int] = Field(
        description="Other missed impacts (label lines) for which this event is the nearest "
        "free one: cited for all of them, assigned to none"
    )


class Miss(_Model):
    cause: MissCause
    rejected_events: list[NearbyEvent] = Field(description="Rejected events within r")
    discarded_peaks: list[NearbyPeak] = Field(description="Discarded peaks within r")
    nearest_free_event: OutsideRadius | None


class ImpactResult(_Model):
    label_line: int
    group_id: str
    outcome: ImpactOutcome
    interception: Interception | None
    miss: Miss | None
    irregular_intervals_in_window: int
    irregular_time_in_window: bool


class NearestTarget(_Model):
    label_line: int
    signed_s: Rounded = Field(description="event - label")


class UnassociatedEvent(_Model):
    event_id: str
    target_group_id: str
    other_group_id: str
    nearest_target: NearestTarget | None
    position: Position
    interval_line: int | None
    verdict: Verdict
    cause: UnassociatedCause | None
    cause_label_line: int | None
    missing_types: list[LabelStrokeType]
    missing_players: str | None


class RadiusResult(_Model):
    radius_s: Rounded
    status: RadiusStatus
    status_reason: str | None
    target_edges: list[EdgeOut]
    target_groups: list[TargetGroup]
    other_edges: list[EdgeOut]
    other_groups: list[OtherGroup]
    impacts: list[ImpactResult]
    unassociated_events: list[UnassociatedEvent]


class Transition(_Model):
    label_line: int
    from_radius_s: Rounded
    from_outcome: ImpactOutcome
    to_radius_s: Rounded
    to_outcome: ImpactOutcome


class ClipEval(_Model):
    clip_id: str
    strokes: StrokesRef
    time_basis: TimeBasis
    map_status: MapStatus
    unverified_time: bool
    params_used: ParamsUsed
    intervals: list[IntervalInfo]
    labels: list[LabelInfo]
    events: list[EventInfo]
    gaps: GapInfo
    radii: list[RadiusResult]
    transitions: list[Transition]
    warnings: list[str]


class StrokesEvalFile(_Model):
    """``strokes_eval.json`` 1.0 (see module docstring)."""

    schema_: str = Field(default=EVAL_SCHEMA_NAME, alias="schema", pattern=r"^tpa\.strokes_eval$")
    schema_version: str = Field(default=EVAL_SCHEMA_VERSION, pattern=r"^1\.0$")
    created_at: datetime = Field(description="The only field ignored by repeatability checks")
    producer: EvalProducer
    inputs: EvalInputs
    params: EvalParams
    clips: list[ClipEval]
    clips_without_results: list[str] = Field(
        description="Manifest clips with no strokes file: listed, never evaluated silently"
    )
    warnings: list[str]


def eval_to_dict(doc: StrokesEvalFile, *, include_created_at: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = doc.model_dump(mode="json", by_alias=True)
    if not include_created_at:
        data.pop("created_at")
    return data


def dump_eval(doc: StrokesEvalFile) -> str:
    """Canonical text: sorted keys, 2-space indentation, final newline."""
    return json.dumps(eval_to_dict(doc), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def same_eval(a: StrokesEvalFile, b: StrokesEvalFile) -> bool:
    """Equal apart from ``created_at`` (repeatability check)."""
    return eval_to_dict(a, include_created_at=False) == eval_to_dict(b, include_created_at=False)
