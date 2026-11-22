"""``strokes.json`` 1.0: the versioned artifact of candidate stroke events (M2.3).

Status: FORMAT ONLY. No detector writes this file yet; the models, the versioned reader,
the invariant checks and the canonical writer are ready so that the M2.3 detector and the
evaluation tool can be built against a fixed contract. See docs/M2_3_EVENTS.md and
ADR-022.

Three levels, kept apart
------------------------
* **Wrist peaks** (``wrist_peaks``): evidence. Every recorded local maximum of the
  apparent 2D speed of either wrist, grouped into an event or discarded with a reason,
  each with its own provenance (keypoint, velocity / smoothing / scale state).
* **Candidate events** (``events``): possible strokes, the only objects that are counted.
  Peaks of both wrists closer than the grouping window G are chained into one event, so
  one gesture is never counted twice.
* **Per event**: evidence of both wrists, representative wrist (may stay
  ``undetermined``), four independent body regions, reliability (never above the worst
  region used), reserved fields for M2.4 / M2.5, and a decision trace.

Nothing here comes from labels: the association with annotated impacts lives in the
evaluation output (``strokes_eval.json``, PR 2), never in this file.

Versions (backward compatibility only)
--------------------------------------
``schema_version`` is ``MAJOR.MINOR``. A reader for 1.N reads 1.0 ... 1.N; a newer minor
raises :class:`StrokesSchemaTooNewError` and another major
:class:`StrokesSchemaUnsupportedError`, both before any field is validated. Models
forbid unknown fields, so nothing is ever ignored silently. A minor version may only add
optional fields whose default means "not recorded", or new values of a closed list.
Writer and readers live in this repository and are bumped together.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    ValidationError,
)

from tennis_cv.errors import TennisCVError
from tennis_cv.signals.scale import ScaleState
from tennis_cv.signals.smoothing import SmoothState
from tennis_cv.signals.timebase import MapStatus, TimeBasis
from tennis_cv.signals.velocity import VelocityState

SCHEMA_NAME = "tpa.strokes"
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
SCHEMA_VERSION = f"{SCHEMA_MAJOR}.{SCHEMA_MINOR}"

DECIMALS = 4
"""Times (s) and speeds are written with this many decimals (0.1 ms): the canonical form
must not depend on float noise. Readers compare times within half of the last digit."""

_VERSION = re.compile(r"^(\d+)\.(\d+)$")
_ID = r"^[a-z][A-Za-z0-9_.-]{0,63}$"
_CODE = r"^[a-z][a-z0-9_]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"


# --------------------------------------------------------------------------- errors


class StrokesFormatError(TennisCVError):
    """``strokes.json`` is not valid JSON or does not match the 1.x models."""

    code = "strokes_format_invalid"


class StrokesSchemaUnsupportedError(TennisCVError):
    """Not a ``tpa.strokes`` file, or a major version this reader does not know."""

    code = "strokes_schema_unsupported"


class StrokesSchemaTooNewError(TennisCVError):
    """Same major, newer minor than this reader: update the reader (never ignore fields)."""

    code = "strokes_schema_too_new"


class StrokesInvariantError(TennisCVError):
    """The file parses but breaks one or more of the eight invariants."""

    code = "strokes_invariant_violated"


class StrokesInputMismatchError(TennisCVError):
    """The recorded input hashes do not match the files next to it (stale output)."""

    code = "strokes_inputs_stale"


# --------------------------------------------------------------------------- vocabulary


class ProducerStatus(StrEnum):
    EXPERIMENTAL = "experimental"


class Wrist(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class PeakStatus(StrEnum):
    GROUPED = "grouped"
    DISCARDED = "discarded"


class EventStatus(StrEnum):
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


class RepresentativeWristValue(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UNDETERMINED = "undetermined"


class EvidenceState(StrEnum):
    PEAKS = "peaks"
    NO_PEAK = "no_peak"


class RegionName(StrEnum):
    TORSO = "torso"
    LEFT_WRIST = "left_wrist"
    RIGHT_WRIST = "right_wrist"
    LEGS = "legs"


class RegionState(StrEnum):
    GOOD = "good"
    LOW_BUT_USABLE = "low_but_usable"
    NOT_OBSERVABLE = "not_observable"


REGION_RANK: dict[RegionState, int] = {
    RegionState.NOT_OBSERVABLE: 0,
    RegionState.LOW_BUT_USABLE: 1,
    RegionState.GOOD: 2,
}
"""Order of region states (higher = more reliable), used by invariant 6."""


class RegionReason(StrEnum):
    NO_POSE = "no_pose"
    BELOW_THRESHOLD = "below_threshold"
    OUT_OF_FRAME = "out_of_frame"
    BROKEN_RUN = "broken_run"
    IRREGULAR_INTERVAL = "irregular_interval"


class RuleKind(StrEnum):
    DISCARD_PEAK = "discard_peak"
    REJECT = "reject"
    UNCERTAIN = "uncertain"
    REPRESENTATIVE_WRIST = "representative_wrist"
    CHECK = "check"
    DECISION = "decision"
    INFO = "info"


class TraceOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


DECISION_RULE = "decision"
"""Rule name of the last trace step of every event (invariant 8)."""


_FORBIDDEN = r"/\\:\u0000"  # anywhere: separators, drive / stream colon, NUL
_WHITESPACE = (
    r"\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a"
    r"\u2028\u2029\u202f\u205f\u3000"
)
"""Exactly the characters for which Python's ``str.isspace()`` is true (what
``str.strip()`` removes), listed explicitly: ``\\s`` means a different set in Python,
Rust (Pydantic core) and ECMA-262 (e.g. U+001C-U+001F, U+0085, U+FEFF), so it is not
used. A test checks this list against ``str.isspace`` over all code points."""
_ANY = rf"[^{_FORBIDDEN}]"
_EDGE = rf"[^{_FORBIDDEN}{_WHITESPACE}]"  # first / last character: no whitespace
_EDGE_NO_DOT = rf"[^{_FORBIDDEN}{_WHITESPACE}.]"
_FILE_NAME = rf"^(?:{_EDGE_NO_DOT}|{_EDGE_NO_DOT}{_EDGE}|\.{_EDGE_NO_DOT}|{_EDGE}{_ANY}+{_EDGE})$"
"""The whole :func:`_plain_file_name` contract as one regular expression, used by Pydantic
and exported unchanged in the JSON Schema, so that external validators accept exactly the
same names: no ``/``, ``\\``, ``:`` or NUL anywhere; no whitespace at either end; not
empty, ``.`` or ``..`` (length 1: not a dot; length 2: not two dots; length 3+: free in the
middle). Only syntax shared by Python ``re``, Rust ``regex`` and ECMA-262 (with or without
the ``u`` flag): explicit ``\\uXXXX`` classes, no ``\\s``, no look-ahead. ECMA-262 ``$``
without the ``m`` flag matches only at the end of input; the last character class
excludes every line terminator anyway. Tests run the exported pattern in Node."""


def _plain_file_name(name: str) -> str:
    """Accept only a plain file name, never a path: ``pose.json`` yes; ``../pose.json``,
    ``/abs``, ``C:\\x``, ``C:x``, ``a\\b``, ``.`` and ``..`` no. Checked at validation time,
    so a bad name never reaches the filesystem."""
    if (
        not name
        or name in (".", "..")
        or any(c in name for c in "/\\:\x00")
        or name != name.strip()
    ):
        raise ValueError(f"must be a plain file name, not a path: {name!r}")
    return name


FileName = Annotated[str, AfterValidator(_plain_file_name), Field(pattern=_FILE_NAME)]
"""Base name of an input file next to ``strokes.json`` (never a path)."""


def _round(x: float) -> float:
    return round(x, DECIMALS)


Rounded = Annotated[float, PlainSerializer(_round, return_type=float)]
"""A time or speed: any float in memory, written with ``DECIMALS`` decimals."""


class _Model(BaseModel):
    """Artifact models: unknown fields and NaN/inf are rejected."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


# --------------------------------------------------------------------------- header


class Producer(_Model):
    algorithm: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    status: ProducerStatus
    engine_version: str = Field(min_length=1)


class PoseInput(_Model):
    file: FileName = Field(description="Plain file name next to strokes.json, never a path")
    sha256: str = Field(pattern=_SHA256, description="SHA-256 of the file bytes")
    fps: float = Field(gt=0)
    frames: int = Field(ge=0, description="Entries in pose.json frames")
    frame_stride: int = Field(ge=1)


class TimemapInput(_Model):
    file: FileName | None = Field(default=None, description="Plain file name, never a path")
    sha256: str | None = Field(default=None, pattern=_SHA256)
    status: MapStatus = Field(description="As reported by signals.timebase.build_timebase")


class Inputs(_Model):
    pose: PoseInput
    timemap: TimemapInput
    player_config: dict[str, JsonValue] | None = Field(
        default=None,
        description="Reserved for the player configuration of M2.2 #6. Null in 1.0 "
        "producers; never used for detection in M2.3",
    )


class Rule(_Model):
    code: str = Field(pattern=_CODE)
    kind: RuleKind
    description: str = Field(min_length=1)


# --------------------------------------------------------------------------- wrist peaks


class Speed(_Model):
    value: Rounded
    unit: str = Field(min_length=1, description="e.g. torso_lengths_per_s or px_per_s")
    rel_to_clip: Rounded | None = Field(
        default=None, description="Position relative to the clip (definition in params)"
    )


class Provenance(_Model):
    signal: str = Field(min_length=1, description="Name of the signal the peak was found on")
    keypoint: str = Field(min_length=1, description="MediaPipe landmark name")
    landmark_index: int = Field(ge=0)
    velocity_state: VelocityState
    smooth_state: SmoothState
    scale_state: ScaleState | None = Field(description="None when no scale was used")


class QualityWindow(_Model):
    half_window_s: Rounded = Field(gt=0)
    good: int = Field(ge=0)
    low_but_usable: int = Field(ge=0)
    unreliable: int = Field(ge=0)
    missing: int = Field(ge=0)
    irregular_intervals: int = Field(ge=0)


class WristPeak(_Model):
    id: str = Field(pattern=_ID)
    wrist: Wrist
    proxy_frame: int = Field(ge=0)
    t_s: Rounded = Field(ge=0, description="Time on the file's time_basis")
    speed: Speed
    provenance: Provenance
    quality_window: QualityWindow
    status: PeakStatus
    reasons: list[str] = Field(default_factory=list)
    event_id: str | None = None


# --------------------------------------------------------------------------- events


class GroupLink(_Model):
    from_: str = Field(alias="from", pattern=_ID)
    to: str = Field(pattern=_ID)
    dt_s: Rounded = Field(ge=0)


class Grouping(_Model):
    method: Literal["chain"]
    g_s: Rounded = Field(gt=0, description="Grouping window G (a parameter, not validated)")
    peak_ids: list[str] = Field(min_length=1)
    links: list[GroupLink]
    span_s: Rounded = Field(ge=0, description="Last minus first peak time; may exceed G")
    possible_merge: bool


class WristEvidence(_Model):
    state: EvidenceState
    peak_ids: list[str]


class Evidence(_Model):
    left: WristEvidence
    right: WristEvidence


class RepresentativeWrist(_Model):
    value: RepresentativeWristValue
    reasons: list[str] = Field(default_factory=list)


class EventTime(_Model):
    ref_proxy_frame: int | None = Field(ge=0)
    ref_t_s: Rounded | None = Field(ge=0)
    window_proxy_frames: tuple[int, int]
    window_t_s: tuple[Rounded, Rounded]


class SampleCounts(_Model):
    good: int = Field(ge=0)
    low_but_usable: int = Field(ge=0)
    unreliable: int = Field(ge=0)
    missing: int = Field(ge=0)


class Region(_Model):
    state: RegionState
    reasons: list[RegionReason] = Field(default_factory=list)
    samples: SampleCounts


class Regions(_Model):
    torso: Region
    left_wrist: Region
    right_wrist: Region
    legs: Region

    def get(self, name: RegionName) -> Region:
        region: Region = getattr(self, name.value)
        return region


class EventReliability(_Model):
    state: RegionState
    regions_used: list[RegionName] = Field(min_length=1)
    limited_by: list[RegionName] = Field(default_factory=list)


class ComplementarySignal(_Model):
    """Reserved for M2.3 complementary signals (from M2.2 #6). Empty in early producers."""

    signal: str = Field(min_length=1)
    value: Rounded | None
    unit: str = Field(min_length=1)
    state: str = Field(min_length=1)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)


class Classification(_Model):
    """Reserved for M2.4 (forehand / backhand)."""

    stroke_type: Literal["unknown"] = "unknown"
    status: Literal["not_attempted"] = "not_attempted"


class PlayerCheck(_Model):
    """Reserved for M2.5 (player verification)."""

    status: Literal["not_checked"] = "not_checked"


class TraceStep(_Model):
    step: int = Field(ge=1)
    rule: str = Field(pattern=_CODE)
    subject: str = Field(pattern=_ID, description="Peak or event id the rule looked at")
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    param: str | None = Field(description="Dotted path into params, e.g. grouping.g_s")
    outcome: TraceOutcome


class CandidateEvent(_Model):
    id: str = Field(pattern=_ID)
    status: EventStatus
    reasons: list[str] = Field(default_factory=list)
    grouping: Grouping
    evidence: Evidence
    representative_wrist: RepresentativeWrist
    time: EventTime
    regions: Regions
    reliability: EventReliability
    complementary: list[ComplementarySignal] = Field(default_factory=list)
    classification: Classification = Field(default_factory=Classification)
    player: PlayerCheck = Field(default_factory=PlayerCheck)
    estimated_contact_s: None = Field(
        default=None, description="Null until the peak-to-impact offset is validated (D4)"
    )
    trace: list[TraceStep] = Field(min_length=1)


# --------------------------------------------------------------------------- file


class StrokesFile(_Model):
    """``strokes.json`` 1.x (see module docstring and docs/M2_3_EVENTS.md)."""

    schema_: Literal["tpa.strokes"] = Field(alias="schema")
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    created_at: datetime = Field(description="The only field ignored by repeatability checks")
    producer: Producer
    inputs: Inputs
    time_basis: TimeBasis
    params: dict[str, JsonValue] = Field(
        description="Every parameter used, M2.2 signal parameters included (as written)"
    )
    params_sha256: str = Field(pattern=_SHA256)
    rules: list[Rule]
    wrist_peaks: list[WristPeak]
    events: list[CandidateEvent]


# --------------------------------------------------------------------------- hashes


def canonical_params_json(params: dict[str, Any]) -> str:
    """Compact, key-sorted JSON of ``params``: the input of ``params_sha256``."""
    return json.dumps(
        params, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def params_sha256(params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_params_json(params).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- invariants

_TOL = 0.5 * 10.0**-DECIMALS + 1e-9
"""Two written times are equal when they differ by at most half of the last decimal."""


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _TOL


def _dupes(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen and v not in out:
            out.append(v)
        seen.add(v)
    return out


def _inv1_peaks_and_events(doc: StrokesFile) -> list[str]:
    """Every peak belongs to at most one event; peak.event_id and the event agree."""
    out = [f"duplicate peak id {d!r}" for d in _dupes(p.id for p in doc.wrist_peaks)]
    out += [f"duplicate event id {d!r}" for d in _dupes(e.id for e in doc.events)]
    peaks = {p.id: p for p in doc.wrist_peaks}
    events = {e.id: e for e in doc.events}
    owner: dict[str, str] = {}
    for e in doc.events:
        for pid in e.grouping.peak_ids:
            if pid in owner and owner[pid] != e.id:
                out.append(f"peak {pid!r} is in events {owner[pid]!r} and {e.id!r}")
            owner.setdefault(pid, e.id)
            p = peaks.get(pid)
            if p is None:
                out.append(f"event {e.id!r} lists unknown peak {pid!r}")
            elif p.event_id != e.id:
                out.append(f"event {e.id!r} lists peak {pid!r} whose event_id is {p.event_id!r}")
        out += [f"event {e.id!r} lists peak {d!r} twice" for d in _dupes(e.grouping.peak_ids)]
    for p in doc.wrist_peaks:
        if p.status is PeakStatus.GROUPED:
            if p.event_id is None:
                out.append(f"grouped peak {p.id!r} has no event_id")
            elif p.event_id not in events:
                out.append(f"peak {p.id!r} points to unknown event {p.event_id!r}")
            elif owner.get(p.id) != p.event_id:
                out.append(f"peak {p.id!r} says event {p.event_id!r}, which does not list it")
        else:
            if p.event_id is not None:
                out.append(f"discarded peak {p.id!r} has event_id {p.event_id!r}")
            if not p.reasons:
                out.append(f"discarded peak {p.id!r} has no reason")
    return out


def _inv2_window(doc: StrokesFile) -> list[str]:
    """The event window runs from its first to its last peak; evidence matches grouping."""
    out: list[str] = []
    peaks = {p.id: p for p in doc.wrist_peaks}
    for e in doc.events:
        own = [peaks[pid] for pid in e.grouping.peak_ids if pid in peaks]
        if not own:
            continue
        frames = (min(p.proxy_frame for p in own), max(p.proxy_frame for p in own))
        times = (min(p.t_s for p in own), max(p.t_s for p in own))
        w = e.time
        if tuple(w.window_proxy_frames) != frames:
            out.append(f"event {e.id!r}: window_proxy_frames {w.window_proxy_frames} != {frames}")
        if not (_close(w.window_t_s[0], times[0]) and _close(w.window_t_s[1], times[1])):
            out.append(f"event {e.id!r}: window_t_s {w.window_t_s} != peaks span {times}")
        if not _close(e.grouping.span_s, times[1] - times[0]):
            out.append(f"event {e.id!r}: span_s {e.grouping.span_s} != {times[1] - times[0]:.4f}")
        for wrist, ev in ((Wrist.LEFT, e.evidence.left), (Wrist.RIGHT, e.evidence.right)):
            expected = sorted(p.id for p in own if p.wrist is wrist)
            if sorted(ev.peak_ids) != expected:
                out.append(f"event {e.id!r}: {wrist.value} evidence {ev.peak_ids} != {expected}")
            want = EvidenceState.PEAKS if expected else EvidenceState.NO_PEAK
            if ev.state is not want:
                out.append(f"event {e.id!r}: {wrist.value} evidence state should be {want.value}")
    return out


def _inv3_no_double_count(doc: StrokesFile) -> list[str]:
    """Chain grouping: peaks of one event are chained by links <= G; events are > G apart."""
    out: list[str] = []
    peaks = {p.id: p for p in doc.wrist_peaks}
    gs = {_round(e.grouping.g_s) for e in doc.events}
    if len(gs) > 1:
        out.append(f"events use different grouping windows G: {sorted(gs)}")
    for e in doc.events:
        g = e.grouping.g_s
        own = sorted(
            (peaks[pid] for pid in e.grouping.peak_ids if pid in peaks),
            key=lambda p: (p.t_s, p.id),
        )
        expected = {(a.id, b.id) for a, b in pairwise(own)}
        links = {(lk.from_, lk.to) for lk in e.grouping.links}
        if links != expected:
            out.append(
                f"event {e.id!r}: links must join consecutive peaks in time order "
                f"{sorted(expected)}, got {sorted(links)}"
            )
        for lk in e.grouping.links:
            pa, pb = peaks.get(lk.from_), peaks.get(lk.to)
            if pa is not None and pb is not None and not _close(lk.dt_s, pb.t_s - pa.t_s):
                out.append(f"event {e.id!r}: link {lk.from_}->{lk.to} dt_s != time difference")
            if lk.dt_s > g + _TOL:
                out.append(f"event {e.id!r}: link {lk.from_}->{lk.to} is {lk.dt_s} s > G {g}")
    ordered = sorted(doc.events, key=lambda e: (e.time.window_t_s[0], e.id))
    for ea, eb in pairwise(ordered):
        gap = eb.time.window_t_s[0] - ea.time.window_t_s[1]
        g = max(ea.grouping.g_s, eb.grouping.g_s)
        if gap <= g + _TOL:
            out.append(
                f"events {ea.id!r} and {eb.id!r} are {gap:.4f} s apart, not more than G {g}: "
                "their peaks belong to one event (a gesture would be counted twice)"
            )
    return out


def _inv4_representative(doc: StrokesFile) -> list[str]:
    """Determined wrist -> reference time is a peak of that wrist; else null + a reason."""
    out: list[str] = []
    peaks = {p.id: p for p in doc.wrist_peaks}
    for e in doc.events:
        rw, t = e.representative_wrist, e.time
        if (t.ref_proxy_frame is None) != (t.ref_t_s is None):
            out.append(f"event {e.id!r}: ref_proxy_frame and ref_t_s must be both set or null")
        if rw.value is RepresentativeWristValue.UNDETERMINED:
            if t.ref_t_s is not None or t.ref_proxy_frame is not None:
                out.append(f"event {e.id!r}: undetermined wrist but a reference time is set")
            if not rw.reasons:
                out.append(f"event {e.id!r}: undetermined wrist without a reason")
            continue
        wrist = Wrist(rw.value.value)
        cands = [
            peaks[pid] for pid in e.grouping.peak_ids if pid in peaks and peaks[pid].wrist is wrist
        ]
        if not cands:
            out.append(f"event {e.id!r}: representative wrist {wrist.value} has no peak")
        elif t.ref_t_s is None or not any(
            p.proxy_frame == t.ref_proxy_frame and _close(p.t_s, t.ref_t_s) for p in cands
        ):
            out.append(
                f"event {e.id!r}: reference time is not a {wrist.value} wrist peak of the event"
            )
    return out


def _inv5_reasons(doc: StrokesFile) -> list[str]:
    """Rejected / uncertain events have a reason; every code used in the file (event, peak
    and representative-wrist reasons, every trace rule, ``decision`` included) is declared
    once in ``rules``."""
    out = [f"duplicate rule code {d!r}" for d in _dupes(r.code for r in doc.rules)]
    known = {r.code for r in doc.rules}
    for e in doc.events:
        for s in e.trace:
            if s.rule not in known:
                out.append(f"event {e.id!r}: trace step {s.step} rule {s.rule!r} is not in rules")
    for e in doc.events:
        if e.status is not EventStatus.ACCEPTED and not e.reasons:
            out.append(f"event {e.id!r} is {e.status.value} without a reason")
        for code in [*e.reasons, *e.representative_wrist.reasons]:
            if code not in known:
                out.append(f"event {e.id!r}: reason {code!r} is not in rules")
    for p in doc.wrist_peaks:
        for code in p.reasons:
            if code not in known:
                out.append(f"peak {p.id!r}: reason {code!r} is not in rules")
    return out


def _inv6_reliability(doc: StrokesFile) -> list[str]:
    """Reliability never above the worst region used; unused regions play no part."""
    out: list[str] = []
    for e in doc.events:
        rel = e.reliability
        used = set(rel.regions_used)
        if len(used) != len(rel.regions_used):
            out.append(f"event {e.id!r}: regions_used has duplicates")
        worst = min(REGION_RANK[e.regions.get(r).state] for r in used)
        if REGION_RANK[rel.state] > worst:
            out.append(
                f"event {e.id!r}: reliability {rel.state.value} is above the worst region used"
            )
        extra = [r.value for r in rel.limited_by if r not in used]
        if extra:
            out.append(f"event {e.id!r}: limited_by {extra} not in regions_used")
        for name in RegionName:
            region = e.regions.get(name)
            if region.state is RegionState.NOT_OBSERVABLE and not region.reasons:
                out.append(f"event {e.id!r}: region {name.value} not_observable without reason")
    return out


def _inv7_time_basis(doc: StrokesFile) -> list[str]:
    """Source times only with a usable map (as build_timebase does), and vice versa."""
    tm = doc.inputs.timemap
    out: list[str] = []
    if (doc.time_basis is TimeBasis.SOURCE) != (tm.status is MapStatus.OK):
        out.append(
            f"time_basis {doc.time_basis.value} with timemap status {tm.status.value}: "
            "source times require (and an ok map implies) basis source"
        )
    if tm.status is MapStatus.MISSING and (tm.file is not None or tm.sha256 is not None):
        out.append("timemap status missing but a timemap file or hash is recorded")
    if tm.status is not MapStatus.MISSING and (tm.file is None or tm.sha256 is None):
        out.append(f"timemap status {tm.status.value} needs its file name and sha256")
    return out


def _inv8_trace(doc: StrokesFile) -> list[str]:
    """Trace steps are numbered 1..n; the last one is the decision and equals status."""
    out: list[str] = []
    for e in doc.events:
        steps = [s.step for s in e.trace]
        if steps != list(range(1, len(steps) + 1)):
            out.append(f"event {e.id!r}: trace steps must be numbered 1..{len(steps)}")
        last = e.trace[-1]
        if last.rule != DECISION_RULE or last.outcome.value != e.status.value:
            out.append(
                f"event {e.id!r}: last trace step must be rule {DECISION_RULE!r} with "
                f"outcome {e.status.value!r} (got {last.rule!r} -> {last.outcome.value!r})"
            )
        if any(s.rule == DECISION_RULE for s in e.trace[:-1]):
            out.append(f"event {e.id!r}: only the last trace step can be the decision")
    return out


INVARIANTS: tuple[tuple[int, Callable[[StrokesFile], list[str]]], ...] = (
    (1, _inv1_peaks_and_events),
    (2, _inv2_window),
    (3, _inv3_no_double_count),
    (4, _inv4_representative),
    (5, _inv5_reasons),
    (6, _inv6_reliability),
    (7, _inv7_time_basis),
    (8, _inv8_trace),
)


def check_invariants(doc: StrokesFile) -> list[str]:
    """All violations, each prefixed with its invariant number (``I3: ...``); [] = valid.

    ``params_sha256`` is also checked against ``params`` (prefix ``P``).
    """
    problems: list[str] = []
    for n, check in INVARIANTS:
        problems += [f"I{n}: {msg}" for msg in check(doc)]
    if params_sha256(doc.params) != doc.params_sha256:
        problems.append("P: params_sha256 does not match params")
    return problems


# --------------------------------------------------------------------------- read / write


def check_version(raw: object, *, supported_minor: int = SCHEMA_MINOR) -> str:
    """Check ``schema`` / ``schema_version`` on the raw JSON, before any field.

    ``supported_minor`` is the newest minor this reader knows (a parameter only so tests
    can simulate a newer reader).
    """
    if not isinstance(raw, dict):
        raise StrokesFormatError("strokes.json must be a JSON object")
    name, version = raw.get("schema"), raw.get("schema_version")
    if name != SCHEMA_NAME:
        raise StrokesSchemaUnsupportedError(
            f"Not a {SCHEMA_NAME} file", details={"schema": name if isinstance(name, str) else None}
        )
    m = _VERSION.match(version) if isinstance(version, str) else None
    if m is None:
        raise StrokesSchemaUnsupportedError(
            "schema_version must be MAJOR.MINOR", details={"schema_version": str(version)}
        )
    major, minor = int(m.group(1)), int(m.group(2))
    if major != SCHEMA_MAJOR:
        raise StrokesSchemaUnsupportedError(
            f"strokes.json major version {major} is not supported (reader: {SCHEMA_MAJOR}.x)",
            details={"schema_version": version},
        )
    if minor > supported_minor:
        raise StrokesSchemaTooNewError(
            f"strokes.json {version} is newer than this reader ({SCHEMA_MAJOR}.{supported_minor})",
            details={"schema_version": version},
        )
    return str(version)


def _problems(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]]


def parse_strokes(text: str | bytes, *, supported_minor: int = SCHEMA_MINOR) -> StrokesFile:
    """Parse and fully validate ``strokes.json`` content: version, models, invariants."""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise StrokesFormatError(
            "strokes.json is not valid JSON", details={"error": str(exc)}
        ) from exc
    check_version(raw, supported_minor=supported_minor)
    try:
        doc = StrokesFile.model_validate(raw)
    except ValidationError as exc:
        raise StrokesFormatError(
            "strokes.json does not match the schema", details={"problems": _problems(exc)}
        ) from exc
    problems = check_invariants(doc)
    if problems:
        raise StrokesInvariantError(
            f"strokes.json breaks {len(problems)} invariant check(s)",
            details={"problems": problems},
        )
    return doc


def load_strokes(path: str | Path) -> StrokesFile:
    p = Path(path)
    if not p.is_file():
        raise StrokesFormatError("strokes.json not found", details={"file": p.name})
    return parse_strokes(p.read_bytes())


def to_canonical_dict(doc: StrokesFile, *, include_created_at: bool = True) -> dict[str, Any]:
    """JSON-ready dict with lists in canonical order (peaks and events by time, then id)."""
    data: dict[str, Any] = doc.model_dump(mode="json", by_alias=True)
    data["rules"] = sorted(data["rules"], key=lambda r: r["code"])
    data["wrist_peaks"] = sorted(data["wrist_peaks"], key=lambda p: (p["t_s"], p["id"]))
    data["events"] = sorted(data["events"], key=lambda e: (e["time"]["window_t_s"][0], e["id"]))
    if not include_created_at:
        del data["created_at"]
    return data


def dump_strokes(doc: StrokesFile) -> str:
    """Canonical text: invariants checked first, keys sorted, fixed decimals, final newline.

    The same document always gives the same bytes.
    """
    problems = check_invariants(doc)
    if problems:
        raise StrokesInvariantError(
            "Refusing to write an invalid strokes.json", details={"problems": problems}
        )
    return (
        json.dumps(
            to_canonical_dict(doc), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False
        )
        + "\n"
    )


def same_output(a: StrokesFile, b: StrokesFile) -> bool:
    """Repeatability check: equal canonical content apart from ``created_at``."""
    return to_canonical_dict(a, include_created_at=False) == to_canonical_dict(
        b, include_created_at=False
    )


def check_inputs(doc: StrokesFile, directory: str | Path) -> list[str]:
    """Compare the recorded input hashes with the files in ``directory`` ([] = current)."""
    d = Path(directory)
    out: list[str] = []
    # names are validated by the model; re-check here for documents built without it
    for name in (doc.inputs.pose.file, doc.inputs.timemap.file):
        if name is not None:
            try:
                _plain_file_name(name)
            except ValueError as exc:
                raise StrokesFormatError(str(exc), details={"field": "inputs"}) from exc
    pose = d / doc.inputs.pose.file
    if not pose.is_file():
        out.append(f"{doc.inputs.pose.file}: not found next to strokes.json")
    elif file_sha256(pose) != doc.inputs.pose.sha256:
        out.append(f"{doc.inputs.pose.file}: sha256 differs (strokes.json is stale)")
    tm = doc.inputs.timemap
    if tm.file is not None and tm.sha256 is not None:
        path = d / tm.file
        if not path.is_file():
            out.append(f"{tm.file}: not found next to strokes.json")
        elif file_sha256(path) != tm.sha256:
            out.append(f"{tm.file}: sha256 differs (strokes.json is stale)")
    return out


def verify_inputs(doc: StrokesFile, directory: str | Path) -> None:
    problems = check_inputs(doc, directory)
    if problems:
        raise StrokesInputMismatchError(
            "strokes.json does not match its input files", details={"problems": problems}
        )
