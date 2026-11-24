"""V1 states and reasons in words (read-only view of strokes.json)."""

from __future__ import annotations

from pathlib import Path

from tennis_cv.strokes.artifact import (
    Coverage,
    CoverageStretch,
    EventStatus,
    RegionName,
    RegionReason,
    RuleKind,
    load_strokes,
)
from tennis_cv.strokes.detector import RULES
from tennis_cv.strokes.states import (
    NOT_ASSESSABLE_NOTE,
    REASON_TEXT,
    STATE_LABEL,
    V1State,
    v1_views,
)

EXAMPLE = Path(__file__).parents[1] / "fixtures" / "strokes" / "example_v1_0.json"


def test_every_event_reason_of_the_detector_has_words() -> None:
    kinds = (RuleKind.REJECT, RuleKind.UNCERTAIN)
    codes = {r.code for r in RULES if r.kind in kinds or r.code.startswith("info_")}
    assert codes <= set(REASON_TEXT)
    assert all(t and "%" not in t for t in REASON_TEXT.values())  # words, no percentages


def test_status_maps_to_the_three_event_states() -> None:
    doc = load_strokes(EXAMPLE)
    events, _ = v1_views(doc)
    expected = {
        EventStatus.ACCEPTED: V1State.FLAGGED,
        EventStatus.UNCERTAIN: V1State.TO_CHECK,
        EventStatus.REJECTED: V1State.DISCARDED,
    }
    assert [v.state for v in events] == [expected[e.status] for e in doc.events]
    assert set(STATE_LABEL) == set(V1State)


def test_not_assessable_stretch_says_insufficient_evidence() -> None:
    doc = load_strokes(EXAMPLE).model_copy(
        update={
            "schema_version": "1.1",
            "coverage": Coverage(
                min_duration_s=0.3,
                not_assessable=[
                    CoverageStretch(
                        start_proxy_frame=0, end_proxy_frame=29, start_t_s=0.0, end_t_s=0.9667,
                        regions=[RegionName.LEFT_WRIST, RegionName.RIGHT_WRIST],
                        reasons=[RegionReason.NO_POSE],
                    )
                ],
            ),
        }
    )  # fmt: skip
    _, stretches = v1_views(doc)
    (s,) = stretches
    assert s.state is V1State.NOT_ASSESSABLE
    assert "giocatore non rilevato" in s.reason and NOT_ASSESSABLE_NOTE in s.reason
    assert s.reason.startswith("Polso sinistro, polso destro")


def test_region_views_follow_the_file_in_fixed_order() -> None:
    from tennis_cv.strokes.artifact import RegionState
    from tennis_cv.strokes.states import REGION_STATE_LABEL

    doc = load_strokes(EXAMPLE)
    events, _ = v1_views(doc)
    assert set(REGION_STATE_LABEL) == set(RegionState)
    for e, view in zip(doc.events, events, strict=True):
        assert [r.region for r in view.regions] == list(RegionName)
        for r in view.regions:
            assert r.state is e.regions.get(r.region).state
            assert r.used == (r.region in e.reliability.regions_used)
            assert len(r.reasons) == len(e.regions.get(r.region).reasons)
            assert r.state_label == REGION_STATE_LABEL[r.state]
