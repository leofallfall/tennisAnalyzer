"""MediaPipe Pose Landmarker (Tasks API) baseline implementation.

Model files are not bundled; download them with ``uv run python scripts/download_models.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2

from tennis_cv.errors import ModelNotAvailableError, PoseRuntimeUnavailableError
from tennis_cv.pose.base import PoseEstimator
from tennis_cv.pose.landmarks import LANDMARK_NAMES
from tennis_cv.pose.models import Keypoint, PoseFrame
from tennis_cv.video.frames import Frame

MODEL_URLS: dict[str, str] = {
    variant: (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        f"pose_landmarker_{variant}/float16/latest/pose_landmarker_{variant}.task"
    )
    for variant in ("lite", "full", "heavy")
}
DEFAULT_VARIANT = "full"


RUNTIME_HINT = (
    "On Linux install the system libraries MediaPipe needs: "
    "sudo apt install libegl1 libgles2 (see README, Prerequisites)."
)

LIMITATIONS = [
    "Single camera 2D landmarks: out-of-plane motion is foreshortened.",
    "Landmark 'z' is a relative depth estimate, not a metric measurement.",
    "Wrist/hand landmarks degrade with motion blur and small player size.",
    "Model is trained on general human activity, not tennis-specific poses.",
    "Only the most prominent detected person is tracked (num_poses=1).",
]


def models_dir() -> Path:
    env = os.environ.get("TPA_MODELS_DIR")
    if env:
        return Path(env)
    # engine/tennis_cv/pose/ -> repository root /models
    return Path(__file__).resolve().parents[3] / "models"


def default_model_path(variant: str = DEFAULT_VARIANT) -> Path:
    return models_dir() / f"pose_landmarker_{variant}.task"


class MediaPipePoseEstimator(PoseEstimator):
    name = "mediapipe_pose_landmarker"
    limitations = LIMITATIONS

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        min_detection_confidence: float = 0.5,
        min_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        path = Path(model_path) if model_path else default_model_path()
        if not path.is_file():
            raise ModelNotAvailableError(
                f"MediaPipe pose model '{path.name}' not found in the models directory. "
                "Run: uv run python scripts/download_models.py",
                details={"model_file": path.name},
            )
        self._landmarker: Any = None
        try:
            import mediapipe as mp  # imported lazily: heavy native module

            self.model = path.name
            self._mp = mp
            vision = mp.tasks.vision
            options = vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=min_detection_confidence,
                min_pose_presence_confidence=min_presence_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            # The native library (and libEGL on Linux) is loaded here, not at import.
            self._landmarker = vision.PoseLandmarker.create_from_options(options)
        except (OSError, ImportError) as exc:
            raise PoseRuntimeUnavailableError(
                f"The MediaPipe runtime could not be loaded ({type(exc).__name__}). "
                + RUNTIME_HINT,
                details={"cause": _short_cause(exc)},
            ) from exc
        self._last_ts_ms = -1

    def estimate(self, frame: Frame) -> PoseFrame:
        rgb = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int(round(frame.timestamp_s * 1000))
        if ts_ms <= self._last_ts_ms:  # MediaPipe requires strictly increasing timestamps
            ts_ms = self._last_ts_ms + 1
        self._last_ts_ms = ts_ms
        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.pose_landmarks:
            return PoseFrame(
                frame_index=frame.index,
                timestamp_s=frame.timestamp_s,
                detected=False,
            )
        landmarks = result.pose_landmarks[0]
        keypoints = [
            Keypoint(
                name=LANDMARK_NAMES[i],
                x=float(lm.x),
                y=float(lm.y),
                z=float(lm.z),
                visibility=_unit(lm.visibility),
                presence=_unit(lm.presence),
            )
            for i, lm in enumerate(landmarks[: len(LANDMARK_NAMES)])
        ]
        return PoseFrame(
            frame_index=frame.index,
            timestamp_s=frame.timestamp_s,
            detected=True,
            keypoints=keypoints,
        )

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None


def _short_cause(exc: BaseException) -> str:
    """First line of the error, with directories stripped from any shared-object path."""
    first = (str(exc).splitlines() or [""])[0]
    return " ".join(Path(tok).name if "/" in tok or "\\" in tok else tok for tok in first.split())


_runtime_ok: bool = False


def check_pose_runtime(model_path: str | Path | None = None) -> tuple[bool, str | None]:
    """Create and close a landmarker once to prove the native runtime really works.

    Returns ``(available, reason)``. A success is cached for the process lifetime (it
    costs about a second); a failure is re-checked on every call so that installing the
    missing libraries or model is picked up without a restart.
    """
    global _runtime_ok
    if _runtime_ok:
        return True, None
    try:
        with MediaPipePoseEstimator(model_path):
            pass
    except ModelNotAvailableError as exc:
        return False, exc.message
    except PoseRuntimeUnavailableError as exc:
        return False, f"{exc.message} Cause: {exc.details.get('cause', '')}".strip()
    _runtime_ok = True
    return True, None


def _unit(v: float | None) -> float | None:
    if v is None:
        return None
    return min(1.0, max(0.0, float(v)))
