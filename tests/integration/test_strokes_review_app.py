"""V1 step 5: corrections in the app, stored apart from strokes.json (ADR-031)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tennis_api.config import Settings
from tennis_api.storage import analysis_video_dir

from .conftest import SyntheticPoseEstimator
from .test_strokes_app import FIXTURE, _processed, _worker


def _setup(client: TestClient, settings: Settings, video: Path) -> tuple[str, Path]:
    aid, vid = _processed(client, _worker(settings, client, SyntheticPoseEstimator), video)
    out_dir = analysis_video_dir(settings.storage_dir, aid, vid)
    (out_dir / "strokes.json").write_bytes(FIXTURE.read_bytes())
    return f"/api/analyses/{aid}/videos/{vid}/strokes", out_dir


def _event(view: dict, event_id: str) -> dict:
    return next(e for e in view["events"] if e["id"] == event_id)


def test_corrections_round_trip_without_touching_strokes_json(
    client: TestClient, settings: Settings, basic_video: Path
) -> None:
    url, out_dir = _setup(client, settings, basic_video)
    raw = (out_dir / "strokes.json").read_bytes()
    view = client.get(url).json()
    assert view["frame_count"] == 1805
    assert view["my_strokes"] == [] and view["reviewed_stretches"] == []
    assert all(e["review"] is None for e in view["events"])

    # confirm with type, at the event's own frame
    r = client.post(
        f"{url}/review/entries",
        json={"event_id": "e0001", "stroke_type": "backhand", "visibility": "full"},
    )
    assert r.status_code == 201
    rev = _event(r.json(), "e0001")["review"]
    assert rev["action"] == "confirmed" and rev["frame"] == 581
    assert rev["stroke_type_label"] == "Rovescio"
    assert _event(r.json(), "e0001")["state"] == "flagged"  # the detector state is kept

    # a second correction on the same event is refused
    r = client.post(f"{url}/review/entries", json={"event_id": "e0001"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "review_conflict"

    # move it
    r = client.put(
        f"{url}/review/entries/{rev['id']}",
        json={"kind": "stroke", "frame": 590, "stroke_type": "backhand", "visibility": "full"},
    )
    assert _event(r.json(), "e0001")["review"]["action"] == "moved"

    # delete another with a reason; a reason is required
    bad = client.post(f"{url}/review/entries", json={"event_id": "e0002", "kind": "not_stroke"})
    assert bad.status_code == 422
    r = client.post(
        f"{url}/review/entries",
        json={"event_id": "e0002", "kind": "not_stroke", "reason": "duplicate"},
    )
    assert _event(r.json(), "e0002")["review"]["action"] == "deleted"

    # add a missed stroke; past the end of the video is refused
    assert client.post(f"{url}/review/entries", json={"frame": 1805}).status_code == 422
    r = client.post(f"{url}/review/entries", json={"frame": 900, "stroke_type": "forehand"})
    mine = r.json()["my_strokes"]
    assert [(m["frame"], m["action"]) for m in mine] == [(900, "added")]
    dup = client.post(f"{url}/review/entries", json={"frame": 900})
    assert dup.json()["error"]["code"] == "review_conflict"
    assert (
        client.post(
            f"{url}/review/entries", json={"kind": "not_stroke", "frame": 5, "reason": "other"}
        ).status_code
        == 422
    )

    # reviewed stretches, merged when they touch
    client.post(f"{url}/review/stretches", json={"start_frame": 500, "end_frame": 700})
    r = client.post(f"{url}/review/stretches", json={"start_frame": 701, "end_frame": 1000})
    stretches = r.json()["reviewed_stretches"]
    assert [(s["start_frame"], s["end_frame"]) for s in stretches] == [(500, 1000)]

    assert (out_dir / "strokes.json").read_bytes() == raw  # detector output untouched
    assert (out_dir / "strokes_review.json").is_file()

    # export: 590 backhand inside the stretch; 900 has no visibility -> listed, not written
    z = client.get(f"{url}/review/export", params={"clip_id": "mio_video"})
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    files = zipfile.ZipFile(io.BytesIO(z.content))
    labels = files.read("labels.csv").decode()
    assert "mio_video,590,backhand,A,full,," in labels
    assert "# non esportato, frame 900: manca visibilità" in labels
    assert "mio_video,500,1000,all,A," in files.read("annotated.csv").decode()
    assert "LEGGIMI.txt" in files.namelist()

    # undo: the event is back to the detector's state only
    r = client.delete(f"{url}/review/entries/{rev['id']}")
    assert _event(r.json(), "e0001")["review"] is None
    sid = stretches[0]["id"]
    assert client.delete(f"{url}/review/stretches/{sid}").json()["reviewed_stretches"] == []
    assert client.delete(f"{url}/review/stretches/{sid}").status_code == 404


def test_corrections_survive_a_new_detection(
    client: TestClient, settings: Settings, basic_video: Path
) -> None:
    url, out_dir = _setup(client, settings, basic_video)
    client.post(f"{url}/review/entries", json={"event_id": "e0001", "stroke_type": "forehand"})
    # a new detection where event e0001 is gone: the correction stays, detached
    text = FIXTURE.read_text().replace('"e0001"', '"e0099"')
    (out_dir / "strokes.json").write_text(text)
    view = client.get(url).json()
    assert view["my_strokes"][0]["action"] == "detached"
    assert view["my_strokes"][0]["frame"] == 581
    assert view["my_strokes"][0]["stroke_type"] == "forehand"


def test_review_needs_strokes(client: TestClient, settings: Settings, basic_video: Path) -> None:
    url, out_dir = _setup(client, settings, basic_video)
    (out_dir / "strokes.json").unlink()
    r = client.post(f"{url}/review/entries", json={"frame": 3})
    assert r.status_code == 404 and r.json()["error"]["code"] == "strokes_not_available"
