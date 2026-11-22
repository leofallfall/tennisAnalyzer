"""M2.3 evaluation (PR 2): association by connected components, miss causes, annotated
intervals, time rules and strokes_eval.json. Synthetic data only: poses, maps, labels and
strokes files are written by hand so every expected outcome is known exactly."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from tennis_cv.errors import TennisCVError
from tennis_cv.evaluation.annotated import AnnotatedInterval
from tennis_cv.evaluation.association import (
    AmbiguityReason,
    Edge,
    EventNode,
    GroupShape,
    LabelNode,
    associate,
    groups_from_edges,
)
from tennis_cv.evaluation.clip_time import FrameState
from tennis_cv.evaluation.dataset import LABEL_COLUMNS, ClipData, DatasetFormatError, Label
from tennis_cv.evaluation.eval_artifact import (
    ClipEval,
    ImpactOutcome,
    MissCause,
    Position,
    RadiusResult,
    RadiusStatus,
    StrokesEvalFile,
    StrokesFormat,
    UnassociatedCause,
    Verdict,
    dump_eval,
    same_eval,
)
from tennis_cv.evaluation.evaluate import (
    LoadedStrokes,
    evaluate_clip,
    evaluate_dataset,
    load_events,
)
from tennis_cv.pose.models import PoseSequence
from tennis_cv.strokes.artifact import file_sha256, params_sha256
from tennis_cv.video.timemap import TimeMap, build_timemap
from tests.unit.test_annotated_intervals import _iv
from tests.unit.test_eval_strokes import _cli, _clip, _label, _manifest_text, _poses

FPS = 30.0
Spec = dict[str, Any]


# ---------------------------------------------------------------- strokes.json builder


def _rule(code: str, kind: str) -> Spec:
    return {"code": code, "kind": kind, "description": f"test rule {code}"}


RULES = [
    _rule("chain_grouping", "check"),
    _rule("decision", "decision"),
    _rule("too_weak", "reject"),
    _rule("low_quality", "uncertain"),
    _rule("no_good_wrist", "representative_wrist"),
    _rule("peak_low", "discard_peak"),
]


def _region() -> Spec:
    counts = {"good": 5, "low_but_usable": 0, "unreliable": 0, "missing": 0}
    return {"state": "good", "reasons": [], "samples": counts}


def _peak(pid: str, wrist: str, frame: int, t: float, event_id: str | None) -> Spec:
    return {
        "id": pid,
        "wrist": wrist,
        "proxy_frame": frame,
        "t_s": round(t, 4),
        "speed": {"value": 5.0, "unit": "torso_lengths_per_s", "rel_to_clip": 0.8},
        "provenance": {
            "signal": "wrist_speed_2d_apparent",
            "keypoint": f"{wrist}_wrist",
            "landmark_index": 15 if wrist == "left" else 16,
            "velocity_state": "valid",
            "smooth_state": "smoothed",
            "scale_state": "ok",
        },
        "quality_window": {
            "half_window_s": 0.2,
            "good": 5,
            "low_but_usable": 0,
            "unreliable": 0,
            "missing": 0,
            "irregular_intervals": 0,
        },
        "status": "grouped" if event_id else "discarded",
        "reasons": [] if event_id else ["peak_low"],
        "event_id": event_id,
    }


def _strokes(
    events: list[Spec],
    discarded: list[tuple[str, str, int]] = (),  # type: ignore[assignment]
    *,
    t: Callable[[int], float] = lambda f: f / FPS,
    basis: str = "proxy",
    map_status: str = "missing",
    g_s: float = 0.1,
    pose_sha: str = "0" * 64,
    timemap_sha: str | None = None,
    params: Spec | None = None,
) -> Spec:
    """A valid strokes.json 1.0 dict. Event spec: id, status, peaks [(id, wrist, frame)],
    rep (left / right / undetermined)."""
    peaks: list[Spec] = []
    out_events: list[Spec] = []
    for ev in events:
        eid, status, rep = ev["id"], ev.get("status", "accepted"), ev.get("rep", "right")
        pks = sorted(ev["peaks"], key=lambda p: (p[2], p[0]))
        for pid, wrist, frame in pks:
            peaks.append(_peak(pid, wrist, frame, t(frame), eid))
        frames = [p[2] for p in pks]
        times = [round(t(f), 4) for f in frames]
        ref = next((p for p in pks if p[1] == rep), None)
        reasons = {"accepted": [], "uncertain": ["low_quality"], "rejected": ["too_weak"]}[status]
        trace = [
            {
                "step": 1,
                "rule": "chain_grouping",
                "subject": eid,
                "inputs": {},
                "param": "grouping.g_s",
                "outcome": "pass",
            },
            {
                "step": 2,
                "rule": "decision",
                "subject": eid,
                "inputs": {},
                "param": None,
                "outcome": status,
            },
        ]
        out_events.append(
            {
                "id": eid,
                "status": status,
                "reasons": reasons,
                "grouping": {
                    "method": "chain",
                    "g_s": g_s,
                    "peak_ids": [p[0] for p in pks],
                    "links": [
                        {"from": a[0], "to": b[0], "dt_s": round(t(b[2]) - t(a[2]), 4)}
                        for a, b in pairwise(pks)
                    ],
                    "span_s": round(times[-1] - times[0], 4),
                    "possible_merge": False,
                },
                "evidence": {
                    w: (
                        {"state": "peaks", "peak_ids": [p[0] for p in pks if p[1] == w]}
                        if any(p[1] == w for p in pks)
                        else {"state": "no_peak", "peak_ids": []}
                    )
                    for w in ("left", "right")
                },
                "representative_wrist": {
                    "value": rep if ref else "undetermined",
                    "reasons": [] if ref else ["no_good_wrist"],
                },
                "time": {
                    "ref_proxy_frame": ref[2] if ref else None,
                    "ref_t_s": round(t(ref[2]), 4) if ref else None,
                    "window_proxy_frames": [frames[0], frames[-1]],
                    "window_t_s": [times[0], times[-1]],
                },
                "regions": {r: _region() for r in ("torso", "left_wrist", "right_wrist", "legs")},
                "reliability": {"state": "good", "regions_used": ["torso"], "limited_by": []},
                "complementary": [],
                "classification": {"stroke_type": "unknown", "status": "not_attempted"},
                "player": {"status": "not_checked"},
                "estimated_contact_s": None,
                "trace": trace,
            }
        )
    for pid, wrist, frame in discarded:
        peaks.append(_peak(pid, wrist, frame, t(frame), None))
    prm = params or {
        "grouping": {"g_s": g_s},
        "signals": {
            "timebase": {"max_interval_ratio": 1.5, "min_interval_ratio": 0.5},
            "smoothing": {"good_visibility": 0.5, "usable_visibility": 0.4},
        },
    }
    return {
        "schema": "tpa.strokes",
        "schema_version": "1.0",
        "created_at": "2026-11-22T12:00:00Z",
        "producer": {
            "algorithm": "test",
            "algorithm_version": "0.0.0",
            "status": "experimental",
            "engine_version": "0.1.0",
        },
        "inputs": {
            "pose": {
                "file": "pose.json",
                "sha256": pose_sha,
                "fps": FPS,
                "frames": 1,
                "frame_stride": 1,
            },
            "timemap": {
                "file": None if map_status == "missing" else "timemap.json",
                "sha256": None if map_status == "missing" else (timemap_sha or "1" * 64),
                "status": map_status,
            },
            "player_config": None,
        },
        "time_basis": basis,
        "params": prm,
        "params_sha256": params_sha256(prm),
        "rules": RULES,
        "wrist_peaks": peaks,
        "events": out_events,
    }


def _ev(eid: str, frame: int, status: str = "accepted", rep: str = "right") -> Spec:
    """One event with a single right-wrist peak (an instant unless rep = undetermined)."""
    return {"id": eid, "status": status, "rep": rep, "peaks": [(f"p{eid}", "right", frame)]}


def _load(tmp_path: Path, doc: Spec, name: str = "strokes.json") -> LoadedStrokes:
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return load_events(p)


def _clipdata(poses: PoseSequence | None = None, timemap: TimeMap | None = None) -> ClipData:
    return ClipData(
        clip=_clip(),
        poses=poses or _poses(300),
        timemap=timemap,
        source_fps=None,
        source_vfr=None,
    )


def _run(
    tmp_path: Path,
    events: list[Spec],
    labels: list[Label],
    *,
    radii: tuple[float, ...] = (0.2,),
    intervals: list[AnnotatedInterval] | None = None,
    discarded: list[tuple[str, str, int]] = (),  # type: ignore[assignment]
    poses: PoseSequence | None = None,
) -> ClipEval:
    loaded = _load(tmp_path, _strokes(events, discarded))
    return evaluate_clip(
        _clipdata(poses),
        labels,
        intervals if intervals is not None else [_iv(0, 299)],
        loaded,
        radii=radii,
    )


def _lbl(frame: int, line: int, stroke: str = "forehand", player: str = "A", **kw: Any) -> Label:
    return _label(frame, stroke=stroke, player=player, line=line, **kw)


def _impact(rr: RadiusResult, line: int) -> Any:
    return next(i for i in rr.impacts if i.label_line == line)


def _unassoc(rr: RadiusResult, eid: str) -> Any:
    return next(u for u in rr.unassociated_events if u.event_id == eid)


# ---------------------------------------------------------------- association (pure)


def _shapes(events: list[EventNode], labels: list[LabelNode], r: float) -> list[Any]:
    return [
        (g.event_ids, g.label_ids, g.shape, g.ambiguity)
        for g in associate(events, labels, r).groups
    ]


def test_basic_shapes() -> None:
    ev = [EventNode("a", 1.0, 1.0), EventNode("b", 5.0, 5.0)]
    lb = [LabelNode("10", 1.1), LabelNode("11", 3.0)]
    assert _shapes(ev, lb, 0.2) == [
        (("a",), ("10",), GroupShape.ASSOCIATED, ()),
        ((), ("11",), GroupShape.LABEL_ONLY, ()),
        (("b",), (), GroupShape.EVENT_ONLY, ()),
    ]


def test_two_events_near_one_impact_are_ambiguous_not_assigned() -> None:
    ev = [EventNode("a", 0.9, 0.9), EventNode("b", 1.15, 1.15)]
    (g,) = associate(ev, [LabelNode("10", 1.0)], 0.2).groups
    assert g.shape is GroupShape.AMBIGUOUS
    assert g.ambiguity == (AmbiguityReason.SEVERAL_EVENTS,)


def test_window_event_touching_two_impacts_is_ambiguous() -> None:
    ev = [EventNode("w", 1.0, 1.5)]
    (g,) = associate(ev, [LabelNode("10", 0.9), LabelNode("11", 1.6)], 0.2).groups
    assert g.shape is GroupShape.AMBIGUOUS
    assert g.ambiguity == (AmbiguityReason.SEVERAL_LABELS,)


def test_chain_e_i_e_i_is_one_group_of_four() -> None:
    ev = [EventNode("e1", 1.0, 1.0), EventNode("e2", 1.3, 1.3)]
    lb = [LabelNode("10", 1.15), LabelNode("11", 1.45)]
    (g,) = associate(ev, lb, 0.2).groups
    assert (g.event_ids, g.label_ids) == (("e1", "e2"), ("10", "11"))
    assert g.ambiguity == (AmbiguityReason.SEVERAL_EVENTS, AmbiguityReason.SEVERAL_LABELS)


def test_distance_zero_inside_window_and_from_edge_outside() -> None:
    a = associate([EventNode("w", 1.0, 2.0)], [LabelNode("1", 1.5), LabelNode("2", 2.15)], 0.5)
    by_label = {e.label_id: e for e in a.edges}
    assert by_label["1"].distance_s == 0 and by_label["1"].signed_s == 0
    assert by_label["2"].distance_s == pytest.approx(0.15)
    assert by_label["2"].signed_s == pytest.approx(-0.15)  # event before label


def test_shuffled_inputs_give_same_groups_and_ids() -> None:
    ev = [EventNode(f"e{i}", i * 0.7, i * 0.7 + (0.1 if i % 3 == 0 else 0)) for i in range(12)]
    lb = [LabelNode(str(100 + i), i * 0.65 + 0.05) for i in range(12)]
    ref = associate(ev, lb, 0.2)
    rng = random.Random(7)
    for _ in range(5):
        e2, l2 = ev[:], lb[:]
        rng.shuffle(e2)
        rng.shuffle(l2)
        assert associate(e2, l2, 0.2) == ref


def test_edge_tolerance_is_only_numeric() -> None:
    assert len(associate([EventNode("a", 1.0, 1.0)], [LabelNode("1", 1.2)], 0.2).edges) == 1
    assert associate([EventNode("a", 1.0, 1.0)], [LabelNode("1", 1.2001)], 0.2).edges == ()


# ---------------------------------------------------------------- outcomes per radius


def test_intercepted_uncertain_missed_and_unassociated(tmp_path: Path) -> None:
    res = _run(
        tmp_path,
        [_ev("e1", 30), _ev("e2", 90, "uncertain"), _ev("e3", 250)],
        [_lbl(31, 2), _lbl(92, 3, "backhand"), _lbl(150, 4)],
    )
    (rr,) = res.radii
    assert _impact(rr, 2).outcome is ImpactOutcome.INTERCEPTED
    assert _impact(rr, 2).interception.peaks[0].signed_frames == -1
    assert _impact(rr, 3).outcome is ImpactOutcome.INTERCEPTED_UNCERTAIN
    assert _impact(rr, 4).outcome is ImpactOutcome.MISSED
    assert [u.event_id for u in rr.unassociated_events] == ["e3"]


def test_two_events_near_impact_not_assigned_in_eval(tmp_path: Path) -> None:
    res = _run(tmp_path, [_ev("e1", 57), _ev("e2", 63)], [_lbl(60, 2)])
    (rr,) = res.radii
    imp = _impact(rr, 2)
    assert imp.outcome is ImpactOutcome.AMBIGUOUS
    assert imp.interception is None and imp.miss is None
    assert rr.unassociated_events == []  # ambiguous events are not "unassociated"
    (g,) = [g for g in rr.target_groups if g.id == imp.group_id]
    assert g.event_ids == ["e1", "e2"] and g.ambiguity == ["several_events"]


def test_groups_rebuild_from_saved_edges_only(tmp_path: Path) -> None:
    """P1: components and outcomes written = those rebuilt from the saved edges alone."""
    res = _run(
        tmp_path,
        [_ev("e1", 30), _ev("e2", 36), _ev("e3", 120), _ev("e4", 200)],
        [_lbl(33, 2), _lbl(120, 3), _lbl(160, 4), _lbl(201, 5, "serve")],
        radii=(0.2, 0.5),
    )
    for rr in res.radii:
        graph_events = [e for e in res.events if e.in_graph]
        nodes = [
            EventNode(e.id, *(e.window_t_s if e.t_s is None else (e.t_s, e.t_s)))
            for e in graph_events
        ]
        targets = [LabelNode(str(lb.line), lb.t_s) for lb in res.labels if lb.role == "target"]
        edges = tuple(
            Edge(x.event_id, str(x.label_line), x.distance_s, x.signed_s) for x in rr.target_edges
        )
        rebuilt = groups_from_edges(nodes, targets, edges, "t")
        assert [
            (g.id, list(g.event_ids), sorted(int(x) for x in g.label_ids)) for g in rebuilt
        ] == [(g.id, g.event_ids, g.label_lines) for g in rr.target_groups]
        for g in rr.target_groups:
            if g.ambiguity:
                linked = {(x.event_id, x.label_line) for x in rr.target_edges}
                assert any((e, lb) in linked for e in g.event_ids for lb in g.label_lines)


def test_rejected_events_stay_out_of_graph_but_explain_misses(tmp_path: Path) -> None:
    res = _run(tmp_path, [_ev("e1", 60, "rejected")], [_lbl(62, 2)])
    (rr,) = res.radii
    assert rr.target_edges == []
    miss = _impact(rr, 2).miss
    assert miss.cause is MissCause.EVENT_REJECTED
    assert miss.rejected_events[0].event_id == "e1"
    assert miss.rejected_events[0].reasons == ["too_weak"]
    assert not next(e for e in res.events if e.id == "e1").in_graph


def test_radii_are_independent_and_transitions_listed(tmp_path: Path) -> None:
    events = [_ev("e1", 60), _ev("e2", 72)]  # e2 0.4 s after the impact
    labels = [_lbl(60, 2)]
    both = _run(tmp_path, events, labels, radii=(0.5, 0.2))
    alone = {r: _run(tmp_path, events, labels, radii=(r,)).radii[0] for r in (0.2, 0.5)}
    assert [rr.radius_s for rr in both.radii] == [0.2, 0.5]
    for rr in both.radii:
        assert rr == alone[rr.radius_s]
    assert _impact(both.radii[0], 2).outcome is ImpactOutcome.INTERCEPTED
    assert _impact(both.radii[1], 2).outcome is ImpactOutcome.AMBIGUOUS
    (tr,) = both.transitions
    assert (tr.label_line, tr.from_radius_s, tr.from_outcome, tr.to_radius_s, tr.to_outcome) == (
        2,
        0.2,
        ImpactOutcome.INTERCEPTED,
        0.5,
        ImpactOutcome.AMBIGUOUS,
    )


# ---------------------------------------------------------------- radius check (P4)


def test_radius_check_uses_all_labels_and_marks_not_interpretable(tmp_path: Path) -> None:
    # D comes from a B label and a 'none' label 0.5 s apart, not from the target impacts
    labels = [_lbl(30, 2), _lbl(150, 3, "serve", "B"), _lbl(165, 4, "none")]
    res = _run(tmp_path, [_ev("e1", 30)], labels, radii=(0.2, 0.3))
    assert res.gaps.min_label_gap_s == pytest.approx(0.5)
    assert res.gaps.gap_lines == (3, 4)
    st = {rr.radius_s: rr.status for rr in res.radii}
    assert st == {0.2: RadiusStatus.OK, 0.3: RadiusStatus.NOT_INTERPRETABLE}
    nip = res.radii[1]
    assert nip.impacts and "D = 0.5000" in (nip.status_reason or "")  # kept, marked

    # the check follows the label file: move one label and it changes
    res2 = _run(
        tmp_path, [_ev("e1", 30)], [labels[0], labels[1], _lbl(180, 4, "none")], radii=(0.3,)
    )
    assert res2.radii[0].status is RadiusStatus.OK


def test_radius_check_fewer_than_two_labels_and_equal_times(tmp_path: Path) -> None:
    for labels in ([], [_lbl(30, 2)]):
        res = _run(tmp_path, [_ev("e1", 30)], labels)
        assert res.gaps.min_label_gap_s is None
        assert res.radii[0].status is RadiusStatus.NOT_APPLICABLE
        assert res.radii[0].status_reason == "fewer_than_two_labels"
    res = _run(tmp_path, [], [_lbl(30, 2), _lbl(30, 3, "serve", "B")], radii=(0.1, 0.5))
    assert res.gaps.min_label_gap_s == 0
    assert {rr.status for rr in res.radii} == {RadiusStatus.NOT_INTERPRETABLE}


def test_w_is_the_actual_span_of_a_chained_event_longer_than_g(tmp_path: Path) -> None:
    chained = {
        "id": "e1",
        "rep": "right",
        "peaks": [("pa", "right", 30), ("pb", "left", 33), ("pc", "right", 36)],
    }
    res = _run(tmp_path, [chained, _ev("e2", 200, "rejected")], [_lbl(30, 2), _lbl(60, 3)])
    assert res.gaps.max_event_span_s == pytest.approx(0.2)  # > G = 0.1
    assert res.gaps.max_span_event_id == "e1"
    # 2 * 0.2 + 0.2 = 0.6 < D = 1.0 -> ok; rejected events do not count in W
    assert res.radii[0].status is RadiusStatus.OK


# ---------------------------------------------------------------- miss causes


def _poses_with(overrides: dict[int, dict[str, float] | None]) -> PoseSequence:
    return _poses(300, overrides=overrides)


def test_cause_no_pose(tmp_path: Path) -> None:
    poses = _poses_with({f: None for f in range(50, 71)})
    res = _run(tmp_path, [_ev("e1", 62, "rejected")], [_lbl(60, 2)], poses=poses, radii=(0.2,))
    assert _impact(res.radii[0], 2).miss.cause is MissCause.NO_POSE  # beats event_rejected


def test_cause_both_wrists_unreliable(tmp_path: Path) -> None:
    low = {"left_wrist": 0.1, "right_wrist": 0.39}
    poses = _poses_with({f: low for f in range(50, 71)})
    res = _run(tmp_path, [_ev("e1", 62, "rejected")], [_lbl(60, 2)], poses=poses)
    assert _impact(res.radii[0], 2).miss.cause is MissCause.BOTH_WRISTS_UNRELIABLE
    # one usable wrist sample (0.4 = low but usable) is enough to pass the check
    poses = _poses_with({f: (low if f != 60 else {"left_wrist": 0.4}) for f in range(50, 71)})
    res = _run(tmp_path, [_ev("e1", 62, "rejected")], [_lbl(60, 2)], poses=poses)
    assert _impact(res.radii[0], 2).miss.cause is MissCause.EVENT_REJECTED


def test_cause_peak_discarded_then_outside_radius_then_no_peak(tmp_path: Path) -> None:
    res = _run(tmp_path, [_ev("e1", 100)], [_lbl(60, 2)], discarded=[("px", "left", 64)])
    miss = _impact(res.radii[0], 2).miss
    assert miss.cause is MissCause.PEAK_DISCARDED
    assert miss.discarded_peaks[0].reasons == ["peak_low"]
    assert miss.nearest_free_event.event_id == "e1"  # recorded, but cause 4 comes first

    res = _run(tmp_path, [_ev("e1", 100)], [_lbl(60, 2)])
    miss = _impact(res.radii[0], 2).miss
    assert miss.cause is MissCause.EVENT_OUTSIDE_RADIUS
    assert miss.nearest_free_event.signed_s == pytest.approx(40 / FPS)

    res = _run(tmp_path, [_ev("e1", 200)], [_lbl(60, 2)])  # 4.67 s away: beyond 2 s
    miss = _impact(res.radii[0], 2).miss
    assert miss.cause is MissCause.NO_PEAK and miss.nearest_free_event is None


def test_intercepted_or_ambiguous_events_never_explain_another_miss(tmp_path: Path) -> None:
    res = _run(
        tmp_path,
        [_ev("e1", 30), _ev("e2", 100), _ev("e3", 106)],
        [_lbl(30, 2), _lbl(60, 3), _lbl(103, 4)],
    )
    rr = res.radii[0]
    assert _impact(rr, 2).outcome is ImpactOutcome.INTERCEPTED
    assert _impact(rr, 4).outcome is ImpactOutcome.AMBIGUOUS
    assert _impact(rr, 3).miss.cause is MissCause.NO_PEAK  # e1 and e2/e3 are not free


def test_shared_free_event_is_cited_for_both_misses(tmp_path: Path) -> None:
    res = _run(tmp_path, [_ev("e1", 100)], [_lbl(70, 2), _lbl(130, 3)])
    rr = res.radii[0]
    a, b = _impact(rr, 2).miss.nearest_free_event, _impact(rr, 3).miss.nearest_free_event
    assert a.event_id == b.event_id == "e1"
    assert a.shared_with == [3] and b.shared_with == [2]
    assert [u.event_id for u in rr.unassociated_events] == ["e1"]  # still unassociated


# ---------------------------------------------------------------- intervals and verdicts


def test_unassociated_verdicts_follow_coverage_and_position(tmp_path: Path) -> None:
    events = [_ev("e1", 50), _ev("e2", 100), _ev("e3", 150), _ev("e4", 205), _ev("e5", 260)]
    labels = [
        _lbl(52, 2, "serve"),
        _lbl(100, 3, "none"),
        _lbl(150, 4, "forehand", "B"),
        _lbl(262, 5, "volley"),
    ]
    intervals = [_iv(0, 209, "all", "A", line=2), _iv(250, 299, "forehand;backhand", "A", line=3)]
    rr = _run(tmp_path, events, labels, intervals=intervals).radii[0]
    got = {u.event_id: (u.position, u.verdict, u.cause) for u in rr.unassociated_events}
    assert got == {
        "e1": (Position.INSIDE, Verdict.JUDGED, UnassociatedCause.NON_TARGET_STROKE),
        "e2": (Position.INSIDE, Verdict.JUDGED, UnassociatedCause.NOT_A_STROKE),
        "e3": (Position.INSIDE, Verdict.JUDGED, UnassociatedCause.OTHER_PLAYER),
        "e4": (Position.NEAR_BOUNDARY, Verdict.NOT_JUDGED_POSITION, None),
        # near a volley label, but the interval is not exhaustive: never judged
        "e5": (Position.INSIDE, Verdict.NOT_JUDGEABLE_COVERAGE, None),
    }
    e5 = _unassoc(rr, "e5")
    assert e5.missing_types == ["serve", "volley", "other"] and e5.missing_players is None


def test_unlabelled_ambiguous_outside_and_no_intervals(tmp_path: Path) -> None:
    events = [_ev("e1", 50), _ev("e2", 120), _ev("e3", 280)]
    labels = [_lbl(118, 2, "none"), _lbl(124, 3, "serve")]
    rr = _run(tmp_path, events, labels, intervals=[_iv(0, 200, "all", "all_visible")]).radii[0]
    assert _unassoc(rr, "e1").cause is UnassociatedCause.UNLABELLED
    assert _unassoc(rr, "e2").cause is UnassociatedCause.AMBIGUOUS
    assert _unassoc(rr, "e3").position is Position.OUTSIDE_ANNOTATED
    og = next(g for g in rr.other_groups if g.id == _unassoc(rr, "e2").other_group_id)
    assert og.ambiguity == ["several_labels"]

    res = _run(tmp_path, events, labels, intervals=[])
    assert {u.position for u in res.radii[0].unassociated_events} == {Position.NO_INTERVALS}
    assert any("no annotated intervals" in w for w in res.warnings)


def test_players_without_a_is_not_exhaustive(tmp_path: Path) -> None:
    rr = _run(tmp_path, [_ev("e1", 100)], [], intervals=[_iv(0, 299, "all", "B")]).radii[0]
    u = _unassoc(rr, "e1")
    assert u.verdict is Verdict.NOT_JUDGEABLE_COVERAGE and u.missing_players == "A"


def test_impact_at_interval_edge_intercepted_by_event_just_outside(tmp_path: Path) -> None:
    res = _run(tmp_path, [_ev("e1", 103)], [_lbl(100, 2)], intervals=[_iv(0, 100)])
    assert _impact(res.radii[0], 2).outcome is ImpactOutcome.INTERCEPTED


# ---------------------------------------------------------------- time rules (P3, P3 bis)


def _map_with_gap(n_source: int = 300, drop: tuple[int, ...] = (100,)) -> TimeMap:
    """30 fps source with frames removed: the proxy repeats the previous image there."""
    pts = [i / FPS for i in range(n_source) if i not in drop]
    return build_timemap(pts, FPS, n_source)


def test_label_time_from_map_and_frame_states(tmp_path: Path) -> None:
    tm = _map_with_gap()
    repeated = [k for k in range(1, 300) if tm.proxy_to_source[k] == tm.proxy_to_source[k - 1]]
    assert repeated  # the dropped source frame makes one proxy frame repeat
    rep = repeated[0]
    poses = _poses(300, overrides={rep - 1: None, 200: None})
    t = lambda f: tm.source_times_s[tm.proxy_to_source[f]]  # noqa: E731
    doc = _strokes([_ev("e1", 30)], t=t, basis="source", map_status="ok")
    res = evaluate_clip(
        _clipdata(poses, tm),
        [_lbl(rep, 2), _lbl(200, 3, "serve"), _lbl(30, 4)],
        [_iv(0, 299)],
        _load(tmp_path, doc),
        radii=(0.2,),
    )
    by_line = {lb.line: lb for lb in res.labels}
    assert by_line[2].frame_state is FrameState.REPEATED_FRAME
    assert by_line[2].kept_frame == rep - 1 and by_line[2].kept_frame_detected is False
    assert by_line[2].t_s == pytest.approx(t(rep - 1))  # same image, same time
    assert by_line[3].frame_state is FrameState.NO_POSE_FRAME and not by_line[3].kinematic_sample
    assert by_line[4].frame_state is FrameState.SAMPLE and by_line[4].kinematic_sample
    assert res.time_basis == "source" and not res.unverified_time
    # the long interval left by the missing source frame is reported, never corrected
    imp = _impact(res.radii[0], 2)
    assert imp.irregular_time_in_window and imp.irregular_intervals_in_window >= 1
    assert not _impact(res.radii[0], 4).irregular_time_in_window


def test_stride_frame_state_and_proxy_basis_unverified(tmp_path: Path) -> None:
    poses = _poses(300, stride=2)
    # without a map the domain ends at the last pose frame (298): beyond it nobody knows
    res = evaluate_clip(
        _clipdata(poses),
        [_lbl(31, 2), _lbl(60, 3)],
        [_iv(0, 298)],
        _load(tmp_path, _strokes([_ev("e1", 30)])),
        radii=(0.2,),
    )
    st = {lb.line: lb.frame_state for lb in res.labels}
    assert st == {2: FrameState.NOT_SAMPLED_STRIDE, 3: FrameState.SAMPLE}
    assert res.unverified_time and res.labels[0].t_s == pytest.approx(31 / FPS)


def test_label_out_of_domain_and_pose_integrity_errors(tmp_path: Path) -> None:
    loaded = _load(tmp_path, _strokes([_ev("e1", 30)]))
    with pytest.raises(TennisCVError) as exc:
        evaluate_clip(_clipdata(), [_lbl(300, 2)], [], loaded)
    assert exc.value.code == "label_frame_out_of_domain"
    broken = _poses(300)
    broken.frames.pop(10)  # a sampled frame missing
    with pytest.raises(TennisCVError) as exc:
        evaluate_clip(_clipdata(broken), [_lbl(10, 2)], [], loaded)
    assert exc.value.code == "pose_integrity_error"  # before looking at labels


def test_time_basis_mismatch_peak_not_sample_and_written_seconds(tmp_path: Path) -> None:
    tm = _map_with_gap()
    t = lambda f: tm.source_times_s[tm.proxy_to_source[f]]  # noqa: E731
    # file says proxy, but a consistent map exists: rebuilt basis is source
    doc = _strokes([_ev("e1", 30)], basis="proxy", map_status="inconsistent")
    with pytest.raises(TennisCVError) as exc:
        evaluate_clip(_clipdata(None, tm), [], [], _load(tmp_path, doc))
    assert exc.value.code == "strokes_time_basis_mismatch"

    rep = next(k for k in range(1, 300) if tm.proxy_to_source[k] == tm.proxy_to_source[k - 1])
    doc = _strokes([_ev("e1", rep)], t=t, basis="source", map_status="ok")
    with pytest.raises(TennisCVError) as exc:
        evaluate_clip(_clipdata(None, tm), [], [], _load(tmp_path, doc))
    assert exc.value.code == "strokes_peak_not_a_sample"

    doc = _strokes([_ev("e1", 30)], t=lambda f: f / FPS + 0.0002)  # consistent, but shifted
    with pytest.raises(TennisCVError) as exc:
        evaluate_clip(_clipdata(), [], [], _load(tmp_path, doc))
    assert exc.value.code == "strokes_time_mismatch"


def test_params_must_be_recorded(tmp_path: Path) -> None:
    prm: Spec = {
        "signals": {
            "timebase": {"max_interval_ratio": 1.5, "min_interval_ratio": 0.5},
            "smoothing": {"note": "missing thresholds"},
        }
    }
    doc = _strokes([_ev("e1", 30)], params=prm)
    with pytest.raises(TennisCVError) as exc:
        evaluate_clip(_clipdata(), [], [], _load(tmp_path, doc))
    assert exc.value.code == "strokes_params_invalid"


def test_minimal_m2_1_format_read_as_instant_events(tmp_path: Path) -> None:
    p = tmp_path / "min.json"
    p.write_text(
        json.dumps({"events": [{"peak_frame": 62}, {"peak_frame": 150, "status": "rejected"}]})
    )
    loaded = load_events(p)
    assert loaded.format is StrokesFormat.M2_1_MINIMAL
    res = evaluate_clip(
        _clipdata(), [_lbl(60, 2), _lbl(152, 3)], [_iv(0, 299)], loaded, radii=(0.2,)
    )
    rr = res.radii[0]
    assert _impact(rr, 2).outcome is ImpactOutcome.INTERCEPTED
    assert _impact(rr, 2).interception.event_signed_frames == 2
    assert _impact(rr, 3).miss.cause is MissCause.EVENT_REJECTED
    assert res.params_used.source == "defaults"


def test_window_only_event_has_no_instant_offset(tmp_path: Path) -> None:
    ev = {
        "id": "e1",
        "status": "uncertain",
        "rep": "undetermined",
        "peaks": [("pa", "left", 58), ("pb", "right", 61)],
    }
    res = _run(tmp_path, [ev], [_lbl(60, 2)])
    inter = _impact(res.radii[0], 2).interception
    assert inter.event_signed_s is None and len(inter.peaks) == 2
    assert next(e for e in res.events if e.id == "e1").t_s is None


# ---------------------------------------------------------------- dataset, file and CLI


def _dataset(tmp_path: Path, *, with_second_clip: bool = True) -> tuple[Path, Path, Path]:
    rows = []
    for cid in ("c1", "c2") if with_second_clip else ("c1",):
        d = tmp_path / "poses" / cid
        d.mkdir(parents=True)
        (d / "pose.json").write_text(_poses(300).model_dump_json())
        rows.append(
            f"| {cid} | a.mov | poses/{cid} | behind | right | two_handed | no | none | dev | |"
        )
    (tmp_path / "MANIFEST.md").write_text(_manifest_text(rows))
    pose_sha = file_sha256(tmp_path / "poses" / "c1" / "pose.json")
    doc = _strokes([_ev("e1", 30), _ev("e2", 150)], pose_sha=pose_sha)
    (tmp_path / "poses" / "c1" / "strokes.json").write_text(json.dumps(doc))
    labels = tmp_path / "labels.csv"
    labels.write_text(
        ",".join(LABEL_COLUMNS) + "\nc1,31,forehand,A,full,,\nc1,90,backhand,A,"
        "partial,ok,\nc1,151,serve,A,full,,\n"
    )
    annotated = tmp_path / "annotated.csv"
    annotated.write_text(
        "clip_id,start_frame,end_frame,labelled_types,players,notes\nc1,0,299,all,A,\n"
    )
    return tmp_path / "MANIFEST.md", labels, annotated


def test_evaluate_dataset_file_is_canonical_and_repeatable(tmp_path: Path) -> None:
    manifest, labels, annotated = _dataset(tmp_path)
    a = evaluate_dataset(manifest, labels, annotated)
    b = evaluate_dataset(manifest, labels, annotated)
    assert same_eval(a, b)
    text = dump_eval(a)
    again = StrokesEvalFile.model_validate_json(text)
    assert dump_eval(again) == text
    assert a.clips_without_results == ["c2"]
    assert any("c2" in w for w in a.warnings)
    (c1,) = a.clips
    assert c1.strokes.sha256 == file_sha256(tmp_path / "poses" / "c1" / "strokes.json")
    assert a.inputs.annotated is not None and a.inputs.labels.file == "labels.csv"
    r02 = next(rr for rr in c1.radii if rr.radius_s == 0.2)
    # e2 (frame 150) is free: 2.0 s from the missed backhand, inside the search limit
    assert _impact(r02, 3).miss.cause is MissCause.EVENT_OUTSIDE_RADIUS
    assert _unassoc(r02, "e2").cause is UnassociatedCause.NON_TARGET_STROKE
    assert next(lb for lb in c1.labels if lb.line == 3).estimated_frame


def test_evaluate_dataset_errors(tmp_path: Path) -> None:
    manifest, labels, annotated = _dataset(tmp_path)
    annotated.write_text(
        "clip_id,start_frame,end_frame,labelled_types,players,notes\nc1,0,100,all,A,\n"
    )
    with pytest.raises(DatasetFormatError) as exc:
        evaluate_dataset(manifest, labels, annotated)
    assert any("outside every annotated interval" in p for p in exc.value.details["problems"])
    # stale pose.json: hash check
    annotated.write_text(
        "clip_id,start_frame,end_frame,labelled_types,players,notes\nc1,0,299,all,A,\n"
    )
    (tmp_path / "poses" / "c1" / "pose.json").write_text(
        _poses(300, fps=30.0).model_dump_json(indent=1)
    )
    with pytest.raises(TennisCVError) as exc2:
        evaluate_dataset(manifest, labels, annotated)
    assert exc2.value.code == "strokes_inputs_stale"
    assert evaluate_dataset(manifest, labels, annotated, check_input_hashes=False).clips


def test_cli_evaluate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest, labels, _ = _dataset(tmp_path)
    out = tmp_path / "strokes_eval.json"
    cli = _cli()
    code = cli.main(
        ["evaluate", str(manifest), str(labels), "--out", str(out), "--radii", "0.3,0.1"]
    )
    assert code == 0
    text = capsys.readouterr()
    assert "r=0.1s" in text.out and "r=0.3s" in text.out
    assert "annotated.csv" not in text.err  # picked up next to labels.csv
    doc = StrokesEvalFile.model_validate_json(out.read_text())
    assert doc.params.radii_s == [0.1, 0.3]
    assert doc.inputs.annotated is not None
