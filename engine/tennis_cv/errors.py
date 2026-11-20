"""Engine error hierarchy. Every error carries a stable machine-readable ``code``.

Error messages and ``details`` reach API clients: they name files by their base name
only, never by absolute path (M1.10). Full paths and commands go to the server log.
"""

from __future__ import annotations

from os import PathLike


def scrub_paths(text: str, *paths: str | PathLike[str]) -> str:
    """Replace every occurrence of the given paths in ``text`` with their base name.

    Used for tool output (ffmpeg/ffprobe stderr) that repeats the input path.
    """
    for p in sorted((str(x) for x in paths), key=len, reverse=True):
        if p:
            name = p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            text = text.replace(p, name)
    return text


class TennisCVError(Exception):
    """Base class for all engine errors."""

    code = "engine_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ToolNotAvailableError(TennisCVError):
    """A required external tool (e.g. ffmpeg/ffprobe) is not installed."""

    code = "tool_not_available"


class ProbeError(TennisCVError):
    """The file could not be read as a video container."""

    code = "probe_failed"


class VideoValidationError(TennisCVError):
    """The video was readable but does not satisfy the input requirements."""

    code = "video_invalid"


class PreprocessError(TennisCVError):
    """FFmpeg preprocessing failed."""

    code = "preprocess_failed"


class DecodeError(TennisCVError):
    """Frames could not be decoded."""

    code = "decode_failed"


class ModelNotAvailableError(TennisCVError):
    """A model file required by an estimator is missing."""

    code = "model_not_available"


class PoseRuntimeUnavailableError(TennisCVError):
    """The pose estimator's native runtime cannot be loaded (e.g. missing system libraries).

    On Linux MediaPipe needs ``libEGL.so.1`` / ``libGLESv2.so.2`` (packages ``libegl1``,
    ``libgles2``). The Python import succeeds without them; the failure only appears when
    the landmarker is created.
    """

    code = "pose_runtime_unavailable"


class NotYetImplementedError(TennisCVError, NotImplementedError):
    """Raised by interfaces whose implementation does not exist yet.

    This is deliberate: the engine must never return fabricated results for
    capabilities that have not been built and validated.
    """

    code = "not_implemented"
