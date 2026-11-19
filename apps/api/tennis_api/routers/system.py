from __future__ import annotations

import os
import platform
import sys

from fastapi import APIRouter

from tennis_api.deps import SettingsDep
from tennis_api.schemas import HealthResponse, SystemInfo, ToolStatus
from tennis_cv import __version__ as engine_version
from tennis_cv.capabilities import CAPABILITIES
from tennis_cv.pose.mediapipe_estimator import default_model_path
from tennis_cv.video.ffmpeg import tool_version

API_VERSION = "0.1.0"
router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=API_VERSION)


@router.get("/system/info", response_model=SystemInfo)
def system_info(settings: SettingsDep) -> SystemInfo:
    tools = []
    for name in ("ffmpeg", "ffprobe"):
        version = tool_version(name)
        tools.append(ToolStatus(name=name, available=version is not None, version=version))
    try:
        import mediapipe

        mp_version: str | None = mediapipe.__version__
    except Exception:  # pragma: no cover - depends on environment
        mp_version = None
    tools.append(ToolStatus(name="mediapipe", available=mp_version is not None, version=mp_version))
    model = default_model_path()
    db_url = settings.resolved_database_url
    return SystemInfo(
        api_version=API_VERSION,
        engine_version=engine_version,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        database=db_url.split(":", 1)[0],
        storage_writable=os.access(settings.storage_dir, os.W_OK),
        tools=tools,
        pose_model=ToolStatus(name=model.name, available=model.is_file(), version=None),
        capabilities=CAPABILITIES,
    )
