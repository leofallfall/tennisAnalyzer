"""Run the real Milestone-1 pipeline and report wall/CPU/peak RSS/output sizes."""

import json
import os
import resource
import sys
import time

os.environ.setdefault("GLOG_minloglevel", "3")
from pathlib import Path

from tennis_cv.pipeline import PipelineOptions, run_video_processing
from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator

src, out, stride, overlay = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "1"
t = time.perf_counter()
with MediaPipePoseEstimator() as est:
    r = run_video_processing(
        src, out, est, options=PipelineOptions(pose_stride=stride, render_overlay=overlay)
    )
wall = time.perf_counter() - t
s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
print(
    json.dumps(
        {
            "src": src,
            "stride": stride,
            "overlay": overlay,
            "cpus": len(os.sched_getaffinity(0)),
            "wall_s": round(wall, 1),
            "cpu_s_self": round(s.ru_utime + s.ru_stime, 1),
            "cpu_s_children_ffmpeg": round(c.ru_utime + c.ru_stime, 1),
            "peak_rss_mb_python": round(s.ru_maxrss / 1024),
            "peak_rss_mb_ffmpeg": round(c.ru_maxrss / 1024),
            "timings": {x.step: round(x.duration_ms / 1000, 1) for x in r.timings},
            "pose_ms_per_frame": round(r.pose_summary.ms_per_frame or 0, 1),
            "frames": r.pose_summary.frames_analysed,
            "detection_rate": round(r.pose_summary.detection_rate, 3),
            "sizes_mb": {p.name: round(p.stat().st_size / 1e6, 2) for p in out.iterdir()},
        }
    ),
    flush=True,
)
