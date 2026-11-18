from __future__ import annotations

from pathlib import Path

import pytest

from tennis_cv.errors import ProbeError
from tennis_cv.video.metadata import parse_ffprobe_output, probe_video


def _probe(stream: dict[str, object], fmt: dict[str, object] | None = None) -> dict[str, object]:
    base = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "r_frame_rate": "60/1",
        "avg_frame_rate": "60/1",
        "duration": "10.0",
    }
    base.update(stream)
    return {"streams": [base], "format": fmt or {"format_name": "mov,mp4", "size": "1000"}}


def test_parses_basic_fields() -> None:
    m = parse_ffprobe_output(_probe({}), "x.mp4")
    assert (m.display_width, m.display_height) == (1920, 1080)
    assert m.effective_fps == 60
    assert m.is_variable_frame_rate is False
    assert m.frame_count == 600  # derived from duration * fps when nb_frames is absent
    assert m.orientation == "landscape"


@pytest.mark.parametrize(
    ("stream", "expected_rotation"),
    [
        ({"side_data_list": [{"rotation": -90}]}, 90),
        ({"side_data_list": [{"rotation": 90}]}, 270),
        ({"tags": {"rotate": "90"}}, 90),
        ({"side_data_list": [{"rotation": 180}]}, 180),
    ],
)
def test_rotation_swaps_display_dimensions(
    stream: dict[str, object], expected_rotation: int
) -> None:
    m = parse_ffprobe_output(_probe(stream), "x.mp4")
    assert m.rotation_deg == expected_rotation
    if expected_rotation in (90, 270):
        assert (m.display_width, m.display_height) == (1080, 1920)
        assert m.orientation == "portrait"


def test_detects_variable_frame_rate() -> None:
    m = parse_ffprobe_output(_probe({"r_frame_rate": "120/1", "avg_frame_rate": "2997/100"}), "x")
    assert m.is_variable_frame_rate is True
    assert m.effective_fps == pytest.approx(29.97)


def test_missing_video_stream_is_probe_error() -> None:
    with pytest.raises(ProbeError):
        parse_ffprobe_output({"streams": [{"codec_type": "audio"}], "format": {}}, "x")


def test_probe_real_file(basic_video: Path) -> None:
    m = probe_video(basic_video)
    assert m.codec == "h264"
    assert (m.display_width, m.display_height) == (640, 360)
    assert m.effective_fps == 30
    assert m.duration_s == pytest.approx(2.0, abs=0.1)


def test_probe_rotated_file(rotated_video: Path) -> None:
    m = probe_video(rotated_video)
    assert (m.coded_width, m.coded_height) == (640, 360)
    assert (m.display_width, m.display_height) == (360, 640)


def test_probe_vfr_file(vfr_video: Path) -> None:
    assert probe_video(vfr_video).is_variable_frame_rate is True


def test_probe_rejects_non_video(not_a_video: Path) -> None:
    with pytest.raises(ProbeError):
        probe_video(not_a_video)
