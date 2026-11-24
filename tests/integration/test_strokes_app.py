"""Personal V1 step 3: detector output in the worker/API flow (ADR-028)."""

from __future__ import annotations

from functools import partial
from pathlib import Path

from fastapi.testclient import TestClient

from tennis_api.config import Settings
from tennis_api.storage import analysis_video_dir
from tennis_worker.jobs.stroke_detection import run_stroke_detection_job
from tennis_worker.jobs.video_processing import run_video_processing_job
from tennis_worker.main import Worker

from .conftest import NoPersonEstimator, SyntheticPoseEstimator
from .test_api import upload

STATES = {"flagged", "to_check", "discarded"}


def _worker(settings: Settings, client: TestClient, estimator: type) -> Worker:
    return Worker(
        settings,
        database=client.app.state.db,  # type: ignore[attr-defined]
        handlers={
            "video_processing": partial(run_video_processing_job, estimator_factory=estimator),
            "stroke_detection": run_stroke_detection_job,
        },
    )


def _processed(client: TestClient, worker: Worker, video: Path) -> tuple[str, str]:
    vid = upload(client, video)["body"]["id"]
    aid = client.post("/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]})
    aid = aid.json()["id"]
    assert client.get(f"/api/analyses/{aid}/videos/{vid}/strokes").status_code == 404
    client.post(f"/api/analyses/{aid}/start")
    assert worker.run_once()
    return aid, vid


def test_processing_writes_strokes_and_view_uses_proxy_times(
    client: TestClient, settings: Settings, basic_video: Path
) -> None:
    aid, vid = _processed(client, _worker(settings, client, SyntheticPoseEstimator), basic_video)
    a = client.get(f"/api/analyses/{aid}").json()
    assert a["status"] == "completed"
    v = a["videos"][0]
    assert v["strokes_url"] == f"/api/analyses/{aid}/videos/{vid}/strokes"
    assert "strokes" in v["artifacts"]
    det = v["stroke_detection"]
    assert det["status"] == "completed" and det["experimental"] is True
    assert set(det["events"]) == {"accepted", "uncertain", "rejected"}
    assert a["results"]["strokes"][vid] == det

    view = client.get(v["strokes_url"]).json()
    assert view["experimental"] is True
    assert "non assenza di colpi" in view["note"]
    assert view["provisional_params"]["t_reject"] == 0.45
    assert view["provisional_params"]["t_accept"] == 0.7
    fps = view["fps"]
    for e in view["events"]:
        assert e["state"] in STATES
        assert e["window_start_s"] <= e["window_end_s"]
        if e["frame"] is not None:
            assert e["time_s"] == round(e["frame"] / fps, 3)
        if e["state"] == "to_check":
            assert e["reasons"], "an uncertain event always says why"
    for s in view["not_assessable"]:
        assert s["start_s"] == round(s["start_frame"] / fps, 3)
        assert "non vuol dire che non ci siano" in s["reason"]
    counts = {k: 0 for k in ("accepted", "uncertain", "rejected")}
    for e in view["events"]:
        counts[e["status"]] += 1
    assert counts == det["events"]
    raw = client.get(v["artifacts"]["strokes"])
    assert raw.status_code == 200 and raw.json()["schema"] == "tpa.strokes"


def test_no_person_gives_not_assessable_not_absence(
    client: TestClient, settings: Settings, basic_video: Path
) -> None:
    aid, vid = _processed(client, _worker(settings, client, NoPersonEstimator), basic_video)
    view = client.get(f"/api/analyses/{aid}/videos/{vid}/strokes").json()
    assert view["events"] == []
    assert view["not_assessable"], "no pose at all must show as not assessable"
    assert view["not_assessable"][0]["label"] == "Non valutabile"


def test_old_analysis_gets_strokes_without_new_pose(
    client: TestClient, settings: Settings, basic_video: Path
) -> None:
    worker = _worker(settings, client, SyntheticPoseEstimator)
    aid, vid = _processed(client, worker, basic_video)
    out_dir = analysis_video_dir(settings.storage_dir, aid, vid)
    (out_dir / "strokes.json").unlink()  # as an analysis processed before V1
    pose_mtime = (out_dir / "pose.json").stat().st_mtime_ns
    before = client.get(f"/api/analyses/{aid}").json()
    assert before["videos"][0]["strokes_url"] is None

    r = client.post(f"/api/analyses/{aid}/strokes")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert client.post(f"/api/analyses/{aid}/strokes").status_code == 409  # not completed
    assert worker.run_once()

    after = client.get(f"/api/analyses/{aid}").json()
    assert after["status"] == "completed"
    assert after["videos"][0]["strokes_url"] is not None
    assert (out_dir / "pose.json").stat().st_mtime_ns == pose_mtime  # pose not recomputed
    assert after["results"]["videos"] == before["results"]["videos"]
    assert client.get(after["videos"][0]["strokes_url"]).status_code == 200


def test_strokes_endpoint_errors(client: TestClient, settings: Settings, basic_video: Path) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    aid = client.post(
        "/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]}
    ).json()["id"]
    r = client.post(f"/api/analyses/{aid}/strokes")
    assert r.status_code == 409 and r.json()["error"]["code"] == "analysis_not_completed"
    r = client.get(f"/api/analyses/{aid}/videos/other/strokes")
    assert r.status_code == 404 and r.json()["error"]["code"] == "video_not_found"
    r = client.get(f"/api/analyses/{aid}/videos/{vid}/strokes")
    assert r.json()["error"]["code"] == "strokes_not_available"
