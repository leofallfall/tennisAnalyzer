"""Aggregate quality assessment for a video.

IMPORTANT: thresholds below are provisional heuristics chosen from general image
processing practice. They are NOT yet calibrated on real tennis footage and are
reported as such (see docs/VIDEO_PIPELINE.md, "Quality assessment").
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
from pydantic import Field

from tennis_cv.common import Observability, StrictModel
from tennis_cv.quality.metrics import FrameQuality
from tennis_cv.video.metadata import VideoMetadata


class Rating(StrEnum):
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    UNKNOWN = "unknown"


class QualityThresholds(StrictModel):
    good_short_side_px: int = 1080
    acceptable_short_side_px: int = 720
    blurry_sharpness: float = 50.0
    sharp_sharpness: float = 150.0
    dark_brightness: float = 50.0
    bright_brightness: float = 215.0
    max_clipped_fraction: float = 0.25
    good_fps: float = 100.0
    acceptable_fps: float = 50.0
    good_detection_rate: float = 0.9
    acceptable_detection_rate: float = 0.6


class Assessment(StrictModel):
    rating: Rating
    value: float | None = None
    unit: str | None = None
    detail: str


class QualityReport(StrictModel):
    resolution: Assessment
    frame_rate: Assessment
    blur: Assessment
    lighting: Assessment
    visibility: Assessment
    frames_sampled: int
    blurry_frame_fraction: float | None = None
    poorly_lit_frame_fraction: float | None = None
    observability: Observability = Field(
        description="Preliminary: can a player's body be analysed at all in this video?"
    )
    notes: list[str] = Field(default_factory=list)
    method: str = (
        "Heuristic image statistics (variance of Laplacian, luma mean/std/clipping) "
        "on sampled frames; pose detection rate for visibility. Thresholds uncalibrated."
    )


def _resolution(meta: VideoMetadata, t: QualityThresholds) -> Assessment:
    short = min(meta.display_width, meta.display_height)
    if short >= t.good_short_side_px:
        rating = Rating.GOOD
    elif short >= t.acceptable_short_side_px:
        rating = Rating.ACCEPTABLE
    else:
        rating = Rating.POOR
    return Assessment(
        rating=rating,
        value=float(short),
        unit="px",
        detail=f"{meta.display_width}x{meta.display_height} ({meta.orientation})",
    )


def _frame_rate(meta: VideoMetadata, t: QualityThresholds) -> Assessment:
    fps = meta.effective_fps
    if fps is None:
        return Assessment(rating=Rating.UNKNOWN, detail="Frame rate unknown")
    if fps >= t.good_fps:
        rating = Rating.GOOD
    elif fps >= t.acceptable_fps:
        rating = Rating.ACCEPTABLE
    else:
        rating = Rating.POOR
    frame_ms = 1000.0 / fps
    return Assessment(
        rating=rating,
        value=round(fps, 3),
        unit="fps",
        detail=f"{frame_ms:.1f} ms between frames"
        + (" (variable frame rate source)" if meta.is_variable_frame_rate else ""),
    )


def assess_quality(
    meta: VideoMetadata,
    frames: list[FrameQuality],
    *,
    pose_detection_rate: float | None = None,
    thresholds: QualityThresholds | None = None,
) -> QualityReport:
    t = thresholds or QualityThresholds()
    notes: list[str] = []
    blurry_frac: float | None = None
    poorly_lit: float | None = None

    if frames:
        sharp = np.array([f.sharpness for f in frames])
        bright = np.array([f.brightness for f in frames])
        clipped = np.array(
            [max(f.dark_clipped_fraction, f.bright_clipped_fraction) for f in frames]
        )
        blurry_frac = float(np.mean(sharp < t.blurry_sharpness))
        med_sharp = float(np.median(sharp))
        if med_sharp >= t.sharp_sharpness and blurry_frac < 0.2:
            blur_rating = Rating.GOOD
        elif med_sharp >= t.blurry_sharpness and blurry_frac < 0.5:
            blur_rating = Rating.ACCEPTABLE
        else:
            blur_rating = Rating.POOR
        blur = Assessment(
            rating=blur_rating,
            value=round(med_sharp, 2),
            unit="laplacian_variance",
            detail=f"{blurry_frac:.0%} of sampled frames below blur threshold. "
            "Note: motion blur on the racket is expected and not captured by this metric.",
        )
        lit_bad = (bright < t.dark_brightness) | (bright > t.bright_brightness)
        lit_bad |= clipped > t.max_clipped_fraction
        poorly_lit = float(np.mean(lit_bad))
        med_bright = float(np.median(bright))
        lighting = Assessment(
            rating=Rating.GOOD
            if poorly_lit < 0.1
            else Rating.ACCEPTABLE
            if poorly_lit < 0.4
            else Rating.POOR,
            value=round(med_bright, 1),
            unit="mean_luma",
            detail=f"{poorly_lit:.0%} of sampled frames too dark, too bright or clipped.",
        )
    else:
        blur = Assessment(rating=Rating.UNKNOWN, detail="No frames sampled")
        lighting = Assessment(rating=Rating.UNKNOWN, detail="No frames sampled")
        notes.append("No frames could be sampled for image quality.")

    if pose_detection_rate is None:
        visibility = Assessment(
            rating=Rating.UNKNOWN, detail="Pose estimation not run; player visibility unknown."
        )
        observability = Observability.NOT_EVALUATED
    else:
        r = pose_detection_rate
        if r >= t.good_detection_rate:
            vis_rating, observability = Rating.GOOD, Observability.OBSERVABLE
        elif r >= t.acceptable_detection_rate:
            vis_rating, observability = Rating.ACCEPTABLE, Observability.PARTIALLY_OBSERVABLE
        elif r > 0:
            vis_rating, observability = Rating.POOR, Observability.PARTIALLY_OBSERVABLE
        else:
            vis_rating, observability = Rating.POOR, Observability.NOT_OBSERVABLE
        visibility = Assessment(
            rating=vis_rating,
            value=round(r, 4),
            unit="fraction_of_frames_with_pose",
            detail=f"A body pose was detected in {r:.0%} of analysed frames. "
            "Detection does not guarantee every joint is accurately located.",
        )
        if r == 0:
            notes.append("No person was detected: body analysis is not possible on this video.")

    return QualityReport(
        resolution=_resolution(meta, t),
        frame_rate=_frame_rate(meta, t),
        blur=blur,
        lighting=lighting,
        visibility=visibility,
        frames_sampled=len(frames),
        blurry_frame_fraction=blurry_frac,
        poorly_lit_frame_fraction=poorly_lit,
        observability=observability,
        notes=notes,
    )
