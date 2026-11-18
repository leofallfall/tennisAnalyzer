from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tennis_worker.main import Worker


def upload(client: TestClient, path: Path, name: str | None = None) -> dict:  # type: ignore[type-arg]
    with path.open("rb") as fh:
        r = client.post("/api/videos/upload", files={"file": (name or path.name, fh, "video/mp4")})
    return {"status": r.status_code, "body": r.json()}


def test_health_and_system_info(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    info = client.get("/api/system/info").json()
    assert {t["name"] for t in info["tools"]} >= {"ffmpeg", "ffprobe", "mediapipe"}
    caps = {c["module"]: c["status"] for c in info["capabilities"]}
    assert caps["strokes"] == "not_implemented"


def test_upload_extracts_metadata(client: TestClient, basic_video: Path) -> None:
    res = upload(client, basic_video)
    assert res["status"] == 201, res
    video = res["body"]
    assert video["metadata"]["display_width"] == 640
    assert video["metadata"]["average_fps"] == 30
    warnings = {i["code"] for i in video["validation"]["issues"]}
    assert {"fps_low", "resolution_low"} <= warnings
    assert client.get(f"/api/videos/{video['id']}").status_code == 200
    file = client.get(video["file_url"])
    assert file.status_code == 200 and len(file.content) == video["size_bytes"]


def test_upload_rejects_bad_inputs(client: TestClient, not_a_video: Path, make_video) -> None:  # type: ignore[no-untyped-def]
    bad_ext = client.post("/api/videos/upload", files={"file": ("a.txt", b"hello", "text/plain")})
    assert bad_ext.status_code == 415
    assert bad_ext.json()["error"]["code"] == "unsupported_file_type"

    garbage = upload(client, not_a_video)
    assert garbage["status"] == 422 and garbage["body"]["error"]["code"] == "probe_failed"

    tiny = upload(client, make_video(width=320, height=240, duration=1.0))
    assert tiny["status"] == 422
    assert tiny["body"]["error"]["code"] == "video_invalid"
    assert "resolution_too_low" in {i["code"] for i in tiny["body"]["error"]["details"]["issues"]}


def test_not_found_is_structured(client: TestClient) -> None:
    r = client.get("/api/analyses/nope")
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "analysis_not_found",
            "message": "Analysis 'nope' not found",
            "details": {},
        }
    }


def test_multi_video_requires_explicit_relation(
    client: TestClient, basic_video: Path, rotated_video: Path
) -> None:
    a = upload(client, basic_video)["body"]["id"]
    b = upload(client, rotated_video)["body"]["id"]
    body = {"mode": "single_stroke", "videos": [{"video_id": a}, {"video_id": b}]}
    assert client.post("/api/analyses", json=body).status_code == 422
    body["video_relation"] = "same_stroke_multi_angle"
    body["videos"][1]["camera_view"] = "behind"
    r = client.post("/api/analyses", json=body)
    assert r.status_code == 201
    assert [v["camera_view"] for v in r.json()["videos"]] == ["unknown", "behind"]


def test_analysis_lifecycle(client: TestClient, fake_worker: Worker, basic_video: Path) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    created = client.post("/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]})
    assert created.status_code == 201
    aid = created.json()["id"]
    assert created.json()["status"] == "created"
    assert client.get("/api/analyses").json()[0]["id"] == aid

    started = client.post(f"/api/analyses/{aid}/start")
    assert started.status_code == 202 and started.json()["status"] == "queued"
    again = client.post(f"/api/analyses/{aid}/start")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "analysis_not_startable"

    assert fake_worker.run_once() is True
    status = client.get(f"/api/analyses/{aid}/status").json()
    assert status["status"] == "completed" and status["progress"] == 1.0

    analysis = client.get(f"/api/analyses/{aid}").json()
    report = analysis["results"]["videos"][vid]
    assert report["pose_summary"]["detection_rate"] == 0.0
    assert report["quality"]["observability"] == "not_observable"
    assert "stroke_detection" in report["not_performed"]
    artifacts = analysis["videos"][0]["artifacts"]
    assert set(artifacts) == {"proxy", "pose", "overlay", "report"}
    overlay = client.get(artifacts["overlay"])
    assert overlay.status_code == 200 and overlay.headers["content-type"] == "video/mp4"
    pose = client.get(artifacts["pose"]).json()
    assert len(pose["frames"]) == 60


def test_failed_job_reports_structured_error(
    client: TestClient, fake_worker: Worker, basic_video: Path, settings
) -> None:  # type: ignore[no-untyped-def]
    vid = upload(client, basic_video)["body"]["id"]
    aid = client.post(
        "/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]}
    ).json()["id"]
    # Simulate storage corruption: the source file disappears before processing.
    video = client.get(f"/api/videos/{vid}").json()
    (settings.storage_dir / video["metadata"]["path"]).unlink()
    client.post(f"/api/analyses/{aid}/start")
    fake_worker.run_once()
    status = client.get(f"/api/analyses/{aid}/status").json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "probe_failed"
    # A failed analysis can be restarted.
    assert client.post(f"/api/analyses/{aid}/start").status_code == 202
