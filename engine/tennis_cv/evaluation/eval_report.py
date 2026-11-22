"""Reports on ``strokes_eval.json`` and the false-candidate review sheet (M2.3, PR 3).

Everything here *describes* an evaluation already written by ``evaluate``: no outcome,
cause or verdict is recomputed or changed. The strokes files are read again only for what
``strokes_eval.json`` does not hold (wrists with peaks, regions, ``possible_merge``) and
only if their sha256 equals the one recorded in the evaluation.

Rules (approved M2.3 proposal, "Report separati", "Precisione temporale", P7, "Falsi
candidati"):

* every number is ``n/total``; a group with fewer than ``MIN_CLIPS_PER_GROUP`` clips is
  marked as a small sample;
* offsets only from 1-1 groups (``intercepted`` / ``intercepted_uncertain``), per wrist,
  in ms and in proxy frames, never converted into each other; labels with an estimated
  frame (``visibility = partial``) are listed but excluded from the statistics; window-only
  events are listed apart;
* percentiles by nearest rank, ``k = (p*n + 99) // 100``, signed and absolute values sorted
  separately, ``None`` when ``n = 0``;
* the review sheet lists every unassociated event that is inside an annotated interval at
  some radius (judged or ``not_judgeable_coverage``), with an empty ``category``; a filled
  sheet is merged back by ``clip_id``, ``event_id`` and the strokes sha256, and a row whose
  sha256 changed is reported as stale and never reused.

No number in a report is a validated result unless the strokes files come from the real
detector; the report says which producer wrote each file.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.errors import TennisCVError
from tennis_cv.evaluation.association import EventNode, distance
from tennis_cv.evaluation.dataset import DatasetFormatError, ManifestClip
from tennis_cv.evaluation.eval_artifact import (
    ClipEval,
    ImpactOutcome,
    ImpactResult,
    LabelInfo,
    LabelRole,
    MissCause,
    Position,
    StrokesEvalFile,
    UnassociatedEvent,
    Verdict,
)
from tennis_cv.evaluation.evaluate import LoadedStrokes
from tennis_cv.evaluation.visibility import MIN_CLIPS_PER_GROUP, Ratio
from tennis_cv.strokes.artifact import CandidateEvent, EvidenceState, StrokesFile

PERCENTILES: tuple[int, ...] = (25, 50, 75, 90)
NOT_CHECKED = "not_checked"
NO_EVENT = "no_associated_event"
REVIEW_COLUMNS: tuple[str, ...] = (
    "clip_id",
    "event_id",
    "strokes_sha256",
    "event_t_s",
    "radii_unassociated",
    "nearest_target_line",
    "nearest_target_signed_ms",
    "verdicts",
    "auto_causes",
    "category",
    "notes",
)


class ReviewCategory(StrEnum):
    """The six approved categories of a false candidate (filled by hand)."""

    PREPARATION = "preparation"
    RECOVERY_FINISH = "recovery_finish"
    BETWEEN_STROKES = "between_strokes"
    TRACKING_ERROR = "tracking_error"
    UNLABELLED_STROKE = "unlabelled_stroke"
    NOT_JUDGEABLE = "not_judgeable"


class EvalStaleError(TennisCVError):
    """A strokes file changed after strokes_eval.json was written."""

    code = "strokes_eval_stale"


# ------------------------------------------------------------------ nearest rank (P7)


def nearest_rank(values: Iterable[float], p: int) -> float | None:
    """``x_k`` with ``k = ceil(p*n/100)`` in integer arithmetic; ``None`` if empty."""
    if not 1 <= p <= 100:
        raise ValueError("p must be an integer between 1 and 100")
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    return ordered[(p * n + 99) // 100 - 1]


class Distribution(StrictModel):
    n: int
    min: float | None
    q1: float | None
    median: float | None
    q3: float | None
    p90: float | None
    max: float | None


def distribution(values: Iterable[float]) -> Distribution:
    v = sorted(values)
    q1, med, q3, p90 = (nearest_rank(v, p) for p in PERCENTILES)
    return Distribution(
        n=len(v),
        min=v[0] if v else None,
        q1=q1,
        median=med,
        q3=q3,
        p90=p90,
        max=v[-1] if v else None,
    )


# ------------------------------------------------------------------ report models


class ImpactGroup(StrictModel):
    dimension: str
    value: str
    clips: int
    small_sample: bool
    impacts: int
    intercepted: Ratio
    intercepted_uncertain: Ratio
    missed: Ratio
    ambiguous: Ratio
    miss_causes: dict[str, Ratio] = Field(description="Denominator: missed impacts")


class OffsetSet(StrictModel):
    wrist: str = Field(description="left, right or event (instant of the event)")
    signed_ms: Distribution
    abs_ms: Distribution
    signed_frames: Distribution
    abs_frames: Distribution
    excluded_estimated: int = Field(description="Offsets of labels with an estimated frame")


class OffsetRow(StrictModel):
    clip_id: str
    label_line: int
    wrist: str
    peak_id: str | None
    signed_ms: float
    signed_frames: int
    estimated_frame: bool


class EventStats(StrictModel):
    available: bool = Field(description="False when no strokes 1.x file could be read")
    graph_events: int
    one_wrist: Ratio
    two_wrists: Ratio
    undetermined_wrist: Ratio
    possible_merge: Ratio
    rejected_near_impact: Ratio = Field(
        description="Rejected events within r of a target impact / rejected events"
    )


class WristCheckRow(StrictModel):
    right_wrist_state: str
    overlay_wrist: str
    impacts: int


class FalseCandidate(StrictModel):
    clip_id: str
    event_id: str
    strokes_sha256: str
    event_t_s: float
    radii_unassociated: list[float]
    nearest_target_line: int | None
    nearest_target_signed_ms: float | None
    wrists_with_peaks: list[str]
    regions: dict[str, str]
    verdicts: dict[str, str] = Field(description="radius -> verdict")
    auto_causes: dict[str, str] = Field(description="radius -> automatic cause, if judged")
    in_review_sheet: bool
    category: ReviewCategory | None


class RadiusReport(StrictModel):
    radius_s: float
    interpretable_clips: Ratio = Field(description="Clips with radius status ok")
    groups: list[ImpactGroup]
    offsets: list[OffsetSet]
    offset_rows: list[OffsetRow]
    window_only_events: list[OffsetRow]
    events: EventStats
    right_wrist_vs_overlay: list[WristCheckRow]
    unassociated_by_verdict: dict[str, int]
    category_counts: dict[str, int]


class StaleReviewRow(StrictModel):
    line: int
    clip_id: str
    event_id: str
    reason: str


class EvalReport(StrictModel):
    clips: list[str]
    producers: dict[str, str]
    params: dict[str, object]
    radii: list[RadiusReport]
    false_candidates: list[FalseCandidate]
    stale_review_rows: list[StaleReviewRow]


# ------------------------------------------------------------------ helpers


def _ratio(n: int, of: int) -> Ratio:
    return Ratio(n=n, of=of)


def _overlay(lb: LabelInfo) -> str:
    return lb.overlay_wrist.value if lb.overlay_wrist is not None else NOT_CHECKED


def _doc_event(loaded: LoadedStrokes | None, event_id: str) -> CandidateEvent | None:
    doc: StrokesFile | None = loaded.doc if loaded is not None else None
    if doc is None:
        return None
    return next((e for e in doc.events if e.id == event_id), None)


def check_strokes_match(doc: StrokesEvalFile, strokes: dict[str, LoadedStrokes]) -> None:
    """Every strokes file given must be the one the evaluation read (same sha256)."""
    problems = [
        f"clip {c.clip_id}: {c.strokes.file} sha256 {strokes[c.clip_id].sha256[:12]}… "
        f"!= {c.strokes.sha256[:12]}… recorded in strokes_eval.json"
        for c in doc.clips
        if c.clip_id in strokes and strokes[c.clip_id].sha256 != c.strokes.sha256
    ]
    if problems:
        raise EvalStaleError(
            "strokes files changed since the evaluation: run evaluate again",
            details={"problems": problems},
        )


Item = tuple[ClipEval, LabelInfo, ImpactResult]


def _items(doc: StrokesEvalFile, radius: float) -> list[Item]:
    out: list[Item] = []
    for c in doc.clips:
        rr = next((r for r in c.radii if r.radius_s == radius), None)
        if rr is None:
            continue
        labels = {lb.line: lb for lb in c.labels}
        out += [(c, labels[i.label_line], i) for i in rr.impacts]
    return out


def _event_wrist(item: Item, strokes: dict[str, LoadedStrokes]) -> str:
    c, _, imp = item
    if imp.interception is None:
        return NO_EVENT
    ev = _doc_event(strokes.get(c.clip_id), imp.interception.event_id)
    if ev is None:
        return "unknown"
    return ev.representative_wrist.value.value


def _group(dimension: str, value: str, items: list[Item]) -> ImpactGroup:
    n = len(items)
    count = {o: sum(1 for _, _, i in items if i.outcome is o) for o in ImpactOutcome}
    missed = [i for _, _, i in items if i.miss is not None]
    causes = {
        m.value: _ratio(sum(1 for i in missed if i.miss and i.miss.cause is m), len(missed))
        for m in MissCause
    }
    clips = len({c.clip_id for c, _, _ in items})
    return ImpactGroup(
        dimension=dimension,
        value=value,
        clips=clips,
        small_sample=clips < MIN_CLIPS_PER_GROUP,
        impacts=n,
        intercepted=_ratio(count[ImpactOutcome.INTERCEPTED], n),
        intercepted_uncertain=_ratio(count[ImpactOutcome.INTERCEPTED_UNCERTAIN], n),
        missed=_ratio(count[ImpactOutcome.MISSED], n),
        ambiguous=_ratio(count[ImpactOutcome.AMBIGUOUS], n),
        miss_causes=causes,
    )


def _groups(
    items: list[Item], views: dict[str, str], strokes: dict[str, LoadedStrokes]
) -> list[ImpactGroup]:
    dims: list[tuple[str, Callable[[Item], str]]] = [
        ("all", lambda it: "all"),
        ("stroke_type", lambda it: it[1].stroke_type.value),
        ("view", lambda it: views.get(it[0].clip_id, "unknown")),
        ("label_quality", lambda it: it[1].visibility.value),
        ("overlay_wrist", lambda it: _overlay(it[1])),
        ("event_wrist", lambda it: _event_wrist(it, strokes)),
        ("clip", lambda it: it[0].clip_id),
    ]
    out: list[ImpactGroup] = []
    for name, key in dims:
        buckets: dict[str, list[Item]] = {}
        for it in items:
            buckets.setdefault(key(it), []).append(it)
        out += [_group(name, v, buckets[v]) for v in sorted(buckets)]
    return out


def _offsets(items: list[Item]) -> tuple[list[OffsetSet], list[OffsetRow], list[OffsetRow]]:
    rows: list[OffsetRow] = []
    window_only: list[OffsetRow] = []
    for c, lb, imp in items:
        inter = imp.interception
        if inter is None:
            continue
        for pk in inter.peaks:
            row = OffsetRow(
                clip_id=c.clip_id,
                label_line=lb.line,
                wrist=pk.wrist.value,
                peak_id=pk.peak_id,
                signed_ms=round(pk.signed_s * 1000, 1),
                signed_frames=pk.signed_frames,
                estimated_frame=lb.estimated_frame,
            )
            (rows if inter.event_signed_s is not None else window_only).append(row)
        if inter.event_signed_s is not None and inter.event_signed_frames is not None:
            rows.append(
                OffsetRow(
                    clip_id=c.clip_id,
                    label_line=lb.line,
                    wrist="event",
                    peak_id=None,
                    signed_ms=round(inter.event_signed_s * 1000, 1),
                    signed_frames=inter.event_signed_frames,
                    estimated_frame=lb.estimated_frame,
                )
            )
    sets: list[OffsetSet] = []
    for wrist in ("left", "right", "event"):
        used = [r for r in rows if r.wrist == wrist and not r.estimated_frame]
        excluded = sum(1 for r in rows if r.wrist == wrist and r.estimated_frame)
        sets.append(
            OffsetSet(
                wrist=wrist,
                signed_ms=distribution(r.signed_ms for r in used),
                abs_ms=distribution(abs(r.signed_ms) for r in used),
                signed_frames=distribution(float(r.signed_frames) for r in used),
                abs_frames=distribution(float(abs(r.signed_frames)) for r in used),
                excluded_estimated=excluded,
            )
        )
    key = lambda r: (r.clip_id, r.label_line, r.wrist, r.peak_id or "")  # noqa: E731
    return sets, sorted(rows, key=key), sorted(window_only, key=key)


def _event_stats(
    doc: StrokesEvalFile, radius: float, strokes: dict[str, LoadedStrokes]
) -> EventStats:
    graph = one = two = undetermined = merge = 0
    rejected = rejected_near = 0
    available = False
    for c in doc.clips:
        loaded = strokes.get(c.clip_id)
        sdoc = loaded.doc if loaded is not None else None
        targets = [lb for lb in c.labels if lb.role is LabelRole.TARGET]
        for e in c.events:
            if not e.in_graph:
                rejected += 1
                node = EventNode(e.id, *(e.window_t_s if e.t_s is None else (e.t_s, e.t_s)))
                if any(distance(node, lb.t_s) <= radius + 1e-9 for lb in targets):
                    rejected_near += 1
        if sdoc is None:
            continue
        available = True
        for ev in sdoc.events:
            if ev.status.value == "rejected":
                continue
            graph += 1
            wrists = sum(
                w.state is EvidenceState.PEAKS for w in (ev.evidence.left, ev.evidence.right)
            )
            one += wrists == 1
            two += wrists == 2
            undetermined += ev.representative_wrist.value.value == "undetermined"
            merge += ev.grouping.possible_merge
    return EventStats(
        available=available,
        graph_events=graph,
        one_wrist=_ratio(one, graph),
        two_wrists=_ratio(two, graph),
        undetermined_wrist=_ratio(undetermined, graph),
        possible_merge=_ratio(merge, graph),
        rejected_near_impact=_ratio(rejected_near, rejected),
    )


def _wrist_check(items: list[Item], strokes: dict[str, LoadedStrokes]) -> list[WristCheckRow]:
    counts: dict[tuple[str, str], int] = {}
    for c, lb, imp in items:
        if imp.interception is None:
            continue
        ev = _doc_event(strokes.get(c.clip_id), imp.interception.event_id)
        state = ev.regions.right_wrist.state.value if ev is not None else "unknown"
        key = (state, _overlay(lb))
        counts[key] = counts.get(key, 0) + 1
    return [
        WristCheckRow(right_wrist_state=s, overlay_wrist=o, impacts=n)
        for (s, o), n in sorted(counts.items())
    ]


# ------------------------------------------------------------------ false candidates


def false_candidates(
    doc: StrokesEvalFile, strokes: dict[str, LoadedStrokes]
) -> list[FalseCandidate]:
    """One row per event unassociated at one or more radii (all of them, none dropped)."""
    out: list[FalseCandidate] = []
    for c in doc.clips:
        events = {e.id: e for e in c.events}
        seen: dict[str, list[tuple[float, UnassociatedEvent]]] = {}
        for rr in c.radii:
            for ue in rr.unassociated_events:
                seen.setdefault(ue.event_id, []).append((rr.radius_s, ue))
        for eid, ues in sorted(seen.items()):
            first = ues[0][1]
            ev = events[eid]
            dev = _doc_event(strokes.get(c.clip_id), eid)
            out.append(
                FalseCandidate(
                    clip_id=c.clip_id,
                    event_id=eid,
                    strokes_sha256=c.strokes.sha256,
                    event_t_s=ev.t_s if ev.t_s is not None else ev.window_t_s[0],
                    radii_unassociated=[r for r, _ in ues],
                    nearest_target_line=(
                        first.nearest_target.label_line if first.nearest_target else None
                    ),
                    nearest_target_signed_ms=(
                        round(first.nearest_target.signed_s * 1000, 1)
                        if first.nearest_target
                        else None
                    ),
                    wrists_with_peaks=(
                        [
                            w
                            for w in ("left", "right")
                            if getattr(dev.evidence, w).state is EvidenceState.PEAKS
                        ]
                        if dev is not None
                        else []
                    ),
                    regions=(
                        {
                            name: getattr(dev.regions, name).state.value
                            for name in ("torso", "left_wrist", "right_wrist", "legs")
                        }
                        if dev is not None
                        else {}
                    ),
                    verdicts={f"{r:g}": u.verdict.value for r, u in ues},
                    auto_causes={f"{r:g}": u.cause.value for r, u in ues if u.cause is not None},
                    in_review_sheet=any(u.position is Position.INSIDE for _, u in ues),
                    category=None,
                )
            )
    return out


def render_review_csv(candidates: list[FalseCandidate]) -> str:
    """The review sheet: rows in the sheet, ``category`` empty unless already reviewed."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(REVIEW_COLUMNS)
    for fc in candidates:
        if not fc.in_review_sheet:
            continue
        w.writerow(
            [
                fc.clip_id,
                fc.event_id,
                fc.strokes_sha256,
                f"{fc.event_t_s:.4f}",
                ";".join(f"{r:g}" for r in fc.radii_unassociated),
                "" if fc.nearest_target_line is None else fc.nearest_target_line,
                "" if fc.nearest_target_signed_ms is None else f"{fc.nearest_target_signed_ms:g}",
                ";".join(f"{k}={v}" for k, v in fc.verdicts.items()),
                ";".join(f"{k}={v}" for k, v in fc.auto_causes.items()),
                "" if fc.category is None else fc.category.value,
                "",
            ]
        )
    return buf.getvalue()


class ReviewRow(StrictModel):
    line: int
    clip_id: str
    event_id: str
    strokes_sha256: str
    category: ReviewCategory | None
    notes: str


def load_review(path: str | Path) -> list[ReviewRow]:
    """Read a filled review sheet; an unknown category or a duplicate row is an error."""
    p = Path(path)
    if not p.is_file():
        raise DatasetFormatError(
            "Review sheet not found", details={"problems": [f"{p.name}: file does not exist"]}
        )
    with p.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or tuple(c.strip() for c in rows[0]) != REVIEW_COLUMNS:
        raise DatasetFormatError(
            "Review sheet header is invalid",
            details={"problems": [f"{p.name}:1: header must be {','.join(REVIEW_COLUMNS)}"]},
        )
    allowed = {c.value: c for c in ReviewCategory}
    problems: list[str] = []
    out: list[ReviewRow] = []
    seen: dict[tuple[str, str, str], int] = {}
    for n, cells in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(REVIEW_COLUMNS):
            problems.append(f"{p.name}:{n}: {len(cells)} fields, expected {len(REVIEW_COLUMNS)}")
            continue
        row = dict(zip(REVIEW_COLUMNS, (c.strip() for c in cells), strict=True))
        cat = row["category"]
        if cat and cat not in allowed:
            problems.append(
                f"{p.name}:{n}: category {cat!r} is not one of {', '.join(allowed)} (or empty)"
            )
            continue
        key = (row["clip_id"], row["event_id"], row["strokes_sha256"])
        if key in seen:
            problems.append(f"{p.name}:{n}: same clip, event and sha256 as line {seen[key]}")
            continue
        seen[key] = n
        out.append(
            ReviewRow(
                line=n,
                clip_id=row["clip_id"],
                event_id=row["event_id"],
                strokes_sha256=row["strokes_sha256"],
                category=allowed[cat] if cat else None,
                notes=row["notes"],
            )
        )
    if problems:
        raise DatasetFormatError("Review sheet has invalid rows", details={"problems": problems})
    return out


def merge_review(
    candidates: list[FalseCandidate], review: list[ReviewRow]
) -> tuple[list[FalseCandidate], list[StaleReviewRow]]:
    """Carry categories over by (clip_id, event_id, sha256); report the rows not reused."""
    index = {(fc.clip_id, fc.event_id): fc for fc in candidates}
    stale: list[StaleReviewRow] = []
    merged = {k: fc.model_copy() for k, fc in index.items()}
    for row in review:
        fc = merged.get((row.clip_id, row.event_id))
        if fc is None:
            reason = "event not among the current unassociated events"
        elif fc.strokes_sha256 != row.strokes_sha256:
            reason = "strokes.json changed (sha256 differs): event ids may not match"
        else:
            fc.category = row.category
            continue
        if row.category is not None:
            stale.append(
                StaleReviewRow(
                    line=row.line, clip_id=row.clip_id, event_id=row.event_id, reason=reason
                )
            )
    return [merged[(fc.clip_id, fc.event_id)] for fc in candidates], stale


# ------------------------------------------------------------------ report


def build_report(
    doc: StrokesEvalFile,
    clips: dict[str, ManifestClip],
    strokes: dict[str, LoadedStrokes],
    review: list[ReviewRow] | None = None,
) -> EvalReport:
    """All tables for every radius of the evaluation. ``strokes`` may miss clips: their
    event-level columns are then ``unknown`` / not available, never guessed."""
    check_strokes_match(doc, strokes)
    views = {cid: c.view.value for cid, c in clips.items()}
    candidates = false_candidates(doc, strokes)
    stale: list[StaleReviewRow] = []
    if review is not None:
        candidates, stale = merge_review(candidates, review)
    radii: list[RadiusReport] = []
    for r in doc.params.radii_s:
        items = _items(doc, r)
        sets, rows, window_only = _offsets(items)
        statuses = [rr.status.value for c in doc.clips for rr in c.radii if rr.radius_s == r]
        verdicts: dict[str, int] = {v.value: 0 for v in Verdict}
        for c in doc.clips:
            for rr in c.radii:
                if rr.radius_s == r:
                    for ue in rr.unassociated_events:
                        verdicts[ue.verdict.value] += 1
        cats: dict[str, int] = {c.value: 0 for c in ReviewCategory}
        cats["not_reviewed"] = 0
        for fc in candidates:
            if fc.in_review_sheet and r in fc.radii_unassociated:
                cats[fc.category.value if fc.category else "not_reviewed"] += 1
        radii.append(
            RadiusReport(
                radius_s=r,
                interpretable_clips=_ratio(statuses.count("ok"), len(statuses)),
                groups=_groups(items, views, strokes),
                offsets=sets,
                offset_rows=rows,
                window_only_events=window_only,
                events=_event_stats(doc, r, strokes),
                right_wrist_vs_overlay=_wrist_check(items, strokes),
                unassociated_by_verdict=verdicts,
                category_counts=cats,
            )
        )
    return EvalReport(
        clips=[c.clip_id for c in doc.clips],
        producers={c.clip_id: c.strokes.producer or c.strokes.format.value for c in doc.clips},
        params=doc.params.model_dump(mode="json"),
        radii=radii,
        false_candidates=candidates,
        stale_review_rows=stale,
    )


def _r(x: Ratio) -> str:
    return f"{x.n}/{x.of}"


def _num(v: float | None, fmt: str = "g") -> str:
    return "—" if v is None else format(v, fmt)


def _dist(d: Distribution) -> str:
    vals = (d.min, d.q1, d.median, d.q3, d.p90, d.max)
    return f"{d.n} | " + " | ".join(_num(v) for v in vals)


def render_report(rep: EvalReport, *, source: str) -> str:
    """Markdown report. The data is in :class:`EvalReport` (JSON)."""
    lines = [
        "# M2.3 evaluation report",
        "",
        f"Evaluation: `{source}`. Clips: {len(rep.clips)}.",
        "",
        "- Strokes producers: " + ", ".join(f"{c}: {p}" for c, p in sorted(rep.producers.items())),
        "- Radii and the search limit are exploratory parameters, not acceptance criteria.",
        "- Every number is n/total. Groups with fewer than "
        f"{MIN_CLIPS_PER_GROUP} clips are marked (small sample).",
        "- Offsets: 1-1 groups only; estimated frames excluded from the statistics; "
        "percentiles by nearest rank; ms and frames never converted into each other.",
        "- These numbers describe the given strokes files only. They are not a validation "
        "unless the files come from the real detector.",
        "",
    ]
    for rr in rep.radii:
        lines += [
            f"## Radius {rr.radius_s:g} s",
            "",
            f"Clips with a compatible radius (2r + W < D): {_r(rr.interpretable_clips)}.",
            "",
            "### Impacts",
            "",
            "| dimension | value | clips | impacts | intercepted | uncertain | missed "
            "| ambiguous | note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for g in rr.groups:
            lines.append(
                f"| {g.dimension} | {g.value} | {g.clips} | {g.impacts} | {_r(g.intercepted)} "
                f"| {_r(g.intercepted_uncertain)} | {_r(g.missed)} | {_r(g.ambiguous)} "
                f"| {'small sample' if g.small_sample else ''} |"
            )
        allg = next((g for g in rr.groups if g.dimension == "all"), None)
        if allg is not None:
            lines += [
                "",
                "Miss causes (of missed): "
                + ", ".join(f"{k} {_r(v)}" for k, v in allg.miss_causes.items()),
                "",
            ]
        lines += [
            "### Offsets (label → peak or event instant)",
            "",
            "| wrist | unit | n | min | Q1 | median | Q3 | P90 | max | estimated excluded |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in rr.offsets:
            for unit, d in (
                ("signed ms", s.signed_ms),
                ("abs ms", s.abs_ms),
                ("signed frames", s.signed_frames),
                ("abs frames", s.abs_frames),
            ):
                lines.append(f"| {s.wrist} | {unit} | {_dist(d)} | {s.excluded_estimated} |")
        if rr.window_only_events:
            lines += [
                "",
                "Window-only events (no instant, listed apart): "
                + ", ".join(f"{w.clip_id}:{w.label_line}" for w in rr.window_only_events),
            ]
        ev = rr.events
        lines += ["", "### Events", ""]
        if ev.available:
            lines += [
                f"- graph events {ev.graph_events}: one wrist {_r(ev.one_wrist)}, two wrists "
                f"{_r(ev.two_wrists)}, undetermined wrist {_r(ev.undetermined_wrist)}, "
                f"possible merge {_r(ev.possible_merge)}"
            ]
        else:
            lines += ["- event details not available (no strokes 1.x file)"]
        lines += [f"- rejected events within r of a target impact: {_r(ev.rejected_near_impact)}"]
        if rr.right_wrist_vs_overlay:
            lines += [
                "",
                "Right-wrist region of the associated event vs overlay_wrist:",
                "",
                "| right wrist | overlay_wrist | impacts |",
                "| --- | --- | --- |",
            ]
            lines += [
                f"| {w.right_wrist_state} | {w.overlay_wrist} | {w.impacts} |"
                for w in rr.right_wrist_vs_overlay
            ]
        lines += [
            "",
            "### Unassociated events",
            "",
            "- by verdict: " + ", ".join(f"{k} {v}" for k, v in rr.unassociated_by_verdict.items()),
            "- review categories (sheet rows at this radius): "
            + ", ".join(f"{k} {v}" for k, v in rr.category_counts.items()),
            "",
        ]
    lines += [
        "## False candidates",
        "",
        "| clip | event | t (s) | unassociated at r | nearest target (ms) | wrists "
        "| verdicts | causes | sheet | category |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for fc in rep.false_candidates:
        near = (
            "—"
            if fc.nearest_target_line is None
            else f"line {fc.nearest_target_line}: {_num(fc.nearest_target_signed_ms)}"
        )
        lines.append(
            f"| {fc.clip_id} | {fc.event_id} | {fc.event_t_s:.3f} | "
            f"{', '.join(f'{r:g}' for r in fc.radii_unassociated)} | {near} | "
            f"{', '.join(fc.wrists_with_peaks) or '—'} | "
            f"{'; '.join(f'{k}: {v}' for k, v in fc.verdicts.items())} | "
            f"{'; '.join(f'{k}: {v}' for k, v in fc.auto_causes.items()) or '—'} | "
            f"{'yes' if fc.in_review_sheet else 'no'} | "
            f"{fc.category.value if fc.category else ''} |"
        )
    if rep.stale_review_rows:
        lines += ["", "## Review rows not reused", ""]
        lines += [
            f"- line {s.line} ({s.clip_id} {s.event_id}): {s.reason}" for s in rep.stale_review_rows
        ]
    return "\n".join(lines) + "\n"
