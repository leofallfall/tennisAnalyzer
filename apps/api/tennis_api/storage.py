"""Local filesystem storage. Paths in the DB are relative so storage can move to S3 later."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from fastapi import UploadFile

from tennis_api.errors import ApiError

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
ARTIFACT_NAMES = {
    "proxy": "proxy.mp4",
    "overlay": "overlay.mp4",
    "pose": "pose.json",
    "report": "report.json",
    "timemap": "timemap.json",
    "strokes": "strokes.json",
}
CHUNK = 1024 * 1024


def video_dir(storage_dir: Path, video_id: str) -> Path:
    return storage_dir / "videos" / video_id


def analysis_video_dir(storage_dir: Path, analysis_id: str, video_id: str) -> Path:
    return storage_dir / "analyses" / analysis_id / "videos" / video_id


async def save_upload(upload: UploadFile, dest_dir: Path, max_bytes: int) -> tuple[Path, int, str]:
    """Stream an upload to disk, enforcing extension and size. Returns (path, size, sha256)."""
    name = upload.filename or ""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError(
            415,
            "unsupported_file_type",
            f"Unsupported file extension '{ext or '(none)'}'",
            {"allowed": sorted(ALLOWED_EXTENSIONS)},
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"original{ext}"
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(
                        413,
                        "file_too_large",
                        "Upload exceeds the maximum allowed size",
                        {"max_bytes": max_bytes},
                    )
                digest.update(chunk)
                fh.write(chunk)
    except BaseException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    if size == 0:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ApiError(400, "empty_file", "Uploaded file is empty")
    return dest, size, digest.hexdigest()
