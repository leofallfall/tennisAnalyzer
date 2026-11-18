"""Structural rules of the AI result contract (no Evidence Pack needed)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tennis_api.ai.result import AnalysisResult


def base(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "session",
        "session_id": "a1",
        "pack_id": "p1",
        "analysis_version": "ai-instructions/1.0",
        "adapter": "claude_code",
        "model": "m",
        "generated_at": "2026-11-18T22:00:00Z",
        "summary": "s",
        "evaluated_strokes": {
            "detected": 4,
            "evaluable": 4,
            "forehand": 4,
            "backhand": 0,
            "unknown": 0,
        },
        "issues": [issue()],
        "confidence": 0.6,
        "insufficient_evidence": False,
    }
    data.update(over)
    return data


def issue(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "iss_1",
        "priority": 1,
        "name": "n",
        "description": "d",
        "stroke_type": "forehand",
        "nature": "technical_error",
        "occurrences": 2,
        "evaluable_strokes": 4,
        "percentage": 50,
        "relevance": "high",
        "confidence": 0.9,
        "stroke_ids": ["s1", "s2"],
        "observations": ["o"],
        "evidence": [{"kind": "stroke", "ref": "s1"}],
        "biomechanical_explanation": "e",
    }
    data.update(over)
    return data


def test_valid_result_and_no_artificial_issue_limit() -> None:
    issues = [
        issue(id=f"i{n}", priority=n, occurrences=1, percentage=25, stroke_ids=[f"s{n}"])
        for n in range(1, 16)
    ]
    assert len(AnalysisResult.model_validate(base(issues=issues)).issues) == 15


@pytest.mark.parametrize(
    "bad",
    [
        {"percentage": 40},
        {"occurrences": 5, "stroke_ids": ["a", "b", "c", "d", "e"]},
        {"stroke_ids": ["s1"]},
        {"stroke_ids": ["s1", "s1"]},
        {"confidence": 0.5},  # technical error below 0.8 without alternatives
        {"confidence": 1.5},
        {"evidence": []},
        {"observations": []},
    ],
)
def test_issue_rules(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(base(issues=[issue(**bad)]))


def test_result_level_rules() -> None:
    with pytest.raises(ValidationError, match="priorities"):
        AnalysisResult.model_validate(base(issues=[issue(), issue(id="iss_2")]))
    rec = {"priority": 1, "title": "t", "rationale": "r", "addresses_issue_ids": ["nope"]}
    with pytest.raises(ValidationError, match="unknown issues"):
        AnalysisResult.model_validate(base(recommendations=[rec]))
    with pytest.raises(ValidationError, match="limitations"):
        AnalysisResult.model_validate(base(insufficient_evidence=True))
    with pytest.raises(ValidationError, match="target"):
        AnalysisResult.model_validate(base(kind="single_stroke"))
    with pytest.raises(ValidationError, match="must equal detected"):
        AnalysisResult.model_validate(
            base(
                evaluated_strokes={
                    "detected": 4,
                    "evaluable": 1,
                    "forehand": 1,
                    "backhand": 0,
                    "unknown": 0,
                }
            )
        )
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(base(extra_field=1))
