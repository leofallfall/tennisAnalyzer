"""strokes.json 1.0: versioned reader, models, the eight invariants, canonical writer.

All data is synthetic: ``tests/fixtures/strokes/example_v1_0.json`` is written by hand
(producer ``hand_written_example``) and is not the output of any detector.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tennis_cv.strokes.artifact import (
    SCHEMA_VERSION,
    StrokesFile,
    StrokesFormatError,
    StrokesInputMismatchError,
    StrokesInvariantError,
    StrokesSchemaTooNewError,
    StrokesSchemaUnsupportedError,
    check_inputs,
    check_invariants,
    dump_strokes,
    file_sha256,
    load_strokes,
    params_sha256,
    parse_strokes,
    same_output,
    verify_inputs,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "strokes" / "example_v1_0.json"

Doc = dict[str, Any]


@pytest.fixture
def raw() -> Doc:
    data: Doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return data


def _text(d: Doc) -> str:
    return json.dumps(d)


def _event(d: Doc, eid: str) -> Doc:
    return next(e for e in d["events"] if e["id"] == eid)


def _peak(d: Doc, pid: str) -> Doc:
    return next(p for p in d["wrist_peaks"] if p["id"] == pid)


def _violations(d: Doc) -> list[str]:
    return check_invariants(StrokesFile.model_validate(d))


# ----------------------------------------------------------------- example and writer


def test_example_is_valid_and_canonical(raw: Doc) -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    doc = parse_strokes(text)
    assert doc.schema_version == SCHEMA_VERSION
    assert check_invariants(doc) == []
    assert dump_strokes(doc) == text  # byte-identical round trip
    assert load_strokes(EXAMPLE) == doc


def test_writer_is_deterministic_and_orders_lists(raw: Doc) -> None:
    shuffled = copy.deepcopy(raw)
    shuffled["events"].reverse()
    shuffled["wrist_peaks"].reverse()
    shuffled["rules"].reverse()
    a = dump_strokes(StrokesFile.model_validate(raw))
    b = dump_strokes(StrokesFile.model_validate(shuffled))
    assert a == b


def test_writer_rounds_times_to_four_decimals(raw: Doc) -> None:
    # a float with noise beyond 1e-4 is written rounded: same bytes as the clean value
    noisy = copy.deepcopy(raw)
    _peak(noisy, "p0002")["speed"]["value"] = 9.81 + 1e-9
    assert dump_strokes(StrokesFile.model_validate(noisy)) == EXAMPLE.read_text(encoding="utf-8")


def test_created_at_is_the_only_field_ignored_by_repeatability(raw: Doc) -> None:
    later = copy.deepcopy(raw)
    later["created_at"] = "2027-01-01T00:00:00Z"
    a, b = StrokesFile.model_validate(raw), StrokesFile.model_validate(later)
    assert dump_strokes(a) != dump_strokes(b)
    assert same_output(a, b)
    other = copy.deepcopy(raw)
    _peak(other, "p0003")["speed"]["value"] = 2.5
    assert not same_output(a, StrokesFile.model_validate(other))


def test_writer_refuses_invalid_document(raw: Doc) -> None:
    _event(raw, "e0002")["reasons"] = []
    with pytest.raises(StrokesInvariantError):
        dump_strokes(StrokesFile.model_validate(raw))


# ----------------------------------------------------------------- versions (P5)


@pytest.mark.parametrize(
    ("version", "error", "code"),
    [
        ("1.1", StrokesSchemaTooNewError, "strokes_schema_too_new"),
        ("2.0", StrokesSchemaUnsupportedError, "strokes_schema_unsupported"),
        ("1", StrokesSchemaUnsupportedError, "strokes_schema_unsupported"),
        (None, StrokesSchemaUnsupportedError, "strokes_schema_unsupported"),
    ],
)
def test_version_errors_come_before_field_errors(
    raw: Doc, version: str | None, error: type[Exception], code: str
) -> None:
    raw["schema_version"] = version
    raw["a_field_from_the_future"] = 1  # would be a field error if the version passed
    with pytest.raises(error) as exc:
        parse_strokes(_text(raw))
    assert getattr(exc.value, "code", None) == code


def test_other_schema_name_is_unsupported(raw: Doc) -> None:
    raw["schema"] = "tpa.something_else"
    with pytest.raises(StrokesSchemaUnsupportedError):
        parse_strokes(_text(raw))


def test_newer_reader_reads_older_minor(raw: Doc) -> None:
    # a 1.1 reader (simulated) still reads 1.0 files: backward compatibility only
    doc = parse_strokes(_text(raw), supported_minor=1)
    assert doc.schema_version == "1.0"


def test_unknown_fields_are_rejected_never_ignored(raw: Doc) -> None:
    _event(raw, "e0001")["new_field"] = True
    with pytest.raises(StrokesFormatError) as exc:
        parse_strokes(_text(raw))
    assert exc.value.code == "strokes_format_invalid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: _peak(d, "p0001").update(t_s=float("nan")),
        lambda d: _event(d, "e0001").update(status="maybe"),
        lambda d: _event(d, "e0001")["regions"]["legs"].update(state="bad"),
        lambda d: _event(d, "e0001")["classification"].update(stroke_type="forehand"),
        lambda d: _event(d, "e0001").update(estimated_contact_s=19.37),
        lambda d: d["inputs"]["pose"].update(sha256="xyz"),
        lambda d: _peak(d, "p0001")["provenance"].update(velocity_state="smoothed"),
    ],
)
def test_model_rejects_bad_values(raw: Doc, mutate: Callable[[Doc], None]) -> None:
    mutate(raw)
    with pytest.raises(StrokesFormatError):
        parse_strokes(json.dumps(raw, allow_nan=True))


def test_invalid_json_is_a_format_error() -> None:
    with pytest.raises(StrokesFormatError):
        parse_strokes("{not json")
    with pytest.raises(StrokesFormatError):
        parse_strokes("[]")


# ----------------------------------------------------------------- the eight invariants


def _inv1_peak_in_two_events(d: Doc) -> None:
    _event(d, "e0002")["grouping"]["peak_ids"].append("p0002")


def _inv1_event_id_disagrees(d: Doc) -> None:
    _peak(d, "p0001")["event_id"] = "e0002"


def _inv1_discarded_without_reason(d: Doc) -> None:
    _peak(d, "p0003")["reasons"] = []


def _inv2_window_not_peak_span(d: Doc) -> None:
    _event(d, "e0001")["time"]["window_proxy_frames"] = [578, 581]


def _inv2_evidence_mismatch(d: Doc) -> None:
    _event(d, "e0001")["evidence"]["left"] = {"state": "no_peak", "peak_ids": []}


def _inv3_double_count(d: Doc) -> None:
    # move the right-wrist peak of e0001 into its own event, 0.0667 s after the left one:
    # one gesture would become two events
    e1 = _event(d, "e0001")
    split = copy.deepcopy(e1)
    split.update(id="e0009")
    split["grouping"].update(peak_ids=["p0002"], links=[], span_s=0.0)
    split["evidence"]["left"] = {"state": "no_peak", "peak_ids": []}
    split["time"].update(window_proxy_frames=[581, 581], window_t_s=[19.3701, 19.3701])
    split["trace"] = [
        {
            "step": 1,
            "rule": "decision",
            "subject": "e0009",
            "inputs": {},
            "param": None,
            "outcome": "accepted",
        }
    ]
    e1["grouping"].update(peak_ids=["p0001"], links=[], span_s=0.0)
    e1["evidence"]["right"] = {"state": "no_peak", "peak_ids": []}
    e1["representative_wrist"] = {"value": "left", "reasons": []}
    e1["time"].update(
        ref_proxy_frame=579,
        ref_t_s=19.3034,
        window_proxy_frames=[579, 579],
        window_t_s=[19.3034, 19.3034],
    )
    _peak(d, "p0002")["event_id"] = "e0009"
    d["events"].append(split)


def _inv3_link_longer_than_g(d: Doc) -> None:
    g = _event(d, "e0002")["grouping"]
    g["g_s"] = 0.1
    for e in d["events"]:
        e["grouping"]["g_s"] = 0.1


def _inv4_reference_not_a_peak(d: Doc) -> None:
    _event(d, "e0001")["time"].update(ref_proxy_frame=580, ref_t_s=19.3368)


def _inv4_undetermined_with_time(d: Doc) -> None:
    _event(d, "e0002")["time"].update(ref_proxy_frame=1160, ref_t_s=38.6672)


def _inv5_uncertain_without_reason(d: Doc) -> None:
    _event(d, "e0002")["reasons"] = []


def _inv5_reason_not_in_catalog(d: Doc) -> None:
    _event(d, "e0003")["reasons"] = ["made_up_rule"]


def _inv6_reliability_above_worst(d: Doc) -> None:
    _event(d, "e0001")["reliability"]["state"] = "good"


def _inv6_limited_by_unused(d: Doc) -> None:
    _event(d, "e0001")["reliability"]["limited_by"] = ["legs"]


def _inv7_source_without_map(d: Doc) -> None:
    d["inputs"]["timemap"] = {"file": None, "sha256": None, "status": "missing"}


def _inv7_proxy_with_ok_map(d: Doc) -> None:
    d["time_basis"] = "proxy"


def _inv8_last_step_not_decision(d: Doc) -> None:
    _event(d, "e0001")["trace"].pop()


def _inv8_decision_differs(d: Doc) -> None:
    _event(d, "e0002")["trace"][-1]["outcome"] = "accepted"


@pytest.mark.parametrize(
    ("invariant", "mutate"),
    [
        (1, _inv1_peak_in_two_events),
        (1, _inv1_event_id_disagrees),
        (1, _inv1_discarded_without_reason),
        (2, _inv2_window_not_peak_span),
        (2, _inv2_evidence_mismatch),
        (3, _inv3_double_count),
        (3, _inv3_link_longer_than_g),
        (4, _inv4_reference_not_a_peak),
        (4, _inv4_undetermined_with_time),
        (5, _inv5_uncertain_without_reason),
        (5, _inv5_reason_not_in_catalog),
        (6, _inv6_reliability_above_worst),
        (6, _inv6_limited_by_unused),
        (7, _inv7_source_without_map),
        (7, _inv7_proxy_with_ok_map),
        (8, _inv8_last_step_not_decision),
        (8, _inv8_decision_differs),
    ],
)
def test_each_invariant_is_detected(
    raw: Doc, invariant: int, mutate: Callable[[Doc], None]
) -> None:
    mutate(raw)
    found = _violations(raw)
    assert any(p.startswith(f"I{invariant}:") for p in found), found
    with pytest.raises(StrokesInvariantError) as exc:
        parse_strokes(_text(raw))
    assert exc.value.code == "strokes_invariant_violated"


def test_double_count_message_names_the_gesture(raw: Doc) -> None:
    _inv3_double_count(raw)
    found = [p for p in _violations(raw) if p.startswith("I3:")]
    assert any("counted twice" in p for p in found)


def test_chain_event_may_be_longer_than_g(raw: Doc) -> None:
    # three peaks 0.2 s apart with G = 0.25 s: one event lasting 0.4 s is valid
    e = _event(raw, "e0002")
    _peak(raw, "p0005").update(proxy_frame=1166, t_s=38.8672)
    extra = copy.deepcopy(_peak(raw, "p0004"))
    extra.update(id="p0007", proxy_frame=1172, t_s=39.0672)
    raw["wrist_peaks"].append(extra)
    e["grouping"].update(
        peak_ids=["p0004", "p0005", "p0007"],
        links=[
            {"from": "p0004", "to": "p0005", "dt_s": 0.2},
            {"from": "p0005", "to": "p0007", "dt_s": 0.2},
        ],
        span_s=0.4,
    )
    e["evidence"]["left"]["peak_ids"] = ["p0004", "p0007"]
    e["time"].update(window_proxy_frames=[1160, 1172], window_t_s=[38.6672, 39.0672])
    assert _violations(raw) == []


def test_unused_unobservable_region_changes_nothing(raw: Doc) -> None:
    # legs are not observable in e0001 and not used: changing them must not matter
    legs = _event(raw, "e0001")["regions"]["legs"]
    assert legs["state"] == "not_observable"
    legs.update(
        state="good",
        reasons=[],
        samples={"good": 13, "low_but_usable": 0, "unreliable": 0, "missing": 0},
    )
    assert _violations(raw) == []


def test_not_observable_region_needs_a_reason(raw: Doc) -> None:
    _event(raw, "e0001")["regions"]["legs"]["reasons"] = []
    assert any(p.startswith("I6:") for p in _violations(raw))


def test_params_hash_must_match_params(raw: Doc) -> None:
    raw["params"]["grouping"]["g_s"] = 0.3
    assert "P: params_sha256 does not match params" in _violations(raw)


def test_params_hash_ignores_key_order() -> None:
    assert params_sha256({"a": 1, "b": {"c": 2, "d": 3}}) == params_sha256(
        {"b": {"d": 3, "c": 2}, "a": 1}
    )


# ----------------------------------------------------------------- input hashes


def test_check_inputs_detects_stale_files(raw: Doc, tmp_path: Path) -> None:
    (tmp_path / "pose.json").write_text("pose", encoding="utf-8")
    (tmp_path / "timemap.json").write_text("map", encoding="utf-8")
    raw["inputs"]["pose"]["sha256"] = file_sha256(tmp_path / "pose.json")
    raw["inputs"]["timemap"]["sha256"] = file_sha256(tmp_path / "timemap.json")
    doc = StrokesFile.model_validate(raw)
    assert check_inputs(doc, tmp_path) == []
    verify_inputs(doc, tmp_path)
    (tmp_path / "timemap.json").write_text("changed", encoding="utf-8")
    assert check_inputs(doc, tmp_path) == ["timemap.json: sha256 differs (strokes.json is stale)"]
    (tmp_path / "pose.json").unlink()
    with pytest.raises(StrokesInputMismatchError):
        verify_inputs(doc, tmp_path)


# ----------------------------------------------------------------- CLI


def _cli() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "eval_strokes", root / "scripts" / "eval_strokes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_validate_strokes(raw: Doc, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _cli()
    (tmp_path / "pose.json").write_text("pose", encoding="utf-8")
    (tmp_path / "timemap.json").write_text("map", encoding="utf-8")
    raw["inputs"]["pose"]["sha256"] = file_sha256(tmp_path / "pose.json")
    raw["inputs"]["timemap"]["sha256"] = file_sha256(tmp_path / "timemap.json")
    path = tmp_path / "strokes.json"
    path.write_text(dump_strokes(StrokesFile.model_validate(raw)), encoding="utf-8")
    assert cli.main(["validate-strokes", str(path)]) == 0
    out = capsys.readouterr().out
    assert "6 (5 grouped, 1 discarded)" in out and "accepted 1, rejected 1, uncertain 1" in out

    (tmp_path / "pose.json").write_text("other pose", encoding="utf-8")
    assert cli.main(["validate-strokes", str(path)]) == 1
    assert "stale" in capsys.readouterr().err
    assert cli.main(["validate-strokes", str(path), "--no-inputs"]) == 0
    capsys.readouterr()

    _event(raw, "e0002")["reasons"] = []
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(["validate-strokes", str(path), "--no-inputs"]) == 1
    err = capsys.readouterr().err
    assert "strokes_invariant_violated" in err and "I5:" in err


# ----------------------------------------------------------------- input file names


BAD_NAMES = [
    "../pose.json",  # POSIX parent
    "/tmp/pose.json",  # POSIX absolute
    "sub/pose.json",  # POSIX relative path
    "..\\pose.json",  # Windows parent
    "C:\\data\\pose.json",  # Windows absolute
    "C:pose.json",  # Windows drive-relative
    "sub\\pose.json",  # Windows relative path
    "\\\\server\\share\\pose.json",  # Windows UNC
    "pose.json:stream",  # NTFS alternate data stream
    ".",
    "..",
    "",
    " pose.json",
    "pose\x00.json",
]


def _no_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("filesystem accessed")

    for name in ("is_file", "exists", "open", "read_bytes", "resolve"):
        monkeypatch.setattr(Path, name, boom)


@pytest.mark.parametrize("field", ["pose", "timemap"])
@pytest.mark.parametrize("name", BAD_NAMES)
def test_input_names_must_be_plain_file_names(
    raw: Doc, field: str, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw["inputs"][field]["file"] = name
    text = _text(raw)
    _no_filesystem(monkeypatch)
    with pytest.raises(StrokesFormatError) as exc:
        parse_strokes(text)
    assert f"inputs.{field}.file" in " ".join(exc.value.details["problems"])  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["pose.json", "timemap.json", "pose_v2.json", "pose-1.v2.json"])
def test_ordinary_file_names_are_accepted(raw: Doc, name: str) -> None:
    raw["inputs"]["pose"]["file"] = name
    raw["inputs"]["timemap"]["file"] = name
    assert parse_strokes(_text(raw)).inputs.pose.file == name


@pytest.mark.parametrize("field", ["pose", "timemap"])
def test_check_inputs_refuses_unvalidated_path_before_filesystem(
    raw: Doc, field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = StrokesFile.model_validate(raw)
    getattr(doc.inputs, field).file = "../outside.json"  # bypasses validation on purpose
    _no_filesystem(monkeypatch)
    with pytest.raises(StrokesFormatError):
        check_inputs(doc, tmp_path)


# ----------------------------------------------------------------- trace rules in the catalogue


def test_example_catalogue_declares_every_trace_rule(raw: Doc) -> None:
    codes = {r["code"] for r in raw["rules"]}
    used = {s["rule"] for e in raw["events"] for s in e["trace"]}
    assert "decision" in codes and used <= codes


def test_trace_rule_not_in_catalogue_is_rejected(raw: Doc) -> None:
    _event(raw, "e0001")["trace"][2]["rule"] = "made_up_check"
    found = _violations(raw)
    assert any(p.startswith("I5:") and "made_up_check" in p for p in found), found
    with pytest.raises(StrokesInvariantError):
        parse_strokes(_text(raw))


@pytest.mark.parametrize("code", ["chain_grouping", "decision"])
def test_catalogue_missing_a_used_rule_is_rejected(raw: Doc, code: str) -> None:
    raw["rules"] = [r for r in raw["rules"] if r["code"] != code]
    found = [p for p in _violations(raw) if p.startswith("I5:")]
    assert found and all(code in p for p in found), found


def test_duplicate_rule_code_is_still_rejected(raw: Doc) -> None:
    raw["rules"].append(dict(raw["rules"][0]))
    assert any("duplicate rule code" in p for p in _violations(raw))
