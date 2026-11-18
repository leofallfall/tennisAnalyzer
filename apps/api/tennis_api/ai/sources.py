"""Turn a processed Analysis (ORM + files on disk) into Evidence Pack inputs."""

from __future__ import annotations

from tennis_api.ai import pack as pk
from tennis_api.ai.builder import VideoSource
from tennis_api.config import Settings
from tennis_api.errors import ApiError
from tennis_api.models import Analysis
from tennis_api.storage import analysis_video_dir
from tennis_cv.pipeline import ProcessingReport


def video_sources(a: Analysis, settings: Settings) -> list[VideoSource]:
    sources = []
    for link in a.video_links:
        out = analysis_video_dir(settings.storage_dir, a.id, link.video_id)
        report_path = out / "report.json"
        if not report_path.is_file():
            raise ApiError(
                409,
                "cv_results_missing",
                "Processing results are missing for a video",
                {"video_id": link.video_id},
            )
        report = ProcessingReport.model_validate_json(report_path.read_text())
        proxy = report.preprocess.metadata
        fps = proxy.average_fps or proxy.nominal_fps or 30.0
        src = report.source_metadata
        sources.append(
            VideoSource(
                info=pk.VideoInfo(
                    video_id=link.video_id,
                    original_filename=link.video.original_filename,
                    camera_view=link.camera_view,
                    duration_s=proxy.duration_s,
                    average_fps=src.average_fps,
                    frame_count=proxy.frame_count,
                    width=src.display_width,
                    height=src.display_height,
                    orientation=src.orientation,
                    codec=src.codec,
                    container=src.container,
                    proxy_fps=fps,
                ),
                report=report,
                proxy_path=out / report.artifacts.get("proxy", "proxy.mp4"),
                pose_path=out / report.artifacts.get("pose", "pose.json"),
            )
        )
    return sources


def session_metadata(a: Analysis) -> pk.SessionMetadata:
    return pk.SessionMetadata(
        analysis_id=a.id,
        title=a.title,
        mode=a.mode,
        created_at=a.created_at,
        stroke_filter=a.stroke_filter,
        focus=a.focus,
        video_relation=a.video_relation,
        videos=[],  # filled by the caller with the videos in the pack
        recording_conditions={
            f"camera_view[{link.video_id}]": link.camera_view for link in a.video_links
        },
    )
