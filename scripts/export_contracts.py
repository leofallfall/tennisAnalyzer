"""Export JSON Schemas of the engine's structured contracts to packages/contracts/schema.

Python (Pydantic) is the source of truth; the frontend's TypeScript types in
packages/contracts/src mirror these schemas. tests/unit/test_contracts.py fails
when the committed schemas are stale.

Usage: uv run python scripts/export_contracts.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from tennis_api.ai.pack import Manifest
from tennis_api.ai.result import AnalysisResult
from tennis_cv.biomechanics import Interpretation, Measurement, Observation, Recommendation
from tennis_cv.evaluation.eval_artifact import StrokesEvalFile
from tennis_cv.pipeline import ProcessingReport
from tennis_cv.pose.models import PoseSequence
from tennis_cv.strokes.artifact import StrokesFile
from tennis_cv.strokes.review import StrokesReview
from tennis_cv.video.timemap import TimeMap

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "packages" / "contracts" / "schema"
CONTRACTS: dict[str, type[BaseModel]] = {
    "processing_report": ProcessingReport,
    "pose_sequence": PoseSequence,
    "time_map": TimeMap,
    "strokes_v1": StrokesFile,
    "strokes_eval_v1": StrokesEvalFile,
    "strokes_review_v1": StrokesReview,
    "measurement": Measurement,
    "observation": Observation,
    "interpretation": Interpretation,
    "recommendation": Recommendation,
    "ai_analysis_result": AnalysisResult,
    "ai_evidence_manifest": Manifest,
}


def render(model: type[BaseModel]) -> str:
    return (
        json.dumps(model.model_json_schema(mode="serialization"), indent=2, sort_keys=True) + "\n"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="Exit 1 if schemas are out of date")
    args = p.parse_args()
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, model in CONTRACTS.items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        content = render(model)
        if args.check:
            if not path.is_file() or path.read_text() != content:
                stale.append(path.name)
        else:
            path.write_text(content)
            print(f"wrote {path}")
    if stale:
        print("Stale contract schemas: " + ", ".join(stale), file=sys.stderr)
        print("Run: uv run python scripts/export_contracts.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
