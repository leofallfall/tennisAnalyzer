"""annotated.csv: parsing, overlap/order checks, label cross-checks and coverage (rule P2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tennis_cv.evaluation.annotated import (
    ANNOTATED_COLUMNS,
    COVERAGE_TYPES,
    AnnotatedInterval,
    check_against_labels,
    coverage,
    load_annotated,
)
from tennis_cv.evaluation.dataset import LABEL_COLUMNS, DatasetFormatError, LabelStrokeType
from tests.unit.test_eval_strokes import _cli, _clip, _label, _manifest_text, _poses

HEADER = ",".join(ANNOTATED_COLUMNS)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "annotated.csv"
    p.write_text(f"# comment\n{HEADER}\n{body}", encoding="utf-8")
    return p


def _iv(
    start: int,
    end: int,
    types: str = "all",
    players: str = "all_visible",
    clip_id: str = "c1",
    line: int = 3,
) -> AnnotatedInterval:
    all_visible = players == "all_visible"
    return AnnotatedInterval(
        clip_id=clip_id,
        start_frame=start,
        end_frame=end,
        labelled_types=(
            frozenset(COVERAGE_TYPES)
            if types == "all"
            else frozenset(LabelStrokeType(t) for t in types.split(";"))
        ),
        all_visible=all_visible,
        players=frozenset() if all_visible else frozenset(players.split(";")),
        line=line,
    )


def test_parse_valid_rows(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        'c1,0,99,forehand;backhand,A,"first part, A only"\n'
        "c1,100,199,all,all_visible,\n"
        "c2,10,10,serve;volley,A;B,\n",
    )
    ivs = load_annotated(p)
    assert [(iv.clip_id, iv.start_frame, iv.end_frame, iv.line) for iv in ivs] == [
        ("c1", 0, 99, 3),
        ("c1", 100, 199, 4),
        ("c2", 10, 10, 5),
    ]
    assert ivs[0].labelled_types == {LabelStrokeType.FOREHAND, LabelStrokeType.BACKHAND}
    assert ivs[0].players == {"A"} and not ivs[0].all_visible
    assert ivs[0].notes == "first part, A only"
    assert ivs[1].labelled_types == set(COVERAGE_TYPES) and ivs[1].all_visible
    assert ivs[2].players == {"A", "B"}
    assert ivs[0].contains(0) and ivs[0].contains(99) and not ivs[0].contains(100)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("c1,50,10,all,A,", "before start_frame"),
        ("c1,x,10,all,A,", "start_frame: 'x' is not a whole number"),
        ("c1,-1,10,all,A,", "frames must be >= 0"),
        ("c1,0,10,none,A,", "'none' is not one of"),
        ("c1,0,10,forehand;;backhand,A,", "empty item"),
        ("c1,0,10,forehand;forehand,A,", "repeated"),
        ("c1,0,10,all,a,", "players"),
        ("c1,0,10,all,A;A,", "repeated letter"),
        ("c1,0,10,all,,", "players"),
        ("c1,0,10,all,A", "5 fields, expected 6"),
    ],
)
def test_parse_errors_name_file_and_line(tmp_path: Path, row: str, message: str) -> None:
    with pytest.raises(DatasetFormatError) as exc:
        load_annotated(_write(tmp_path, row + "\n"))
    problems = exc.value.details["problems"]
    assert len(problems) == 1 and problems[0].startswith("annotated.csv:3:")
    assert message in problems[0]


def test_overlap_within_clip_is_an_error_but_not_across_clips(tmp_path: Path) -> None:
    ok = _write(tmp_path, "c1,0,99,all,A,\nc2,50,150,all,A,\nc1,100,120,all,A,\n")
    assert len(load_annotated(ok)) == 3
    bad = _write(tmp_path, "c1,100,200,all,A,\nc1,0,100,all,A,\n")
    with pytest.raises(DatasetFormatError) as exc:
        load_annotated(bad)
    (problem,) = exc.value.details["problems"]
    assert problem.startswith("annotated.csv:3: overlaps line 4")
    # A long interval overlapping two later ones is reported for both.
    nested = _write(tmp_path, "c1,0,100,all,A,\nc1,10,20,all,A,\nc1,30,40,all,A,\n")
    with pytest.raises(DatasetFormatError) as exc:
        load_annotated(nested)
    assert [p.split(":")[1] for p in exc.value.details["problems"]] == ["4", "5"]
    assert all("overlaps line 3" in p for p in exc.value.details["problems"])


def test_header_and_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "annotated.csv"
    p.write_text("clip_id,start,end\n")
    with pytest.raises(DatasetFormatError, match="header is invalid"):
        load_annotated(p)
    with pytest.raises(DatasetFormatError, match="not found"):
        load_annotated(tmp_path / "missing.csv")


def test_coverage_rule_p2_revised() -> None:
    full = coverage(_iv(0, 10))
    assert full.exhaustive and full.missing_types == () and full.missing_players is None
    # Revised 2026-11-22: A alone is enough, other people or not (one tracked skeleton).
    assert coverage(_iv(0, 10, players="A")).exhaustive
    assert coverage(_iv(0, 10, players="A;B")).exhaustive
    assert coverage(_iv(0, 10, players="B")).missing_players == "A"
    assert not coverage(_iv(0, 10, players="B")).exhaustive
    # Today's M2.1 labels: forehands and backhands of A only.
    current = coverage(_iv(0, 10, types="forehand;backhand", players="A"))
    assert current.missing_types == (
        LabelStrokeType.SERVE,
        LabelStrokeType.VOLLEY,
        LabelStrokeType.OTHER,
    )
    assert current.missing_players is None and not current.exhaustive


def test_cross_check_with_labels_and_manifest() -> None:
    clips = {"c1": _clip("c1"), "c2": _clip("c2")}
    intervals = [_iv(0, 99, types="forehand;backhand", players="A", line=3)]
    labels = [
        _label(50, "forehand", line=2),
        _label(60, "serve", line=3),
        _label(70, "backhand", player="B", line=4),
        _label(150, "forehand", line=5),
        _label(10, "forehand", clip_id="c2", line=6),
    ]
    problems, warnings = check_against_labels(intervals, labels, clips, {"c1": 200, "c2": 200})
    assert problems == [
        "labels.csv:5: frame 150 of clip c1 is outside every annotated interval of that clip"
    ]
    assert "clip c2: no annotated intervals (coverage unknown)" in warnings
    assert any(w.startswith("labels.csv:3: serve inside interval") for w in warnings)
    assert any(w.startswith("labels.csv:4: player B inside interval") for w in warnings)
    assert not any(w.startswith("labels.csv:2:") for w in warnings)
    # The label inside an interval of c2 is never checked against c1.
    assert not any("labels.csv:6" in p for p in problems)

    bad = [_iv(0, 200, line=3), _iv(0, 5, clip_id="zz", line=4)]
    problems, _ = check_against_labels(bad, [], clips, {"c1": 200, "c2": 200})
    assert problems == [
        "annotated.csv:3: end_frame 200 is past the end of clip c1 (200 frames, last = 199)",
        "annotated.csv:4: unknown clip_id 'zz'",
    ]


def test_cli_check_reads_annotated_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _cli()
    pose_dir = tmp_path / "poses" / "c1"
    pose_dir.mkdir(parents=True)
    (pose_dir / "pose.json").write_text(_poses(120).model_dump_json())
    (tmp_path / "MANIFEST.md").write_text(
        _manifest_text(
            ["| c1 | a.mov | poses/c1 | behind | right | two_handed | yes | none | dev | |"]
        )
    )
    (tmp_path / "labels.csv").write_text(
        ",".join(LABEL_COLUMNS) + "\nc1,30,forehand,A,full,,\nc1,90,serve,B,full,,\n"
    )
    args = ["check", str(tmp_path / "MANIFEST.md"), str(tmp_path / "labels.csv")]

    # Without annotated.csv: still OK, with a warning.
    assert cli.main(args) == 0
    assert "coverage unknown" in capsys.readouterr().out

    # Picked up automatically next to labels.csv.
    (tmp_path / "annotated.csv").write_text(
        HEADER + "\nc1,0,59,forehand;backhand,A,\nc1,60,119,all,all_visible,\n"
    )
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "annotated.csv:2: c1 frames 0-59: NOT exhaustive, missing serve, volley, other\n" in out
    assert "annotated.csv:3: c1 frames 60-119: exhaustive" in out
    assert "2 annotated intervals" in out

    # Explicit path; a label outside every interval is an error.
    other = tmp_path / "other.csv"
    other.write_text(HEADER + "\nc1,0,59,all,all_visible,\n")
    assert cli.main([*args, "--annotated", str(other)]) == 1
    assert "frame 90 of clip c1 is outside every annotated interval" in capsys.readouterr().err
