"""Per-frame image quality measurements. Pure functions on BGR images."""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt
from pydantic import Field

from tennis_cv.common import StrictModel

# Images are analysed at this width so sharpness values are comparable across resolutions.
ANALYSIS_WIDTH = 640


class FrameQuality(StrictModel):
    frame_index: int
    timestamp_s: float
    sharpness: float = Field(description="Variance of Laplacian at 640px width (higher=sharper)")
    brightness: float = Field(description="Mean luma 0-255")
    contrast: float = Field(description="Std of luma 0-255")
    dark_clipped_fraction: float = Field(ge=0, le=1, description="Fraction of pixels with luma<=5")
    bright_clipped_fraction: float = Field(ge=0, le=1, description="Fraction with luma>=250")


def _to_analysis_gray(image: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    if w != ANALYSIS_WIDTH:
        new_h = max(1, int(round(h * ANALYSIS_WIDTH / w)))
        interp = cv2.INTER_AREA if w > ANALYSIS_WIDTH else cv2.INTER_LINEAR
        gray = cv2.resize(gray, (ANALYSIS_WIDTH, new_h), interpolation=interp)
    return gray.astype(np.uint8)


def sharpness(image: npt.NDArray[np.uint8]) -> float:
    gray = _to_analysis_gray(image)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def measure_frame(
    image: npt.NDArray[np.uint8], frame_index: int, timestamp_s: float
) -> FrameQuality:
    gray = _to_analysis_gray(image)
    total = float(gray.size)
    return FrameQuality(
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        brightness=float(gray.mean()),
        contrast=float(gray.std()),
        dark_clipped_fraction=float(np.count_nonzero(gray <= 5)) / total,
        bright_clipped_fraction=float(np.count_nonzero(gray >= 250)) / total,
    )
