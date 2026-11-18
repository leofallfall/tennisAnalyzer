"""First computer-vision experiment (Milestone 1).

Loads a video, extracts frames, runs MediaPipe pose estimation, exports structured
joint coordinates (pose.json), renders a skeleton overlay video (overlay.mp4) and
prints processing time and basic quality metrics.

Usage:
    uv run python scripts/pose_experiment.py path/to/video.mp4 [--out DIR]
        [--stride N] [--max-frames N] [--model PATH] [--no-overlay]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")

from tennis_cv.errors import TennisCVError
from tennis_cv.pipeline import PipelineOptions, run_video_processing
from tennis_cv.pose.mediapipe_estimator import MediaPipePoseEstimator


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, default=None, help="Default: storage/experiments/<name>")
    p.add_argument("--stride", type=int, default=1, help="Run pose on every N-th frame")
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--model", type=Path, default=None)
    p.add_argument("--no-overlay", action="store_true")
    args = p.parse_args()

    out = (
        args.out
        or Path(__file__).resolve().parents[1] / "storage" / "experiments" / args.video.stem
    )
    opts = PipelineOptions(
        pose_stride=args.stride, max_pose_frames=args.max_frames, render_overlay=not args.no_overlay
    )

    def progress(stage: str, frac: float) -> None:
        print(f"\r{stage:<20} {frac:6.1%}", end="", flush=True)

    try:
        with MediaPipePoseEstimator(args.model) as est:
            report = run_video_processing(args.video, out, est, options=opts, progress=progress)
    except TennisCVError as exc:
        print(f"\nERROR [{exc.code}] {exc.message}", file=sys.stderr)
        for k, v in exc.details.items():
            print(f"  {k}: {v}", file=sys.stderr)
        return 1
    print()

    m, q, ps = report.source_metadata, report.quality, report.pose_summary
    print("== Source video")
    print(
        f"  {m.display_width}x{m.display_height} {m.orientation}, codec={m.codec}, "
        f"fps={m.effective_fps}, VFR={m.is_variable_frame_rate}, rotation={m.rotation_deg}, "
        f"duration={m.duration_s}s"
    )
    for issue in report.validation.issues:
        print(f"  [{issue.severity}] {issue.message}")
    print("== Quality (heuristic, uncalibrated)")
    for name in ("resolution", "frame_rate", "blur", "lighting", "visibility"):
        a = getattr(q, name)
        print(f"  {name:<11} {a.rating:<10} {a.detail}")
    print(f"  observability: {q.observability}")
    print("== Pose")
    print(
        f"  {report.pose_estimator} ({report.pose_model}): {ps.frames_with_pose}/"
        f"{ps.frames_analysed} frames with a pose ({ps.detection_rate:.0%})"
    )
    if ps.ms_per_frame:
        print(f"  inference: {ps.ms_per_frame:.1f} ms/frame")
    print("== Timings")
    for t in report.timings:
        print(f"  {t.step:<28} {t.duration_ms / 1000:7.2f} s")
    print(f"  {'total':<28} {report.total_duration_ms / 1000:7.2f} s")
    print(f"== Outputs in {out}")
    for k, v in report.artifacts.items():
        print(f"  {k:<8} {out / v}")
    print(f"  report   {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
