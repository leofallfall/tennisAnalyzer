"""M1.10 #2: API responses never expose server filesystem paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tennis_api.config import Settings
from tennis_api.redaction import redact
from tennis_cv.errors import DecodeError, ModelNotAvailableError, PreprocessError, scrub_paths
from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator
from tennis_cv.video.frames import iter_frames
from tennis_cv.video.metadata import probe_video
from tennis_cv.video.preprocess import PreprocessOptions, preprocess_video
from tennis_worker.main import Worker

from .test_api import upload


def assert_no_paths(payload: Any, *roots: Path) -> None:
    dumped = json.dumps(payload)
    for root in (*roots, Path.home()):
        for variant in {str(root), str(root.resolve())}:
            assert variant not in dumped, f"{variant} leaked in {dumped[:500]}"
    assert "traceback" not in dumped.lower()


def _analysis(client: TestClient, vid: str) -> str:
    r = client.post("/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]})
    return str(r.json()["id"])


def test_probe_failed_names_file_only(
    client: TestClient, not_a_video: Path, settings: Settings
) -> None:
    res = upload(client, not_a_video)
    assert res["status"] == 422
    error = res["body"]["error"]
    assert error["code"] == "probe_failed"
    assert error["details"]["file"] == "original.mp4"
    assert "path" not in error["details"]
    assert error["details"]["stderr"]  # still useful for diagnosis, without the path
    assert_no_paths(res["body"], settings.storage_dir, not_a_video.parent)


def test_completed_analysis_and_report_have_no_paths(
    client: TestClient, fake_worker: Worker, basic_video: Path, settings: Settings
) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    aid = _analysis(client, vid)
    client.post(f"/api/analyses/{aid}/start")
    fake_worker.run_once()
    detail = client.get(f"/api/analyses/{aid}").json()
    assert detail["status"] == "completed"
    assert_no_paths(detail, settings.storage_dir)
    report = detail["results"]["videos"][vid]
    assert report["source_metadata"]["path"] == "original.mp4"
    assert report["preprocess"]["output_path"] == "proxy.mp4"
    assert report["preprocess"]["metadata"]["path"] == "proxy.mp4"
    served = client.get(detail["videos"][0]["artifacts"]["report"])
    assert served.status_code == 200
    assert_no_paths(served.json(), settings.storage_dir)


def test_legacy_rows_and_reports_are_redacted(
    client: TestClient, fake_worker: Worker, basic_video: Path, settings: Settings
) -> None:
    """Data written before M1.10 still holds absolute paths: redacted on the way out."""
    vid = upload(client, basic_video)["body"]["id"]
    aid = _analysis(client, vid)
    client.post(f"/api/analyses/{aid}/start")
    fake_worker.run_once()
    abs_src = str(settings.storage_dir / "videos" / vid / "original.mp4")
    out_dir = settings.storage_dir / "analyses" / aid / "videos" / vid
    report_file = out_dir / "report.json"
    report = json.loads(report_file.read_text())
    report["source_metadata"]["path"] = abs_src
    report_file.write_text(json.dumps(report))
    db = client.app.state.db  # type: ignore[attr-defined]
    with db.engine.begin() as conn:
        row = conn.execute(text("SELECT results FROM analyses WHERE id = :id"), {"id": aid})
        results = json.loads(row.scalar_one())
        results["videos"][vid]["source_metadata"]["path"] = abs_src
        conn.execute(
            text(
                "UPDATE analyses SET results = :r, error_code = 'internal_error', "
                "error_message = :m, error_details = :d WHERE id = :id"
            ),
            {
                "id": aid,
                "r": json.dumps(results),
                "m": f"FileNotFoundError: {abs_src}",
                "d": json.dumps({"traceback": f'File "{abs_src}"', "path": abs_src}),
            },
        )
    detail = client.get(f"/api/analyses/{aid}").json()
    assert_no_paths(detail, settings.storage_dir)
    assert detail["error"]["message"].endswith("<storage>/videos/" + vid + "/original.mp4")
    assert "traceback" not in detail["error"]["details"]
    assert_no_paths(client.get(f"/api/analyses/{aid}/status").json(), settings.storage_dir)
    assert_no_paths(client.get("/api/analyses").json(), settings.storage_dir)
    served = client.get(detail["videos"][0]["artifacts"]["report"]).json()
    assert served["source_metadata"]["path"].startswith("<storage>/")


def test_internal_error_keeps_traceback_in_log_only(
    client: TestClient, settings: Settings, basic_video: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def crashing_handler(*_args: object) -> dict[str, object]:
        raise FileNotFoundError(f"{settings.storage_dir}/secret/thing.bin")

    worker = Worker(
        settings,
        database=client.app.state.db,  # type: ignore[attr-defined]
        handlers={"video_processing": crashing_handler},
    )
    vid = upload(client, basic_video)["body"]["id"]
    aid = _analysis(client, vid)
    client.post(f"/api/analyses/{aid}/start")
    with caplog.at_level("ERROR"):
        worker.run_once()
    status = client.get(f"/api/analyses/{aid}/status").json()
    error = status["error"]
    assert error["code"] == "internal_error"
    assert set(error["details"]) == {"error_id", "exception"}
    assert error["details"]["exception"] == "FileNotFoundError"
    assert_no_paths(status, settings.storage_dir)
    # The full traceback, with the error id to correlate, is in the server log.
    assert error["details"]["error_id"] in caplog.text
    assert "Traceback" in caplog.text


def test_system_info_does_not_expose_storage_path(client: TestClient, settings: Settings) -> None:
    info = client.get("/api/system/info").json()
    assert "storage_dir" not in info
    assert info["storage_writable"] is True
    assert_no_paths(info, settings.storage_dir)


def test_engine_errors_use_base_names(tmp_path: Path, basic_video: Path) -> None:
    with pytest.raises(DecodeError) as decode:
        next(iter_frames(tmp_path / "missing.mp4"))
    assert decode.value.details == {"file": "missing.mp4"}
    assert str(tmp_path) not in decode.value.message

    with pytest.raises(ModelNotAvailableError) as model:
        MediaPipePoseEstimator(tmp_path / "nope.task")
    assert model.value.details == {"model_file": "nope.task"}
    assert str(tmp_path) not in model.value.message

    meta = probe_video(basic_video)
    with pytest.raises(PreprocessError) as prep:
        # An invalid preset makes ffmpeg fail after reading the input.
        preprocess_video(
            basic_video,
            tmp_path / "out" / "proxy.mp4",
            source_metadata=meta,
            options=PreprocessOptions(preset="not-a-preset"),
        )
    assert "command" not in prep.value.details
    assert str(tmp_path) not in json.dumps(prep.value.details)
    assert str(basic_video.parent) not in json.dumps(prep.value.details)


def test_scrub_paths_and_redact() -> None:
    assert scrub_paths("/a/b/c.mp4: Invalid data", "/a/b/c.mp4") == "c.mp4: Invalid data"
    settings = Settings(storage_dir=Path("/srv/tpa/storage"), database_url="", cors_origins=[])
    value = {"x": ["/srv/tpa/storage/videos/1/original.mp4", 3], "y": ("ok",)}
    assert redact(value, settings) == {"x": ["<storage>/videos/1/original.mp4", 3], "y": ("ok",)}
