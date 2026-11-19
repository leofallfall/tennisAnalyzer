"""M2.1 tools on artifacts written by the real pipeline (synthetic video, real MediaPipe).

Checks that the manifest/labels workflow reads what the pipeline actually writes
(pose.json at stored precision, timemap.json, report.json) and that the overlay prints
the same frame index the labels refer to.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from tennis_cv.comparison.base import ViewAngle
from tennis_cv.evaluation.dataset import LABEL_COLUMNS, ManifestClip, load_clip_data, load_labels
from tennis_cv.evaluation.visibility import analyse_impacts
from tennis_cv.pipeline import run_video_processing
from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator
from tests.conftest import requires_model

pytestmark = [requires_model, pytest.mark.slow]


def test_tools_read_real_pipeline_artifacts(basic_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "video"
    with MediaPipePoseEstimator() as est:
        run_video_processing(basic_video, out, est)

    # The overlay has one frame per proxy frame, so "#N" on overlay frame N is frame N.
    counts = {}
    for name in ("proxy.mp4", "overlay.mp4"):
        cap = cv2.VideoCapture(str(out / name))
        n = 0
        while cap.grab():
            n += 1
        cap.release()
        counts[name] = n

    clip = ManifestClip(
        clip_id="synthetic",
        file=basic_video.name,
        pose_dir=out,
        view=ViewAngle.UNKNOWN,
        dominant_hand="right",
        backhand="unknown",
        other_people=False,
        occlusion="none",
        split="unassigned",
    )
    data = load_clip_data(clip)
    assert counts["proxy.mp4"] == counts["overlay.mp4"] == data.frame_count == 60
    assert data.timemap is not None and data.source_vfr is False

    labels_csv = tmp_path / "labels.csv"
    labels_csv.write_text(",".join(LABEL_COLUMNS) + "\nsynthetic,30,forehand,A,full,missing,\n")
    (impact,) = analyse_impacts(data, load_labels(labels_csv))
    # A test pattern has no person: nothing is visible, and nothing is invented.
    assert not impact.pose_at_impact
    assert all(j.visibility_at_impact is None for j in impact.joints)
    assert impact.repeated_proxy_frames == 0
