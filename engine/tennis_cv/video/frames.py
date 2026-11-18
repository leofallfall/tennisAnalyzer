"""Frame extraction from a preprocessed (constant frame rate) proxy video."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from tennis_cv.errors import DecodeError


@dataclass(frozen=True, slots=True)
class Frame:
    index: int
    """Zero-based index in the proxy video."""
    timestamp_s: float
    """Seconds from the first frame, ``index / fps`` (proxy is CFR)."""
    image: npt.NDArray[np.uint8]
    """BGR image, shape (H, W, 3)."""


def iter_frames(
    path: str | Path,
    *,
    stride: int = 1,
    start_s: float = 0.0,
    end_s: float | None = None,
    max_frames: int | None = None,
) -> Iterator[Frame]:
    """Yield every ``stride``-th frame between ``start_s`` and ``end_s``.

    Frames are decoded sequentially (seeking in long-GOP smartphone video is
    imprecise); skipped frames are grabbed but not retrieved, which is cheap.
    """
    if stride < 1:
        raise ValueError("stride must be >= 1")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise DecodeError(f"OpenCV could not open video: {path}", details={"path": str(path)})
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if fps <= 0:
            raise DecodeError("Video reports no frame rate", details={"path": str(path)})
        first = int(round(start_s * fps))
        last = None if end_s is None else int(round(end_s * fps))
        emitted = 0
        index = 0
        while True:
            if last is not None and index > last:
                break
            if not cap.grab():
                break
            if index >= first and (index - first) % stride == 0:
                ok, image = cap.retrieve()
                if not ok or image is None:
                    raise DecodeError(
                        f"Failed to decode frame {index}", details={"frame_index": index}
                    )
                yield Frame(
                    index=index, timestamp_s=index / fps, image=np.asarray(image, dtype=np.uint8)
                )
                emitted += 1
                if max_frames is not None and emitted >= max_frames:
                    break
            index += 1
    finally:
        cap.release()
