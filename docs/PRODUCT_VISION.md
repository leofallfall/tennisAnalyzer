# Product Vision

## Objective

Tennis Performance Analyzer is a **personal tennis technique laboratory**. It analyses
tennis strokes from ordinary video recordings, identifies technical weaknesses,
explains the biomechanical reasons behind them and suggests specific exercises to
improve execution.

It is not a video player with generic AI comments. Its output is **structured,
measurable and explainable** technical information, every piece of which can be traced
back to measurements and video frames.

## Target audience

| Included                                   | Not targeted initially          |
| ------------------------------------------ | ------------------------------- |
| Intermediate players                       | Absolute beginners              |
| Advanced players                           | Coaches managing many athletes  |
| Competitive and semi-competitive players   | Other sports                    |

These players already have functional strokes; their questions are specific
("why does my forehand land short under pressure?", "is my contact point too close on
the backhand?") and need specific, evidence-based answers.

## Core value proposition

1. **Session-level truth**: every observable stroke in a practice session contributes to
   statistics, so recurring issues are distinguished from one-off bad swings.
2. **Deep single-stroke analysis**: frame-by-frame phase decomposition, skeleton,
   trajectories and measurements for the strokes that matter.
3. **Honest uncertainty**: the system says what it could not measure and why.
4. **Actionable improvement**: drills and improvement paths tied to evidence, with
   progress measured over time on comparable footage.
5. **Meaningful references**: comparison with past personal executions and with
   professional players when the executions are genuinely comparable.

## Product principles

1. Accuracy over quantity of feedback. One reliable insight beats ten speculative ones.
2. Never invent biomechanical measurements.
3. Never present uncertain observations as facts; always communicate confidence and
   observability.
4. Every important conclusion is supported by measurable evidence or identifiable frames.
5. "Unable to evaluate this variable reliably from the available video" is a valid
   result, not a failure.
6. Distinguish objective technical problems from individual stylistic preferences.
7. Keep observation, interpretation and recommendation as separate layers.
8. No arbitrary scores, no gamification.
9. A professional is a technical reference, not a model to imitate blindly.
10. The user stays in control: AI recommends, the user chooses.

## Scope

**In scope**: forehand and backhand (first), serve (immediately after MVP); session and
single-stroke analysis; 2D biomechanics from smartphone video, designed for later
multi-view/3D; professional comparison; AI Coach; longitudinal progress.

**Out of scope**: tactical/match analysis and statistics, other sports, medical analysis,
injury prediction, general fitness programming, multi-athlete coach management,
commercial subscription system, 3D reconstruction unless justified by an experiment.
