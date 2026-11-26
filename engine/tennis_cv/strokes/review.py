"""``strokes_review.json`` 1.0: the user's corrections of the detector output (V1 step 5).

The detector writes ``strokes.json``; this file holds what the user says about it, and is
never mixed with it (ADR-025, ADR-031):

* **Entries**: one per correction. ``kind = stroke`` says "there is a stroke at ``frame``"
  (a confirmed detector event, a moved one, or one the user added); ``kind = not_stroke``
  says "this detector event is not a stroke" (deleted, with a reason). An entry linked to a
  detector event records the event id, its frame and the detector version at the time of
  the correction.
* **Reviewed stretches**: parts of the video the user went through end to end. Inside them
  every stroke of the player is marked, so they can be exported as exhaustive annotated
  intervals for ``evaluate``.

The user's frame is the truth; the link to the detector is only bookkeeping. When the
detector is run again and the linked event is gone or has moved, the entry is *detached*:
it stays, with its own frame, type and note, and is shown apart. Nothing here is a
measurement and nothing is guessed: a missing type or impact visibility stays missing and
is reported at export.

Frames are proxy frames (``frame / fps`` = app time), as in ``labels.csv``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tennis_cv.errors import TennisCVError
from tennis_cv.evaluation.dataset import ImpactVisibility
from tennis_cv.strokes.artifact import CandidateEvent

SCHEMA: Literal["tpa.strokes_review"] = "tpa.strokes_review"
SCHEMA_VERSION: Literal["1.0"] = "1.0"
REVIEW_FILE = "strokes_review.json"

LINK_TOLERANCE_FRAMES = 3
"""A linked entry still applies to the event with the same id when the event's frame moved
by at most this many frames (a re-run with the same detector gives the same frame)."""

PLAYER = "A"
"""Every corrected stroke belongs to the analysed player (one skeleton, ADR-020)."""

_CLIP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StrokesReviewError(TennisCVError):
    code = "strokes_review_invalid"


class ReviewKind(StrEnum):
    STROKE = "stroke"
    NOT_STROKE = "not_stroke"


class ReviewStrokeType(StrEnum):
    """Types the user sets by hand (ADR-025): forehand, backhand, anything else."""

    FOREHAND = "forehand"
    BACKHAND = "backhand"
    OTHER = "other"


class NotStrokeReason(StrEnum):
    DUPLICATE = "duplicate"
    NOT_A_STROKE = "not_a_stroke"
    OTHER_PERSON = "other_person"
    OUT_OF_FRAME = "out_of_frame"
    OTHER = "other"


TYPE_LABEL: dict[ReviewStrokeType, str] = {
    ReviewStrokeType.FOREHAND: "Dritto",
    ReviewStrokeType.BACKHAND: "Rovescio",
    ReviewStrokeType.OTHER: "Altro",
}

VISIBILITY_LABEL: dict[ImpactVisibility, str] = {
    ImpactVisibility.FULL: "impatto visibile",
    ImpactVisibility.PARTIAL: "impatto visibile in parte",
    ImpactVisibility.NONE: "impatto non visibile",
}

REASON_LABEL: dict[NotStrokeReason, str] = {
    NotStrokeReason.DUPLICATE: "Doppione di un altro colpo",
    NotStrokeReason.NOT_A_STROKE: "Non è un colpo (altro movimento)",
    NotStrokeReason.OTHER_PERSON: "Scheletro su un'altra persona o oggetto",
    NotStrokeReason.OUT_OF_FRAME: "Fuori inquadratura",
    NotStrokeReason.OTHER: "Altro motivo",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventLink(_Model):
    event_id: str = Field(min_length=1, max_length=64)
    event_frame: int = Field(ge=0, description="Event frame when corrected (see event_frame())")
    algorithm_version: str = Field(min_length=1, max_length=64)


class ReviewEntry(_Model):
    id: str = Field(min_length=1, max_length=64)
    kind: ReviewKind
    frame: int = Field(ge=0, description="Proxy frame of the stroke (the user's frame)")
    stroke_type: ReviewStrokeType | None = None
    visibility: ImpactVisibility | None = Field(
        default=None, description="How well the user sees the impact; null = not said"
    )
    reason: NotStrokeReason | None = None
    note: str = Field(default="", max_length=500)
    event: EventLink | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def _consistent(self) -> ReviewEntry:
        if self.kind is ReviewKind.NOT_STROKE:
            if self.reason is None:
                raise ValueError("a deleted event needs a reason")
            if self.stroke_type is not None or self.visibility is not None:
                raise ValueError("a deleted event has no stroke type or visibility")
            if self.event is None:
                raise ValueError("only a detector event can be deleted")
        elif self.reason is not None:
            raise ValueError("a reason is only for deleted events")
        return self


class ReviewedStretch(_Model):
    id: str = Field(min_length=1, max_length=64)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0, description="Included")
    updated_at: datetime

    @model_validator(mode="after")
    def _order(self) -> ReviewedStretch:
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame before start_frame")
        return self


class StrokesReview(_Model):
    schema_: Literal["tpa.strokes_review"] = Field(default=SCHEMA, alias="schema")
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    video_id: str = Field(min_length=1)
    entries: tuple[ReviewEntry, ...] = ()
    reviewed_stretches: tuple[ReviewedStretch, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _unique(self) -> StrokesReview:
        ids = [e.id for e in self.entries] + [s.id for s in self.reviewed_stretches]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate ids")
        linked = [e.event.event_id for e in self.entries if e.event is not None]
        if len(linked) != len(set(linked)):
            raise ValueError("two corrections for the same detector event")
        frames = [e.frame for e in self.entries if e.kind is ReviewKind.STROKE]
        if len(frames) != len(set(frames)):
            raise ValueError("two strokes on the same frame")
        spans = sorted((s.start_frame, s.end_frame) for s in self.reviewed_stretches)
        if any(b[0] <= a[1] for a, b in pairwise(spans)):
            raise ValueError("reviewed stretches overlap")
        return self


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def empty_review(video_id: str) -> StrokesReview:
    return StrokesReview(video_id=video_id)


def load_review(path: Path, video_id: str) -> StrokesReview:
    """The saved corrections, or an empty review when the file does not exist yet."""
    if not path.is_file():
        return empty_review(video_id)
    try:
        doc = StrokesReview.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise StrokesReviewError(
            "strokes_review.json is not valid",
            details={"file": path.name, "problems": [e["msg"] for e in exc.errors()][:10]},
        ) from exc
    if doc.video_id != video_id:
        raise StrokesReviewError("strokes_review.json belongs to another video")
    return doc


def dump_review(doc: StrokesReview) -> str:
    data = doc.model_dump(mode="json", by_alias=True)
    data["entries"] = sorted(data["entries"], key=lambda e: (e["frame"], e["id"]))
    data["reviewed_stretches"] = sorted(data["reviewed_stretches"], key=lambda s: s["start_frame"])
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def save_review(path: Path, doc: StrokesReview) -> None:
    """Atomic write next to strokes.json (never inside it)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dump_review(doc), encoding="utf-8")
    tmp.replace(path)


# --- linking to the detector output -------------------------------------------------------


def event_frame(e: CandidateEvent) -> int:
    """Frame the app shows for an event: reference frame, else window start."""
    ref = e.time.ref_proxy_frame
    return ref if ref is not None else e.time.window_proxy_frames[0]


def linked_event(entry: ReviewEntry, events: Mapping[str, CandidateEvent]) -> CandidateEvent | None:
    """The detector event this entry still applies to, or None (added, or detached)."""
    if entry.event is None:
        return None
    e = events.get(entry.event.event_id)
    if e is None or abs(event_frame(e) - entry.event.event_frame) > LINK_TOLERANCE_FRAMES:
        return None
    return e


Action = Literal["confirmed", "moved", "deleted", "added", "detached"]

ACTION_LABEL: dict[str, str] = {
    "confirmed": "Confermato da te",
    "moved": "Spostato da te",
    "deleted": "Eliminato da te",
    "added": "Aggiunto da te",
    "detached": "Tua correzione (il rilevatore è cambiato)",
}


def action(entry: ReviewEntry, linked: CandidateEvent | None) -> Action:
    if entry.event is None:
        return "added"
    if linked is None:
        return "detached"
    if entry.kind is ReviewKind.NOT_STROKE:
        return "deleted"
    return "confirmed" if entry.frame == event_frame(linked) else "moved"


# --- reviewed stretches ----------------------------------------------------------------------


def add_stretch(
    stretches: Iterable[ReviewedStretch], start: int, end: int
) -> tuple[ReviewedStretch, ...]:
    """Add [start, end]; overlapping or touching stretches are merged into one."""
    lo, hi = min(start, end), max(start, end)
    keep: list[ReviewedStretch] = []
    merged_id: str | None = None
    for s in sorted(stretches, key=lambda s: s.start_frame):
        if s.end_frame + 1 >= lo and s.start_frame <= hi + 1:
            lo, hi = min(lo, s.start_frame), max(hi, s.end_frame)
            merged_id = merged_id or s.id
        else:
            keep.append(s)
    keep.append(
        ReviewedStretch(id=merged_id or new_id(), start_frame=lo, end_frame=hi, updated_at=now())
    )
    return tuple(sorted(keep, key=lambda s: s.start_frame))


# --- export as labels.csv / annotated.csv (docs/M2_1_DATASET.md §4) --------------------------


@dataclass(frozen=True)
class Export:
    labels_csv: str
    annotated_csv: str | None
    exported: int
    left_out: tuple[str, ...]
    """One line per stroke not written as a label, with the reason (also as # comments)."""


def check_clip_id(clip_id: str) -> str:
    if not _CLIP_ID.match(clip_id):
        raise StrokesReviewError(
            "clip_id: letters, digits, '_' or '-' only, at most 64",
            details={"clip_id": clip_id[:80]},
        )
    return clip_id


def export_labels(doc: StrokesReview, clip_id: str) -> Export:
    """``labels.csv`` rows for the user's strokes and ``annotated.csv`` rows for the reviewed
    stretches (``all`` types, player ``A``: everything there was marked by the user).

    Written only when complete and consistent with ``eval_strokes.py check``: a stroke
    without type or impact visibility, or outside the reviewed stretches when there are
    some, is left out and listed as a ``#`` comment (ignored by the loader). Deleted events
    are not written: inside a reviewed stretch an unmatched detector event is already
    judged false; ``none`` labels stay a manual choice.
    """
    check_clip_id(clip_id)
    spans = [(s.start_frame, s.end_frame) for s in doc.reviewed_stretches]

    def inside(frame: int) -> bool:
        return any(a <= frame <= b for a, b in spans)

    rows: list[list[str]] = []
    left_out: list[str] = []
    for e in sorted(doc.entries, key=lambda x: x.frame):
        if e.kind is not ReviewKind.STROKE:
            continue
        missing = [
            name
            for name, value in (("tipo", e.stroke_type), ("visibilità", e.visibility))
            if value is None
        ]
        if missing:
            left_out.append(f"frame {e.frame}: manca {' e '.join(missing)}")
            continue
        if spans and not inside(e.frame):
            left_out.append(f"frame {e.frame}: fuori dai tratti rivisti")
            continue
        assert e.stroke_type is not None and e.visibility is not None
        rows.append(
            [clip_id, str(e.frame), e.stroke_type.value, PLAYER, e.visibility.value, "", e.note]
        )

    out = io.StringIO()
    out.write("# Esportato dall'app (correzioni a mano). Giocatore A = giocatore analizzato.\n")
    for line in left_out:
        out.write(f"# non esportato, {line}\n")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(
        ["clip_id", "proxy_frame", "stroke_type", "player", "visibility", "overlay_wrist", "notes"]
    )
    w.writerows(rows)

    annotated: str | None = None
    if spans:
        a = io.StringIO()
        aw = csv.writer(a, lineterminator="\n")
        aw.writerow(["clip_id", "start_frame", "end_frame", "labelled_types", "players", "notes"])
        for lo, hi in sorted(spans):
            aw.writerow([clip_id, lo, hi, "all", PLAYER, "tratto rivisto nell'app"])
        annotated = a.getvalue()
    return Export(out.getvalue(), annotated, len(rows), tuple(left_out))
