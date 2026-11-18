"""Download MediaPipe Pose Landmarker model files into ./models (or $TPA_MODELS_DIR).

Usage: uv run python scripts/download_models.py [--variant lite|full|heavy] [--all]
Models are published by Google under the Apache 2.0 licence; they are not committed.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request

from tennis_cv.pose.mediapipe_estimator import DEFAULT_VARIANT, MODEL_URLS, models_dir


def download(variant: str, force: bool = False) -> None:
    dest = models_dir() / f"pose_landmarker_{variant}.task"
    if dest.is_file() and not force:
        print(f"[skip] {dest} already exists")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = MODEL_URLS[variant]
    print(f"[get ] {url}")
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    print(f"[ok  ] {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--variant", choices=sorted(MODEL_URLS), default=DEFAULT_VARIANT)
    p.add_argument("--all", action="store_true", help="Download lite, full and heavy")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    for v in sorted(MODEL_URLS) if args.all else [args.variant]:
        try:
            download(v, args.force)
        except OSError as exc:
            print(f"[fail] {v}: {exc}", file=sys.stderr)
            print(f"       Download manually: {MODEL_URLS[v]}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
