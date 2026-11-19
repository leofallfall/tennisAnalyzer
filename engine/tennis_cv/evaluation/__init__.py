"""Offline evaluation tooling for stroke recognition (M2.1).

Reads a local dataset of real clips (``MANIFEST.md`` + ``labels.csv``, git-ignored under
``tests/fixtures/real/``) and the engine artifacts the pipeline already writes
(``pose.json``, ``timemap.json``, ``report.json``).

* :mod:`~tennis_cv.evaluation.matching` compares manual labels with detected events
  (a ``strokes.json`` that may be written by hand: no detector exists yet).
* :mod:`~tennis_cv.evaluation.visibility` measures the *input* quality: what MediaPipe
  reports around each labelled impact. It does not measure any detector.

Nothing here runs in the production pipeline or the worker. See docs/M2_1_DATASET.md.
"""

from tennis_cv.evaluation.dataset import (
    DatasetFormatError,
    Label,
    ManifestClip,
    load_clip_data,
    load_labels,
    load_manifest,
)

__all__ = [
    "DatasetFormatError",
    "Label",
    "ManifestClip",
    "load_clip_data",
    "load_labels",
    "load_manifest",
]
