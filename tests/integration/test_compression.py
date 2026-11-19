"""M1.10 #7: JSON responses are gzip-compressed; video responses and ranges are not."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tennis_worker.main import Worker

from .test_api import upload


def test_json_is_gzipped_video_is_not(
    client: TestClient, fake_worker: Worker, basic_video: Path
) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    aid = client.post(
        "/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]}
    ).json()["id"]
    client.post(f"/api/analyses/{aid}/start")
    fake_worker.run_once()
    artifacts = client.get(f"/api/analyses/{aid}").json()["videos"][0]["artifacts"]

    gz = {"Accept-Encoding": "gzip"}
    pose = client.get(artifacts["pose"], headers=gz)
    assert pose.status_code == 200
    assert pose.headers.get("content-encoding") == "gzip"
    assert len(pose.json()["frames"]) == 60  # transparently decoded by the client

    plain = client.get(artifacts["pose"], headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert plain.json() == pose.json()

    proxy = client.get(artifacts["proxy"], headers=gz)
    assert proxy.status_code == 200 and "content-encoding" not in proxy.headers
    ranged = client.get(artifacts["proxy"], headers={**gz, "Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    assert "content-encoding" not in ranged.headers
    original = client.get(f"/api/videos/{vid}/file", headers=gz)
    assert "content-encoding" not in original.headers
