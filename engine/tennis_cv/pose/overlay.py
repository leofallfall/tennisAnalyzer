"""Skeleton overlay rendering (debug / evidence video)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from tennis_cv.errors import PreprocessError
from tennis_cv.pose.landmarks import SKELETON_CONNECTIONS
from tennis_cv.pose.models import PoseFrame
from tennis_cv.video.ffmpeg import find_tool, run_tool

# BGR colours: left side blue-ish, right side orange, low confidence grey.
LEFT = (235, 160, 60)
RIGHT = (40, 140, 255)
CENTER = (230, 230, 230)
LOW_CONF = (120, 120, 120)


def draw_skeleton(
    image: npt.NDArray[np.uint8], pose: PoseFrame, *, min_visibility: float = 0.5
) -> npt.NDArray[np.uint8]:
    """Draw ``pose`` onto a copy of ``image``. Low-visibility joints are drawn grey."""
    out = image.copy()
    if not pose.detected or not pose.keypoints:
        return out
    h, w = out.shape[:2]
    thickness = max(2, int(round(min(h, w) / 300)))
    pts = [(int(round(k.x * w)), int(round(k.y * h))) for k in pose.keypoints]

    def ok(i: int) -> bool:
        v = pose.keypoints[i].visibility
        return v is None or v >= min_visibility

    def side_colour(i: int) -> tuple[int, int, int]:
        name = pose.keypoints[i].name
        return LEFT if name.startswith("left") else RIGHT if name.startswith("right") else CENTER

    for a, b in SKELETON_CONNECTIONS:
        if a >= len(pts) or b >= len(pts):
            continue
        colour = side_colour(b) if ok(a) and ok(b) else LOW_CONF
        cv2.line(out, pts[a], pts[b], colour, thickness, cv2.LINE_AA)
    for i, p in enumerate(pts):
        cv2.circle(out, p, thickness + 1, side_colour(i) if ok(i) else LOW_CONF, -1, cv2.LINE_AA)
    return out


def annotate_frame_info(image: npt.NDArray[np.uint8], text: str) -> None:
    cv2.putText(image, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(
        image, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA
    )


class OverlayWriter:
    """Writes frames to a temporary MJPEG/mp4v file, then transcodes to browser H.264."""

    def __init__(self, dst: str | Path, fps: float, size: tuple[int, int]) -> None:
        self.dst = Path(dst)
        self.dst.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self.dst.with_suffix(".tmp.avi")
        fourcc = cv2.VideoWriter.fourcc(*"MJPG")
        self._writer = cv2.VideoWriter(str(self._tmp), fourcc, fps, size)
        if not self._writer.isOpened():
            raise PreprocessError("OpenCV could not open the overlay video writer")

    def write(self, image: npt.NDArray[np.uint8]) -> None:
        self._writer.write(image)

    def abort(self) -> None:
        self._writer.release()
        self._tmp.unlink(missing_ok=True)

    def finish(self) -> Path:
        self._writer.release()
        cmd = [
            find_tool("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(self._tmp),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(self.dst),
        ]
        result = run_tool(cmd)
        self._tmp.unlink(missing_ok=True)
        if result.returncode != 0:
            raise PreprocessError(
                "Overlay transcoding failed", details={"stderr": result.stderr[-2000:]}
            )
        return self.dst
