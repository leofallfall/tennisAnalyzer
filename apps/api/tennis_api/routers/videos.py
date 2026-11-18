from __future__ import annotations

import shutil

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse

from tennis_api.deps import DbDep, SettingsDep
from tennis_api.errors import ApiError, not_found
from tennis_api.models import Video, new_id
from tennis_api.schemas import VideoOut
from tennis_api.storage import save_upload, video_dir
from tennis_cv.errors import TennisCVError
from tennis_cv.video.metadata import probe_video
from tennis_cv.video.validation import validate_metadata

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.post("/upload", response_model=VideoOut, status_code=201)
async def upload_video(file: UploadFile, db: DbDep, settings: SettingsDep) -> Video:
    """Store a video, extract metadata with ffprobe and validate it.

    Invalid videos are rejected (and deleted) with a structured error listing the issues.
    Warnings (e.g. low frame rate) are stored with the video and shown to the user.
    """
    video_id = new_id()
    dest_dir = video_dir(settings.storage_dir, video_id)
    path, size, sha = await save_upload(file, dest_dir, settings.max_upload_bytes)
    try:
        meta = probe_video(path)
        validation = validate_metadata(meta)
    except TennisCVError:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    if not validation.is_valid:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ApiError(
            422,
            "video_invalid",
            "The video does not meet the input requirements",
            {"issues": [i.model_dump(mode="json") for i in validation.issues]},
        )
    meta_json = meta.model_dump(mode="json")
    meta_json["path"] = str(path.relative_to(settings.storage_dir))
    meta_json["orientation"] = meta.orientation
    video = Video(
        id=video_id,
        original_filename=file.filename or path.name,
        stored_path=str(path.relative_to(settings.storage_dir)),
        content_type=file.content_type,
        size_bytes=size,
        sha256=sha,
        video_metadata=meta_json,
        validation=validation.model_dump(mode="json"),
    )
    db.add(video)
    db.commit()
    return video


@router.get("/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: DbDep) -> Video:
    video = db.get(Video, video_id)
    if video is None:
        raise not_found("video", video_id)
    return video


@router.get("/{video_id}/file")
def get_video_file(video_id: str, db: DbDep, settings: SettingsDep) -> FileResponse:
    """Original uploaded file (supports HTTP range requests for seeking)."""
    video = db.get(Video, video_id)
    if video is None:
        raise not_found("video", video_id)
    path = settings.storage_dir / video.stored_path
    if not path.is_file():
        raise ApiError(410, "video_file_missing", "The stored video file is missing")
    return FileResponse(path, media_type=video.content_type or "application/octet-stream")
