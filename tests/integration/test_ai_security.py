"""Regression tests for the Personal AI Analysis audit fixes.

* C1: path traversal and symlinks in an Evidence Pack.
* C2: manifest integrity anchored in the database, required files, legacy packs.
* I3 (minimal): no user free text (title, focus, original file name) in a pack.
* I2: append-only import history; schema migration of existing databases.

Every attack below reproduces a case that the audit proved on commit b655112.
Synthetic fixtures only: nothing here validates biomechanical quality.
"""

# ruff: noqa: F811  (the shared `processed` fixture is imported, then used as a parameter)
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from tennis_api.ai import pack as pk
from tennis_api.ai.result import RESULT_FILENAME
from tennis_api.ai.safe_files import PackFileError, is_safe_pack_path, read_pack_file
from tennis_api.ai.validation import (
    PackAnchor,
    ValidationOutcome,
    check_pack_integrity,
    read_verified_image,
    validate_result_file,
    validate_result_text,
)
from tennis_api.config import Settings
from tennis_api.db import Database
from tennis_api.models import AiAnalysis, AiAnalysisImport
from tennis_worker.main import Worker

from .test_ai_analysis import (
    Processed,
    _cv_hashes,
    _process,
    _tamper_strokes_and_rehash,
    anchor,
    api_pack,
    build_with_strokes,
    manifest,
    processed,  # noqa: F401 - pytest fixture
    register,
    valid_session_result,
)

# A file outside every pack that the audit managed to serve through the image endpoint.
OUTSIDE_SECRET = b"outside-the-pack-secret\n"


@pytest.fixture
def outside_file(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "hostname"
    path.parent.mkdir()
    path.write_bytes(OUTSIDE_SECRET)
    return path


def _no_strokes_result(
    m: pk.Manifest, summary: str = "Solo qualità della ripresa."
) -> dict[str, Any]:
    return {
        "kind": "session",
        "session_id": m.analysis_id,
        "pack_id": m.pack_id,
        "analysis_version": m.instructions_version,
        "adapter": "claude_code",
        "model": "test-model",
        "generated_at": "2026-11-18T22:00:00Z",
        "summary": summary,
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


def _prepare(p: Processed) -> tuple[str, Path]:
    body = p.client.post(f"/api/analyses/{p.analysis_id}/ai/session").json()
    return body["id"], api_pack(p, body)


def _error_codes(response: Any) -> set[str]:
    return {e["code"] for e in response.json()["error"]["details"]["errors"]}


def _imports(p: Processed, ai_id: str) -> list[dict[str, Any]]:
    with p.client.app.state.db.session() as s:  # type: ignore[attr-defined]
        rows = s.query(AiAnalysisImport).filter_by(ai_analysis_id=ai_id).all()
        return [
            {"id": r.id, "version": r.version, "result": r.result, "at": r.imported_at}
            for r in sorted(rows, key=lambda r: r.version)
        ]


def _rewrite_manifest(pack_dir: Path, edit: Any) -> None:
    m = json.loads((pack_dir / pk.MANIFEST).read_text())
    edit(m)
    (pack_dir / pk.MANIFEST).write_text(json.dumps(m, indent=2))


# ============================================================== C1: paths


UNSAFE_PATHS = [
    "/etc/hostname",
    "../../../../../../../etc/hostname",
    "evidence/../../../etc/hostname",
    "evidence/../manifest.json",
    "..%2F..%2Fetc%2Fhostname",
    "%2e%2e/%2e%2e/etc/hostname",
    "evidence/%2e%2e/x.jpg",
    "evidence\\..\\..\\etc\\hostname",
    "C:/Windows/win.ini",
    "\uff0e\uff0e/etc/hostname",  # fullwidth dots
    "evidence/./img_001.jpg",
    "evidence//img_001.jpg",
    "./manifest.json",
    ".hidden",
    "evidence/img_001.jpg\x00.png",
    "~/secret",
    "",
]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_unsafe_pack_paths_are_rejected_everywhere(path: str, tmp_path: Path) -> None:
    assert not is_safe_pack_path(path)
    with pytest.raises(PackFileError) as exc:
        read_pack_file(tmp_path, path)
    assert exc.value.code == "pack_path_unsafe"
    with pytest.raises(ValueError):
        pk.PackFile(path=path, sha256="0" * 64, bytes=1)


def test_image_paths_must_stay_in_evidence() -> None:
    base = {
        "id": "img_x",
        "video_id": "v",
        "frame_index": 0,
        "timestamp_s": 0.0,
        "role": "overview",
        "skeleton_drawn": True,
        "width": 1,
        "height": 1,
    }
    assert pk.ImageRef(path="evidence/img_001.jpg", **base)
    for bad in ("manifest.json", "/etc/hostname", "evidence/../strokes.json"):
        with pytest.raises(ValueError):
            pk.ImageRef(path=bad, **base)


def test_image_endpoint_never_serves_files_outside_the_pack(
    processed: Processed, outside_file: Path
) -> None:
    """Audit C1 proof: images[0].path pointing to /etc/hostname was served with 200."""
    c = processed.client
    ai_id, pack_dir = _prepare(processed)
    body = c.get(f"/api/ai-analyses/{ai_id}").json()
    img = body["images"][0]
    legit = c.get(img["url"])
    assert legit.status_code == 200 and legit.headers["content-type"] == "image/jpeg"
    assert legit.content == (pack_dir / img["path"]).read_bytes()

    secret_hash = hashlib.sha256(OUTSIDE_SECRET).hexdigest()
    for target in (
        "/etc/hostname",
        str(outside_file),
        "../../../../../../../etc/hostname",
        os.path.relpath(outside_file, pack_dir),
    ):

        def edit(m: dict[str, Any], target: str = target) -> None:
            m["images"][0]["path"] = target
            m["files"].append({"path": target, "sha256": secret_hash, "bytes": 24})

        original = (pack_dir / pk.MANIFEST).read_bytes()
        _rewrite_manifest(pack_dir, edit)
        r = c.get(img["url"])
        assert r.status_code == 404, target
        assert r.json()["error"]["code"] == "image_not_found"
        assert OUTSIDE_SECRET not in r.content and str(pack_dir) not in r.text
        (pack_dir / RESULT_FILENAME).write_text(
            json.dumps(_no_strokes_result(manifest_raw(original)))
        )
        imp = c.post(f"/api/ai-analyses/{ai_id}/import")
        assert imp.status_code == 422 and "pack_manifest_modified" in _error_codes(imp)
        (pack_dir / pk.MANIFEST).write_bytes(original)
        (pack_dir / RESULT_FILENAME).unlink()

    # Encoded traversal in the URL is just an unknown image id.
    for raw in ("..%2F..%2F..%2Fetc%2Fhostname", "%2e%2e%2f%2e%2e%2fetc%2fhostname"):
        r = c.get(f"/api/ai-analyses/{ai_id}/images/{raw}")
        assert r.status_code == 404 and OUTSIDE_SECRET not in r.content


def manifest_raw(raw: bytes) -> pk.Manifest:
    return pk.Manifest.model_validate_json(raw)


def test_unsafe_manifest_paths_fail_even_if_the_anchor_matched(
    processed: Processed, outside_file: Path
) -> None:
    """Defence in depth: path checks do not rely on the database anchor."""
    pack_dir = build_with_strokes(processed)

    def edit(m: dict[str, Any]) -> None:
        m["images"][0]["path"] = str(outside_file)
        m["files"].append(
            {
                "path": str(outside_file),
                "sha256": hashlib.sha256(OUTSIDE_SECRET).hexdigest(),
                "bytes": len(OUTSIDE_SECRET),
            }
        )

    _rewrite_manifest(pack_dir, edit)
    raw = (pack_dir / pk.MANIFEST).read_bytes()
    forged_anchor = PackAnchor(
        pack_id=anchor(pack_dir).pack_id,
        analysis_id=anchor(pack_dir).analysis_id,
        kind=pk.PackKind.SESSION,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )
    outcome = ValidationOutcome()
    assert check_pack_integrity(pack_dir, outcome, forged_anchor) is None
    assert "pack_manifest_invalid" in {e.code for e in outcome.errors}
    assert read_verified_image(pack_dir, forged_anchor, "img_001") is None
    # The CLI path (no anchor available) rejects it too.
    unanchored = ValidationOutcome()
    assert check_pack_integrity(pack_dir, unanchored, None, require_anchor=False) is None
    assert "pack_manifest_invalid" in {e.code for e in unanchored.errors}
    for e in outcome.errors + unanchored.errors:
        assert str(outside_file) not in e.message and str(pack_dir) not in e.message


def test_symlinks_inside_the_pack_are_rejected(processed: Processed, outside_file: Path) -> None:
    c = processed.client
    ai_id, pack_dir = _prepare(processed)
    img = c.get(f"/api/ai-analyses/{ai_id}").json()["images"][0]
    image_path = pack_dir / img["path"]
    saved = image_path.read_bytes()
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(_no_strokes_result(manifest(pack_dir))))

    # 1. An evidence file replaced by a link to a file outside the pack.
    image_path.unlink()
    image_path.symlink_to(outside_file)
    r = c.get(img["url"])
    assert r.status_code == 404 and OUTSIDE_SECRET not in r.content
    with pytest.raises(PackFileError) as exc:
        read_pack_file(pack_dir, img["path"])
    assert exc.value.code == "pack_symlink"
    imp = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert imp.status_code == 422 and "pack_symlink" in _error_codes(imp)
    image_path.unlink()
    image_path.write_bytes(saved)

    # 2. The evidence folder replaced by a link to a folder outside the pack.
    outside_dir = outside_file.parent / "evidence"
    shutil.copytree(pack_dir / pk.EVIDENCE_DIR, outside_dir)
    shutil.rmtree(pack_dir / pk.EVIDENCE_DIR)
    (pack_dir / pk.EVIDENCE_DIR).symlink_to(outside_dir, target_is_directory=True)
    assert c.get(img["url"]).status_code == 404
    imp = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert imp.status_code == 422 and "pack_symlink" in _error_codes(imp)
    (pack_dir / pk.EVIDENCE_DIR).unlink()
    shutil.copytree(outside_dir, pack_dir / pk.EVIDENCE_DIR)

    # 3. analysis_result.json as a link (e.g. to another pack's result or any file).
    real = outside_file.parent / "result.json"
    real.write_text((pack_dir / RESULT_FILENAME).read_text())
    (pack_dir / RESULT_FILENAME).unlink()
    (pack_dir / RESULT_FILENAME).symlink_to(real)
    imp = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert imp.status_code == 422 and "pack_symlink" in _error_codes(imp)
    (pack_dir / RESULT_FILENAME).unlink()

    # 4. An extra link next to legitimate files.
    (pack_dir / "notes.txt").symlink_to(outside_file)
    (pack_dir / RESULT_FILENAME).write_text(real.read_text())
    imp = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert imp.status_code == 422 and "pack_symlink" in _error_codes(imp)
    (pack_dir / "notes.txt").unlink()

    # Restored pack: legitimate image and import work again.
    assert c.get(img["url"]).status_code == 200
    assert c.post(f"/api/ai-analyses/{ai_id}/import").status_code == 200


def test_symlinked_pack_folder_is_rejected(processed: Processed, tmp_path: Path) -> None:
    pack_dir = build_with_strokes(processed)
    elsewhere = tmp_path / "elsewhere"
    shutil.move(pack_dir, elsewhere)
    pack_dir.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PackFileError) as exc:
        read_pack_file(pack_dir, pk.MANIFEST)
    assert exc.value.code == "pack_symlink"


# =========================================================== C2: integrity


def test_modified_file_without_manifest_update_is_rejected(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    ai_id = register(processed, pack_dir)
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(valid_session_result(pack_dir)))
    strokes = json.loads((pack_dir / pk.STROKES).read_text())
    strokes["strokes"][0]["stroke_type"] = "backhand"
    (pack_dir / pk.STROKES).write_text(json.dumps(strokes))
    r = processed.client.post(f"/api/ai-analyses/{ai_id}/import")
    assert r.status_code == 422 and "pack_file_modified" in _error_codes(r)
    assert _imports(processed, ai_id) == []


def test_modified_file_and_rehashed_manifest_is_rejected(processed: Processed) -> None:
    """Audit C2 proof: a forged stroke + re-hashed manifest was imported with 200."""
    c, aid = processed.client, processed.analysis_id
    cv_before = _cv_hashes(processed.settings, aid)
    results_before = c.get(f"/api/analyses/{aid}").json()["results"]
    pack_dir = build_with_strokes(processed)
    ai_id = register(processed, pack_dir)
    forged = valid_session_result(pack_dir)
    forged["issues"][0]["confidence"] = 0.95
    forged["issues"][0]["nature"] = "technical_error"
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(forged))
    _tamper_strokes_and_rehash(pack_dir)
    # Without the database anchor, the pack would look consistent: that was the bug.
    unanchored = validate_result_file(pack_dir, None, require_anchor=False)
    assert unanchored.ok, unanchored.errors

    r = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert r.status_code == 422 and "pack_manifest_modified" in _error_codes(r)
    assert _imports(processed, ai_id) == []
    state = c.get(f"/api/ai-analyses/{ai_id}").json()
    assert state["status"] == "invalid" and state["result"] is None
    assert state["pack_status"] == "modified" and state["handoff"] is None
    assert state["images"] == []
    # The forged stroke cannot be used as a single-stroke target either.
    deep = c.post(f"/api/analyses/{aid}/ai/single-stroke", json={"stroke_id": "stk_1"})
    assert deep.status_code == 404 and deep.json()["error"]["code"] == "stroke_not_found"
    assert _cv_hashes(processed.settings, aid) == cv_before
    assert c.get(f"/api/analyses/{aid}").json()["results"] == results_before


def test_unmodified_pack_is_imported(processed: Processed) -> None:
    c, aid = processed.client, processed.analysis_id
    cv_before = _cv_hashes(processed.settings, aid)
    pack_dir = build_with_strokes(processed)
    ai_id = register(processed, pack_dir)
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(valid_session_result(pack_dir)))
    r = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "imported" and body["pack_status"] == "verified"
    assert len(body["imports"]) == 1 and body["imports"][0]["version"] == 1
    # The stroke of a verified pack can be deep-dived.
    deep = c.post(f"/api/analyses/{aid}/ai/single-stroke", json={"stroke_id": "stk_2"})
    assert deep.status_code == 201
    assert _cv_hashes(processed.settings, aid) == cv_before


def test_prepare_stores_the_manifest_hash_in_the_database(processed: Processed) -> None:
    ai_id, pack_dir = _prepare(processed)
    with processed.client.app.state.db.session() as s:  # type: ignore[attr-defined]
        row = s.get(AiAnalysis, ai_id)
        assert row is not None
        stored = row.manifest_sha256
    assert stored == hashlib.sha256((pack_dir / pk.MANIFEST).read_bytes()).hexdigest()
    # Nothing in the pack folder holds the manifest hash.
    for f in pack_dir.rglob("*"):
        if f.is_file():
            assert stored.encode() not in f.read_bytes()


def test_required_files_and_unexpected_files(processed: Processed) -> None:
    pack_dir = build_with_strokes(processed)
    raw = json.dumps(valid_session_result(pack_dir))

    # Required file dropped from the integrity list (CLI path without anchor).
    def drop(m: dict[str, Any]) -> None:
        m["files"] = [f for f in m["files"] if f["path"] != pk.STROKES]

    _rewrite_manifest(pack_dir, drop)
    out = validate_result_text(raw, pack_dir, None, require_anchor=False)
    assert "pack_required_file_missing" in {e.code for e in out.errors}
    # With the anchor, any manifest edit is caught first.
    assert "pack_manifest_modified" in {
        e.code for e in validate_result_text(raw, pack_dir, anchor(pack_dir)).errors
    }

    # A file the app did not create is an error, not a warning.
    pack2 = build_with_strokes(processed)
    (pack2 / "extra.json").write_text("{}")
    out2 = validate_result_text(json.dumps(valid_session_result(pack2)), pack2, anchor(pack2))
    assert "pack_unexpected_file" in {e.code for e in out2.errors}
    (pack2 / "extra.json").unlink()
    assert validate_result_text(json.dumps(valid_session_result(pack2)), pack2, anchor(pack2)).ok


def test_pack_cannot_be_moved_to_another_analysis_or_id(processed: Processed) -> None:
    pack_a = build_with_strokes(processed)
    pack_b = build_with_strokes(processed)
    raw_a = json.dumps(valid_session_result(pack_a))
    a = anchor(pack_a)
    for wrong in (
        PackAnchor(a.pack_id, "another-analysis", a.kind, a.manifest_sha256),
        PackAnchor("session-other", a.analysis_id, a.kind, a.manifest_sha256),
        PackAnchor(a.pack_id, a.analysis_id, pk.PackKind.SINGLE_STROKE, a.manifest_sha256),
    ):
        assert "pack_identity_mismatch" in {
            e.code for e in validate_result_text(raw_a, pack_a, wrong).errors
        }
    # Pack A's files copied over pack B's folder: B's anchor rejects them.
    shutil.rmtree(pack_b)
    shutil.copytree(pack_a, pack_b)
    errors = {e.code for e in validate_result_text(raw_a, pack_b, anchor(pack_b)).errors}
    assert "pack_manifest_modified" in errors
    # A's own anchor on B's folder: the folder name is not the pack id.
    assert "pack_identity_mismatch" in {
        e.code for e in validate_result_text(raw_a, pack_b, anchor(pack_a)).errors
    }


def test_legacy_pack_without_anchor_must_be_regenerated(processed: Processed) -> None:
    c = processed.client
    ai_id, pack_dir = _prepare(processed)
    with c.app.state.db.session() as s:  # type: ignore[attr-defined]
        row = s.get(AiAnalysis, ai_id)
        assert row is not None
        row.manifest_sha256 = None  # as created before this fix
        s.commit()
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(_no_strokes_result(manifest(pack_dir))))
    state = c.get(f"/api/ai-analyses/{ai_id}").json()
    assert state["pack_status"] == "legacy"
    assert state["handoff"] is None and state["images"] == []
    assert c.get(f"/api/ai-analyses/{ai_id}/images/img_001").status_code == 404
    r = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert r.status_code == 422 and _error_codes(r) == {"pack_legacy_unanchored"}
    assert _imports(processed, ai_id) == []
    # A new pack for the same analysis works.
    new_id, new_dir = _prepare(processed)
    (new_dir / RESULT_FILENAME).write_text(json.dumps(_no_strokes_result(manifest(new_dir))))
    assert c.post(f"/api/ai-analyses/{new_id}/import").status_code == 200


# ============================================================ I3: privacy


def test_packs_contain_no_user_free_text(
    client: TestClient,
    synthetic_worker: Worker,
    basic_video: Path,
    settings: Settings,
    tmp_path: Path,
) -> None:
    named = tmp_path / "Mario_Rossi_allenamento.mp4"
    shutil.copy(basic_video, named)
    with named.open("rb") as fh:
        video_id = client.post(
            "/api/videos/upload", files={"file": (named.name, fh, "video/mp4")}
        ).json()["id"]
    aid = client.post(
        "/api/analyses",
        json={
            "mode": "session",
            "title": "Lezione con coach Bianchi",
            "focus": "dritto, mi fa male il gomito (visita dal Dott. Verdi)",
            "videos": [{"video_id": video_id}],
        },
    ).json()["id"]
    client.post(f"/api/analyses/{aid}/start")
    assert synthetic_worker.run_once()
    p = Processed(client, settings, aid, video_id)
    session = client.post(f"/api/analyses/{aid}/ai/session")
    stroke = client.post(
        f"/api/analyses/{aid}/ai/single-stroke", json={"start_s": 0.5, "end_s": 1.5}
    )
    assert session.status_code == 201 and stroke.status_code == 201
    for body in (session.json(), stroke.json()):
        pack_dir = api_pack(p, body)
        meta = json.loads((pack_dir / pk.SESSION_METADATA).read_text())
        assert not {"title", "focus"} & set(meta)
        assert all("original_filename" not in v for v in meta["videos"])
        assert meta["videos"][0]["video_id"] == video_id
        for f in (f for f in pack_dir.rglob("*") if f.is_file()):
            data = f.read_bytes()
            for secret in (
                b"Mario",
                b"Rossi",
                b"allenamento",
                b"Bianchi",
                b"Verdi",
                b"mi fa male",
            ):
                assert secret not in data, (f.name, secret)
            assert str(settings.storage_dir).encode() not in data
        instructions = (pack_dir / pk.INSTRUCTIONS).read_text().lower()
        for word in ("titolo", "focus", "nome del file", "original_filename"):
            assert word not in instructions
    skill = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "analizza-sessione"
    skill_text = (skill / "SKILL.md").read_text().lower()
    assert "titolo" not in skill_text and "original_filename" not in skill_text


# ======================================================= I2: import history


def test_each_valid_import_is_a_new_version(processed: Processed) -> None:
    c = processed.client
    ai_id, pack_dir = _prepare(processed)
    m = manifest(pack_dir)
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(_no_strokes_result(m, "Prima versione.")))
    first = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert first.status_code == 200
    v1 = _imports(processed, ai_id)
    assert [i["version"] for i in v1] == [1]

    (pack_dir / RESULT_FILENAME).write_text(json.dumps(_no_strokes_result(m, "Seconda versione.")))
    second = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert second.status_code == 200
    history = _imports(processed, ai_id)
    assert [i["version"] for i in history] == [1, 2]
    assert history[0] == v1[0]  # version 1 is untouched
    assert history[1]["result"]["summary"] == "Seconda versione."
    body = second.json()
    assert [i["version"] for i in body["imports"]] == [1, 2]
    assert body["imports"][0]["result"]["summary"] == "Prima versione."
    assert body["result"]["summary"] == "Seconda versione."  # latest
    assert (
        body["imports"][1]["result_sha256"]
        == hashlib.sha256((pack_dir / RESULT_FILENAME).read_bytes()).hexdigest()
    )

    # The same file again is not a new version.
    dup = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ai_result_already_imported"
    assert _imports(processed, ai_id) == history

    # An invalid attempt changes nothing in the history.
    (pack_dir / RESULT_FILENAME).write_text("{not json")
    bad = c.post(f"/api/ai-analyses/{ai_id}/import")
    assert bad.status_code == 422
    assert _imports(processed, ai_id) == history
    state = c.get(f"/api/ai-analyses/{ai_id}").json()
    assert state["status"] == "imported" and state["result"]["summary"] == "Seconda versione."
    assert state["validation_errors"][0]["code"] == "invalid_json"
    assert state["last_rejected_at"] is not None
    assert len(state["imports"]) == 2

    # A deep-dive can point to a specific version (here v1 has no issues: 404).
    r = c.post(
        f"/api/analyses/{processed.analysis_id}/ai/single-stroke",
        json={
            "stroke_id": "stk_1",
            "source_ai_analysis_id": ai_id,
            "source_import_id": history[0]["id"],
            "issue_id": "iss_1",
        },
    )
    assert r.status_code == 404


def test_deep_dive_records_the_source_version(processed: Processed) -> None:
    c, aid = processed.client, processed.analysis_id
    pack_dir = build_with_strokes(processed)
    ai_id = register(processed, pack_dir)
    (pack_dir / RESULT_FILENAME).write_text(json.dumps(valid_session_result(pack_dir)))
    v1 = c.post(f"/api/ai-analyses/{ai_id}/import").json()["imports"][0]
    deep = c.post(
        f"/api/analyses/{aid}/ai/single-stroke",
        json={
            "stroke_id": "stk_2",
            "source_ai_analysis_id": ai_id,
            "source_import_id": v1["id"],
            "issue_id": "iss_1",
        },
    )
    assert deep.status_code == 201, deep.json()
    target = manifest(api_pack(processed, deep.json())).target
    assert target is not None and target.motivating_issue is not None
    assert target.motivating_issue.source_import_id == v1["id"]


def test_migration_keeps_results_imported_before_the_history(
    settings: Settings, synthetic_worker: Worker, client: TestClient, basic_video: Path
) -> None:
    p = _process(client, synthetic_worker, basic_video, settings)
    ai_id, pack_dir = _prepare(p)
    legacy_result = _no_strokes_result(manifest(pack_dir), "Importato prima dello storico.")
    db: Database = client.app.state.db  # type: ignore[attr-defined]
    # Recreate the schema of commit b655112: no anchor columns, no history table.
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE ai_analysis_imports"))
        conn.execute(text("DELETE FROM schema_migrations"))
        conn.execute(text("ALTER TABLE ai_analyses DROP COLUMN manifest_sha256"))
        conn.execute(text("ALTER TABLE ai_analyses DROP COLUMN last_rejected_at"))
        conn.execute(
            text(
                "UPDATE ai_analyses SET status = 'imported', result = :r, model = 'old-model', "
                "imported_at = '2026-11-01 10:00:00.000000' WHERE id = :id"
            ),
            {"r": json.dumps(legacy_result), "id": ai_id},
        )
    applied = db.migrate()
    # Every migration recorded after b655112 runs again (M1.10 added the second one).
    assert applied == [
        "2026_11_18_ai_integrity_and_history",
        "2026_11_19_analysis_player_handedness",
    ]
    assert db.migrate() == []  # idempotent
    cols = {col["name"] for col in inspect(db.engine).get_columns("ai_analyses")}
    assert {"manifest_sha256", "last_rejected_at"} <= cols
    with db.engine.connect() as conn:
        original = conn.execute(
            text("SELECT result, model FROM ai_analyses WHERE id = :id"), {"id": ai_id}
        ).one()
    assert json.loads(original.result) == legacy_result and original.model == "old-model"

    state = client.get(f"/api/ai-analyses/{ai_id}").json()
    assert state["pack_status"] == "legacy" and state["status"] == "imported"
    assert len(state["imports"]) == 1
    v1 = state["imports"][0]
    assert v1["version"] == 1 and v1["origin"] == "legacy_migration"
    assert v1["result"] == legacy_result and v1["model"] == "old-model"
    assert state["result"] == legacy_result
    # The legacy pack cannot receive new imports; its history stays readable.
    r = client.post(f"/api/ai-analyses/{ai_id}/import")
    assert r.status_code == 422 and "pack_legacy_unanchored" in _error_codes(r)
    assert len(client.get(f"/api/ai-analyses/{ai_id}").json()["imports"]) == 1
