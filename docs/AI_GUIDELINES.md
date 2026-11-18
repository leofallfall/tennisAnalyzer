# AI Guidelines

Applies to every LLM-based component (AI Coach, issue explanations, recommendations).
The LLM **interprets**; it never **measures**.

## 1. Evidence-based interpretation

- Input to the LLM is structured data produced by the CV engine (see §7), not raw video.
- Every statement must reference observation/measurement ids or frames.
- Output must be structured as separate `observations` (restated, never altered),
  `interpretations` (with `based_on_observation_ids`) and `recommendations`
  (with `addresses_interpretation_ids`) — the `tennis_cv.biomechanics.layers` contracts.
  Free text without references is rejected by validation.

## 2. No hallucinated measurements

- The LLM must not output any number that is not present in its input.
- Post-generation check: every numeric token in the response is matched against input
  values; unmatched numbers fail validation and the response is regenerated or dropped.
- If a needed metric is missing or `not_observable`, the answer says so:
  "Unable to evaluate X reliably from the available video because …".

## 3. Confidence handling

- Interpretation confidence ≤ the lowest confidence of its supporting measurements.
- Use calibrated language bands: high (≥ 0.8) "clearly", medium (0.5–0.8) "likely",
  low (< 0.5) "possibly / cannot confirm". Never upgrade certainty in prose.
- Always include alternative explanations for low/medium-confidence interpretations
  (including recording conditions).

## 4. Observability

Non-observable metrics are passed to the LLM explicitly (with reason) so it can mention
limitations instead of ignoring them. Frequencies use observable denominators only.

## 5. Issue prioritisation

Rank by: (1) technical significance for stroke outcome, (2) frequency among evaluable
strokes, (3) confidence, (4) whether it is a root cause (e.g. late preparation) rather than
a downstream symptom (e.g. late contact). Prefer few well-supported issues.

## 6. Style vs technical error

Classify each interpretation as `technical_error`, `stylistic_variation` or `uncertain`.
Grip, backswing shape, open/closed stance choice and one- vs two-handed backhand are
stylistic unless they demonstrably cause a measurable problem. Professional players show
a wide range of styles; deviation from one pro is not an error.

## 7. Structured input format (LLM context)

```json
{
  "player": {"dominant_hand": "right", "backhand": "two_handed", "level": "advanced"},
  "analysis": {"mode": "session", "camera_views": ["behind"], "fps": 60},
  "strokes": {"forehand": {"candidates": 40, "observable": 35}},
  "issues": [{"id": "iss_1", "metric_id": "unit_turn_timing", "occurrences": 19,
              "evaluable": 35, "confidence": 0.7, "evidence": ["stk_4", "stk_9"]}],
  "measurements": [{"id": "m_1", "metric_id": "contact_distance_body", "value": null,
                    "observability": "not_observable",
                    "limitations": ["Side view: lateral distance not visible"]}],
  "history": {"previous_sessions": 3, "open_paths": ["Improve forehand preparation"]},
  "coach_notes": [{"text": "Prepare earlier on the forehand", "author": "external_coach"}]
}
```

Coach notes are labelled as hypotheses to be checked against evidence.

## 8. Actionable recommendations

Each recommendation names: the target issue, a specific drill (setup, cue, repetitions),
what to film next time to verify change (camera view, fps), and how success will be
measured (which metric moving in which direction). No medical advice, injury prediction,
or general fitness programmes.

## 9. Guardrails

- Tennis technique only; decline tactical match analysis and other sports.
- No scores, ratings or gamified language.
- Do not mention professional players as "correct" — use "reference".
- Model and prompt versions are stored with every generated output for reproducibility.
