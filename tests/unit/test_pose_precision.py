"""M1.10 #7: stored pose precision (smaller pose.json, same measurements)."""

from __future__ import annotations

import gzip
import json
import random
from types import SimpleNamespace
from typing import Any

import numpy as np

from tennis_cv.biomechanics.geometry import projected_angle_deg
from tennis_cv.pose import mediapipe_estimator as mpe
from tennis_cv.pose.landmarks import LANDMARK_NAMES
from tennis_cv.pose.models import Keypoint, PoseFrame, PoseSequence
from tennis_cv.video.frames import Frame


def _estimator_with(landmarks: list[Any]) -> mpe.MediaPipePoseEstimator:
    """The real estimate() code path with a stubbed landmarker (no model needed)."""
    est = object.__new__(mpe.MediaPipePoseEstimator)
    est._mp = SimpleNamespace(  # type: ignore[attr-defined]
        Image=lambda **_k: None, ImageFormat=SimpleNamespace(SRGB=1)
    )
    est._landmarker = SimpleNamespace(
        detect_for_video=lambda *_a: SimpleNamespace(pose_landmarks=[landmarks])
    )
    est._last_ts_ms = -1  # type: ignore[attr-defined]
    return est


def _raw_landmarks(rng: random.Random) -> list[Any]:
    return [
        SimpleNamespace(
            x=rng.random(), y=rng.random(), z=rng.random() - 0.5,
            visibility=rng.random(), presence=rng.random(),
        )
        for _ in LANDMARK_NAMES
    ]  # fmt: skip


def _decimals(v: float) -> int:
    text = repr(v)
    return len(text.split(".")[1]) if "." in text and "e" not in text else 0


def test_estimator_rounds_stored_values() -> None:
    raw = _raw_landmarks(random.Random(1))
    frame = Frame(index=7, timestamp_s=7 / 30, image=np.zeros((4, 4, 3), np.uint8))
    pose = _estimator_with(raw).estimate(frame)
    assert pose.detected and len(pose.keypoints) == len(LANDMARK_NAMES)
    for kp, lm in zip(pose.keypoints, raw, strict=True):
        assert _decimals(kp.x) <= 5 and _decimals(kp.y) <= 5
        assert kp.z is not None and _decimals(kp.z) <= 4
        assert kp.visibility is not None and _decimals(kp.visibility) <= 3
        assert abs(kp.x - lm.x) <= 5e-6 and abs(kp.y - lm.y) <= 5e-6
    assert pose.timestamp_s == 7 / 30  # timestamps are not rounded: frame / fps exactly


def test_rounding_does_not_change_projected_angles() -> None:
    """Pixel-scaled angles on a 1920x1080 frame move by far less than 0.01 degrees."""
    rng = random.Random(2)
    worst = 0.0
    for _ in range(2000):
        pts = [(rng.random(), rng.random()) for _ in range(3)]
        scaled = [(x * 1920, y * 1080) for x, y in pts]
        rounded = [(round(x, 5) * 1920, round(y, 5) * 1080) for x, y in pts]
        a, b = projected_angle_deg(*scaled), projected_angle_deg(*rounded)  # type: ignore[arg-type]
        if (
            a is not None
            and b is not None
            and min(np_dist(scaled[0], scaled[1]), np_dist(scaled[2], scaled[1])) > 50
        ):  # joints at least 50 px apart, as on a visible arm
            worst = max(worst, abs(a - b))
    assert worst < 0.05


def np_dist(p: tuple[float, float], q: tuple[float, float]) -> float:
    return float(np.hypot(p[0] - q[0], p[1] - q[1]))


def test_pose_json_size_one_minute_30_fps() -> None:
    """Documents the size reduction (synthetic random values compress worse than real)."""
    rng = random.Random(3)

    def frames(decimals: bool) -> list[PoseFrame]:
        r = (lambda v, d: round(v, d)) if decimals else (lambda v, _d: v)
        return [
            PoseFrame(
                frame_index=i,
                timestamp_s=r(i / 30, 6),
                detected=True,
                keypoints=[
                    Keypoint(
                        name=n,
                        x=r(rng.random(), 5),
                        y=r(rng.random(), 5),
                        z=r(rng.random() - 0.5, 4),
                        visibility=r(rng.random(), 3),
                        presence=r(rng.random(), 3),
                    )
                    for n in LANDMARK_NAMES
                ],
            )
            for i in range(1800)
        ]

    def size(fs: list[PoseFrame]) -> tuple[int, int]:
        seq = PoseSequence(
            estimator="e", model="m", image_width=1920, image_height=1080,
            fps=30, frame_stride=1, frames=fs,
        )  # fmt: skip
        raw = seq.model_dump_json().encode()
        json.loads(raw)
        return len(raw), len(gzip.compress(raw))

    full, _full_gz = size(frames(False))
    small, small_gz = size(frames(True))
    assert small < 0.7 * full
    assert small_gz < 0.25 * full
