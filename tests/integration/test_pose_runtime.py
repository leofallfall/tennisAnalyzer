"""M1.10 #3: MediaPipe runtime failures are detected up front and reported as structured
errors (missing libEGL/libGLES on Linux used to surface as internal_error)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import tennis_cv.pose.mediapipe_estimator as mpe
from tennis_api.config import Settings
from tennis_cv.errors import PoseRuntimeUnavailableError
from tennis_worker.main import Worker

from ..conftest import requires_model
from .test_api import upload

LIB_ERROR = "libEGL.so.1: cannot open shared object file: No such file or directory"


@pytest.fixture
def broken_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Simulate the missing system library at the exact point where MediaPipe loads it."""
    import mediapipe as mp

    def fail(*_a: Any, **_k: Any) -> None:
        raise OSError(LIB_ERROR)

    monkeypatch.setattr(mp.tasks.vision.PoseLandmarker, "create_from_options", fail)
    monkeypatch.setattr(mpe, "_runtime_ok", False)
    yield


@requires_model
def test_estimator_raises_structured_error(broken_runtime: None) -> None:
    with pytest.raises(PoseRuntimeUnavailableError) as err:
        mpe.MediaPipePoseEstimator()
    assert err.value.code == "pose_runtime_unavailable"
    assert "libegl1 libgles2" in err.value.message
    assert err.value.details["cause"].startswith("libEGL.so.1")


@requires_model
def test_worker_reports_runtime_error_not_internal_error(
    client: TestClient, real_worker: Worker, basic_video: Path, broken_runtime: None
) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    aid = client.post(
        "/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]}
    ).json()["id"]
    client.post(f"/api/analyses/{aid}/start")
    real_worker.run_once()
    error = client.get(f"/api/analyses/{aid}/status").json()["error"]
    assert error["code"] == "pose_runtime_unavailable"
    assert "libegl1 libgles2" in error["message"]


@requires_model
def test_system_info_checks_the_runtime_not_just_the_import(
    client: TestClient, broken_runtime: None
) -> None:
    info = client.get("/api/system/info").json()
    mediapipe = next(t for t in info["tools"] if t["name"] == "mediapipe")
    assert mediapipe["available"] is True  # the import alone still works
    runtime = info["pose_runtime"]
    assert runtime["available"] is False
    assert "libegl1 libgles2" in runtime["detail"]


@requires_model
def test_system_info_runtime_ok_in_a_working_environment(client: TestClient) -> None:
    info = client.get("/api/system/info").json()
    assert info["pose_runtime"] == {
        "name": "mediapipe_runtime",
        "available": True,
        "version": None,
        "detail": None,
    }


def test_missing_model_is_reported_by_runtime_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(mpe, "_runtime_ok", False)
    ok, reason = mpe.check_pose_runtime(tmp_path / "absent.task")
    assert ok is False and reason is not None
    assert "absent.task" in reason and str(tmp_path) not in reason
