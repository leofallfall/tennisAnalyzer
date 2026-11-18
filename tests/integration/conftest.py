from __future__ import annotations

import math
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tennis_api.config import Settings
from tennis_api.main import create_app
from tennis_cv.pose.base import PoseEstimator
from tennis_cv.pose.landmarks import LANDMARK_NAMES
from tennis_cv.pose.models import Keypoint, PoseFrame
from tennis_cv.video.frames import Frame
from tennis_worker.jobs.video_processing import run_video_processing_job
from tennis_worker.main import Worker


class NoPersonEstimator(PoseEstimator):
    """Test double: behaves like an estimator that never finds a person."""

    name = "test_no_person"
    model = "none"

    def estimate(self, frame: Frame) -> PoseFrame:
        return PoseFrame(frame_index=frame.index, timestamp_s=frame.timestamp_s, detected=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(storage_dir=tmp_path / "storage", database_url="", cors_origins=[])


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def fake_worker(settings: Settings, client: TestClient) -> Worker:
    handler = partial(run_video_processing_job, estimator_factory=NoPersonEstimator)
    return Worker(settings, database=client.app.state.db, handlers={"video_processing": handler})  # type: ignore[attr-defined]


@pytest.fixture
def real_worker(settings: Settings, client: TestClient) -> Worker:
    return Worker(settings, database=client.app.state.db)  # type: ignore[attr-defined]


class SyntheticPoseEstimator(PoseEstimator):
    """Test double with a deterministic, fully visible pose (synthetic fixture, not CV).

    The right elbow angle sweeps 60° → 170° every 60 frames so angle time series are
    non-trivial. Coordinates are built in pixels (640x360) and normalised, so projected
    angles are exact.
    """

    name = "test_synthetic_pose"
    model = "synthetic"
    width, height = 640, 360

    def estimate(self, frame: Frame) -> PoseFrame:
        base = {name: (320.0, 180.0) for name in LANDMARK_NAMES}
        base.update(
            {
                "left_shoulder": (290.0, 110.0),
                "right_shoulder": (350.0, 110.0),
                "left_elbow": (280.0, 160.0),
                "left_wrist": (280.0, 205.0),
                "left_hip": (300.0, 200.0),
                "right_hip": (340.0, 200.0),
                "left_knee": (295.0, 260.0),
                "right_knee": (345.0, 260.0),
                "left_ankle": (300.0, 320.0),
                "right_ankle": (350.0, 320.0),
            }
        )
        theta = math.radians(60 + (frame.index % 60) * 110 / 59)
        ex, ey = 380.0, 150.0  # right elbow
        sx, sy = base["right_shoulder"]
        ux, uy = (sx - ex), (sy - ey)
        norm = math.hypot(ux, uy)
        ux, uy = ux / norm, uy / norm
        # Rotate the upper-arm direction by theta to get the forearm direction.
        fx = ux * math.cos(theta) - uy * math.sin(theta)
        fy = ux * math.sin(theta) + uy * math.cos(theta)
        base["right_elbow"] = (ex, ey)
        base["right_wrist"] = (ex + 50 * fx, ey + 50 * fy)
        kps = [
            Keypoint(name=n, x=x / self.width, y=y / self.height, visibility=0.95, presence=0.99)
            for n, (x, y) in ((n, base[n]) for n in LANDMARK_NAMES)
        ]
        return PoseFrame(
            frame_index=frame.index, timestamp_s=frame.timestamp_s, detected=True, keypoints=kps
        )


@pytest.fixture
def synthetic_worker(settings: Settings, client: TestClient) -> Worker:
    handler = partial(run_video_processing_job, estimator_factory=SyntheticPoseEstimator)
    return Worker(settings, database=client.app.state.db, handlers={"video_processing": handler})  # type: ignore[attr-defined]
