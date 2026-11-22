"""Stroke detection, classification and phase segmentation.

Detection, classification and phases: INTERFACES ONLY (``base``). The ``strokes.json``
1.0 artifact format (``artifact``) is defined and validated, but no detector writes it
yet (M2.3). See docs/M2_3_EVENTS.md.
"""

from tennis_cv.strokes.artifact import (
    SCHEMA_VERSION,
    CandidateEvent,
    StrokesFile,
    WristPeak,
    check_invariants,
    dump_strokes,
    load_strokes,
    parse_strokes,
)
from tennis_cv.strokes.base import (
    PhaseName,
    PhaseSegmenter,
    StrokeCandidate,
    StrokeClassifier,
    StrokeDetector,
    StrokePhase,
    StrokeType,
)

__all__ = [
    "SCHEMA_VERSION",
    "CandidateEvent",
    "PhaseName",
    "PhaseSegmenter",
    "StrokeCandidate",
    "StrokeClassifier",
    "StrokeDetector",
    "StrokePhase",
    "StrokeType",
    "StrokesFile",
    "WristPeak",
    "check_invariants",
    "dump_strokes",
    "load_strokes",
    "parse_strokes",
]
