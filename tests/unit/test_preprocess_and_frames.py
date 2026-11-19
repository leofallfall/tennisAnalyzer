from __future__ import annotations

from pathlib import Path

import pytest

from tennis_cv.video.frames import iter_frames
from tennis_cv.video.metadata import probe_video
from tennis_cv.video.preprocess import PreprocessOptions, build_preprocess_command, preprocess_video


def test_command_caps_long_side_for_portrait() -> None:
    from tennis_cv.video.metadata import VideoMetadata

    meta = VideoMetadata(
        path="x",
        coded_width=3840,
        coded_height=2160,
        rotation_deg=90,
        display_width=2160,
        display_height=3840,
        average_fps=30,
        nominal_fps=30,
    )
    cmd = build_preprocess_command("in", "out", meta, PreprocessOptions(max_long_side_px=1920))
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=-2:trunc(min(ih\\,1920)/2)*2" in vf
    # CFR input is not resampled: the fps filter (always present since M1.10, so one rule
    # maps proxy frames to source frames) keeps the nominal rate.
    assert vf.endswith("setpts=PTS-STARTPTS,fps=30/1:eof_action=pass")


def test_proxy_applies_rotation(rotated_video: Path, tmp_path: Path) -> None:
    result = preprocess_video(rotated_video, tmp_path / "proxy.mp4")
    assert result.applied_rotation_deg in (90, 270)
    m = result.metadata
    assert (m.coded_width, m.coded_height) == (360, 640)  # pixels are now upright
    assert m.rotation_deg == 0
    assert m.codec == "h264" and m.pixel_format == "yuv420p"


def test_proxy_converts_vfr_to_cfr(vfr_video: Path, tmp_path: Path) -> None:
    src = probe_video(vfr_video)
    result = preprocess_video(vfr_video, tmp_path / "proxy.mp4")
    assert result.resampled_to_cfr
    assert not result.metadata.is_variable_frame_rate
    assert result.metadata.duration_s == pytest.approx(src.duration_s, abs=0.15)


def test_frame_timestamps_are_index_over_fps(basic_video: Path) -> None:
    frames = list(iter_frames(basic_video, stride=5))
    assert [f.index for f in frames[:4]] == [0, 5, 10, 15]
    for f in frames:
        assert f.timestamp_s == pytest.approx(f.index / 30)
        assert f.image.shape == (360, 640, 3)
    assert len(list(iter_frames(basic_video))) == 60


def test_frame_window_and_limit(basic_video: Path) -> None:
    frames = list(iter_frames(basic_video, start_s=1.0, end_s=1.5))
    assert frames[0].index == 30 and frames[-1].index == 45
    assert len(list(iter_frames(basic_video, max_frames=3))) == 3
