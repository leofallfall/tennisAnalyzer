from __future__ import annotations

from tennis_cv.video.metadata import VideoMetadata
from tennis_cv.video.validation import validate_metadata


def _meta(**kw: object) -> VideoMetadata:
    base: dict[str, object] = dict(
        path="x",
        codec="h264",
        coded_width=1920,
        coded_height=1080,
        display_width=1920,
        display_height=1080,
        nominal_fps=60.0,
        average_fps=60.0,
        duration_s=30.0,
    )
    base.update(kw)
    return VideoMetadata(**base)  # type: ignore[arg-type]


def codes(meta: VideoMetadata) -> set[str]:
    return {i.code for i in validate_metadata(meta).issues}


def test_good_video_has_no_issues() -> None:
    result = validate_metadata(_meta())
    assert result.is_valid and result.issues == []


def test_low_fps_is_warning_not_error() -> None:
    result = validate_metadata(_meta(nominal_fps=30.0, average_fps=30.0))
    assert result.is_valid
    assert {i.code for i in result.warnings} == {"fps_low"}


def test_very_low_fps_is_error() -> None:
    result = validate_metadata(_meta(nominal_fps=10.0, average_fps=10.0))
    assert not result.is_valid
    assert "fps_too_low" in {i.code for i in result.errors}


def test_resolution_rules() -> None:
    assert "resolution_too_low" in codes(_meta(display_width=320, display_height=240))
    assert "resolution_low" in codes(_meta(display_width=640, display_height=360))


def test_duration_rules() -> None:
    assert "too_short" in codes(_meta(duration_s=0.2))
    assert "unknown_duration" in codes(_meta(duration_s=None))


def test_unsupported_codec() -> None:
    assert "unsupported_codec" in codes(_meta(codec="weird"))


def test_vfr_warning() -> None:
    assert "variable_frame_rate" in codes(_meta(is_variable_frame_rate=True))
