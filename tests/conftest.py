"""Shared fixtures. Synthetic videos are generated with FFmpeg at test time (not committed)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tennis_cv.pose.mediapipe_estimator import default_model_path

os.environ.setdefault("GLOG_minloglevel", "2")

MakeVideo = Callable[..., Path]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def video_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("videos")


@pytest.fixture(scope="session")
def make_video(video_dir: Path) -> MakeVideo:
    """Create a synthetic test-pattern video. Cached per parameter set."""

    def _make(
        width: int = 640,
        height: int = 360,
        fps: int = 30,
        duration: float = 2.0,
        name: str | None = None,
    ) -> Path:
        path = video_dir / (name or f"testsrc_{width}x{height}_{fps}fps_{duration}s.mp4")
        if not path.exists():
            _ffmpeg(
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            )
        return path

    return _make


@pytest.fixture(scope="session")
def basic_video(make_video: MakeVideo) -> Path:
    return make_video()


@pytest.fixture(scope="session")
def rotated_video(basic_video: Path, video_dir: Path) -> Path:
    """Landscape pixels with a display-matrix rotation, like a portrait phone recording."""
    path = video_dir / "rotated.mp4"
    if not path.exists():
        _ffmpeg("-display_rotation:v:0", "90", "-i", str(basic_video), "-c", "copy", str(path))
    return path


@pytest.fixture(scope="session")
def vfr_video(video_dir: Path) -> Path:
    """Variable frame rate: 10 fps for the first second, 30 fps afterwards."""
    path = video_dir / "vfr.mp4"
    if not path.exists():
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=3",
            "-vf",
            "select='gte(t,1)+not(mod(n,3))'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        )
    return path


@pytest.fixture(scope="session")
def not_a_video(video_dir: Path) -> Path:
    path = video_dir / "notes.mp4"
    path.write_text("this is not a video")
    return path


requires_model = pytest.mark.skipif(
    not default_model_path().is_file(),
    reason="MediaPipe model missing: run `uv run python scripts/download_models.py`",
)
