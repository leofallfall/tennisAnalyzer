"""Grouping and Markdown rendering for the M2.1 evaluation reports.

Every number is printed with its denominator, every group with its clip count, and
small samples are flagged instead of being presented as robust results.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.evaluation.dataset import ClipData, Split
from tennis_cv.evaluation.matching import ClipMatch, EventStatus, EventType
from tennis_cv.evaluation.visibility import (
    MIN_CLIPS_PER_GROUP,
    GroupSummary,
    ImpactVisibility,
    Ratio,
    summarize_group,
)

MIN_CLIPS_PER_SPLIT = 5
"""Below this many clips in the dev or the test split, the split is declared not
meaningful. A convention for honest reporting, not a statistical guarantee."""

CLIP_DIMENSIONS: tuple[str, ...] = (
    "view",
    "proxy_fps",
    "source_frame_rate",
    "other_people",
    "split",
)


def clip_groups(data: ClipData) -> dict[str, str]:
    """The values of each reporting dimension for one clip (derived where possible)."""
    rates = {None: "unknown", True: "vfr", False: "cfr"}
    rate = rates[data.source_vfr]
    return {
        "view": data.clip.view.value,
        "proxy_fps": f"{data.fps:g}",
        "source_frame_rate": rate,
        "other_people": "yes" if data.clip.other_people else "no",
        "split": data.clip.split.value,
    }


class SplitAssessment(StrictModel):
    dev_clips: int
    test_clips: int
    unassigned_clips: int
    meaningful: bool
    statement: str


def assess_split(clips: list[ClipData]) -> SplitAssessment:
    counts = {s: sum(1 for c in clips if c.clip.split is s) for s in Split}
    dev, test = counts[Split.DEV], counts[Split.TEST]
    meaningful = dev >= MIN_CLIPS_PER_SPLIT and test >= MIN_CLIPS_PER_SPLIT
    if meaningful:
        statement = f"Dev/test split by clip: {dev} dev, {test} test."
    else:
        statement = (
            f"No meaningful dev/test split: {dev} dev and {test} test clips (minimum "
            f"{MIN_CLIPS_PER_SPLIT} each). Results below describe these clips only and "
            "must not be read as a general estimate."
        )
    return SplitAssessment(
        dev_clips=dev,
        test_clips=test,
        unassigned_clips=counts[Split.UNASSIGNED],
        meaningful=meaningful,
        statement=statement,
    )


# ---------------------------------------------------------------- visibility report


def visibility_groups(
    items: list[ImpactVisibility], groups_of: dict[str, dict[str, str]]
) -> list[GroupSummary]:
    out = [summarize_group("all", "all", items)]
    dims: list[tuple[str, dict[str, list[ImpactVisibility]]]] = []
    for dim in CLIP_DIMENSIONS:
        by: dict[str, list[ImpactVisibility]] = defaultdict(list)
        for it in items:
            by[groups_of[it.clip_id][dim]].append(it)
        dims.append((dim, by))
    for dim, attr in (("stroke_type", "stroke_type"), ("label_visibility", "label_visibility")):
        by = defaultdict(list)
        for it in items:
            by[str(getattr(it, attr))].append(it)
        dims.append((dim, by))
    for dim, by in dims:
        for value in sorted(by):
            out.append(summarize_group(dim, value, by[value]))
    return out


def _r(r: Ratio) -> str:
    return f"{r.n}/{r.of}"


def render_visibility(
    groups: list[GroupSummary],
    split: SplitAssessment,
    *,
    window_s: float,
    threshold: float,
) -> str:
    lines = [
        "# M2.1 input visibility report",
        "",
        "Measures the pose input around labelled impacts of player A. It evaluates no "
        "stroke detector and implies no detector accuracy.",
        "",
        f"- Window: ±{window_s:g} s around the labelled frame. MediaPipe score threshold: "
        f"{threshold:g} (a counting threshold, not a validated reliability level).",
        f"- {split.statement}",
        f"- Groups with fewer than {MIN_CLIPS_PER_GROUP} clips are marked (small sample).",
        "",
        "Read the three parts separately:",
        "",
        "1. **MediaPipe self-report**: the estimator's own visibility score. Not an accuracy.",
        "2. **Continuity**: necessary, not sufficient, for future velocities/accelerations. "
        "A continuous, high-score landmark can still be in the wrong place.",
        "3. **Human check** (`overlay_wrist`): the only evidence here of where the wrist "
        "really is, for one joint at one frame.",
        "",
    ]
    for g in groups:
        flag = " (small sample)" if g.small_sample else ""
        lines += [
            f"## {g.dimension} = {g.value}: {g.impacts} impacts, {g.clips} clips{flag}",
            "",
            "**1. MediaPipe self-report**",
            "",
            f"Pose detected at the impact frame: {_r(g.pose_at_impact)}",
            "",
            "| Joint | Score ≥ threshold at impact | Median score at impact |",
            "| --- | --- | --- |",
        ]
        for j in g.joints:
            med = (
                "n/a"
                if j.median_visibility_at_impact is None
                else f"{j.median_visibility_at_impact:.3f}"
            )
            lines.append(f"| {j.joint} | {_r(j.above_threshold_at_impact)} | {med} |")
        lines += [
            "",
            "**2. Continuity (necessary, not sufficient)**",
            "",
            f"Body reference (both shoulders and hips ≥ threshold) at impact: "
            f"{_r(g.body_reference_at_impact)}. Windows with repeated proxy frames (VFR): "
            f"{_r(g.windows_with_repeated_frames)} (denominator: impacts with timemap).",
            "",
            "| Joint | Unbroken ≥ threshold over the whole window |",
            "| --- | --- |",
        ]
        for j in g.joints:
            lines.append(f"| {j.joint} | {_r(j.run_covers_window)} |")
        human = ", ".join(f"{k}: {v}" for k, v in g.overlay_wrist.items())
        cross = ", ".join(f"{k}: {v}" for k, v in g.hitting_wrist_cross.items()) or "none"
        lines += [
            "",
            "**3. Human check of the hitting wrist on the overlay**",
            "",
            f"Labels: {human}. MediaPipe score vs human check: {cross}.",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------- comparison report


class MatchSummary(StrictModel):
    dimension: str
    value: str
    clips: int
    target_labels: int
    events_considered: int
    matched: int
    misses: int
    misses_by_visibility: dict[str, int]
    false_positives: int
    false_positives_by_cause: dict[str, int]
    detection_recall: Ratio = Field(description="matched / target labels")
    detection_precision: Ratio = Field(description="matched / events considered")
    median_error_ms: float | None = Field(description="Signed, event minus label")
    p90_abs_error_ms: float | None
    uncertain_matched: int
    confusion: dict[str, int] = Field(description="'true->predicted' counts on matched events")
    classified_correct: Ratio = Field(
        description="Matched forehand/backhand labels whose event says the same type; "
        "denominator excludes 'unknown' predictions (abstentions)"
    )
    abstentions: Ratio = Field(description="unknown predictions / matched forehand+backhand")
    small_sample: bool


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)  # nearest-rank percentile
    return round(ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)], 1)


def summarize_matches(dimension: str, value: str, matches: list[ClipMatch]) -> MatchSummary:
    matched = [m for cm in matches for m in cm.matched]
    misses = [m for cm in matches for m in cm.misses]
    fps_ = [f for cm in matches for f in cm.false_positives]
    considered = sum(cm.events_considered for cm in matches)
    targets = len(matched) + len(misses)
    by_vis: dict[str, int] = defaultdict(int)
    for miss in misses:
        by_vis[miss.visibility] += 1
    by_cause: dict[str, int] = defaultdict(int)
    for f in fps_:
        by_cause[f.cause.value] += 1
    confusion: dict[str, int] = defaultdict(int)
    for m in matched:
        confusion[f"{m.true_type.value}->{m.predicted_type.value}"] += 1
    fh_bh = [m for m in matched if m.true_type.value in ("forehand", "backhand")]
    decided = [m for m in fh_bh if m.predicted_type is not EventType.UNKNOWN]
    errors = [m.error_ms for m in matched]
    clips = len(matches)
    return MatchSummary(
        dimension=dimension,
        value=value,
        clips=clips,
        target_labels=targets,
        events_considered=considered,
        matched=len(matched),
        misses=len(misses),
        misses_by_visibility=dict(sorted(by_vis.items())),
        false_positives=len(fps_),
        false_positives_by_cause=dict(sorted(by_cause.items())),
        detection_recall=Ratio(n=len(matched), of=targets),
        detection_precision=Ratio(n=len(matched), of=considered),
        median_error_ms=round(median(errors), 1) if errors else None,
        p90_abs_error_ms=_p90([abs(e) for e in errors]),
        uncertain_matched=sum(1 for m in matched if m.status is EventStatus.UNCERTAIN),
        confusion=dict(sorted(confusion.items())),
        classified_correct=Ratio(
            n=sum(1 for m in decided if m.predicted_type.value == m.true_type.value),
            of=len(decided),
        ),
        abstentions=Ratio(n=len(fh_bh) - len(decided), of=len(fh_bh)),
        small_sample=clips < MIN_CLIPS_PER_GROUP,
    )


def match_groups(
    matches: list[ClipMatch], groups_of: dict[str, dict[str, str]]
) -> list[MatchSummary]:
    out = [summarize_matches("all", "all", matches)]
    for dim in CLIP_DIMENSIONS:
        by: dict[str, list[ClipMatch]] = defaultdict(list)
        for cm in matches:
            by[groups_of[cm.clip_id][dim]].append(cm)
        for value in sorted(by):
            out.append(summarize_matches(dim, value, by[value]))
    return out


def render_matches(
    summaries: list[MatchSummary], split: SplitAssessment, *, tolerance_s: float, source: str
) -> str:
    lines = [
        "# M2.1 label comparison",
        "",
        f"- Events compared: {source}.",
        f"- Matching tolerance: ±{tolerance_s:g} s around the labelled frame "
        "(a parameter, not a validated value).",
        f"- {split.statement}",
        f"- Groups with fewer than {MIN_CLIPS_PER_GROUP} clips are marked (small sample).",
        "",
        "| Group | Clips | Matched / targets | Matched / events | False positives (cause) "
        "| Misses (label visibility) | Median error ms | p90 abs error ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def fmt(d: dict[str, int]) -> str:
        return ", ".join(f"{k} {v}" for k, v in d.items()) or "-"

    def num(v: float | None) -> str:
        return "n/a" if v is None else f"{v:g}"

    for s in summaries:
        flag = " (small sample)" if s.small_sample else ""
        lines.append(
            f"| {s.dimension} = {s.value}{flag} | {s.clips} | {_r(s.detection_recall)} "
            f"| {_r(s.detection_precision)} "
            f"| {s.false_positives} ({fmt(s.false_positives_by_cause)}) "
            f"| {s.misses} ({fmt(s.misses_by_visibility)}) | {num(s.median_error_ms)} "
            f"| {num(s.p90_abs_error_ms)} |"
        )
    lines += [
        "",
        "## Forehand / backhand on matched events",
        "",
        "| Group | Confusion (true->predicted) | Same type / decided | Abstentions "
        "| Uncertain status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lines.append(
            f"| {s.dimension} = {s.value} | {fmt(s.confusion)} | {_r(s.classified_correct)} "
            f"| {_r(s.abstentions)} | {s.uncertain_matched} |"
        )
    lines.append("")
    return "\n".join(lines)
