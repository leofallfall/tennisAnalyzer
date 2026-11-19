"""Validate an AI result against the Evidence Pack it was produced from.

Order: pack integrity → JSON syntax → schema → cross-checks with the pack data.
All problems are collected so the user (or Claude) can fix them in one pass.
Nothing here writes files.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from tennis_api.ai import pack as pk
from tennis_api.ai.result import (
    RESULT_FILENAME,
    WHOLE_INTERVAL,
    AnalysisResult,
    EvidenceKind,
    EvidenceRef,
    IssueStrokeType,
    ResultKind,
)
from tennis_api.ai.safe_files import PackFileError, read_pack_file, scan_pack_tree
from tennis_cv.common import StrictModel
from tennis_cv.strokes.base import StrokeType

TIME_TOLERANCE_S = 0.5
# "142°", "142.5 °", "142 deg", "142 gradi"
_ANGLE_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(?:°|deg\b|degrees\b|gradi\b|grado\b)", re.I)


class ValidationProblem(StrictModel):
    code: str
    message: str
    location: str | None = None


@dataclass
class ValidationOutcome:
    result: AnalysisResult | None = None
    errors: list[ValidationProblem] = field(default_factory=list)
    warnings: list[ValidationProblem] = field(default_factory=list)
    result_sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.errors

    def error(self, code: str, message: str, location: str | None = None) -> None:
        self.errors.append(ValidationProblem(code=code, message=message, location=location))


@dataclass(frozen=True)
class PackAnchor:
    """What the database recorded when the pack was created (C2).

    The pack folder is writable by the analyst (Claude Code), so the expected
    ``manifest.json`` hash and the pack identity come from here, never from the folder.
    """

    pack_id: str
    analysis_id: str
    kind: pk.PackKind
    manifest_sha256: str


@dataclass(frozen=True)
class VerifiedManifest:
    directory: Path
    manifest: pk.Manifest
    manifest_sha256: str


@dataclass(frozen=True)
class LoadedPack:
    directory: Path
    manifest: pk.Manifest
    strokes: pk.StrokesFile
    measurements: pk.MeasurementsFile
    aggregates: pk.Aggregates | None
    timeseries: pk.StrokeTimeseries | None


LEGACY_MESSAGE = (
    "This Evidence Pack was created before integrity protection and cannot be verified. "
    "Prepare a new AI analysis to get a protected pack."
)


# ------------------------------------------------------------------------ pack


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_anchored_manifest(
    pack_dir: Path, anchor: PackAnchor, outcome: ValidationOutcome
) -> VerifiedManifest | None:
    """Read ``manifest.json`` and check it against the hash and identity stored in the DB."""
    try:
        raw = read_pack_file(pack_dir, pk.MANIFEST)
    except PackFileError as exc:
        code = (
            "pack_manifest_missing"
            if exc.code in ("pack_file_missing", "pack_missing")
            else (exc.code)
        )
        outcome.error(code, f"manifest.json could not be read: {exc.message}", pk.MANIFEST)
        return None
    digest = _sha256(raw)
    if digest != anchor.manifest_sha256:
        outcome.error(
            "pack_manifest_modified",
            "manifest.json was modified after the pack was created (its hash does not match "
            "the one recorded by the app). Prepare a new AI analysis.",
            pk.MANIFEST,
        )
        return None
    try:
        manifest = pk.Manifest.model_validate_json(raw)
    except (ValidationError, ValueError):
        outcome.error("pack_manifest_invalid", "manifest.json is invalid", pk.MANIFEST)
        return None
    for field_name, expected, actual in (
        ("pack_id", anchor.pack_id, manifest.pack_id),
        ("analysis_id", anchor.analysis_id, manifest.analysis_id),
        ("kind", anchor.kind.value, manifest.kind.value),
        ("folder", anchor.pack_id, pack_dir.name),
    ):
        if expected != actual:
            outcome.error(
                "pack_identity_mismatch",
                f"The pack's {field_name} does not match the AI analysis it belongs to",
                pk.MANIFEST,
            )
    if outcome.errors:
        return None
    return VerifiedManifest(directory=pack_dir, manifest=manifest, manifest_sha256=digest)


def check_pack_integrity(
    pack_dir: Path,
    outcome: ValidationOutcome,
    anchor: PackAnchor | None,
    *,
    require_anchor: bool = True,
) -> tuple[pk.Manifest, dict[str, bytes]] | None:
    """Verify the whole pack and return its manifest plus the verified file contents.

    1. ``manifest.json`` must match the hash recorded in the database (``anchor``).
       Without an anchor the pack is legacy: an error unless ``require_anchor`` is false
       (only the CLI does that, for packs it cannot find in the database).
    2. Every path is safe, every required file for the pack kind is listed, every listed
       file matches its hash, there are no symlinks and no files the app did not create.

    The returned bytes are the ones that were hashed: callers parse them instead of
    reopening the files.
    """
    if anchor is None:
        if require_anchor:
            outcome.error("pack_legacy_unanchored", LEGACY_MESSAGE, pk.MANIFEST)
            return None
        try:
            raw = read_pack_file(pack_dir, pk.MANIFEST)
            manifest = pk.Manifest.model_validate_json(raw)
        except PackFileError as exc:
            outcome.error("pack_manifest_missing", f"manifest.json: {exc.message}", pk.MANIFEST)
            return None
        except (ValidationError, ValueError):
            outcome.error("pack_manifest_invalid", "manifest.json is invalid", pk.MANIFEST)
            return None
    else:
        verified = read_anchored_manifest(pack_dir, anchor, outcome)
        if verified is None:
            return None
        manifest = verified.manifest

    listed: dict[str, pk.PackFile] = {}
    for f in manifest.files:
        if f.path in listed or f.path in (pk.MANIFEST, RESULT_FILENAME):
            outcome.error("pack_file_list_invalid", f"Invalid integrity list entry: {f.path}")
        listed[f.path] = f
    image_paths = {img.path for img in manifest.images}
    for missing in sorted(pk.REQUIRED_FILES[manifest.kind] - set(listed)):
        outcome.error(
            "pack_required_file_missing",
            f"Required file is not in the integrity list: {missing}",
            missing,
        )
    for path in sorted(set(listed) - pk.REQUIRED_FILES[manifest.kind] - image_paths):
        outcome.error("pack_file_list_invalid", f"Unexpected entry in the integrity list: {path}")
    for img in manifest.images:
        if img.path not in listed:
            outcome.error("pack_image_unlisted", f"Image not in integrity list: {img.path}")

    contents: dict[str, bytes] = {}
    for path, f in listed.items():
        try:
            data = read_pack_file(pack_dir, path)
        except PackFileError as exc:
            outcome.error(exc.code, f"Evidence file {path}: {exc.message}", path)
            continue
        if _sha256(data) != f.sha256:
            outcome.error(
                "pack_file_modified",
                f"Evidence file was modified after the pack was created: {path}",
                path,
            )
            continue
        contents[path] = data

    try:
        files, links, other = scan_pack_tree(pack_dir)
    except OSError:
        outcome.error("pack_missing", "The Evidence Pack folder could not be listed")
        return None
    for rel in links:
        outcome.error("pack_symlink", f"Symbolic links are not allowed in a pack: {rel}", rel)
    for rel in other:
        outcome.error("pack_not_regular_file", f"Not a regular file: {rel}", rel)
    for rel in sorted(files - set(listed) - {pk.MANIFEST, RESULT_FILENAME}):
        outcome.error(
            "pack_unexpected_file",
            f"File not created by the app: {rel}. Only {RESULT_FILENAME} may be added; "
            "remove this file and import again.",
            rel,
        )
    if outcome.errors:
        return None
    return manifest, contents


def load_pack(
    pack_dir: Path,
    outcome: ValidationOutcome,
    anchor: PackAnchor | None,
    *,
    require_anchor: bool = True,
) -> LoadedPack | None:
    checked = check_pack_integrity(pack_dir, outcome, anchor, require_anchor=require_anchor)
    if checked is None:
        return None
    manifest, contents = checked
    try:
        aggregates = (
            pk.Aggregates.model_validate_json(contents[pk.AGGREGATES])
            if pk.AGGREGATES in contents
            else None
        )
        timeseries = (
            pk.StrokeTimeseries.model_validate_json(contents[pk.TIMESERIES])
            if pk.TIMESERIES in contents
            else None
        )
        return LoadedPack(
            directory=pack_dir,
            manifest=manifest,
            strokes=pk.StrokesFile.model_validate_json(contents[pk.STROKES]),
            measurements=pk.MeasurementsFile.model_validate_json(contents[pk.MEASUREMENTS]),
            aggregates=aggregates,
            timeseries=timeseries,
        )
    except (ValidationError, ValueError, KeyError):
        outcome.error("pack_data_invalid", "Evidence Pack data could not be read")
        return None


def read_verified_image(pack_dir: Path, anchor: PackAnchor, image_id: str) -> bytes | None:
    """Bytes of one evidence image, or ``None`` if it cannot be served safely.

    The manifest is checked against the database hash, the image path is validated and
    opened without following links, and the bytes must match the manifest hash.
    """
    outcome = ValidationOutcome()
    verified = read_anchored_manifest(pack_dir, anchor, outcome)
    if verified is None:
        return None
    m = verified.manifest
    img = next((i for i in m.images if i.id == image_id), None)
    entry = next((f for f in m.files if img is not None and f.path == img.path), None)
    if img is None or entry is None:
        return None
    try:
        data = read_pack_file(pack_dir, img.path)
    except PackFileError:
        return None
    return data if _sha256(data) == entry.sha256 else None


# ---------------------------------------------------------------------- parsing


def parse_result(raw: str, outcome: ValidationOutcome) -> AnalysisResult | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        outcome.error(
            "invalid_json",
            f"Not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
        )
        return None
    try:
        return AnalysisResult.model_validate(data)
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            outcome.error("schema_error", e.get("msg", "invalid"), loc or None)
        return None


# ------------------------------------------------------------------ cross-checks


def _pack_numbers(pack: LoadedPack) -> list[float]:
    nums: list[float] = []
    for m in pack.measurements.measurements:
        if m.measurement.value is not None:
            nums.append(m.measurement.value)
    if pack.aggregates:
        for a in pack.aggregates.metrics:
            nums.extend(v for v in (a.median, a.q1, a.q3, a.min, a.max) if v is not None)
    if pack.timeseries:
        for s in pack.timeseries.samples:
            nums.extend(a.value_deg for a in s.angles.values() if a.value_deg is not None)
    return nums


def _texts(result: AnalysisResult) -> Iterator[tuple[str, str]]:
    yield "summary", result.summary
    for i, lim in enumerate(result.limitations):
        yield f"limitations[{i}]", lim
    for i, st in enumerate(result.by_stroke_type):
        yield f"by_stroke_type[{i}].summary", st.summary
    for i, issue in enumerate(result.issues):
        base = f"issues[{i}]"
        yield f"{base}.description", issue.description
        yield f"{base}.biomechanical_explanation", issue.biomechanical_explanation
        for j, o in enumerate(issue.observations):
            yield f"{base}.observations[{j}]", o
        for j, a in enumerate(issue.alternative_explanations):
            yield f"{base}.alternative_explanations[{j}]", a
    for i, pe in enumerate(result.positive_executions):
        yield f"positive_executions[{i}].description", pe.description
    for i, rec in enumerate(result.recommendations):
        yield f"recommendations[{i}].rationale", rec.rationale
    for i, ph in enumerate(result.phases):
        for group in ("observed", "hypotheses", "not_observable"):
            for j, t in enumerate(getattr(ph, group)):
                yield f"phases[{i}].{group}[{j}]", t


def _type_matches(issue_type: IssueStrokeType, stroke_type: StrokeType) -> bool:
    if issue_type is IssueStrokeType.BOTH:
        return stroke_type in (StrokeType.FOREHAND, StrokeType.BACKHAND)
    return issue_type.value == stroke_type.value


def cross_check(result: AnalysisResult, pack: LoadedPack, outcome: ValidationOutcome) -> None:
    m = pack.manifest
    err = outcome.error
    if result.session_id != m.analysis_id:
        err("wrong_session", f"session_id must be '{m.analysis_id}'", "session_id")
    if result.pack_id != m.pack_id:
        err("wrong_pack", f"pack_id must be '{m.pack_id}'", "pack_id")
    if result.kind.value != m.kind.value:
        err("wrong_kind", f"kind must be '{m.kind.value}'", "kind")
    if result.analysis_version != m.instructions_version:
        err(
            "wrong_version",
            f"analysis_version must be '{m.instructions_version}'",
            "analysis_version",
        )

    strokes = {s.id: s for s in pack.strokes.strokes}
    by_type = {t: sum(1 for s in strokes.values() if s.stroke_type is t) for t in StrokeType}
    ev = result.evaluated_strokes
    if ev.detected != len(strokes):
        err(
            "stroke_count",
            f"evaluated_strokes.detected must be {len(strokes)}",
            "evaluated_strokes",
        )
    for name, stype in (("forehand", StrokeType.FOREHAND), ("backhand", StrokeType.BACKHAND)):
        if getattr(ev, name) != by_type[stype]:
            err(
                "stroke_count",
                f"evaluated_strokes.{name} must be {by_type[stype]}",
                f"evaluated_strokes.{name}",
            )
    for st in result.by_stroke_type:
        actual = by_type[StrokeType(st.stroke_type)]
        if st.detected != actual or st.evaluable > actual:
            err(
                "stroke_count",
                f"{st.stroke_type}: detected must be {actual} and evaluable ≤ {actual}",
                "by_stroke_type",
            )

    if not strokes:
        if result.issues:
            err("no_strokes_in_pack", "The pack has no strokes: issues must be empty", "issues")
        if result.positive_executions:
            err(
                "no_strokes_in_pack",
                "The pack has no strokes: positive_executions must be empty",
                "positive_executions",
            )
        if not result.insufficient_evidence:
            err(
                "no_strokes_in_pack",
                "The pack has no strokes: insufficient_evidence must be true",
                "insufficient_evidence",
            )

    image_ids = {i.id for i in m.images}
    measurement_ids = {x.id for x in pack.measurements.measurements}
    metric_ids = {a.metric_id for a in pack.aggregates.metrics} if pack.aggregates else set()

    def check_refs(refs: list[EvidenceRef], loc: str) -> None:
        for k, ref in enumerate(refs):
            kind, value = ref.kind, ref.ref
            known: set[str] | None = {
                EvidenceKind.IMAGE: image_ids,
                EvidenceKind.MEASUREMENT: measurement_ids,
                EvidenceKind.STROKE: set(strokes),
                EvidenceKind.AGGREGATE: metric_ids,
            }.get(kind)
            if kind is EvidenceKind.TIMESERIES and pack.timeseries is None:
                err("unknown_evidence", "The pack has no time series", f"{loc}[{k}]")
            if known is not None and value not in known:
                err("unknown_evidence", f"{kind.value} '{value}' is not in the pack", f"{loc}[{k}]")

    for i, issue in enumerate(result.issues):
        loc = f"issues[{i}]"
        type_total = sum(
            1 for s in strokes.values() if _type_matches(issue.stroke_type, s.stroke_type)
        )
        if issue.evaluable_strokes > type_total:
            err(
                "denominator",
                f"evaluable_strokes ({issue.evaluable_strokes}) exceeds the {type_total} "
                f"'{issue.stroke_type.value}' strokes in the pack",
                f"{loc}.evaluable_strokes",
            )
        for sid in issue.stroke_ids:
            stroke = strokes.get(sid)
            if stroke is None:
                err("unknown_stroke", f"Stroke '{sid}' is not in the pack", f"{loc}.stroke_ids")
            elif not _type_matches(issue.stroke_type, stroke.stroke_type):
                err(
                    "stroke_type_mismatch",
                    f"Stroke '{sid}' is {stroke.stroke_type.value}, "
                    f"issue is {issue.stroke_type.value}",
                    f"{loc}.stroke_ids",
                )
        cited = [strokes[s] for s in issue.stroke_ids if s in strokes]
        for t in issue.timestamps_s:
            if cited and not any(
                s.interval.start_s - TIME_TOLERANCE_S <= t <= s.interval.end_s + TIME_TOLERANCE_S
                for s in cited
            ):
                err(
                    "timestamp_mismatch",
                    f"Timestamp {t} s is not within any cited stroke",
                    f"{loc}.timestamps_s",
                )
        check_refs(issue.evidence, f"{loc}.evidence")

    for i, pe in enumerate(result.positive_executions):
        loc = f"positive_executions[{i}]"
        stroke = strokes.get(pe.stroke_id)
        if stroke is None:
            err("unknown_stroke", f"Stroke '{pe.stroke_id}' is not in the pack", loc)
        elif not _type_matches(pe.stroke_type, stroke.stroke_type):
            err("stroke_type_mismatch", f"Stroke '{pe.stroke_id}' type does not match", loc)
        check_refs(pe.evidence, f"{loc}.evidence")

    if m.kind is pk.PackKind.SINGLE_STROKE and m.target is not None:
        tgt = result.target
        if tgt is None or (
            tgt.stroke_id != m.target.stroke_id
            or tgt.video_id != m.target.video_id
            or abs(tgt.interval.start_s - m.target.interval.start_s) > 1e-3
            or abs(tgt.interval.end_s - m.target.interval.end_s) > 1e-3
        ):
            err("wrong_target", "target must be copied from manifest.target", "target")
        stroke = strokes.get(m.target.stroke_id)
        phase_names = {p.name.value for p in stroke.phases} if stroke else set()
        for i, ph in enumerate(result.phases):
            name = ph.phase if isinstance(ph.phase, str) else ph.phase.value
            if phase_names and name not in phase_names:
                err("unknown_phase", f"Phase '{name}' is not segmented in the pack", f"phases[{i}]")
            if not phase_names and name != WHOLE_INTERVAL:
                err(
                    "unknown_phase",
                    f"No phases in the pack: use '{WHOLE_INTERVAL}', not '{name}'",
                    f"phases[{i}]",
                )
            check_refs(ph.evidence, f"phases[{i}].evidence")
        if not result.phases:
            err("phases_missing", "A single-stroke result needs at least one phase entry", "phases")

    numbers = _pack_numbers(pack)
    for loc, text in _texts(result):
        for match in _ANGLE_RE.finditer(text):
            written = match.group(1).replace(",", ".")
            decimals = len(written.split(".")[1]) if "." in written else 0
            value = float(written)
            # The cited angle must be a pack value written at the cited precision.
            if not any(abs(round(n, decimals) - value) < 1e-9 for n in numbers):
                err(
                    "unsupported_number",
                    f"Angle '{match.group(0)}' does not appear in the pack data",
                    loc,
                )


# ------------------------------------------------------------------------ entry


def validate_result_text(
    raw: str,
    pack_dir: Path,
    anchor: PackAnchor | None = None,
    *,
    require_anchor: bool = True,
) -> ValidationOutcome:
    outcome = ValidationOutcome()
    pack = load_pack(pack_dir, outcome, anchor, require_anchor=require_anchor)
    result = parse_result(raw, outcome)
    if pack is not None and result is not None:
        cross_check(result, pack, outcome)
    outcome.result = result
    outcome.result_sha256 = _sha256(raw.encode("utf-8"))
    return outcome


def validate_result_file(
    pack_dir: Path,
    anchor: PackAnchor | None = None,
    *,
    require_anchor: bool = True,
) -> ValidationOutcome:
    try:
        raw = read_pack_file(pack_dir, RESULT_FILENAME)
    except PackFileError as exc:
        outcome = ValidationOutcome()
        check_pack_integrity(pack_dir, outcome, anchor, require_anchor=require_anchor)
        if exc.code in ("pack_file_missing", "pack_missing"):
            outcome.error(
                "result_missing",
                f"{RESULT_FILENAME} was not found in the pack folder. Run the analysis first.",
                RESULT_FILENAME,
            )
        else:
            outcome.error(exc.code, f"{RESULT_FILENAME}: {exc.message}", RESULT_FILENAME)
        return outcome
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        outcome = ValidationOutcome()
        check_pack_integrity(pack_dir, outcome, anchor, require_anchor=require_anchor)
        outcome.error("invalid_json", f"{RESULT_FILENAME} is not UTF-8 text", RESULT_FILENAME)
        return outcome
    return validate_result_text(text, pack_dir, anchor, require_anchor=require_anchor)


__all__ = [
    "LEGACY_MESSAGE",
    "LoadedPack",
    "PackAnchor",
    "ResultKind",
    "ValidationOutcome",
    "ValidationProblem",
    "check_pack_integrity",
    "load_pack",
    "read_anchored_manifest",
    "read_verified_image",
    "validate_result_file",
    "validate_result_text",
]
