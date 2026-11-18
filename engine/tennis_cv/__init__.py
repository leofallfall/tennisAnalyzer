"""Tennis computer vision engine.

The engine is a plain Python library. It knows nothing about HTTP, databases or LLMs:
it takes files/frames in and returns structured, validated data (Pydantic models) out.

Module status is declared in :mod:`tennis_cv.capabilities` and must be kept honest:
only modules with a real, tested implementation may be reported as implemented.
"""

__version__ = "0.1.0"
