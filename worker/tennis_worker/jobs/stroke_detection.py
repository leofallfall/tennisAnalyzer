"""Personal V1 step 3: run the experimental M2.3 stroke detector next to ``pose.json``.

The detector (``tennis_cv.strokes.detector``) and its parameters are used unchanged.
A failure here never invalidates the pose results: it is recorded for the strokes only.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tennis_api.config import Settings
from tennis_api.models import Analysis, new_id
from tennis_api.redaction import redact
from tennis_api.storage import analysis_video_dir
from tennis_cv.errors import TennisCVError
from tennis_cv.strokes.artifact import dump_strokes
from tennis_cv.strokes.detector import ALGORITHM_VERSION, POSE_FILE, detect_dir

log = logging.getLogger(__name__)

STROKES_FILE = "strokes.json"
ProgressReporter = Callable[[str, float], None]


def detect_strokes_in_dir(out_dir: Path, settings: Settings) -> dict[str, Any]:
    """Write ``out_dir/strokes.json`` from ``out_dir/pose.json``; return a small summary.

    ``status`` is ``completed``, ``failed`` (error code and message, paths redacted) or
    ``not_available`` (no pose). Counts are numbers of events/stretches, never rates.
    """
    if not (out_dir / POSE_FILE).is_file():
        return {"status": "not_available", "reason": "pose.json missing"}
    try:
        doc = detect_dir(out_dir)
        text = dump_strokes(doc)
    except TennisCVError as exc:
        log.warning("Stroke detection failed in %s: %s", out_dir.name, exc.code)
        message, details = redact((exc.message, exc.details), settings)
        return {"status": "failed", "error": {"code": exc.code, "message": message, **details}}
    except Exception as exc:  # the pose results stay valid; the traceback stays in the log
        error_id = new_id()[:12]
        log.exception("Stroke detection crashed in %s (error_id=%s)", out_dir.name, error_id)
        return {
            "status": "failed",
            "error": {
                "code": "internal_error",
                "message": redact(f"{type(exc).__name__}: {exc}", settings),
                "error_id": error_id,
            },
        }
    tmp = out_dir / (STROKES_FILE + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out_dir / STROKES_FILE)
    counts = Counter(e.status.value for e in doc.events)
    return {
        "status": "completed",
        "algorithm_version": ALGORITHM_VERSION,
        "experimental": True,
        "events": {s: counts.get(s, 0) for s in ("accepted", "uncertain", "rejected")},
        "not_assessable_stretches": len(doc.coverage.not_assessable) if doc.coverage else 0,
    }


def run_stroke_detection_job(
    analysis: Analysis, settings: Settings, report_progress: ProgressReporter
) -> dict[str, Any]:
    """Strokes for an analysis that already has pose results, without re-estimating pose.

    Returns the analysis ``results`` unchanged except for the ``strokes`` entry.
    """
    results: dict[str, Any] = dict(analysis.results or {})
    strokes: dict[str, Any] = dict(results.get("strokes") or {})
    links = list(analysis.video_links)
    for n, link in enumerate(links):
        report_progress("stroke detection", n / max(1, len(links)))
        out_dir = analysis_video_dir(settings.storage_dir, analysis.id, link.video_id)
        strokes[link.video_id] = detect_strokes_in_dir(out_dir, settings)
    results["strokes"] = strokes
    return results
