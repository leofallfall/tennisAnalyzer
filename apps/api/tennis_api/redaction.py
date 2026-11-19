"""Remove server filesystem paths from data sent to API clients (M1.10).

The engine already names files by base name in new errors and reports. This module is
the safety net for everything else: rows and ``report.json`` files written before the
fix, exception messages from third-party code (e.g. ``FileNotFoundError``) and stderr.
Known server directories are replaced by short placeholders such as ``<storage>``.

Limit: only the absolute paths of these known roots (storage, models, application,
home) are replaced. A path outside them that appears in a third-party message, e.g.
``/var/tmp/x/original.mp4``, is returned unchanged. Engine errors no longer contain such
paths, but this module cannot recognise arbitrary paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

from tennis_api.config import Settings
from tennis_cv.pose.mediapipe_estimator import models_dir

T = TypeVar("T")

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _roots(settings: Settings) -> list[tuple[str, str]]:
    pairs: list[tuple[Path, str]] = [
        (settings.storage_dir, "<storage>"),
        (models_dir(), "<models>"),
        (_REPO_ROOT, "<app>"),
        (Path.home(), "<home>"),
    ]
    out: dict[str, str] = {}
    for path, label in pairs:
        for variant in {str(path), str(path.resolve()), os.path.abspath(path)}:
            # Only absolute paths: a relative TPA_STORAGE_DIR such as "storage" would
            # otherwise replace the plain word in messages. Never a bare "/".
            if os.path.isabs(variant) and len(variant) > 1:
                out.setdefault(variant, label)
    # Longest first, so <storage> wins over <app> or <home> when nested.
    return sorted(out.items(), key=lambda kv: len(kv[0]), reverse=True)


def _redact_str(value: str, roots: list[tuple[str, str]]) -> str:
    for path, label in roots:
        if path in value:
            value = value.replace(path, label)
    return value


def _walk(value: object, roots: list[tuple[str, str]]) -> object:
    if isinstance(value, str):
        return _redact_str(value, roots)
    if isinstance(value, dict):
        return {k: _walk(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, roots) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, roots) for v in value)
    return value


def redact(value: T, settings: Settings) -> T:
    """Return a copy of ``value`` (str / dict / list / tuple) with server paths replaced."""
    return _walk(value, _roots(settings))  # type: ignore[return-value]
