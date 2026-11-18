# Personal AI Analysis (Claude Code, no API)

Status: **experimental**. The plumbing is implemented and tested with synthetic fixtures.
It is not validated on real tennis footage: there are no detected strokes yet (M2).

## Goal

During development and personal testing, Claude interprets the engine's evidence through
**Claude Code on the user's own subscription**. The application never calls an LLM and
needs no API key. Swapping to the Anthropic API later must not change the data model,
the Evidence Pack, the validation, the UI or the flow.

## Architecture

```
CV pipeline (unchanged) ──► report.json · pose.json · proxy.mp4   (immutable)
                                   │ read-only
"Prepare AI analysis" ──► tennis_api.ai.builder ──► storage/ai/<analysis_id>/<pack_id>/
                                                        manifest.json (sha256 of every file)
                                                        session_metadata.json, data_quality.json
                                                        strokes.json, measurements.json
                                                        aggregates.json, issues_candidates.json
                                                        stroke_timeseries.json (single stroke)
                                                        evidence/*.jpg (≤ 768 px, skeleton drawn)
                                                        claude_instructions.md, result_schema.json
User runs `claude` → /analizza-sessione <pack>   ──►    analysis_result.json  (only file written)
"Import AI analysis" ──► AIClient.collect_result ──► validation ──► ai_analyses table ──► UI
```

| Piece | Where |
|---|---|
| Result contract (Pydantic) | `apps/api/tennis_api/ai/result.py` (`AnalysisResult`) |
| Evidence Pack model | `apps/api/tennis_api/ai/pack.py` (reuses `Measurement`, `StrokePhase`, `QualityReport`, `PoseSummary`) |
| Pack builder | `apps/api/tennis_api/ai/builder.py` |
| Instructions in each pack | `apps/api/tennis_api/ai/instructions.py` |
| Validation | `apps/api/tennis_api/ai/validation.py` |
| `AIClient`, `ClaudeCodeAdapter`, `AnthropicAPIAdapter` (stub) | `apps/api/tennis_api/ai/client.py` |
| API endpoints | `apps/api/tennis_api/routers/ai.py` |
| Claude Code skill | `.claude/skills/analizza-sessione/SKILL.md` |
| CLI check | `scripts/ai_pack.py validate <pack>` / `list` |
| UI | `apps/web/src/components/analysis/AiAnalysisPanel.tsx` (on the analysis page) |

The AI layer lives in the API package, not in the engine: the engine stays an LLM-free CV
library (ADR-002, ADR-013).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/analyses/{id}/ai` | AI analyses of an analysis (newest first) |
| POST | `/api/analyses/{id}/ai/session` | Build a session Evidence Pack |
| POST | `/api/analyses/{id}/ai/single-stroke` | Build a single-stroke pack. Body: `stroke_id` (from a session pack) **or** `start_s`/`end_s` (user interval ≤ 6 s), optional `declared_stroke_type`, optional `source_ai_analysis_id` + `issue_id` (the issue to deep-dive). A single-stroke upload with no body uses the whole clip. |
| GET | `/api/ai-analyses/{ai_id}` | One AI analysis with handoff instructions and evidence images |
| POST | `/api/ai-analyses/{ai_id}/import` | Validate `analysis_result.json` and store it. Returns 422 `ai_result_invalid` with every problem; nothing is imported. |
| GET | `/api/ai-analyses/{ai_id}/images/{image_id}` | Evidence JPEG |

## Honesty rules enforced by validation

1. **Pack integrity.** Every pack file must match its SHA-256 in `manifest.json`: a
   modified or missing file blocks the import. Extra files only produce a warning.
2. **JSON, then schema.**
   - Strict Pydantic validation: unknown fields are rejected.
   - Percentages must equal occurrences divided by evaluable strokes.
   - There must be exactly one stroke id per occurrence, and priorities must be unique.
   - A technical error with confidence below 0.8 needs alternative explanations.
   - Single-stroke results need a target.
3. **Cross-checks with the pack.**
   - IDs and identity: session, pack, kind and version ids must match the pack.
   - Stroke references: cited stroke ids, image ids, measurement ids and metric ids must exist. The stroke type must match the cited strokes.
   - Counts: the denominator can't exceed the number of strokes of that type, and timestamps must fall inside the cited strokes.
   - **No strokes in the pack means no issues and no positive executions**, and `insufficient_evidence` must be true.
   - **Angles written in text must be pack values** at the cited precision.
     - This guards against invented angles.
     - With dense time series, an invented value can still coincide with a real one, so it is a guard, not a proof.
4. **Immutability.** CV results and `Analysis.results` are never written by this flow (tested).
   A later invalid file does not overwrite an imported result.

## Using it with a real session (Claude Code)

1. **Start the app:** `./scripts/dev.sh`. Upload a video, create the analysis and press **Start processing**.
2. **Prepare the evidence:**
   - When processing completes, press **Prepare AI analysis** on the analysis page.
   - The card shows the command, for example `/analizza-sessione storage/ai/<analysis>/<pack>`.
3. **Run Claude Code:**
   - In a terminal at the repository root, run `claude`.
   - Paste the command.
   - Claude reads the pack, writes `analysis_result.json` and runs `uv run python scripts/ai_pack.py validate <pack>` until it prints `OK`.
   - Allow only that write when asked.
4. **Import:** back in the app, press **Import AI analysis**. Invalid output shows each problem. Fix the file, or ask Claude Code to fix it, and import again.
5. **Single stroke:**
   - **From a session result:** pick an issue and one of its strokes, then press **Deep-dive this stroke**.
   - **Without detected strokes (today):** pause the player on a stroke, choose the stroke type you see, and press **Prepare single-stroke analysis at <time> (±1 s)**.
   - Then repeat steps 3–4.

The packs live under `storage/` (git-ignored, local only). No video, full pose data, credential or absolute path is put in a pack.

## Switching to the Anthropic API later

Implement `AnthropicAPIAdapter.request_analysis` and select it with `TPA_AI_ADAPTER=anthropic_api`. The adapter would:

- send `claude_instructions.md` as the system prompt;
- send the JSON files and evidence images as content;
- request output conforming to `result_schema.json`;
- write `analysis_result.json` into the pack.

`collect_result`, the validation, the database row and the UI stay the same. Model choice and costs are in `docs/audit/D_COST_MODEL.md` and `E_OPTIMIZED_PIPELINE.md`.

## Current limitations

- **The engine provides no strokes, phases or stroke metrics yet (M2/M3).** As a result:
  - Real session packs declare these `not_implemented`, and validation only accepts "insufficient evidence" results.
  - Single-stroke packs from a user interval carry per-frame 2D elbow and knee angles, with no phases and no hitting-arm identification.
- **Measured issue lists have only been exercised with synthetic fixtures.**
- **`pose.json` is loaded whole to build a pack.** That is fine for short clips; for long sessions see audit step 1.3.
- **The model name in the result is self-reported** by Claude Code.
- **Claude Code's permission prompts are the user's control over writes.** The skill restricts writes to `analysis_result.json`, but the app cannot technically prevent other writes in an interactive Claude Code session. Integrity hashes detect any change to the pack itself.
- **Dates are shown 1 h early in Italy** (the open M1 bug, step 0.1 of the audit roadmap).
