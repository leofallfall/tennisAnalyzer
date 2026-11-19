"""Personal AI analysis: Evidence Pack, validation and import (synthetic fixtures only).

These tests check plumbing and honesty rules. They do NOT validate biomechanical quality:
poses come from a synthetic estimator and strokes are hand-made fixtures.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tennis_api.ai import pack as pk
from tennis_api.ai.builder import (
    EvidencePackError,
    StrokeData,
    build_session_pack,
    build_single_stroke_pack,
    manifest_sha256,
)
from tennis_api.ai.client import AnthropicAPIAdapter, ClaudeCodeAdapter, get_ai_client
from tennis_api.ai.result import RESULT_FILENAME, AnalysisResult
from tennis_api.ai.sources import session_metadata, video_sources
from tennis_api.ai.validation import PackAnchor, validate_result_file, validate_result_text
from tennis_api.config import Settings
from tennis_api.models import AiAnalysis, Analysis
from tennis_cv.biomechanics.measurement import Measurement
from tennis_cv.common import Observability, Reliability, TimeInterval
from tennis_cv.errors import NotYetImplementedError
from tennis_cv.strokes.base import StrokeType
from tennis_worker.main import Worker

# ------------------------------------------------------------------ fixtures


@dataclass
class Processed:
    client: TestClient
    settings: Settings
    analysis_id: str
    video_id: str

    def analysis(self) -> Iterator[Analysis]:
        with self.client.app.state.db.session() as s:  # type: ignore[attr-defined]
            a = s.get(Analysis, self.analysis_id)
            assert a is not None
            yield a


def _process(
    client: TestClient, worker: Worker, video: Path, settings: Settings, mode: str = "session"
) -> Processed:
    with video.open("rb") as fh:
        vid = client.post("/api/videos/upload", files={"file": ("clip.mp4", fh, "video/mp4")})
    video_id = vid.json()["id"]
    aid = client.post(
        "/api/analyses", json={"mode": mode, "videos": [{"video_id": video_id}]}
    ).json()["id"]
    client.post(f"/api/analyses/{aid}/start")
    assert worker.run_once()
    assert client.get(f"/api/analyses/{aid}/status").json()["status"] == "completed"
    return Processed(client, settings, aid, video_id)


@pytest.fixture
def processed(
    client: TestClient, synthetic_worker: Worker, basic_video: Path, settings: Settings
) -> Processed:
    return _process(client, synthetic_worker, basic_video, settings)


def _cv_hashes(settings: Settings, analysis_id: str) -> dict[str, str]:
    root = settings.storage_dir / "analyses" / analysis_id
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _rec(sid: str, video_id: str, start: float, stype: StrokeType) -> pk.StrokeRecord:
    return pk.StrokeRecord(
        id=sid,
        video_id=video_id,
        interval=TimeInterval(start_s=start, end_s=start + 0.5),
        estimated_contact_s=start + 0.3,
        stroke_type=stype,
        type_confidence=0.9,
        reliability=Reliability(observability=Observability.OBSERVABLE, confidence=0.8),
    )


def _meas(mid: str, sid: str, video_id: str, value: float | None) -> pk.MeasurementRecord:
    observable = value is not None
    return pk.MeasurementRecord(
        id=mid,
        video_id=video_id,
        measurement=Measurement(
            metric_id="elbow_angle_projected",
            value=value,
            unit="deg",
            stroke_id=sid,
            confidence=0.8 if observable else None,
            observability=Observability.OBSERVABLE if observable else Observability.NOT_OBSERVABLE,
            method="synthetic fixture",
        ),
    )


def synthetic_stroke_data(video_id: str) -> StrokeData:
    strokes = [
        _rec("stk_1", video_id, 0.0, StrokeType.FOREHAND),
        _rec("stk_2", video_id, 0.6, StrokeType.FOREHAND),
        _rec("stk_3", video_id, 1.2, StrokeType.BACKHAND),
    ]
    measurements = [
        _meas("m_1", "stk_1", video_id, 142.0),
        _meas("m_2", "stk_2", video_id, 120.5),
        _meas("m_3", "stk_3", video_id, None),  # not observable: excluded from denominators
    ]
    candidates = [
        pk.IssueCandidate(
            id="cand_1",
            metric_id="elbow_angle_projected",
            stroke_type=StrokeType.FOREHAND,
            description="synthetic candidate",
            rule="synthetic",
            stroke_ids=["stk_2"],
            occurrences=1,
            evaluable=2,
        )
    ]
    return StrokeData(strokes=strokes, measurements=measurements, issue_candidates=candidates)


def build_with_strokes(p: Processed) -> Path:
    for a in p.analysis():
        videos = video_sources(a, p.settings)
        meta = session_metadata(a).model_copy(update={"videos": [v.info for v in videos]})
        m, pack_dir = build_session_pack(
            p.settings.ai_dir / a.id, meta, videos, synthetic_stroke_data(p.video_id)
        )
    # What the app records in the database when it creates the pack (C2 anchor).
    ANCHORS[pack_dir] = PackAnchor(
        pack_id=m.pack_id,
        analysis_id=m.analysis_id,
        kind=m.kind,
        manifest_sha256=manifest_sha256(m),
    )
    return pack_dir


ANCHORS: dict[Path, PackAnchor] = {}


def anchor(pack_dir: Path) -> PackAnchor:
    return ANCHORS[pack_dir]


def api_pack(p: Processed, body: dict[str, Any]) -> Path:
    """Pack folder of an AI analysis created through the API, with its DB anchor."""
    with p.client.app.state.db.session() as s:  # type: ignore[attr-defined]
        row = s.get(AiAnalysis, body["id"])
        assert row is not None and row.manifest_sha256
        ANCHORS[p.settings.storage_dir / row.pack_dir] = PackAnchor(
            pack_id=row.pack_id,
            analysis_id=row.analysis_id,
            kind=pk.PackKind(row.kind),
            manifest_sha256=row.manifest_sha256,
        )
        return p.settings.storage_dir / row.pack_dir


def register(p: Processed, pack_dir: Path) -> str:
    """Record a builder-made session pack in the DB, as the prepare endpoint would."""
    m = manifest(pack_dir)
    with p.client.app.state.db.session() as s:  # type: ignore[attr-defined]
        row = AiAnalysis(
            analysis_id=p.analysis_id,
            kind=m.kind.value,
            pack_id=m.pack_id,
            pack_dir=pack_dir.relative_to(p.settings.storage_dir).as_posix(),
            adapter="claude_code",
            data_status={k: v.value for k, v in m.data_status.items()},
            manifest_sha256=anchor(pack_dir).manifest_sha256,
        )
        s.add(row)
        s.commit()
        return row.id


def manifest(pack_dir: Path) -> pk.Manifest:
    return pk.Manifest.model_validate_json((pack_dir / pk.MANIFEST).read_text())


def valid_session_result(pack_dir: Path) -> dict[str, Any]:
    m = manifest(pack_dir)
    contact_img = next(i.id for i in m.images if i.stroke_id == "stk_2" and i.role == "contact")
    return {
        "kind": "session",
        "session_id": m.analysis_id,
        "pack_id": m.pack_id,
        "analysis_version": m.instructions_version,
        "adapter": "claude_code",
        "model": "test-model",
        "generated_at": "2026-11-18T22:00:00+01:00",
        "summary": "Sintesi della sessione (fixture).",
        "evaluated_strokes": {
            "detected": 3,
            "evaluable": 2,
            "forehand": 2,
            "backhand": 1,
            "unknown": 0,
        },
        "by_stroke_type": [
            {
                "stroke_type": "forehand",
                "detected": 2,
                "evaluable": 2,
                "summary": "Gomito a 120.5° nel colpo stk_2 contro 142° in stk_1.",
                "issue_ids": ["iss_1"],
            },
            {
                "stroke_type": "backhand",
                "detected": 1,
                "evaluable": 0,
                "summary": "Misura non osservabile.",
            },
        ],
        "issues": [
            {
                "id": "iss_1",
                "priority": 1,
                "name": "Braccio piegato all'impatto",
                "description": "Isolato: un solo colpo su due valutabili.",
                "stroke_type": "forehand",
                "nature": "uncertain",
                "occurrences": 1,
                "evaluable_strokes": 2,
                "percentage": 50.0,
                "relevance": "medium",
                "confidence": 0.5,
                "stroke_ids": ["stk_2"],
                "timestamps_s": [0.9],
                "observations": ["Angolo del gomito 120.5° (2D) in stk_2."],
                "evidence": [
                    {"kind": "measurement", "ref": "m_2"},
                    {"kind": "image", "ref": contact_img},
                    {"kind": "aggregate", "ref": "elbow_angle_projected"},
                ],
                "biomechanical_explanation": "Possibile contatto troppo vicino al corpo.",
                "alternative_explanations": ["Angolo di ripresa"],
                "recommended_exercises": [
                    {"name": "Shadow swing", "description": "Estensione al contatto"}
                ],
            }
        ],
        "positive_executions": [
            {
                "stroke_id": "stk_1",
                "stroke_type": "forehand",
                "timestamp_s": 0.3,
                "description": "Braccio esteso (142°).",
                "evidence": [{"kind": "measurement", "ref": "m_1"}],
            }
        ],
        "recommendations": [
            {
                "priority": 1,
                "title": "Spazio al contatto",
                "rationale": "Riduce la flessione del gomito.",
                "addresses_issue_ids": ["iss_1"],
            }
        ],
        "limitations": ["Dati sintetici."],
        "confidence": 0.5,
        "insufficient_evidence": False,
    }


def codes(raw: dict[str, Any] | str, pack_dir: Path) -> set[str]:
    text = raw if isinstance(raw, str) else json.dumps(raw)
    return {e.code for e in validate_result_text(text, pack_dir, anchor(pack_dir)).errors}


# ------------------------------------------------------------- Evidence Pack


def test_session_pack_without_strokes_declares_missing_capabilities(processed: Processed) -> None:
    before = _cv_hashes(processed.settings, processed.analysis_id)
    r = processed.client.post(f"/api/analyses/{processed.analysis_id}/ai/session")
    assert r.status_code == 201, r.json()
    body = r.json()
    pack_dir = api_pack(processed, body)
    m = manifest(pack_dir)
    assert m.data_status["strokes"] is pk.DataStatus.NOT_IMPLEMENTED
    assert m.data_status["measurements"] is pk.DataStatus.NOT_IMPLEMENTED
    strokes = json.loads((pack_dir / pk.STROKES).read_text())
    assert strokes == {"status": "not_implemented", "reason": strokes["reason"], "strokes": []}
    for name in (
        pk.SESSION_METADATA,
        pk.DATA_QUALITY,
        pk.MEASUREMENTS,
        pk.AGGREGATES,
        pk.ISSUE_CANDIDATES,
        pk.INSTRUCTIONS,
        pk.RESULT_SCHEMA,
    ):
        assert (pack_dir / name).is_file(), name
    # Overview frames, skeleton drawn (synthetic pose is detected).
    assert len(m.images) == 6 and all(i.role == "overview" and i.skeleton_drawn for i in m.images)
    # No video, no full pose, no absolute paths, no secrets.
    names = {p.name for p in pack_dir.rglob("*")}
    assert not names & {"pose.json", "proxy.mp4", "overlay.mp4", "original.mp4"}
    blob = (
        "".join(p.read_text() for p in pack_dir.glob("*.json"))
        + (pack_dir / pk.INSTRUCTIONS).read_text()
    )
    assert str(processed.settings.storage_dir) not in blob
    assert "api_key" not in blob.lower() and "ANTHROPIC" not in blob
    # Handoff for Claude Code.
    assert body["handoff"]["command"].startswith("/analizza-sessione ")
    assert body["handoff"]["command"].endswith(body["pack_id"])
    assert body["status"] == "awaiting_result"
    # CV results untouched.
    assert _cv_hashes(processed.settings, processed.analysis_id) == before


def test_pack_image_references_are_complete(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    m = manifest(pack_dir)
    files = {f.path for f in m.files}
    on_disk = {p.relative_to(pack_dir).as_posix() for p in (pack_dir / "evidence").iterdir()}
    assert {i.path for i in m.images} == on_disk  # every image referenced, none extra
    assert on_disk <= files
    for f in m.files:
        assert hashlib.sha256((pack_dir / f.path).read_bytes()).hexdigest() == f.sha256
    strokes = pk.StrokesFile.model_validate_json((pack_dir / pk.STROKES).read_text())
    image_ids = {i.id for i in m.images}
    for s in strokes.strokes:
        roles = {i.role for i in m.images if i.stroke_id == s.id}
        assert roles == {"preparation", "contact", "follow_through"}
        assert set(s.evidence_image_ids) <= image_ids and len(s.evidence_image_ids) == 3


def test_aggregates_use_observable_denominators(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    agg = pk.Aggregates.model_validate_json((pack_dir / pk.AGGREGATES).read_text())
    fh = next(a for a in agg.metrics if a.stroke_type == "forehand")
    bh = next(a for a in agg.metrics if a.stroke_type == "backhand")
    assert (fh.observable, fh.not_observable, fh.median) == (2, 0, 131.25)
    assert (bh.observable, bh.not_observable, bh.median) == (0, 1, None)
    assert agg.strokes_by_type == {"backhand": 1, "forehand": 2}


def test_stroke_ids_and_measurements_must_be_consistent(processed: Processed) -> None:
    for a in processed.analysis():
        videos = video_sources(a, processed.settings)
        meta = session_metadata(a)
        data = synthetic_stroke_data(processed.video_id)
        orphan = StrokeData(
            strokes=data.strokes,
            measurements=[*data.measurements, _meas("m_9", "stk_404", processed.video_id, 1.0)],
        )
        with pytest.raises(EvidencePackError, match="not in the pack"):
            build_session_pack(processed.settings.ai_dir / a.id, meta, videos, orphan)
        dup = StrokeData(strokes=[*data.strokes, data.strokes[0]], measurements=[])
        with pytest.raises(EvidencePackError, match="Duplicate stroke"):
            build_session_pack(processed.settings.ai_dir / a.id, meta, videos, dup)
        # Failed builds leave nothing behind.
        assert not list((processed.settings.ai_dir / a.id).glob(".tmp-*"))


def test_single_stroke_pack_from_interval_has_timeseries(processed: Processed) -> None:
    r = processed.client.post(
        f"/api/analyses/{processed.analysis_id}/ai/single-stroke",
        json={"start_s": 0.5, "end_s": 1.5, "declared_stroke_type": "forehand"},
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["kind"] == "single_stroke" and body["target"]["source"] == "user_interval"
    pack_dir = api_pack(processed, body)
    ts = pk.StrokeTimeseries.model_validate_json((pack_dir / pk.TIMESERIES).read_text())
    assert len(ts.samples) == 31  # frames 15..45 at 30 fps
    right = [s.angles["elbow_angle_projected:right"].value_deg for s in ts.samples]
    assert all(v is not None for v in right) and min(right) < max(right)  # type: ignore[type-var]
    strokes = pk.StrokesFile.model_validate_json((pack_dir / pk.STROKES).read_text()).strokes
    assert strokes[0].stroke_type == "forehand" and strokes[0].type_declared_by_user
    assert manifest(pack_dir).data_status["phases"] is pk.DataStatus.NOT_IMPLEMENTED
    assert len(manifest(pack_dir).images) == 8
    too_long = processed.client.post(
        f"/api/analyses/{processed.analysis_id}/ai/single-stroke",
        json={"start_s": 0.0, "end_s": 1.9, "video_id": "nope"},
    )
    assert too_long.status_code == 404


def test_single_stroke_interval_limits(processed: Processed) -> None:
    for a in processed.analysis():
        video = video_sources(a, processed.settings)[0]
        with pytest.raises(EvidencePackError, match="between 0 and"):
            build_single_stroke_pack(
                processed.settings.ai_dir / a.id,
                session_metadata(a),
                video,
                interval=TimeInterval(start_s=0, end_s=10),
            )


# ---------------------------------------------------------------- validation


def test_valid_result_passes_validation(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    outcome = validate_result_text(
        json.dumps(valid_session_result(pack_dir)), pack_dir, anchor(pack_dir)
    )
    assert outcome.ok, outcome.errors
    assert isinstance(outcome.result, AnalysisResult)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda r: r["issues"][0].update(stroke_ids=["stk_404"]), "unknown_stroke"),
        (lambda r: r["issues"][0].update(stroke_ids=["stk_3"]), "stroke_type_mismatch"),
        (lambda r: r["issues"][0].update(evaluable_strokes=3, percentage=33.3), "denominator"),
        (
            lambda r: r["issues"][0]["observations"].append("Gomito a 97° all'impatto"),
            "unsupported_number",
        ),
        (lambda r: r["issues"][0].update(timestamps_s=[1.9]), "timestamp_mismatch"),
        (
            lambda r: r["issues"][0]["evidence"].append({"kind": "image", "ref": "img_999"}),
            "unknown_evidence",
        ),
        (lambda r: r.update(pack_id="other"), "wrong_pack"),
        (lambda r: r["evaluated_strokes"].update(detected=4, unknown=1), "stroke_count"),
        (lambda r: r["issues"][0].update(percentage=40.0), "schema_error"),
        (lambda r: r["issues"][0].update(occurrences=3), "schema_error"),
        (lambda r: r.pop("summary"), "schema_error"),
    ],
)
def test_invalid_results_are_rejected(processed: Processed, mutate: Any, code: str) -> None:
    pack_dir = build_with_strokes(processed)
    result = valid_session_result(pack_dir)
    mutate(result)
    assert code in codes(result, pack_dir)


def test_malformed_json_is_reported(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    outcome = validate_result_text('{"kind": "session",', pack_dir, anchor(pack_dir))
    assert not outcome.ok and outcome.errors[0].code == "invalid_json"
    assert "line 1" in outcome.errors[0].message


def test_no_strokes_means_no_issues(processed: Processed) -> None:
    r = processed.client.post(f"/api/analyses/{processed.analysis_id}/ai/session").json()
    pack_dir = api_pack(processed, r)
    result = valid_session_result(build_with_strokes(processed))  # borrow a full result
    m = manifest(pack_dir)
    result.update(session_id=m.analysis_id, pack_id=m.pack_id)
    assert {"no_strokes_in_pack", "stroke_count"} <= codes(result, pack_dir)
    honest = {
        **result,
        "evaluated_strokes": {
            "detected": 0,
            "evaluable": 0,
            "forehand": 0,
            "backhand": 0,
            "unknown": 0,
        },
        "by_stroke_type": [],
        "issues": [],
        "positive_executions": [],
        "recommendations": [],
        "insufficient_evidence": True,
        "limitations": ["Rilevamento colpi non implementato."],
    }
    assert codes(honest, pack_dir) == set()


def test_missing_or_modified_evidence_blocks_import(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    raw = json.dumps(valid_session_result(pack_dir))
    image = next((pack_dir / "evidence").iterdir())
    image.unlink()
    assert "pack_file_missing" in codes(raw, pack_dir)
    pack_dir2 = build_with_strokes(processed)
    raw2 = json.dumps(valid_session_result(pack_dir2))
    (pack_dir2 / pk.STROKES).write_text('{"status": "available", "strokes": []}')
    assert "pack_file_modified" in codes(raw2, pack_dir2)
    outcome = validate_result_file(pack_dir2, anchor(pack_dir2))
    assert "result_missing" in {e.code for e in outcome.errors}


def test_session_and_single_stroke_share_the_result_contract(processed: Processed) -> None:
    session_pack = build_with_strokes(processed)
    assert validate_result_text(
        json.dumps(valid_session_result(session_pack)), session_pack, anchor(session_pack)
    ).ok
    # A stroke from the session pack can be analysed on its own without reprocessing.
    register(processed, session_pack)
    r = processed.client.post(
        f"/api/analyses/{processed.analysis_id}/ai/single-stroke", json={"stroke_id": "stk_2"}
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    pack_dir = api_pack(processed, body)
    m = manifest(pack_dir)
    assert m.target is not None and m.target.source == "detected"
    ts = pk.StrokeTimeseries.model_validate_json((pack_dir / pk.TIMESERIES).read_text())
    angle = next(
        s.angles["elbow_angle_projected:right"].value_deg
        for s in ts.samples
        if s.angles["elbow_angle_projected:right"].value_deg is not None
    )
    stroke_result = {
        "kind": "single_stroke",
        "session_id": m.analysis_id,
        "pack_id": m.pack_id,
        "analysis_version": m.instructions_version,
        "adapter": "claude_code",
        "model": "test-model",
        "generated_at": "2026-11-18T22:00:00Z",
        "summary": f"Gomito destro a {angle}° all'inizio dell'intervallo.",
        "evaluated_strokes": {
            "detected": 1,
            "evaluable": 1,
            "forehand": 1,
            "backhand": 0,
            "unknown": 0,
        },
        "issues": [],
        "limitations": ["Fasi non segmentate."],
        "confidence": 0.4,
        "insufficient_evidence": False,
        "target": m.target.model_dump(mode="json", include={"video_id", "stroke_id", "interval"}),
        "phases": [
            {
                "phase": "whole_interval",
                "observed": [f"Angolo iniziale {angle}°"],
                "hypotheses": ["Possibile estensione tardiva"],
                "evidence": [{"kind": "timeseries", "ref": "elbow_angle_projected:right"}],
            }
        ],
    }
    assert codes(stroke_result, pack_dir) == set()
    stroke_result["phases"][0]["phase"] = "contact"
    assert "unknown_phase" in codes(stroke_result, pack_dir)
    # The same result does not validate against the session pack (different contract use).
    assert "wrong_kind" in codes(stroke_result, session_pack)


# ---------------------------------------------------------------------- API flow


def test_import_flow_validates_and_keeps_cv_results(processed: Processed) -> None:
    c, aid = processed.client, processed.analysis_id
    cv_before = _cv_hashes(processed.settings, aid)
    results_before = c.get(f"/api/analyses/{aid}").json()["results"]
    body = c.post(f"/api/analyses/{aid}/ai/session").json()
    ai_id = body["id"]
    pack_dir = api_pack(processed, body)

    missing = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert missing.status_code == 422
    assert missing.json()["error"]["details"]["errors"][-1]["code"] == "result_missing"

    (pack_dir / RESULT_FILENAME).write_text("{not json")
    bad = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["code"] == "invalid_json"
    state = c.get(f"/api/ai-analyses/{ai_id}").json()
    assert state["status"] == "invalid" and state["result"] is None
    assert state["import_attempts"] == 2 and state["handoff"] is not None

    m = manifest(pack_dir)
    good = {
        "kind": "session",
        "session_id": aid,
        "pack_id": m.pack_id,
        "analysis_version": m.instructions_version,
        "adapter": "claude_code",
        "model": "test-model",
        "generated_at": "2026-11-18T22:00:00Z",
        "summary": "Nessun colpo disponibile: solo qualità della ripresa.",
        "evaluated_strokes": {
            "detected": 0,
            "evaluable": 0,
            "forehand": 0,
            "backhand": 0,
            "unknown": 0,
        },
        "limitations": ["Rilevamento colpi non implementato."],
        "confidence": 0.2,
        "insufficient_evidence": True,
    }
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(good))
    ok = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert ok.status_code == 200, ok.json()
    imported = ok.json()
    assert imported["status"] == "imported" and imported["model"] == "test-model"
    assert imported["result"]["insufficient_evidence"] is True
    assert imported["handoff"] is None and imported["validation_errors"] == []
    listed = c.get(f"/api/analyses/{aid}/ai").json()
    assert [x["id"] for x in listed] == [ai_id]
    img = c.get(imported["images"][0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    # CV results and analysis.results are untouched by the whole flow.
    assert _cv_hashes(processed.settings, aid) == cv_before
    assert c.get(f"/api/analyses/{aid}").json()["results"] == results_before

    # A later invalid file does not overwrite an imported result.
    (pack_dir / RESULT_FILENAME).write_text("[]")
    assert c.post(f"/api/ai-analyses/{ai_id}/import").status_code == 422
    after = c.get(f"/api/ai-analyses/{ai_id}").json()
    assert after["status"] == "imported" and after["result"] == imported["result"]


def test_prepare_requires_completed_processing(client: TestClient, basic_video: Path) -> None:
    with basic_video.open("rb") as fh:
        vid = client.post("/api/videos/upload", files={"file": ("a.mp4", fh, "video/mp4")}).json()
    aid = client.post(
        "/api/analyses", json={"mode": "session", "videos": [{"video_id": vid["id"]}]}
    ).json()["id"]
    r = client.post(f"/api/analyses/{aid}/ai/session")
    assert r.status_code == 409 and r.json()["error"]["code"] == "cv_not_completed"


def test_single_stroke_unknown_stroke_and_motivating_issue(processed: Processed) -> None:
    c, aid = processed.client, processed.analysis_id
    r = c.post(f"/api/analyses/{aid}/ai/single-stroke", json={"stroke_id": "stk_404"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "stroke_not_found"
    # Import a session result with an issue, then deep-dive on it.
    pack_dir = build_with_strokes(processed)
    ai_id = register(processed, pack_dir)
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(valid_session_result(pack_dir)))
    assert c.post(f"/api/ai-analyses/{ai_id}/import").status_code == 200
    deep = c.post(
        f"/api/analyses/{aid}/ai/single-stroke",
        json={"stroke_id": "stk_2", "source_ai_analysis_id": ai_id, "issue_id": "iss_1"},
    )
    assert deep.status_code == 201, deep.json()
    deep_dir = api_pack(processed, deep.json())
    target = manifest(deep_dir).target
    assert target is not None and target.motivating_issue is not None
    assert target.motivating_issue.issue_id == "iss_1"
    assert "Braccio piegato" in (deep_dir / pk.INSTRUCTIONS).read_text()


# ------------------------------------------------------------------- adapters


def test_claude_code_adapter_only_returns_instructions(tmp_path: Path) -> None:
    client = get_ai_client("claude_code")
    assert isinstance(client, ClaudeCodeAdapter) and client.interactive
    handoff = client.request_analysis(tmp_path, "storage/ai/a/p", pk.PackKind.SESSION)
    assert handoff.command == "/analizza-sessione storage/ai/a/p"
    assert handoff.result_path.endswith(RESULT_FILENAME)
    assert any("claude" in s for s in handoff.steps)


def test_anthropic_adapter_is_not_implemented(
    processed: Processed, tmp_path: Path, settings: Settings
) -> None:
    with pytest.raises(NotYetImplementedError):
        AnthropicAPIAdapter().request_analysis(tmp_path, "x", pk.PackKind.SESSION)
    with pytest.raises(ValueError, match="Unknown AI adapter"):
        get_ai_client("local_llm")


def test_validate_cli(processed: Processed) -> None:
    import os
    import subprocess
    import sys

    pack_dir = build_with_strokes(processed)
    script = Path(__file__).resolve().parents[2] / "scripts" / "ai_pack.py"
    env = {**os.environ, "TPA_STORAGE_DIR": str(processed.settings.storage_dir)}

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "validate", str(pack_dir)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    # Not registered in the database: the CLI says the manifest hash was not checked.
    unregistered = run()
    assert unregistered.returncode == 1 and "result_missing" in unregistered.stdout
    assert "pack_not_registered" in unregistered.stdout
    register(processed, pack_dir)
    missing = run()
    assert missing.returncode == 1 and "result_missing" in missing.stdout
    assert "pack_not_registered" not in missing.stdout
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(valid_session_result(pack_dir)))
    ok = run()
    assert ok.returncode == 0 and ok.stdout.startswith("OK"), ok.stdout + ok.stderr
    # The CLI uses the database anchor: a re-hashed manifest is caught here too.
    _tamper_strokes_and_rehash(pack_dir)
    forged = run()
    assert forged.returncode == 1 and "pack_manifest_modified" in forged.stdout


def _tamper_strokes_and_rehash(pack_dir: Path) -> None:
    """The audit's C2 attack: change strokes.json and update its hash in the manifest."""
    strokes = json.loads((pack_dir / pk.STROKES).read_text())
    # Silent change: a forged classification confidence, invisible to the cross-checks.
    strokes["strokes"][0]["type_confidence"] = 0.99
    (pack_dir / pk.STROKES).write_text(json.dumps(strokes))
    m = json.loads((pack_dir / pk.MANIFEST).read_text())
    for f in m["files"]:
        if f["path"] == pk.STROKES:
            f["sha256"] = hashlib.sha256((pack_dir / pk.STROKES).read_bytes()).hexdigest()
            f["bytes"] = (pack_dir / pk.STROKES).stat().st_size
    (pack_dir / pk.MANIFEST).write_text(json.dumps(m, indent=2))
