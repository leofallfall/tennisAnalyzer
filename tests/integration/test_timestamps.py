"""M1.10 #1: timestamps returned by the API are explicit UTC, also on SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from tennis_api.models import UTCDateTime
from tennis_worker.main import Worker

from .test_api import upload


def _is_utc(value: str) -> bool:
    parsed = datetime.fromisoformat(value)
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def test_api_timestamps_carry_utc_offset(
    client: TestClient, fake_worker: Worker, basic_video: Path
) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    aid = client.post(
        "/api/analyses", json={"mode": "session", "videos": [{"video_id": vid}]}
    ).json()["id"]
    client.post(f"/api/analyses/{aid}/start")
    fake_worker.run_once()

    # Every value below is re-read from SQLite by a fresh session.
    assert _is_utc(client.get(f"/api/videos/{vid}").json()["created_at"])
    detail = client.get(f"/api/analyses/{aid}").json()
    for key in ("created_at", "updated_at", "started_at", "finished_at"):
        assert _is_utc(detail[key]), (key, detail[key])
    status = client.get(f"/api/analyses/{aid}/status").json()
    for key in ("updated_at", "started_at", "finished_at"):
        assert _is_utc(status[key]), (key, status[key])
    assert _is_utc(client.get("/api/analyses").json()[0]["created_at"])


def test_existing_naive_rows_are_read_as_utc(client: TestClient, basic_video: Path) -> None:
    """Rows written before the fix hold naive UTC wall times: they must not shift."""
    vid = upload(client, basic_video)["body"]["id"]
    db = client.app.state.db  # type: ignore[attr-defined]
    with db.engine.begin() as conn:
        conn.execute(
            text("UPDATE videos SET created_at = '2026-11-18 10:00:00.000000' WHERE id = :id"),
            {"id": vid},
        )
    created = datetime.fromisoformat(client.get(f"/api/videos/{vid}").json()["created_at"])
    assert created == datetime(2026, 11, 18, 10, 0, tzinfo=UTC)


def test_utc_type_normalises_other_offsets() -> None:
    col = UTCDateTime()
    utc_noon = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    rome = datetime(2026, 11, 19, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    stored = col.process_bind_param(rome, None)  # type: ignore[arg-type]
    assert stored == utc_noon and stored is not None and stored.tzinfo == UTC
    assert col.process_result_value(datetime(2026, 11, 19, 12, 0), None) == utc_noon  # type: ignore[arg-type]
