"""M2.3 I2: detector chain (peaks -> events -> strokes.json 1.1) on synthetic poses.

Built cases with known speed bumps; nothing here is a biomechanical claim and nothing
validates the detector on real data."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from datetime import UTC, datetime
from fractions import Fraction
from math import erf, sqrt
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.signals import build_timebase, keypoint_velocity, torso_scale
from tennis_cv.signals.velocity import VelocityState
from tennis_cv.strokes.artifact import (
    EventStatus,
    PeakStatus,
    RegionName,
    RegionReason,
    RegionState,
    RepresentativeWristValue,
    StrokesFile,
    check_invariants,
    dump_strokes,
    load_strokes,
)
from tennis_cv.strokes.detector import (
    R_BELOW_MIN,
    R_DUPLICATE,
    R_HEIGHT_REJECT,
    R_HEIGHT_UNCERTAIN,
    R_MERGE,
    R_NO_CLIP_REF,
    R_OUT_OF_FRAME,
    R_PROXY,
    R_REP_ONLY_EDGE,
    R_TRACKING_JUMP,
    R_UNDETERMINED,
    DetectorParams,
    detect,
    detect_dir,
    truncate_inputs,
)
from tennis_cv.video.timemap import TimeMap, build_timemap

FPS = 30.0
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
Pos = Callable[[int], float]


def _bell(center: float, amp: float = 300.0, width: float = 4.0) -> Pos:
    """Position whose speed is a Gaussian bump peaking at ``center`` (frames)."""
    return lambda k: amp * (1 + erf((k - center) / (width * sqrt(2)))) / 2


def _sum(*fs: Pos) -> Pos:
    return lambda k: sum(f(k) for f in fs)


def _still(k: int) -> float:
    return 0.0


def _poses(
    right: Pos = _still,
    left: Pos = _still,
    n: int = 120,
    missing: Any = (),
    no_right: Any = (),
    no_left: Any = (),
    legs: bool = True,
) -> PoseSequence:
    """Torso 200 px tall; wrists move along x by ``right(k)`` / ``left(k)`` pixels.
    ``missing``: frames without a pose; ``no_right`` / ``no_left``: frames without that
    wrist."""
    frames = []
    for k in range(n):
        pts: dict[str, tuple[float, float]] = {
            "left_shoulder": (400.0, 500.0),
            "right_shoulder": (320.0, 500.0),
            "left_hip": (390.0, 700.0),
            "right_hip": (330.0, 700.0),
            "left_wrist": (100.0 + left(k), 650.0),
            "right_wrist": (100.0 + right(k), 600.0),
        }
        if legs:
            pts |= {
                "left_knee": (390.0, 850.0),
                "right_knee": (330.0, 850.0),
                "left_ankle": (390.0, 1000.0),
                "right_ankle": (330.0, 1000.0),
            }
        if k in no_right:
            del pts["right_wrist"]
        if k in no_left:
            del pts["left_wrist"]
        kps = [
            Keypoint(name=nm, x=px / 720, y=py / 1280, visibility=0.9, presence=0.9)
            for nm, (px, py) in pts.items()
        ]
        det = k not in missing
        frames.append(
            PoseFrame(
                frame_index=k, timestamp_s=k / FPS, detected=det, keypoints=kps if det else []
            )
        )
    return PoseSequence(
        estimator="t", model="t", image_width=720, image_height=1280, fps=FPS,
        frame_stride=1, frames=frames,
    )  # fmt: skip


ONE_PEAK_REF = DetectorParams(min_kept_peaks=1)
"""Short synthetic clips have few peaks: a reference height from one peak keeps the event
tests about grouping and regions independent of the height rules (tested apart below)."""


def _run(poses: PoseSequence, params: DetectorParams | None = ONE_PEAK_REF) -> StrokesFile:
    doc = detect(poses, pose_file="pose.json", pose_sha256="0" * 64, params=params, now=NOW)
    assert check_invariants(doc) == []
    return doc


# ------------------------------------------------------------------ events


def test_one_bell_gives_one_accepted_event_on_its_wrist() -> None:
    doc = _run(_poses(right=_bell(60)))
    assert doc.schema_version == "1.1"
    (e,) = doc.events
    assert e.status is EventStatus.ACCEPTED
    assert e.representative_wrist.value is RepresentativeWristValue.RIGHT
    assert e.time.ref_proxy_frame == 60
    assert e.time.ref_t_s == pytest.approx(2.0)
    assert e.evidence.right.peak_ids and not e.evidence.left.peak_ids
    assert e.reliability.state is RegionState.GOOD
    assert e.reliability.regions_used == [RegionName.TORSO, RegionName.RIGHT_WRIST]
    assert e.trace[-1].rule == "decision" and e.trace[-1].outcome.value == "accepted"
    grouped = [p for p in doc.wrist_peaks if p.status is PeakStatus.GROUPED]
    assert grouped and all(p.event_id == e.id for p in grouped)
    assert all(p.prominence is not None for p in doc.wrist_peaks)
    assert R_PROXY in e.reasons  # no time map: times are proxy times, said in words


def test_two_far_bells_give_two_events() -> None:
    doc = _run(_poses(right=_sum(_bell(30), _bell(90))))
    assert [e.time.ref_proxy_frame for e in doc.events] == [30, 90]
    assert all(e.status is EventStatus.ACCEPTED for e in doc.events)


def test_staggered_wrists_are_one_event_with_both_wrists_as_evidence() -> None:
    doc = _run(_poses(right=_bell(60, amp=300), left=_bell(64, amp=200)))
    (e,) = doc.events
    assert e.evidence.left.peak_ids and e.evidence.right.peak_ids
    assert e.representative_wrist.value is RepresentativeWristValue.RIGHT  # more prominent
    assert e.time.ref_proxy_frame == 60
    assert e.reliability.regions_used == [
        RegionName.TORSO,
        RegionName.LEFT_WRIST,
        RegionName.RIGHT_WRIST,
    ]
    assert len(e.grouping.links) == len(e.grouping.peak_ids) - 1


def test_long_chain_is_one_uncertain_event_never_split() -> None:
    # bells 8 frames (0.267 s) apart: every link <= G = 0.3 s, span 1.07 s > 1.0 s
    doc = _run(_poses(right=_sum(*(_bell(c, width=2.0) for c in (20, 28, 36, 44, 52)))))
    (e,) = doc.events
    assert e.grouping.possible_merge
    assert e.grouping.span_s > 1.0
    assert e.status is EventStatus.UNCERTAIN and R_MERGE in e.reasons


def test_g_separates_events_more_than_g_apart() -> None:
    bells = _sum(_bell(40, width=2.0), _bell(52, width=2.0))  # 0.4 s apart
    assert len(_run(_poses(right=bells)).events) == 2  # G = 0.3
    assert len(_run(_poses(right=bells), DetectorParams(g_s=0.5, min_kept_peaks=1)).events) == 1


def test_weak_bell_is_discarded_and_gives_no_event() -> None:
    doc = _run(_poses(right=_bell(60, amp=40)))  # about 0.6 torso lengths/s
    assert doc.events == []
    assert doc.wrist_peaks and all(p.status is PeakStatus.DISCARDED for p in doc.wrist_peaks)
    assert all(p.reasons == [R_BELOW_MIN] for p in doc.wrist_peaks)


def test_only_run_edge_peak_leaves_the_wrist_undetermined() -> None:
    # right wrist appears at frame 60, while already slowing down after a bump at 58
    doc = _run(_poses(right=_bell(58), no_right=range(60)))
    (e,) = doc.events
    assert e.representative_wrist.value is RepresentativeWristValue.UNDETERMINED
    assert e.representative_wrist.reasons == [R_REP_ONLY_EDGE]
    assert e.time.ref_t_s is None and e.time.ref_proxy_frame is None
    assert e.status is EventStatus.UNCERTAIN and R_UNDETERMINED in e.reasons
    peak = next(p for p in doc.wrist_peaks if p.event_id == e.id)
    assert peak.prominence is not None and peak.prominence.category.value == "run_edge"


# ------------------------------------------------------------------ height rules (V1 step 2)


def _height_clip() -> PoseSequence:
    """12 equal strokes (speed S), then one at 0.6 S and one at 0.33 S: the clip reference
    (90th percentile of kept peak speeds) is S, so rel_to_clip ~ 1, 0.6 and 0.33."""
    centres = [20 + 40 * i for i in range(12)]
    bells = [_bell(c, amp=300, width=2.0) for c in centres]
    bells += [_bell(500, amp=180, width=2.0), _bell(540, amp=100, width=2.0)]
    return _poses(right=_sum(*bells), n=580)


def test_height_rules_reject_low_and_flag_middle_events() -> None:
    doc = _run(_height_clip(), DetectorParams())
    by_frame = {e.time.ref_proxy_frame: e for e in doc.events}
    assert len(doc.events) == 14
    assert by_frame[20].status is EventStatus.ACCEPTED
    middle, low = by_frame[500], by_frame[540]
    assert middle.status is EventStatus.UNCERTAIN and R_HEIGHT_UNCERTAIN in middle.reasons
    assert R_HEIGHT_REJECT not in middle.reasons
    assert low.status is EventStatus.REJECTED and R_HEIGHT_REJECT in low.reasons
    peak = next(p for p in doc.wrist_peaks if p.proxy_frame == 540 and p.event_id == low.id)
    assert peak.speed.rel_to_clip == pytest.approx(1 / 3, abs=0.03)
    step = next(s for s in low.trace if s.rule == R_HEIGHT_REJECT)
    assert step.param == "decision.t_reject" and step.outcome.value == "fail"
    assert low.trace[-1].outcome.value == "rejected"


@pytest.mark.parametrize(("t_reject", "t_accept", "expected"), [
    (0.55, 0.65, EventStatus.UNCERTAIN),  # 0.6 between the two values
    (0.65, 0.7, EventStatus.REJECTED),  # 0.6 just below t_reject
    (0.5, 0.55, EventStatus.ACCEPTED),  # 0.6 just above t_accept
])  # fmt: skip
def test_height_thresholds_on_both_sides(
    t_reject: float, t_accept: float, expected: EventStatus
) -> None:
    doc = _run(_height_clip(), DetectorParams(t_reject=t_reject, t_accept=t_accept))
    middle = next(e for e in doc.events if e.time.ref_proxy_frame == 500)
    assert middle.status is expected


def test_too_few_peaks_leave_the_height_unjudged() -> None:
    doc = _run(_poses(right=_bell(60)), DetectorParams())  # 1 kept peak < 10
    (e,) = doc.events
    assert e.status is EventStatus.UNCERTAIN and e.reasons[0] == R_NO_CLIP_REF
    assert all(p.speed.rel_to_clip is None for p in doc.wrist_peaks)


def test_reject_must_not_exceed_accept() -> None:
    with pytest.raises(ValueError):
        DetectorParams(t_reject=0.8, t_accept=0.7)


# ------------------------------------------------------------------ regions, coverage


def test_unseen_regions_are_not_observable_with_their_reason() -> None:
    doc = _run(_poses(right=_bell(60), legs=False, no_left=range(120)))
    (e,) = doc.events
    assert e.regions.legs.state is RegionState.NOT_OBSERVABLE
    assert e.regions.legs.reasons == [RegionReason.NO_POSE]
    assert e.regions.left_wrist.state is RegionState.NOT_OBSERVABLE
    assert e.regions.legs.samples.good == 0
    assert e.regions.legs.samples.missing == e.regions.legs.samples.unreliable
    # regions not used by the event do not change its reliability
    assert e.reliability.state is RegionState.GOOD and e.status is EventStatus.ACCEPTED


def test_coverage_lists_long_holes_only() -> None:
    doc = _run(_poses(right=_bell(80), missing=set(range(0, 30)) | set(range(50, 54))))
    assert doc.coverage is not None
    stretches = doc.coverage.not_assessable
    assert len(stretches) >= 1
    first = stretches[0]
    assert first.start_proxy_frame == 0 and first.end_proxy_frame >= 29
    assert RegionReason.NO_POSE in first.reasons
    assert RegionName.TORSO in first.regions and RegionName.RIGHT_WRIST in first.regions
    # the 4-frame hole (0.13 s, plus the fit edges) is shorter than 0.3 s: not listed
    assert all(not (s.start_proxy_frame >= 40 and s.end_proxy_frame <= 60) for s in stretches)


def test_no_pose_gives_no_event_and_one_stretch() -> None:
    doc = _run(_poses(missing=range(120)))
    assert doc.events == [] and doc.wrist_peaks == []
    assert doc.coverage is not None
    (s,) = doc.coverage.not_assessable
    assert (s.start_proxy_frame, s.end_proxy_frame) == (0, 119)


# ------------------------------------------------------------------ V1 review 1 (ADR-029)


def _edit(
    poses: PoseSequence, frames: Any, move: Callable[[str, float, float], Any]
) -> PoseSequence:
    """Copy with ``move(name, x, y) -> (x, y)`` (normalised) applied in ``frames``."""
    out = []
    for f in poses.frames:
        if f.frame_index in frames:
            kps = [
                k.model_copy(update=dict(zip(("x", "y"), move(k.name, k.x, k.y), strict=True)))
                for k in f.keypoints
            ]
            f = f.model_copy(update={"keypoints": kps})
        out.append(f)
    return poses.model_copy(update={"frames": out})


def _legs_out(name: str, x: float, y: float) -> tuple[float, float]:
    return (x, 1.2) if name.endswith(("knee", "ankle")) else (x, y)


def test_duplicate_within_min_gap_is_rejected_in_favour_of_the_stronger() -> None:
    bells = _sum(_bell(40, width=2.0), _bell(52, amp=240, width=2.0))  # 0.4 s apart
    doc = _run(_poses(right=bells))
    strong, weak = doc.events
    assert strong.status is EventStatus.ACCEPTED and R_DUPLICATE not in strong.reasons
    assert weak.status is EventStatus.REJECTED and R_DUPLICATE in weak.reasons
    step = next(s for s in weak.trace if s.rule == R_DUPLICATE)
    assert step.inputs["stronger_event"] == strong.id
    assert weak.trace[-1].outcome.value == "rejected"


def test_events_at_least_min_gap_apart_are_both_kept() -> None:
    doc = _run(_poses(right=_sum(_bell(30), _bell(66, amp=240))))  # 1.2 s apart
    assert [e.status for e in doc.events] == [EventStatus.ACCEPTED] * 2
    assert all(R_DUPLICATE not in e.reasons for e in doc.events)
    shorter = _run(_poses(right=_sum(_bell(30), _bell(66, amp=240))),
                   DetectorParams(min_kept_peaks=1, min_event_gap_s=1.5))  # fmt: skip
    assert [e.status for e in shorter.events] == [EventStatus.ACCEPTED, EventStatus.REJECTED]


def _drop_right_wrist(poses: PoseSequence, down: Pos) -> PoseSequence:
    """Copy where the right wrist also moves down the image by ``down(k)`` pixels."""
    frames = []
    for f in poses.frames:
        kps = [
            k.model_copy(update={"y": k.y + down(f.frame_index) / 1280})
            if k.name == "right_wrist"
            else k
            for k in f.keypoints
        ]
        frames.append(f.model_copy(update={"keypoints": kps}))
    return poses.model_copy(update={"frames": frames})


def test_descending_event_never_beats_a_close_one() -> None:
    # a horizontal swing at 40, a faster drop of the wrist at 52 (0.4 s later)
    poses = _drop_right_wrist(_poses(right=_bell(40, amp=240, width=2.0)), _bell(52, width=2.0))
    doc = _run(poses)
    swing, drop = doc.events
    assert (swing.time.ref_proxy_frame, drop.time.ref_proxy_frame) == (40, 52)
    assert swing.status is EventStatus.ACCEPTED and R_DUPLICATE not in swing.reasons
    assert drop.status is EventStatus.REJECTED and R_DUPLICATE in drop.reasons
    # the same two movements, both horizontal: the faster one (52) is kept
    flat = _run(_poses(right=_sum(_bell(40, amp=240, width=2.0), _bell(52, width=2.0))))
    assert [R_DUPLICATE in e.reasons for e in flat.events] == [True, False]


def test_out_of_frame_peak_is_discarded_and_the_stretch_not_assessable() -> None:
    poses = _edit(_poses(right=_bell(60)), range(45, 76), _legs_out)  # 4 keypoints outside
    doc = _run(poses, DetectorParams(min_kept_peaks=1, out_of_frame_min_keypoints=4))
    assert doc.events == []
    assert any(p.reasons == [R_OUT_OF_FRAME] for p in doc.wrist_peaks)
    assert doc.coverage is not None
    (s,) = [
        s for s in doc.coverage.not_assessable if s.start_proxy_frame <= 60 <= s.end_proxy_frame
    ]
    assert RegionReason.OUT_OF_FRAME in s.reasons
    assert s.regions == [RegionName.TORSO, RegionName.LEFT_WRIST, RegionName.RIGHT_WRIST]
    # default (9 keypoints): 4 legs outside is not "out of frame"
    assert len(_run(poses).events) == 1


def test_player_at_the_side_edge_is_out_of_frame() -> None:
    def to_edge(name: str, x: float, y: float) -> tuple[float, float]:
        return (x + 0.45, y) if name.endswith("shoulder") else (x, y)  # mid-shoulder x 0.95

    poses = _edit(_poses(right=_bell(60)), range(45, 76), to_edge)
    doc = _run(poses)
    assert doc.events == []
    assert any(p.reasons == [R_OUT_OF_FRAME] for p in doc.wrist_peaks)
    assert doc.coverage is not None
    assert any(
        RegionReason.OUT_OF_FRAME in s.reasons and s.start_proxy_frame <= 60 <= s.end_proxy_frame
        for s in doc.coverage.not_assessable
    )
    assert len(_run(poses, DetectorParams(min_kept_peaks=1, edge_margin=0.0)).events) == 1


def test_short_out_of_frame_blip_still_shows_a_stretch_of_min_duration() -> None:
    poses = _edit(_poses(right=_bell(60)), range(58, 63), _legs_out)  # 5 frames only
    doc = _run(poses, DetectorParams(min_kept_peaks=1, out_of_frame_min_keypoints=4))
    assert doc.events == []
    assert doc.coverage is not None
    (s,) = [
        s for s in doc.coverage.not_assessable if s.start_proxy_frame <= 60 <= s.end_proxy_frame
    ]
    assert s.end_t_s - s.start_t_s + 1 / FPS >= 0.3 - 1e-3  # duration as in coverage


def test_tiny_torso_is_a_tracking_jump() -> None:
    def shrink(name: str, x: float, y: float) -> tuple[float, float]:
        if name.endswith(("shoulder", "hip")):  # torso 200 px -> 20 px (centre y 600)
            return x, (600 + (y * 1280 - 600) / 10) / 1280
        return x, y

    poses = _edit(_poses(right=_bell(60)), range(45, 76), shrink)
    doc = _run(poses)
    assert doc.events == []
    assert any(p.reasons == [R_TRACKING_JUMP] for p in doc.wrist_peaks)
    assert doc.coverage is not None
    assert any(
        RegionReason.OUT_OF_FRAME in s.reasons and s.start_proxy_frame <= 60 <= s.end_proxy_frame
        for s in doc.coverage.not_assessable
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_event_gap_s", 0.0), ("min_torso_ratio", 1.0), ("min_torso_ratio", 0.0),
     ("out_of_frame_min_keypoints", 0), ("out_of_frame_margin", 0.5), ("edge_margin", 0.5)],
)  # fmt: skip
def test_review_params_are_checked(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(DetectorParams(), **{field: value})


# ------------------------------------------------------------------ file


def test_written_file_is_byte_repeatable_and_round_trips(tmp_path: Path) -> None:
    poses = _poses(right=_sum(_bell(30), _bell(90)), left=_bell(34, amp=150))
    a = dump_strokes(_run(poses))
    b = dump_strokes(_run(poses))
    assert a == b
    f = tmp_path / "strokes.json"
    f.write_text(a, encoding="utf-8")
    assert dump_strokes(load_strokes(f)) == a


def test_detect_dir_records_input_hashes(tmp_path: Path) -> None:
    (tmp_path / "pose.json").write_text(_poses(right=_bell(60)).model_dump_json(), "utf-8")
    doc = detect_dir(tmp_path, now=NOW)
    assert check_invariants(doc) == []
    assert doc.inputs.pose.file == "pose.json" and len(doc.inputs.pose.sha256) == 64
    assert doc.inputs.timemap.status.value == "missing"
    assert doc.params["grouping"] == DetectorParams().as_dict()["grouping"]
    assert DetectorParams().g_s == 0.3 and DetectorParams().min_prominence_torso_per_s == 2.0


@pytest.mark.parametrize("field", ["g_s", "min_prominence_torso_per_s", "max_single_span_s"])
def test_params_must_be_positive(field: str) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(DetectorParams(), **{field: 0.0})


def test_last_proxy_frame_writes_nothing_after_it() -> None:
    poses = _poses(right=_sum(_bell(30), _bell(90)), missing=range(100, 120))
    full = _run(poses)
    assert full.coverage is not None and full.coverage.not_assessable
    cut = detect(poses, pose_file="pose.json", pose_sha256="0" * 64, now=NOW, last_proxy_frame=60)
    assert check_invariants(cut) == []
    assert [e.time.ref_proxy_frame for e in cut.events] == [30]
    assert all(p.proxy_frame <= 60 for p in cut.wrist_peaks)
    assert cut.coverage is not None and cut.coverage.not_assessable == []
    assert cut.params["input"] == {"last_proxy_frame": 60}
    assert cut.inputs.pose.frames == 120  # hashes and counts are those of the whole file


# ------------------------------------------------------------------ split boundary

LIMIT = 149  # stands for the Development B / Control boundary (4499 on img5098_full)


def _iphone_timemap(n_source: int) -> TimeMap:
    """Source intervals alternating 31.67 / 35.00 ms (iPhone, M2.2), mapped to 30 fps."""
    t, src = 0, [Fraction(0)]
    for d in [114, 126] * (n_source // 2):  # 1/3600 s units
        t += d
        src.append(Fraction(t, 3600))
    return build_timemap(src, FPS)


def _boundary_case() -> tuple[PoseSequence, TimeMap]:
    """A stroke whose speed bump straddles the limit (peak 8 frames before it, the fall and
    the fit / scale / prominence windows after it), plus one earlier stroke."""
    tm = _iphone_timemap(330)
    n = tm.summary.proxy_frame_count
    poses = _poses(right=_sum(_bell(100), _bell(LIMIT - 8, width=4.0)), n=n)
    return poses, tm


def _tamper_after(poses: PoseSequence, limit: int) -> PoseSequence:
    """Frames after ``limit`` changed in every way the detector reads: wrist motion, torso
    size (scale), visibility, missing keypoints and missing poses. Earlier frames untouched."""
    frames = []
    for f in poses.frames:
        k = f.frame_index
        if k <= limit:
            frames.append(f)
            continue
        if k % 17 == 0:
            frames.append(f.model_copy(update={"detected": False, "keypoints": []}))
            continue
        kps = []
        for kp in f.keypoints:
            if kp.name == "left_wrist" and k % 5 == 0:
                continue  # wrist absent
            x, y, vis = kp.x, kp.y, kp.visibility
            if kp.name == "right_wrist":
                x += 0.4 * ((k - limit) % 4) / 3  # fast zig-zag right after the limit
                vis = 0.45 if k % 3 == 0 else vis  # LOW samples
            if "shoulder" in kp.name:
                y -= 0.05  # longer torso: different scale
            kps.append(kp.model_copy(update={"x": x, "y": y, "visibility": vis}))
        frames.append(f.model_copy(update={"keypoints": kps}))
    return poses.model_copy(update={"frames": frames})


def _detect(poses: PoseSequence, tm: TimeMap, limit: int | None) -> StrokesFile:
    return detect(
        poses, pose_file="pose.json", pose_sha256="0" * 64, timemap=tm,
        timemap_file="timemap.json", timemap_sha256="1" * 64, now=NOW, last_proxy_frame=limit,
    )  # fmt: skip


def test_last_proxy_frame_result_ignores_later_frames() -> None:
    poses, tm = _boundary_case()
    tampered = _tamper_after(poses, LIMIT)
    assert [f for f in tampered.frames if f.frame_index <= LIMIT] == [
        f for f in poses.frames if f.frame_index <= LIMIT
    ]
    a, b = _detect(poses, tm, LIMIT), _detect(tampered, tm, LIMIT)
    assert check_invariants(a) == [] and a.time_basis.value == "source"
    # the whole detector result, byte for byte: peaks, prominences, quality windows,
    # events, regions, reliability, trace, coverage
    assert dump_strokes(a) == dump_strokes(b)
    assert [e.time.ref_proxy_frame for e in a.events] == [100, LIMIT - 8]
    assert a.inputs.pose.frames == len(poses.frames)  # metadata of the whole file

    # the test is sensitive: without the cut, the same frames give a different result
    fa, fb = _detect(poses, tm, None), _detect(tampered, tm, None)
    near = [p for p in fa.wrist_peaks if p.proxy_frame <= LIMIT]
    near_b = [p for p in fb.wrist_peaks if p.proxy_frame <= LIMIT]
    assert [p.model_dump() for p in near] != [p.model_dump() for p in near_b]


def test_last_proxy_frame_intermediate_signals_ignore_later_frames() -> None:
    poses, tm = _boundary_case()
    tampered = _tamper_after(poses, LIMIT)
    signals = []
    for ps in (poses, tampered):
        cp, cm = truncate_inputs(ps, tm, LIMIT)
        tb = build_timebase(cp, cm)
        sc = torso_scale(cp, tb)
        vel = keypoint_velocity(cp, tb, "right_wrist", sc)
        signals.append((tb, sc, vel))
    (tb_a, sc_a, v_a), (tb_b, sc_b, v_b) = signals
    assert tb_a.basis.value == "source" and tb_a.map_status.value == "ok"
    assert int(tb_a.proxy_frames[-1]) == LIMIT
    np.testing.assert_array_equal(tb_a.times_s, tb_b.times_s)
    np.testing.assert_array_equal(sc_a.length_px, sc_b.length_px)
    np.testing.assert_array_equal(v_a.speed_2d_px_per_s, v_b.speed_2d_px_per_s)
    assert v_a.state == v_b.state
    # times are those of the whole file: same as the time base of the uncut inputs
    whole = build_timebase(poses, tm)
    np.testing.assert_array_equal(tb_a.times_s, whole.times_s[: tb_a.times_s.size])
    np.testing.assert_array_equal(tb_a.proxy_frames, whole.proxy_frames[: tb_a.times_s.size])
    # the samples next to the limit are run edges of the cut input (no later sample used)
    assert v_a.state[-1] is not VelocityState.VALID


REAL_DEV_B = os.environ.get("TPA_DEV_B_POSE_DIR")


@pytest.mark.skipif(not REAL_DEV_B, reason="set TPA_DEV_B_POSE_DIR to img5098_full's pose dir")
def test_real_development_b_ignores_control_frames(tmp_path: Path) -> None:
    """Real img5098_full (not committed): Development B with limit 4499, then the frames
    after 4499 tampered; the detector result must be identical (hashes are given, not computed)."""
    src = Path(str(REAL_DEV_B))
    poses = PoseSequence.model_validate_json((src / "pose.json").read_bytes())
    tm = TimeMap.model_validate_json((src / "timemap.json").read_bytes())
    tampered = _tamper_after(poses, 4499)

    def run(ps: PoseSequence) -> str:
        doc = detect(
            ps, pose_file="pose.json", pose_sha256="0" * 64, timemap=tm,
            timemap_file="timemap.json", timemap_sha256="1" * 64, now=NOW, last_proxy_frame=4499,
        )  # fmt: skip
        assert check_invariants(doc) == []
        return dump_strokes(doc)

    assert run(poses) == run(tampered)


def _tamper_before(poses: PoseSequence, first: int) -> PoseSequence:
    """Frames before ``first`` changed (motion, torso size, missing poses); later untouched."""
    frames = []
    for f in poses.frames:
        if f.frame_index >= first:
            frames.append(f)
        elif f.frame_index % 7 == 0:
            frames.append(f.model_copy(update={"detected": False, "keypoints": []}))
        else:
            kps = [
                kp.model_copy(
                    update={
                        "x": kp.x + (0.3 * (f.frame_index % 3) if "wrist" in kp.name else 0.0),
                        "y": kp.y - (0.05 if "shoulder" in kp.name else 0.0),
                    }
                )
                for kp in f.keypoints
            ]
            frames.append(f.model_copy(update={"keypoints": kps}))
    return poses.model_copy(update={"frames": frames})


def test_first_proxy_frame_result_ignores_earlier_frames() -> None:
    first = 60
    tm = _iphone_timemap(330)
    n = tm.summary.proxy_frame_count
    poses = _poses(right=_sum(_bell(first + 8, width=4.0), _bell(200)), n=n)
    tampered = _tamper_before(poses, first)

    def run(ps: PoseSequence) -> StrokesFile:
        return detect(
            ps, pose_file="pose.json", pose_sha256="0" * 64, timemap=tm,
            timemap_file="timemap.json", timemap_sha256="1" * 64, now=NOW,
            first_proxy_frame=first, params=ONE_PEAK_REF,
        )  # fmt: skip

    a, b = run(poses), run(tampered)
    assert check_invariants(a) == [] and a.time_basis.value == "source"
    assert dump_strokes(a) == dump_strokes(b)
    assert all(p.proxy_frame >= first for p in a.wrist_peaks)
    assert [e.time.ref_proxy_frame for e in a.events] == [first + 8, 200]
    assert a.params["input"] == {"last_proxy_frame": None, "first_proxy_frame": first}
    # times are those of the whole file
    whole = build_timebase(poses, tm)
    ref = next(e for e in a.events if e.time.ref_proxy_frame == 200).time.ref_t_s
    assert ref == pytest.approx(float(whole.times_s[whole.proxy_frames == 200][0]), abs=1e-4)
    # sensitive: without the cut, the tampering changes peaks after the first frame
    fa = _detect(poses, tm, None)
    fb = _detect(tampered, tm, None)
    near = [p.model_dump() for p in fa.wrist_peaks if first <= p.proxy_frame < first + 30]
    near_b = [p.model_dump() for p in fb.wrist_peaks if first <= p.proxy_frame < first + 30]
    assert near != near_b


def test_params_input_has_no_first_key_when_unused() -> None:
    doc = _run(_poses(right=_bell(60)))
    assert doc.params["input"] == {"last_proxy_frame": None}
