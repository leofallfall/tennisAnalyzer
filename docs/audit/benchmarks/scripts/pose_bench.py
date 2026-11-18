"""Per-frame pose cost by model variant / stride / input size. Prints JSON lines."""

import json
import os
import resource
import statistics
import sys
import time

os.environ.setdefault("GLOG_minloglevel", "3")
import cv2

from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator
from tennis_cv.video.frames import Frame, iter_frames

src, nframes = sys.argv[1], int(sys.argv[2])
models = "/tmp/claude-0/bench/models"
t = time.perf_counter()
frames = list(iter_frames(src, max_frames=nframes))
dec = (time.perf_counter() - t) * 1000 / len(frames)
print(
    json.dumps(
        {
            "stage": "decode_cv2_retrieve",
            "ms_per_frame": round(dec, 2),
            "n": len(frames),
            "res": f"{frames[0].image.shape[1]}x{frames[0].image.shape[0]}",
        }
    ),
    flush=True,
)


def run(variant, stride=1, scale=None):
    with MediaPipePoseEstimator(f"{models}/pose_landmarker_{variant}.task") as est:
        times, det = [], 0
        for f in frames[::stride]:
            img = (
                f.image
                if scale is None
                else cv2.resize(f.image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            )
            t0 = time.perf_counter()
            p = est.estimate(Frame(f.index, f.timestamp_s, img))
            times.append((time.perf_counter() - t0) * 1000)
            det += p.detected
    return {
        "stage": "pose",
        "variant": variant,
        "stride": stride,
        "scale": scale,
        "frames": len(times),
        "ms_mean": round(statistics.mean(times), 2),
        "ms_p50": round(statistics.median(times), 2),
        "ms_p95": round(sorted(times)[int(len(times) * 0.95)], 2),
        "detection_rate": round(det / len(times), 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
    }


for v in ("lite", "full", "heavy"):
    print(json.dumps(run(v)), flush=True)
for s in (2, 4):
    print(json.dumps(run("full", stride=s)), flush=True)
print(json.dumps(run("full", scale=0.5)), flush=True)
