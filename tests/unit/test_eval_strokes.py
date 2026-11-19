"""M2.1 evaluation tooling on synthetic data: dataset parsing, matching, visibility, reports.

No real footage and no stroke detector are involved: the poses and the "detected" events
are written by hand so that every expected number can be checked exactly.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tennis_cv.comparison.base import ViewAngle
from tennis_cv.evaluation.dataset import (
    LABEL_COLUMNS,
    MANIFEST_COLUMNS,
    ClipData,
    DatasetFormatError,
    Hand,
    Label,
    LabelStrokeType,
    ManifestClip,
    OverlayWrist,
    Split,
    load_clip_data,
    load_labels,
    load_manifest,
)
from tennis_cv.evaluation.matching import (
    DetectedEvent,
    EventStatus,
    EventType,
    FalsePositiveCause,
    match_clip,
)
from tennis_cv.evaluation.report import (
    MIN_CLIPS_PER_SPLIT,
    assess_split,
    clip_groups,
    match_groups,
    render_matches,
    render_visibility,
    visibility_groups,
)
from tennis_cv.evaluation.visibility import analyse_impacts
from tennis_cv.pose.landmarks import LANDMARK_NAMES
from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.video.timemap import build_timemap

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- helpers


def _frame(i: int, fps: float, vis: dict[str, float] | None, default: float = 0.9) -> PoseFrame:
    """A pose frame; ``vis=None`` means no pose detected."""
    if vis is None:
        return PoseFrame(frame_index=i, timestamp_s=i / fps, detected=False)
    kps = [
        Keypoint(name=n, x=0.5, y=0.5, z=0.0, visibility=vis.get(n, default), presence=0.99)
        for n in LANDMARK_NAMES
    ]
    return PoseFrame(frame_index=i, timestamp_s=i / fps, detected=True, keypoints=kps)


def _poses(
    n: int,
    fps: float = 30.0,
    stride: int = 1,
    overrides: dict[int, dict[str, float] | None] | None = None,
) -> PoseSequence:
    ov = overrides or {}
    frames = [_frame(i, fps, ov.get(i, {})) for i in range(0, n, stride)]
    return PoseSequence(
        estimator="test",
        model="test",
        image_width=1920,
        image_height=1080,
        fps=fps,
        frame_stride=stride,
        frames=frames,
    )


def _clip(
    clip_id: str = "c1",
    hand: Hand = Hand.RIGHT,
    view: ViewAngle = ViewAngle.BEHIND,
    split: Split = Split.DEV,
    other_people: bool = False,
) -> ManifestClip:
    return ManifestClip(
        clip_id=clip_id,
        file="x.mov",
        pose_dir=Path("."),
        view=view,
        dominant_hand=hand,
        backhand="two_handed",
        other_people=other_people,
        occlusion="none",
        split=split,
    )


def _label(
    frame: int,
    stroke: str = "forehand",
    player: str = "A",
    clip_id: str = "c1",
    line: int = 2,
    visibility: str = "full",
    overlay_wrist: str | None = None,
) -> Label:
    return Label(
        clip_id=clip_id,
        proxy_frame=frame,
        stroke_type=LabelStrokeType(stroke),
        player=player,
        visibility=visibility,
        overlay_wrist=OverlayWrist(overlay_wrist) if overlay_wrist else None,
        line=line,
    )


def _event(frame: int, stroke: str = "forehand", status: str = "classified") -> DetectedEvent:
    return DetectedEvent(
        peak_frame=frame, stroke_type=EventType(stroke), status=EventStatus(status)
    )


def _manifest_text(rows: list[str]) -> str:
    header = "| " + " | ".join(MANIFEST_COLUMNS) + " |"
    sep = "| " + " | ".join("---" for _ in MANIFEST_COLUMNS) + " |"
    return "# Clips\n\nFree text.\n\n" + "\n".join([header, sep, *rows]) + "\n\nMore text.\n"


GOOD_ROW = "| c1 | a.mov | poses/c1 | behind | right | two_handed | yes | none | dev | |"


# ---------------------------------------------------------------- manifest


def test_manifest_parses_and_resolves_relative_pose_dir(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.md"
    m.write_text(_manifest_text([GOOD_ROW]))
    (clip,) = load_manifest(m)
    assert clip.pose_dir == tmp_path / "poses" / "c1"
    assert clip.view is ViewAngle.BEHIND
    assert clip.other_people is True
    assert clip.split is Split.DEV


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ("| c1 | a.mov | p | sideways | right | two_handed | no | none | dev | |", "sideways"),
        ("| c1 | a.mov | p | behind | right | two_handed | maybe | none | dev | |", "yes/no"),
        ("| c 1 | a.mov | p | behind | right | two_handed | no | none | dev | |", "clip_id"),
        ("| c1 | a.mov | p | behind | right | two_handed | no | none |", "cells"),
    ],
)
def test_manifest_rejects_bad_rows_with_line(tmp_path: Path, row: str, fragment: str) -> None:
    m = tmp_path / "MANIFEST.md"
    m.write_text(_manifest_text([row]))
    with pytest.raises(DatasetFormatError) as exc:
        load_manifest(m)
    problems = exc.value.details["problems"]
    assert any("MANIFEST.md:7" in p and fragment in p for p in problems), problems


def test_manifest_rejects_duplicates_and_bad_header(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.md"
    m.write_text(_manifest_text([GOOD_ROW, GOOD_ROW]))
    with pytest.raises(DatasetFormatError, match="invalid rows"):
        load_manifest(m)
    m.write_text("| clip_id | file |\n| --- | --- |\n| c1 | a |\n")
    with pytest.raises(DatasetFormatError, match="header"):
        load_manifest(m)


# ---------------------------------------------------------------- labels


def test_labels_parse_with_comments_and_optional_overlay_check(tmp_path: Path) -> None:
    f = tmp_path / "labels.csv"
    f.write_text(
        "# comment\n"
        + ",".join(LABEL_COLUMNS)
        + '\n\nc1,812,forehand,A,full,,\nc1,905,backhand,A,partial,off,"rete, racchetta"\n'
    )
    a, b = load_labels(f)
    assert (a.proxy_frame, a.overlay_wrist, a.line) == (812, None, 4)
    assert b.overlay_wrist is OverlayWrist.OFF
    assert b.notes == "rete, racchetta"


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ("c1,10,smash,A,full,,", "stroke_type"),
        ("c1,-1,forehand,A,full,,", "proxy_frame"),
        ("c1,ten,forehand,A,full,,", "invalid literal"),
        ("c1,10,forehand,a,full,,", "player"),
        ("c1,10,forehand,A,clear,,", "visibility"),
        ("c1,10,forehand,A,full,,,extra", "fields"),
    ],
)
def test_labels_reject_bad_rows_with_line(tmp_path: Path, row: str, fragment: str) -> None:
    f = tmp_path / "labels.csv"
    f.write_text(",".join(LABEL_COLUMNS) + "\n" + row + "\n")
    with pytest.raises(DatasetFormatError) as exc:
        load_labels(f)
    problems = exc.value.details["problems"]
    assert any("labels.csv:2" in p and fragment in p for p in problems), problems


def test_labels_reject_duplicates_and_bad_header(tmp_path: Path) -> None:
    f = tmp_path / "labels.csv"
    f.write_text(",".join(LABEL_COLUMNS) + "\nc1,10,forehand,A,full,,\nc1,10,backhand,A,full,,\n")
    with pytest.raises(DatasetFormatError) as exc:
        load_labels(f)
    assert "line 2" in exc.value.details["problems"][0]
    f.write_text("clip,frame\nc1,10\n")
    with pytest.raises(DatasetFormatError, match="header"):
        load_labels(f)


# ---------------------------------------------------------------- matching


def test_match_one_to_one_with_signed_error() -> None:
    labels = [_label(100), _label(200, "backhand", line=3)]
    events = [_event(103), _event(97), _event(198, "backhand")]
    m = match_clip("c1", labels, events, fps=30.0)
    assert [(p.label_frame, p.event_frame) for p in m.matched] == [(100, 97), (200, 198)]
    # 97 and 103 are equally close: the tie goes to the earlier event, and the leftover
    # event is a false positive, not a second match.
    assert m.matched[0].error_ms == pytest.approx(-100.0)
    assert m.matched[1].error_ms == pytest.approx(-66.667, abs=1e-3)
    assert [f.event_frame for f in m.false_positives] == [103]
    assert m.misses == []


def test_tolerance_is_a_parameter() -> None:
    labels = [_label(100)]
    events = [_event(108)]  # 8 frames = 0.267 s at 30 fps
    assert len(match_clip("c1", labels, events, 30.0, tolerance_s=0.25).matched) == 0
    assert len(match_clip("c1", labels, events, 30.0, tolerance_s=0.30).matched) == 1
    # Same frame distance, double frame rate: 0.133 s.
    assert len(match_clip("c1", labels, events, 60.0, tolerance_s=0.25).matched) == 1


def test_false_positive_causes_and_rejected_events() -> None:
    labels = [
        _label(100),
        _label(300, "none", line=3),
        _label(500, "forehand", player="B", line=4),
        _label(700, "serve", line=5),
        _label(900, "backhand", line=6, visibility="partial"),
    ]
    events = [
        _event(100),
        _event(301),
        _event(499),
        _event(702),
        _event(1100),
        _event(900, status="rejected"),
    ]
    m = match_clip("c1", labels, events, 30.0)
    causes = {f.event_frame: f.cause for f in m.false_positives}
    assert causes == {
        301: FalsePositiveCause.NOT_A_STROKE,
        499: FalsePositiveCause.OTHER_PLAYER,
        702: FalsePositiveCause.NON_TARGET_STROKE,
        1100: FalsePositiveCause.UNLABELLED,
    }
    assert m.events_rejected == 1 and m.events_considered == 5
    assert [(x.label_frame, x.visibility) for x in m.misses] == [(900, "partial")]


def test_targets_and_validation() -> None:
    labels = [_label(700, "serve")]
    m = match_clip("c1", labels, [_event(700)], 30.0, targets=frozenset({LabelStrokeType.SERVE}))
    assert len(m.matched) == 1
    with pytest.raises(ValueError):
        match_clip("c1", labels, [], 30.0, targets=frozenset({LabelStrokeType.NONE}))
    with pytest.raises(ValueError):
        match_clip("c1", labels, [], 0.0)


# ---------------------------------------------------------------- visibility


def _data(poses: PoseSequence, clip: ManifestClip | None = None, timemap: bool = False) -> ClipData:
    tm = None
    if timemap:
        # Source at 10 fps for 1 s then 30 fps, resampled to 30 fps: repeated frames early.
        times = [i / 10 for i in range(10)] + [1 + i / 30 for i in range(60)]
        tm = build_timemap(times, 30, len(poses.frames))
    return ClipData(clip=clip or _clip(), poses=poses, timemap=tm, source_fps=None, source_vfr=None)


def test_visibility_window_runs_and_human_check() -> None:
    # Hitting (right) wrist drops below threshold 3 frames after the impact at 50.
    poses = _poses(100, overrides={53: {"right_wrist": 0.2}})
    labels = [_label(50, overlay_wrist="ok"), _label(60, "none"), _label(70, player="B")]
    (it,) = analyse_impacts(_data(poses), labels, window_s=0.3, threshold=0.5)
    assert (it.window_first_frame, it.window_last_frame, it.frames_in_window) == (41, 59, 19)
    assert it.frames_analysed == 19 and it.pose_at_impact and it.body_reference_at_impact
    wrist = next(j for j in it.joints if j.joint == "hitting_wrist")
    assert wrist.landmark == "right_wrist"
    assert wrist.above_threshold_at_impact
    assert wrist.run_through_impact == 12  # frames 41..52
    assert not wrist.run_covers_window
    elbow = next(j for j in it.joints if j.joint == "hitting_elbow")
    assert elbow.run_covers_window
    assert it.overlay_wrist == "ok"


def test_visibility_handles_missing_pose_stride_edges_and_unknown_hand() -> None:
    poses = _poses(100, stride=2, overrides={4: None})
    clip = _clip(hand=Hand.UNKNOWN)
    items = analyse_impacts(_data(poses, clip), [_label(4), _label(5, line=3)], window_s=0.3)
    first, second = items
    assert (first.window_first_frame, first.window_last_frame) == (0, 13)
    assert first.frames_analysed == 7  # even frames only
    assert not first.pose_at_impact and not first.body_reference_at_impact
    names = {j.joint for j in first.joints}
    assert names == {f"{s}_{j}" for s in ("left", "right") for j in ("wrist", "elbow", "shoulder")}
    assert all(j.visibility_at_impact is None and j.run_through_impact == 0 for j in first.joints)
    # Frame 5 was not analysed (stride 2): no score at impact, not a "visible" impact.
    assert not second.pose_at_impact
    assert all(not j.above_threshold_at_impact for j in second.joints)


def test_visibility_counts_repeated_proxy_frames_from_timemap() -> None:
    poses = _poses(90)
    items = analyse_impacts(_data(poses, timemap=True), [_label(10), _label(70, line=3)])
    early, late = items
    assert early.repeated_proxy_frames is not None and early.repeated_proxy_frames > 0
    assert late.repeated_proxy_frames == 0
    no_tm = analyse_impacts(_data(poses), [_label(10)])
    assert no_tm[0].repeated_proxy_frames is None


def test_visibility_summary_keeps_denominators_and_flags_small_samples() -> None:
    poses = _poses(200, overrides={100: {"right_wrist": 0.3}})
    labels = [
        _label(50, overlay_wrist="ok"),
        _label(100, "backhand", line=3, overlay_wrist="off"),
        _label(150, line=4),
    ]
    data = _data(poses)
    items = analyse_impacts(data, labels)
    groups = visibility_groups(items, {"c1": clip_groups(data)})
    overall = groups[0]
    assert (overall.dimension, overall.impacts, overall.clips) == ("all", 3, 1)
    wrist = next(j for j in overall.joints if j.joint == "hitting_wrist")
    assert (wrist.above_threshold_at_impact.n, wrist.above_threshold_at_impact.of) == (2, 3)
    assert overall.overlay_wrist == {"not_checked": 1, "off": 1, "ok": 1}
    assert overall.hitting_wrist_cross == {"mp_above/ok": 1, "mp_below/off": 1}
    assert overall.small_sample
    dims = {(g.dimension, g.value) for g in groups}
    assert {("view", "behind"), ("proxy_fps", "30"), ("other_people", "no")} <= dims
    assert {("stroke_type", "forehand"), ("stroke_type", "backhand")} <= dims
    text = render_visibility(groups, assess_split([data]), window_s=0.3, threshold=0.5)
    assert "2/3" in text and "(small sample)" in text
    assert "Not an accuracy" in text and "No meaningful dev/test split" in text


# ---------------------------------------------------------------- split and comparison report


def test_split_assessment_requires_enough_clips_per_side() -> None:
    poses = _poses(10)
    few = [_data(poses, _clip(f"d{i}", split=Split.DEV)) for i in range(MIN_CLIPS_PER_SPLIT)]
    assert not assess_split(few).meaningful
    many = few + [
        _data(poses, _clip(f"t{i}", split=Split.TEST)) for i in range(MIN_CLIPS_PER_SPLIT)
    ]
    assessment = assess_split(many)
    assert assessment.meaningful and assessment.dev_clips == MIN_CLIPS_PER_SPLIT


def test_match_summary_counts_abstentions_separately() -> None:
    labels = [_label(100), _label(200, "backhand", line=3), _label(300, line=4)]
    events = [_event(100), _event(200, "unknown", "uncertain"), _event(300, "backhand")]
    data = _data(_poses(400))
    m = match_clip("c1", labels, events, 30.0)
    (overall, *_rest) = match_groups([m], {"c1": clip_groups(data)})
    assert (overall.matched, overall.misses, overall.false_positives) == (3, 0, 0)
    assert (overall.classified_correct.n, overall.classified_correct.of) == (1, 2)
    assert (overall.abstentions.n, overall.abstentions.of) == (1, 3)
    assert overall.uncertain_matched == 1
    assert overall.confusion == {
        "backhand->unknown": 1,
        "forehand->backhand": 1,
        "forehand->forehand": 1,
    }
    text = render_matches([overall], assess_split([data]), tolerance_s=0.25, source="test")
    assert "not a validated value" in text and "3/3" in text


# ---------------------------------------------------------------- CLI end to end


def _cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "eval_strokes", ROOT / "scripts" / "eval_strokes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_init_check_visibility_compare(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _cli()
    d = tmp_path / "real"
    assert cli.main(["init", str(d)]) == 0
    assert (d / "MANIFEST.md").is_file() and (d / "labels.csv").is_file()
    # The template's example row is valid syntax but points at a missing pose folder.
    assert cli.main(["check", str(d / "MANIFEST.md"), str(d / "labels.csv")]) == 1

    pose_dir = d / "poses" / "c1"
    pose_dir.mkdir(parents=True)
    (pose_dir / "pose.json").write_text(_poses(120).model_dump_json())
    (d / "MANIFEST.md").write_text(
        _manifest_text(
            ["| c1 | a.mov | poses/c1 | behind | right | two_handed | no | none | dev | |"]
        )
    )
    (d / "labels.csv").write_text(
        ",".join(LABEL_COLUMNS) + "\nc1,30,forehand,A,full,ok,\nc1,90,backhand,A,full,,\n"
    )
    (pose_dir / "strokes.json").write_text(
        json.dumps({"events": [{"peak_frame": 31, "stroke_type": "forehand", "extra": 1}]})
    )
    args = [str(d / "MANIFEST.md"), str(d / "labels.csv")]
    capsys.readouterr()
    assert cli.main(["check", *args]) == 0
    out = capsys.readouterr().out
    assert "c1: 120 proxy frames at 30 fps" in out and "No meaningful dev/test split" in out

    report, data = tmp_path / "vis.md", tmp_path / "vis.json"
    assert cli.main(["visibility", *args, "--out", str(report), "--json", str(data)]) == 0
    assert "M2.1 input visibility report" in report.read_text()
    assert len(json.loads(data.read_text())["impacts"]) == 2

    cmp_report = tmp_path / "cmp.md"
    assert cli.main(["compare", *args, "--tolerance-s", "0.1", "--out", str(cmp_report)]) == 0
    text = cmp_report.read_text()
    assert "±0.1 s" in text and "1/2" in text

    # A label past the end of the clip is an error, not a silent skip.
    (d / "labels.csv").write_text(",".join(LABEL_COLUMNS) + "\nc1,500,forehand,A,full,,\n")
    assert cli.main(["check", *args]) == 1
    assert "past the end" in capsys.readouterr().err


def test_load_clip_data_reads_optional_artifacts(tmp_path: Path) -> None:
    (tmp_path / "pose.json").write_text(_poses(30).model_dump_json())
    (tmp_path / "timemap.json").write_text(
        build_timemap([i / 30 for i in range(30)], 30).model_dump_json()
    )
    (tmp_path / "report.json").write_text(
        json.dumps({"source_metadata": {"average_fps": 29.97, "is_variable_frame_rate": True}})
    )
    clip = _clip().model_copy(update={"pose_dir": tmp_path})
    data = load_clip_data(clip)
    assert data.timemap is not None and data.source_fps == 29.97 and data.source_vfr is True
    assert clip_groups(data)["source_frame_rate"] == "vfr"
    (tmp_path / "pose.json").write_text("{}")
    with pytest.raises(DatasetFormatError, match=r"pose\.json is invalid"):
        load_clip_data(clip)
