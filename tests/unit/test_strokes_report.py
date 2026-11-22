"""M2.3 reports (PR 3): groups, offsets (nearest rank), event statistics, false candidates
and the review sheet. Synthetic strokes files only; no number here is a real result."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from tennis_cv.evaluation.dataset import LABEL_COLUMNS, DatasetFormatError, load_manifest
from tennis_cv.evaluation.eval_artifact import StrokesEvalFile
from tennis_cv.evaluation.eval_report import (
    REVIEW_COLUMNS,
    EvalReport,
    build_report,
    distribution,
    load_review,
    nearest_rank,
    render_report,
    render_review_csv,
)
from tennis_cv.evaluation.evaluate import LoadedStrokes, evaluate_dataset, load_events
from tennis_cv.strokes.artifact import file_sha256
from tests.unit.test_eval_strokes import _cli, _manifest_text, _poses
from tests.unit.test_strokes_evaluation import _ev, _strokes

Spec = dict[str, Any]

# ---------------------------------------------------------------- nearest rank (P7)


def test_nearest_rank_known_cases() -> None:
    assert nearest_rank([7.0], 25) == nearest_rank([7.0], 90) == 7.0
    two = [3.0, 1.0]
    assert [nearest_rank(two, p) for p in (25, 50, 75, 90)] == [1.0, 1.0, 3.0, 3.0]
    four = [4.0, 1.0, 3.0, 2.0]
    assert [nearest_rank(four, p) for p in (25, 50, 75, 90)] == [1.0, 2.0, 3.0, 4.0]
    five = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert [nearest_rank(five, p) for p in (25, 50, 75, 90)] == [2.0, 3.0, 4.0, 5.0]
    ten = [float(x) for x in range(10, 0, -1)]
    assert [nearest_rank(ten, p) for p in (25, 50, 75, 90)] == [3.0, 5.0, 8.0, 9.0]
    for values in (two, four, five, ten):
        for p in (25, 50, 75, 90):
            assert nearest_rank(values, p) in values  # never an average of two
    assert nearest_rank([], 50) is None
    with pytest.raises(ValueError):
        nearest_rank([1.0], 0)


def test_distribution_empty_and_signed_vs_abs() -> None:
    d = distribution([])
    assert (d.n, d.min, d.median, d.max) == (0, None, None, None)
    signed = distribution([-30.0, 10.0, 20.0])
    absolute = distribution(abs(x) for x in [-30.0, 10.0, 20.0])
    assert (signed.min, signed.median, signed.max) == (-30.0, 10.0, 20.0)
    assert (absolute.min, absolute.median, absolute.max) == (10.0, 20.0, 30.0)


# ---------------------------------------------------------------- synthetic dataset


def _dataset(
    tmp_path: Path, clips: dict[str, tuple[list[Spec], list[str]]], views: dict[str, str]
) -> tuple[Path, Path, Path]:
    """clips: clip_id -> (events, label rows 'frame,type,player,visibility,overlay')."""
    rows, labels, annotated = (
        [],
        [",".join(LABEL_COLUMNS)],
        ["clip_id,start_frame,end_frame,labelled_types,players,notes"],
    )
    for cid, (events, label_rows) in clips.items():
        d = tmp_path / "poses" / cid
        d.mkdir(parents=True)
        (d / "pose.json").write_text(_poses(300).model_dump_json())
        doc = _strokes(events, pose_sha=file_sha256(d / "pose.json"))
        (d / "strokes.json").write_text(json.dumps(doc))
        rows.append(
            f"| {cid} | a.mov | poses/{cid} | {views[cid]} | right | two_handed | no | none "
            "| dev | |"
        )
        labels += [f"{cid},{r}," for r in label_rows]
        annotated.append(f"{cid},20,279,all,A,")
    (tmp_path / "MANIFEST.md").write_text(_manifest_text(rows))
    (tmp_path / "labels.csv").write_text("\n".join(labels) + "\n")
    (tmp_path / "annotated.csv").write_text("\n".join(annotated) + "\n")
    return tmp_path / "MANIFEST.md", tmp_path / "labels.csv", tmp_path / "annotated.csv"


TWO_WRISTS = {
    "id": "e2",
    "status": "uncertain",
    "rep": "undetermined",
    "peaks": [("pe2a", "left", 118), ("pe2b", "right", 121)],
}
CLIPS: dict[str, tuple[list[Spec], list[str]]] = {
    "c1": (
        # e1 intercepts line 2 (label 31 -> peak 30: -1 frame); e2 window event on line 3;
        # e3 unassociated inside the interval; e4 rejected near the missed impact (line 4)
        [_ev("e1", 30), TWO_WRISTS, _ev("e3", 200), _ev("e4", 162, "rejected")],
        ["31,forehand,A,full,ok", "120,backhand,A,partial,off", "160,forehand,A,full,"],
    ),
    "c2": (
        # e1 at 64 vs label 60 (+4 frames), estimated label frame; e2 outside the interval
        [_ev("e1", 64), _ev("e2", 290)],
        ["60,forehand,A,partial,missing", "240,serve,B,full,"],
    ),
}
VIEWS = {"c1": "behind", "c2": "side_forehand"}


def _evaluated(tmp_path: Path) -> tuple[StrokesEvalFile, Any, dict[str, LoadedStrokes]]:
    manifest, labels, annotated = _dataset(tmp_path, CLIPS, VIEWS)
    doc = evaluate_dataset(manifest, labels, annotated, radii=[0.2])
    clips = {c.clip_id: c for c in load_manifest(manifest)}
    strokes = {cid: load_events(tmp_path / "poses" / cid / "strokes.json") for cid in CLIPS}
    return doc, clips, strokes


def _group(rep: EvalReport, dim: str, value: str) -> Any:
    return next(g for g in rep.radii[0].groups if g.dimension == dim and g.value == value)


# ---------------------------------------------------------------- report


def test_groups_have_denominators_and_small_samples(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    rep = build_report(doc, clips, strokes)
    allg = _group(rep, "all", "all")
    assert allg.impacts == 4 and allg.clips == 2 and allg.small_sample  # < 3 clips
    assert (allg.intercepted.n, allg.intercepted.of) == (2, 4)
    assert (allg.intercepted_uncertain.n, allg.missed.n, allg.ambiguous.n) == (1, 1, 0)
    assert allg.miss_causes["event_rejected"].n == 1 and allg.miss_causes["event_rejected"].of == 1
    assert _group(rep, "stroke_type", "backhand").impacts == 1
    assert _group(rep, "view", "side_forehand").impacts == 1
    assert _group(rep, "label_quality", "partial").impacts == 2
    assert _group(rep, "overlay_wrist", "not_checked").impacts == 1
    assert _group(rep, "event_wrist", "undetermined").intercepted_uncertain.n == 1
    assert _group(rep, "event_wrist", "no_associated_event").missed.n == 1
    assert {g.value for g in rep.radii[0].groups if g.dimension == "clip"} == {"c1", "c2"}
    # the report recomputes nothing: outcomes are those of strokes_eval.json
    outcomes = [i.outcome for c in doc.clips for i in c.radii[0].impacts]
    assert allg.intercepted.n == sum(o == "intercepted" for o in outcomes)


def test_offsets_exclude_estimated_frames_and_list_window_events(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    rr = build_report(doc, clips, strokes).radii[0]
    right = next(s for s in rr.offsets if s.wrist == "right")
    # c1 line 2: peak 30 vs label 31 (full) is used; c2 (label partial) is listed, excluded
    assert right.signed_frames.n == 1 and right.signed_frames.median == -1.0
    assert right.abs_frames.median == 1.0
    assert right.signed_ms.median == pytest.approx(-33.3)
    assert right.excluded_estimated == 1
    estimated = [r for r in rr.offset_rows if r.estimated_frame]
    assert [(r.clip_id, r.signed_frames) for r in estimated if r.wrist == "right"] == [("c2", 4)]
    assert {(w.clip_id, w.peak_id) for w in rr.window_only_events} == {
        ("c1", "pe2a"),
        ("c1", "pe2b"),
    }
    assert not any(r.peak_id in ("pe2a", "pe2b") for r in rr.offset_rows)


def test_event_statistics_and_wrist_check(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    rr = build_report(doc, clips, strokes).radii[0]
    ev = rr.events
    assert ev.available and ev.graph_events == 5
    assert (ev.one_wrist.n, ev.two_wrists.n, ev.undetermined_wrist.n) == (4, 1, 1)
    assert ev.possible_merge.n == 0
    assert (ev.rejected_near_impact.n, ev.rejected_near_impact.of) == (1, 1)
    checks = {(w.right_wrist_state, w.overlay_wrist): w.impacts for w in rr.right_wrist_vs_overlay}
    assert checks == {("good", "ok"): 1, ("good", "off"): 1, ("good", "missing"): 1}


def test_missing_strokes_files_give_unknown_details(tmp_path: Path) -> None:
    doc, clips, _ = _evaluated(tmp_path)
    rep = build_report(doc, clips, {})  # no strokes files given: details unknown, not guessed
    assert not rep.radii[0].events.available
    assert _group(rep, "event_wrist", "unknown").impacts == 3
    assert all(fc.wrists_with_peaks == [] for fc in rep.false_candidates)


def test_stale_strokes_file_is_refused(tmp_path: Path) -> None:
    doc, clips, _ = _evaluated(tmp_path)
    path = tmp_path / "poses" / "c1" / "strokes.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(Exception) as exc:
        build_report(doc, clips, {"c1": load_events(path)})
    assert getattr(exc.value, "code", None) == "strokes_eval_stale"


# ---------------------------------------------------------------- false candidates and sheet


def test_false_candidates_all_listed_sheet_only_inside(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    rep = build_report(doc, clips, strokes)
    got = {(fc.clip_id, fc.event_id): fc for fc in rep.false_candidates}
    assert set(got) == {("c1", "e3"), ("c2", "e2")}
    assert got[("c1", "e3")].in_review_sheet and got[("c1", "e3")].auto_causes == {
        "0.2": "unlabelled"
    }
    assert not got[("c2", "e2")].in_review_sheet  # outside the annotated interval
    assert got[("c1", "e3")].regions["right_wrist"] == "good"
    text = render_review_csv(rep.false_candidates)
    lines = text.splitlines()
    assert lines[0] == ",".join(REVIEW_COLUMNS)
    assert len(lines) == 2 and lines[1].startswith("c1,e3,") and lines[1].endswith(",,")


def _write_sheet(tmp_path: Path, rep: EvalReport, edit: dict[int, dict[str, str]]) -> Path:
    rows = list(csv.reader(io.StringIO(render_review_csv(rep.false_candidates))))
    for i, changes in edit.items():
        for col, value in changes.items():
            rows[i][REVIEW_COLUMNS.index(col)] = value
    p = tmp_path / "review.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    return p


def test_review_sheet_round_trip_and_merge(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    rep = build_report(doc, clips, strokes)
    sheet = _write_sheet(tmp_path, rep, {1: {"category": "between_strokes"}})
    merged = build_report(doc, clips, strokes, load_review(sheet))
    fc = next(f for f in merged.false_candidates if f.event_id == "e3")
    assert fc.category is not None and fc.category.value == "between_strokes"
    assert merged.radii[0].category_counts["between_strokes"] == 1
    assert merged.stale_review_rows == []
    # the next sheet keeps the category
    assert ",between_strokes," in render_review_csv(merged.false_candidates)


def test_review_sheet_bad_category_and_changed_hash(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    rep = build_report(doc, clips, strokes)
    bad = _write_sheet(tmp_path, rep, {1: {"category": "swing"}})
    with pytest.raises(DatasetFormatError) as exc:
        load_review(bad)
    assert any("review.csv:2" in p and "'swing'" in p for p in exc.value.details["problems"])

    stale = _write_sheet(
        tmp_path, rep, {1: {"category": "preparation", "strokes_sha256": "f" * 64}}
    )
    merged = build_report(doc, clips, strokes, load_review(stale))
    fc = next(f for f in merged.false_candidates if f.event_id == "e3")
    assert fc.category is None  # not reused
    (row,) = merged.stale_review_rows
    assert row.line == 2 and "sha256 differs" in row.reason


# ---------------------------------------------------------------- render and CLI


def test_render_report_states_limits(tmp_path: Path) -> None:
    doc, clips, strokes = _evaluated(tmp_path)
    text = render_report(build_report(doc, clips, strokes), source="strokes_eval.json")
    assert "not a validation unless the files come from the real detector" in text
    assert "c1: test 0.0.0" in text
    assert "| all | all | 2 | 4 | 2/4 | 1/4 | 1/4 | 0/4 | small sample |" in text
    assert "| right | signed frames | 1 | -1 | -1 | -1 | -1 | -1 | -1 | 1 |" in text


def test_cli_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest, labels, _ = _dataset(tmp_path, CLIPS, VIEWS)
    cli = _cli()
    ev = tmp_path / "strokes_eval.json"
    assert cli.main(["evaluate", str(manifest), str(labels), "--out", str(ev)]) == 0
    out, data, sheet = tmp_path / "r.md", tmp_path / "r.json", tmp_path / "s.csv"
    args = [
        "report",
        str(ev),
        str(manifest),
        "--out",
        str(out),
        "--json",
        str(data),
        "--review-csv",
        str(sheet),
    ]
    assert cli.main(args) == 0
    assert "## Radius 0.1 s" in out.read_text() and "## Radius 0.5 s" in out.read_text()
    assert EvalReport.model_validate_json(data.read_text()).clips == ["c1", "c2"]
    assert "review sheet written" in capsys.readouterr().out
    assert len(sheet.read_text().splitlines()) >= 2
    # merge a filled sheet back through the CLI
    text = sheet.read_text().splitlines()
    cells = text[1].split(",")
    cells[REVIEW_COLUMNS.index("category")] = "tracking_error"
    sheet.write_text("\n".join([text[0], ",".join(cells), *text[2:]]) + "\n")
    assert cli.main([*args[:-2], "--review-in", str(sheet)]) == 0
    assert "tracking_error" in out.read_text()
