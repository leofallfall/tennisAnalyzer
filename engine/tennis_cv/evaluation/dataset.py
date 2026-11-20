"""Dataset files of the M2.1 stroke-label dataset: ``MANIFEST.md`` and ``labels.csv``.

Both are written by hand (see docs/M2_1_DATASET.md). Parsing is strict: every problem is
reported with its file and line, and nothing is guessed or silently fixed.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import Field, ValidationError

from tennis_cv.common import StrictModel
from tennis_cv.comparison.base import ViewAngle
from tennis_cv.errors import TennisCVError
from tennis_cv.pose.models import PoseSequence
from tennis_cv.video.timemap import TimeMap

POSE_FILE = "pose.json"
TIMEMAP_FILE = "timemap.json"
REPORT_FILE = "report.json"

MANIFEST_COLUMNS: tuple[str, ...] = (
    "clip_id",
    "file",
    "pose_dir",
    "view",
    "dominant_hand",
    "backhand",
    "other_people",
    "occlusion",
    "split",
    "notes",
)
LABEL_COLUMNS: tuple[str, ...] = (
    "clip_id",
    "proxy_frame",
    "stroke_type",
    "player",
    "visibility",
    "overlay_wrist",
    "notes",
)

_CLIP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PLAYER = re.compile(r"^[A-Z]$")
ANALYSED_PLAYER = "A"
_T = TypeVar("_T")


class DatasetFormatError(TennisCVError):
    """The manifest, the labels or a clip's artifacts are missing or malformed."""

    code = "dataset_format_invalid"


class Hand(StrEnum):
    RIGHT = "right"
    LEFT = "left"
    UNKNOWN = "unknown"


class Backhand(StrEnum):
    ONE_HANDED = "one_handed"
    TWO_HANDED = "two_handed"
    UNKNOWN = "unknown"


class Occlusion(StrEnum):
    NONE = "none"
    PARTIAL = "partial"


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"
    UNASSIGNED = "unassigned"


class LabelStrokeType(StrEnum):
    FOREHAND = "forehand"
    BACKHAND = "backhand"
    SERVE = "serve"
    VOLLEY = "volley"
    OTHER = "other"
    NONE = "none"
    """A movement that is *not* a stroke but could be mistaken for one (shadow swing,
    ball bounce, picking up balls). Used to explain false positives."""


class ImpactVisibility(StrEnum):
    """Human judgement of how well the impact can be seen in the video."""

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class OverlayWrist(StrEnum):
    """Human check, on the overlay frame of the impact, of the drawn hitting wrist."""

    OK = "ok"
    OFF = "off"
    MISSING = "missing"
    OTHER_PERSON = "other_person"


class ManifestClip(StrictModel):
    clip_id: str
    file: str = Field(description="Original file name, informational only")
    pose_dir: Path = Field(description="Directory with pose.json (+ timemap/report)")
    view: ViewAngle
    dominant_hand: Hand
    backhand: Backhand
    other_people: bool
    occlusion: Occlusion
    split: Split
    notes: str = ""


class Label(StrictModel):
    clip_id: str
    proxy_frame: int = Field(ge=0, description="Frame index printed on overlay.mp4 (#N)")
    stroke_type: LabelStrokeType
    player: str
    visibility: ImpactVisibility
    overlay_wrist: OverlayWrist | None = Field(
        default=None, description="Empty in the CSV = not checked"
    )
    notes: str = ""
    line: int = Field(ge=1, description="Line in labels.csv, for error messages")


@dataclass(frozen=True)
class ClipData:
    """A manifest clip with its engine artifacts loaded."""

    clip: ManifestClip
    poses: PoseSequence
    timemap: TimeMap | None
    source_fps: float | None
    source_vfr: bool | None

    @property
    def fps(self) -> float:
        return self.poses.fps

    @property
    def frame_count(self) -> int:
        """Proxy frames covered by pose.json (last analysed index + 1)."""
        return (self.poses.frames[-1].frame_index + 1) if self.poses.frames else 0


def _fail(message: str, problems: list[str]) -> DatasetFormatError:
    return DatasetFormatError(message, details={"problems": problems})


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def _yes_no(value: str) -> bool:
    v = value.strip().lower()
    if v in ("yes", "y", "true"):
        return True
    if v in ("no", "n", "false"):
        return False
    raise ValueError(f"expected yes/no, got {value!r}")


def load_manifest(path: str | Path) -> list[ManifestClip]:
    """Parse the clip table of ``MANIFEST.md``.

    The table is the first Markdown pipe table whose header starts with ``clip_id``; the
    rest of the file is free text. ``pose_dir`` is relative to the manifest's directory
    unless absolute.
    """
    p = Path(path)
    if not p.is_file():
        raise _fail("Manifest not found", [f"{p.name}: file does not exist"])
    lines = p.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip().startswith("|") and _split_row(line)[:1] == ["clip_id"]
        ),
        None,
    )
    if start is None:
        raise _fail("Manifest has no clip table", [f"{p.name}: no table with a clip_id header"])
    header = _split_row(lines[start])
    problems: list[str] = []
    if tuple(header) != MANIFEST_COLUMNS:
        problems.append(
            f"{p.name}:{start + 1}: header must be {' | '.join(MANIFEST_COLUMNS)} "
            f"(got {' | '.join(header)})"
        )
        raise _fail("Manifest header is invalid", problems)
    clips: list[ManifestClip] = []
    seen: set[str] = set()
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip().startswith("|"):
            break
        cells = _split_row(line)
        if _is_separator(cells):
            continue
        where = f"{p.name}:{i + 1}"
        if len(cells) != len(header):
            problems.append(f"{where}: {len(cells)} cells, expected {len(header)}")
            continue
        row = dict(zip(header, cells, strict=True))
        if not _CLIP_ID.match(row["clip_id"]):
            problems.append(f"{where}: clip_id {row['clip_id']!r} must match [A-Za-z0-9_-]")
            continue
        if row["clip_id"] in seen:
            problems.append(f"{where}: duplicate clip_id {row['clip_id']!r}")
            continue
        try:
            pose_dir = Path(row["pose_dir"])
            clip = ManifestClip(
                clip_id=row["clip_id"],
                file=row["file"],
                pose_dir=pose_dir if pose_dir.is_absolute() else p.parent / pose_dir,
                view=_field(row, "view", ViewAngle),
                dominant_hand=_field(row, "dominant_hand", Hand),
                backhand=_field(row, "backhand", Backhand),
                other_people=_field(row, "other_people", _yes_no),
                occlusion=_field(row, "occlusion", Occlusion),
                split=_field(row, "split", Split),
                notes=row["notes"],
            )
        except (ValueError, ValidationError) as exc:
            problems.append(f"{where}: {_short(exc)}")
            continue
        seen.add(clip.clip_id)
        clips.append(clip)
    if problems:
        raise _fail("Manifest has invalid rows", problems)
    if not clips:
        raise _fail("Manifest lists no clips", [f"{p.name}: the clip table is empty"])
    return clips


def load_labels(path: str | Path) -> list[Label]:
    """Parse ``labels.csv``. Blank lines and lines starting with ``#`` are ignored."""
    p = Path(path)
    if not p.is_file():
        raise _fail("Labels file not found", [f"{p.name}: file does not exist"])
    with p.open(encoding="utf-8", newline="") as fh:
        raw = [(n, line) for n, line in enumerate(fh, start=1)]
    content = [(n, line) for n, line in raw if line.strip() and not line.lstrip().startswith("#")]
    if not content:
        raise _fail("Labels file is empty", [f"{p.name}: no header"])
    header_line, header_text = content[0]
    header = [h.strip() for h in next(csv.reader([header_text]))]
    if tuple(header) != LABEL_COLUMNS:
        raise _fail(
            "Labels header is invalid",
            [f"{p.name}:{header_line}: header must be {','.join(LABEL_COLUMNS)}"],
        )
    labels: list[Label] = []
    problems: list[str] = []
    seen: dict[tuple[str, int, str], int] = {}
    for n, text in content[1:]:
        where = f"{p.name}:{n}"
        cells = [c.strip() for c in next(csv.reader([text]))]
        if len(cells) != len(header):
            problems.append(f"{where}: {len(cells)} fields, expected {len(header)}")
            continue
        row: dict[str, Any] = dict(zip(header, cells, strict=True))
        if not _PLAYER.match(row["player"]):
            problems.append(f"{where}: player must be one capital letter (A = analysed player)")
            continue
        try:
            label = Label(
                clip_id=row["clip_id"],
                proxy_frame=_field(row, "proxy_frame", int),
                stroke_type=_field(row, "stroke_type", LabelStrokeType),
                player=row["player"],
                visibility=_field(row, "visibility", ImpactVisibility),
                overlay_wrist=(
                    _field(row, "overlay_wrist", OverlayWrist) if row["overlay_wrist"] else None
                ),
                notes=row["notes"],
                line=n,
            )
        except (ValueError, ValidationError) as exc:
            problems.append(f"{where}: {_short(exc)}")
            continue
        key = (label.clip_id, label.proxy_frame, label.player)
        if key in seen:
            problems.append(f"{where}: same clip, frame and player as line {seen[key]}")
            continue
        seen[key] = n
        labels.append(label)
    if problems:
        raise _fail("Labels file has invalid rows", problems)
    return labels


def load_clip_data(clip: ManifestClip) -> ClipData:
    """Load pose.json (required), timemap.json and report.json (optional)."""
    pose_path = clip.pose_dir / POSE_FILE
    if not pose_path.is_file():
        raise _fail(
            "pose.json not found",
            [f"clip {clip.clip_id}: no {POSE_FILE} in pose_dir {clip.pose_dir.name!r}"],
        )
    try:
        poses = PoseSequence.model_validate_json(pose_path.read_bytes())
    except ValidationError as exc:
        raise _fail("pose.json is invalid", [f"clip {clip.clip_id}: {_short(exc)}"]) from exc
    timemap: TimeMap | None = None
    tm_path = clip.pose_dir / TIMEMAP_FILE
    if tm_path.is_file():
        try:
            timemap = TimeMap.model_validate_json(tm_path.read_bytes())
        except ValidationError as exc:
            raise _fail("timemap.json is invalid", [f"clip {clip.clip_id}: {_short(exc)}"]) from exc
    source_fps: float | None = None
    source_vfr: bool | None = None
    rp_path = clip.pose_dir / REPORT_FILE
    if rp_path.is_file():
        try:
            meta = json.loads(rp_path.read_text(encoding="utf-8")).get("source_metadata") or {}
        except ValueError as exc:
            raise _fail("report.json is invalid", [f"clip {clip.clip_id}: {exc}"]) from exc
        fps = meta.get("average_fps") or meta.get("nominal_fps")
        source_fps = float(fps) if isinstance(fps, int | float) else None
        vfr = meta.get("is_variable_frame_rate")
        source_vfr = vfr if isinstance(vfr, bool) else None
    return ClipData(
        clip=clip, poses=poses, timemap=timemap, source_fps=source_fps, source_vfr=source_vfr
    )


def _field(row: dict[str, Any], name: str, conv: Callable[[str], _T]) -> _T:
    """Convert one cell, naming the column in the error."""
    try:
        return conv(row[name])
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from exc


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errs = exc.errors()
        return "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in errs[:3])
    return str(exc)
