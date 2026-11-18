from __future__ import annotations

from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tennis_api.config import Settings
from tennis_api.main import create_app
from tennis_cv.pose.base import PoseEstimator
from tennis_cv.pose.models import PoseFrame
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
