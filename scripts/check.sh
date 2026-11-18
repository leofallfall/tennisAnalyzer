#!/usr/bin/env bash
# Run every lint, type-check and test command. Exits non-zero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."
export GLOG_minloglevel="${GLOG_minloglevel:-2}"
echo "== ruff";        uv run ruff check .
echo "== ruff format"; uv run ruff format --check .
echo "== mypy";        uv run mypy
echo "== contracts";   uv run python scripts/export_contracts.py --check
echo "== pytest";      uv run pytest
echo "== web lint";    pnpm -r lint
echo "== web format";  pnpm format:check
echo "== web types";   pnpm -r typecheck
echo "== web tests";   pnpm -r test
echo "All checks passed."
