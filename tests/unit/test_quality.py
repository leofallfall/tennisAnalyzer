from __future__ import annotations

import cv2
import numpy as np

from tennis_cv.common import Observability
from tennis_cv.quality.assessment import Rating, assess_quality
from tennis_cv.quality.metrics import measure_frame
from tennis_cv.video.metadata import VideoMetadata

RNG = np.random.default_rng(0)


def _checkerboard() -> np.ndarray:
    tile = np.kron([[0, 255] * 8, [255, 0] * 8] * 5, np.ones((40, 40)))
    return np.dstack([tile] * 3).astype(np.uint8)[:360, :640]


def _meta() -> VideoMetadata:
    return VideoMetadata(
        path="x",
        coded_width=1920,
        coded_height=1080,
        display_width=1920,
        display_height=1080,
        average_fps=60,
        nominal_fps=60,
    )


def test_blur_lowers_sharpness() -> None:
    sharp = _checkerboard()
    blurred = cv2.GaussianBlur(sharp, (31, 31), 10)
    assert measure_frame(sharp, 0, 0).sharpness > 10 * measure_frame(blurred, 0, 0).sharpness


def test_brightness_and_clipping() -> None:
    dark = np.zeros((360, 640, 3), np.uint8)
    q = measure_frame(dark, 0, 0)
    assert q.brightness == 0 and q.dark_clipped_fraction == 1.0


def test_assessment_flags_dark_blurry_video() -> None:
    dark_blur = cv2.GaussianBlur((_checkerboard() * 0.1).astype(np.uint8), (31, 31), 10)
    frames = [measure_frame(dark_blur, i, i / 30) for i in range(10)]
    report = assess_quality(_meta(), frames)
    assert report.blur.rating is Rating.POOR
    assert report.lighting.rating is Rating.POOR
    assert report.observability is Observability.NOT_EVALUATED  # pose not run


def test_visibility_from_pose_detection_rate() -> None:
    frames = [measure_frame(_checkerboard(), 0, 0)]
    assert assess_quality(_meta(), frames, pose_detection_rate=0.95).observability is (
        Observability.OBSERVABLE
    )
    none = assess_quality(_meta(), frames, pose_detection_rate=0.0)
    assert none.observability is Observability.NOT_OBSERVABLE
    assert any("No person" in n for n in none.notes)


def test_resolution_and_fps_ratings() -> None:
    r = assess_quality(_meta(), [])
    assert r.resolution.rating is Rating.GOOD
    assert r.frame_rate.rating is Rating.ACCEPTABLE
    assert r.blur.rating is Rating.UNKNOWN
