"""Evidence Pack helper for the personal AI analysis flow (Claude Code).

Read-only: it never imports into the database and never modifies the pack.

Usage:
    uv run python scripts/ai_pack.py validate PACK_DIR   # check analysis_result.json
    uv run python scripts/ai_pack.py list [ANALYSIS_ID]  # list packs under storage/ai
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tennis_api.ai.pack import MANIFEST, Manifest
from tennis_api.ai.result import RESULT_FILENAME
from tennis_api.ai.validation import validate_result_file
from tennis_api.config import get_settings


def cmd_validate(pack_dir: Path) -> int:
    if not (pack_dir / MANIFEST).is_file():
        print(f"Not an Evidence Pack (no {MANIFEST}): {pack_dir}", file=sys.stderr)
        return 2
    outcome = validate_result_file(pack_dir)
    for w in outcome.warnings:
        print(f"WARNING [{w.code}] {w.location or ''}: {w.message}")
    if outcome.ok:
        assert outcome.result is not None
        r = outcome.result
        print(
            f"OK: {RESULT_FILENAME} is valid ({r.kind.value}, {len(r.issues)} issues, "
            f"{len(r.positive_executions)} positive executions). "
            'Import it in the app with "Import AI analysis".'
        )
        return 0
    print(f"INVALID: {len(outcome.errors)} problem(s)")
    for e in outcome.errors:
        where = f" at {e.location}" if e.location else ""
        print(f"- [{e.code}]{where}: {e.message}")
    return 1


def cmd_list(analysis_id: str | None) -> int:
    root = get_settings().ai_dir
    dirs = sorted(root.glob(f"{analysis_id or '*'}/*/{MANIFEST}")) if root.is_dir() else []
    if not dirs:
        print(f"No Evidence Packs under {root}")
        return 0
    for path in dirs:
        m = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        has_result = (path.parent / RESULT_FILENAME).is_file()
        print(
            f"{m.created_at:%Y-%m-%d %H:%M}  {m.kind.value:<13} {path.parent}  "
            f"result={'yes' if has_result else 'no'}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="Validate analysis_result.json against its pack")
    v.add_argument("pack_dir", type=Path)
    ls = sub.add_parser("list", help="List Evidence Packs")
    ls.add_argument("analysis_id", nargs="?")
    args = parser.parse_args()
    if args.cmd == "validate":
        return cmd_validate(args.pack_dir)
    return cmd_list(args.analysis_id)


if __name__ == "__main__":
    raise SystemExit(main())
