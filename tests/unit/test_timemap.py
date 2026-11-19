"""M1.10 #6: proxy -> original frame mapping.

Ground truth: every synthetic source frame carries its own index as 10 black/white
blocks. After building the proxy with the real preprocessing command, each proxy frame
is decoded and its index read back; the reconstructed time map must match it exactly.
These are synthetic videos: real smartphone VFR files are validated in M1.11.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from tennis_cv.video.metadata import probe_video
from tennis_cv.video.preprocess import preprocess_video, proxy_frame_rate
from tennis_cv.video.timemap import build_timemap, read_source_frame_times

W, H, BITS = 640, 360, 10
TB = 1 / 3600  # timestamps below are integers in this time base


def _write_indexed(path: Path, pts: list[int], *extra: str) -> Path:
    """Encode len(pts) frames, frame i showing i in binary, at times pts[i] * TB."""
    expr = "+".join(f"eq(N,{i})*{p}" for i, p in enumerate(pts)) if len(pts) < 200 else None
    assert expr is not None
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", "3600", "-i", "-",
        *extra,
        "-vf", f"setpts='{expr}',format=yuv420p",
        "-fps_mode", "passthrough", "-c:v", "libx264", "-g", "30",
        str(path),
    ]  # fmt: skip
    frames = bytearray()
    for i in range(len(pts)):
        img = np.full((H, W), 128, np.uint8)
        for b in range(BITS):
            img[100:260, 20 + b * 60 : 70 + b * 60] = 255 if (i >> b) & 1 else 0
        frames += img.tobytes()
    subprocess.run(cmd, input=bytes(frames), check=True)
    return path


def _read_indices(path: Path) -> list[int]:
    cap = cv2.VideoCapture(str(path))
    out: list[int] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sx, sy = gray.shape[1] / W, gray.shape[0] / H
        out.append(
            sum(1 << b for b in range(BITS) if gray[int(180 * sy), int((45 + b * 60) * sx)] > 128)
        )
    cap.release()
    return out


def _check(src: Path, tmp_path: Path) -> tuple[list[int], object]:
    meta = probe_video(src)
    prep = preprocess_video(src, tmp_path / "proxy.mp4", source_metadata=meta)
    rate = proxy_frame_rate(meta)
    assert rate is not None
    timemap = build_timemap(read_source_frame_times(src), rate, prep.metadata.frame_count)
    truth = _read_indices(tmp_path / "proxy.mp4")
    assert len(truth) == timemap.summary.proxy_frame_count
    assert timemap.proxy_to_source == truth
    return truth, timemap


def test_cfr_maps_one_to_one(tmp_path: Path) -> None:
    src = _write_indexed(tmp_path / "cfr.mp4", [i * 120 for i in range(60)])  # 30 fps
    truth, tm = _check(src, tmp_path)
    assert truth == list(range(60))
    assert tm.summary.duplicated_frames == 0 and tm.summary.dropped_frames == 0  # type: ignore[attr-defined]
    assert tm.summary.max_abs_error_ms < 0.01  # type: ignore[attr-defined]


def test_vfr_reports_duplicates_drops_and_error(tmp_path: Path) -> None:
    # 1 s at 30 fps, then 0.5 s at 120 fps: resampled to the average rate.
    pts = [i * 120 for i in range(30)] + [3600 + i * 30 for i in range(60)]
    src = _write_indexed(tmp_path / "vfr.mp4", pts)
    _, tm = _check(src, tmp_path)
    s = tm.summary  # type: ignore[attr-defined]
    assert s.exact_time_sync is False
    assert s.duplicated_frames > 0 and s.dropped_frames > 0
    assert s.max_abs_error_ms <= 1000 / 30  # bounded by the largest source gap
    assert s.source_start_pts_s == 0


def test_non_zero_start_time(tmp_path: Path) -> None:
    src = _write_indexed(tmp_path / "late.mp4", [9000 + i * 120 for i in range(45)])  # 2.5 s
    truth, tm = _check(src, tmp_path)
    # No leading duplicated frames: proxy time 0 is the first source frame. (The synthetic
    # file's short last-frame duration makes ffprobe call it VFR, hence a few duplicates.)
    assert truth[:10] == list(range(10)) and truth[-1] == 44
    assert tm.summary.source_start_pts_s == pytest.approx(2.5, abs=1e-3)  # type: ignore[attr-defined]
    assert tm.source_time(0.0) == pytest.approx(2.5, abs=1e-3)  # type: ignore[attr-defined]
    # Time-approximate: within one source frame interval here (the shown frame may repeat).
    assert tm.source_time(1.0) == pytest.approx(3.5, abs=1 / 30 + 1e-3)  # type: ignore[attr-defined]


def test_audio_starting_before_video(tmp_path: Path) -> None:
    video = _write_indexed(tmp_path / "v.mp4", [i * 120 for i in range(45)])
    src = tmp_path / "av.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-itsoffset", "0.5", "-i", str(video),
         "-f", "lavfi", "-i", "sine=duration=2", "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-shortest", str(src)],
        check=True,
    )  # fmt: skip
    truth, tm = _check(src, tmp_path)
    assert truth == list(range(len(truth))) and truth[0] == 0  # no duplicated lead-in
    assert tm.summary.source_start_pts_s == pytest.approx(0.5, abs=0.01)  # type: ignore[attr-defined]


def test_high_frame_rate_240_fps(tmp_path: Path) -> None:
    src = _write_indexed(tmp_path / "hfr.mp4", [i * 15 for i in range(120)])  # 240 fps
    truth, tm = _check(src, tmp_path)
    assert truth == list(range(120))
    assert tm.summary.proxy_fps == 240  # type: ignore[attr-defined]


def test_phone_like_jittery_120_fps(tmp_path: Path) -> None:
    """Nominal 120 fps with timestamp jitter and a few missing frames (seeded)."""
    rng = random.Random(7)
    pts, t = [], 0
    for _ in range(150):
        pts.append(t)
        t += 30 + rng.choice([-2, -1, 0, 0, 0, 1, 2]) + (30 if rng.random() < 0.05 else 0)
    src = _write_indexed(tmp_path / "jitter.mp4", pts)
    _, tm = _check(src, tmp_path)
    assert tm.summary.max_abs_error_ms > 0  # type: ignore[attr-defined]


def test_build_timemap_rule_and_lookup() -> None:
    tm = build_timemap([10.0, 10.1, 10.12, 10.4], 10)
    # slots 0, 1, 1, 4 -> frame 2 wins slot 1 (last), slots 2-3 repeat it, frame 1 dropped
    assert tm.proxy_to_source == [0, 2, 2, 2, 3]
    assert tm.summary.dropped_frames == 1 and tm.summary.duplicated_frames == 2
    assert tm.source_time(0.25) == pytest.approx(10.12)
    assert tm.source_frame(99) == 3  # clamped to the last proxy frame


@pytest.mark.parametrize(
    "failure",
    ["ffprobe_error", "invalid_json", "timeout"],
)
def test_timemap_failure_is_not_fatal(
    failure: str,
    basic_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the map cannot be built the analysis completes with time_mapping = None,
    no timemap artifact, and a warning in the server log."""
    import tennis_cv.video.timemap as tm
    from tennis_cv.pipeline import PipelineOptions, run_video_processing
    from tests.integration.conftest import NoPersonEstimator

    def fake(cmd: list[str], timeout_s: float | None = None) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1)
        if failure == "invalid_json":
            return subprocess.CompletedProcess(cmd, 0, "{not json", "")
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr(tm, "run_tool", fake)
    with caplog.at_level("WARNING"):
        report = run_video_processing(
            basic_video,
            tmp_path,
            NoPersonEstimator(),
            options=PipelineOptions(render_overlay=False),
        )
    assert report.time_mapping is None
    assert "timemap" not in report.artifacts
    assert not (tmp_path / "timemap.json").exists()
    assert "Time map not built" in caplog.text
