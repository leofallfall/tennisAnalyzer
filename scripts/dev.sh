#!/usr/bin/env bash
# Start API (port 8000), worker and web dev server (port 5173). Ctrl+C stops all.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export GLOG_minloglevel="${GLOG_minloglevel:-2}"
trap 'kill 0' EXIT INT TERM
uv run tpa-api &
uv run tpa-worker &
pnpm --filter @tpa/web dev &
wait
