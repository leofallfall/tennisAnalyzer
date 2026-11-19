"""Compare manual labels with detected stroke events, one clip at a time.

No stroke detector exists yet (M2.1): the events can come from a ``strokes.json`` written
by hand, which is how this module is tested. Only the fields needed for the comparison
are read, so the future detector output (M2.3) can be compared without changes.

Matching rule (documented in docs/M2_1_DATASET.md):

1. Candidate pairs are (event, label of the analysed player with a *target* stroke type)
   with ``|event.peak_frame - label.proxy_frame| / fps <= tolerance_s``.
2. Pairs are assigned one-to-one, smallest time difference first (ties: earlier label,
   then earlier event). A matched event is a true positive; an unmatched target label is
   a miss.
3. Each remaining event is matched the same way against the remaining labels to give the
   false positive a cause (non-target stroke of the analysed player, another player's
   stroke, a labelled non-stroke movement); events matching nothing are ``unlabelled``.

The tolerance is a parameter, not a validated value.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tennis_cv.common import StrictModel
from tennis_cv.evaluation.dataset import (
    ANALYSED_PLAYER,
    DatasetFormatError,
    Label,
    LabelStrokeType,
)

DEFAULT_TOLERANCE_S = 0.25
"""Initial proposal from the M2.0 design. NOT validated: change it with --tolerance-s."""

DEFAULT_TARGETS: frozenset[LabelStrokeType] = frozenset(
    {LabelStrokeType.FOREHAND, LabelStrokeType.BACKHAND}
)


class EventType(StrEnum):
    FOREHAND = "forehand"
    BACKHAND = "backhand"
    UNKNOWN = "unknown"


class EventStatus(StrEnum):
    CLASSIFIED = "classified"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


class DetectedEvent(BaseModel):
    """The part of a detected event the comparison needs. Other fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    peak_frame: int = Field(ge=0)
    stroke_type: EventType = EventType.UNKNOWN
    status: EventStatus = EventStatus.CLASSIFIED


class ResultsFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[DetectedEvent]


class FalsePositiveCause(StrEnum):
    NON_TARGET_STROKE = "non_target_stroke"
    OTHER_PLAYER = "other_player"
    NOT_A_STROKE = "not_a_stroke"
    UNLABELLED = "unlabelled"


class MatchedPair(StrictModel):
    label_line: int
    label_frame: int
    event_frame: int
    error_ms: float = Field(description="(event - label) / fps, signed")
    true_type: LabelStrokeType
    predicted_type: EventType
    status: EventStatus


class FalsePositive(StrictModel):
    event_frame: int
    cause: FalsePositiveCause
    label_line: int | None = None


class Miss(StrictModel):
    label_line: int
    label_frame: int
    true_type: LabelStrokeType
    visibility: str


class ClipMatch(StrictModel):
    clip_id: str
    fps: float
    tolerance_s: float
    targets: list[LabelStrokeType]
    events_considered: int
    events_rejected: int
    matched: list[MatchedPair]
    false_positives: list[FalsePositive]
    misses: list[Miss]


def load_results(path: str | Path) -> list[DetectedEvent]:
    p = Path(path)
    if not p.is_file():
        raise DatasetFormatError(
            "Results file not found", details={"problems": [f"{p.name}: file does not exist"]}
        )
    try:
        return ResultsFile.model_validate(json.loads(p.read_text(encoding="utf-8"))).events
    except (ValueError, ValidationError) as exc:
        raise DatasetFormatError(
            "Results file is invalid", details={"problems": [f"{p.name}: {exc}"]}
        ) from exc


def _greedy(
    events: list[DetectedEvent], labels: list[Label], max_frames: float
) -> list[tuple[DetectedEvent, Label]]:
    pairs = sorted(
        (
            (abs(e.peak_frame - lb.proxy_frame), lb.proxy_frame, e.peak_frame, ei, li)
            for ei, e in enumerate(events)
            for li, lb in enumerate(labels)
            if abs(e.peak_frame - lb.proxy_frame) <= max_frames
        ),
    )
    used_e: set[int] = set()
    used_l: set[int] = set()
    out: list[tuple[DetectedEvent, Label]] = []
    for _d, _lf, _ef, ei, li in pairs:
        if ei in used_e or li in used_l:
            continue
        used_e.add(ei)
        used_l.add(li)
        out.append((events[ei], labels[li]))
    return out


def match_clip(
    clip_id: str,
    labels: list[Label],
    events: list[DetectedEvent],
    fps: float,
    *,
    tolerance_s: float = DEFAULT_TOLERANCE_S,
    targets: frozenset[LabelStrokeType] = DEFAULT_TARGETS,
) -> ClipMatch:
    if fps <= 0:
        raise ValueError("fps must be positive")
    if tolerance_s < 0:
        raise ValueError("tolerance_s must be >= 0")
    if LabelStrokeType.NONE in targets:
        raise ValueError("'none' marks non-strokes and cannot be a target")
    own = [lb for lb in labels if lb.clip_id == clip_id]
    considered = [e for e in events if e.status is not EventStatus.REJECTED]
    max_frames = tolerance_s * fps + 1e-9

    target_labels = [lb for lb in own if lb.player == ANALYSED_PLAYER and lb.stroke_type in targets]
    tp = _greedy(considered, target_labels, max_frames)
    matched_events = {id(e) for e, _ in tp}
    matched_labels = {id(lb) for _, lb in tp}

    rest_events = [e for e in considered if id(e) not in matched_events]
    target_ids = {id(lb) for lb in target_labels}
    other_labels = [lb for lb in own if id(lb) not in target_ids]
    explained = _greedy(rest_events, other_labels, max_frames)
    cause_of: dict[int, tuple[FalsePositiveCause, int]] = {}
    for e, lb in explained:
        if lb.stroke_type is LabelStrokeType.NONE:
            cause = FalsePositiveCause.NOT_A_STROKE
        elif lb.player != ANALYSED_PLAYER:
            cause = FalsePositiveCause.OTHER_PLAYER
        else:
            cause = FalsePositiveCause.NON_TARGET_STROKE
        cause_of[id(e)] = (cause, lb.line)

    false_positives = []
    for e in rest_events:
        cause, line = cause_of.get(id(e), (FalsePositiveCause.UNLABELLED, None))
        false_positives.append(
            FalsePositive(event_frame=e.peak_frame, cause=cause, label_line=line)
        )

    return ClipMatch(
        clip_id=clip_id,
        fps=fps,
        tolerance_s=tolerance_s,
        targets=sorted(targets),
        events_considered=len(considered),
        events_rejected=len(events) - len(considered),
        matched=sorted(
            (
                MatchedPair(
                    label_line=lb.line,
                    label_frame=lb.proxy_frame,
                    event_frame=e.peak_frame,
                    error_ms=round((e.peak_frame - lb.proxy_frame) / fps * 1000, 3),
                    true_type=lb.stroke_type,
                    predicted_type=e.stroke_type,
                    status=e.status,
                )
                for e, lb in tp
            ),
            key=lambda m: m.label_frame,
        ),
        false_positives=sorted(false_positives, key=lambda f: f.event_frame),
        misses=[
            Miss(
                label_line=lb.line,
                label_frame=lb.proxy_frame,
                true_type=lb.stroke_type,
                visibility=lb.visibility.value,
            )
            for lb in sorted(target_labels, key=lambda x: x.proxy_frame)
            if id(lb) not in matched_labels
        ],
    )
