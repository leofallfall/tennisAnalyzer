"""M2.3 I2: detector chain (peaks -> events -> strokes.json 1.1) on synthetic poses.

Built cases with known speed bumps; nothing here is a biomechanical claim and nothing
validates the detector on real data."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from math import erf, sqrt
from pathlib import Path
from typing import Any

import pytest

from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
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
    R_MERGE,
    R_PROXY,
    R_REP_ONLY_EDGE,
    R_UNDETERMINED,
    DetectorParams,
    detect,
    detect_dir,
)

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


def _run(poses: PoseSequence, params: DetectorParams | None = None) -> StrokesFile:
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
    assert len(_run(_poses(right=bells), DetectorParams(g_s=0.5)).events) == 1


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
