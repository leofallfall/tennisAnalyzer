"""M1.10 #5: dominant hand and backhand style reach the backend: optional, validated,
persisted, null when not provided, never inferred."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from tennis_api.db import Database

from .test_api import upload


def _create(client: TestClient, vid: str, **extra: object):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/analyses",
        json={"mode": "session", "videos": [{"video_id": vid}], **extra},
    )


def test_values_are_persisted_and_returned(client: TestClient, basic_video: Path) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    r = _create(client, vid, dominant_hand="left", backhand="one_handed")
    assert r.status_code == 201, r.json()
    aid = r.json()["id"]
    assert (r.json()["dominant_hand"], r.json()["backhand"]) == ("left", "one_handed")
    again = client.get(f"/api/analyses/{aid}").json()  # re-read from the database
    assert (again["dominant_hand"], again["backhand"]) == ("left", "one_handed")


def test_missing_values_stay_null(client: TestClient, basic_video: Path) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    for extra in ({}, {"dominant_hand": None, "backhand": None}):
        aid = _create(client, vid, **extra).json()["id"]
        body = client.get(f"/api/analyses/{aid}").json()
        assert body["dominant_hand"] is None and body["backhand"] is None
    only_hand = _create(client, vid, dominant_hand="right").json()
    assert only_hand["dominant_hand"] == "right" and only_hand["backhand"] is None


def test_invalid_values_are_rejected(client: TestClient, basic_video: Path) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    for extra in (
        {"dominant_hand": "ambidextrous"},
        {"dominant_hand": "Right"},
        {"dominant_hand": ""},
        {"backhand": "two-handed"},
    ):
        r = _create(client, vid, **extra)
        assert r.status_code == 422, extra
        assert r.json()["error"]["code"] == "request_invalid"


def test_migration_adds_nullable_columns_to_existing_database(
    client: TestClient, basic_video: Path
) -> None:
    vid = upload(client, basic_video)["body"]["id"]
    aid = _create(client, vid).json()["id"]
    db: Database = client.app.state.db  # type: ignore[attr-defined]
    with db.engine.begin() as conn:  # schema before M1.10
        conn.execute(text("ALTER TABLE analyses DROP COLUMN dominant_hand"))
        conn.execute(text("ALTER TABLE analyses DROP COLUMN backhand"))
        conn.execute(
            text("DELETE FROM schema_migrations WHERE id = '2026_11_19_analysis_player_handedness'")
        )
    assert db.migrate() == ["2026_11_19_analysis_player_handedness"]
    assert db.migrate() == []
    cols = {c["name"] for c in inspect(db.engine).get_columns("analyses")}
    assert {"dominant_hand", "backhand"} <= cols
    body = client.get(f"/api/analyses/{aid}").json()  # existing row: not inferred
    assert body["dominant_hand"] is None and body["backhand"] is None
