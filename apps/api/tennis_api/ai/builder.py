"""Build Evidence Packs from finished CV results.

Reads engine outputs (report, pose, CFR proxy) and never writes to them: a pack is a new
directory. Missing engine capabilities are declared (``not_implemented``), never filled
with placeholders.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from tennis_api.ai import pack as pk
from tennis_api.ai.instructions import render_instructions
from tennis_api.ai.result import RESULT_FILENAME, RESULT_SCHEMA_VERSION, AnalysisResult
from tennis_cv import __version__ as engine_version
from tennis_cv.biomechanics.geometry import projected_joint_angle
from tennis_cv.common import Observability, Reliability, TimeInterval
from tennis_cv.errors import TennisCVError
from tennis_cv.pipeline import ProcessingReport
from tennis_cv.pose.models import PoseFrame, PoseSequence
from tennis_cv.pose.overlay import draw_skeleton
from tennis_cv.strokes.base import StrokeType

GENERATOR = f"tennis_api.ai.builder (engine {engine_version})"
OBSERVED = {Observability.OBSERVABLE, Observability.PARTIALLY_OBSERVABLE}
KEY_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
ANGLES: dict[str, tuple[str, tuple[str, str, str], bool]] = {
    # key -> (metric_id, joints, convert_to_flexion)
    "elbow_angle_projected:left": (
        "elbow_angle_projected",
        ("left_shoulder", "left_elbow", "left_wrist"),
        False,
    ),
    "elbow_angle_projected:right": (
        "elbow_angle_projected",
        ("right_shoulder", "right_elbow", "right_wrist"),
        False,
    ),
    "knee_flexion_projected:left": (
        "knee_flexion_projected",
        ("left_hip", "left_knee", "left_ankle"),
        True,
    ),
    "knee_flexion_projected:right": (
        "knee_flexion_projected",
        ("right_hip", "right_knee", "right_ankle"),
        True,
    ),
}


class EvidencePackError(TennisCVError):
    """Inputs for an Evidence Pack are inconsistent or unavailable."""

    code = "evidence_pack_invalid"


@dataclass(frozen=True)
class VideoSource:
    info: pk.VideoInfo
    report: ProcessingReport
    proxy_path: Path
    pose_path: Path


@dataclass(frozen=True)
class StrokeData:
    """Stroke-level engine output. ``None`` everywhere today (M2 not implemented)."""

    strokes: list[pk.StrokeRecord]
    measurements: list[pk.MeasurementRecord]
    issue_candidates: list[pk.IssueCandidate] | None = None


@dataclass(frozen=True)
class PackOptions:
    language: str = "it"
    overview_frames_per_video: int = 6
    max_evidence_strokes: int = 40
    sequence_frames: int = 8
    image_long_side: int = 768
    max_single_stroke_window_s: float = 6.0


# --------------------------------------------------------------------------- helpers


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: BaseModel | dict[str, object]) -> None:
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _new_pack_id(kind: pk.PackKind, now: datetime) -> str:
    prefix = "session" if kind is pk.PackKind.SESSION else "stroke"
    return f"{prefix}-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _load_pose(path: Path) -> PoseSequence:
    if not path.is_file():
        raise EvidencePackError("Pose data is missing for this video", details={"file": path.name})
    return PoseSequence.model_validate_json(path.read_text())


def _pose_by_frame(seq: PoseSequence) -> dict[int, PoseFrame]:
    return {f.frame_index: f for f in seq.frames}


def _nearest_pose(poses: dict[int, PoseFrame], index: int, stride: int) -> PoseFrame | None:
    """Pose for ``index``; with stride > 1 use the nearest analysed frame (≤ stride/2 away)."""
    if index in poses:
        return poses[index]
    for d in range(1, max(1, stride // 2) + 1):
        for cand in (index - d, index + d):
            if cand in poses:
                return poses[cand]
    return None


def read_frames(path: Path, indices: Iterable[int]) -> dict[int, npt.NDArray[np.uint8]]:
    """Decode specific frames of the CFR proxy. Seeks for big jumps, grabs for small ones."""
    wanted = sorted(set(i for i in indices if i >= 0))
    out: dict[int, npt.NDArray[np.uint8]] = {}
    if not wanted:
        return out
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise EvidencePackError("The proxy video could not be opened")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        pos = 0
        for target in wanted:
            if target < pos or target - pos > 2 * fps:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                if pos > target:  # seek overshoot: restart from the beginning
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    pos = 0
            while pos < target:
                if not cap.grab():
                    break
                pos += 1
            if pos != target:
                continue
            ok, image = cap.read()
            if ok and image is not None:
                out[target] = np.asarray(image, dtype=np.uint8)
            pos += 1
    finally:
        cap.release()
    return out


def _resize(image: npt.NDArray[np.uint8], long_side: int) -> npt.NDArray[np.uint8]:
    h, w = image.shape[:2]
    scale = long_side / max(h, w)
    if scale >= 1:
        return image
    size = (int(round(w * scale)), int(round(h * scale)))
    return np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)


@dataclass(frozen=True)
class _FrameRequest:
    video: VideoSource
    frame_index: int
    role: pk.ImageRole
    stroke_id: str | None


def _write_images(
    pack_dir: Path, requests: Sequence[_FrameRequest], opts: PackOptions
) -> tuple[list[pk.ImageRef], list[str]]:
    """Extract, annotate and save requested frames. Returns refs and missing-frame notes."""
    evidence = pack_dir / pk.EVIDENCE_DIR
    evidence.mkdir(exist_ok=True)
    refs: list[pk.ImageRef] = []
    missing: list[str] = []
    by_video: dict[str, list[_FrameRequest]] = defaultdict(list)
    for r in requests:
        by_video[r.video.info.video_id].append(r)
    for video_id, reqs in by_video.items():
        video = reqs[0].video
        seq = _load_pose(video.pose_path)
        poses = _pose_by_frame(seq)
        frames = read_frames(video.proxy_path, (r.frame_index for r in reqs))
        fps = video.info.proxy_fps
        for r in reqs:
            image = frames.get(r.frame_index)
            if image is None:
                missing.append(f"Frame {r.frame_index} of video {video_id} could not be decoded")
                continue
            pose = _nearest_pose(poses, r.frame_index, seq.frame_stride)
            drawn = pose is not None and pose.detected
            if pose is not None and drawn:
                image = draw_skeleton(image, pose)
            image = _resize(image, opts.image_long_side)
            image_id = f"img_{len(refs) + 1:03d}"
            stroke_part = f"_{r.stroke_id}" if r.stroke_id else ""
            name = f"{image_id}_{video_id[:8]}_f{r.frame_index:06d}{stroke_part}_{r.role}.jpg"
            rel = f"{pk.EVIDENCE_DIR}/{name}"
            if not cv2.imwrite(str(pack_dir / rel), image, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                missing.append(f"Frame {r.frame_index} of video {video_id} could not be saved")
                continue
            refs.append(
                pk.ImageRef(
                    id=image_id,
                    path=rel,
                    video_id=video_id,
                    frame_index=r.frame_index,
                    timestamp_s=round(r.frame_index / fps, 3),
                    stroke_id=r.stroke_id,
                    role=r.role,
                    skeleton_drawn=drawn,
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                )
            )
    return refs, missing


def _frame_at(t: float, fps: float, frame_count: int | None) -> int:
    index = int(round(t * fps))
    if frame_count:
        index = min(index, frame_count - 1)
    return max(0, index)


def _check_stroke_data(data: StrokeData, videos: dict[str, VideoSource]) -> None:
    ids = [s.id for s in data.strokes]
    dupes = [k for k, n in Counter(ids).items() if n > 1]
    if dupes:
        raise EvidencePackError("Duplicate stroke ids", details={"stroke_ids": dupes})
    known = set(ids)
    for s in data.strokes:
        if s.video_id not in videos:
            raise EvidencePackError(
                "Stroke references an unknown video", details={"stroke_id": s.id}
            )
        duration = videos[s.video_id].info.duration_s
        if duration is not None and s.interval.end_s > duration + 0.5:
            raise EvidencePackError(
                "Stroke interval is outside the video", details={"stroke_id": s.id}
            )
    m_ids = [m.id for m in data.measurements]
    dupes = [k for k, n in Counter(m_ids).items() if n > 1]
    if dupes:
        raise EvidencePackError("Duplicate measurement ids", details={"measurement_ids": dupes})
    orphans = sorted({m.id for m in data.measurements if m.measurement.stroke_id not in known})
    if orphans:
        raise EvidencePackError(
            "Measurements reference strokes that are not in the pack",
            details={"measurement_ids": orphans},
        )
    by_id = {s.id: s for s in data.strokes}
    wrong_video = sorted(
        m.id
        for m in data.measurements
        if m.measurement.stroke_id and by_id[m.measurement.stroke_id].video_id != m.video_id
    )
    if wrong_video:
        raise EvidencePackError(
            "Measurement and stroke belong to different videos",
            details={"measurement_ids": wrong_video},
        )
    for c in data.issue_candidates or []:
        unknown = sorted(set(c.stroke_ids) - known)
        if unknown:
            raise EvidencePackError(
                "Issue candidate references unknown strokes",
                details={"candidate": c.id, "stroke_ids": unknown},
            )


def compute_aggregates(
    strokes: Sequence[pk.StrokeRecord], measurements: Sequence[pk.MeasurementRecord]
) -> pk.Aggregates:
    """Per stroke type and metric. Denominators count observable measurements only."""
    type_of = {s.id: s.stroke_type for s in strokes}
    groups: dict[tuple[StrokeType, str], list[pk.MeasurementRecord]] = defaultdict(list)
    for m in measurements:
        sid = m.measurement.stroke_id
        if sid is None:
            continue
        groups[(type_of[sid], m.measurement.metric_id)].append(m)
    metrics: list[pk.MetricAggregate] = []
    for (stype, metric_id), records in sorted(groups.items(), key=lambda kv: kv[0]):
        values = [
            r.measurement.value
            for r in records
            if r.measurement.observability in OBSERVED and r.measurement.value is not None
        ]
        strokes_with = {r.measurement.stroke_id for r in records}
        quart = statistics.quantiles(values, n=4) if len(values) >= 2 else None
        metrics.append(
            pk.MetricAggregate(
                metric_id=metric_id,
                stroke_type=stype,
                unit=records[0].measurement.unit,
                strokes_with_record=len(strokes_with),
                observable=len(values),
                not_observable=len(records) - len(values),
                median=round(statistics.median(values), 2) if values else None,
                q1=round(quart[0], 2) if quart else None,
                q3=round(quart[2], 2) if quart else None,
                min=round(min(values), 2) if values else None,
                max=round(max(values), 2) if values else None,
            )
        )
    counts = Counter(s.stroke_type.value for s in strokes)
    return pk.Aggregates(
        status=pk.DataStatus.AVAILABLE if strokes else pk.DataStatus.EMPTY,
        strokes_by_type=dict(sorted(counts.items())),
        metrics=metrics,
    )


def _data_quality(
    videos: Sequence[VideoSource], measurements: pk.MeasurementsFile
) -> pk.DataQuality:
    rates = [v.report.pose_summary.detection_rate for v in videos]
    visibility = ", ".join(
        f"{v.info.video_id[:8]}: player detected in {r:.0%} of analysed frames"
        for v, r in zip(videos, rates, strict=True)
    )
    limitations: list[str] = []
    for v in videos:
        for item in v.report.not_performed:
            note = f"Not performed by the CV engine: {item}"
            if note not in limitations:
                limitations.append(note)
        limitations.extend(n for n in v.report.quality.notes if n not in limitations)
    limitations.append(
        "Quality thresholds are uncalibrated heuristics (not yet validated on tennis footage)."
    )
    if measurements.status is pk.DataStatus.AVAILABLE:
        total = len(measurements.measurements)
        obs = sum(1 for m in measurements.measurements if m.measurement.observability in OBSERVED)
        mo = pk.CheckStatus(
            status=pk.DataStatus.AVAILABLE,
            detail=f"{obs} of {total} stroke measurements are observable",
        )
    else:
        mo = pk.CheckStatus(
            status=pk.DataStatus.NOT_IMPLEMENTED,
            detail="Stroke-level measurements are not computed yet (needs stroke detection).",
        )
    return pk.DataQuality(
        videos=[
            pk.VideoQuality(
                video_id=v.info.video_id,
                quality=v.report.quality,
                pose_estimator=v.report.pose_estimator,
                pose_model=v.report.pose_model,
                pose=v.report.pose_summary,
                validation_issues=[i.message for i in v.report.validation.issues],
            )
            for v in videos
        ],
        player_visibility=visibility,
        occlusions=pk.CheckStatus(
            status=pk.DataStatus.NOT_IMPLEMENTED,
            detail="No occlusion detection. Low joint visibility scores are the only signal.",
        ),
        tracking=pk.CheckStatus(
            status=pk.DataStatus.NOT_IMPLEMENTED,
            detail="Single-person pose tracking; switches to another person (e.g. the "
            "opponent) are not detected.",
        ),
        measurement_observability=mo,
        limitations=limitations,
    )


def _finalise(
    tmp_dir: Path,
    final_dir: Path,
    manifest_kwargs: dict[str, object],
    images: list[pk.ImageRef],
) -> pk.Manifest:
    _write_json(tmp_dir / pk.RESULT_SCHEMA, AnalysisResult.model_json_schema())
    files: list[pk.PackFile] = []
    for path in sorted(tmp_dir.rglob("*")):
        if path.is_file():
            files.append(
                pk.PackFile(
                    path=path.relative_to(tmp_dir).as_posix(),
                    sha256=sha256_file(path),
                    bytes=path.stat().st_size,
                )
            )
    manifest = pk.Manifest(images=images, files=files, **manifest_kwargs)
    _write_json(tmp_dir / pk.MANIFEST, manifest)
    tmp_dir.rename(final_dir)
    return manifest


def _start(dest_root: Path, kind: pk.PackKind, now: datetime) -> tuple[str, Path, Path]:
    pack_id = _new_pack_id(kind, now)
    dest_root.mkdir(parents=True, exist_ok=True)
    tmp = dest_root / f".tmp-{pack_id}"
    tmp.mkdir()
    return pack_id, tmp, dest_root / pack_id


# ------------------------------------------------------------------------ session pack


def build_session_pack(
    dest_root: Path,
    metadata: pk.SessionMetadata,
    videos: Sequence[VideoSource],
    stroke_data: StrokeData | None = None,
    *,
    options: PackOptions | None = None,
    now: datetime | None = None,
) -> tuple[pk.Manifest, Path]:
    opts = options or PackOptions()
    now = now or datetime.now(UTC)
    if not videos:
        raise EvidencePackError("The analysis has no processed video")
    by_id = {v.info.video_id: v for v in videos}
    if stroke_data is not None:
        _check_stroke_data(stroke_data, by_id)
    pack_id, tmp, final = _start(dest_root, pk.PackKind.SESSION, now)
    try:
        if stroke_data is None:
            reason = "Stroke detection and classification are not implemented yet (M2)."
            strokes_file = pk.StrokesFile(status=pk.DataStatus.NOT_IMPLEMENTED, reason=reason)
            meas_file = pk.MeasurementsFile(
                status=pk.DataStatus.NOT_IMPLEMENTED,
                reason="Stroke-level biomechanical measurements need stroke detection.",
            )
            candidates = pk.IssueCandidatesFile(
                status=pk.DataStatus.NOT_IMPLEMENTED,
                reason="Rule-based issue candidates need stroke measurements.",
            )
            strokes: list[pk.StrokeRecord] = []
        else:
            strokes = list(stroke_data.strokes)
            strokes_file = pk.StrokesFile(
                status=pk.DataStatus.AVAILABLE if strokes else pk.DataStatus.EMPTY,
                reason=None if strokes else "No strokes were detected in this session.",
                strokes=[],  # filled after images are written
            )
            meas_file = pk.MeasurementsFile(
                status=pk.DataStatus.AVAILABLE if stroke_data.measurements else pk.DataStatus.EMPTY,
                measurements=list(stroke_data.measurements),
            )
            if stroke_data.issue_candidates is None:
                candidates = pk.IssueCandidatesFile(
                    status=pk.DataStatus.NOT_IMPLEMENTED,
                    reason="Rule-based issue candidates are not implemented yet.",
                )
            else:
                candidates = pk.IssueCandidatesFile(
                    status=pk.DataStatus.AVAILABLE
                    if stroke_data.issue_candidates
                    else pk.DataStatus.EMPTY,
                    candidates=list(stroke_data.issue_candidates),
                )

        requests = _session_frame_requests(videos, strokes, candidates, opts)
        images, missing = _write_images(tmp, requests, opts)
        image_ids_by_stroke: dict[str, list[str]] = defaultdict(list)
        for img in images:
            if img.stroke_id:
                image_ids_by_stroke[img.stroke_id].append(img.id)
        if strokes:
            strokes_file = strokes_file.model_copy(
                update={
                    "strokes": [
                        s.model_copy(update={"evidence_image_ids": image_ids_by_stroke[s.id]})
                        for s in strokes
                    ]
                }
            )
        quality = _data_quality(videos, meas_file)
        quality.limitations.extend(missing)
        if strokes and len(strokes) > opts.max_evidence_strokes:
            quality.limitations.append(
                f"Frames are included for {opts.max_evidence_strokes} of {len(strokes)} strokes "
                "(pack size limit); numeric data covers every stroke."
            )

        _write_json(tmp / pk.SESSION_METADATA, metadata)
        _write_json(tmp / pk.DATA_QUALITY, quality)
        _write_json(tmp / pk.STROKES, strokes_file)
        _write_json(tmp / pk.MEASUREMENTS, meas_file)
        _write_json(tmp / pk.AGGREGATES, compute_aggregates(strokes, meas_file.measurements))
        _write_json(tmp / pk.ISSUE_CANDIDATES, candidates)
        data_status = {
            "strokes": strokes_file.status,
            "measurements": meas_file.status,
            "aggregates": pk.DataStatus.AVAILABLE if strokes else pk.DataStatus.EMPTY,
            "issue_candidates": candidates.status,
        }
        manifest_kwargs: dict[str, object] = {
            "pack_id": pack_id,
            "kind": pk.PackKind.SESSION,
            "analysis_id": metadata.analysis_id,
            "created_at": now,
            "generator": GENERATOR,
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "result_file": RESULT_FILENAME,
            "language": opts.language,
            "data_status": data_status,
        }
        (tmp / pk.INSTRUCTIONS).write_text(
            render_instructions(
                kind=pk.PackKind.SESSION,
                pack_id=pack_id,
                analysis_id=metadata.analysis_id,
                language=opts.language,
                data_status=data_status,
                target=None,
            ),
            encoding="utf-8",
        )
        return _finalise(tmp, final, manifest_kwargs, images), final
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _session_frame_requests(
    videos: Sequence[VideoSource],
    strokes: Sequence[pk.StrokeRecord],
    candidates: pk.IssueCandidatesFile,
    opts: PackOptions,
) -> list[_FrameRequest]:
    by_id = {v.info.video_id: v for v in videos}
    requests: list[_FrameRequest] = []
    if not strokes:
        for v in videos:
            n = opts.overview_frames_per_video
            count = v.info.frame_count or int((v.info.duration_s or 0) * v.info.proxy_fps)
            if count <= 0:
                continue
            for k in range(n):
                idx = int((k + 0.5) * count / n)
                requests.append(_FrameRequest(v, idx, pk.ImageRole.OVERVIEW, None))
        return requests
    # Strokes named by issue candidates first, then an even spread over the session.
    priority: list[str] = []
    for c in candidates.candidates:
        priority.extend(s for s in c.stroke_ids if s not in priority)
    order = [s.id for s in strokes]
    remaining = [s for s in order if s not in priority]
    budget = max(0, opts.max_evidence_strokes - len(priority))
    if budget and remaining:
        step = max(1, len(remaining) / budget)
        spread = [remaining[int(i * step)] for i in range(min(budget, len(remaining)))]
    else:
        spread = []
    chosen = set((priority + spread)[: opts.max_evidence_strokes])
    for s in strokes:
        if s.id not in chosen:
            continue
        v = by_id[s.video_id]
        fps, fc = v.info.proxy_fps, v.info.frame_count
        contact = s.estimated_contact_s
        if contact is None:
            contact = (s.interval.start_s + s.interval.end_s) / 2
        for t, role in (
            (s.interval.start_s, pk.ImageRole.PREPARATION),
            (contact, pk.ImageRole.CONTACT),
            (s.interval.end_s, pk.ImageRole.FOLLOW_THROUGH),
        ):
            requests.append(_FrameRequest(v, _frame_at(t, fps, fc), role, s.id))
    return requests


# ------------------------------------------------------------------ single-stroke pack


def build_stroke_timeseries(
    video: VideoSource, stroke_id: str, interval: TimeInterval
) -> pk.StrokeTimeseries:
    """Every analysed pose frame in the interval: projected angles + key joints."""
    seq = _load_pose(video.pose_path)
    size = (seq.image_width, seq.image_height)
    samples: list[pk.TimeseriesSample] = []
    for frame in seq.frames:
        if not (interval.start_s - 1e-6 <= frame.timestamp_s <= interval.end_s + 1e-6):
            continue
        angles: dict[str, pk.AngleSample] = {}
        for key, (metric_id, joints, flexion) in ANGLES.items():
            m = projected_joint_angle(
                frame, joints, metric_id=metric_id, image_size=size, stroke_id=stroke_id
            )
            value = m.value
            if value is not None and flexion:
                value = round(180.0 - value, 2)
            angles[key] = pk.AngleSample(
                value_deg=value, observability=m.observability, confidence=m.confidence
            )
        joints_out = {
            k.name: pk.JointSample(x=round(k.x, 4), y=round(k.y, 4), visibility=k.visibility)
            for k in frame.keypoints
            if k.name in KEY_JOINTS
        }
        samples.append(
            pk.TimeseriesSample(
                frame_index=frame.frame_index,
                timestamp_s=round(frame.timestamp_s, 4),
                pose_detected=frame.detected,
                angles=angles,
                joints=joints_out,
            )
        )
    return pk.StrokeTimeseries(
        stroke_id=stroke_id,
        video_id=video.info.video_id,
        fps=seq.fps,
        frame_stride=seq.frame_stride,
        interval=interval,
        method="projected_joint_angle/v1 on MediaPipe 2D keypoints scaled to pixels; "
        "knee flexion = 180 - hip-knee-ankle angle",
        limitations=[
            "2D image-plane angles: out-of-plane motion changes the apparent angle.",
            "Angles below 0.6 joint visibility are reported as not_observable.",
            "The hitting arm is not identified automatically: both sides are listed.",
            "No phase segmentation: phases are not available.",
        ],
        samples=samples,
    )


def build_single_stroke_pack(
    dest_root: Path,
    metadata: pk.SessionMetadata,
    video: VideoSource,
    *,
    stroke: pk.StrokeRecord | None = None,
    interval: TimeInterval | None = None,
    declared_type: StrokeType | None = None,
    measurements: Sequence[pk.MeasurementRecord] = (),
    motivating_issue: pk.MotivatingIssue | None = None,
    options: PackOptions | None = None,
    now: datetime | None = None,
) -> tuple[pk.Manifest, Path]:
    """A pack for one stroke: a detected stroke, or an interval the user selected."""
    opts = options or PackOptions()
    now = now or datetime.now(UTC)
    if (stroke is None) == (interval is None):
        raise EvidencePackError("Give either a detected stroke or a user-selected interval")
    if stroke is None:
        assert interval is not None
        length = interval.end_s - interval.start_s
        if length <= 0 or length > opts.max_single_stroke_window_s:
            raise EvidencePackError(
                f"The selected interval must be between 0 and "
                f"{opts.max_single_stroke_window_s:g} s long",
                details={"length_s": round(length, 3)},
            )
        duration = video.info.duration_s
        if duration is not None and interval.end_s > duration + 0.05:
            raise EvidencePackError("The selected interval ends after the video")
        stroke = pk.StrokeRecord(
            id=f"interval_{round(interval.start_s * 1000)}_{round(interval.end_s * 1000)}",
            video_id=video.info.video_id,
            source=pk.StrokeSource.USER_INTERVAL,
            interval=interval,
            stroke_type=declared_type or StrokeType.UNKNOWN,
            type_declared_by_user=declared_type is not None,
            reliability=Reliability(
                observability=Observability.NOT_EVALUATED,
                limitations=["Interval selected by the user; not detected automatically."],
            ),
        )
    elif stroke.video_id != video.info.video_id:
        raise EvidencePackError("Stroke and video do not match", details={"stroke_id": stroke.id})
    bad = sorted(m.id for m in measurements if m.measurement.stroke_id != stroke.id)
    if bad:
        raise EvidencePackError(
            "Measurements belong to another stroke", details={"measurement_ids": bad}
        )

    pack_id, tmp, final = _start(dest_root, pk.PackKind.SINGLE_STROKE, now)
    try:
        fps, fc = video.info.proxy_fps, video.info.frame_count
        first = _frame_at(stroke.interval.start_s, fps, fc)
        last = _frame_at(stroke.interval.end_s, fps, fc)
        n = max(2, opts.sequence_frames)
        indices = sorted({int(round(first + k * (last - first) / (n - 1))) for k in range(n)})
        requests = [_FrameRequest(video, i, pk.ImageRole.SEQUENCE, stroke.id) for i in indices]
        if stroke.estimated_contact_s is not None:
            requests.append(
                _FrameRequest(
                    video,
                    _frame_at(stroke.estimated_contact_s, fps, fc),
                    pk.ImageRole.CONTACT,
                    stroke.id,
                )
            )
        images, missing = _write_images(tmp, requests, opts)
        stroke = stroke.model_copy(update={"evidence_image_ids": [i.id for i in images]})
        meas_file = pk.MeasurementsFile(
            status=pk.DataStatus.AVAILABLE if measurements else pk.DataStatus.NOT_IMPLEMENTED,
            reason=None
            if measurements
            else "Stroke-level metrics need phase segmentation (not implemented). "
            "See stroke_timeseries.json for per-frame 2D angles.",
            measurements=list(measurements),
        )
        quality = _data_quality([video], meas_file)
        quality.limitations.extend(missing)
        timeseries = build_stroke_timeseries(video, stroke.id, stroke.interval)
        target = pk.SingleStrokeTarget(
            video_id=video.info.video_id,
            stroke_id=stroke.id,
            source=stroke.source,
            interval=stroke.interval,
            motivating_issue=motivating_issue,
        )
        _write_json(tmp / pk.SESSION_METADATA, metadata)
        _write_json(tmp / pk.DATA_QUALITY, quality)
        _write_json(
            tmp / pk.STROKES, pk.StrokesFile(status=pk.DataStatus.AVAILABLE, strokes=[stroke])
        )
        _write_json(tmp / pk.MEASUREMENTS, meas_file)
        _write_json(tmp / pk.TIMESERIES, timeseries)
        data_status = {
            "strokes": pk.DataStatus.AVAILABLE,
            "measurements": meas_file.status,
            "timeseries": pk.DataStatus.AVAILABLE
            if any(s.pose_detected for s in timeseries.samples)
            else pk.DataStatus.EMPTY,
            "phases": pk.DataStatus.AVAILABLE if stroke.phases else pk.DataStatus.NOT_IMPLEMENTED,
        }
        manifest_kwargs: dict[str, object] = {
            "pack_id": pack_id,
            "kind": pk.PackKind.SINGLE_STROKE,
            "analysis_id": metadata.analysis_id,
            "created_at": now,
            "generator": GENERATOR,
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "result_file": RESULT_FILENAME,
            "language": opts.language,
            "data_status": data_status,
            "target": target,
        }
        (tmp / pk.INSTRUCTIONS).write_text(
            render_instructions(
                kind=pk.PackKind.SINGLE_STROKE,
                pack_id=pack_id,
                analysis_id=metadata.analysis_id,
                language=opts.language,
                data_status=data_status,
                target=target,
            ),
            encoding="utf-8",
        )
        return _finalise(tmp, final, manifest_kwargs, images), final
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
