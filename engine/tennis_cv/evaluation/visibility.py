"""Input quality around labelled impacts: what MediaPipe reports, and nothing more.

For every impact labelled for the analysed player (player ``A``, any stroke type except
``none``) this module looks at the pose frames within ``± window_s`` of the labelled
frame and reports three separate things:

1. **MediaPipe self-report** - pose detected, landmark ``visibility`` score. The score is
   the estimator's own likelihood that a joint is visible. It is *not* an accuracy: a
   wrist can score 0.9 and be drawn on the racket or on another person.
2. **Continuity** - whether the frames above the score threshold form an unbroken run
   through the impact, whether the body reference (both shoulders and hips) is
   available, and whether the proxy repeats source frames there (VFR). These are
   *necessary* conditions for future velocity/acceleration signals, never sufficient
   ones: continuity says nothing about where the landmark actually is.
3. **Human check** - the ``overlay_wrist`` label column, where the person labelling says
   whether the drawn hitting wrist is on the real wrist at the impact frame. This is
   the only evidence here about real observability, and only for one joint at one frame.

No stroke detector is evaluated here and no accuracy of any future detector is implied.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from pydantic import Field

from tennis_cv.common import StrictModel
from tennis_cv.evaluation.dataset import (
    ANALYSED_PLAYER,
    ClipData,
    Hand,
    Label,
    LabelStrokeType,
)
from tennis_cv.pose.models import PoseFrame

DEFAULT_WINDOW_S = 0.3
DEFAULT_VISIBILITY_THRESHOLD = 0.5
"""A MediaPipe score threshold for counting, not a validated reliability threshold."""

JOINTS: tuple[str, ...] = ("wrist", "elbow", "shoulder")
BODY_REFERENCE: tuple[str, ...] = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")


class JointWindow(StrictModel):
    """One joint of one side in the window around one impact."""

    joint: str = Field(description="e.g. hitting_wrist, or left_wrist if the hand is unknown")
    landmark: str = Field(description="MediaPipe landmark name, e.g. right_wrist")
    visibility_at_impact: float | None = Field(
        description="None when the impact frame was not analysed or no pose was detected"
    )
    above_threshold_at_impact: bool
    frames_above_threshold: int
    run_through_impact: int = Field(
        description="Analysed frames in the unbroken above-threshold run containing the impact"
    )
    run_covers_window: bool


class ImpactVisibility(StrictModel):
    clip_id: str
    label_line: int
    proxy_frame: int
    stroke_type: LabelStrokeType
    label_visibility: str = Field(description="Human judgement of the impact (full/partial/none)")
    window_first_frame: int
    window_last_frame: int
    frames_in_window: int
    frames_analysed: int = Field(description="Pose frames present (pose_stride may skip)")
    frames_with_pose: int
    pose_at_impact: bool
    body_reference_at_impact: bool = Field(
        description="Both shoulders and both hips above threshold at the impact frame"
    )
    repeated_proxy_frames: int | None = Field(
        description="Proxy frames in the window repeating the previous source frame "
        "(VFR resampling). None without timemap.json"
    )
    joints: list[JointWindow]
    overlay_wrist: str | None = Field(description="Human check from labels.csv; None = not done")


def _side_joints(hand: Hand) -> list[tuple[str, str]]:
    """(report name, landmark) pairs. Hitting side = declared dominant hand only."""
    if hand is Hand.UNKNOWN:
        return [(f"{s}_{j}", f"{s}_{j}") for j in JOINTS for s in ("left", "right")]
    hit = hand.value
    off = "left" if hand is Hand.RIGHT else "right"
    return [(f"hitting_{j}", f"{hit}_{j}") for j in JOINTS] + [
        (f"non_hitting_{j}", f"{off}_{j}") for j in JOINTS
    ]


def _score(frame: PoseFrame | None, landmark: str) -> float | None:
    if frame is None or not frame.detected:
        return None
    for kp in frame.keypoints:
        if kp.name == landmark:
            return kp.visibility
    return None


def analyse_impacts(
    data: ClipData,
    labels: list[Label],
    *,
    window_s: float = DEFAULT_WINDOW_S,
    threshold: float = DEFAULT_VISIBILITY_THRESHOLD,
) -> list[ImpactVisibility]:
    if window_s < 0:
        raise ValueError("window_s must be >= 0")
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be in [0, 1]")
    by_index = {f.frame_index: f for f in data.poses.frames}
    analysed_sorted = sorted(by_index)
    half = int(round(window_s * data.fps))
    last_frame = max(data.frame_count - 1, 0)
    out: list[ImpactVisibility] = []
    for lb in sorted(labels, key=lambda x: x.proxy_frame):
        if lb.clip_id != data.clip.clip_id or lb.player != ANALYSED_PLAYER:
            continue
        if lb.stroke_type is LabelStrokeType.NONE:
            continue
        lo = max(0, lb.proxy_frame - half)
        hi = min(last_frame, lb.proxy_frame + half)
        window = [i for i in analysed_sorted if lo <= i <= hi]
        impact = by_index.get(lb.proxy_frame)

        def ok(frame: PoseFrame | None, landmark: str) -> bool:
            s = _score(frame, landmark)
            return s is not None and s >= threshold

        joints: list[JointWindow] = []
        for name, landmark in _side_joints(data.clip.dominant_hand):
            flags = [ok(by_index[i], landmark) for i in window]
            run = 0
            covers = False
            if impact is not None and lb.proxy_frame in window:
                pos = window.index(lb.proxy_frame)
                if flags[pos]:
                    left = pos
                    while left > 0 and flags[left - 1]:
                        left -= 1
                    right = pos
                    while right < len(flags) - 1 and flags[right + 1]:
                        right += 1
                    run = right - left + 1
                    covers = run == len(window)
            joints.append(
                JointWindow(
                    joint=name,
                    landmark=landmark,
                    visibility_at_impact=_score(impact, landmark),
                    above_threshold_at_impact=ok(impact, landmark),
                    frames_above_threshold=sum(flags),
                    run_through_impact=run,
                    run_covers_window=covers,
                )
            )
        repeated: int | None = None
        if data.timemap is not None:
            mapping = data.timemap.proxy_to_source
            repeated = sum(
                1
                for k in range(lo + 1, hi + 1)
                if k < len(mapping) and mapping[k] == mapping[k - 1]
            )
        out.append(
            ImpactVisibility(
                clip_id=lb.clip_id,
                label_line=lb.line,
                proxy_frame=lb.proxy_frame,
                stroke_type=lb.stroke_type,
                label_visibility=lb.visibility.value,
                window_first_frame=lo,
                window_last_frame=hi,
                frames_in_window=hi - lo + 1,
                frames_analysed=len(window),
                frames_with_pose=sum(1 for i in window if by_index[i].detected),
                pose_at_impact=impact is not None and impact.detected,
                body_reference_at_impact=all(ok(impact, b) for b in BODY_REFERENCE),
                repeated_proxy_frames=repeated,
                joints=joints,
                overlay_wrist=lb.overlay_wrist.value if lb.overlay_wrist else None,
            )
        )
    return out


class Ratio(StrictModel):
    """A count with its denominator. Never reported as a bare percentage."""

    n: int
    of: int


class JointSummary(StrictModel):
    joint: str
    above_threshold_at_impact: Ratio
    run_covers_window: Ratio
    median_visibility_at_impact: float | None = Field(
        description="Over impacts where a score exists; None when there is none"
    )


class GroupSummary(StrictModel):
    dimension: str
    value: str
    clips: int
    impacts: int
    pose_at_impact: Ratio
    body_reference_at_impact: Ratio
    windows_with_repeated_frames: Ratio = Field(description="Denominator: impacts with timemap")
    joints: list[JointSummary]
    overlay_wrist: dict[str, int] = Field(
        description="Human check counts (ok/off/missing/other_person/not_checked)"
    )
    hitting_wrist_cross: dict[str, int] = Field(
        description="MediaPipe hitting wrist at impact (above/below threshold) x human check; "
        "only impacts with a declared hand and a human check"
    )
    small_sample: bool = Field(description="Fewer than MIN_CLIPS_PER_GROUP clips")


MIN_CLIPS_PER_GROUP = 3


def summarize_group(dimension: str, value: str, items: list[ImpactVisibility]) -> GroupSummary:
    joint_names: list[str] = []
    for it in items:
        for j in it.joints:
            if j.joint not in joint_names:
                joint_names.append(j.joint)
    joints = []
    for name in joint_names:
        rows = [j for it in items for j in it.joints if j.joint == name]
        scores = [j.visibility_at_impact for j in rows if j.visibility_at_impact is not None]
        joints.append(
            JointSummary(
                joint=name,
                above_threshold_at_impact=Ratio(
                    n=sum(j.above_threshold_at_impact for j in rows), of=len(rows)
                ),
                run_covers_window=Ratio(n=sum(j.run_covers_window for j in rows), of=len(rows)),
                median_visibility_at_impact=round(median(scores), 3) if scores else None,
            )
        )
    human: dict[str, int] = defaultdict(int)
    cross: dict[str, int] = defaultdict(int)
    for it in items:
        human[it.overlay_wrist or "not_checked"] += 1
        hit = next((j for j in it.joints if j.joint == "hitting_wrist"), None)
        if hit is not None and it.overlay_wrist is not None:
            mp = "mp_above" if hit.above_threshold_at_impact else "mp_below"
            cross[f"{mp}/{it.overlay_wrist}"] += 1
    with_tm = [it for it in items if it.repeated_proxy_frames is not None]
    clips = len({it.clip_id for it in items})
    return GroupSummary(
        dimension=dimension,
        value=value,
        clips=clips,
        impacts=len(items),
        pose_at_impact=Ratio(n=sum(it.pose_at_impact for it in items), of=len(items)),
        body_reference_at_impact=Ratio(
            n=sum(it.body_reference_at_impact for it in items), of=len(items)
        ),
        windows_with_repeated_frames=Ratio(
            n=sum(1 for it in with_tm if (it.repeated_proxy_frames or 0) > 0), of=len(with_tm)
        ),
        joints=joints,
        overlay_wrist=dict(sorted(human.items())),
        hitting_wrist_cross=dict(sorted(cross.items())),
        small_sample=clips < MIN_CLIPS_PER_GROUP,
    )
