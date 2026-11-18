"""Thin, explicit wrappers around the ffmpeg / ffprobe executables."""

from __future__ import annotations

import os
import shutil
import subprocess

from tennis_cv.errors import ToolNotAvailableError


def find_tool(name: str) -> str:
    """Return the path of ``ffmpeg``/``ffprobe``; honours FFMPEG_PATH / FFPROBE_PATH."""
    override = os.environ.get(f"{name.upper()}_PATH")
    path = override or shutil.which(name)
    if not path:
        raise ToolNotAvailableError(
            f"'{name}' was not found on PATH. Install FFmpeg (https://ffmpeg.org) "
            f"or set {name.upper()}_PATH.",
            details={"tool": name},
        )
    return path


def tool_version(name: str) -> str | None:
    """First line of ``<tool> -version`` or None when the tool is missing."""
    try:
        path = find_tool(name)
    except ToolNotAvailableError:
        return None
    out = subprocess.run([path, "-version"], capture_output=True, text=True, check=False)
    first = out.stdout.splitlines()[0] if out.stdout else ""
    return first or None


def run_tool(
    args: list[str], *, timeout_s: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout_s)
