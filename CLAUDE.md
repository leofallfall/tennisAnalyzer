# CLAUDE.md — permanent project instructions

Tennis Performance Analyzer: a tennis biomechanics and stroke-analysis platform for
intermediate → competitive players. Accuracy and honesty about uncertainty matter more
than the amount of feedback.

## Before coding

1. Read the docs relevant to the task (`docs/`): ARCHITECTURE, DATA_MODEL,
   VIDEO_PIPELINE, BIOMECHANICS, AI_GUIDELINES, ROADMAP, DECISIONS.
2. Check `docs/ROADMAP.md` for the current milestone and its acceptance criteria.
   Do not start a later milestone's work unless asked.
3. Follow the established architecture (engine = pure library; API = HTTP + persistence;
   worker = long-running jobs; web = UI only).

## Working rules

- Work in small, reviewable increments. Avoid broad refactoring without approval.
- Do not silently change product requirements; propose changes and update
  `docs/PRODUCT_REQUIREMENTS.md` only when agreed.
- Write tests for important functionality (pytest for Python, Vitest for web).
- Update documentation when architecture, data model or pipeline behaviour changes;
  record significant decisions in `docs/DECISIONS.md` (with alternatives).
- Never claim functionality works without having run it/tested it. Report what was run.
- Clearly mark incomplete implementations (interfaces raising `NotYetImplementedError`,
  `capabilities.py` status, UI `PlaceholderNotice`).
- Do not add dependencies without a concrete need; explain the trade-off when you do.
- Keep code modular, typed and readable. Explain important trade-offs in PR/commit text.
- Never push to remotes, create external services or paid accounts unless asked.

## Domain rules (non-negotiable)

- Never invent biomechanical measurements or fake CV output. No mock data presented as
  analysis. Example data in the UI lives only in `apps/web/src/lib/exampleData.ts` and is
  always rendered with `<PlaceholderNotice />`.
- Every measurement uses `tennis_cv.biomechanics.Measurement` (value, unit, time/frame,
  stroke, body part, confidence, observability, method, limitations). A non-observable
  metric has `value = None` — this is a valid result.
- Keep OBSERVATION, INTERPRETATION and RECOMMENDATION separate.
- Statistical denominators = strokes where the metric is observable.
- Distinguish technical errors from stylistic variation.
- Assess comparability (camera view, distance, stroke type) before comparing executions.
- No tactical/match analysis, other sports, medical advice, injury prediction or fitness plans.
- Professional footage: only licensed content; never assume download/redistribution rights.

## Commands

```bash
uv sync && pnpm install                       # install
uv run python scripts/download_models.py      # MediaPipe model → models/
./scripts/dev.sh                              # API :8000 + worker + web :5173
./scripts/check.sh                            # all lint/format/type/test checks
uv run pytest -m "not slow"                   # fast Python tests
uv run python scripts/pose_experiment.py VIDEO  # CV experiment from the CLI
uv run python scripts/export_contracts.py     # after changing engine contracts
uv run python scripts/ai_pack.py validate PACK  # check an AI result before importing
```

Personal AI analysis: `/analizza-sessione <pack>` (skill in `.claude/skills/`) interprets an
Evidence Pack and writes only `analysis_result.json`. See `docs/AI_ANALYSIS.md`.

## Code conventions

- Python 3.12, Ruff (lint+format, line 100), MyPy strict on all packages, Pydantic v2.
- Engine errors subclass `TennisCVError` with a stable `code`; the API turns them into
  `{"error": {code, message, details}}`.
- TypeScript strict, ESLint, Prettier (no semicolons, single quotes, width 100).
- UI components follow shadcn/ui conventions in `apps/web/src/components/ui`; design
  tokens in `apps/web/src/index.css`.
- Mirror changes to engine/API models in `packages/contracts/src/index.ts`.
- Timestamps refer to the CFR proxy timeline (`frame / fps`).
