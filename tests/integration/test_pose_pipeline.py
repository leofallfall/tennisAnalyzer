"""Runs the real MediaPipe model. Skipped when the model file has not been downloaded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tennis_cv.pipeline import PipelineOptions, run_video_processing
from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator
from tennis_worker.main import Worker
from tests.conftest import requires_model

pytestmark = [requires_model, pytest.mark.slow]


def test_pipeline_outputs_on_synthetic_video(basic_video: Path, tmp_path: Path) -> None:
    with MediaPipePoseEstimator() as est:
        report = run_video_processing(
            basic_video, tmp_path, est, options=PipelineOptions(pose_stride=2)
        )
    # A test pattern contains no person: the honest answer is "not observable".
    assert report.pose_summary.frames_analysed == 30
    assert report.pose_summary.detection_rate == 0.0
    assert report.quality.observability == "not_observable"
    pose = json.loads((tmp_path / "pose.json").read_text())
    assert pose["frame_stride"] == 2
    assert [f["frame_index"] for f in pose["frames"][:3]] == [0, 2, 4]
    assert pose["frames"][1]["timestamp_s"] == pytest.approx(2 / 30)
    assert (tmp_path / "overlay.mp4").stat().st_size > 0
    assert {t.step for t in report.timings} >= {"preprocess", "decode_quality_pose_overlay"}


def test_worker_with_real_model(client: TestClient, real_worker: Worker, basic_video: Path) -> None:
    with basic_video.open("rb") as fh:
        vid = client.post("/api/videos/upload", files={"file": ("a.mp4", fh, "video/mp4")}).json()[
            "id"
        ]
    aid = client.post(
        "/api/analyses", json={"mode": "single_stroke", "videos": [{"video_id": vid}]}
    ).json()["id"]
    client.post(f"/api/analyses/{aid}/start")
    real_worker.run_once()
    analysis = client.get(f"/api/analyses/{aid}").json()
    assert analysis["status"] == "completed", analysis["error"]
    assert analysis["results"]["videos"][vid]["pose_estimator"] == "mediapipe_pose_landmarker"
