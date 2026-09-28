"""Surface Extrapolation Assistant for FreeCAD."""

__version__ = "0.1.0"

from surface_assistant.extrapolation import (  # noqa: F401
    ExtrapolationResult,
    ExtrapolationStatus,
    extrapolate_face,
)