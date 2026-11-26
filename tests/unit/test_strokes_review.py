"""strokes_review.json: the user's corrections, kept apart from strokes.json (ADR-031)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tennis_cv.evaluation.annotated import load_annotated
from tennis_cv.evaluation.dataset import ImpactVisibility, load_labels
from tennis_cv.strokes import review as rv
from tennis_cv.strokes.artifact import load_strokes

FIXTURE = Path(__file__).parents[1] / "fixtures" / "strokes" / "example_v1_0.json"


def _stroke(frame: int, **kw: object) -> rv.ReviewEntry:
    return rv.ReviewEntry(
        id=rv.new_id(), kind=rv.ReviewKind.STROKE, frame=frame, updated_at=rv.now(), **kw
    )


def _stretch(lo: int, hi: int) -> rv.ReviewedStretch:
    return rv.ReviewedStretch(id=rv.new_id(), start_frame=lo, end_frame=hi, updated_at=rv.now())


def test_round_trip_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / rv.REVIEW_FILE
    assert rv.load_review(path, "v1") == rv.empty_review("v1")
    doc = rv.StrokesReview(
        video_id="v1",
        entries=(_stroke(10, stroke_type=rv.ReviewStrokeType.FOREHAND, note="ok, bello"),),
        reviewed_stretches=(_stretch(0, 99),),
    )
    rv.save_review(path, doc)
    assert rv.load_review(path, "v1") == doc
    with pytest.raises(rv.StrokesReviewError):
        rv.load_review(path, "other-video")
    path.write_text("{}")
    with pytest.raises(rv.StrokesReviewError):
        rv.load_review(path, "v1")


def test_entry_rules() -> None:
    link = rv.EventLink(event_id="e1", event_frame=5, algorithm_version="x")
    with pytest.raises(ValueError, match="reason"):
        rv.ReviewEntry(
            id="a", kind=rv.ReviewKind.NOT_STROKE, frame=5, event=link, updated_at=rv.now()
        )
    with pytest.raises(ValueError, match="only a detector event"):
        rv.ReviewEntry(
            id="a",
            kind=rv.ReviewKind.NOT_STROKE,
            frame=5,
            reason=rv.NotStrokeReason.DUPLICATE,
            updated_at=rv.now(),
        )
    with pytest.raises(ValueError, match="same frame"):
        rv.StrokesReview(video_id="v", entries=(_stroke(3), _stroke(3)))
    with pytest.raises(ValueError, match="overlap"):
        rv.StrokesReview(video_id="v", reviewed_stretches=(_stretch(0, 10), _stretch(10, 20)))


def test_add_stretch_merges_overlapping_and_touching() -> None:
    s = rv.add_stretch((), 100, 50)
    assert [(x.start_frame, x.end_frame) for x in s] == [(50, 100)]
    s = rv.add_stretch(s, 200, 300)
    s = rv.add_stretch(s, 101, 120)  # touches the first
    assert [(x.start_frame, x.end_frame) for x in s] == [(50, 120), (200, 300)]
    s = rv.add_stretch(s, 110, 250)
    assert [(x.start_frame, x.end_frame) for x in s] == [(50, 300)]


def test_link_and_action_follow_the_detector() -> None:
    doc = load_strokes(FIXTURE)
    events = {e.id: e for e in doc.events}
    e1 = events["e0001"]  # ref frame 581
    e2 = events["e0002"]  # undetermined wrist: window start 1160
    assert rv.event_frame(e1) == 581 and rv.event_frame(e2) == 1160

    def linked(frame: int, event_frame: int, kind: rv.ReviewKind = rv.ReviewKind.STROKE) -> str:
        entry = rv.ReviewEntry(
            id="x",
            kind=kind,
            frame=frame,
            reason=rv.NotStrokeReason.DUPLICATE if kind is rv.ReviewKind.NOT_STROKE else None,
            event=rv.EventLink(event_id="e0001", event_frame=event_frame, algorithm_version="v"),
            updated_at=rv.now(),
        )
        return rv.action(entry, rv.linked_event(entry, events))

    assert linked(581, 581) == "confirmed"
    assert linked(590, 581) == "moved"
    assert linked(581, 581, rv.ReviewKind.NOT_STROKE) == "deleted"
    assert linked(581, 584) == "confirmed"  # within the tolerance of 3 frames
    assert linked(581, 585) == "detached"  # the event moved: the correction stays apart
    assert rv.action(_stroke(7), None) == "added"


def test_export_is_accepted_by_the_label_loaders(tmp_path: Path) -> None:
    link = rv.EventLink(event_id="e9", event_frame=40, algorithm_version="v")
    doc = rv.StrokesReview(
        video_id="v",
        entries=(
            _stroke(
                20,
                stroke_type=rv.ReviewStrokeType.BACKHAND,
                visibility=ImpactVisibility.FULL,
                note='rete, "alta"',
            ),
            _stroke(30, stroke_type=rv.ReviewStrokeType.FOREHAND),  # no visibility
            _stroke(500, stroke_type=rv.ReviewStrokeType.OTHER, visibility=ImpactVisibility.NONE),
            rv.ReviewEntry(
                id="d",
                kind=rv.ReviewKind.NOT_STROKE,
                frame=40,
                reason=rv.NotStrokeReason.NOT_A_STROKE,
                event=link,
                updated_at=rv.now(),
            ),
        ),
        reviewed_stretches=(_stretch(0, 100),),
    )
    exp = rv.export_labels(doc, "clip_1")
    assert exp.exported == 1
    assert exp.left_out == ("frame 30: manca visibilità", "frame 500: fuori dai tratti rivisti")
    (tmp_path / "labels.csv").write_text(exp.labels_csv)
    labels = load_labels(tmp_path / "labels.csv")
    assert [(lb.clip_id, lb.proxy_frame, lb.stroke_type.value, lb.player) for lb in labels] == [
        ("clip_1", 20, "backhand", "A")
    ]
    assert labels[0].notes == 'rete, "alta"'
    assert exp.annotated_csv is not None
    (tmp_path / "annotated.csv").write_text(exp.annotated_csv)
    intervals = load_annotated(tmp_path / "annotated.csv")
    assert [(i.start_frame, i.end_frame) for i in intervals] == [(0, 100)]

    no_stretch = rv.StrokesReview(video_id="v", entries=doc.entries[2:3])
    exp2 = rv.export_labels(no_stretch, "clip_1")
    assert exp2.exported == 1 and exp2.annotated_csv is None
    with pytest.raises(rv.StrokesReviewError):
        rv.export_labels(doc, "con spazi")
