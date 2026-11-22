"""Annotated intervals of the stroke-label dataset: ``annotated.csv``.

An annotated interval says which part of a clip was labelled and *what* was labelled there
exhaustively: which stroke types and which players. It is what lets a later evaluation
judge an unmatched detector event automatically (only inside an exhaustive interval) or
send it to manual review (see the M2.3 evaluation proposal, rule P2).

Format (comment lines start with ``#``, as in ``labels.csv``)::

    clip_id,start_frame,end_frame,labelled_types,players,notes
    img4893,0,1799,forehand;backhand,A,first minute, strokes of A only

* ``start_frame`` / ``end_frame``: proxy frames (``#N`` on overlay.mp4), both included.
  Several rows per clip are allowed if they do not overlap.
* ``labelled_types``: ``;``-separated subset of forehand, backhand, serve, volley, other,
  or ``all`` for all five. ``none`` is not a coverage type: non-stroke movements can never
  be labelled exhaustively.
* ``players``: ``all_visible`` (every person visible in the frame) or ``;``-separated
  capital letters (``A`` = analysed player).

Parsing is strict: every problem is reported with its file and line, nothing is guessed.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.evaluation.dataset import (
    ANALYSED_PLAYER,
    DatasetFormatError,
    Label,
    LabelStrokeType,
    ManifestClip,
)

ANNOTATED_FILE = "annotated.csv"
ANNOTATED_COLUMNS: tuple[str, ...] = (
    "clip_id",
    "start_frame",
    "end_frame",
    "labelled_types",
    "players",
    "notes",
)
ALL_VISIBLE = "all_visible"
ALL_TYPES = "all"
COVERAGE_TYPES: tuple[LabelStrokeType, ...] = (
    LabelStrokeType.FOREHAND,
    LabelStrokeType.BACKHAND,
    LabelStrokeType.SERVE,
    LabelStrokeType.VOLLEY,
    LabelStrokeType.OTHER,
)
_PLAYER = re.compile(r"^[A-Z]$")


class AnnotatedInterval(StrictModel):
    clip_id: str
    start_frame: int = Field(ge=0, description="First proxy frame, included")
    end_frame: int = Field(ge=0, description="Last proxy frame, included")
    labelled_types: frozenset[LabelStrokeType] = Field(
        description="Stroke types labelled exhaustively inside the interval"
    )
    all_visible: bool = Field(description="Every person visible in the frame was labelled")
    players: frozenset[str] = Field(
        default=frozenset(), description="Players labelled exhaustively (empty if all_visible)"
    )
    notes: str = ""
    line: int = Field(ge=1, description="Line in annotated.csv, for error messages")

    def contains(self, frame: int) -> bool:
        return self.start_frame <= frame <= self.end_frame


def _fail(message: str, problems: list[str]) -> DatasetFormatError:
    return DatasetFormatError(message, details={"problems": problems})


def _parse_types(cell: str) -> frozenset[LabelStrokeType]:
    if cell.strip() == ALL_TYPES:
        return frozenset(COVERAGE_TYPES)
    parts = [p.strip() for p in cell.split(";")]
    if not cell.strip() or any(not p for p in parts):
        raise ValueError("labelled_types: empty item (use ';' between types, or 'all')")
    allowed = {t.value: t for t in COVERAGE_TYPES}
    out: set[LabelStrokeType] = set()
    for p in parts:
        if p not in allowed:
            raise ValueError(f"labelled_types: {p!r} is not one of {', '.join(allowed)} (or 'all')")
        if allowed[p] in out:
            raise ValueError(f"labelled_types: {p!r} repeated")
        out.add(allowed[p])
    return frozenset(out)


def _parse_players(cell: str) -> tuple[bool, frozenset[str]]:
    if cell.strip() == ALL_VISIBLE:
        return True, frozenset()
    parts = [p.strip() for p in cell.split(";")]
    if not cell.strip() or any(not _PLAYER.match(p) for p in parts):
        raise ValueError(
            f"players: {cell!r} must be {ALL_VISIBLE!r} or capital letters separated by ';'"
        )
    if len(set(parts)) != len(parts):
        raise ValueError(f"players: {cell!r} has a repeated letter")
    return False, frozenset(parts)


def load_annotated(path: str | Path) -> list[AnnotatedInterval]:
    """Parse ``annotated.csv``: format, inverted frames and overlaps within a clip."""
    p = Path(path)
    if not p.is_file():
        raise _fail("Annotated intervals file not found", [f"{p.name}: file does not exist"])
    with p.open(encoding="utf-8", newline="") as fh:
        raw = list(enumerate(fh, start=1))
    content = [(n, line) for n, line in raw if line.strip() and not line.lstrip().startswith("#")]
    if not content:
        raise _fail("Annotated intervals file is empty", [f"{p.name}: no header"])
    header_line, header_text = content[0]
    header = [h.strip() for h in next(csv.reader([header_text]))]
    if tuple(header) != ANNOTATED_COLUMNS:
        raise _fail(
            "Annotated intervals header is invalid",
            [f"{p.name}:{header_line}: header must be {','.join(ANNOTATED_COLUMNS)}"],
        )
    problems: list[str] = []
    intervals: list[AnnotatedInterval] = []
    for n, text in content[1:]:
        where = f"{p.name}:{n}"
        cells = [c.strip() for c in next(csv.reader([text]))]
        if len(cells) != len(header):
            problems.append(f"{where}: {len(cells)} fields, expected {len(header)}")
            continue
        row = dict(zip(header, cells, strict=True))
        try:
            start = _int(row, "start_frame")
            end = _int(row, "end_frame")
            types = _parse_types(row["labelled_types"])
            all_visible, players = _parse_players(row["players"])
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
            continue
        if start < 0 or end < 0:
            problems.append(f"{where}: frames must be >= 0")
            continue
        if end < start:
            problems.append(f"{where}: end_frame {end} is before start_frame {start}")
            continue
        intervals.append(
            AnnotatedInterval(
                clip_id=row["clip_id"],
                start_frame=start,
                end_frame=end,
                labelled_types=types,
                all_visible=all_visible,
                players=players,
                notes=row["notes"],
                line=n,
            )
        )
    by_clip: dict[str, list[AnnotatedInterval]] = {}
    for iv in intervals:
        by_clip.setdefault(iv.clip_id, []).append(iv)
    for clip_ivs in by_clip.values():
        clip_ivs.sort(key=lambda iv: (iv.start_frame, iv.line))
        # Compare with the interval reaching furthest so far, not only the previous one.
        reach = clip_ivs[0]
        for cur in clip_ivs[1:]:
            if cur.start_frame <= reach.end_frame:
                problems.append(
                    f"{p.name}:{cur.line}: overlaps line {reach.line} "
                    f"(frames {cur.start_frame}-{cur.end_frame} vs "
                    f"{reach.start_frame}-{reach.end_frame})"
                )
            if cur.end_frame > reach.end_frame:
                reach = cur
    if problems:
        raise _fail("Annotated intervals file has invalid rows", problems)
    return intervals


def _int(row: dict[str, str], name: str) -> int:
    try:
        return int(row[name])
    except ValueError as exc:
        raise ValueError(f"{name}: {row[name]!r} is not a whole number") from exc


@dataclass(frozen=True)
class Coverage:
    """What an interval covers exhaustively, and what keeps it from being exhaustive."""

    missing_types: tuple[LabelStrokeType, ...]
    """Coverage types (forehand, backhand, serve, volley, other) not labelled."""
    missing_players: str | None
    """None if the analysed player is covered; otherwise ``A``."""

    @property
    def exhaustive(self) -> bool:
        return not self.missing_types and self.missing_players is None


def coverage(interval: AnnotatedInterval) -> Coverage:
    """Exhaustiveness of an interval (rule P2 of the M2.3 evaluation proposal, revised).

    Exhaustive = all five coverage types labelled and the analysed player ``A`` covered
    (``A`` listed, or ``all_visible``). ``none`` labels never count.

    Revision approved by Leonardo on 2026-11-22: other players are **not** required. The
    pose estimator tracks one skeleton, the analysed player; another player's stroke can
    only produce a detector event if the skeleton jumps onto that person, which is a
    tracking error (overlay_wrist = other_person, or a jump visible in the pose), not an
    unlabelled stroke. The first version required ``all_visible`` whenever the manifest
    said ``other_people = yes``.
    """
    types = tuple(t for t in COVERAGE_TYPES if t not in interval.labelled_types)
    missing_players: str | None = None
    if not interval.all_visible and ANALYSED_PLAYER not in interval.players:
        missing_players = ANALYSED_PLAYER
    return Coverage(missing_types=types, missing_players=missing_players)


def check_against_labels(
    intervals: list[AnnotatedInterval],
    labels: list[Label],
    clips: dict[str, ManifestClip],
    frame_counts: dict[str, int],
    file_name: str = ANNOTATED_FILE,
    labels_name: str = "labels.csv",
) -> tuple[list[str], list[str]]:
    """Cross-check intervals with the manifest and the labels.

    Errors: unknown clip, interval past the end of the clip, a label outside every interval
    of a clip that has intervals. Warnings: a clip without intervals, a label inside an
    interval whose type or player is not declared as labelled there (allowed, but that
    label does not make the interval exhaustive).
    """
    problems: list[str] = []
    warnings: list[str] = []
    by_clip: dict[str, list[AnnotatedInterval]] = {}
    for iv in intervals:
        where = f"{file_name}:{iv.line}"
        if iv.clip_id not in clips:
            problems.append(f"{where}: unknown clip_id {iv.clip_id!r}")
            continue
        n = frame_counts.get(iv.clip_id)
        if n is not None and iv.end_frame >= n:
            problems.append(
                f"{where}: end_frame {iv.end_frame} is past the end of clip {iv.clip_id} "
                f"({n} frames, last = {n - 1})"
            )
        by_clip.setdefault(iv.clip_id, []).append(iv)
    for clip_id in clips:
        if clip_id not in by_clip:
            warnings.append(f"clip {clip_id}: no annotated intervals (coverage unknown)")
    for lb in labels:
        ivs = by_clip.get(lb.clip_id)
        if not ivs:
            continue
        inside = next((iv for iv in ivs if iv.contains(lb.proxy_frame)), None)
        if inside is None:
            problems.append(
                f"{labels_name}:{lb.line}: frame {lb.proxy_frame} of clip {lb.clip_id} is "
                "outside every annotated interval of that clip"
            )
            continue
        if lb.stroke_type in COVERAGE_TYPES and lb.stroke_type not in inside.labelled_types:
            warnings.append(
                f"{labels_name}:{lb.line}: {lb.stroke_type.value} inside interval at "
                f"{file_name}:{inside.line}, which does not declare {lb.stroke_type.value}"
            )
        if not inside.all_visible and lb.player not in inside.players:
            warnings.append(
                f"{labels_name}:{lb.line}: player {lb.player} inside interval at "
                f"{file_name}:{inside.line}, which does not declare player {lb.player}"
            )
    return problems, warnings
