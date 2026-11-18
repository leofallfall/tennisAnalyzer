"""Validate an AI result against the Evidence Pack it was produced from.

Order: pack integrity → JSON syntax → schema → cross-checks with the pack data.
All problems are collected so the user (or Claude) can fix them in one pass.
Nothing here writes files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from tennis_api.ai import pack as pk
from tennis_api.ai.builder import sha256_file
from tennis_api.ai.result import (
    RESULT_FILENAME,
    WHOLE_INTERVAL,
    AnalysisResult,
    EvidenceKind,
    EvidenceRef,
    IssueStrokeType,
    ResultKind,
)
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

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.errors

    def error(self, code: str, message: str, location: str | None = None) -> None:
        self.errors.append(ValidationProblem(code=code, message=message, location=location))


@dataclass(frozen=True)
class LoadedPack:
    directory: Path
    manifest: pk.Manifest
    strokes: pk.StrokesFile
    measurements: pk.MeasurementsFile
    aggregates: pk.Aggregates | None
    timeseries: pk.StrokeTimeseries | None


# ------------------------------------------------------------------------ pack


def check_pack_integrity(pack_dir: Path, outcome: ValidationOutcome) -> pk.Manifest | None:
    manifest_path = pack_dir / pk.MANIFEST
    if not manifest_path.is_file():
        outcome.error("pack_manifest_missing", "manifest.json is missing", pk.MANIFEST)
        return None
    try:
        manifest = pk.Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        outcome.error("pack_manifest_invalid", f"manifest.json is invalid: {exc}", pk.MANIFEST)
        return None
    listed = set()
    for f in manifest.files:
        listed.add(f.path)
        path = pack_dir / f.path
        if not path.is_file():
            outcome.error("pack_file_missing", f"Evidence file is missing: {f.path}", f.path)
        elif sha256_file(path) != f.sha256:
            outcome.error(
                "pack_file_modified",
                f"Evidence file was modified after the pack was created: {f.path}",
                f.path,
            )
    for img in manifest.images:
        if img.path not in listed:
            outcome.error("pack_image_unlisted", f"Image not in integrity list: {img.path}")
    allowed = listed | {pk.MANIFEST, manifest.result_file}
    for path in sorted(pack_dir.rglob("*")):
        rel = path.relative_to(pack_dir).as_posix()
        if path.is_file() and rel not in allowed:
            outcome.warnings.append(
                ValidationProblem(
                    code="pack_unexpected_file",
                    message=f"File not created by the app: {rel} (ignored)",
                    location=rel,
                )
            )
    return manifest


def load_pack(pack_dir: Path, outcome: ValidationOutcome) -> LoadedPack | None:
    manifest = check_pack_integrity(pack_dir, outcome)
    if manifest is None or outcome.errors:
        return None

    def read(name: str) -> str:
        return (pack_dir / name).read_text(encoding="utf-8")

    try:
        aggregates = (
            pk.Aggregates.model_validate_json(read(pk.AGGREGATES))
            if (pack_dir / pk.AGGREGATES).is_file()
            else None
        )
        timeseries = (
            pk.StrokeTimeseries.model_validate_json(read(pk.TIMESERIES))
            if (pack_dir / pk.TIMESERIES).is_file()
            else None
        )
        return LoadedPack(
            directory=pack_dir,
            manifest=manifest,
            strokes=pk.StrokesFile.model_validate_json(read(pk.STROKES)),
            measurements=pk.MeasurementsFile.model_validate_json(read(pk.MEASUREMENTS)),
            aggregates=aggregates,
            timeseries=timeseries,
        )
    except (ValidationError, ValueError, OSError) as exc:
        outcome.error("pack_data_invalid", f"Evidence Pack data could not be read: {exc}")
        return None


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


def validate_result_text(raw: str, pack_dir: Path) -> ValidationOutcome:
    outcome = ValidationOutcome()
    pack = load_pack(pack_dir, outcome)
    result = parse_result(raw, outcome)
    if pack is not None and result is not None:
        cross_check(result, pack, outcome)
    outcome.result = result
    return outcome


def validate_result_file(pack_dir: Path) -> ValidationOutcome:
    path = pack_dir / RESULT_FILENAME
    if not path.is_file():
        outcome = ValidationOutcome()
        check_pack_integrity(pack_dir, outcome)
        outcome.error(
            "result_missing",
            f"{RESULT_FILENAME} was not found in the pack folder. Run the analysis first.",
            RESULT_FILENAME,
        )
        return outcome
    return validate_result_text(path.read_text(encoding="utf-8"), pack_dir)


__all__ = [
    "ResultKind",
    "ValidationOutcome",
    "ValidationProblem",
    "check_pack_integrity",
    "validate_result_file",
    "validate_result_text",
]
